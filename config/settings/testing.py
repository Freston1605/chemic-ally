"""Pytest-only Django settings.

**This module is pytest-only and is never deployed.** The Lambda container sets
``DJANGO_SETTINGS_MODULE=config.settings.production`` (see ``template.yaml``),
and local development uses ``config.settings.development``. Nothing outside the
test suite imports this module.

``base.py`` now requires ``SECRET_KEY`` (R016: no hard-coded fallback), so the
test suite needs an explicit, obviously-insecure key rather than depending on
whatever happens to be in the ambient environment or a developer's ``.env``.
``pytest.ini`` supplies the same value via pytest-env so existing tests keep
running under ``config.settings.development``; this module is the escape hatch
for tests that want test-specific settings without ambient environment state.
"""

import os

# R016: seed the key BEFORE importing development, which imports base, which
# now raises ImproperlyConfigured when SECRET_KEY is unset. This mirrors the
# os.environ.setdefault pattern in config/settings/ci.py. Without this, the
# import of development would abort before the assignment below could run.
os.environ.setdefault(
    "SECRET_KEY", "test-only-insecure-key-0123456789-abcdefghij"
)

from .development import *  # noqa: E402,F401,F403

# Re-read after the import so an explicitly provided key still wins, and so the
# module exposes the value it actually runs with.
# Deliberately long and obviously fake: satisfies Django's security.W009
# entropy/length heuristic so a settings.check() under these settings does not
# fail for a reason unrelated to the test.
SECRET_KEY = os.environ.get(
    "SECRET_KEY", "test-only-insecure-key-0123456789-abcdefghij"
)
