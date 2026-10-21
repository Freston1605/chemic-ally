"""Origin header guard: the direct Function-URL bypass around the WAF.

M006/S03 (R029 / legacy THROT-04). ``template.yaml`` grants
``lambda:InvokeFunctionUrl`` and ``lambda:InvokeFunction`` to ``Principal "*"``
so CloudFront can reach the Function URL without credentials -- but that also
means anybody who learns the URL can call the application directly and skip
CloudFront, and with it every CloudFront WAF rule.

The guard closes that bypass:

* CloudFront stamps every origin request with the custom header
  ``X-ChemicAlly-Origin`` whose value is the shared secret (the NoEcho SAM
  parameter ``OriginSecret``; the value is never committed).
* The same secret reaches Lambda as ``ORIGIN_HEADER_SECRET``.
* ``chemically.middleware.OriginHeaderGuardMiddleware`` refuses any request
  that does not carry the matching header with 403, before the request
  reaches a view.

These tests prove the behavior through the real request path (the Django test
client runs the full middleware stack) and the wiring in ``template.yaml``:
the parameter is NoEcho, the CloudFront origin sets the custom header, and the
Lambda environment carries the same value. The secret used here is a test
fixture set through ``override_settings`` -- no secret is ever required in the
repository.

Following the convention established by ``tests/test_waf_rate_rule.py``
(M006/S01), the wiring assertions are expressed as named guard functions and
each one is proved non-vacuous by a mutation-based negative test: the guard
is pointed at a deliberately broken copy of the template and must reject it.
Without those, a guard that silently stopped matching (for example after a
template reformat) would keep the suite green while the origin header guard
was no longer enforced.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from chemically.middleware import DEFAULT_ORIGIN_HEADER_NAME, REFUSAL_BODY

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "template.yaml"

# Test-only fixture value. Never a real secret: the production value lives in
# CloudFormation (NoEcho parameter) and in the deploy environment.
TEST_SECRET = "test-only-origin-secret"


def _entry_bodies(text, section):
    """Map entry name -> body text for the entries under ``section:``.

    CloudFormation entries are keyed at exactly two spaces of indentation
    and their properties are nested deeper, so a line-based scan recovers
    each entry's own body. Comment lines are skipped. No YAML dependency.
    """
    start = re.search(
        r"^" + re.escape(section) + r":[ \t]*$", text, re.MULTILINE
    )
    if start is None:
        return {}
    body = text[start.end():]
    # The section ends at the next top-level key ("Globals:", "Outputs:").
    following = re.search(
        r"^[A-Za-z][A-Za-z0-9_]*:[ \t]*$", body, re.MULTILINE
    )
    if following is not None:
        body = body[:following.start()]
    entries = {}
    keys = list(
        re.finditer(r"^  (?!\#)(\S[^\n:]*):[ \t]*$", body, re.MULTILINE)
    )
    for index, key in enumerate(keys):
        end = keys[index + 1].start() if index + 1 < len(keys) else len(body)
        entries[key.group(1)] = body[key.end():end]
    return entries


def _parameter(text, parameter_name):
    """Return the body of a template parameter, or "" when not declared."""
    return _entry_bodies(text, "Parameters").get(parameter_name, "")


def _forwarded_header_names(text):
    """Return the viewer-header allow-list under ``ForwardedValues.Headers``.

    Legacy CloudFront forwarding is an ALLOW-list: only the headers listed
    here reach the origin. ``None`` means the list could not be found.
    """
    start = re.search(
        r"^[ \t]*ForwardedValues:[ \t]*$", text, re.MULTILINE
    )
    if start is None:
        return None
    body = text[start.end():]
    headers = re.search(r"^[ \t]+Headers:[ \t]*$", body, re.MULTILINE)
    if headers is None:
        return None
    names = []
    for line in body[headers.end():].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            break
        names.append(stripped[2:].strip())
    return names


# ─────────────────────────────────────────────
# Wiring guards. Each returns True when the template enforces the guard.
# ─────────────────────────────────────────────


def _secret_parameter_is_noecho(text):
    """The shared secret must live in a NoEcho parameter with no default."""
    parameter = _parameter(text, "OriginSecret")
    if not parameter:
        return False
    if re.search(r"NoEcho:[ \t]*true", parameter) is None:
        return False
    # A committed Default would put the secret in the repository.
    return re.search(r"Default:[ \t]*\S", parameter) is None


def _lambda_receives_the_secret(text):
    """Lambda must receive the secret and the header name by reference."""
    if re.search(
        r"ORIGIN_HEADER_SECRET:[ \t]*!Ref[ \t]+OriginSecret", text
    ) is None:
        return False
    return re.search(
        r"ORIGIN_HEADER_NAME:[ \t]*!Ref[ \t]+OriginHeaderName", text
    ) is not None


def _cloudfront_origin_sets_the_header(text):
    """The CloudFront origin must stamp the header with the shared secret."""
    if re.search(r"OriginCustomHeaders:[ \t]*\n", text) is None:
        return False
    if re.search(r"HeaderName:[ \t]*!Ref[ \t]+OriginHeaderName", text) is None:
        return False
    return re.search(
        r"HeaderValue:[ \t]*!Ref[ \t]+OriginSecret", text
    ) is not None


def _header_name_matches_the_django_default(text):
    """The two sides of the guard must not drift apart silently."""
    parameter = _parameter(text, "OriginHeaderName")
    default = re.search(r"Default:[ \t]*(\S+)", parameter)
    if default is None:
        return False
    return default.group(1) == DEFAULT_ORIGIN_HEADER_NAME


def _header_is_not_forwarded_from_the_viewer(text):
    """The guard header must NOT be in the viewer-header allow-list.

    ``ForwardedValues.Headers`` forwards those headers from the viewer to
    the origin. If the guard header were on that list, a viewer could send
    its own copy of the header and impersonate CloudFront -- reopening the
    exact bypass the guard exists to close.
    """
    forwarded = _forwarded_header_names(text)
    if forwarded is None:
        return False
    parameter = _parameter(text, "OriginHeaderName")
    default = re.search(r"Default:[ \t]*(\S+)", parameter)
    if default is None:
        return False
    return default.group(1) not in forwarded


class OriginHeaderGuardRefusalTests(SimpleTestCase):
    """With the guard enabled, a request without the origin header dies."""

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_without_origin_header_is_refused(self):
        response = self.client.get("/")
        self.assertEqual(
            response.status_code,
            403,
            "a request that did not come through CloudFront must be refused "
            "with 403 (R029 / THROT-04).",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_wrong_origin_secret_is_refused(self):
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: "not-the-secret"}
        )
        self.assertEqual(
            response.status_code,
            403,
            "a request carrying the right header name but the wrong value "
            "must be refused (R029 / THROT-04).",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_truncated_origin_secret_is_refused(self):
        """A prefix of the secret is not the secret."""
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: TEST_SECRET[:-1]}
        )
        self.assertEqual(
            response.status_code,
            403,
            "a truncated secret must not pass the origin header guard.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_a_non_ascii_origin_header_is_refused_not_raised(self):
        """A hostile header value must be refused, never raise a 500.

        The guard compares bytes with ``hmac.compare_digest`` precisely so a
        non-ASCII value cannot blow up inside the middleware.
        """
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: "\xe9\xff\xfe"}
        )
        self.assertEqual(
            response.status_code,
            403,
            "a non-ASCII header value must be refused with 403, not raise.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_an_empty_origin_header_is_refused(self):
        """An empty header value is not the secret."""
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: ""}
        )
        self.assertEqual(
            response.status_code,
            403,
            "an empty origin header value must be refused.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_refusal_precedes_csrf_and_view_work(self):
        """The guard refuses before CSRF or view work happens.

        A POST without a CSRF token and without the origin header is refused
        BY THE GUARD (its own body), not by the CSRF middleware, and a
        request to a path that does not exist is refused with 403 instead of
        404 -- proof that the guard runs before URL resolution.
        """
        response = self.client.post("/", data={})
        self.assertEqual(response.status_code, 403)
        self.assertIn(
            REFUSAL_BODY,
            response.content.decode(),
            "the refusal body must be the guard's own, not Django's CSRF "
            "failure page.",
        )

        missing = self.client.get("/hplc/does-not-exist/")
        self.assertEqual(
            missing.status_code,
            403,
            "the guard must refuse before URL resolution (a missing path "
            "must not produce a 404).",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_a_differently_spelled_header_is_refused(self):
        """A near-miss header name is a different header, not the guard one.

        Django normalizes header names with ``str.title()``, so
        ``X-Chemicalally-Origin`` (three l's) is NOT the guard's
        ``X-ChemicAlly-Origin`` -- a request that carries only the near-miss
        header must still be refused.
        """
        response = self.client.get(
            "/", headers={"X-Chemicalally-Origin": TEST_SECRET}
        )
        self.assertEqual(
            response.status_code,
            403,
            "only the exact configured header name may carry the secret.",
        )


class OriginHeaderGuardAcceptanceTests(SimpleTestCase):
    """With the guard enabled, a CloudFront-flavoured request gets through."""

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_with_correct_origin_header_is_accepted(self):
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: TEST_SECRET}
        )
        self.assertEqual(
            response.status_code,
            200,
            "a request carrying the correct origin header must be served "
            "normally (R029 / THROT-04).",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_origin_header_name_is_case_insensitive(self):
        """HTTP header names are case-insensitive; the guard must be too."""
        response = self.client.get(
            "/", headers={"x-chemically-origin": TEST_SECRET}
        )
        self.assertEqual(
            response.status_code,
            200,
            "the origin header must be matched case-insensitively, like "
            "every other HTTP header.",
        )


class OriginHeaderGuardDisabledTests(SimpleTestCase):
    """Without a configured secret the guard is inert (dev / test default).

    The deployed environment always sets ``ORIGIN_HEADER_SECRET``. Everywhere
    else (local ``runserver``, the test suite, CI) the middleware must pass
    every request through untouched, so the guard never blocks development.
    """

    @override_settings(
        ORIGIN_HEADER_SECRET="",
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_request_without_origin_header_is_served(self):
        response = self.client.get("/")
        self.assertEqual(
            response.status_code,
            200,
            "with no configured secret the guard must be inert and the "
            "application must serve the request normally.",
        )
        self.assertNotIn(
            REFUSAL_BODY,
            response.content.decode(),
            "an inert guard must not emit the refusal body.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET="",
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_guard_is_inert_even_when_a_header_is_present(self):
        """An inert guard ignores whatever the client sends."""
        response = self.client.get(
            "/", headers={DEFAULT_ORIGIN_HEADER_NAME: "anything-at-all"}
        )
        self.assertEqual(
            response.status_code,
            200,
            "with no configured secret the guard must ignore any header "
            "value and let the request through.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=None,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_guard_is_inert_when_the_secret_setting_is_none(self):
        """A missing/None setting must not raise -- it disables the guard."""
        response = self.client.get("/")
        self.assertEqual(
            response.status_code,
            200,
            "a None secret means 'guard off', not 'error'.",
        )

    def test_settings_default_carries_no_secret(self):
        """No secret is required in the repository to run the suite.

        Asserted against the committed source, not the live settings value:
        a developer who exports ORIGIN_HEADER_SECRET in their own shell must
        not make this test fail.
        """
        source = (
            REPO_ROOT / "config" / "settings" / "base.py"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"^ORIGIN_HEADER_SECRET[ \t]*=[ \t]*(.+)$", source, re.MULTILINE
        )
        self.assertIsNotNone(
            match,
            "config/settings/base.py must define ORIGIN_HEADER_SECRET.",
        )
        self.assertIn(
            '""',
            match.group(1),
            "the committed default of ORIGIN_HEADER_SECRET must be empty so "
            "the guard is off outside a deployed environment and no secret "
            "is ever needed in the repository (R029 / THROT-04).",
        )


class OriginHeaderGuardWiringTests(SimpleTestCase):
    """The guard must actually be deployed, and the secret must stay secret."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_middleware_is_registered_first(self):
        """The guard must run before session, CSRF, and view work."""
        self.assertIn(
            "chemically.middleware.OriginHeaderGuardMiddleware",
            settings.MIDDLEWARE,
            "the origin header guard must be part of the middleware stack.",
        )
        self.assertEqual(
            settings.MIDDLEWARE[0],
            "chemically.middleware.OriginHeaderGuardMiddleware",
            "the origin header guard must run first so a bypass attempt is "
            "refused before any other work happens.",
        )

    def test_secret_parameter_is_noecho(self):
        self.assertTrue(
            _secret_parameter_is_noecho(self.template),
            "template.yaml must declare the OriginSecret parameter as NoEcho "
            "with no committed default, so the shared secret lives in "
            "CloudFormation only (R029 / THROT-04).",
        )

    def test_lambda_receives_the_secret(self):
        self.assertTrue(
            _lambda_receives_the_secret(self.template),
            "the Lambda environment must receive the shared secret as "
            "ORIGIN_HEADER_SECRET and the header name as ORIGIN_HEADER_NAME, "
            "both by reference to the template parameters.",
        )

    def test_cloudfront_origin_sets_the_custom_header(self):
        self.assertTrue(
            _cloudfront_origin_sets_the_header(self.template),
            "the CloudFront origin must stamp the origin custom header with "
            "the shared secret (R029 / THROT-04).",
        )

    def test_header_name_matches_the_django_default(self):
        """The two sides of the guard must not drift apart silently."""
        self.assertTrue(
            _header_name_matches_the_django_default(self.template),
            "the template's header name must match the Django default "
            "constant; otherwise CloudFront and Django would look for "
            "different headers and every request would be refused.",
        )

    def test_guard_header_is_not_forwarded_from_the_viewer(self):
        """A viewer must not be able to supply its own copy of the header.

        Legacy CloudFront forwarding is an allow-list. If the guard header
        were on it, a viewer could send the header itself and impersonate
        CloudFront -- reopening the bypass the guard exists to close.
        """
        self.assertTrue(
            _header_is_not_forwarded_from_the_viewer(self.template),
            "the origin guard header must not appear in the CloudFront "
            "viewer-header allow-list, otherwise a viewer could forge it.",
        )


class OriginHeaderGuardWiringNegativeTests(SimpleTestCase):
    """The wiring guards above must fail when the wiring is broken.

    Each case mutates a copy of the tracked template and asserts that the
    corresponding guard rejects it. Without these, a guard that silently
    stopped matching (for example after a template reformat) would keep the
    suite green while the origin header guard was no longer enforced.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.template,
            f"the template no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.template.replace(old, new, 1)

    def test_missing_noecho_is_detected(self):
        mutated = self._mutate(
            "  OriginSecret:\n    Type: String\n    NoEcho: true",
            "  OriginSecret:\n    Type: String",
        )
        self.assertFalse(
            _secret_parameter_is_noecho(mutated),
            "the secret guard must fail when the parameter is not NoEcho.",
        )

    def test_committed_default_is_detected(self):
        mutated = self._mutate(
            "  OriginSecret:\n    Type: String\n    NoEcho: true",
            "  OriginSecret:\n    Type: String\n    NoEcho: true\n"
            "    Default: committed-secret",
        )
        self.assertFalse(
            _secret_parameter_is_noecho(mutated),
            "the secret guard must fail when the parameter carries a "
            "committed default value.",
        )

    def test_missing_origin_secret_parameter_is_detected(self):
        mutated = self._mutate(
            "  OriginSecret:\n    Type: String\n    NoEcho: true",
            "  OriginSecretRenamed:\n    Type: String\n    NoEcho: true",
        )
        self.assertFalse(
            _secret_parameter_is_noecho(mutated),
            "the secret guard must fail when the parameter is not declared.",
        )

    def test_hardcoded_lambda_secret_is_detected(self):
        mutated = self._mutate(
            "        ORIGIN_HEADER_SECRET: !Ref OriginSecret",
            "        ORIGIN_HEADER_SECRET: literal-secret",
        )
        self.assertFalse(
            _lambda_receives_the_secret(mutated),
            "the Lambda guard must fail when the secret is not a reference "
            "to the template parameter.",
        )

    def test_missing_lambda_secret_is_detected(self):
        mutated = self._mutate(
            "        ORIGIN_HEADER_SECRET: !Ref OriginSecret",
            "        SOMETHING_ELSE: !Ref OriginSecret",
        )
        self.assertFalse(
            _lambda_receives_the_secret(mutated),
            "the Lambda guard must fail when ORIGIN_HEADER_SECRET is absent.",
        )

    def test_missing_origin_custom_headers_is_detected(self):
        mutated = self._mutate(
            "            OriginCustomHeaders:\n"
            "              - HeaderName: !Ref OriginHeaderName\n"
            "                HeaderValue: !Ref OriginSecret",
            "            OriginCustomHeaders: []",
        )
        self.assertFalse(
            _cloudfront_origin_sets_the_header(mutated),
            "the origin guard must fail when CloudFront no longer stamps the "
            "custom header.",
        )

    def test_hardcoded_origin_header_value_is_detected(self):
        mutated = self._mutate(
            "                HeaderValue: !Ref OriginSecret",
            "                HeaderValue: literal-secret",
        )
        self.assertFalse(
            _cloudfront_origin_sets_the_header(mutated),
            "the origin guard must fail when the header value is not a "
            "reference to the secret parameter.",
        )

    def test_wrong_origin_header_name_is_detected(self):
        mutated = self._mutate(
            "              - HeaderName: !Ref OriginHeaderName",
            "              - HeaderName: X-Some-Other-Header",
        )
        self.assertFalse(
            _cloudfront_origin_sets_the_header(mutated),
            "the origin guard must fail when the stamped header name is not "
            "the shared one.",
        )

    def test_drifted_header_name_default_is_detected(self):
        mutated = self._mutate(
            "    Default: X-ChemicAlly-Origin",
            "    Default: X-Something-Else",
        )
        self.assertFalse(
            _header_name_matches_the_django_default(mutated),
            "the header-name guard must fail when the template default "
            "drifts away from the Django default.",
        )

    def test_viewer_forwarded_guard_header_is_detected(self):
        mutated = self._mutate(
            "              - X-CSRFToken",
            "              - X-CSRFToken\n              - X-ChemicAlly-Origin",
        )
        self.assertFalse(
            _header_is_not_forwarded_from_the_viewer(mutated),
            "the viewer-forwarding guard must fail when the guard header is "
            "on the viewer allow-list, because a viewer could then forge it.",
        )
