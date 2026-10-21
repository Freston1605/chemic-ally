"""Durable guard on the CloudFront WAF rate-based rule (R026 / THROT-01).

Abuse is bounded at the edge: a CloudFront-scoped WAF web ACL holds a
single rate-based rule that counts (or refuses) a source IP once it
exceeds the configured request budget (default 2000 requests per 5
minutes) over the HPLC API paths and the equilibria calculator.

WHERE THE RULE LIVES (M007/S01): a WAFv2 web ACL with Scope CLOUDFRONT
can only be created in us-east-1, while the application stack deploys to
us-east-2 (D015). The rule therefore lives in the dedicated us-east-1
EDGE stack (``edge-template.yaml``) and is associated with the live
distribution through the ``DistributionArn`` parameter the app stack
exports. These guards read that edge template; the app stack's WAF-free
contract is asserted in ``tests/test_edge_stack_contract.py``.

These tests assert the *wiring* -- the ACL resource, the rate-based
statement and its limit, the scope-down to the two rate-limited path
prefixes, the Count/Block action switch, and the association to the
imported distribution -- and not any AWS-side runtime behavior. They read
the tracked template as plain text and parse the resource/parameter blocks
with regexes only, so no YAML dependency is introduced. ``SimpleTestCase``
blocks all DB access; these checks touch only the filesystem.

The guard functions are deliberately separated from the assertions so the
same code path can be pointed at a mutated copy of the template: the
negative tests below prove that each guard actually fails when the
corresponding wiring is removed or weakened.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
EDGE_TEMPLATE = REPO_ROOT / "edge-template.yaml"

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

# The canary switch: the rule must ship counting, and blocking must stay
# reachable as a parameter change.
DEFAULT_ACTION = "Count"
BLOCKING_ACTION = "Block"


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


def _allowed_values(parameter_body):
    """Return the values listed under a parameter's ``AllowedValues:``."""
    values = []
    in_list = False
    for line in parameter_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("AllowedValues:"):
            in_list = True
            continue
        if in_list:
            if stripped.startswith("- "):
                values.append(stripped[2:].strip())
            elif stripped:
                break
    return values


def _has_web_acl(text):
    """A CloudFront-scoped WAF web ACL must exist in the edge template."""
    acl = _resource(text, "WafWebACL")
    if not acl:
        return False
    return (
        "Type: AWS::WAFv2::WebACL" in acl
        and re.search(r"Scope:[ \t]*CLOUDFRONT", acl) is not None
    )


def _has_rate_based_rule(text):
    """The ACL must hold a rate-based rule, per IP, over 300 s."""
    acl = _resource(text, "WafWebACL")
    if "RateBasedStatement:" not in acl:
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


def _scope_uses_text_transformations(text):
    """The byte-match statements must use the current WAFv2 schema shape.

    The pre-2019 API took a singular ``TextTransformation: NONE``. The
    published CloudFormation schema requires a ``TextTransformations``
    LIST of ``{Priority, Type}`` objects, and the singular form is
    rejected by the resource provider -- so the old shape would have
    failed the deploy with a schema error that reads like a template bug.
    """
    acl = _resource(text, "WafWebACL")
    if "TextTransformations:" not in acl:
        return False
    if re.search(r"TextTransformation:[ \t]", acl) is not None:
        # The singular form must not reappear anywhere.
        return False
    if len(re.findall(r"Type:[ \t]*NONE", acl)) < 2:
        return False
    return len(re.findall(r"Priority:[ \t]*0", acl)) >= 2


def _action_is_the_parameter_switch(text):
    """The rule's action must be the WafAction parameter, not a hard-coded one.

    The rule ships in Count mode (the canary) and must be able to switch
    to Block as a parameter change on the edge stack. A hard-coded Block
    would enforce an unproven ceiling on the first deploy; a hard-coded
    Count could never be flipped without a code change.
    """
    acl = _resource(text, "WafWebACL")
    action = re.search(
        r"Action:[ \t]*!If\s*\[\s*UseBlockAction\s*,"
        r"\s*\{Block:\s*\{\}\}\s*,\s*\{Count:\s*\{\}\}\s*\]",
        acl,
    )
    if action is None:
        return False
    condition = re.search(
        r"UseBlockAction:[ \t]*!Equals\s*\[!Ref[ \t]+WafAction,[ \t]*"
        r"\"Block\"\]",
        text,
    )
    return condition is not None


