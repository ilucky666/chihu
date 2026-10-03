import hashlib
import io
import json
import uuid
import zipfile
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection, transaction

from core.models import ExpenseClaim, ExportRun, Visit
from core.services.access import leader
from core.services.finance import line_total, order_calculated_total, order_issues, order_paid
from core.services.template_workbook import workbook_bytes


def safe_text(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def create_claim(user, project, visit_ids, title="报销材料"):
    leader(user, project)
    ids = [str(value) for value in visit_ids]
    if not ids or len(ids) != len(set(ids)):
        raise ValidationError("请选择不重复的到店活动")
    try:
        ids = [str(uuid.UUID(value)) for value in ids]
    except ValueError as exc:
        raise ValidationError("活动 ID 无效") from exc
    visits = Visit.objects.filter(project=project, pk__in=ids)
    if visits.count() != len(ids):
        raise ValidationError("活动不属于本项目")
    return ExpenseClaim.objects.create(
        project=project, title=title.strip() or "报销材料", visit_ids=ids, created_by=user
    )


def claim_snapshot(claim):
    visits = list(
        Visit.objects.filter(project=claim.project, pk__in=claim.visit_ids)
        .select_related("venue")
        .order_by("created_at")
    )
    blocks = []
    all_evidence = {}
    for visit in visits:
        orders = []
        for order in visit.orders.prefetch_related(
            "lines", "adjustments", "allocations__payment", "evidence_links__asset"
        ).order_by("created_at"):
            issues = order_issues(order)
            lines = [
                {
                    "name": safe_text(line.name),
                    "quantity": str(line.quantity),
                    "unit": safe_text(line.unit),
                    "price": str(line.price) if line.price is not None else "",
                    "price_type": line.price_type,
                    "total": str(line_total(line)),
                }
                for line in order.lines.all()
            ]
            adjustments = [
                {"label": safe_text(a.label), "amount": str(a.amount)}
                for a in order.adjustments.all()
            ]
            evidence = []
            links = list(order.evidence_links.select_related("asset"))
            for allocation in order.allocations.select_related("payment").all():
                links += list(allocation.payment.evidence_links.select_related("asset"))
            for link in links:
                asset = link.asset
                all_evidence[str(asset.pk)] = asset
                evidence.append(
                    {
                        "asset_id": str(asset.pk),
                        "kind": asset.kind,
                        "sha256": asset.sha256,
                        "order_id": str(order.pk),
                        "payment_id": str(link.payment_id) if link.payment_id else None,
                    }
                )
            orders.append(
                {
                    "id": str(order.pk),
                    "label": safe_text(order.label),
                    "merchant": safe_text(order.merchant),
                    "expected": str(order.expected_amount)
                    if order.expected_amount is not None
                    else "",
                    "calculated": str(order_calculated_total(order)),
                    "paid": str(order_paid(order)),
                    "lines": lines,
                    "adjustments": adjustments,
                    "issues": issues,
                    "evidence": evidence,
                }
            )
        blocks.append(
            {
                "visit_id": str(visit.pk),
                "venue": safe_text(visit.venue.name),
                "orders": orders,
                "total": str(sum((Decimal(order["paid"]) for order in orders), Decimal("0.00"))),
            }
        )
    return {
        "project_id": str(claim.project_id),
        "claim_id": str(claim.pk),
        "title": safe_text(claim.title),
        "blocks": blocks,
        "total": str(sum((Decimal(block["total"]) for block in blocks), Decimal("0.00"))),
        "exceptions": claim.exception_notes.copy(),
    }, all_evidence


@transaction.atomic
def export_claim(user, claim, final=False):
    if connection.vendor == "postgresql" and not connection.savepoint_ids:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    claim = ExpenseClaim.objects.select_for_update().select_related("project").get(pk=claim.pk)
    leader(user, claim.project)
    if final and claim.status == "final":
        raise ValidationError("正式报销材料已经生成；请新建报销单以保留旧快照")
    snapshot, assets = claim_snapshot(claim)
    unresolved = [
        order["label"]
        for block in snapshot["blocks"]
        for order in block["orders"]
        if order["issues"] and not claim.exception_notes.get(order["id"], "").strip()
    ]
    if final and unresolved:
        raise ValidationError(f"请先补齐凭证或逐项填写核对说明：{'、'.join(unresolved)}")
    workbook = workbook_bytes(snapshot, user.display_name or user.username)
    output = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("报销表.xlsx", workbook)
        for asset_id, asset in assets.items():
            with asset.file.open("rb") as source:
                raw = source.read()
            if hashlib.sha256(raw).hexdigest() != asset.sha256:
                raise ValidationError("凭证原件摘要不一致，已中止导出")
            extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
                asset.content_type, "bin"
            )
            path = f"凭证/{asset_id}.{extension}"
            archive.writestr(path, raw)
            manifest.append({"asset_id": asset_id, "path": path, "sha256": asset.sha256})
        archive.writestr(
            "索引.json",
            json.dumps(
                {"snapshot": snapshot, "files": manifest, "status": "final" if final else "draft"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    raw = output.getvalue()
    run = ExportRun(
        project=claim.project,
        created_by=user,
        kind="finance",
        status="final" if final else "draft",
        template_version="sample-blank-v1",
        scope={"claim_id": str(claim.pk), "visit_ids": claim.visit_ids},
        snapshot=snapshot,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    run.file.save(f"finance-{run.pk}.zip", ContentFile(raw), save=False)
    run.save()
    if final:
        claim.status = "final"
        claim.save(update_fields=["status", "updated_at"])
    return run


def finance_is_stale(run):
    if run.kind != "finance":
        return False
    claim = ExpenseClaim.objects.filter(pk=run.scope.get("claim_id"), project=run.project).first()
    if not claim:
        return True
    current, _ = claim_snapshot(claim)
    return current != run.snapshot
