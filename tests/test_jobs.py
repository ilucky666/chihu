from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Job
from core.services.jobs import claim_job


@pytest.mark.django_db
def test_expired_lease_reclaimed_then_failed_at_limit():
    job = Job.objects.create(
        kind="recognize_menu",
        key="lease-example",
        payload={"asset_id": "unused"},
        available_at=timezone.now() - timedelta(minutes=1),
        max_attempts=3,
    )
    first = claim_job()
    assert first.pk == job.pk and first.attempts == 1
    job.refresh_from_db()
    job.lease_until = timezone.now() - timedelta(seconds=1)
    job.save(update_fields=["lease_until"])
    second = claim_job()
    assert second.pk == job.pk and second.attempts == 2
    job.refresh_from_db()
    job.lease_until = timezone.now() - timedelta(seconds=1)
    job.attempts = 3
    job.save(update_fields=["lease_until", "attempts"])
    assert claim_job() is None
    job.refresh_from_db()
    assert job.status == "failed"
