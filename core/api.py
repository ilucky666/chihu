import json

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.http import Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.models import Assignment, ExportRun, Project, ReviewDocument
from core.services import access, dishes, documents, projects


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        return Response({"error": "validation_error", "detail": exc.messages}, status=400)
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data = {"error": getattr(exc, "default_code", "error"), "detail": response.data}
    return response


def project_data(project):
    return {
        "id": str(project.pk),
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "updated_at": project.updated_at.isoformat(),
    }


def visit_data(visit):
    return {
        "id": str(visit.pk),
        "project_id": str(visit.project_id),
        "venue": visit.venue.name,
        "title": visit.title,
        "status": visit.status,
        "scheduled_at": visit.scheduled_at.isoformat() if visit.scheduled_at else None,
        "schedule_revision": visit.schedule_revision,
    }


def paged(request, queryset, serialize):
    try:
        page_number = max(1, int(request.query_params.get("page", 1)))
        page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
    except ValueError as exc:
        raise ValidationError("分页参数无效") from exc
    page = Paginator(queryset, page_size).get_page(page_number)
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "next": page.next_page_number() if page.has_next() else None,
            "previous": page.previous_page_number() if page.has_previous() else None,
            "results": [serialize(item) for item in page.object_list],
        }
    )


@api_view(["GET"])
def me(request):
    return Response(
        {
            "id": str(request.user.pk),
            "username": request.user.username,
            "display_name": request.user.display_name,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def openapi(request):
    with (settings.BASE_DIR / "docs" / "openapi.json").open(encoding="utf-8") as source:
        return Response(json.load(source))


@api_view(["GET", "POST"])
def project_list(request):
    if request.method == "GET":
        queryset = (
            Project.objects.filter(memberships__user=request.user, memberships__active=True)
            .distinct()
            .order_by("-updated_at")
        )
        return paged(request, queryset, project_data)
    project = projects.create_project(
        request.user, request.data.get("title", ""), request.data.get("description", "")
    )
    return Response(project_data(project), status=201)


@api_view(["GET"])
def project_detail(request, pk):
    return Response(project_data(access.project(request.user, pk)))


@api_view(["GET"])
def project_directory(request):
    return paged(
        request,
        Project.objects.exclude(status=Project.Status.ARCHIVED).order_by("-created_at"),
        project_data,
    )


@api_view(["POST"])
def project_join(request, pk):
    project = Project.objects.filter(pk=pk).first()
    if not project:
        raise Http404
    member = projects.join_project(request.user, project)
    return Response({"project_id": str(project.pk), "role": member.role})


@api_view(["GET", "POST"])
def visit_list(request, pk):
    project = access.project(request.user, pk)
    if request.method == "GET":
        return paged(
            request, project.visits.select_related("venue").order_by("created_at"), visit_data
        )
    visit = projects.create_visit(
        request.user, project, request.data.get("venue", ""), request.data.get("title", "")
    )
    return Response(visit_data(visit), status=201)


@api_view(["GET"])
def visit_detail(request, pk):
    return Response(visit_data(access.visit(request.user, pk)))


@api_view(["GET"])
def assignment_list(request, pk):
    visit = access.visit(request.user, pk)
    assignments = (
        Assignment.objects.filter(dish__visit=visit)
        .select_related("dish", "owner")
        .order_by("dish__sort_order", "kind")
    )
    return paged(
        request,
        assignments,
        lambda a: {
            "id": str(a.pk),
            "dish_id": str(a.dish_id),
            "dish": a.dish.name,
            "kind": a.kind,
            "status": a.status,
            "owner_id": str(a.owner_id) if a.owner_id else None,
            "deadline": a.deadline.isoformat() if a.deadline else None,
        },
    )


@api_view(["POST"])
def assignment_action(request, pk):
    assignment = Assignment.objects.select_related("dish__visit__project").filter(pk=pk).first()
    if not assignment:
        raise Http404
    access.visit(request.user, assignment.dish.visit_id)
    action = request.data.get("action")
    if action == "claim":
        assignment = dishes.claim(request.user, assignment)
    elif action == "submit":
        assignment = dishes.submit_assignment(request.user, assignment)
    else:
        raise ValidationError("未知操作")
    return Response(
        {
            "id": str(assignment.pk),
            "status": assignment.status,
            "owner_id": str(assignment.owner_id) if assignment.owner_id else None,
        }
    )


@api_view(["GET", "PUT"])
def document_detail(request, pk):
    doc = ReviewDocument.objects.select_related("project").filter(pk=pk).first()
    if not doc:
        raise Http404
    access.can_read_document(request.user, doc)
    if request.method == "PUT":
        try:
            doc = documents.save_document(
                request.user, doc, request.data.get("body", ""), request.data.get("revision")
            )
        except documents.RevisionConflict as exc:
            return Response(
                {
                    "error": "revision_conflict",
                    "current_revision": exc.document.revision,
                    "current_body": exc.document.body,
                    "draft": exc.draft,
                },
                status=409,
            )
    return Response(
        {
            "id": str(doc.pk),
            "project_id": str(doc.project_id),
            "dish_id": str(doc.dish_id) if doc.dish_id else None,
            "title": doc.title,
            "body": doc.body,
            "revision": doc.revision,
            "status": doc.status,
        }
    )


@api_view(["GET"])
def export_list(request, pk):
    project = access.project(request.user, pk)
    access.leader(request.user, project)
    runs = ExportRun.objects.filter(project=project).order_by("-created_at")
    return paged(
        request,
        runs,
        lambda run: {
            "id": str(run.pk),
            "kind": run.kind,
            "status": run.status,
            "created_at": run.created_at.isoformat(),
            "sha256": run.sha256,
            "download_url": f"/exports/{run.pk}/download/",
        },
    )