def _association_binds_distribution(text):
    """A WebACLAssociation must bind the ACL to the imported distribution."""
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
    # The ARN must come from the parameter the app stack exports, so the
    # two stacks cannot drift apart (a hard-coded ARN would let them).
    return target.group(1).strip() == "!Ref DistributionArn"


class WafRateRuleWiringTests(SimpleTestCase):
    """R026 / THROT-01: the edge rate limit must stay wired and reviewable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = EDGE_TEMPLATE.read_text(encoding="utf-8")

    def test_edge_template_declares_cloudfront_scoped_web_acl(self):
        self.assertTrue(
            _has_web_acl(self.template),
            "edge-template.yaml must declare an AWS::WAFv2::WebACL with "
            "Scope CLOUDFRONT so abuse is bounded at the edge (R026).",
        )

    def test_web_acl_holds_a_rate_based_rule(self):
        self.assertTrue(
            _has_rate_based_rule(self.template),
            "the WAF web ACL must hold a rate-based rule that aggregates "
            "per source IP and is evaluated over "
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

    def test_scope_uses_the_current_text_transformation_shape(self):
        self.assertTrue(
            _scope_uses_text_transformations(self.template),
            "the byte-match statements must use the TextTransformations "
            "list of {Priority, Type} objects; the singular "
            "TextTransformation form is rejected by the WAFv2 resource "
            "provider (R026).",
        )

    def test_rule_action_is_the_count_block_parameter_switch(self):
        self.assertTrue(
            _action_is_the_parameter_switch(self.template),
            "the rule's action must be driven by the WafAction parameter "
            "(Count by default, Block reachable) so the canary is a "
            "parameter change and the rollback is the same change (R026).",
        )

    def test_web_acl_is_associated_with_the_imported_distribution(self):
        self.assertTrue(
            _association_binds_distribution(self.template),
            "edge-template.yaml must declare an AWS::WAFv2::WebACLAssociation "
            "that binds the web ACL to the DistributionArn parameter, so the "
            "edge stack cannot drift from the live distribution (R026).",
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
        cls.template = EDGE_TEMPLATE.read_text(encoding="utf-8")

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

    def test_singular_text_transformation_is_detected(self):
        """The pre-2019 singular form must be caught before a deploy does."""
        mutated = self._mutate(
            "                        TextTransformations:\n"
            "                          - Priority: 0\n"
            "                            Type: NONE",
            "                        TextTransformation: NONE",
        )
        self.assertFalse(
            _scope_uses_text_transformations(mutated),
            "the text-transformation guard must fail when the singular "
            "TextTransformation form reappears.",
        )

    def test_hardcoded_block_action_is_detected(self):
        mutated = self._mutate(
            "          Action: !If [UseBlockAction, {Block: {}}, {Count: {}}]",
            "          Action:\n            Block: {}",
        )
        self.assertFalse(
            _action_is_the_parameter_switch(mutated),
            "the action guard must fail when the rule's action is hard-coded "
            "instead of driven by the WafAction parameter.",
        )

    def test_hardcoded_count_action_is_detected(self):
        mutated = self._mutate(
            "          Action: !If [UseBlockAction, {Block: {}}, {Count: {}}]",
            "          Action:\n            Count: {}",
        )
        self.assertFalse(
            _action_is_the_parameter_switch(mutated),
            "the action guard must fail when the rule can never be switched "
            "to Block.",
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
            "      ResourceArn: !Ref DistributionArn",
            "      ResourceArn: arn:aws:cloudfront::123456789012:distribution/"
            "E1EXAMPLE",
        )
        self.assertFalse(
            _association_binds_distribution(mutated),
            "the association guard must fail when the resource ARN is a "
            "hard-coded literal instead of a reference to the imported "
            "DistributionArn parameter.",
        )
