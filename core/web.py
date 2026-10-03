from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.forms import ProfileForm
from core.models import (
    Asset,
    Assignment,
    ChoiceGroup,
    Comment,
    Dish,
    Invitation,
    Notification,
    Project,
    ProjectMember,
    ReviewDocument,
    Suggestion,
    TimeOption,
    Visit,
)
from core.services import (
    access,
    dishes,
    documents,
    files,
    jobs,
    projects,
    recognition,
    reminders,
    scheduling,
    workflow,
)


def register(request):
    return render(request, "registration/register.html", status=403)


def _integer(value, label):
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label}无效") from exc


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("日期格式无效") from exc


@login_required
def home(request):
    memberships = list(
        request.user.memberships.filter(active=True)
        .select_related("project")
        .order_by("-project__updated_at")
    )
    project_cards = []
    for member in memberships:
        visits = [
            workflow.sync_visit(visit)
            for visit in member.project.visits.select_related("venue").order_by("created_at")
        ]
        stage, active_visit = workflow.project_step(member.project, visits)
        project_cards.append(
            {
                "member": member,
                "stage": stage,
                "active_visit": active_visit,
                "visit_count": len(visits),
            }
        )
    task_count = (
        Assignment.objects.filter(
            owner=request.user,
            dish__visit__project__memberships__user=request.user,
            dish__visit__project__memberships__active=True,
        )
        .exclude(status__in=workflow.TERMINAL_ASSIGNMENTS)
        .count()
    )
    unread = list(
        request.user.notifications.filter(read_at__isnull=True).order_by("-created_at")[:10]
    )
    return render(
        request,
        "core/home.html",
        {
            "memberships": memberships,
            "project_cards": project_cards,
            "task_count": task_count,
            "unread": unread,
            "directory": Paginator(
                Project.objects.exclude(status=Project.Status.ARCHIVED)
                .exclude(pk__in=[member.project_id for member in memberships])
                .select_related("created_by")
                .order_by("-created_at"),
                12,
            ).get_page(request.GET.get("page")),
        },
    )


@login_required
def task_inbox(request):
    memberships = list(ProjectMember.objects.filter(user=request.user, active=True))
    roles = {member.project_id: member.role for member in memberships}
    project_ids = list(roles)
    visits = list(
        Visit.objects.filter(project_id__in=project_ids)
        .exclude(status=Visit.Status.CANCELLED)
        .select_related("project", "venue")
        .order_by("created_at")
    )
    items = []
    for visit in visits:
        current = workflow.sync_visit(visit)
        step = workflow.visit_step(current)
        if step == "complete":
            continue
        items.append(
            {
                "visit": current,
                "step": step,
                "is_leader": roles[current.project_id] == ProjectMember.Role.LEADER,
            }
        )
    my_assignments = (
        Assignment.objects.filter(owner=request.user, dish__visit__project_id__in=project_ids)
        .exclude(status__in=workflow.TERMINAL_ASSIGNMENTS)
        .select_related("dish__visit__project")
        .order_by("deadline", "created_at")
    )
    return render(
        request,
        "core/tasks.html",
        {"items": items, "my_assignments": my_assignments},
    )


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            return redirect("profile")
    return render(
        request,
        "core/profile.html",
        {
            "form": form,
            "vision_status": recognition.configuration_status(),
            "project_count": request.user.memberships.filter(active=True).count(),
            "task_count": Assignment.objects.filter(owner=request.user)
            .exclude(status__in=workflow.TERMINAL_ASSIGNMENTS)
            .count(),
        },
    )


@login_required
@require_POST
def project_create(request):
    return redirect(
        "project",
        pk=projects.create_project(
            request.user, request.POST.get("title", ""), request.POST.get("description", "")
        ).pk,
    )


@login_required
def project_overview(request, pk):
    project = get_object_or_404(Project.objects.select_related("created_by"), pk=pk)
    if project.memberships.filter(user=request.user, active=True).exists():
        return redirect("project", pk=pk)
    return render(
        request,
        "core/project_overview.html",
        {
            "project": project,
            "can_join": project.status != Project.Status.ARCHIVED,
            "member_count": project.memberships.filter(active=True).count(),
        },
    )


