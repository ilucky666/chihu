"""Synthetic container smoke; invoked only by .platform/validate.sh on an isolated database."""

import hashlib
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from core.models import Asset, User
from core.services.files import save_image
from core.services.projects import create_project, create_visit


def seed():
    owner = User.objects.create_user(username="platform-smoke-owner")
    User.objects.create_user(username="platform-smoke-outsider")
    project = create_project(owner, "平台隔离测试")
    visit = create_visit(owner, project, "测试店")
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "orange").save(output, "PNG")
    save_image(owner, visit, SimpleUploadedFile("test.png", output.getvalue()), "menu")
    verify()


def verify():
    asset = Asset.objects.get(original_name="test.png")
    with asset.file.open("rb") as source:
        assert hashlib.sha256(source.read()).hexdigest() == asset.sha256
    client = Client(HTTP_HOST="127.0.0.1", secure=True)
    path = f"/images/{asset.pk}/download/"
    assert client.get(path, secure=True).status_code == 302
    client.force_login(User.objects.get(username="platform-smoke-outsider"))
    # Membership denial deliberately hides the existence of another project's files.
    assert client.get(path, secure=True).status_code == 404
    client.force_login(asset.owner)
    response = client.get(path, secure=True)
    assert response.status_code == 200
    assert hashlib.sha256(b"".join(response.streaming_content)).hexdigest() == asset.sha256
    print("Private download and persisted file checksum passed")
