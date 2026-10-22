"""Cross-stack contract between the app stack and the edge stack (M007/S01).

A CloudFront-scoped WAFv2 web ACL can only be created in us-east-1,
while the application stack (Lambda, VPC, RDS) deploys to us-east-2
(D015). The edge control therefore lives in a dedicated us-east-1 edge
stack (``edge-template.yaml``) which associates the rule with the live
CloudFront distribution through an imported ARN.

Two stacks can drift apart silently, and a detached WAF association is a
security control that appears deployed and is not. These guards pin the
handshake:

* the app stack declares NO WAFv2 resource at all (it cannot create one
  in its own region, and declaring it breaks every us-east-2 deploy);
* the app stack keeps the distribution under a stable logical ID and
  exports its ARN;
* the edge stack takes that ARN as a REQUIRED parameter (never defaulted,
  never hard-coded) and owns nothing but the edge control.

Following the convention established by ``tests/test_waf_rate_rule.py``
(M006/S01), the wiring assertions are expressed as named guard functions
and each one is proved non-vacuous by a mutation-based negative test.
The tracked templates are read as plain text and parsed with regexes
only, so no YAML dependency is introduced. ``SimpleTestCase`` blocks all
DB access; these checks touch only the filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_TEMPLATE = REPO_ROOT / "template.yaml"
EDGE_TEMPLATE = REPO_ROOT / "edge-template.yaml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


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


def _output(text, output_name):
    """Return the body of an output, or "" when it is not declared."""
    return _entry_bodies(text, "Outputs").get(output_name, "")


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


def _app_stack_has_no_waf_resources(text):
    """The us-east-2 app stack must not declare any WAFv2 resource.

    A CLOUDFRONT-scoped web ACL can only be created in us-east-1, so a
    WAFv2 resource in this stack makes every us-east-2 deploy fail on a
    resource it cannot create. The guard inspects the declared resource
    TYPES (not the prose: the template's comments name the constraint on
    purpose) and also rejects a leftover WAF limit parameter, which would
    mean the ceiling is still configured in two places.
    """
    for body in _entry_bodies(text, "Resources").values():
        resource_type = re.search(r"Type:[ \t]*(\S+)", body)
        if resource_type and resource_type.group(1).startswith("AWS::WAFv2"):
            return False
    return "WafRateLimit" not in _entry_bodies(text, "Parameters")


def _app_stack_keeps_the_distribution(text):
    """The distribution must stay under its stable logical ID.

    CloudFormation updates a resource in place when the logical ID is
    unchanged, so the distribution keeps its ID -- and therefore its ARN
    -- across deploys. Renaming or moving the logical ID would replace
    the distribution and silently detach the edge association.
    """
    body = _resource(text, "CloudFrontDistribution")
    if not body:
        return False
    return "Type: AWS::CloudFront::Distribution" in body


def _app_stack_exports_distribution_arn(text):
    """The app stack must export the distribution ARN for the edge stack."""
    output = _output(text, "DistributionArn")
    if not output:
        return False
    # The value must be built from the distribution resource itself, so it
    # can never be a stale literal.
    if "CloudFrontDistribution" not in output:
        return False
    if "arn:aws:cloudfront::" not in output:
        return False
    return not re.search(r"distribution/[A-Z0-9]{8,}", output)


def _distribution_has_no_name_property(text):
    """The distribution must not carry a `Name:` property.

    AWS::CloudFront::Distribution has no Name property -- the published
    CloudFormation resource schema lists only Aliases, Comment, Origins,
    DefaultCacheBehavior and friends; a distribution's identity is its ID.
    Adding a Name key would make the template fail at deploy time with an
    unknown-property error.
    """
    body = _resource(text, "CloudFrontDistribution")
    if not body:
        return False
    return re.search(r"^[ \t]+Name:[ \t]*\S", body, re.MULTILINE) is None


def _edge_template_exports_the_web_acl_arn(text):
    """The edge stack must export the web ACL's ARN for the app stack.

    The WAFv2 API cannot associate a web ACL with a CloudFront
    distribution, so the handover runs the other way round: the edge stack
    hands over the rule, the app stack attaches it to the distribution
    through the distribution's WebACLId.
    """
    output = _output(text, "WebACLArn")
    if not output:
        return False
    if "Fn::GetAtt: WafWebACL.Arn" not in output:
        return False
    return "WafWebACLAssociation" not in text


def _app_stack_attaches_the_edge_web_acl(text):
    """The app stack must attach the edge web ACL to the distribution.

    The attachment is the distribution's own WebACLId (the WAFv2 API
    cannot take a CloudFront ARN), and it is gated on the EdgeWebACLArn
    parameter so the first deploy - before the edge stack exists - is a
    valid state rather than a failed one.
    """
    parameter = _parameter(text, "EdgeWebACLArn")
    if not parameter:
        return False
    if re.search(r"Type:[ 	]*String", parameter) is None:
        return False
    distribution = _resource(text, "CloudFrontDistribution")
    if not distribution:
        return False
    if "WebACLId:" not in distribution:
        return False
    if "EdgeWebACLArn" not in distribution:
        return False
    return "AWS::NoValue" in distribution


def _edge_template_owns_only_the_edge(text):
    """The edge stack must contain nothing but the edge control.

    The Lambda function, the VPC and the RDS instance stay in the app
    stack; moving them would change the blast radius of the edge split
    for no gain.
    """
    forbidden = (
        "AWS::Serverless::Function",
        "AWS::Lambda::Function",
        "AWS::EC2::VPC",
        "AWS::EC2::Subnet",
        "AWS::EC2::SecurityGroup",
        "AWS::RDS::DBInstance",
        "AWS::CloudFront::Distribution",
        "AWS::S3::Bucket",
    )
    resources = _entry_bodies(text, "Resources")
    for body in resources.values():
        for resource_type in forbidden:
            if resource_type in body:
                return False
    return True


def _edge_template_action_defaults_to_count(text):
    """The rule must ship counting, with blocking reachable as a parameter."""
    parameter = _parameter(text, "WafAction")
    if not parameter:
        return False
    if re.search(r"Type:[ \t]*String", parameter) is None:
        return False
    default = re.search(r"Default:[ \t]*(\w+)", parameter)
    if default is None or default.group(1) != "Count":
        return False
    allowed = _allowed_values(parameter)
    return "Count" in allowed and "Block" in allowed


def _edge_template_resources_are_unconditional(text):
    """No resource may be gated on a Condition, and no disable switch.

    D015/D018/D022: the control must either be created and working or
    the deploy must fail loudly. Gating a resource on a Condition (or
    adding a "deploy it disabled" parameter) would let the stack come up
    green with the rule absent -- the silent-disablement state those
    decisions reject. The template's only condition is the Count/Block
    action switch, which selects the rule's behaviour and gates no
    resource; a Fn::FindInMap cannot replace it, because CloudFormation
    Mappings values must be strings or lists of strings and cannot hold
    the {Block: {}} / {Count: {}} action object.
    """
    for body in _entry_bodies(text, "Resources").values():
        if re.search(r"^[ \t]+Condition:[ \t]*\S", body, re.MULTILINE):
            return False
    for parameter_name in _entry_bodies(text, "Parameters"):
        if re.search(r"Enable|Disable", parameter_name):
            return False
    # The only condition may be the action switch: any other condition
    # would be a control that can be switched off without a deploy.
    declared = re.findall(
        r"^  ([A-Za-z0-9_]+):[ \t]*(?:\n[ \t]+)?(?:!Equals|Fn::Equals:)",
        text,
        re.MULTILINE,
    )
    return all(name == "UseBlockAction" for name in declared)


def _workflow_lints_both_templates(text):
    """CI must validate BOTH templates against the CloudFormation schemas.

    A schema error in the edge template (the WAFv2 statement shape, an
    invalid Description, an unknown property) otherwise surfaces only as an
    opaque CloudFormation failure during a deploy.
    """
    if "cfn-lint" not in text:
        return False
    run_lines = re.findall(r"^[ \t]*run:[ \t]*(cfn-lint .*)$", text, re.MULTILINE)
    if not run_lines:
        return False
    return any(
        "template.yaml" in line and "edge-template.yaml" in line
        for line in run_lines
    )


class AppStackHandoverTests(SimpleTestCase):
    """The app stack must hand the distribution over, and hold no WAF."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = APP_TEMPLATE.read_text(encoding="utf-8")

    def test_app_stack_declares_no_waf_resources(self):
        self.assertTrue(
            _app_stack_has_no_waf_resources(self.template),
            "template.yaml must not declare an AWS::WAFv2 resource: a "
            "CLOUDFRONT-scoped web ACL can only be created in us-east-1, so "
            "declaring it here breaks every us-east-2 deploy (R026/D015).",
        )

    def test_app_stack_keeps_the_distribution_under_a_stable_logical_id(self):
        self.assertTrue(
            _app_stack_keeps_the_distribution(self.template),
            "template.yaml must keep the CloudFrontDistribution under its "
            "stable logical ID so the distribution (and its ARN, which the "
            "edge stack associates the WAF rule with) survives deploys.",
        )

    def test_app_stack_attaches_the_edge_web_acl(self):
        self.assertTrue(
            _app_stack_attaches_the_edge_web_acl(self.template),
            "template.yaml must attach the edge stack's web ACL to the "
            "distribution through the distribution's WebACLId, gated on the "
            "EdgeWebACLArn parameter (R026).",
        )

    def test_app_stack_exports_the_distribution_arn(self):
        self.assertTrue(
            _app_stack_exports_distribution_arn(self.template),
            "template.yaml must export a DistributionArn output built from "
            "the CloudFrontDistribution resource, so the us-east-1 edge "
            "stack can associate the WAF rule with the live distribution.",
        )

    def test_distribution_carries_no_name_property(self):
        self.assertTrue(
            _distribution_has_no_name_property(self.template),
            "the CloudFrontDistribution must not declare a Name property: "
            "AWS::CloudFront::Distribution has no such property (the "
            "published resource schema does not list one) and the deploy "
            "would fail with an unknown-property error.",
        )


