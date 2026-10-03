from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import ExpenseClaim, ExportRun
from core.services import access, content_export, exports


@login_required
def project_page(request, pk):
    project = access.project(request.user, pk)
    access.leader(request.user, project)
    runs = list(project.exports.order_by("-created_at"))
    for run in runs:
        run.is_stale = (
            content_export.is_stale(run) if run.kind == "content" else exports.finance_is_stale(run)
        )
    return render(
        request,
        "core/claims.html",
        {
            "project": project,
            "visits": project.visits.select_related("venue"),
            "claims": project.expense_claims.order_by("-created_at"),
            "runs": runs,
        },
    )


@login_required
@require_POST
def create(request, pk):
    project = access.project(request.user, pk)
    claim = exports.create_claim(
        request.user, project, request.POST.getlist("visits"), request.POST.get("title", "报销材料")
    )
    return redirect("claim", pk=claim.pk)


@login_required
def claim_page(request, pk):
    claim = get_object_or_404(ExpenseClaim.objects.select_related("project"), pk=pk)
    access.leader(request.user, claim.project)
    snapshot, _ = exports.claim_snapshot(claim)
    return render(request, "core/claim.html", {"claim": claim, "snapshot": snapshot})


@login_required
@require_POST
def explain(request, pk):
    claim = get_object_or_404(ExpenseClaim.objects.select_related("project"), pk=pk)
    access.leader(request.user, claim.project)
    if claim.status == "final":
        raise PermissionDenied("已定稿报销单不可修改")
    order_id = request.POST.get("order_id")
    valid_ids = {
        order["id"]
        for block in exports.claim_snapshot(claim)[0]["blocks"]
        for order in block["orders"]
    }
    if order_id not in valid_ids:
        raise PermissionDenied("订单不属于报销范围")
    notes = claim.exception_notes.copy()
    notes[order_id] = request.POST.get("note", "").strip()[:1000]
    claim.exception_notes = notes
    claim.save(update_fields=["exception_notes", "updated_at"])
    return redirect("claim", pk=pk)


@login_required
@require_POST
def generate(request, pk):
    claim = get_object_or_404(ExpenseClaim.objects.select_related("project"), pk=pk)
    run = exports.export_claim(request.user, claim, final=request.POST.get("mode") == "final")
    return redirect("export-download", pk=run.pk)


@login_required
def download(request, pk):
    run = get_object_or_404(ExportRun.objects.select_related("project"), pk=pk)
    access.leader(request.user, run.project)
    return FileResponse(
        run.file.open("rb"), as_attachment=True, filename=f"eatful-{run.kind}-{run.pk}.zip"
    )


@login_required
@require_POST
def content_generate(request, pk):
    project = access.project(request.user, pk)
    run = content_export.export_content(request.user, project)
    return redirect("export-download", pk=run.pk)
