from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import Assignment, Attendance, Dish, ProjectMember, TimeOption, User, Visit, Vote
from core.services import projects, workflow


@pytest.fixture
def scenario():
    leader = User.objects.create_user(username="flow-leader", password="test-pass-2026")
    member = User.objects.create_user(username="flow-member", password="test-pass-2026")
    project = projects.create_project(leader, "工作流测试")
    ProjectMember.objects.create(project=project, user=member)
    visit = projects.create_visit(leader, project, "测试餐厅")
    visit.status = Visit.Status.POLLING
    visit.save(update_fields=["status"])
    first = TimeOption.objects.create(
        visit=visit, starts_at=timezone.now() + timedelta(days=3), meal="lunch"
    )
    second = TimeOption.objects.create(
        visit=visit, starts_at=timezone.now() + timedelta(days=4), meal="dinner"
    )
    return leader, member, project, visit, first, second


@pytest.mark.django_db
def test_vote_completion_advances_to_confirmation_and_then_visit(scenario):
    leader, member, _, visit, first, second = scenario
    for option, user, choice in (
        (first, leader, "yes"),
        (first, member, "yes"),
        (second, leader, "maybe"),
        (second, member, "no"),
    ):
        Vote.objects.create(option=option, user=user, choice=choice)
    current = workflow.sync_visit(visit)
    assert current.status == Visit.Status.CONFIRMING
    assert current.scheduled_at == first.starts_at
    assert current.schedule_revision == 1
    assert workflow.sync_visit(current).schedule_revision == 1

    for user in (leader, member):
        Attendance.objects.create(
            visit=visit, user=user, status="yes", schedule_revision=current.schedule_revision
        )
    assert workflow.sync_visit(current).status == Visit.Status.SCHEDULED


@pytest.mark.django_db
def test_deadlines_advance_unanswered_stages(scenario):
    _, _, _, visit, first, _ = scenario
    visit.poll_deadline = timezone.now() - timedelta(minutes=1)
    visit.confirmation_deadline = timezone.now() + timedelta(days=1)
    visit.save(update_fields=["poll_deadline", "confirmation_deadline"])
    call_command("advance_workflows")
    visit.refresh_from_db()
    assert visit.status == Visit.Status.CONFIRMING
    assert visit.scheduled_at == first.starts_at

    visit.confirmation_deadline = timezone.now() - timedelta(minutes=1)
    visit.save(update_fields=["confirmation_deadline"])
    call_command("advance_workflows")
    visit.refresh_from_db()
    assert visit.status == Visit.Status.SCHEDULED


@pytest.mark.django_db
def test_only_current_visit_node_is_rendered_and_tasks_finish(scenario, client):
    leader, member, project, visit, first, _ = scenario
    client.force_login(member)
    poll = client.get(reverse("visit", args=[visit.pk]))
    assert poll.status_code == 200
    assert "哪天方便一起去？" in poll.content.decode()
    assert "确认最终到店名单" not in poll.content.decode()
    assert "认领菜品，完成文案与照片" not in poll.content.decode()

    visit.status = Visit.Status.CONFIRMING
    visit.scheduled_at = first.starts_at
    visit.schedule_revision = 1
    visit.save(update_fields=["status", "scheduled_at", "schedule_revision"])
    confirm = client.get(reverse("visit", args=[visit.pk]))
    assert "确认最终到店名单" in confirm.content.decode()
    assert "哪天方便一起去？" not in confirm.content.decode()

    visit.status = Visit.Status.SCHEDULED
    visit.save(update_fields=["status"])
    assert "准备出发" in client.get(reverse("visit", args=[visit.pk])).content.decode()

    visit.status = Visit.Status.DONE
    visit.save(update_fields=["status"])
    dish = Dish.objects.create(visit=visit, name="招牌菜", is_selected=True)
    task = Assignment.objects.create(dish=dish, kind=Assignment.Kind.REVIEW)
    assert workflow.visit_step(visit) == "tasks"
    assert workflow.project_step(project, [visit])[0] == "visits"
    task.status = Assignment.Status.APPROVED
    task.save(update_fields=["status"])
    assert workflow.visit_step(visit) == "complete"
    assert workflow.project_step(project, [visit])[0] == "summary"

    client.force_login(leader)
    assert client.get(reverse("task-inbox")).status_code == 200
    assert client.get(reverse("profile")).status_code == 200
    assert (
        client.post(
            reverse("profile"), {"username": leader.username, "display_name": "新称呼", "email": ""}
        ).status_code
        == 302
    )
    leader.refresh_from_db()
    assert leader.display_name == "新称呼"


@pytest.mark.django_db
def test_brand_dashboard_uses_real_roles_and_local_logo(scenario, client):
    leader, member, project, _, _, _ = scenario
    client.force_login(leader)
    response = client.get(reverse("home"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "chi-logo.jpg" in html
    assert project.title in html
    assert "我的身份：组长" in html
    assert "我的项目" in html and "测评任务" in html and "i自强" in html

    client.force_login(member)
    response = client.get(reverse("task-inbox"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "当前身份：组员" in html
    assert "当前要完成" in html
    assert "时间投票" in html
