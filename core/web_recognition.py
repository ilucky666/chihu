from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Asset, Job, MenuCandidate, MenuSource, PaymentCandidate
from core.services import access, jobs, recognition


@login_required
@require_POST
def queue(request, pk):
    asset = get_object_or_404(Asset.objects.select_related("visit__project"), pk=pk)
    access.membership(request.user, asset.visit.project)
    if asset.owner_id != request.user.id:
        access.leader(request.user, asset.visit.project)
    if not recognition.configuration_status()["ready"]:
        raise ValidationError("AI 识别尚未启用或配置不完整，请先手工录入或联系管理员")
    kind = "payment" if asset.kind == "payment" else "menu"
    jobs.enqueue_recognition(asset, kind)
    return redirect("recognition", pk=asset.visit_id)


@login_required
def page(request, pk):
    visit = access.visit(request.user, pk)
    member = access.membership(request.user, visit.project)
    assets = visit.assets.filter(kind__in=["menu", "order", "payment"]).order_by("-created_at")
    if member.role != "leader":
        assets = assets.filter(owner=request.user)
    asset_ids = list(assets.values_list("id", flat=True))
    sources = (
        MenuSource.objects.filter(asset_id__in=asset_ids)
        .prefetch_related("candidates")
        .order_by("-created_at")
    )
    candidates = (
        PaymentCandidate.objects.filter(asset_id__in=asset_ids)
        .select_related("asset")
        .order_by("-created_at")
    )
    job_keys = [
        f"recognize:{kind}:{asset.pk}:{asset.sha256}"
        for asset in assets
        for kind in (["payment"] if asset.kind == "payment" else ["menu"])
    ]
    return render(
        request,
        "core/recognition.html",
        {
            "visit": visit,
            "vision_status": recognition.configuration_status(),
            "is_leader": member.role == "leader",
            "assets": assets,
            "sources": sources,
            "payment_candidates": candidates,
            "jobs": Job.objects.filter(key__in=job_keys).order_by("-created_at"),
        },
    )


@login_required
@require_POST
def menu_confirm(request, pk):
    source = get_object_or_404(MenuSource.objects.select_related("visit__project"), pk=pk)
    recognition.confirm_menu(request.user, source, request.POST.getlist("candidate"))
    return redirect("visit", pk=source.visit_id)


@login_required
@require_POST
def menu_candidate_update(request, pk):
    candidate = get_object_or_404(
        MenuCandidate.objects.select_related("source__visit__project"), pk=pk
    )
    recognition.update_candidate(request.user, candidate, request.POST)
    return redirect("recognition", pk=candidate.source.visit_id)


@login_required
@require_POST
def payment_confirm(request, pk):
    candidate = get_object_or_404(
        PaymentCandidate.objects.select_related("asset__visit__project"), pk=pk
    )
    recognition.confirm_payment(request.user, candidate, request.POST.get("amount"))
    return redirect("finance-visit", pk=candidate.asset.visit_id)


@login_required
@require_POST
def retry(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if not recognition.configuration_status()["ready"]:
        raise ValidationError("请先配置 AI 识别再重试")
    jobs.retry_job(request.user, job)
    asset = get_object_or_404(Asset, pk=job.payload["asset_id"])
    return redirect("recognition", pk=asset.visit_id)
