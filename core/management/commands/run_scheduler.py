import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = "持续扫描站内提醒与工作流截止；异常退出由容器监督器重启"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                call_command("scan_reminders")
            finally:
                close_old_connections()
            if options["once"]:
                return
            time.sleep(300)
