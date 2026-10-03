from django.db import transaction
from django.utils import timezone

from core.models import Assignment, Notification, Visit
from core.services.access import leader


def active_assignments(project):
    return Assignment.objects.filter(
        dish__visit__project=project,
        dish__visit__status__in=[
            Visit.Status.CONFIRMING,
            Visit.Status.SCHEDULED,
            Visit.Status.DONE,
        ],
        dish__is_selected=True,
        owner__isnull=False,
        status__in=[Assignment.Status.CLAIMED, Assignment.Status.SUBMITTED],
    ).select_related("dish__visit", "owner")


@transaction.atomic
def scan_project(project, now=None):
    now = now or timezone.now()
    active_keys = set()
    created = 0
    for assignment in active_assignments(project):
        if (
            assignment.status != Assignment.Status.CLAIMED
            or not assignment.deadline
            or assignment.deadline > now
        ):
            continue
        key = f"assignment:{assignment.pk}:deadline:{assignment.deadline_revision}"
        active_keys.add(key)
        _, was_created = Notification.objects.get_or_create(
            dedupe_key=key,
            defaults={
                "recipient": assignment.owner,
                "project": project,
                "visit": assignment.dish.visit,
                "assignment": assignment,
                "kind": "assignment_due",
                "title": f"待提交：{assignment.dish.name}",
                "body": f"{assignment.get_kind_display()}已到截止时间，请尽快提交。",
            },
        )
        created += int(was_created)
    Notification.objects.filter(
        project=project, kind="assignment_due", closed_at__isnull=True
    ).exclude(dedupe_key__in=active_keys).update(closed_at=now)
    return created


def summary(user, project):
    leader(user, project)
    rows = []
    for assignment in active_assignments(project).filter(status=Assignment.Status.CLAIMED):
        rows.append(
            f"{assignment.dish.visit.venue.name} · {assignment.dish.name} · {assignment.get_kind_display()}：{assignment.owner}"
        )
    return f"{project.title}待提交任务\n" + ("\n".join(rows) if rows else "暂无待提交任务")
