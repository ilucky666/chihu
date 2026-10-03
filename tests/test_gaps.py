import io
import json
import zipfile
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from core.models import Assignment, User
from core.services import dishes, documents, exports, files, finance, projects, scheduling


@pytest.mark.django_db
def test_cancel_visit_retains_history_and_stops_tasks():
    user = User.objects.create_user(username="cancel-leader")
    project = projects.create_project(user, "取消")
    visit = projects.create_visit(user, project, "店")
    task = dishes.create_dish(user, visit, "菜").assignments.get(kind="review")
    order = finance.create_order(user, visit, "费用", expected_amount=12)
    projects.cancel_visit(user, visit)
    task.refresh_from_db()
    assert task.status == Assignment.Status.CANCELLED
    assert visit.orders.filter(pk=order.pk).exists()


@pytest.mark.django_db
def test_poll_deadline_and_suggestion_base_revision():
    user = User.objects.create_user(username="cutoff-leader")
    project = projects.create_project(user, "截止")
    visit = projects.create_visit(user, project, "店")
    day = timezone.localdate()
    slot = scheduling.generate_options(user, visit, day, day)[0]
    visit.poll_deadline = timezone.now() - timedelta(seconds=1)
    visit.save()
    with pytest.raises(ValidationError):
        scheduling.cast_vote(user, slot, "yes")
    dish = dishes.create_dish(user, visit, "菜")
    doc = documents.document_for_dish(user, dish)
    documents.save_document(user, doc, "v1", 0)
    doc.refresh_from_db()
    suggestion = documents.add_suggestion(user, doc, "建议稿")
    documents.save_document(user, doc, "v2", 1)
    with pytest.raises(documents.RevisionConflict):
        documents.decide_suggestion(user, suggestion, True)
    assert doc.suggestions.get(pk=suggestion.pk).status == "open"


@pytest.mark.django_db
def test_evidence_zip_has_original_and_index(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="proof-leader")
    project = projects.create_project(user, "凭证")
    visit = projects.create_visit(user, project, "店")
    order = finance.create_order(user, visit, "单", expected_amount=30)
    finance.add_line(user, order, "菜", price=30)
    payment = finance.create_payment(user, visit, user, 30)
    finance.allocate(user, payment, order, 30)
    images = []
    for kind, color in [("order", "blue"), ("payment", "red")]:
        buffer = io.BytesIO()
        Image.new("RGB", (100, 80), color).save(buffer, "PNG")
        raw = buffer.getvalue()
        asset = files.save_image(user, visit, SimpleUploadedFile("same-name.png", raw), kind)
        finance.link_evidence(
            user,
            asset,
            order=order if kind == "order" else None,
            payment=payment if kind == "payment" else None,
        )
        images.append((asset, raw))
    claim = exports.create_claim(user, project, [visit.pk])
    run = exports.export_claim(user, claim, final=True)
    with run.file.open("rb") as source, zipfile.ZipFile(io.BytesIO(source.read())) as archive:
        index = json.loads(archive.read("索引.json"))
        assert len(index["files"]) == 2
        assert len({item["path"] for item in index["files"]}) == 2
        for asset, raw in images:
            path = next(
                item["path"] for item in index["files"] if item["asset_id"] == str(asset.pk)
            )
            assert archive.read(path) == raw
