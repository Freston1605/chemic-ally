"""Origin header guard (M006/S03 -- R029 / THROT-04).

The Lambda Function URL behind CloudFront is public: ``template.yaml`` grants
``lambda:InvokeFunctionUrl`` and ``lambda:InvokeFunction`` to ``Principal "*"``
so that CloudFront can reach it without credentials. The side effect is that
ANYONE who learns the Function URL can call the application directly and skip
CloudFront entirely -- and with it every CloudFront WAF rule, including the
edge rate limit added in M006/S01.

The guard closes that bypass:

* CloudFront stamps every origin request with a custom header whose value is a
  shared secret (the NoEcho SAM parameter ``OriginSecret``; the value lives in
  CloudFormation and is never committed to the repository).
* The same secret is handed to Lambda as the ``ORIGIN_HEADER_SECRET``
  environment variable.
* This middleware refuses any request that does not carry the matching header,
  before the request reaches any view, URL resolution, session, or CSRF work.

The guard is INERT when no secret is configured (development, the test suite,
a local ``runserver``): every request is passed through untouched. Only a
deployed environment -- which always sets the variable -- enforces the check,
so the development and test workflows are unaffected by the guard.

Every refusal is also COUNTED (M007/S03): the guard publishes one
CloudWatch datapoint per refused direct call, so a caller bypassing
CloudFront is a metric and an alarm rather than a log line somebody has to
grep for. The metric is published on a best-effort basis -- a CloudWatch
failure is logged and swallowed, because the refusal is the outcome and the
metric is only a side effect.

The header name is a setting (``ORIGIN_HEADER_NAME``) so the Django side and
the CloudFront origin custom header cannot drift apart silently. The default
below MUST match the header name in ``template.yaml``.
"""

import hmac
import logging

from django.conf import settings
from django.http import HttpResponseForbidden

from chemically.metrics import publish_refusal_metric

logger = logging.getLogger(__name__)

#: Header CloudFront stamps on every origin request. Kept in sync with
#: ``template.yaml`` (``OriginHeaderName``) and with
#: ``config.settings.base.ORIGIN_HEADER_NAME``.
DEFAULT_ORIGIN_HEADER_NAME = "X-ChemicAlly-Origin"

#: Body of the refusal. Deliberately terse and free of any detail about the
#: expected value -- a 403 must not help an attacker probe the secret.
REFUSAL_BODY = "Forbidden: request did not arrive through the CloudFront origin."


def configured_secret():
    """Return the configured shared secret, or "" when the guard is off."""
    secret = getattr(settings, "ORIGIN_HEADER_SECRET", "")
    if not secret:
        return ""
    return str(secret)


def configured_header_name():
    """Return the header name the guard expects."""
    name = getattr(settings, "ORIGIN_HEADER_NAME", "") or ""
    return name or DEFAULT_ORIGIN_HEADER_NAME


class OriginHeaderGuardMiddleware:
    """Reject requests that did not come through the CloudFront origin."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        secret = configured_secret()
        if not secret:
            # Guard not configured (development / test suite): inert.
            return self.get_response(request)

        header_name = configured_header_name()
        presented = request.headers.get(header_name, "")
        # Constant-time comparison on bytes: compare_digest rejects
        # non-ASCII str inputs, and bytes keep a hostile header value from
        # raising inside the middleware.
        if not hmac.compare_digest(
            presented.encode("utf-8"), secret.encode("utf-8")
        ):
            logger.warning(
                "Origin header guard refused a request without the "
                "CloudFront origin header (path=%s, client=%s)",
                request.path,
                request.META.get("REMOTE_ADDR", ""),
            )
            # The refusal is countable (M007/S03). The metric is a side
            # effect: the publisher swallows its own failures, and this
            # guard catches anything it might still raise, so the refusal
            # below can never become a 500.
            try:
                publish_refusal_metric()
            except Exception:  # noqa: BLE001 - the refusal must not fail
                logger.warning(
                    "Could not publish the origin guard refusal metric; "
                    "the refusal itself is unaffected",
                    exc_info=True,
                )
            return HttpResponseForbidden(
                REFUSAL_BODY, content_type="text/plain"
            )
        return self.get_response(request)
