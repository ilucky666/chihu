from decimal import Decimal

import pytest

from core.models import ProjectMember, User
from core.services import exports, finance
from core.services.projects import create_project, create_visit


@pytest.mark.django_db
def test_three_orders_and_partial_proof_never_claim_complete():
    leader = User.objects.create_user(username="reconcile-leader")
    project = create_project(leader, "多单核对")
    visit = create_visit(leader, project, "三单店")
    orders = []
    for amount in (205, 208, 24):
        order = finance.create_order(leader, visit, f"{amount}元订单", expected_amount=amount)
        finance.add_line(leader, order, "费用项", price=amount)
        payment = finance.create_payment(leader, visit, leader, amount)
        finance.allocate(leader, payment, order, amount)
        orders.append(order)
    assert finance.visit_reimbursement(visit) == Decimal("437.00")
    assert all("缺订单或菜单证明" in finance.order_issues(order) for order in orders)
    assert all(any("缺证明" in issue for issue in finance.order_issues(order)) for order in orders)


@pytest.mark.django_db
def test_expected_vs_paid_and_menu_vs_claim_difference():
    leader = User.objects.create_user(username="difference-leader")
    project = create_project(leader, "差额核对")
    visit = create_visit(leader, project, "原价店")
    order = finance.create_order(leader, visit, "原价单", expected_amount=674)
    finance.add_line(leader, order, "菜单原价", price=674)
    payment = finance.create_payment(leader, visit, leader, 550)
    finance.allocate(leader, payment, order, 550)
    assert any("少于" in issue for issue in finance.order_issues(order))
    claim = exports.create_claim(leader, project, [visit.pk])
    assert exports.claim_snapshot(claim)[0]["total"] == "550.00"
    other = finance.create_order(leader, visit, "套餐差额", expected_amount=258)
    finance.add_line(leader, other, "菜单套餐", price=245)
    assert any("245.00" in issue and "258.00" in issue for issue in finance.order_issues(other))


@pytest.mark.django_db
def test_two_payers_one_project_claim():
    leader = User.objects.create_user(username="payers-leader")
    member = User.objects.create_user(username="payers-member")
    project = create_project(leader, "多人垫付")
    ProjectMember.objects.create(project=project, user=member)
    visit = create_visit(leader, project, "店")
    order = finance.create_order(leader, visit, "合单", expected_amount=100)
    finance.add_line(leader, order, "合单", price=100)
    for payer, amount in ((leader, 40), (member, 60)):
        payment = finance.create_payment(payer, visit, payer, amount)
        finance.allocate(leader, payment, order, amount)
    assert finance.order_paid(order) == Decimal("100.00")
    assert {p.payer_id for p in visit.payments.all()} == {leader.pk, member.pk}
    claim = exports.create_claim(leader, project, [visit.pk])
    assert exports.claim_snapshot(claim)[0]["total"] == "100.00"
