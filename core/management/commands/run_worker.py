import time

from django.core.management.base import BaseCommand

from core.services.jobs import process_one


class Command(BaseCommand):
    help = "运行串行后台识别任务 worker"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            job = process_one()
            if job:
                self.stdout.write(f"{job.pk} {job.status}")
            if options["once"]:
                break
            if not job:
                time.sleep(2)
