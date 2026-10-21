"""The throttle's shared state is a hard deployment dependency (M006/S02).

Django's ``DatabaseCache`` -- the state behind the DRF scoped throttles --
reads and writes a table that ``migrate`` does NOT create; Django ships it as
the ``createcachetable`` management command. The deploy migrator runs it
(``config/migrate.py``) and the deploy gate fails when the migrator does not
report it (``scripts/run_migrator.py``).

This test proves the RUNTIME half of that contract: if the table is missing
anyway, a throttled endpoint must fail CLOSED -- the request is refused with
a generic 503 instead of being served unthrottled, and the error must not
leak the table name or the driver detail.

The table is dropped inside the test's transaction (SQLite and PostgreSQL
both roll it back) and recreated explicitly so the suite's cache-clear
teardown keeps working.
"""

import pytest
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient

# The suite-wide "do not throttle the tests" override must NOT apply here:
# the request has to reach the real throttle to hit the cache.
pytestmark = pytest.mark.throttle_enabled


@pytest.mark.django_db
def test_missing_cache_table_fails_closed(db):
    """A throttled endpoint refuses the request instead of serving it."""
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE django_cache")

    client = APIClient(HTTP_X_FORWARDED_FOR="203.0.113.77")
    response = client.post("/hplc/api/simulate/", {}, format="json")
    status = response.status_code
    body = response.content.decode()

    # Leave the test database with the cache table back in place so the
    # suite's cache-clear teardown still works.
    call_command("createcachetable", verbosity=0)

    assert status == 503, (
        "the missing cache table did not fail closed: the request answered "
        "%s, so the throttle would have been bypassed entirely" % status
    )
    assert "django_cache" not in body, "the error leaked the table name"
    assert "sqlite3" not in body, "the error leaked the driver detail"
