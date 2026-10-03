import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections
from django.test import Client
from django.utils import timezone

from core.models import ProjectMember, User
from core.services.projects import create_project, create_visit
from core.services.scheduling import choose_time, generate_options


@pytest.mark.django_db(transaction=True)
def test_ten_members_vote_and_confirm_concurrently():
    people = [User.objects.create_user(username=f"load-{index}") for index in range(10)]
    project = create_project(people[0], "十人流程")
    ProjectMember.objects.bulk_create(
        [ProjectMember(project=project, user=user) for user in people[1:]]
    )
    visit = create_visit(people[0], project, "试点店")
    today = timezone.localdate()
    option = generate_options(people[0], visit, today, today)[0]

    def vote(user):
        try:
            client = Client()
            client.force_login(user)
            start = time.perf_counter()
            assert client.get(f"/projects/{project.pk}/").status_code == 200
            assert client.post(f"/options/{option.pk}/vote/", {"choice": "yes"}).status_code == 302
            return time.perf_counter() - start
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=10) as pool:
        times = list(pool.map(vote, people))
    assert option.votes.filter(choice="yes").count() == 10
    choose_time(people[0], visit, option)
    visit.refresh_from_db()

    def confirm(user):
        try:
            client = Client()
            client.force_login(user)
            assert (
                client.post(
                    f"/visits/{visit.pk}/attendance/",
                    {"status": "yes", "revision": visit.schedule_revision},
                ).status_code
                == 302
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(confirm, people))
    assert (
        visit.attendance.filter(status="yes", schedule_revision=visit.schedule_revision).count()
        == 10
    )
    p95 = sorted(times)[-1]
    print(f"local_ten_user_vote_p95_upper={p95:.3f}s")
