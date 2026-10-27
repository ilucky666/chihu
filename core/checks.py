from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from core.services.ai_credentials import encryption_ready


@register(Tags.security, deploy=True)
def personal_ai_encryption_check(app_configs, **kwargs):
    if not settings.AI_ENCRYPTION_KEYS:
        return [
            Warning(
                "未配置 EATFUL_AI_ENCRYPTION_KEYS，个人 AI 不可启用，手工录入仍可用。",
                id="core.W001",
            )
        ]
    if not encryption_ready():
        return [Error("EATFUL_AI_ENCRYPTION_KEYS 格式无效。", id="core.E001")]
    return []
