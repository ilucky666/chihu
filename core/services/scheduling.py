from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Assignment, Attendance, AuditEvent, ProjectMember, TimeOption, Visit, Vote
from core.services.access import leader, membership


@transaction.atomic
def generate_options(
    user, obj, first_day, last_day, meals=("lunch", "dinner"), lunch=time(12), dinner=time(18)
):
    leader(user, obj.project)
    days = (last_day - first_day).days
    if days < 0 or days > 30 or not meals or not set(meals).issubset({"lunch", "dinner"}):
        raise ValidationError("日期范围或餐次无效")
    if obj.status not in (Visit.Status.DRAFT, Visit.Status.POLLING):
        raise ValidationError("当前活动不能生成时段")
    zone = timezone.get_current_timezone()
    created = []
    for offset in range(days + 1):
        day = first_day + timedelta(days=offset)
        for meal in meals:
            starts_at = timezone.make_aware(
                datetime.combine(day, lunch if meal == "lunch" else dinner), zone
            )
            option, was_created = TimeOption.objects.get_or_create(
                visit=obj, starts_at=starts_at, defaults={"meal": meal}
            )
            if was_created:
                created.append(option)
    obj.status = Visit.Status.POLLING
    obj.save(update_fields=["status", "updated_at"])
    return created


def cast_vote(user, option, choice):
    membership(user, option.visit.project)
    if option.visit.status != Visit.Status.POLLING:
        raise ValidationError("活动未开放投票")
    if option.visit.poll_deadline and timezone.now() > option.visit.poll_deadline:
        raise ValidationError("投票已截止")
    if choice not in Vote.Choice.values:
        raise ValidationError("投票选项无效")
    vote, _ = Vote.objects.update_or_create(option=option, user=user, defaults={"choice": choice})
    return vote


@transaction.atomic
def choose_time(user, obj, option):
    leader(user, obj.project)
    if option.visit_id != obj.id:
        raise ValidationError("候选时段不属于此活动")
    locked = Visit.objects.select_for_update().get(pk=obj.pk)
    if locked.status == Visit.Status.CANCELLED:
        raise ValidationError("活动已取消")
    if locked.scheduled_at != option.starts_at:
        locked.schedule_revision += 1
        locked.scheduled_at = option.starts_at
    locked.status = Visit.Status.CONFIRMING
    locked.save(update_fields=["scheduled_at", "schedule_revision", "status", "updated_at"])
    AuditEvent.objects.create(
        project=obj.project,
        actor=user,
        action="visit.scheduled",
        target_type="Visit",
        target_id=obj.id,
        detail={"revision": locked.schedule_revision},
    )
    return locked


@transaction.atomic
def confirm_attendance(user, obj, status, expected_revision):
    membership(user, obj.project)
    locked = Visit.objects.select_for_update().get(pk=obj.pk)
    if (
        locked.status not in (Visit.Status.CONFIRMING, Visit.Status.SCHEDULED)
        or locked.scheduled_at is None
    ):
        raise ValidationError("尚未确定到店时间")
    if locked.confirmation_deadline and timezone.now() > locked.confirmation_deadline:
        raise ValidationError("确认已截止")
    if expected_revision != locked.schedule_revision:
        raise ValidationError("活动时间已变化，请重新确认")
    if status not in Attendance.Status.values:
        raise ValidationError("确认状态无效")
    if status == Attendance.Status.YES and locked.capacity:
        others = (
            Attendance.objects.filter(
                visit=locked,
                status=Attendance.Status.YES,
                schedule_revision=locked.schedule_revision,
            )
            .exclude(user=user)
            .count()
        )
        if others >= locked.capacity:
            raise ValidationError("参加名额已满")
    attendance, _ = Attendance.objects.update_or_create(
        visit=locked,
        user=user,
        defaults={"status": status, "schedule_revision": locked.schedule_revision},
    )
    if status != Attendance.Status.YES:
        Assignment.objects.filter(dish__visit=locked, owner=user).exclude(
            status__in=[Assignment.Status.APPROVED, Assignment.Status.CANCELLED]
        ).update(owner=None, status=Assignment.Status.OPEN, reason="参加者退出，待转交")
    return attendance


def confirmed_member(user, visit):
    if not ProjectMember.objects.filter(project=visit.project, user=user, active=True).exists():
        return False
    return Attendance.objects.filter(
        visit=visit,
        user=user,
        status=Attendance.Status.YES,
        schedule_revision=visit.schedule_revision,
    ).exists()
