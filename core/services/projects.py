import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, Invitation, Organization, Project, ProjectMember, Venue, Visit
from core.services.access import leader


@transaction.atomic
def create_project(user, title, description=""):
    if not title.strip():
        raise ValidationError("请输入项目主题")
    organization, _ = Organization.objects.get_or_create(
        slug="eatful", defaults={"name": "Eatful 社团"}
    )
    obj = Project.objects.create(
        organization=organization, title=title.strip(), description=description, created_by=user
    )
    ProjectMember.objects.create(project=obj, user=user, role=ProjectMember.Role.LEADER)
    AuditEvent.objects.create(
        project=obj, actor=user, action="project.created", target_type="Project", target_id=obj.id
    )
    return obj


@transaction.atomic
def create_visit(user, project, venue_name, title=""):
    leader(user, project)
    if project.status == Project.Status.ARCHIVED:
        raise ValidationError("已归档项目不能新增活动")
    name = venue_name.strip()
    if not name:
        raise ValidationError("请输入店铺名称")
    venue = Venue.objects.create(organization=project.organization, name=name)
    obj = Visit.objects.create(project=project, venue=venue, title=title.strip())
    AuditEvent.objects.create(
        project=project, actor=user, action="visit.created", target_type="Visit", target_id=obj.id
    )
    return obj


def _hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


@transaction.atomic
def join_project(user, project, *, role=ProjectMember.Role.MEMBER):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("请先登录")
    locked = Project.objects.select_for_update().get(pk=project.pk)
    member = ProjectMember.objects.filter(project=locked, user=user).first()
    if member and member.active:
        return member
    if locked.status == Project.Status.ARCHIVED:
        raise ValidationError("项目已结束，不能再加入")
    if member:
        member.active = True
        member.save(update_fields=["active", "updated_at"])
    else:
        member = ProjectMember.objects.create(project=locked, user=user, role=role)
    AuditEvent.objects.create(
        project=locked,
        actor=user,
        action="member.joined",
        target_type="ProjectMember",
        target_id=member.pk,
    )
    return member


@transaction.atomic
def create_invitation(user, project, role=ProjectMember.Role.MEMBER, hours=72, max_uses=1):
    leader(user, project)
    if project.status == Project.Status.ARCHIVED:
        raise ValidationError("项目已结束，不能再邀请")
    if role not in ProjectMember.Role.values or max_uses < 1 or hours < 1 or hours > 720:
        raise ValidationError("邀请参数无效")
    raw = secrets.token_urlsafe(32)
    invite = Invitation.objects.create(
        project=project,
        token_hash=_hash_token(raw),
        role=role,
        expires_at=timezone.now() + timedelta(hours=hours),
        max_uses=max_uses,
        created_by=user,
    )
    return invite, raw


@transaction.atomic
def join_invitation(user, raw):
    try:
        invite = (
            Invitation.objects.select_for_update()
            .select_related("project")
            .get(token_hash=_hash_token(raw))
        )
    except Invitation.DoesNotExist as exc:
        raise ValidationError("邀请无效") from exc
    if invite.revoked_at or invite.expires_at <= timezone.now() or invite.uses >= invite.max_uses:
        raise ValidationError("邀请已失效")
    if ProjectMember.objects.filter(project=invite.project, user=user, active=True).exists():
        return ProjectMember.objects.get(project=invite.project, user=user)
    member = join_project(user, invite.project, role=invite.role)
    invite.uses += 1
    invite.save(update_fields=["uses", "updated_at"])
    return member


def revoke_invitation(user, invitation):
    leader(user, invitation.project)
    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
    return invitation


def update_visit_settings(
    user, visit, *, poll_deadline=None, confirmation_deadline=None, capacity=None
):
    leader(user, visit.project)
    visit.poll_deadline = poll_deadline
    visit.confirmation_deadline = confirmation_deadline
    visit.capacity = capacity
    visit.save(update_fields=["poll_deadline", "confirmation_deadline", "capacity", "updated_at"])
    return visit


@transaction.atomic
def cancel_visit(user, obj):
    leader(user, obj.project)
    if obj.status == Visit.Status.CANCELLED:
        return obj
    obj.status = Visit.Status.CANCELLED
    obj.save(update_fields=["status", "updated_at"])
    from core.models import Assignment, Notification

    Assignment.objects.filter(dish__visit=obj).exclude(status=Assignment.Status.APPROVED).update(
        status=Assignment.Status.CANCELLED
    )
    Notification.objects.filter(visit=obj, closed_at__isnull=True).update(closed_at=timezone.now())
    AuditEvent.objects.create(
        project=obj.project,
        actor=user,
        action="visit.cancelled",
        target_type="Visit",
        target_id=obj.id,
    )
    return obj


@transaction.atomic
def set_project_status(user, project, status):
    leader(user, project)
    project = Project.objects.select_for_update().get(pk=project.pk)
    allowed = {
        Project.Status.PREPARING: {Project.Status.ACTIVE, Project.Status.ARCHIVED},
        Project.Status.ACTIVE: {Project.Status.CLOSING, Project.Status.ARCHIVED},
        Project.Status.CLOSING: {Project.Status.ACTIVE, Project.Status.ARCHIVED},
        Project.Status.ARCHIVED: set(),
    }
    if status not in allowed.get(project.status, set()):
        raise ValidationError("项目状态转换无效")
    project.status = status
    project.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(
        project=project,
        actor=user,
        action="project.status",
        target_type="Project",
        target_id=project.pk,
        detail={"status": status},
    )
    return project


def set_visit_status(user, visit, status):
    leader(user, visit.project)
    if status == Visit.Status.CANCELLED:
        return cancel_visit(user, visit)
    if status == Visit.Status.SCHEDULED and visit.status == Visit.Status.CONFIRMING:
        pass
    elif status == Visit.Status.DONE and visit.status in (
        Visit.Status.CONFIRMING,
        Visit.Status.SCHEDULED,
    ):
        pass
    else:
        raise ValidationError("活动状态转换无效")
    visit.status = status
    visit.save(update_fields=["status", "updated_at"])
    return visit
