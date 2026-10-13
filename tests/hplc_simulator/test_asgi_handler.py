"""Regression tests for the Lambda handler path reconciliation.

AWS Lambda Function URLs (payload v2) strip the trailing slash from
``requestContext.http.path`` while ``rawPath`` preserves it, and Mangum
reads the normalized field. Django's ``APPEND_SLASH`` then 301-redirects to
the trailing-slash URL, which the Function URL normalizes again, producing
an infinite redirect loop on every trailing-slash route. ``config.asgi``
restores ``requestContext.http.path`` from ``rawPath`` for v2 events.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import pytest  # noqa: E402

import config.asgi  # noqa: E402


def _function_url_v2_event(raw_path, http_path, method="GET"):
    """Build a minimal Function URL (API Gateway payload v2) event."""
    return {
        "version": "2.0",
        "rawPath": raw_path,
        "rawQueryString": "",
        "headers": {"host": "localhost"},
        "body": "",
        "isBase64Encoded": False,
        "requestContext": {
            "http": {
                "method": method,
                "path": http_path,
                "sourceIp": "127.0.0.1",
            },
            "requestId": "test",
        },
    }


def _location(response):
    for key, value in response["headers"].items():
        if key.lower() == "location":
            return value
    return None


@pytest.mark.django_db
class TestFunctionUrlTrailingSlash:
    """End-to-end: drive the real ASGI app through the exported handler."""

    def test_normalized_path_is_restored(self):
        """Function URL strips the slash; the handler restores it -> 200."""
        event = _function_url_v2_event("/hplc/", "/hplc")

        response = config.asgi.handler(event, None)

        assert response["statusCode"] == 200

    def test_preserved_path_is_unchanged(self):
        """A path that already keeps its slash still resolves -> 200."""
        event = _function_url_v2_event("/hplc/", "/hplc/")

        response = config.asgi.handler(event, None)

        assert response["statusCode"] == 200

    def test_missing_slash_still_redirects(self):
        """A genuinely slash-less path is not over-rewritten -> 301."""
        event = _function_url_v2_event("/hplc", "/hplc")

        response = config.asgi.handler(event, None)

        assert response["statusCode"] == 301
        assert _location(response) == "/hplc/"


@pytest.mark.django_db
class TestNonFunctionUrlPassthrough:
    def test_v1_event_path_untouched(self):
        """An API Gateway v1 shaped event passes through unchanged."""
        event = {
            "version": "1.0",
            "path": "/hplc/",
            "httpMethod": "GET",
            "headers": {"Host": "localhost"},
            "body": "",
            "isBase64Encoded": False,
            "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        }

        response = config.asgi.handler(event, None)

        assert response["statusCode"] == 200
        assert "http" not in event["requestContext"]


class TestPathReconciliationGuard:
    """Unit-level checks of the rewrite guard, isolated from the app."""

    def test_v2_event_rewrites_http_path(self, monkeypatch):
        captured = {}

        def fake_mangum(event, context):
            captured["event"] = event
            return {"statusCode": 200}

        monkeypatch.setattr(config.asgi, "_mangum", fake_mangum)
        event = _function_url_v2_event("/hplc/", "/hplc")

        config.asgi.handler(event, None)

        assert captured["event"]["requestContext"]["http"]["path"] == "/hplc/"

    def test_non_v2_event_is_not_rewritten(self, monkeypatch):
        captured = {}

        def fake_mangum(event, context):
            captured["event"] = event
            return {"statusCode": 200}

        monkeypatch.setattr(config.asgi, "_mangum", fake_mangum)
        event = _function_url_v2_event("/hplc/", "/hplc")
        event["version"] = "1.0"

        config.asgi.handler(event, None)

        assert captured["event"]["requestContext"]["http"]["path"] == "/hplc"

    def test_incomplete_v2_event_is_not_rewritten(self, monkeypatch):
        captured = {}

        def fake_mangum(event, context):
            captured["event"] = event
            return {"statusCode": 400}

        monkeypatch.setattr(config.asgi, "_mangum", fake_mangum)
        event = {"version": "2.0", "rawPath": "/hplc/"}

        config.asgi.handler(event, None)

        assert captured["event"] == {"version": "2.0", "rawPath": "/hplc/"}
