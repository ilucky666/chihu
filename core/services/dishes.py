from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Assignment, AuditEvent, Dish, Visit
from core.services.access import leader, membership
from core.services.scheduling import confirmed_member


@transaction.atomic
def create_dish(
    user,
    visit,
    name,
    *,
    parent=None,
    choice_group=None,
    selected=True,
    needs_review=True,
    needs_photo=True,
    quantity=1,
    unit="",
):
    leader(user, visit.project)
    if visit.status == Visit.Status.CANCELLED:
        raise ValidationError("活动已取消")
    if not name.strip():
        raise ValidationError("请输入菜名")
    if parent and parent.visit_id != visit.id or choice_group and choice_group.visit_id != visit.id:
        raise ValidationError("套餐关系不属于此活动")
    dish = Dish.objects.create(
        visit=visit,
        name=name.strip(),
        parent=parent,
        choice_group=choice_group,
        is_selected=selected,
        needs_review=needs_review,
        needs_photo=needs_photo,
        quantity=quantity,
        unit=unit,
    )
    if selected:
        if needs_review:
            Assignment.objects.create(dish=dish, kind=Assignment.Kind.REVIEW)
        if needs_photo:
            Assignment.objects.create(dish=dish, kind=Assignment.Kind.PHOTO)
    AuditEvent.objects.create(
        project=visit.project,
        actor=user,
        action="dish.created",
        target_type="Dish",
        target_id=dish.id,
    )
    return dish


@transaction.atomic
def select_choice(user, group, dish):
    leader(user, group.visit.project)
    if dish.choice_group_id != group.id:
        raise ValidationError("菜品不属于选项组")
    locked = list(Dish.objects.select_for_update().filter(choice_group=group))
    chosen = sum(item.is_selected for item in locked)
    if not dish.is_selected and chosen >= group.required_count:
        raise ValidationError("超过可选数量，请先取消其他选项")
    dish.is_selected = True
    dish.save(update_fields=["is_selected", "updated_at"])
    for kind, needed in (
        (Assignment.Kind.REVIEW, dish.needs_review),
        (Assignment.Kind.PHOTO, dish.needs_photo),
    ):
        if needed:
            Assignment.objects.get_or_create(dish=dish, kind=kind)
    return dish


@transaction.atomic
def claim(user, assignment):
    membership(user, assignment.dish.visit.project)
    if not confirmed_member(user, assignment.dish.visit):
        raise ValidationError("仅已确认参加的成员可认领")
    locked = (
        Assignment.objects.select_for_update().select_related("dish__visit").get(pk=assignment.pk)
    )
    if locked.dish.visit.status == Visit.Status.CANCELLED:
        raise ValidationError("活动已取消")
    if (
        locked.owner_id
        and locked.owner_id != user.id
        or locked.status not in (Assignment.Status.OPEN, Assignment.Status.CLAIMED)
    ):
        raise ValidationError("任务已被认领或不可认领")
    locked.owner = user
    locked.status = Assignment.Status.CLAIMED
    locked.save(update_fields=["owner", "status", "updated_at"])
    return locked


@transaction.atomic
def reassign(user, assignment, new_owner=None, reason=""):
    leader(user, assignment.dish.visit.project)
    if new_owner and not confirmed_member(new_owner, assignment.dish.visit):
        raise ValidationError("新负责人尚未确认参加")
    locked = Assignment.objects.select_for_update().get(pk=assignment.pk)
    locked.owner = new_owner
    locked.status = Assignment.Status.CLAIMED if new_owner else Assignment.Status.OPEN
    locked.reason = reason
    locked.deadline_revision += 1
    locked.save(update_fields=["owner", "status", "reason", "deadline_revision", "updated_at"])
    AuditEvent.objects.create(
        project=assignment.dish.visit.project,
        actor=user,
        action="assignment.reassigned",
        target_type="Assignment",
        target_id=locked.id,
        detail={"reason": reason},
    )
    return locked


@transaction.atomic
def submit_assignment(user, assignment):
    locked = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if locked.owner_id != user.id:
        raise ValidationError("只有负责人可以提交")
    if locked.status not in (Assignment.Status.CLAIMED, Assignment.Status.SUBMITTED):
        raise ValidationError("任务状态不允许提交")
    if locked.kind == Assignment.Kind.PHOTO and not locked.dish.assets.exists():
        raise ValidationError("请先上传并关联菜品照片")
    if (
        locked.kind == Assignment.Kind.REVIEW
        and not locked.dish.documents.filter(author=user, is_publication=False)
        .exclude(body="")
        .exists()
    ):
        raise ValidationError("请先填写测评")
    locked.status = Assignment.Status.SUBMITTED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def review_assignment(user, assignment, decision, reason=""):
    leader(user, assignment.dish.visit.project)
    locked = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if decision == "approve":
        if locked.status != Assignment.Status.SUBMITTED:
            raise ValidationError("只有已提交任务可通过")
        locked.status = Assignment.Status.APPROVED
    elif decision == "return":
        if locked.status != Assignment.Status.SUBMITTED:
            raise ValidationError("只有已提交任务可退回")
        locked.status = Assignment.Status.CLAIMED
    elif decision == "waive":
        locked.status = Assignment.Status.WAIVED
    else:
        raise ValidationError("任务审核操作无效")
    locked.reason = reason[:1000]
    locked.save(update_fields=["status", "reason", "updated_at"])
    AuditEvent.objects.create(
        project=assignment.dish.visit.project,
        actor=user,
        action=f"assignment.{decision}",
        target_type="Assignment",
        target_id=assignment.pk,
        detail={"reason": reason[:1000]},
    )
    return locked


@transaction.atomic
def set_deadline(user, assignment, value):
    leader(user, assignment.dish.visit.project)
    locked = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if locked.status in (
        Assignment.Status.CANCELLED,
        Assignment.Status.APPROVED,
        Assignment.Status.WAIVED,
    ):
        raise ValidationError("已结束任务不能设置截止时间")
    if value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("截止时间格式无效") from exc
        deadline = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    else:
        deadline = None
    locked.deadline = deadline
    locked.deadline_revision += 1
    locked.save(update_fields=["deadline", "deadline_revision", "updated_at"])
    return locked


def set_collaborator(user, assignment, collaborator, add=True):
    leader(user, assignment.dish.visit.project)
    if add and not confirmed_member(collaborator, assignment.dish.visit):
        raise ValidationError("协作者尚未确认参加")
    if add:
        assignment.collaborators.add(collaborator)
    else:
        assignment.collaborators.remove(collaborator)
    AuditEvent.objects.create(
        project=assignment.dish.visit.project,
        actor=user,
        action="assignment.collaborator",
        target_type="Assignment",
        target_id=assignment.pk,
        detail={"user_id": str(collaborator.pk), "add": add},
    )
    return assignment
