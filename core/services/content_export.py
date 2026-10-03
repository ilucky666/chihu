import hashlib
import io
import json
import zipfile

from django.core.files.base import ContentFile

from core.models import Asset, ExportRun, ReviewDocument
from core.services.access import leader


def export_content(user, project):
    leader(user, project)
    document = ReviewDocument.objects.filter(
        project=project, dish__isnull=True, is_publication=True
    ).first()
    if not document:
        from core.services.documents import project_document

        document = project_document(user, project)
    photos = list(
        Asset.objects.filter(visit__project=project, kind=Asset.Kind.FOOD, selected=True)
        .select_related("visit__venue", "dish")
        .order_by("visit__created_at", "dish__sort_order", "sort_order", "created_at")
    )
    revisions = {
        str(doc.dish_id): doc.revision
        for doc in ReviewDocument.objects.filter(
            project=project, dish__isnull=False, is_publication=True
        )
    }
    snapshot = {
        "document_id": str(document.pk),
        "document_revision": document.revision,
        "dish_revisions": revisions,
        "photos": [str(p.pk) for p in photos],
    }
    buffer = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("总稿.md", document.body)
        for index, asset in enumerate(photos, 1):
            extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
                asset.content_type, "bin"
            )
            path = f"配图/{index:03d}-{asset.pk}.{extension}"
            with asset.file.open("rb") as file:
                raw = file.read()
            if hashlib.sha256(raw).hexdigest() != asset.sha256:
                raise ValueError("图片原件摘要不一致")
            archive.writestr(path, raw)
            manifest.append(
                {
                    "path": path,
                    "asset_id": str(asset.pk),
                    "visit": str(asset.visit_id),
                    "dish": str(asset.dish_id) if asset.dish_id else None,
                    "sha256": asset.sha256,
                }
            )
        archive.writestr(
            "索引.json",
            json.dumps({"snapshot": snapshot, "files": manifest}, ensure_ascii=False, indent=2),
        )
    raw = buffer.getvalue()
    run = ExportRun(
        project=project,
        created_by=user,
        kind="content",
        status="final",
        template_version="markdown-photos-v1",
        scope={"project_id": str(project.pk)},
        snapshot=snapshot,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    run.file.save(f"content-{run.pk}.zip", ContentFile(raw), save=False)
    run.save()
    return run


def is_stale(run):
    if run.kind != "content":
        return False
    snapshot = run.snapshot
    document = ReviewDocument.objects.filter(pk=snapshot.get("document_id")).first()
    if not document or document.revision != snapshot.get("document_revision"):
        return True
    current = {
        str(doc.dish_id): doc.revision
        for doc in ReviewDocument.objects.filter(
            project=run.project, dish__isnull=False, is_publication=True
        )
    }
    if current != snapshot.get("dish_revisions", {}):
        return True
    current_photos = [
        str(p.pk)
        for p in Asset.objects.filter(
            visit__project=run.project, kind=Asset.Kind.FOOD, selected=True
        ).order_by("visit__created_at", "dish__sort_order", "sort_order", "created_at")
    ]
    return current_photos != snapshot.get("photos", [])
