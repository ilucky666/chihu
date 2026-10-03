from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.models import Asset, Job
from core.services.access import leader, membership
from core.services.recognition import call_vision, store_menu_result, store_payment_result


def enqueue_recognition(asset, kind):
    if kind not in ("menu", "payment"):
        raise ValidationError("任务类型无效")
    if (
        kind == "menu"
        and asset.kind not in (Asset.Kind.MENU, Asset.Kind.ORDER)
        or kind == "payment"
        and asset.kind != Asset.Kind.PAYMENT
    ):
        raise ValidationError("图片类别不匹配")
    key = f"recognize:{kind}:{asset.pk}:{asset.sha256}"
    job, _ = Job.objects.get_or_create(
        key=key,
        defaults={
            "kind": f"recognize_{kind}",
            "payload": {"asset_id": str(asset.pk)},
            "available_at": timezone.now(),
        },
    )
    return job


def retry_job(user, job):
    if job.kind not in ("recognize_menu", "recognize_payment") or "asset_id" not in job.payload:
        raise ValidationError("任务不可重试")
    asset = Asset.objects.select_related("visit__project").get(pk=job.payload["asset_id"])
    membership(user, asset.visit.project)
    if asset.owner_id != user.id:
        leader(user, asset.visit.project)
    if job.status != "failed":
        raise ValidationError("只有失败任务可重试")
    job.status = "queued"
    job.attempts = 0
    job.available_at = timezone.now()
    job.last_error = ""
    job.save(update_fields=["status", "attempts", "available_at", "last_error", "updated_at"])
    return job


@transaction.atomic
def claim_job(now=None):
    now = now or timezone.now()
    Job.objects.filter(
        status="running", lease_until__lte=now, attempts__gte=F("max_attempts")
    ).update(status="failed", lease_until=None, last_error="任务租约过期且已达重试上限")
    job = (
        Job.objects.select_for_update(skip_locked=True)
        .filter(
            Q(status="queued", available_at__lte=now, attempts__lt=F("max_attempts"))
            | Q(status="running", lease_until__lte=now, attempts__lt=F("max_attempts"))
        )
        .order_by("available_at", "created_at")
        .first()
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.lease_until = now + timedelta(minutes=3)
    job.save(update_fields=["status", "attempts", "lease_until", "updated_at"])
    return job


def process_one():
    job = claim_job()
    if job is None:
        return None
    try:
        if job.kind not in ("recognize_menu", "recognize_payment"):
            raise ValidationError("未知后台任务")
        asset = Asset.objects.get(pk=job.payload["asset_id"])
        kind = job.kind.removeprefix("recognize_")
        data = call_vision(asset, kind)
        result = (
            store_menu_result(asset, data) if kind == "menu" else store_payment_result(asset, data)
        )
        job.result = {"record_id": str(result.pk)}
        job.status = "done"
        job.last_error = ""
        job.lease_until = None
        job.save(update_fields=["result", "status", "last_error", "lease_until", "updated_at"])
    except Exception as exc:
        job.status = "failed" if job.attempts >= job.max_attempts else "queued"
        job.available_at = timezone.now() + timedelta(seconds=min(300, 10 * 2**job.attempts))
        job.lease_until = None
        job.last_error = str(exc)[:500]
        job.save(
            update_fields=["status", "available_at", "lease_until", "last_error", "updated_at"]
        )
    return job
