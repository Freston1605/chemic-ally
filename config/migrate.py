"""Lambda handler that runs production database migrations.

Deployed as the ``MigrateFunction`` resource in ``template.yaml`` with
``ImageConfig.Command: ["config.migrate.handler"]`` (D017). A deploy-job
helper (``scripts/run_migrator.py``) probes RDS availability first and only
invokes this handler when an instance reports ``DBInstanceStatus=available``
(D006: RDS is deliberately absent today, so the migrate step is gated, not
removed).

The handler:

1. sets ``DJANGO_SETTINGS_MODULE`` (default ``config.settings.production``);
2. calls ``django.setup()``;
3. runs ``migrate --noinput`` (R011: schema delivery);
4. runs ``createcachetable`` (S06's ``DatabaseCache`` needs it);
5. returns a JSON-serializable dict describing the result.

On any exception it logs with a traceback and re-raises so Lambda reports
``FunctionError`` — the deploy helper treats that as a deploy failure. Django
imports are kept inside the handler so importing this module is cheap.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def handler(event, context):  # noqa: ARG001 - Lambda signature
    """Run migrations and the cache-table bootstrap inside Lambda."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

    import django
    django.setup()
    from django.core.management import call_command

    try:
        call_command("migrate", "--noinput")
        call_command("createcachetable")
    except Exception:
        logger.exception("Migrator handler failed")
        raise

    body = {
        "status": "ok",
        "commands": ["migrate", "createcachetable"],
        "settings": os.environ["DJANGO_SETTINGS_MODULE"],
    }
    # Log the completion marker; CloudWatch is the deploy-job diagnostics surface.
    logger.info("Migrator completed: %s", json.dumps(body))
    return body
