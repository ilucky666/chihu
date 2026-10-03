import pytest

from core import api_urls
from core.models import ProjectMember, User
from core.services.dishes import create_dish
from core.services.documents import document_for_dish
from core.services.projects import create_project, create_visit


@pytest.mark.django_db
def test_api_is_scoped_like_pages(client):
    leader = User.objects.create_user(username="api-leader")
    member = User.objects.create_user(username="api-member")
    stranger = User.objects.create_user(username="api-stranger")
    project = create_project(leader, "API项目")
    ProjectMember.objects.create(project=project, user=member)
    visit = create_visit(leader, project, "API店")
    dish = create_dish(leader, visit, "菜")
    doc = document_for_dish(leader, dish)
    private = document_for_dish(leader, dish, publication=False)
    client.force_login(stranger)
    assert client.get("/api/v1/projects/").json()["results"] == []
    assert client.get(f"/api/v1/projects/{project.pk}/").status_code == 404
    assert client.get(f"/api/v1/visits/{visit.pk}/").status_code == 404
    assert client.get(f"/api/v1/documents/{doc.pk}/").status_code == 404
    client.force_login(member)
    assert client.get("/api/v1/projects/").json()["results"][0]["id"] == str(project.pk)
    assert client.get(f"/api/v1/projects/{project.pk}/visits/").json()["results"][0]["id"] == str(
        visit.pk
    )
    assert client.get(f"/api/v1/projects/{project.pk}/exports/").status_code == 403
    assert client.get(f"/api/v1/documents/{private.pk}/").status_code == 404
    assert client.get(f"/documents/{private.pk}/").status_code == 404
    client.force_login(leader)
    assert (
        client.put(
            f"/api/v1/documents/{doc.pk}/",
            data={"body": "第一稿", "revision": 0},
            content_type="application/json",
        ).status_code
        == 200
    )
    response = client.put(
        f"/api/v1/documents/{doc.pk}/",
        data={"body": "冲突稿", "revision": 0},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["draft"] == "冲突稿"


def test_openapi_paths_match_runtime(client):
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    documented = set(response.json()["paths"])
    runtime = {
        "/" + str(route.pattern).replace("<uuid:pk>", "{pk}") for route in api_urls.urlpatterns
    }
    assert documented == runtime
