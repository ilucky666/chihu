from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.utils import timezone

from core.models import (
    Assignment,
    Attendance,
    ChoiceGroup,
    OrderLine,
    ProjectMember,
    User,
    Vote,
)
from core.services import dishes, finance, projects, scheduling
from core.services.access import leader, membership


@pytest.fixture
def people(db):
    return [
        User.objects.create_user(username=name, password="A secure passphrase 123!")
        for name in ("leader", "member", "stranger")
    ]


@pytest.fixture
def project_and_visits(people):
    head, member, _ = people
    project = projects.create_project(head, "小龙虾测评")
    ProjectMember.objects.create(project=project, user=member)
    first = projects.create_visit(head, project, "胖哥饺子馆", "第一次")
    second = projects.create_visit(head, project, "胖哥饺子馆", "第二次")
    return project, first, second


@pytest.mark.django_db
def test_project_roles_and_multiple_visits(people, project_and_visits):
    head, member, stranger = people
    project, first, second = project_and_visits
    assert first.id != second.id
    assert first.venue.name == second.venue.name
    assert membership(member, project).role == ProjectMember.Role.MEMBER
    with pytest.raises(PermissionDenied):
        leader(member, project)
    with pytest.raises(Http404):
        membership(stranger, project)
    other = projects.create_project(stranger, "另一个项目")
    with pytest.raises(Http404):
        membership(member, other)


@pytest.mark.django_db(transaction=True)
def test_invite_reuse_does_not_raise_role(people, project_and_visits):
    head, member, stranger = people
    project, _, _ = project_and_visits
    invitation, raw = projects.create_invitation(head, project, max_uses=2)
    joined = projects.join_invitation(stranger, raw)
    assert joined.role == "member"
    again = projects.join_invitation(stranger, raw)
    assert again.id == joined.id
    assert projects.join_invitation(member, raw).role == "member"
    invitation.refresh_from_db()
    assert invitation.uses == 1


@pytest.mark.django_db
def test_vote_and_time_confirmation_are_independent(people, project_and_visits):
    head, member, _ = people
    _, visit, other = project_and_visits
    today = timezone.localdate() + timedelta(days=1)
    slots = scheduling.generate_options(head, visit, today, today + timedelta(days=1))
    assert len(slots) == 4
    assert scheduling.generate_options(head, visit, today, today + timedelta(days=1)) == []
    scheduling.cast_vote(member, slots[0], Vote.Choice.YES)
    assert not Attendance.objects.filter(visit=visit, user=member).exists()
    visit = scheduling.choose_time(head, visit, slots[0])
    assert visit.schedule_revision == 1
    scheduling.confirm_attendance(member, visit, Attendance.Status.YES, 1)
    assert scheduling.confirmed_member(member, visit)
    visit = scheduling.choose_time(head, visit, slots[1])
    assert visit.schedule_revision == 2
    assert not scheduling.confirmed_member(member, visit)
    with pytest.raises(ValidationError):
        scheduling.confirm_attendance(member, visit, Attendance.Status.YES, 1)
    assert other.schedule_revision == 0


@pytest.mark.django_db
def test_choice_group_creates_only_selected_tasks(people, project_and_visits):
    head, _, _ = people
    _, visit, _ = project_and_visits
    group = ChoiceGroup.objects.create(visit=visit, name="饮品二选一")
    tea = dishes.create_dish(head, visit, "酸梅汤", choice_group=group, selected=False)
    beer = dishes.create_dish(head, visit, "啤酒", choice_group=group, selected=False)
    assert Assignment.objects.filter(dish__in=[tea, beer]).count() == 0
    dishes.select_choice(head, group, tea)
    assert Assignment.objects.filter(dish=tea).count() == 2
    with pytest.raises(ValidationError):
        dishes.select_choice(head, group, beer)


@pytest.mark.django_db
def test_claim_requires_confirmed_member_and_reassign_on_exit(people, project_and_visits):
    head, member, _ = people
    _, visit, _ = project_and_visits
    slot = scheduling.generate_options(head, visit, timezone.localdate(), timezone.localdate())[0]
    visit = scheduling.choose_time(head, visit, slot)
    dish = dishes.create_dish(head, visit, "虾球")
    assignment = dish.assignments.get(kind="review")
    with pytest.raises(ValidationError):
        dishes.claim(member, assignment)
    scheduling.confirm_attendance(member, visit, "yes", visit.schedule_revision)
    assert dishes.claim(member, assignment).owner == member
    scheduling.confirm_attendance(member, visit, "no", visit.schedule_revision)
    assignment.refresh_from_db()
    assert assignment.owner is None
    assert assignment.status == Assignment.Status.OPEN


@pytest.mark.django_db
def test_line_price_types_and_discount(people, project_and_visits):
    head, _, _ = people
    _, visit, _ = project_and_visits
    order = finance.create_order(head, visit, "第二单", expected_amount="208")
    finance.add_line(
        head,
        order,
        "油焖大虾",
        quantity=4,
        unit="斤",
        price="152",
        price_type=OrderLine.PriceType.LINE,
    )
    finance.add_line(head, order, "锅包肉", price="38")
    finance.add_line(head, order, "凉面", price="5")
    finance.add_line(head, order, "大拉皮", price="18")
    finance.add_adjustment(head, order, "优惠券", "-5", "discount")
    assert finance.order_calculated_total(order) == Decimal("208.00")


@pytest.mark.django_db
def test_payment_allocation_and_refund(people, project_and_visits):
    head, member, _ = people
    _, visit, other = project_and_visits
    order = finance.create_order(head, visit, "团购", expected_amount="550")
    payment = finance.create_payment(head, visit, member, "550")
    assert finance.allocate(head, payment, order, "500").amount == Decimal("500.00")
    with pytest.raises(ValidationError):
        finance.allocate(head, payment, order, "600")
    with pytest.raises(ValidationError):
        finance.refund(head, payment, "100")
    finance.refund(head, payment, "50")
    assert finance.payment_net(payment) == Decimal("500.00")
    assert finance.visit_reimbursement(visit) == Decimal("500.00")
    other_order = finance.create_order(head, other, "别的活动")
    with pytest.raises(ValidationError):
        finance.allocate(head, payment, other_order, "1")
