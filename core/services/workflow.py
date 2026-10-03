"""Derive the one active step for each visit and advance expired/completed steps."""

from django.db import transaction
from django.utils import timezone

from core.models import Assignment, AuditEvent, ProjectMember, ReviewDocument, Visit

STEPS = (
    ("poll", "时间投票"),
    ("confirm", "确认参加"),
    ("visit", "到店准备"),
    ("tasks", "菜品测评"),
)
TERMINAL_ASSIGNMENTS = (
    Assignment.Status.APPROVED,
    Assignment.Status.WAIVED,
    Assignment.Status.CANCELLED,
)


def _vote_score(option, active_ids):
    votes = [vote for vote in option.votes.all() if vote.user_id in active_ids]
    return (
        sum(vote.choice == "yes" for vote in votes),
        sum(vote.choice == "maybe" for vote in votes),
    )


@transaction.atomic
def sync_visit(visit, now=None):
    """Materialize a deadline or unanimous completion, without skipping empty steps."""
    now = now or timezone.now()
    locked = Visit.objects.select_for_update().select_related("project").get(pk=visit.pk)
    active_ids = set(
        ProjectMember.objects.filter(project=locked.project, active=True).values_list(
            "user_id", flat=True
        )
    )
    if locked.status == Visit.Status.POLLING:
        options = list(locked.time_options.prefetch_related("votes"))
        if options:
            all_voted = bool(active_ids) and all(
                active_ids.issubset({vote.user_id for vote in option.votes.all()})
                for option in options
            )
            deadline_passed = bool(locked.poll_deadline and locked.poll_deadline <= now)
            if all_voted or deadline_passed:
                # Options are ordered by start time. max() keeps the earliest on a tie.
                selected = max(options, key=lambda option: _vote_score(option, active_ids))
                locked.scheduled_at = selected.starts_at
                locked.schedule_revision += 1
                locked.status = Visit.Status.CONFIRMING
                locked.save(
                    update_fields=["scheduled_at", "schedule_revision", "status", "updated_at"]
                )
                AuditEvent.objects.create(
                    project=locked.project,
                    action="workflow.poll_completed",
                    target_type="Visit",
                    target_id=locked.pk,
                    detail={"reason": "all_voted" if all_voted else "deadline"},
                )
    if locked.status == Visit.Status.CONFIRMING:
        replied = set(
            locked.attendance.filter(schedule_revision=locked.schedule_revision).values_list(
                "user_id", flat=True
            )
        )
        all_replied = bool(active_ids) and active_ids.issubset(replied)
        deadline_passed = bool(locked.confirmation_deadline and locked.confirmation_deadline <= now)
        if locked.scheduled_at and (all_replied or deadline_passed):
            locked.status = Visit.Status.SCHEDULED
            locked.save(update_fields=["status", "updated_at"])
            AuditEvent.objects.create(
                project=locked.project,
                action="workflow.confirmation_completed",
                target_type="Visit",
                target_id=locked.pk,
                detail={"reason": "all_replied" if all_replied else "deadline"},
            )
    return locked


def visit_step(visit):
    if visit.status in (Visit.Status.DRAFT, Visit.Status.POLLING):
        return "poll"
    if visit.status == Visit.Status.CONFIRMING:
        return "confirm"
    if visit.status == Visit.Status.SCHEDULED:
        return "visit"
    if visit.status == Visit.Status.CANCELLED:
        return "cancelled"
    tasks = Assignment.objects.filter(dish__visit=visit, dish__is_selected=True)
    if (
        visit.dishes.filter(is_selected=True).exists()
        and not tasks.exclude(status__in=TERMINAL_ASSIGNMENTS).exists()
    ):
        return "complete"
    return "tasks"


def project_step(project, visits):
    active = [visit for visit in visits if visit.status != Visit.Status.CANCELLED]
    if not active:
        return "planning", None
    for visit in active:
        if visit_step(visit) != "complete":
            return "visits", visit
    if not ReviewDocument.objects.filter(
        project=project, dish__isnull=True, is_publication=True, status=ReviewDocument.Status.FINAL
    ).exists():
        return "summary", None
    if not project.exports.filter(kind="finance", status="final").exists():
        return "finance", None
    return "complete", None
