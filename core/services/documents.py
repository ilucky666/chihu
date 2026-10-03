import bleach
import markdown
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, Comment, Dish, ReviewDocument, Revision, Suggestion
from core.services.access import can_read_document, leader, membership


class RevisionConflict(Exception):
    def __init__(self, document, draft):
        self.document = document
        self.draft = draft
        super().__init__("文案已由其他成员更新")


def preview(body):
    html = markdown.markdown(body, extensions=["fenced_code", "sane_lists"])
    return bleach.clean(
        html,
        tags={
            "p",
            "br",
            "strong",
            "em",
            "ul",
            "ol",
            "li",
            "blockquote",
            "h1",
            "h2",
            "h3",
            "code",
            "pre",
            "a",
        },
        attributes={"a": ["href", "title"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def document_for_dish(user, dish, publication=True):
    membership(user, dish.visit.project)
    lookup = {"project": dish.visit.project, "dish": dish, "is_publication": publication}
    if not publication:
        lookup["author"] = user
    defaults = {"title": dish.name, "author": None if publication else user}
    doc, _ = ReviewDocument.objects.get_or_create(defaults=defaults, **lookup)
    return doc


def project_document(user, project):
    membership(user, project)
    doc, _ = ReviewDocument.objects.get_or_create(
        project=project,
        dish=None,
        is_publication=True,
        defaults={"title": project.title, "author": None},
    )
    return doc


def _can_edit(user, doc):
    membership(user, doc.project)
    if not doc.is_publication and doc.author_id != user.id:
        raise PermissionDenied("只能修改自己的原始测评")
    if doc.status == ReviewDocument.Status.FINAL:
        raise ValidationError("已定稿，请联系组长重新开启")


@transaction.atomic
def save_document(user, doc, body, base_revision, reason="save"):
    if len(body) > 100_000:
        raise ValidationError("文案过长")
    try:
        expected = int(base_revision)
    except (TypeError, ValueError) as exc:
        raise ValidationError("缺少有效文案版本") from exc
    current = ReviewDocument.objects.select_for_update().get(pk=doc.pk)
    _can_edit(user, current)
    if current.revision != expected:
        raise RevisionConflict(current, body)
    current.body = body
    current.revision += 1
    if current.status == ReviewDocument.Status.APPROVED:
        current.status = ReviewDocument.Status.WRITING
    current.save(update_fields=["body", "revision", "status", "updated_at"])
    Revision.objects.create(
        document=current, number=current.revision, body=body, actor=user, reason=reason
    )
    return current


def add_comment(user, doc, body, parent=None):
    can_read_document(user, doc)
    if parent and parent.document_id != doc.id:
        raise ValidationError("回复不属于本文案")
    if not body.strip() or len(body) > 5000:
        raise ValidationError("评论内容无效")
    return Comment.objects.create(
        document=doc, author=user, revision_number=doc.revision, body=body.strip(), parent=parent
    )


def resolve_comment(user, comment, resolved=True):
    can_read_document(user, comment.document)
    member = membership(user, comment.document.project)
    if member.role != "leader" and comment.author_id != user.id:
        raise PermissionDenied("无权处理评论")
    comment.resolved = resolved
    comment.save(update_fields=["resolved", "updated_at"])
    return comment


def add_suggestion(user, doc, proposed_body, note=""):
    can_read_document(user, doc)
    if not proposed_body.strip() or len(proposed_body) > 100_000:
        raise ValidationError("建议内容无效")
    return Suggestion.objects.create(
        document=doc,
        author=user,
        base_revision=doc.revision,
        proposed_body=proposed_body,
        note=note,
    )


@transaction.atomic
def decide_suggestion(user, suggestion, accept):
    doc = suggestion.document
    member = membership(user, doc.project)
    if member.role != "leader" and (not doc.is_publication or doc.author_id != user.id):
        raise PermissionDenied("无权处理建议")
    locked = Suggestion.objects.select_for_update().get(pk=suggestion.pk)
    if locked.status != "open":
        raise ValidationError("建议已处理")
    if accept:
        if doc.revision != locked.base_revision:
            raise RevisionConflict(ReviewDocument.objects.get(pk=doc.pk), locked.proposed_body)
        save_document(user, doc, locked.proposed_body, locked.base_revision, reason="suggestion")
    locked.status = "accepted" if accept else "rejected"
    locked.decided_by = user
    locked.save(update_fields=["status", "decided_by", "updated_at"])
    return locked


@transaction.atomic
def set_status(user, doc, status):
    leader(user, doc.project)
    doc = ReviewDocument.objects.select_for_update().get(pk=doc.pk)
    if status not in ReviewDocument.Status.values:
        raise ValidationError("文案状态无效")
    if (
        status in (ReviewDocument.Status.APPROVED, ReviewDocument.Status.FINAL)
        and not doc.body.strip()
    ):
        raise ValidationError("空文案不能通过或定稿")
    if doc.status == status:
        return doc
    doc.status = status
    doc.revision += 1
    doc.save(update_fields=["status", "revision", "updated_at"])
    Revision.objects.create(
        document=doc, number=doc.revision, body=doc.body, actor=user, reason=f"status:{status}"
    )
    AuditEvent.objects.create(
        project=doc.project,
        actor=user,
        action="document.status",
        target_type="ReviewDocument",
        target_id=doc.id,
        detail={"status": status},
    )
    return doc


@transaction.atomic
def restore(user, doc, historical_number, current_revision):
    leader(user, doc.project)
    old = Revision.objects.filter(document=doc, number=historical_number).first()
    if not old:
        raise ValidationError("历史版本不存在")
    latest = ReviewDocument.objects.get(pk=doc.pk)
    if latest.revision != current_revision:
        raise RevisionConflict(latest, old.body)
    if latest.status == ReviewDocument.Status.FINAL:
        latest.status = ReviewDocument.Status.WRITING
        latest.save(update_fields=["status", "updated_at"])
    return save_document(
        user, latest, old.body, current_revision, reason=f"restore:{historical_number}"
    )


@transaction.atomic
def compose_project_document(user, project, force=False):
    leader(user, project)
    doc = project_document(user, project)
    if doc.body.strip() and not force:
        return doc, False
    sections = []
    for dish in (
        Dish.objects.filter(visit__project=project, is_selected=True)
        .select_related("visit__venue")
        .order_by("visit__created_at", "sort_order", "created_at")
    ):
        publication = dish.documents.filter(is_publication=True).first()
        if publication and publication.body.strip():
            sections.append(
                f"## {dish.visit.venue.name} · {dish.name}\n\n{publication.body.strip()}"
            )
    text = f"# {project.title}\n\n" + "\n\n".join(sections)
    doc = save_document(user, doc, text, doc.revision, reason="compose")
    doc.generated_at = timezone.now()
    doc.save(update_fields=["generated_at", "updated_at"])
    return doc, True
