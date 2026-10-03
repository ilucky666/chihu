import pytest
from django.urls import reverse

from core.models import ExternalIdentity, ProjectMember, User
from core.services.projects import create_invitation, create_project, create_visit


@pytest.mark.django_db
def test_pages_and_project_isolation(client):
    leader = User.objects.create_user(username="web-leader", password="safe-password")
    outsider = User.objects.create_user(username="outsider", password="safe-password")
    project = create_project(leader, "测试主题")
    visit = create_visit(leader, project, "测试店铺")
    client.force_login(leader)
    for url in (
        reverse("home"),
        reverse("project", args=[project.pk]),
        reverse("visit", args=[visit.pk]),
        reverse("finance-visit", args=[visit.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200, response.content[:500]
    client.force_login(outsider)
    assert client.get(reverse("project", args=[project.pk])).status_code == 404
    assert client.get(reverse("visit", args=[visit.pk])).status_code == 404
    assert client.get(reverse("finance-visit", args=[visit.pk])).status_code == 404


@pytest.mark.django_db
def test_member_cannot_create_visit(client):
    leader = User.objects.create_user(username="leader2", password="safe-password")
    member = User.objects.create_user(username="member2", password="safe-password")
    project = create_project(leader, "主题")
    ProjectMember.objects.create(project=project, user=member)
    client.force_login(member)
    assert (
        client.post(reverse("visit-create", args=[project.pk]), {"venue": "越权店铺"}).status_code
        == 403
    )


@pytest.mark.django_db
def test_invitation_survives_login(client):
    leader = User.objects.create_user(username="invite-leader")
    project = create_project(leader, "邀请流程")
    _, token = create_invitation(leader, project)
    join_path = reverse("join", args=[token])
    assert client.get(join_path).status_code == 302
    User.objects.create_user(username="new-member", password="correct-horse-2026")
    response = client.post(
        reverse("login"),
        {
            "username": "new-member",
            "password": "correct-horse-2026",
            "next": join_path,
        },
    )
    assert response.status_code == 302 and response.url == join_path
    assert client.post(join_path).status_code == 302
    assert project.memberships.filter(user__username="new-member", active=True).exists()


@pytest.mark.django_db
def test_external_identity_is_stable_across_display_name_change():
    user = User.objects.create_user(username="identity-user", display_name="原昵称")
    identity = ExternalIdentity.objects.create(
        user=user, provider="future-platform", subject="opaque-123"
    )
    user.display_name = "新昵称"
    user.save(update_fields=["display_name"])
    assert (
        ExternalIdentity.objects.get(provider="future-platform", subject="opaque-123").user_id
        == user.pk
    )
    assert identity.user_id == user.pk


@pytest.mark.django_db
def test_invalid_form_returns_clear_400_page(client):
    user = User.objects.create_user(username="validation-user")
    client.force_login(user)
    response = client.post(reverse("project-create"), {"title": "   "})
    assert response.status_code == 400
    assert "无法完成操作".encode() in response.content
