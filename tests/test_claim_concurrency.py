from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from django.utils import timezone

from core.models import Assignment, ProjectMember, User
from core.services import dishes, scheduling
from core.services.projects import create_project, create_visit


@pytest.mark.django_db(transaction=True)
def test_only_one_member_can_claim_same_task():
    users = [User.objects.create_user(username=f"claim-{index}") for index in range(3)]
    project = create_project(users[0], "并发认领")
    for user in users[1:]:
        ProjectMember.objects.create(project=project, user=user)
    visit = create_visit(users[0], project, "店")
    today = timezone.localdate()
    slot = scheduling.generate_options(users[0], visit, today, today)[0]
    visit = scheduling.choose_time(users[0], visit, slot)
    for user in users[1:]:
        scheduling.confirm_attendance(user, visit, "yes", visit.schedule_revision)
    assignment = dishes.create_dish(users[0], visit, "菜").assignments.get(kind="review")
    barrier = Barrier(2)

    def attempt(user):
        try:
            barrier.wait(timeout=5)
            dishes.claim(user, assignment)
            return True
        except ValidationError:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, users[1:]))
    assert sorted(result) == [False, True]
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.CLAIMED
    assert assignment.owner in users[1:]
