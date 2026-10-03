from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.urls import reverse
from django.utils import timezone

from core.models import Project, ProjectMember, User, Visit
from core.services.accounts import import_users
from core.services.projects import (
    create_invitation,
    create_project,
    create_visit,
    join_invitation,
    join_project,
    set_project_status,
)

CSV = b"username,password,display_name,email\nimported-one,Horse-Soup-2026!,Member,member@example.org\n"


@pytest.mark.django_db
def test_registration_is_closed(client):
    assert client.get(reverse("register")).status_code == 403
    assert (
        client.post(
            reverse("register"),
            {
                "username": "unauthorized",
                "password1": "Horse-Soup-2026!",
                "password2": "Horse-Soup-2026!",
            },
        ).status_code
        == 403
    )
    assert not User.objects.filter(username="unauthorized").exists()
    assert reverse("register").encode() not in client.get(reverse("login")).content


@pytest.mark.django_db
def test_csv_import_dry_run_and_hashed_passwords():
    assert import_users(CSV, dry_run=True)["created"] == 1
    assert not User.objects.exists()
    assert import_users(CSV)["created"] == 1
    user = User.objects.get(username="imported-one")
    assert user.check_password("Horse-Soup-2026!")
    assert user.password != "Horse-Soup-2026!" and not user.is_staff and not user.is_superuser
    with pytest.raises(ValidationError):
        import_users(CSV)
    assert import_users(CSV, update_existing=True)["updated"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "second",
    [
        b"bad-two,123,Name,\n",
        b"imported-one,Another-Horse-2026!,Name,\n",
        b"bad-two,Horse-Soup-2026!,Name,not-an-email\n",
    ],
)
def test_csv_import_rejects_whole_batch(second):
    with pytest.raises(ValidationError):
        import_users(CSV + second)
    assert not User.objects.exists()


@pytest.mark.django_db
def test_csv_cannot_overwrite_admin():
    admin = User.objects.create_superuser(username="imported-one", password="Original-Admin-2026!")
    with pytest.raises(ValidationError):
        import_users(CSV, update_existing=True)
    admin.refresh_from_db()
    assert admin.check_password("Original-Admin-2026!")


@pytest.mark.django_db
def test_bulk_import_is_admin_only(client):
    member = User.objects.create_user(username="import-member", is_staff=True)
    client.force_login(member)
    path = reverse("admin:core_user_import")
    assert client.get(path).status_code == 403
    admin = User.objects.create_superuser(username="import-admin", password="Admin-Horse-2026!")
    client.force_login(admin)
    assert client.get(path).status_code == 200
    assert (
        client.post(
            path, {"file": SimpleUploadedFile("accounts.csv", CSV), "dry_run": "on"}
        ).status_code
        == 302
    )
    assert not User.objects.filter(username="imported-one").exists()
    assert client.post(path, {"file": SimpleUploadedFile("accounts.csv", CSV)}).status_code == 302
    assert User.objects.get(username="imported-one").check_password("Horse-Soup-2026!")


