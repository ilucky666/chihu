import base64
import io
import json

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from core.models import PersonalVisionConfiguration, User
from core.services import ai_credentials, ai_http, recognition


def image_file(color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (30, 30), color).save(stream, "PNG")
    return SimpleUploadedFile("menu.png", stream.getvalue(), content_type="image/png")


def edit_data(config, **changes):
    return {
        "mode": config.mode,
        "api_url": config.api_url,
        "model": config.model,
        "api_key": "",
        "revision": str(config.revision),
        **changes,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_personal_protocol_encrypted_at_rest_and_no_global_fallback(
    settings, tmp_path, monkeypatch, personal_ai, mode
):
    from core.services import files
    from core.services.projects import create_project, create_visit

    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="vision-user")
    other = User.objects.create_user(username="vision-other")
    config = personal_ai(user, mode=mode, key="private-user-A-key")
    assert "private-user-A-key" not in config.encrypted_api_key
    assert ai_credentials.decrypt_key(config) == "private-user-A-key"
    monkeypatch.setenv("EATFUL_VISION_API_KEY", "must-never-be-used")
    monkeypatch.setenv("EATFUL_VISION_MODEL", "legacy-model")
    assert not recognition.configuration_status(other)["ready"]
    with pytest.raises(ValidationError):
        recognition.configuration(other)
    captured = []

    def send(url, key, payload):
        captured.append((url, key, payload))
        text = '{"items":[{"name":"测试菜品"}]}'
        return (
            {"choices": [{"message": {"content": text}}]}
            if mode == "chat_completions"
            else {"output": [{"content": [{"type": "output_text", "text": text}]}]}
        )

    monkeypatch.setattr(ai_http, "post_json", send)
    visit = create_visit(user, create_project(user, "识别"), "测试店")
    asset = files.save_image(user, visit, image_file(), "menu")
    assert recognition.call_vision(asset, "menu", user=user)["items"][0]["name"] == "测试菜品"
    url, key, payload = captured[0]
    assert url == config.api_url and key == "private-user-A-key"
    assert payload["model"] == "image-model"
    content = payload["messages" if mode == "chat_completions" else "input"][0]["content"]
    data_url = (
        content[1]["image_url"]["url"] if mode == "chat_completions" else content[1]["image_url"]
    )
    assert data_url.startswith("data:image/png;base64,")
    assert "private-user-A-key" not in json.dumps(recognition.configuration_status(user))


@pytest.mark.django_db
def test_settings_self_only_no_key_echo_including_invalid_post(client, personal_ai):
    user = User.objects.create_user(username="owner")
    other = User.objects.create_user(username="other")
    config = personal_ai(user, key="never-render-this-secret")
    personal_ai(other, key="other-private-secret")
    client.force_login(user)
    page = client.get(reverse("personal-ai"))
    assert page.status_code == 200 and "no-store" in page["Cache-Control"]
    assert b"never-render-this-secret" not in page.content
    assert b"other-private-secret" not in page.content
    assert config.encrypted_api_key.encode() not in page.content
    response = client.post(
        reverse("personal-ai"), edit_data(config, user=str(other.pk), model="changed")
    )
    assert response.status_code == 302
    assert PersonalVisionConfiguration.objects.get(user=other).model == "image-model"
    config.refresh_from_db()
    assert (
        config.model == "changed"
        and ai_credentials.decrypt_key(config) == "never-render-this-secret"
    )
    invalid = client.post(
        reverse("personal-ai"), edit_data(config, model="", api_key="invalid-post-secret")
    )
    assert invalid.status_code == 400 and b"invalid-post-secret" not in invalid.content
    for name in ("personal-ai-delete", "personal-ai-test"):
        assert client.get(reverse(name)).status_code == 405
    admin = User.objects.create_superuser(
        username="admin-personal", password="local-strong-password"
    )
    client.force_login(admin)
    assert client.get("/admin/core/personalvisionconfiguration/").status_code == 404
    assert client.get("/admin/core/visionconfiguration/").status_code == 404
    assert b"never-render-this-secret" not in client.get(reverse("personal-ai")).content


