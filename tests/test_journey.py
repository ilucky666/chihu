import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.models import Asset, ExpenseClaim, Project, TimeOption, User, Visit


def sample_png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (90, 70), color).save(buffer, "PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_leader_web_journey_from_login_to_export(client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    User.objects.create_user(username="journey-leader", password="correct-horse-2026")
    response = client.post(
        reverse("login"),
        {
            "username": "journey-leader",
            "password": "correct-horse-2026",
        },
    )
    assert response.status_code == 302
    assert User.objects.filter(username="journey-leader").exists()
    assert client.post(reverse("project-create"), {"title": "完整流程"}).status_code == 302
    project = Project.objects.get(title="完整流程")
    assert (
        client.post(reverse("visit-create", args=[project.pk]), {"venue": "试吃店"}).status_code
        == 302
    )
    visit = Visit.objects.get(project=project)
    today = date.today().isoformat()
    assert (
        client.post(
            reverse("options-generate", args=[visit.pk]),
            {"start": today, "end": today, "meal": ["lunch", "dinner"]},
        ).status_code
        == 302
    )
    option = TimeOption.objects.filter(visit=visit).first()
    assert client.post(reverse("vote", args=[option.pk]), {"choice": "yes"}).status_code == 302
    assert (
        client.post(reverse("schedule", args=[visit.pk]), {"option": str(option.pk)}).status_code
        == 302
    )
    visit.refresh_from_db()
    assert (
        client.post(
            reverse("attendance", args=[visit.pk]),
            {"status": "yes", "revision": visit.schedule_revision},
        ).status_code
        == 302
    )
    assert (
        client.post(
            reverse("dish-create", args=[visit.pk]),
            {"name": "招牌菜", "needs_review": "on", "needs_photo": "on"},
        ).status_code
        == 302
    )
    dish = visit.dishes.get()
    tasks = {task.kind: task for task in dish.assignments.all()}
    for task in tasks.values():
        assert client.post(reverse("assignment-claim", args=[task.pk])).status_code == 302
    response = client.get(reverse("my-dish-document", args=[dish.pk]))
    assert response.status_code == 302
    assert (
        client.post(response.url + "save/", {"body": "很好吃。", "revision": 0}).status_code == 302
    )
    assert client.post(reverse("assignment-submit", args=[tasks["review"].pk])).status_code == 302
    assert (
        client.post(
            reverse("image-upload", args=[visit.pk]),
            {"kind": "food", "dish": str(dish.pk), "image": sample_png("red")},
        ).status_code
        == 302
    )
    assert client.post(reverse("assignment-submit", args=[tasks["photo"].pk])).status_code == 302
    image = Asset.objects.get(visit=visit, kind="food")
    assert client.post(reverse("image-select", args=[image.pk]), {"selected": 1}).status_code == 302
    assert client.post(reverse("content-export", args=[project.pk])).status_code == 302
    assert project.exports.filter(kind="content").exists()
    assert (
        client.post(
            reverse("order-create", args=[visit.pk]), {"label": "订单1", "expected_amount": "30"}
        ).status_code
        == 302
    )
    order = visit.orders.get()
    assert (
        client.post(
            reverse("line-create", args=[order.pk]),
            {"name": "招牌菜", "quantity": "1", "price": "30", "price_type": "line"},
        ).status_code
        == 302
    )
    assert (
        client.post(reverse("payment-create", args=[visit.pk]), {"amount": "30"}).status_code == 302
    )
    payment = visit.payments.get()
    assert (
        client.post(
            reverse("allocation-create", args=[payment.pk]),
            {"order": str(order.pk), "amount": "30"},
        ).status_code
        == 302
    )
    for kind, color, target in [("order", "blue", "order"), ("payment", "green", "payment")]:
        assert (
            client.post(
                reverse("image-upload", args=[visit.pk]), {"kind": kind, "image": sample_png(color)}
            ).status_code
            == 302
        )
        asset = Asset.objects.get(visit=visit, kind=kind)
        data = {"asset": str(asset.pk), target: str(order.pk if target == "order" else payment.pk)}
        assert client.post(reverse("evidence-create", args=[visit.pk]), data).status_code == 302
    assert (
        client.post(
            reverse("claim-create", args=[project.pk]), {"visits": [str(visit.pk)]}
        ).status_code
        == 302
    )
    claim = ExpenseClaim.objects.get(project=project)
    assert (
        client.post(reverse("claim-export", args=[claim.pk]), {"mode": "final"}).status_code == 302
    )
    assert project.exports.filter(kind="finance", status="final").exists()
