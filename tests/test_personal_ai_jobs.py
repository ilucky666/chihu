import io
import json

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Job, MenuSource, ProjectMember, User, Visit
from core.services import ai_credentials, ai_http, files, jobs
from core.services.projects import create_project, create_visit


def picture(color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (30, 30), color).save(stream, "PNG")
    return SimpleUploadedFile("menu.png", stream.getvalue(), content_type="image/png")


@pytest.fixture
def asset(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="asset-leader")
    visit = create_visit(user, create_project(user, "识别测试"), "测试店")
    return files.save_image(user, visit, picture(), "menu")


def provider_result(name="测试菜品"):
    return {
        "output": [
            {"content": [{"type": "output_text", "text": json.dumps({"items": [{"name": name}]})}]}
        ]
    }


@pytest.mark.django_db
def test_queue_uses_requester_and_leader_cannot_borrow_key(client, asset, personal_ai, monkeypatch):
    leader = asset.owner
    owner = User.objects.create_user(username="image-owner")
    ProjectMember.objects.create(project=asset.visit.project, user=owner)
    asset.owner = owner
    asset.save()
    personal_ai(owner, key="owner-api-secret")
    personal_ai(leader, key="leader-api-secret")
    job = jobs.enqueue_recognition(owner, asset, "menu")
    assert jobs.enqueue_recognition(owner, asset, "menu").pk == job.pk
    seen = []

    def send(url, key, payload):
        seen.append(key)
        return provider_result()

    monkeypatch.setattr(ai_http, "post_json", send)
    assert jobs.process_one().status == "done" and seen == ["owner-api-secret"]
    job.status = "failed"
    job.save()
    with pytest.raises(PermissionDenied):
        jobs.retry_job(leader, job)
    client.force_login(leader)
    assert client.post(reverse("job-retry", args=[job.pk])).status_code == 403
    own_job = jobs.enqueue_recognition(leader, asset, "menu")
    assert own_job.pk != job.pk and own_job.requested_by == leader
    assert set(job.payload) == {"asset_id"} and "owner-api-secret" not in json.dumps(job.payload)
    assert job.key.encode() not in client.get(reverse("recognition", args=[asset.visit_id])).content
    assert list(client.get(reverse("recognition", args=[asset.visit_id])).context["jobs"]) == [
        own_job
    ]
    admin = User.objects.create_superuser(
        username="jobs-admin", password="only-test-admin-password"
    )
    client.force_login(admin)
    assert (
        client.post(
            reverse("admin:core_job_change", args=[job.pk]), {"requested_by": str(leader.pk)}
        ).status_code
        == 403
    )
    job.refresh_from_db()
    assert job.requested_by == owner


@pytest.mark.django_db
def test_changed_or_deleted_config_invalidates_old_tasks(client, asset, personal_ai, monkeypatch):
    user = asset.owner
    config = personal_ai(user)
    job = jobs.enqueue_recognition(user, asset, "menu")
    replacement = personal_ai(user, key="new-key")
    job.refresh_from_db()
    assert job.status == "failed" and replacement.revision != config.revision
    with pytest.raises(ValidationError):
        jobs.retry_job(user, job)
    job = jobs.enqueue_recognition(user, asset, "menu")

    def send(*args):
        ai_credentials.delete_configuration(user)
        return provider_result("must-not-persist")

    monkeypatch.setattr(ai_http, "post_json", send)
    assert jobs.process_one().status == "failed"
    assert not MenuSource.objects.filter(asset=asset).exists()
    client.force_login(user)
    assert (
        client.post(reverse("dish-create", args=[asset.visit_id]), {"name": "手工菜品"}).status_code
        == 302
    )


@pytest.mark.django_db
def test_worker_error_never_persists_secret_and_deactivated_user_cannot_run(
    asset, personal_ai, monkeypatch
):
    user = asset.owner
    personal_ai(user)
    job = jobs.enqueue_recognition(user, asset, "menu")
    monkeypatch.setattr(
        ai_http,
        "post_json",
        lambda *a: (_ for _ in ()).throw(RuntimeError("private-secret-in-exception")),
    )
    assert "private-secret" not in jobs.process_one().last_error
    job.refresh_from_db()
    job.available_at = timezone.now()
    job.save()
    user.is_active = False
    user.save()
    monkeypatch.setattr(ai_http, "post_json", lambda *a: pytest.fail("deactivated requester"))
    assert jobs.process_one().status != "done"


@pytest.mark.django_db
def test_image_upload_choice_belongs_to_requester(client, asset, personal_ai):
    user = asset.owner
    personal_ai(user)
    visit = asset.visit
    visit.status = Visit.Status.SCHEDULED
    visit.save()
    client.force_login(user)
    for color, choice in (("red", ""), ("blue", "on")):
        response = client.post(
            reverse("image-upload", args=[visit.pk]),
            {
                "image": picture(color),
                "kind": "menu",
                "recognize": choice,
            },
        )
        assert response.status_code == 302
    assert visit.assets.count() == 3 and Job.objects.count() == 1
    assert Job.objects.get().requested_by == user
    assert "你个人配置的 AI 服务".encode() in client.get(reverse("visit", args=[visit.pk])).content


@pytest.mark.django_db
def test_provider_cannot_echo_literal_key_into_shared_candidates(asset, personal_ai, monkeypatch):
    personal_ai(asset.owner, key="known-sensitive-key")
    jobs.enqueue_recognition(asset.owner, asset, "menu")
    monkeypatch.setattr(
        ai_http, "post_json", lambda *a: provider_result("菜名 known-sensitive-key")
    )
    assert jobs.process_one().status == "done"
    assert "known-sensitive-key" not in MenuSource.objects.get(asset=asset).candidates.get().name


@pytest.mark.django_db
def test_upgrade_stops_legacy_jobs_without_assigning_someones_credentials(asset, personal_ai):
    from importlib import import_module

    from django.apps import apps

    personal_ai(asset.owner)
    current = jobs.enqueue_recognition(asset.owner, asset, "menu")
    legacy = Job.objects.create(
        kind="recognize_menu",
        key="legacy",
        payload={"asset_id": str(asset.pk)},
        available_at=timezone.now(),
    )
    import_module("core.migrations.0006_personal_ai_credentials").stop_legacy_tasks(apps, None)
    legacy.refresh_from_db()
    current.refresh_from_db()
    assert legacy.status == "failed" and legacy.requested_by is None
    assert current.status == "queued"
