"""Unit tests for the migrator Lambda handler (config/migrate.py).

R011/D017: the migrator that the deploy job invokes (via ``scripts/run_migrator.py``)
must run ``migrate --noinput`` **then** ``createcachetable``, in that order, and
must re-raise any exception so Lambda reports ``FunctionError`` instead of
swallowing a failed migration.

These tests patch ``django.setup`` and ``django.core.management.call_command``
so no real migration runs against any database — pure control-flow checks, no
DB access. ``SimpleTestCase`` blocks DB access as extra insurance.
"""

import json
import os
import sys
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config.migrate  # noqa: E402

HANDLER = config.migrate.handler


class MigrateHandlerTests(SimpleTestCase):
    """Control-flow contract of the migrator Lambda handler."""

    def setUp(self):
        # config/migrate.py only *reads* DEFAULT_SETTINGS_MODULE via setdefault,
        # so under pytest (DJANGO_SETTINGS_MODULE=config.settings.development)
        # django.setup() is testable; we still mock setup() to keep the
        # test settings-agnostic and fast.
        patcher = mock.patch("django.setup")
        self.setup_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_runs_migrate_then_createcachetable_in_order(self):
        """migrate --noinput is called first, then createcachetable."""
        with mock.patch("django.core.management.call_command") as run:
            HANDLER({}, None)

        self.assertEqual(
            [mock.call("migrate", "--noinput"), mock.call("createcachetable")],
            run.call_args_list,
        )

    def test_result_is_describable_json_body(self):
        """The handler returns a JSON body describing the run."""
        with mock.patch("django.core.management.call_command"):
            result = HANDLER({}, None)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["commands"], ["migrate", "createcachetable"]
        )
        self.assertIn("settings", result)
        # The Lambda response must survive json.dumps round-tripping.
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_settings_module_defaults_to_production(self):
        """Without a pre-set DJANGO_SETTINGS_MODULE the handler defaults to
        config.settings.production (Lambda deploys set it via Globals, but the
        default protects bare invocations)."""
        sentinel = "DJANGO_SETTINGS_MODULE"
        with mock.patch.dict(os.environ):
            os.environ.pop(sentinel, None)
            with mock.patch("django.core.management.call_command"):
                result = HANDLER({}, None)
            # Assert inside patch.dict: setdefault must have taken effect.
            self.assertEqual(
                os.environ.get(sentinel), "config.settings.production"
            )
        self.assertEqual(result["status"], "ok")

    def test_failure_during_migrate_re_raises(self):
        """A failing migration is logged and re-raised (FunctionError on Lambda)."""
        boom = RuntimeError("migration 0042 is a breaking change")
        with mock.patch("django.core.management.call_command") as run:
            run.side_effect = [boom, mock.DEFAULT]
            with self.assertRaises(RuntimeError) as ctx:
                HANDLER({}, None)
        self.assertIs(ctx.exception, boom)
        # The failure must short-circuit: createcachetable never runs.
        run.assert_called_once_with("migrate", "--noinput")

    def test_failure_during_createcachetable_re_raises(self):
        with mock.patch("django.core.management.call_command") as run:
            run.side_effect = [None, RuntimeError("cache table failed")]
            with self.assertRaises(RuntimeError):
                HANDLER({}, None)
        self.assertEqual(run.call_count, 2)
