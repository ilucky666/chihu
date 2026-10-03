from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.models import (
    Adjustment,
    EvidenceLink,
    Order,
    OrderLine,
    Payment,
    PaymentAllocation,
    Refund,
)
from core.services.access import leader, membership

CENT = Decimal("0.01")


def money(value):
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("金额无效") from exc
    if not amount.is_finite():
        raise ValidationError("金额无效")
    return amount


def line_total(line):
    if not line.billable or line.price is None:
        return Decimal("0.00")
    if line.price_type == OrderLine.PriceType.UNIT:
        return money(line.quantity * line.price)
    return money(line.price)


def order_lines_total(order):
    return sum((line_total(line) for line in order.lines.all()), Decimal("0.00"))


def order_calculated_total(order):
    return money(
        order_lines_total(order)
        + sum((item.amount for item in order.adjustments.all()), Decimal("0.00"))
    )


def payment_net(payment):
    refunded = payment.refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return money(payment.amount - refunded)


def payment_allocated(payment):
    return payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def order_paid(order):
    return order.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def create_order(user, visit, label, merchant="", expected_amount=None):
    leader(user, visit.project)
    if not label.strip():
        raise ValidationError("请输入订单名称")
    if expected_amount not in (None, "") and money(expected_amount) < 0:
        raise ValidationError("订单应付金额不能为负")
    return Order.objects.create(
        visit=visit,
        label=label.strip(),
        merchant=merchant.strip(),
        expected_amount=money(expected_amount) if expected_amount not in (None, "") else None,
    )


def add_line(
    user,
    order,
    name,
    quantity=1,
    unit="",
    price=None,
    price_type=OrderLine.PriceType.LINE,
    billable=True,
    dish=None,
):
    leader(user, order.visit.project)
    if dish and dish.visit_id != order.visit_id:
        raise ValidationError("菜品不属于本次活动")
    try:
        qty = Decimal(str(quantity))
    except InvalidOperation as exc:
        raise ValidationError("数量无效") from exc
    if (
        not qty.is_finite()
        or qty <= 0
        or qty > 10000
        or price_type not in OrderLine.PriceType.values
        or not name.strip()
    ):
        raise ValidationError("订单行参数无效")
    if price not in (None, "") and money(price) < 0:
        raise ValidationError("价格不能为负；优惠请记录为调整项")
    return OrderLine.objects.create(
        order=order,
        dish=dish,
        name=name.strip(),
        quantity=qty,
        unit=unit,
        price=money(price) if price not in (None, "") else None,
        price_type=price_type,
        billable=billable,
    )


def add_adjustment(user, order, label, amount, kind):
    leader(user, order.visit.project)
    if kind not in ("discount", "fee") or not label.strip():
        raise ValidationError("调整类型或说明无效")
    value = money(amount)
    if kind == "discount" and value >= 0 or kind == "fee" and value <= 0:
        raise ValidationError("优惠为负数，附加费用为正数")
    return Adjustment.objects.create(order=order, label=label, amount=value, kind=kind)


def create_payment(user, visit, payer, amount, method="", notes=""):
    membership(user, visit.project)
    if payer != user:
        leader(user, visit.project)
    value = money(amount)
    if value <= 0:
        raise ValidationError("付款金额必须大于零")
    return Payment.objects.create(
        visit=visit, payer=payer, amount=value, method=method, notes=notes
    )


@transaction.atomic
def allocate(user, payment, order, amount):
    leader(user, payment.visit.project)
    if payment.visit_id != order.visit_id:
        raise ValidationError("付款和订单必须属于同次活动")
    locked = Payment.objects.select_for_update().get(pk=payment.pk)
    value = money(amount)
    if value <= 0:
        raise ValidationError("分配金额必须大于零")
    existing = PaymentAllocation.objects.filter(payment=locked, order=order).first()
    used = payment_allocated(locked) - (existing.amount if existing else Decimal("0.00"))
    if used + value > payment_net(locked):
        raise ValidationError("分配金额超过实际净付款")
    allocation, _ = PaymentAllocation.objects.update_or_create(
        payment=locked, order=order, defaults={"amount": value}
    )
    return allocation


@transaction.atomic
def refund(user, payment, amount, notes=""):
    leader(user, payment.visit.project)
    locked = Payment.objects.select_for_update().get(pk=payment.pk)
    value = money(amount)
    if value <= 0 or value > payment_net(locked) - payment_allocated(locked):
        raise ValidationError("退款会使已分配金额超过净付款，请先调整分配")
    return Refund.objects.create(payment=locked, amount=value, notes=notes)


def link_evidence(user, asset, *, order=None, payment=None, note=""):
    member = membership(user, asset.visit.project)
    if not order and not payment:
        raise ValidationError("请选择订单或付款")
    if order and asset.kind not in ("menu", "order", "receipt"):
        raise ValidationError("订单证明必须是菜单、订单或票据图片")
    if payment and asset.kind not in ("payment", "receipt"):
        raise ValidationError("付款证明必须是付款或票据图片")
    if order and order.visit_id != asset.visit_id or payment and payment.visit_id != asset.visit_id:
        raise ValidationError("凭证与费用不属于同次活动")
    if member.role != "leader" and asset.owner_id != user.id:
        raise ValidationError("只能关联自己上传的凭证")
    if (
        payment
        and EvidenceLink.objects.filter(
            payment__isnull=False, asset__visit=asset.visit, asset__sha256=asset.sha256
        )
        .exclude(payment=payment)
        .exists()
    ):
        raise ValidationError("同一付款截图已关联另一笔付款")
    link, _ = EvidenceLink.objects.get_or_create(
        asset=asset, order=order, payment=payment, defaults={"note": note}
    )
    return link


def order_issues(order):
    issues = []
    calculated = order_calculated_total(order)
    if order.expected_amount is not None and calculated != order.expected_amount:
        issues.append(f"订单明细 {calculated} 与应付 {order.expected_amount} 不一致")
    if not order.evidence_links.exists():
        issues.append("缺订单或菜单证明")
    paid = order_paid(order)
    if paid <= 0:
        issues.append("缺已分配付款")
    if paid > 0 and paid < (order.expected_amount or calculated):
        issues.append(f"已分配付款 {paid} 少于订单应付 {order.expected_amount or calculated}")
    if paid > (order.expected_amount or calculated):
        issues.append(f"已分配付款 {paid} 超过订单应付 {order.expected_amount or calculated}")
    for allocation in order.allocations.select_related("payment").all():
        if not allocation.payment.evidence_links.exists():
            issues.append(f"付款 {allocation.payment_id} 缺证明")
    return issues


def visit_reimbursement(visit):
    return sum((order_paid(order) for order in visit.orders.all()), Decimal("0.00"))


def reconcile_project(project, visits=None):
    chosen = visits if visits is not None else project.visits.all()
    results = []
    for visit in chosen:
        for order in visit.orders.all():
            results.append(
                {
                    "visit": visit,
                    "order": order,
                    "paid": order_paid(order),
                    "issues": order_issues(order),
                }
            )
    return results
