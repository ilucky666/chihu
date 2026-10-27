from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import PersonalVisionConfiguration
from core.services.ai_credentials import decrypt_key, encrypt_key


class Command(BaseCommand):
    help = "使用 EATFUL_AI_ENCRYPTION_KEYS 的首个主密钥重加密；旧密钥须仍在密钥环内。"

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        try:
            for config in (
                PersonalVisionConfiguration.objects.select_for_update()
                .exclude(encrypted_api_key="")
                .order_by("created_at", "id")
            ):
                config.encrypted_api_key = encrypt_key(config, decrypt_key(config))
                config.save(update_fields=["encrypted_api_key", "updated_at"])
                count += 1
        except ValidationError as exc:
            raise CommandError("重加密未完成，整批已回滚；请检查主密钥环与备份") from exc
        self.stdout.write(f"已重加密 {count} 条个人配置；未输出任何 API Key")
