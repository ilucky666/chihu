from django.core.management.base import BaseCommand

from core.models import Project, Visit
from core.services.reminders import scan_project
from core.services.workflow import sync_visit


class Command(BaseCommand):
    help = "扫描任务截止状态并生成去重的站内提醒"

    def handle(self, *args, **options):
        advanced = 0
        for visit in Visit.objects.filter(
            status__in=[Visit.Status.POLLING, Visit.Status.CONFIRMING]
        ):
            if sync_visit(visit).status != visit.status:
                advanced += 1
        count = sum(scan_project(project) for project in Project.objects.exclude(status="archived"))
        self.stdout.write(f"advanced={advanced} created={count}")
