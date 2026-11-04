from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "验证外置数据库、MySQL 字符集和媒体后端配置；不打印连接串或凭证"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if connection.vendor == "mysql":
                cursor.execute("SELECT @@character_set_database, @@collation_database")
                charset, collation = cursor.fetchone()
                if charset != "utf8mb4" or collation not in ("utf8mb4_bin", "utf8mb4_0900_bin"):
                    raise CommandError(
                        "MySQL 数据库需使用 utf8mb4 和二进制排序规则，保持账号及标识精确比较"
                    )
        # Instantiate the backend to reject incomplete OSS configuration before accepting traffic.
        storages["default"]
        self.stdout.write("数据库和媒体后端配置检查通过；OSS 真实读写另行试点验收")
