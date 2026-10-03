from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Assignment, Attendance, User
from core.services import dishes, reminders
from core.services.projects import create_project, create_visit
from core.services.scheduling import choose_time, generate_options


@pytest.mark.django_db
def test_reminders_dedupe_and_close_after_completion():
    user = User.objects.create_user(username="reminder-leader")
    project = create_project(user, "提醒")
    visit = create_visit(user, project, "店")
    today = timezone.localdate()
    option = generate_options(user, visit, today, today)[0]
    choose_time(user, visit, option)
    visit.refresh_from_db()
    Attendance.objects.create(
        visit=visit, user=user, status="yes", schedule_revision=visit.schedule_revision
    )
    dish = dishes.create_dish(user, visit, "菜")
    task = dish.assignments.get(kind=Assignment.Kind.REVIEW)
    dishes.claim(user, task)
    dishes.set_deadline(user, task, (timezone.now() - timedelta(hours=1)).isoformat())
    assert reminders.scan_project(project) == 1
    assert reminders.scan_project(project) == 0
    assert "菜" in reminders.summary(user, project)
    task.refresh_from_db()
    dishes.review_assignment(user, task, "waive", "无需测评")
    assert reminders.scan_project(project) == 0
    assert project.notification_set.filter(closed_at__isnull=False).count() == 1