@login_required
@require_POST
def project_join(request, pk):
    project = get_object_or_404(Project, pk=pk)
    member = projects.join_project(request.user, project)
    return redirect("project", pk=member.project_id)


@login_required
def project_page(request, pk):
    project = access.project(request.user, pk)
    member = access.membership(request.user, project)
    visits = [
        workflow.sync_visit(visit)
        for visit in project.visits.select_related("venue").order_by("created_at")
    ]
    current_step, active_visit = workflow.project_step(project, visits)
    for visit in visits:
        visit.workflow_step = workflow.visit_step(visit)
    return render(
        request,
        "core/project.html",
        {
            "project": project,
            "is_leader": member.role == "leader",
            "visits": visits,
            "workflow_step": current_step,
            "active_visit": active_visit,
            "members": project.memberships.filter(active=True).select_related("user"),
            "documents": project.documents.filter(dish__isnull=True),
            "claims": project.expense_claims.order_by("-created_at"),
            "invitations": project.invitations.order_by("-created_at")
            if member.role == "leader"
            else [],
        },
    )


@login_required
@require_POST
def visit_create(request, pk):
    project = access.project(request.user, pk)
    obj = projects.create_visit(
        request.user, project, request.POST.get("venue", ""), request.POST.get("title", "")
    )
    return redirect("visit", pk=obj.pk)


@login_required
@require_POST
def project_status(request, pk):
    project = access.project(request.user, pk)
    projects.set_project_status(request.user, project, request.POST.get("status"))
    return redirect("project", pk=pk)


@login_required
@require_POST
def visit_status(request, pk):
    visit = access.visit(request.user, pk)
    projects.set_visit_status(request.user, visit, request.POST.get("status"))
    return redirect("visit", pk=pk)


