from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.models import Asset, Job, PersonalVisionConfiguration
from core.services import ai_credentials
from core.services.access import leader, membership
from core.services.recognition import call_vision, store_menu_result, store_payment_result


def _authorize(user, asset):
    if not user or not user.is_active:
        raise PermissionDenied
    membership(user, asset.visit.project)
    if asset.owner_id != user.pk:
        leader(user, asset.visit.project)


@transaction.atomic
def enqueue_recognition(user, asset, kind):
    _authorize(user, asset)
    if kind not in ("menu", "payment"):
        raise ValidationError("任务类型无效")
    if (
        kind == "menu"
        and asset.kind not in (Asset.Kind.MENU, Asset.Kind.ORDER)
        or kind == "payment"
        and asset.kind != Asset.Kind.PAYMENT
    ):
        raise ValidationError("图片类别不匹配")
    config = PersonalVisionConfiguration.objects.select_for_update().filter(user=user).first()
    if not config or config.mode == "manual":
        raise ValidationError("请先配置个人 AI 服务，或继续手工录入")
    # Verify readability before queuing, without storing any plaintext in the job.
    ai_credentials.decrypt_key(config)
    key = f"recognize:{user.pk}:{config.revision}:{kind}:{asset.pk}"
    job, _ = Job.objects.get_or_create(
        key=key,
        defaults={
            "kind": f"recognize_{kind}",
            "payload": {"asset_id": str(asset.pk)},
            "requested_by": user,
            "configuration_revision": config.revision,
            "available_at": timezone.now(),
        },
    )
    return job


@transaction.atomic
def retry_job(user, job):
    if job.requested_by_id != user.pk:
        raise PermissionDenied("只能重试自己发起的识别；可另行使用自己的配置发起新任务")
    if job.kind not in ("recognize_menu", "recognize_payment") or "asset_id" not in job.payload:
        raise ValidationError("任务不可重试")
    asset = Asset.objects.select_related("visit__project").get(pk=job.payload["asset_id"])
    _authorize(user, asset)
    config = PersonalVisionConfiguration.objects.select_for_update().filter(user=user).first()
    if not config or config.mode == "manual" or config.revision != job.configuration_revision:
        raise ValidationError("个人配置已改变，请重新发起识别")
    ai_credentials.decrypt_key(config)
    job = Job.objects.select_for_update().get(pk=job.pk)
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
        user = job.requested_by
        if not user or not user.is_active:
            raise PermissionDenied
        asset = Asset.objects.select_related("visit__project").get(pk=job.payload["asset_id"])
        _authorize(user, asset)
        kind = job.kind.removeprefix("recognize_")
        if not job.configuration_revision:
            raise ValidationError("旧任务必须由成员重新发起")
        data = call_vision(asset, kind, user=user, revision=job.configuration_revision)
        with transaction.atomic():
            config = (
                PersonalVisionConfiguration.objects.select_for_update().filter(user=user).first()
            )
            user.refresh_from_db(fields=["is_active"])
            _authorize(user, asset)
            current = Job.objects.select_for_update().get(pk=job.pk)
            if (
                not config
                or config.revision != job.configuration_revision
                or current.status != "running"
                or current.attempts != job.attempts
            ):
                raise ValidationError("任务配置已过期，丢弃识别结果")
            result = (
                store_menu_result(asset, data)
                if kind == "menu"
                else store_payment_result(asset, data)
            )
            job.result = {"record_id": str(result.pk)}
            job.status = "done"
            job.last_error = ""
            job.lease_until = None
            job.save(update_fields=["result", "status", "last_error", "lease_until", "updated_at"])
    except Exception:
        job.status = "failed" if job.attempts >= job.max_attempts else "queued"
        job.available_at = timezone.now() + timedelta(seconds=min(300, 10 * 2**job.attempts))
        job.lease_until = None
        # Never persist provider bodies, URLs, arbitrary exception strings, or keys.
        job.last_error = "识别失败，请检查个人 AI 设置、图片和接口返回格式后重试"
        Job.objects.filter(pk=job.pk, status="running", attempts=job.attempts).update(
            status=job.status,
            available_at=job.available_at,
            lease_until=None,
            last_error=job.last_error,
        )
        job.refresh_from_db()
    return job
