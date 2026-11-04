import io
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.db import DatabaseError, IntegrityError, transaction

from config.database import from_url
from core.models import EvidenceLink, ReviewDocument, User
from core.services import dishes
from core.services.projects import create_project, create_visit
from core.storage import PrivateOSSStorage


def test_database_urls_and_strict_mysql(monkeypatch):
    monkeypatch.setenv("MYSQL_SSL_CA", "/etc/rds-ca.pem")
    config = from_url("mysql://member:p%40ss@rds.example:3306/chihu")
    assert config["PASSWORD"] == "p@ss"
    assert config["OPTIONS"]["charset"] == "utf8mb4"
    assert config["OPTIONS"]["isolation_level"] == "read committed"
    assert config["OPTIONS"]["ssl"] == {"ca": "/etc/rds-ca.pem", "check_hostname": True}
    assert from_url("postgresql://u:p@db/chihu?sslmode=require")["OPTIONS"]["sslmode"] == "require"
    with pytest.raises(ImproperlyConfigured):
        from_url("sqlite:///tmp.db")


@pytest.mark.django_db
def test_health_is_http_probe_but_other_pages_still_require_https(client, settings):
    settings.SECURE_SSL_REDIRECT = True
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz/").status_code == 200
    assert client.get("/").status_code == 301
    with patch("core.views.connection.cursor", side_effect=DatabaseError("private details")):
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.fixture
def private_storage(monkeypatch):
    for key, value in {
        "OSS_ENDPOINT": "https://oss-cn-hangzhou.aliyuncs.com",
        "OSS_BUCKET": "test-private-bucket",
        "OSS_PROJECT": "chihu-test",
        "OSS_ACCESS_KEY_ID": "test-access",
        "OSS_ACCESS_KEY_SECRET": "test-secret",
    }.items():
        monkeypatch.setenv(key, value)
    bucket = Mock()
    bucket.object_exists.return_value = False
    with patch("core.storage.oss2.Bucket", return_value=bucket):
        yield PrivateOSSStorage(), bucket


def test_private_oss_namespace_streams_and_forbids_public_urls(private_storage):
    storage, bucket = private_storage
    bucket.get_object.return_value = io.BytesIO(b"private image")
    bucket.get_object_meta.return_value.content_length = 13
    assert storage.save("private/a.png", ContentFile(b"private image")) == "private/a.png"
    call = bucket.put_object.call_args
    assert call.args[0] == "chihu-test/private/a.png"
    assert call.kwargs["headers"] == {
        "x-oss-object-acl": "private",
        "x-oss-forbid-overwrite": "true",
    }
    with storage.open("private/a.png") as source:
        assert source.read() == b"private image"
    assert storage.size("private/a.png") == 13
    storage.delete("private/a.png")
    bucket.delete_object.assert_called_once_with("chihu-test/private/a.png")
    with pytest.raises(NotImplementedError):
        storage.url("private/a.png")
    for path in ("../other-project/key", "/other-project/key", "..\\key"):
        with pytest.raises(SuspiciousFileOperation):
            storage.exists(path)


def test_private_oss_no_partial_or_http_configuration(monkeypatch):
    monkeypatch.setenv("OSS_ENDPOINT", "http://oss.example")
    with pytest.raises(ImproperlyConfigured):
        PrivateOSSStorage()


@pytest.mark.django_db
def test_document_uniqueness_is_enforced_by_each_database():
    owner = User.objects.create_user(username="portable-owner")
    other = User.objects.create_user(username="portable-other")
    project = create_project(owner, "跨数据库唯一性")
    visit = create_visit(owner, project, "店")
    dish = dishes.create_dish(owner, visit, "菜")
    lookups = [
        {"project": project, "dish": dish, "is_publication": True},
        {"project": project, "dish": None, "is_publication": True},
        {"project": project, "dish": dish, "is_publication": False, "author": owner},
    ]
    for lookup in lookups:
        ReviewDocument.objects.get_or_create(**lookup)
        with pytest.raises(IntegrityError), transaction.atomic():
            ReviewDocument.objects.create(**lookup)
    ReviewDocument.objects.create(project=project, dish=dish, is_publication=False, author=other)


@pytest.mark.django_db
def test_evidence_nulls_remain_unique_across_databases(tmp_path, settings):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from core.models import Order, Payment
    from core.services.files import save_image

    settings.MEDIA_ROOT = tmp_path
    owner = User.objects.create_user(username="evidence-unique")
    project = create_project(owner, "报销")
    visit = create_visit(owner, project, "店")
    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, "PNG")
    asset = save_image(owner, visit, SimpleUploadedFile("receipt.png", image.getvalue()), "receipt")
    order = Order.objects.create(visit=visit, label="订单")
    payment = Payment.objects.create(visit=visit, payer=owner, amount="10")
    for targets in ({"order": order}, {"payment": payment}, {"order": order, "payment": payment}):
        EvidenceLink.objects.create(asset=asset, **targets)
        with pytest.raises(IntegrityError), transaction.atomic():
            EvidenceLink.objects.create(asset=asset, **targets)
