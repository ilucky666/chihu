from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.services.accounts import import_users


class Command(BaseCommand):
    help = "从私有 UTF-8 CSV 批量导入账号；不会开放自行注册。"

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)
        parser.add_argument("--update-existing", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            result = import_users(
                options["file"].read_bytes(),
                update_existing=options["update_existing"],
                dry_run=options["dry_run"],
            )
        except (OSError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{'检查通过（未写入）' if result['dry_run'] else '导入完成'}：新增 {result['created']}，更新 {result['updated']}"
        )
