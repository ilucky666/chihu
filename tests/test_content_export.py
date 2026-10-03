import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from core.models import User
from core.services import content_export, documents, files
from core.services.dishes import create_dish
from core.services.projects import create_project, create_visit


@pytest.mark.django_db
def test_content_pack_retains_snapshot_and_photo_order(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="content-leader")
    project = create_project(user, "内容包")
    visit = create_visit(user, project, "店")
    dish = create_dish(user, visit, "菜")
    review = documents.document_for_dish(user, dish)
    documents.save_document(user, review, "好吃。", 0)
    combined, _ = documents.compose_project_document(user, project)
    picture = io.BytesIO()
    Image.new("RGB", (80, 80), "red").save(picture, "PNG")
    asset = files.save_image(
        user, visit, SimpleUploadedFile("dish.png", picture.getvalue()), "food", dish=dish
    )
    asset.selected = True
    asset.save()
    run = content_export.export_content(user, project)
    assert not content_export.is_stale(run)
    with run.file.open("rb") as source, zipfile.ZipFile(io.BytesIO(source.read())) as archive:
        assert "好吃。" in archive.read("总稿.md").decode("utf-8")
        photo = next(name for name in archive.namelist() if name.startswith("配图/"))
        assert archive.read(photo) == picture.getvalue()
    documents.save_document(user, review, "更新的原文。", 1)
    assert content_export.is_stale(run)
