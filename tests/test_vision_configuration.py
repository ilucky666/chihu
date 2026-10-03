import io
import json
from urllib.error import HTTPError

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.models import Job, User, VisionConfiguration, Visit
from core.services import files, recognition
from core.services.projects import create_project, create_visit


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_configurable_image_protocol(settings, tmp_path, monkeypatch, mode):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setenv("EATFUL_VISION_API_KEY", "test-only-secret")
    config = VisionConfiguration.objects.create(
        mode=mode, api_url=f"https://vision.example.org/v1/{mode}", model="vision-model"
    )
    user = User.objects.create_user(username="vision-user")
    project = create_project(user, "识别")
    visit = create_visit(user, project, "店")
    stream = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(stream, "PNG")
    asset = files.save_image(user, visit, SimpleUploadedFile("menu.png", stream.getvalue()), "menu")
    captured = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            text = '{"items":[{"name":"测试菜品"}]}'
            return json.dumps(
                {"choices": [{"message": {"content": text}}]}
                if mode == "chat_completions"
                else {"output": [{"content": [{"type": "output_text", "text": text}]}]}
            ).encode()

    def send(req, timeout):
        captured.append(req)
        assert timeout == 60
        return Response()

    monkeypatch.setattr(recognition.request, "urlopen", send)
    assert recognition.call_vision(asset, "menu")["items"][0]["name"] == "测试菜品"
    req = captured[0]
    assert (
        req.full_url == config.api_url and req.headers["Authorization"] == "Bearer test-only-secret"
    )
    payload = json.loads(req.data)
    assert payload["model"] == "vision-model"
    content = payload["messages" if mode == "chat_completions" else "input"][0]["content"]
    image_url = (
        content[1]["image_url"]["url"] if mode == "chat_completions" else content[1]["image_url"]
    )
    assert image_url.startswith("data:image/png;base64,")
    assert "test-only-secret" not in json.dumps(recognition.configuration_status())
    monkeypatch.setattr(
        recognition.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPError(config.api_url, 401, "private body", {}, None)
        ),
    )
    with pytest.raises(ValidationError, match="API 密钥无效"):
        recognition.call_vision(asset, "menu")


@pytest.mark.django_db
def test_manual_mode_never_calls_ai_and_keeps_manual_entry(client, monkeypatch):
    monkeypatch.setenv("EATFUL_VISION_API_KEY", "test-only-secret")
    monkeypatch.setenv("EATFUL_VISION_MODEL", "some-image-model")
    VisionConfiguration.objects.create(mode="manual")
    monkeypatch.setattr(
        recognition.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("手工模式不能调用外部 API"),
    )
    assert not recognition.configuration_status()["ready"]
    with pytest.raises(ValidationError, match="可继续手工录入"):
        recognition.call_vision(None, "menu")
    user = User.objects.create_user(username="manual-user")
    project = create_project(user, "手工录入")
    visit = create_visit(user, project, "店")
    client.force_login(user)
    assert (
        client.post(reverse("dish-create", args=[visit.pk]), {"name": "手工菜品"}).status_code
        == 302
    )
    assert visit.dishes.filter(name="手工菜品").exists()
    assert (
        client.post(reverse("payment-create", args=[visit.pk]), {"amount": "30"}).status_code == 302
    )


@pytest.mark.django_db
def test_missing_api_config_and_admin_only_settings(client, monkeypatch):
    monkeypatch.delenv("EATFUL_VISION_API_KEY", raising=False)
    assert not recognition.configuration_status()["ready"]
    user = User.objects.create_user(username="regular-user")
    client.force_login(user)
    assert client.get(reverse("admin:core_visionconfiguration_changelist")).status_code == 302
    assert "配置图片识别服务".encode() not in client.get(reverse("profile")).content
    with pytest.raises(ValidationError):
        VisionConfiguration(api_url="http://unsafe.example.org/v1").full_clean()


@pytest.mark.django_db
def test_each_image_can_skip_or_request_recognition(client, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setenv("EATFUL_VISION_API_KEY", "test-only-secret")
    monkeypatch.setenv("EATFUL_VISION_MODEL", "image-model")
    monkeypatch.setenv("EATFUL_VISION_MODE", "responses")
    user = User.objects.create_user(username="image-choice-user")
    project = create_project(user, "自由选择识别")
    visit = create_visit(user, project, "店")
    visit.status = Visit.Status.SCHEDULED
    visit.save()
    client.force_login(user)
    for color, choice in (("white", ""), ("red", "on")):
        stream = io.BytesIO()
        Image.new("RGB", (30, 30), color).save(stream, "PNG")
        response = client.post(
            reverse("image-upload", args=[visit.pk]),
            {
                "image": SimpleUploadedFile(f"{color}.png", stream.getvalue()),
                "kind": "menu",
                "recognize": choice,
            },
        )
        assert response.status_code == 302
    assert visit.assets.count() == 2
    assert Job.objects.count() == 1
    page = client.get(reverse("visit", args=[visit.pk]))
    assert b'name="recognize"' in page.content
    assert "取消勾选".encode() in page.content


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
