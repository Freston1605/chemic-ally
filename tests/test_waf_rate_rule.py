"""Durable guard on the CloudFront WAF rate-based rule (M006/S01, R026).

Abuse is bounded at the edge: a CloudFront-scoped WAF web ACL holds a
single rate-based rule that blocks a source IP once it exceeds the
configured request budget (default 2000 requests per 5 minutes) over the
HPLC API paths and the equilibria calculator. The web ACL is bound to the
distribution by a ``WebACLAssociation`` in the same template, so a
distribution can never ship without the rate rule attached.

These tests assert the *wiring* in ``template.yaml`` -- the ACL resource,
the rate-based statement and its limit, the scope-down to the two
rate-limited path prefixes, and the association to the distribution -- and
not any AWS-side runtime behavior (there is none to observe before a
deploy). They read the tracked template as plain text and parse the
resource/parameter blocks with regexes only, so no YAML dependency is
introduced. ``SimpleTestCase`` blocks all DB access; these checks touch
only the filesystem.

The guard functions are deliberately separated from the assertions so the
same code path can be pointed at a mutated copy of the template: the
negative tests below prove that each guard actually fails when the
corresponding wiring is removed or weakened.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "template.yaml"

# The rate-limited path prefixes. Both must be counted by the rule, and
# nothing else may be (the landing page and the simulator level pages are
# deliberately left unthrottled).
HPLC_API_PREFIX = "/hplc/api/"
EQUILIBRIA_PREFIX = "/calculate/equilibria"

# The default abuse ceiling and the evaluation window it is measured over
# (300 s = 5 minutes). Pinned so a silent change to the shipped limit is a
# test failure, not a surprise in production.
EXPECTED_LIMIT = 2000
EXPECTED_WINDOW_SEC = 300


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
    following = re.search(r"^[A-Za-z][A-Za-z0-9_]*:[ \t]*$", body, re.MULTILINE)
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


def _resource(text, logical_name):
    """Return the body of a resource, or "" when it is not declared."""
    return _entry_bodies(text, "Resources").get(logical_name, "")


def _parameter(text, parameter_name):
    """Return the body of a parameter, or "" when it is not declared."""
    return _entry_bodies(text, "Parameters").get(parameter_name, "")


def _search_strings(acl_body):
    """Return every UriPath SearchString configured on the web ACL."""
    return re.findall(r"SearchString:[ \t]*(\S+)", acl_body)


def _has_web_acl(text):
    """A CloudFront-scoped WAF web ACL must exist in the template."""
    acl = _resource(text, "WafWebACL")
    if not acl:
        return False
    return (
        "Type: AWS::WAFv2::WebACL" in acl
        and re.search(r"Scope:[ \t]*CLOUDFRONT", acl) is not None
    )


def _has_rate_based_rule(text):
    """The ACL must hold a rate-based rule that blocks, per IP, over 300 s."""
    acl = _resource(text, "WafWebACL")
    if "RateBasedStatement:" not in acl:
        return False
    if "Block: {}" not in acl:
        return False
    if re.search(r"AggregateKeyType:[ \t]*IP", acl) is None:
        return False
    window = re.search(r"EvaluationWindowSec:[ \t]*(\d+)", acl)
    if window is None or int(window.group(1)) != EXPECTED_WINDOW_SEC:
        return False
    return True


def _has_parameterized_limit(text):
    """The limit must come from a reviewable template parameter."""
    acl = _resource(text, "WafWebACL")
    if re.search(r"Limit:[ \t]*!Ref[ \t]+WafRateLimit", acl) is None:
        return False
    parameter = _parameter(text, "WafRateLimit")
    if not parameter:
        return False
    if re.search(r"Type:[ \t]*Number", parameter) is None:
        return False
    default = re.search(r"Default:[ \t]*(\d+)", parameter)
    return default is not None and int(default.group(1)) == EXPECTED_LIMIT


def _scope_covers(text):
    """The rule must count exactly the two rate-limited path prefixes."""
    acl = _resource(text, "WafWebACL")
    if "ScopeDownStatement:" not in acl:
        return False
    if re.search(r"PositionalConstraint:[ \t]*STARTS_WITH", acl) is None:
        return False
    strings = _search_strings(acl)
    if HPLC_API_PREFIX not in strings:
        return False
    if EQUILIBRIA_PREFIX not in strings:
        return False
    # Nothing else may be counted: a broadened scope-down would throttle
    # ordinary browsing of the landing page and the simulator levels.
    return len(strings) == 2


def _association_binds_distribution(text):
    """A WebACLAssociation must bind the ACL to the CloudFront distribution."""
    association = _resource(text, "WafWebACLAssociation")
    if not association:
        return False
    if "Type: AWS::WAFv2::WebACLAssociation" not in association:
        return False
    if re.search(r"WebACLArn:[ \t]*!GetAtt[ \t]+WafWebACL\.Arn", association) is None:
        return False
    target = re.search(r"ResourceArn:[ \t]*(.+)$", association, re.MULTILINE)
    if target is None:
        return False
    # The ARN must be built from the distribution resource itself, so the
    # ACL and the distribution cannot drift apart (a hard-coded ARN would
    # let them).
    return "CloudFrontDistribution" in target.group(1)


class WafRateRuleWiringTests(SimpleTestCase):
    """R026 / THROT-01: the edge rate limit must stay wired and reviewable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_template_declares_cloudfront_scoped_web_acl(self):
        self.assertTrue(
            _has_web_acl(self.template),
            "template.yaml must declare an AWS::WAFv2::WebACL with "
            "Scope CLOUDFRONT so abuse is bounded at the edge (R026).",
        )

    def test_web_acl_holds_a_rate_based_rule(self):
        self.assertTrue(
            _has_rate_based_rule(self.template),
            "the WAF web ACL must hold a rate-based rule that blocks, "
            "aggregates per source IP, and is evaluated over "
            f"{EXPECTED_WINDOW_SEC} s (R026).",
        )

    def test_rate_limit_is_a_reviewable_parameter(self):
        self.assertTrue(
            _has_parameterized_limit(self.template),
            "the rate limit must be the WafRateLimit template parameter "
            f"with default {EXPECTED_LIMIT}, so the abuse ceiling stays "
            "reviewable without editing the template (R026).",
        )

    def test_rule_scope_covers_api_and_equilibria_paths(self):
        self.assertTrue(
            _scope_covers(self.template),
            "the rate rule must count requests to "
            f"'{HPLC_API_PREFIX}' and '{EQUILIBRIA_PREFIX}' and nothing "
            "else, so ordinary browsing is never throttled (R026).",
        )

    def test_web_acl_is_associated_with_the_distribution(self):
        self.assertTrue(
            _association_binds_distribution(self.template),
            "template.yaml must declare an AWS::WAFv2::WebACLAssociation "
            "that binds the web ACL to the CloudFrontDistribution, so the "
            "distribution cannot ship without the rate rule (R026).",
        )


