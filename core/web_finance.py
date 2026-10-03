from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Asset, Order, Payment
from core.services import access, finance


@login_required
def visit_page(request, pk):
    visit = access.visit(request.user, pk)
    is_leader = access.membership(request.user, visit.project).role == "leader"
    orders = list(
        visit.orders.prefetch_related("lines", "adjustments", "allocations", "evidence_links")
    )
    for order in orders:
        order.calculated = finance.order_calculated_total(order)
        order.paid = finance.order_paid(order)
        order.issues = finance.order_issues(order)
    payments = list(
        visit.payments.select_related("payer").prefetch_related(
            "allocations", "refunds", "evidence_links"
        )
    )
    if not is_leader:
        payments = [payment for payment in payments if payment.payer_id == request.user.id]
    return render(
        request,
        "core/finance.html",
        {
            "visit": visit,
            "is_leader": is_leader,
            "orders": orders,
            "payments": payments,
            "total": finance.visit_reimbursement(visit),
            "proofs": Asset.objects.filter(
                visit=visit, kind__in=["menu", "order", "payment", "receipt"]
            ).filter(owner=request.user)
            if not is_leader
            else Asset.objects.filter(
                visit=visit, kind__in=["menu", "order", "payment", "receipt"]
            ),
        },
    )


@login_required
@require_POST
def order_create(request, pk):
    visit = access.visit(request.user, pk)
    finance.create_order(
        request.user,
        visit,
        request.POST.get("label", ""),
        request.POST.get("merchant", ""),
        request.POST.get("expected_amount"),
    )
    return redirect("finance-visit", pk=pk)


@login_required
@require_POST
def line_create(request, pk):
    order = get_object_or_404(Order, pk=pk)
    access.visit(request.user, order.visit_id)
    finance.add_line(
        request.user,
        order,
        request.POST.get("name", ""),
        request.POST.get("quantity", 1),
        request.POST.get("unit", ""),
        request.POST.get("price"),
        request.POST.get("price_type", "line"),
    )
    return redirect("finance-visit", pk=order.visit_id)


@login_required
@require_POST
def adjustment_create(request, pk):
    order = get_object_or_404(Order, pk=pk)
    access.visit(request.user, order.visit_id)
    finance.add_adjustment(
        request.user,
        order,
        request.POST.get("label", ""),
        request.POST.get("amount"),
        request.POST.get("kind"),
    )
    return redirect("finance-visit", pk=order.visit_id)


@login_required
@require_POST
def payment_create(request, pk):
    visit = access.visit(request.user, pk)
    finance.create_payment(
        request.user,
        visit,
        request.user,
        request.POST.get("amount"),
        request.POST.get("method", ""),
        request.POST.get("notes", ""),
    )
    return redirect("finance-visit", pk=pk)


@login_required
@require_POST
def allocation_create(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    access.visit(request.user, payment.visit_id)
    order = get_object_or_404(Order, pk=request.POST.get("order"), visit=payment.visit)
    finance.allocate(request.user, payment, order, request.POST.get("amount"))
    return redirect("finance-visit", pk=payment.visit_id)


@login_required
@require_POST
def refund_create(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    access.visit(request.user, payment.visit_id)
    finance.refund(request.user, payment, request.POST.get("amount"), request.POST.get("notes", ""))
    return redirect("finance-visit", pk=payment.visit_id)


@login_required
@require_POST
def evidence_create(request, pk):
    visit = access.visit(request.user, pk)
    asset = get_object_or_404(Asset, pk=request.POST.get("asset"), visit=visit)
    order = (
        get_object_or_404(Order, pk=request.POST.get("order"), visit=visit)
        if request.POST.get("order")
        else None
    )
    payment = (
        get_object_or_404(Payment, pk=request.POST.get("payment"), visit=visit)
        if request.POST.get("payment")
        else None
    )
    finance.link_evidence(request.user, asset, order=order, payment=payment)
    return redirect("finance-visit", pk=pk)
