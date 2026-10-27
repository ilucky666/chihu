from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "生成个人 AI 凭证的服务器加密主密钥；仅在私有终端执行并妥善保管输出。"

    def handle(self, *args, **options):
        self.stdout.write(Fernet.generate_key().decode("ascii"))