@pytest.mark.django_db
def test_cipher_owner_endpoint_binding_missing_key_and_stale_edit(client, settings, personal_ai):
    user = User.objects.create_user(username="bound-user")
    config = personal_ai(user, key="context-bound-secret")
    client.force_login(user)
    assert (
        client.post(
            reverse("personal-ai"),
            edit_data(config, api_url="https://open.bigmodel.cn/api/paas/v4/chat/completions"),
        ).status_code
        == 400
    )
    assert (
        client.post(
            reverse("personal-ai"), edit_data(config, revision="", api_key="stale-secret")
        ).status_code
        == 400
    )
    config.refresh_from_db()
    assert config.api_url.startswith("https://api.openai.com/")
    config.api_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    with pytest.raises(ValidationError):
        ai_credentials.decrypt_key(config)
    config.api_url = "https://api.openai.com/v1/responses"
    other = User.objects.create_user(username="binding-other")
    config.user = other
    with pytest.raises(ValidationError):
        ai_credentials.decrypt_key(config)
    settings.AI_ENCRYPTION_KEYS = ""
    assert not recognition.configuration_status(user)["ready"]
    with pytest.raises(ValidationError):
        personal_ai(user)


@pytest.mark.django_db
def test_manual_delete_and_connection_test(client, personal_ai, monkeypatch):
    user = User.objects.create_user(username="connection")
    config = personal_ai(user)
    client.force_login(user)
    captured = []

    def send(url, key, payload):
        captured.append(payload)
        return {"output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}]}

    monkeypatch.setattr(ai_http, "post_json", send)
    response = client.post(reverse("personal-ai-test"), follow=True)
    assert response.status_code == 200 and "连接测试成功".encode() in response.content
    assert "input_image" not in json.dumps(captured)
    assert client.post(reverse("personal-ai"), edit_data(config, mode="manual")).status_code == 302
    config.refresh_from_db()
    assert config.encrypted_api_key == "" and not recognition.configuration_status(user)["ready"]
    monkeypatch.setattr(ai_http, "post_json", lambda *a: pytest.fail("manual mode"))
    with pytest.raises(ValidationError):
        recognition.call_vision(None, "menu", user=user)
    assert client.post(reverse("personal-ai-delete")).status_code == 302
    assert not PersonalVisionConfiguration.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_master_key_rotation_retains_readability(personal_ai, settings):
    user = User.objects.create_user(username="rotation")
    config = personal_ai(user, key="rotate-private-key")
    old = settings.AI_ENCRYPTION_KEYS
    new = base64.urlsafe_b64encode(b"B" * 32).decode()
    settings.AI_ENCRYPTION_KEYS = new + "," + old
    call_command("rotate_ai_keys", stdout=io.StringIO())
    settings.AI_ENCRYPTION_KEYS = new
    config.refresh_from_db()
    assert ai_credentials.decrypt_key(config) == "rotate-private-key"


@pytest.mark.django_db
def test_personal_endpoints_require_login_csrf_and_redact_post(settings, client, personal_ai):
    from django.test import Client
    from django.views.debug import SafeExceptionReporterFilter

    for name in ("personal-ai", "personal-ai-test", "personal-ai-delete"):
        assert client.get(reverse(name)).status_code == 302
    user = User.objects.create_user(username="csrf-user")
    config = personal_ai(user)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    for name in ("personal-ai", "personal-ai-test", "personal-ai-delete"):
        assert strict.post(reverse(name), edit_data(config)).status_code == 403
    settings.DEBUG = False
    client.force_login(user)
    response = client.post(
        reverse("personal-ai"), edit_data(config, model="", api_key="redacted-post-key")
    )
    safe = SafeExceptionReporterFilter().get_post_parameters(response.wsgi_request)
    assert "redacted-post-key" not in str(safe)


@pytest.mark.django_db
def test_rotation_failure_rolls_back_whole_batch(personal_ai, settings):
    from django.core.management.base import CommandError

    first = personal_ai(User.objects.create_user(username="rotate-good"))
    second = personal_ai(User.objects.create_user(username="rotate-bad"))
    original = first.encrypted_api_key
    second.encrypted_api_key = "corrupt"
    second.save(update_fields=["encrypted_api_key"])
    settings.AI_ENCRYPTION_KEYS = (
        base64.urlsafe_b64encode(b"B" * 32).decode() + "," + settings.AI_ENCRYPTION_KEYS
    )
    with pytest.raises(CommandError):
        call_command("rotate_ai_keys", stdout=io.StringIO())
    first.refresh_from_db()
    assert first.encrypted_api_key == original


@pytest.mark.parametrize(
    "mode,response",
    [
        ("responses", []),
        ("chat_completions", {"choices": []}),
        ("responses", {"output": []}),
        ("chat_completions", {"choices": [{"message": {"content": "not json"}}]}),
    ],
)
def test_invalid_provider_response(mode, response):
    with pytest.raises(ValidationError):
        recognition._extract_text(response, mode)