class WafRateRuleGuardNegativeTests(SimpleTestCase):
    """The guards above must fail when the wiring is removed or weakened.

    Each case mutates a copy of the tracked template and asserts that the
    corresponding guard rejects it. Without these, a guard that silently
    stopped matching (for example after a template reformat) would keep
    the suite green while the edge rate limit was no longer enforced.
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

    def test_missing_web_acl_is_detected(self):
        mutated = self._mutate(
            "    Type: AWS::WAFv2::WebACL", "    Type: AWS::WAFv2::IPSet"
        )
        self.assertFalse(
            _has_web_acl(mutated),
            "the web-ACL guard must fail when the resource type is not "
            "AWS::WAFv2::WebACL.",
        )

    def test_non_cloudfront_scope_is_detected(self):
        mutated = self._mutate("Scope: CLOUDFRONT", "Scope: REGIONAL")
        self.assertFalse(
            _has_web_acl(mutated),
            "the web-ACL guard must fail when the scope is not CLOUDFRONT.",
        )

    def test_removed_rate_based_statement_is_detected(self):
        mutated = self._mutate(
            "            RateBasedStatement:", "            ByteMatchStatement:"
        )
        self.assertFalse(
            _has_rate_based_rule(mutated),
            "the rate-rule guard must fail when the rate-based statement "
            "is gone.",
        )

    def test_non_blocking_action_is_detected(self):
        mutated = self._mutate("            Block: {}", "            Allow: {}")
        self.assertFalse(
            _has_rate_based_rule(mutated),
            "the rate-rule guard must fail when the rule no longer blocks.",
        )

    def test_wrong_evaluation_window_is_detected(self):
        mutated = self._mutate(
            "EvaluationWindowSec: 300", "EvaluationWindowSec: 60"
        )
        self.assertFalse(
            _has_rate_based_rule(mutated),
            "the rate-rule guard must fail when the evaluation window is "
            "not the pinned 300 s window.",
        )

    def test_hardcoded_limit_is_detected(self):
        mutated = self._mutate("Limit: !Ref WafRateLimit", "Limit: 1000000")
        self.assertFalse(
            _has_parameterized_limit(mutated),
            "the limit guard must fail when the limit is hard-coded instead "
            "of coming from the reviewable parameter.",
        )

    def test_changed_limit_default_is_detected(self):
        mutated = self._mutate("    Default: 2000", "    Default: 200000")
        self.assertFalse(
            _has_parameterized_limit(mutated),
            "the limit guard must fail when the parameter default drifts "
            f"away from {EXPECTED_LIMIT}.",
        )

    def test_missing_api_scope_is_detected(self):
        mutated = self._mutate(
            "SearchString: /hplc/api/", "SearchString: /hplc/other/"
        )
        self.assertFalse(
            _scope_covers(mutated),
            "the scope guard must fail when the HPLC API prefix is no "
            "longer counted.",
        )

    def test_missing_equilibria_scope_is_detected(self):
        mutated = self._mutate(
            "SearchString: /calculate/equilibria", "SearchString: /calculate/other"
        )
        self.assertFalse(
            _scope_covers(mutated),
            "the scope guard must fail when the equilibria calculator is no "
            "longer counted.",
        )

    def test_broadened_scope_is_detected(self):
        mutated = self._mutate(
            "SearchString: /calculate/equilibria", "SearchString: /"
        )
        self.assertFalse(
            _scope_covers(mutated),
            "the scope guard must fail when the rule is broadened beyond "
            "the two rate-limited prefixes.",
        )

    def test_removed_association_is_detected(self):
        mutated = self._mutate(
            "    Type: AWS::WAFv2::WebACLAssociation",
            "    Type: AWS::WAFv2::IPSet",
        )
        self.assertFalse(
            _association_binds_distribution(mutated),
            "the association guard must fail when the web ACL is not "
            "associated with the distribution.",
        )

    def test_hardcoded_distribution_arn_is_detected(self):
        mutated = self._mutate(
            'arn:aws:cloudfront::${AWS::AccountId}:distribution/'
            "${CloudFrontDistribution}",
            "arn:aws:cloudfront::123456789012:distribution/E1EXAMPLE",
        )
        self.assertFalse(
            _association_binds_distribution(mutated),
            "the association guard must fail when the resource ARN is a "
            "hard-coded literal instead of a reference to the "
            "CloudFrontDistribution resource.",
        )
