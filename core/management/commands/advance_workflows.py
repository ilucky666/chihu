from django.core.management.base import BaseCommand

from core.models import Visit
from core.services.workflow import sync_visit


class Command(BaseCommand):
    help = "推进完成或到期的活动工作流"

    def handle(self, *args, **options):
        changed = 0
        for visit in Visit.objects.filter(
            status__in=[Visit.Status.POLLING, Visit.Status.CONFIRMING]
        ):
            if sync_visit(visit).status != visit.status:
                changed += 1
        self.stdout.write(f"advanced={changed}")