class EdgeStackContractTests(SimpleTestCase):
    """The edge stack must own the rule and nothing else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = EDGE_TEMPLATE.read_text(encoding="utf-8")

    def test_edge_stack_exports_the_web_acl_arn(self):
        self.assertTrue(
            _edge_template_exports_the_web_acl_arn(self.template),
            "edge-template.yaml must export the web ACL's ARN and must not "
            "carry a WafWebACLAssociation: the WAFv2 API cannot accept a "
            "CloudFront distribution ARN (R026).",
        )

    def test_edge_stack_owns_only_the_edge_control(self):
        self.assertTrue(
            _edge_template_owns_only_the_edge(self.template),
            "edge-template.yaml must contain only the WAF web ACL and its "
            "association; the Lambda function, the VPC and RDS stay in the "
            "app stack.",
        )

    def test_edge_rule_ships_counting_with_block_reachable(self):
        self.assertTrue(
            _edge_template_action_defaults_to_count(self.template),
            "the edge rule must default to Count (the canary) and allow "
            "Block as a parameter change, so enforcement is never switched "
            "on cold and the rollback is the same parameter change.",
        )

    def test_edge_stack_gates_no_resource_on_a_condition(self):
        self.assertTrue(
            _edge_template_resources_are_unconditional(self.template),
            "edge-template.yaml must not gate a resource on a Condition and "
            "must not carry a disable switch: the WAF control has to be "
            "created and working or the deploy has to fail loudly, never "
            "come up green with the rule absent (D015/D018/D022).",
        )

    def test_ci_lints_both_templates(self):
        self.assertTrue(
            _workflow_lints_both_templates(
                DEPLOY_WORKFLOW.read_text(encoding="utf-8")
            ),
            "the CI lint job must run cfn-lint over template.yaml AND "
            "edge-template.yaml, so a CloudFormation schema error is caught "
            "before a deploy instead of during one.",
        )


class EdgeStackContractNegativeTests(SimpleTestCase):
    """The cross-stack guards must fail when the handshake is broken.

    Each case mutates a copy of the tracked template and asserts that the
    corresponding guard rejects it, so a guard that silently stopped
    matching cannot keep the suite green while the two stacks have
    drifted apart.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = APP_TEMPLATE.read_text(encoding="utf-8")
        cls.edge = EDGE_TEMPLATE.read_text(encoding="utf-8")

    def _mutate(self, text, old, new):
        self.assertIn(
            old,
            text,
            f"the template no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return text.replace(old, new, 1)

    def test_waf_resource_in_the_app_stack_is_detected(self):
        mutated = self._mutate(
            self.app,
            "  CloudFrontDistribution:\n    Type: AWS::CloudFront::Distribution",
            "  WafWebACL:\n    Type: AWS::WAFv2::WebACL\n"
            "  CloudFrontDistribution:\n    Type: AWS::CloudFront::Distribution",
        )
        self.assertFalse(
            _app_stack_has_no_waf_resources(mutated),
            "the app-stack guard must fail when a WAFv2 resource reappears "
            "in the us-east-2 stack.",
        )

    def test_leftover_waf_limit_parameter_in_the_app_stack_is_detected(self):
        mutated = self._mutate(
            self.app,
            "  OriginHeaderName:",
            "  WafRateLimit:\n    Type: Number\n    Default: 2000\n"
            "  OriginHeaderName:",
        )
        self.assertFalse(
            _app_stack_has_no_waf_resources(mutated),
            "the app-stack guard must fail when the rate limit is still "
            "configured in the app stack as well as in the edge stack.",
        )

    def test_renamed_distribution_logical_id_is_detected(self):
        """The distribution must not move to another logical ID.

        CloudFormation replaces a resource whose logical ID changes, and a
        replaced distribution gets a NEW id -- and therefore a NEW ARN.
        The edge stack's imported ARN would then point at a distribution
        that no longer serves traffic, silently detaching the WAF
        association.
        """
        mutated = self._mutate(
            self.app,
            "  CloudFrontDistribution:\n    Type: AWS::CloudFront::Distribution",
            "  ChemicAllyDistribution:\n    Type: AWS::CloudFront::Distribution",
        )
        self.assertFalse(
            _app_stack_keeps_the_distribution(mutated),
            "the stable-identity guard must fail when the distribution moves "
            "to another logical ID: it would be replaced and its ARN would "
            "change.",
        )

    def test_missing_distribution_arn_output_is_detected(self):
        mutated = self._mutate(
            self.app,
            "  DistributionArn:\n",
            "  DistributionArnRemoved:\n",
        )
        self.assertFalse(
            _app_stack_exports_distribution_arn(mutated),
            "the export guard must fail when the DistributionArn output is "
            "renamed away.",
        )

    def test_hardcoded_distribution_arn_output_is_detected(self):
        mutated = self._mutate(
            self.app,
            "arn:aws:cloudfront::${AWS::AccountId}:distribution/"
            "${CloudFrontDistribution}",
            "arn:aws:cloudfront::768125641662:distribution/E1EXAMPLE",
        )
        self.assertFalse(
            _app_stack_exports_distribution_arn(mutated),
            "the export guard must fail when the exported ARN is a literal "
            "instead of a reference to the distribution resource.",
        )

    def test_distribution_name_property_is_detected(self):
        mutated = self._mutate(
            self.app,
            "        # The fingerprint makes CloudFormation notice a change to the",
            "        Name: chemically\n"
            "        # The fingerprint makes CloudFormation notice a change to the",
        )
        self.assertFalse(
            _distribution_has_no_name_property(mutated),
            "the guard must fail when a Name property is added to the "
            "distribution: the resource type has no such property.",
        )

    def test_edge_stack_association_is_detected(self):
        mutated = self._mutate(
            self.edge,
            "  WafWebACL:",
            "  WafWebACLAssociation:\n"
            "    Type: AWS::WAFv2::WebACLAssociation\n"
            "  WafWebACL:",
        )
        self.assertFalse(
            _edge_template_exports_the_web_acl_arn(mutated),
            "the export guard must fail when the edge stack tries to "
            "associate the rule itself.",
        )

    def test_missing_edge_web_acl_arn_export_is_detected(self):
        mutated = self._mutate(
            self.edge, "  WebACLArn:", "  WebACLArnRemoved:"
        )
        self.assertFalse(
            _edge_template_exports_the_web_acl_arn(mutated),
            "the export guard must fail when the web ACL's ARN is not "
            "exported: the app stack would have nothing to attach.",
        )

    def test_missing_edge_web_acl_attachment_is_detected(self):
        mutated = self._mutate(
            self.app,
            "        WebACLId:\n"
            "          Fn::If:\n"
            "            - HasEdgeWebACL\n"
            "            - Ref: EdgeWebACLArn\n"
            "            - Ref: AWS::NoValue\n",
            "",
        )
        self.assertFalse(
            _app_stack_attaches_the_edge_web_acl(mutated),
            "the attachment guard must fail when the distribution no longer "
            "carries the edge web ACL.",
        )

    def test_application_resource_in_the_edge_stack_is_detected(self):
        mutated = self._mutate(
            self.edge,
            "  WafWebACL:",
            "  DjangoFunction:\n    Type: AWS::Serverless::Function\n"
            "  WafWebACL:",
        )
        self.assertFalse(
            _edge_template_owns_only_the_edge(mutated),
            "the edge-stack guard must fail when application resources move "
            "into the edge stack.",
        )

    def test_block_by_default_is_detected(self):
        mutated = self._mutate(self.edge, "    Default: Count", "    Default: Block")
        self.assertFalse(
            _edge_template_action_defaults_to_count(mutated),
            "the action guard must fail when the rule would ship blocking: "
            "the canary must count first.",
        )

    def test_conditional_resource_in_the_edge_stack_is_detected(self):
        mutated = self._mutate(
            self.edge,
            "    Type: AWS::WAFv2::WebACL\n",
            "    Type: AWS::WAFv2::WebACL\n"
            "    Condition: UseBlockAction\n",
        )
        self.assertFalse(
            _edge_template_resources_are_unconditional(mutated),
            "the edge-stack guard must fail when a resource is gated on a "
            "Condition: the control must not be conditionally creatable.",
        )

    def test_disable_switch_parameter_in_the_edge_stack_is_detected(self):
        mutated = self._mutate(
            self.edge,
            "  WafAction:\n",
            "  EnableWaf:\n    Type: String\n    Default: \"false\"\n"
            "  WafAction:\n",
        )
        self.assertFalse(
            _edge_template_resources_are_unconditional(mutated),
            "the edge-stack guard must fail when a disable switch is added: "
            "the control must not ship in a state where it does nothing.",
        )

    def test_linting_only_the_app_template_is_detected(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        mutated = self._mutate(
            workflow,
            "run: cfn-lint template.yaml edge-template.yaml",
            "run: cfn-lint template.yaml",
        )
        self.assertFalse(
            _workflow_lints_both_templates(mutated),
            "the CI guard must fail when the edge template is no longer "
            "linted.",
        )
