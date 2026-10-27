"""Account-scoped credentials; ciphertext is bound to its owner and endpoint."""

import json
import uuid

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from core.models import Job, PersonalVisionConfiguration, User
from core.services.ai_http import validate_endpoint


def cipher():
    try:
        keys = [value.strip() for value in settings.AI_ENCRYPTION_KEYS.split(",") if value.strip()]
        if not keys:
            raise ValueError
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("服务器未正确配置 AI 加密主密钥；请联系维护者，仍可手工录入") from exc


def encryption_ready():
    try:
        cipher()
        return True
    except ValidationError:
        return False


def _context(config):
    return {
        "owner": str(config.user_id),
        "revision": str(config.revision),
        "mode": config.mode,
        "url": config.api_url,
        "model": config.model,
    }


@sensitive_variables("key", "payload")
def encrypt_key(config, key):
    payload = {**_context(config), "key": key}
    return cipher().encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")


@sensitive_variables("payload", "value")
def decrypt_key(config):
    try:
        payload = json.loads(cipher().decrypt(config.encrypted_api_key.encode("ascii")))
        if not isinstance(payload, dict) or any(
            payload.get(k) != v for k, v in _context(config).items()
        ):
            raise ValueError
        value = payload["key"]
        if not isinstance(value, str) or not value:
            raise ValueError
        return value
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValidationError("个人 AI 密钥无法解密，请重新保存或联系维护者") from exc


def owned_configuration(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("请先登录有效账号")
    return PersonalVisionConfiguration.objects.filter(user=user).first()


def cancel_pending(user):
    Job.objects.filter(requested_by=user, status__in=["queued", "running"]).update(
        status="failed",
        lease_until=None,
        last_error="个人 AI 配置已更新或删除，请重新发起识别",
    )


@transaction.atomic
@sensitive_variables("api_key", "previous_key", "config")
def save_configuration(user, *, mode, api_url, model, api_key="", expected_revision=None):
    if not user.is_active:
        raise PermissionDenied
    User.objects.select_for_update().get(pk=user.pk)
    config = PersonalVisionConfiguration.objects.select_for_update().filter(user=user).first()
    if (
        expected_revision is not None
        and (str(config.revision) if config else "") != expected_revision
    ):
        raise ValidationError("设置已在其他页面修改，请刷新后重新填写")
    if mode not in PersonalVisionConfiguration.Mode.values:
        raise ValidationError("识别模式无效")
    api_url, model = api_url.strip(), model.strip()
    api_key = api_key.strip()
    if len(api_key) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ValidationError("API Key 须为不含空白的可见 ASCII 字符，长度不超过4096")
    previous_key = ""
    if mode != PersonalVisionConfiguration.Mode.MANUAL:
        validate_endpoint(api_url)
        if not model or len(model) > 150:
            raise ValidationError("请填写长度不超过150的图片模型名称")
        if not api_key:
            if not config or api_url != config.api_url:
                raise ValidationError("首次配置或更换 API 地址时必须重新输入 Key")
            previous_key = decrypt_key(config)
    config = config or PersonalVisionConfiguration(user=user)
    config.mode, config.api_url, config.model = mode, api_url, model
    config.revision = uuid.uuid4()
    # Selecting manual mode clears the saved key rather than silently retaining it.
    config.encrypted_api_key = (
        encrypt_key(config, api_key or previous_key) if mode != "manual" else ""
    )
    config.full_clean()
    config.save()
    cancel_pending(user)
    return config


@transaction.atomic
def delete_configuration(user):
    User.objects.select_for_update().get(pk=user.pk)
    PersonalVisionConfiguration.objects.select_for_update().filter(user=user).delete()
    cancel_pending(user)