@login_required
@require_POST
def visit_settings(request, pk):
    obj = access.visit(request.user, pk)

    def parse(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("截止时间格式无效") from exc
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    capacity = (
        _integer(request.POST["capacity"], "人数上限") if request.POST.get("capacity") else None
    )
    if capacity is not None and capacity < 1:
        raise ValidationError("名额必须大于零")
    projects.update_visit_settings(
        request.user,
        obj,
        poll_deadline=parse(request.POST.get("poll_deadline")),
        confirmation_deadline=parse(request.POST.get("confirmation_deadline")),
        capacity=capacity,
    )
    return redirect("visit", pk=pk)


@login_required
@require_POST
def invite_create(request, pk):
    project = access.project(request.user, pk)
    _, token = projects.create_invitation(
        request.user, project, max_uses=_integer(request.POST.get("max_uses", 10), "可用次数")
    )
    return render(
        request,
        "core/invite.html",
        {"project": project, "join_url": request.build_absolute_uri(f"/join/{token}/")},
    )


@login_required
@require_POST
def invite_revoke(request, pk):
    invitation = get_object_or_404(Invitation.objects.select_related("project"), pk=pk)
    projects.revoke_invitation(request.user, invitation)
    return redirect("project", pk=invitation.project_id)


@login_required
def join(request, token):
    if request.method == "POST":
        member = projects.join_invitation(request.user, token)
        return redirect("project", pk=member.project_id)
    return render(request, "core/join.html", {"token": token})


@login_required
def visit_page(request, pk):
    obj = workflow.sync_visit(access.visit(request.user, pk))
    member = access.membership(request.user, obj.project)
    active_ids = set(obj.project.memberships.filter(active=True).values_list("user_id", flat=True))
    options = list(obj.time_options.prefetch_related("votes"))
    for option in options:
        votes = [vote for vote in option.votes.all() if vote.user_id in active_ids]
        option.yes_count = sum(v.choice == "yes" for v in votes)
        option.maybe_count = sum(v.choice == "maybe" for v in votes)
        option.no_count = sum(v.choice == "no" for v in votes)
        option.missing_count = len(active_ids) - len(votes)
        option.my_choice = next(
            (vote.choice for vote in votes if vote.user_id == request.user.id), None
        )
    step = workflow.visit_step(obj)
    step_index = {name: index for index, (name, _) in enumerate(workflow.STEPS)}.get(step, 4)
    my_attendance = obj.attendance.filter(user=request.user).first()
    return render(
        request,
        "core/visit.html",
        {
            "visit": obj,
            "is_leader": member.role == "leader",
            "workflow_step": step,
            "workflow_steps": workflow.STEPS,
            "workflow_step_index": step_index,
            "my_attendance": my_attendance,
            "vision_status": recognition.configuration_status(),
            "can_confirm_attendance": obj.status == Visit.Status.SCHEDULED
            and (not obj.confirmation_deadline or obj.confirmation_deadline >= timezone.now()),
            "is_confirmed_attendee": scheduling.confirmed_member(request.user, obj),
            "options": options,
            "attendance": obj.attendance.select_related("user"),
            "confirmed_attendees": obj.attendance.filter(
                status="yes", schedule_revision=obj.schedule_revision
            ).select_related("user"),
            "dishes": obj.dishes.prefetch_related("assignments", "assets"),
            "choices": obj.choice_groups.prefetch_related("options"),
            "assets": obj.assets.select_related("dish")
            .filter(Q(kind__in=["food", "menu", "order", "other"]) | Q(owner=request.user))
            .order_by("-created_at")
            if member.role != "leader"
            else obj.assets.select_related("dish").order_by("-created_at"),
            "orders": obj.orders.all(),
            "payments": obj.payments.select_related("payer"),
            "my_assignments": Assignment.objects.filter(dish__visit=obj, owner=request.user),
        },
    )


@login_required
@require_POST
def options_generate(request, pk):
    obj = access.visit(request.user, pk)
    scheduling.generate_options(
        request.user,
        obj,
        _date(request.POST.get("start")),
        _date(request.POST.get("end")),
        tuple(request.POST.getlist("meal") or ["lunch", "dinner"]),
    )
    return redirect("visit", pk=pk)


@login_required
@require_POST
def vote(request, pk):
    option = get_object_or_404(TimeOption, pk=pk)
    access.visit(request.user, option.visit_id)
    scheduling.cast_vote(request.user, option, request.POST.get("choice"))
    workflow.sync_visit(option.visit)
    return redirect("visit", pk=option.visit_id)


@login_required
@require_POST
def schedule(request, pk):
    obj = access.visit(request.user, pk)
    option = get_object_or_404(TimeOption, pk=request.POST.get("option"), visit=obj)
    scheduling.choose_time(request.user, obj, option)
    return redirect("visit", pk=pk)


@login_required
@require_POST
def attendance(request, pk):
    obj = access.visit(request.user, pk)
    scheduling.confirm_attendance(
        request.user,
        obj,
        request.POST.get("status"),
        _integer(request.POST.get("revision"), "排期版本"),
    )
    workflow.sync_visit(obj)
    return redirect("visit", pk=pk)


@login_required
@require_POST
def choice_create(request, pk):
    obj = access.visit(request.user, pk)
    access.leader(request.user, obj.project)
    ChoiceGroup.objects.create(
        visit=obj,
        name=request.POST.get("name", "").strip(),
        required_count=_integer(request.POST.get("required_count", 1), "可选数量"),
    )
    return redirect("visit", pk=pk)


@login_required
@require_POST
def dish_create(request, pk):
    obj = access.visit(request.user, pk)
    group = (
        get_object_or_404(ChoiceGroup, pk=request.POST["choice_group"], visit=obj)
        if request.POST.get("choice_group")
        else None
    )
    parent = (
        get_object_or_404(Dish, pk=request.POST["parent"], visit=obj)
        if request.POST.get("parent")
        else None
    )
    dishes.create_dish(
        request.user,
        obj,
        request.POST.get("name", ""),
        choice_group=group,
        parent=parent,
        selected=group is None,
        needs_review="needs_review" in request.POST,
        needs_photo="needs_photo" in request.POST,
    )
    return redirect("visit", pk=pk)


@login_required
@require_POST
def dish_update(request, pk):
    dish = get_object_or_404(Dish.objects.select_related("visit__project"), pk=pk)
    access.leader(request.user, dish.visit.project)
    name = request.POST.get("name", "").strip()
    if not name:
        raise ValidationError("请输入菜名")
    dish.name = name[:200]
    dish.description = request.POST.get("description", "")[:5000]
    dish.save(update_fields=["name", "description", "updated_at"])
    return redirect("visit", pk=dish.visit_id)


@login_required
@require_POST
def choice_select(request, pk):
    group = get_object_or_404(ChoiceGroup, pk=pk)
    access.visit(request.user, group.visit_id)
    dish = get_object_or_404(Dish, pk=request.POST.get("dish"), choice_group=group)
    dishes.select_choice(request.user, group, dish)
    return redirect("visit", pk=group.visit_id)


@login_required
@require_POST
def assignment_claim(request, pk):
    assignment = get_object_or_404(Assignment, pk=pk)
    access.visit(request.user, assignment.dish.visit_id)
    dishes.claim(request.user, assignment)
    return redirect("visit", pk=assignment.dish.visit_id)


@login_required
@require_POST
def assignment_submit(request, pk):
    assignment = get_object_or_404(Assignment, pk=pk)
    access.visit(request.user, assignment.dish.visit_id)
    dishes.submit_assignment(request.user, assignment)
    return redirect("visit", pk=assignment.dish.visit_id)


@login_required
@require_POST
def assignment_manage(request, pk):
    assignment = get_object_or_404(Assignment.objects.select_related("dish__visit__project"), pk=pk)
    access.leader(request.user, assignment.dish.visit.project)
    action = request.POST.get("action")
    if action in ("reassign", "collaborator_add", "collaborator_remove"):
        from core.models import User

        owner_id = request.POST.get("owner")
        owner = get_object_or_404(User, pk=owner_id) if owner_id else None
        if action == "reassign":
            dishes.reassign(request.user, assignment, owner, request.POST.get("reason", ""))
        elif owner:
            dishes.set_collaborator(
                request.user, assignment, owner, add=action == "collaborator_add"
            )
        else:
            raise ValidationError("请选择协作者")
    elif action == "deadline":
        dishes.set_deadline(request.user, assignment, request.POST.get("deadline"))
    else:
        dishes.review_assignment(request.user, assignment, action, request.POST.get("reason", ""))
    return redirect("visit", pk=assignment.dish.visit_id)


@login_required
@require_POST
def image_upload(request, pk):
    obj = access.visit(request.user, pk)
    dish = (
        get_object_or_404(Dish, pk=request.POST["dish"], visit=obj)
        if request.POST.get("dish")
        else None
    )
    if "image" not in request.FILES:
        raise ValidationError("请选择图片")
    asset = files.save_image(
        request.user,
        obj,
        request.FILES["image"],
        request.POST.get("kind"),
        dish=dish,
        caption=request.POST.get("caption", ""),
    )
    if (
        asset.kind in (Asset.Kind.MENU, Asset.Kind.ORDER, Asset.Kind.PAYMENT)
        and recognition.configuration_status()["ready"]
        and request.POST.get("recognize") == "on"
    ):
        jobs.enqueue_recognition(asset, "payment" if asset.kind == Asset.Kind.PAYMENT else "menu")
    return redirect("visit", pk=pk)


@login_required
def image_download(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if not access.can_read_asset(request.user, asset):
        raise PermissionDenied
    return FileResponse(asset.file.open("rb"), as_attachment=True, filename=asset.original_name)


@login_required
def image_thumbnail(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    access.can_read_asset(request.user, asset)
    if not asset.thumbnail:
        raise Http404
    return FileResponse(asset.thumbnail.open("rb"), content_type="image/jpeg")


@login_required
@require_POST
def image_select(request, pk):
    asset = get_object_or_404(Asset.objects.select_related("visit__project"), pk=pk)
    access.leader(request.user, asset.visit.project)
    if asset.kind != Asset.Kind.FOOD:
        raise ValidationError("只有菜品照片可进入内容包")
    asset.selected = request.POST.get("selected") == "1"
    asset.save(update_fields=["selected", "updated_at"])
    return redirect("visit", pk=asset.visit_id)


@login_required
def document_page(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    access.can_read_document(request.user, doc)
    member = access.membership(request.user, doc.project)
    return render(
        request,
        "core/document.html",
        {
            "doc": doc,
            "html": documents.preview(doc.body),
            "is_leader": member.role == "leader",
            "history": doc.history.select_related("actor").order_by("-number"),
            "comments": doc.comments.select_related("author").order_by("created_at"),
            "suggestions": doc.suggestions.select_related("author").order_by("-created_at"),
        },
    )


@login_required
def dish_document(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    return redirect("document", pk=documents.document_for_dish(request.user, dish).pk)


@login_required
def my_dish_document(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    return redirect(
        "document", pk=documents.document_for_dish(request.user, dish, publication=False).pk
    )


@login_required
def project_document(request, pk):
    project = access.project(request.user, pk)
    return redirect("document", pk=documents.project_document(request.user, project).pk)


@login_required
@require_POST
def document_save(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    access.membership(request.user, doc.project)
    try:
        saved = documents.save_document(
            request.user, doc, request.POST.get("body", ""), request.POST.get("revision")
        )
    except documents.RevisionConflict as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {
                    "error": "revision_conflict",
                    "revision": exc.document.revision,
                    "body": exc.document.body,
                    "draft": exc.draft,
                },
                status=409,
            )
        return render(
            request, "core/conflict.html", {"doc": exc.document, "draft": exc.draft}, status=409
        )
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"revision": saved.revision, "status": saved.status})
    return redirect("document", pk=pk)


@login_required
@require_POST
def comment_add(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    parent = (
        get_object_or_404(Comment, pk=request.POST["parent"], document=doc)
        if request.POST.get("parent")
        else None
    )
    documents.add_comment(request.user, doc, request.POST.get("body", ""), parent=parent)
    return redirect("document", pk=pk)


@login_required
@require_POST
def comment_resolve(request, pk):
    comment = get_object_or_404(Comment.objects.select_related("document__project"), pk=pk)
    documents.resolve_comment(request.user, comment, request.POST.get("resolved") == "1")
    return redirect("document", pk=comment.document_id)


@login_required
@require_POST
def suggestion_add(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    documents.add_suggestion(
        request.user, doc, request.POST.get("body", ""), request.POST.get("note", "")
    )
    return redirect("document", pk=pk)


@login_required
@require_POST
def suggestion_decide(request, pk):
    suggestion = get_object_or_404(Suggestion, pk=pk)
    access.membership(request.user, suggestion.document.project)
    try:
        documents.decide_suggestion(
            request.user, suggestion, request.POST.get("decision") == "accept"
        )
    except documents.RevisionConflict as exc:
        return render(
            request, "core/conflict.html", {"doc": exc.document, "draft": exc.draft}, status=409
        )
    return redirect("document", pk=suggestion.document_id)


@login_required
@require_POST
def document_status(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    documents.set_status(request.user, doc, request.POST.get("status"))
    return redirect("document", pk=pk)


@login_required
@require_POST
def document_restore(request, pk):
    doc = get_object_or_404(ReviewDocument, pk=pk)
    try:
        documents.restore(
            request.user,
            doc,
            _integer(request.POST.get("number"), "历史版本"),
            _integer(request.POST.get("revision"), "当前版本"),
        )
    except documents.RevisionConflict as exc:
        return render(
            request, "core/conflict.html", {"doc": exc.document, "draft": exc.draft}, status=409
        )
    return redirect("document", pk=pk)


@login_required
@require_POST
def document_compose(request, pk):
    project = access.project(request.user, pk)
    doc, _ = documents.compose_project_document(
        request.user, project, force=request.POST.get("force") == "1"
    )
    return redirect("document", pk=doc.pk)


@login_required
@require_POST
def notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.read_at = timezone.now()
    notification.save(update_fields=["read_at", "updated_at"])
    return redirect("project", pk=notification.project_id)


@login_required
def reminder_summary(request, pk):
    project = access.project(request.user, pk)
    return render(
        request,
        "core/reminders.html",
        {"project": project, "summary": reminders.summary(request.user, project)},
    )
