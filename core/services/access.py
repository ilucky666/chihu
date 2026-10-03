from django.core.exceptions import PermissionDenied
from django.http import Http404

from core.models import Project, ProjectMember, Visit


def membership(user, project):
    if not user.is_authenticated:
        raise PermissionDenied("请先登录")
    member = ProjectMember.objects.filter(project=project, user=user, active=True).first()
    if not member:
        raise Http404("项目不存在")
    return member


def leader(user, project):
    member = membership(user, project)
    if member.role != ProjectMember.Role.LEADER:
        raise PermissionDenied("需要组长权限")
    return member


def project(user, project_id):
    obj = Project.objects.filter(id=project_id).first()
    if not obj:
        raise Http404("项目不存在")
    membership(user, obj)
    return obj


def visit(user, visit_id):
    obj = Visit.objects.select_related("project", "venue").filter(id=visit_id).first()
    if not obj:
        raise Http404("活动不存在")
    membership(user, obj.project)
    return obj


def can_read_asset(user, asset):
    member = membership(user, asset.visit.project)
    if (
        asset.kind in ("payment", "receipt")
        and member.role != ProjectMember.Role.LEADER
        and asset.owner_id != user.id
    ):
        raise Http404("文件不存在")
    return True


def can_read_document(user, document):
    member = membership(user, document.project)
    if (
        not document.is_publication
        and document.author_id != user.id
        and member.role != ProjectMember.Role.LEADER
    ):
        raise Http404("文案不存在")
    return True
