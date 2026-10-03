import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from core.models import MenuSource, User
from core.services import files, jobs, recognition
from core.services.projects import create_project, create_visit


def image_file(color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(stream, "PNG")
    return SimpleUploadedFile("sample.png", stream.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_menu_review_and_idempotent_import(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="menu-leader")
    project = create_project(user, "菜单")
    visit = create_visit(user, project, "店")
    asset = files.save_image(user, visit, image_file(), "menu")
    data = {
        "items": [
            {"key": "package", "name": "双人套餐", "price": "245", "price_type": "package"},
            {"key": "drink-a", "name": "饮品甲", "parent_key": "package", "choice_group": "二选一"},
            {"key": "drink-b", "name": "饮品乙", "parent_key": "package", "choice_group": "二选一"},
        ]
    }
    source = recognition.store_menu_result(asset, data)
    assert source.candidates.count() == 3
    chosen = list(source.candidates.exclude(external_key="drink-b").values_list("id", flat=True))
    result = recognition.confirm_menu(user, source, chosen)
    assert len(result) == 2
    assert len(recognition.confirm_menu(user, source, chosen)) == 2
    assert visit.dishes.count() == 2
    with pytest.raises(ValidationError):
        recognition.parse_menu({"items": [{"name": "X", "price": "unknown"}]})


@pytest.mark.django_db
def test_payment_recognition_pending_until_confirmed(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="pay-leader")
    project = create_project(user, "付款")
    visit = create_visit(user, project, "店")
    asset = files.save_image(user, visit, image_file(), "payment")
    candidate = recognition.store_payment_result(
        asset,
        {
            "status": "success",
            "direction": "expense",
            "amount": "208",
            "merchant": "店",
            "reference_hint": "123456789",
        },
    )
    assert candidate.reference_hint == "6789"
    assert visit.payments.count() == 0
    payment = recognition.confirm_payment(user, candidate, "208")
    assert payment.amount == 208
    assert recognition.confirm_payment(user, candidate, "208").pk == payment.pk
    assert visit.payments.count() == 1
    other = files.save_image(user, visit, image_file("red"), "payment")
    failed = recognition.store_payment_result(
        other, {"status": "failed", "direction": "expense", "amount": 208}
    )
    with pytest.raises(ValidationError):
        recognition.confirm_payment(user, failed, "208")


@pytest.mark.django_db
def test_job_retry_and_dedupe(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="job-leader")
    project = create_project(user, "任务")
    visit = create_visit(user, project, "店")
    asset = files.save_image(user, visit, image_file(), "menu")
    job = jobs.enqueue_recognition(asset, "menu")
    assert jobs.enqueue_recognition(asset, "menu").pk == job.pk
    monkeypatch.setattr(jobs, "call_vision", lambda asset, kind: {"items": [{"name": "菜A"}]})
    done = jobs.process_one()
    assert done.status == "done"
    assert MenuSource.objects.get(asset=asset).candidates.count() == 1


def test_negative_expense_is_positive_amount_with_direction():
    parsed = recognition.parse_payment(
        {"status": "success", "direction": "expense", "amount": "-208"}
    )
    assert str(parsed["amount"]) == "208.00"
    assert parsed["direction"] == "expense"