@pytest.mark.django_db
def test_profile_change_keeps_id_and_password_change_keeps_session(client):
    user = User.objects.create_user(username="old-login", password="Original-Horse-2026!")
    another = User.objects.create_user(username="taken-login")
    client.force_login(user)
    response = client.post(
        reverse("profile"),
        {
            "username": "new-login",
            "display_name": "新称呼",
            "email": "new@example.org",
            "is_staff": "on",
        },
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.username == "new-login" and not user.is_staff
    assert client.post(reverse("profile"), {"username": another.username}).status_code == 200
    user.refresh_from_db()
    assert user.username == "new-login"
    assert (
        client.post(
            reverse("password-change"),
            {
                "old_password": "wrong",
                "new_password1": "Changed-Horse-2026!",
                "new_password2": "Changed-Horse-2026!",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            reverse("password-change"),
            {
                "old_password": "Original-Horse-2026!",
                "new_password1": "Changed-Horse-2026!",
                "new_password2": "Changed-Horse-2026!",
            },
        ).status_code
        == 302
    )
    assert client.get(reverse("home")).status_code == 200
    user.refresh_from_db()
    assert user.check_password("Changed-Horse-2026!")


@pytest.mark.django_db
@pytest.mark.parametrize("status", Visit.Status.values[:-1])
def test_directory_join_any_stage_without_rewinding(client, status):
    leader = User.objects.create_user(username="directory-leader")
    newcomer = User.objects.create_user(username="directory-newcomer")
    project = create_project(leader, "可加入主题", "项目介绍")
    visit = create_visit(leader, project, "内部店铺")
    visit.status = status
    visit.notes = "内部记录不会出现在大厅"
    visit.save()
    client.force_login(newcomer)
    assert "可加入主题".encode() in client.get(reverse("home")).content
    preview = client.get(reverse("project-overview", args=[project.pk]))
    assert preview.status_code == 200 and visit.notes.encode() not in preview.content
    assert client.get(reverse("project", args=[project.pk])).status_code == 404
    path = reverse("project-join", args=[project.pk])
    assert client.get(path).status_code == 405
    assert client.post(path, {"role": "leader"}).status_code == 302
    assert client.post(path).status_code == 302
    assert ProjectMember.objects.get(project=project, user=newcomer).role == "member"
    assert project.memberships.filter(user=newcomer).count() == 1
    visit.refresh_from_db()
    assert visit.status == status and not visit.attendance.filter(user=newcomer).exists()


@pytest.mark.django_db
def test_ended_project_blocks_new_members_and_old_invitations(client):
    leader = User.objects.create_user(username="end-leader")
    newcomer = User.objects.create_user(username="end-newcomer")
    project = create_project(leader, "已结束主题")
    _, token = create_invitation(leader, project)
    set_project_status(leader, project, Project.Status.ARCHIVED)
    client.force_login(newcomer)
    assert "已结束主题".encode() not in client.get(reverse("home")).content
    assert client.post(reverse("project-join", args=[project.pk])).status_code == 400
    with pytest.raises(ValidationError):
        join_invitation(newcomer, token)
    assert project.memberships.count() == 1
    assert join_project(leader, project).role == "leader"


@pytest.mark.django_db
def test_late_join_confirm_before_deadline_and_api_directory(client):
    leader = User.objects.create_user(username="late-leader")
    newcomer = User.objects.create_user(username="late-newcomer")
    project = create_project(leader, "中途加入")
    visit = create_visit(leader, project, "店")
    visit.status = Visit.Status.SCHEDULED
    visit.scheduled_at = timezone.now() + timedelta(days=2)
    visit.schedule_revision = 1
    visit.confirmation_deadline = timezone.now() + timedelta(days=1)
    visit.save()
    assert client.post(reverse("project-join", args=[project.pk])).status_code == 302
    client.force_login(newcomer)
    assert client.get("/api/v1/project-directory/").json()["results"][0]["id"] == str(project.pk)
    assert (
        client.post(f"/api/v1/projects/{project.pk}/join/", {"role": "leader"}).json()["role"]
        == "member"
    )
    page = client.get(reverse("visit", args=[visit.pk]))
    assert "确认或调整本次参加状态".encode() in page.content
    assert (
        client.post(
            reverse("attendance", args=[visit.pk]), {"status": "yes", "revision": 1}
        ).status_code
        == 302
    )
    visit.refresh_from_db()
    assert visit.status == Visit.Status.SCHEDULED
    assert visit.attendance.get(user=newcomer).status == "yes"


@pytest.mark.django_db(transaction=True)
def test_concurrent_join_creates_one_membership_and_audit():
    leader = User.objects.create_user(username="concurrent-leader")
    member = User.objects.create_user(username="concurrent-member")
    project = create_project(leader, "并发加入")
    barrier = Barrier(2)

    def attempt(_):
        try:
            barrier.wait(timeout=5)
            return join_project(member, project).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        joined = list(pool.map(attempt, range(2)))
    assert joined[0] == joined[1]
    assert project.memberships.filter(user=member).count() == 1
    assert project.auditevent_set.filter(action="member.joined").count() == 1
