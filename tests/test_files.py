import hashlib
import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.models import User
from core.services import files
from core.services.projects import create_project, create_visit


def png():
    out = io.BytesIO()
    Image.new("RGB", (90, 70), "purple").save(out, "PNG")
    return out.getvalue()


@pytest.mark.django_db
def test_private_original_thumbnail_and_duplicate(client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    owner = User.objects.create_user(username="file-owner")
    stranger = User.objects.create_user(username="file-stranger")
    project = create_project(owner, "文件")
    visit = create_visit(owner, project, "店")
    raw = png()
    asset = files.save_image(owner, visit, SimpleUploadedFile("proof.png", raw), "payment")
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValidationError):
        files.save_image(owner, visit, SimpleUploadedFile("again.png", raw), "payment")
    assert client.get(reverse("image-download", args=[asset.pk])).status_code == 302
    client.force_login(stranger)
    assert client.get(reverse("image-download", args=[asset.pk])).status_code == 404
    assert client.get(reverse("image-thumbnail", args=[asset.pk])).status_code == 404
    client.force_login(owner)
    assert b"".join(client.get(reverse("image-download", args=[asset.pk])).streaming_content) == raw
    assert client.get("/private-media/" + asset.file.name).status_code == 404


@pytest.mark.django_db
def test_reject_fake_image(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    owner = User.objects.create_user(username="file-fake-owner")
    project = create_project(owner, "文件")
    visit = create_visit(owner, project, "店")
    with pytest.raises(ValidationError):
        files.save_image(
            owner, visit, SimpleUploadedFile("evil.png", b"<script>alert(1)</script>"), "food"
        )
