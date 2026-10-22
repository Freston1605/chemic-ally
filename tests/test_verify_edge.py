"""Unit tests for the edge verifier (scripts/verify_edge.py).

M007/S02: the deploy job's post-deploy check must prove that the CloudFront
rate rule is attached to the distribution that is ACTUALLY serving traffic.
A detached association is the failure that matters: the web ACL exists, the
deployment looks healthy, and the control enforces nothing.

Each test pins one gate:

* a healthy edge stack associated with the live distribution -> 0;
* the association pointing at a different distribution -> non-zero;
* the web ACL's own resource list not containing the live distribution ->
  non-zero;
* a missing WebACLAssociation resource -> non-zero;
* a missing app stack / edge stack -> non-zero;
* a missing DistributionArn output (app-stack drift) -> non-zero;
* a missing WebACLArn output (edge-stack drift) -> non-zero;
* an unhealthy stack (ROLLBACK_COMPLETE / *_IN_PROGRESS / *_FAILED) ->
  non-zero, for the app stack as well as the edge stack;
* an unreportable live mode (missing / unknown action, missing or
  non-numeric limit) -> non-zero;
* the live action reported: Count (canary) and Block (enforcement).

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients - no network access.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_edge.py"

_spec = importlib.util.spec_from_file_location("verify_edge", MODULE_PATH)
verify_edge = importlib.util.module_from_spec(_spec)
sys.modules["verify_edge"] = verify_edge
_spec.loader.exec_module(verify_edge)

APP_REGION = "us-east-2"
EDGE_REGION = "us-east-1"
APP_STACK = "chemically"
EDGE_STACK = "chemically-edge"

DISTRIBUTION_ARN = (
    "arn:aws:cloudfront::768125641662:distribution/E1EXAMPLE"
)
OTHER_DISTRIBUTION_ARN = (
    "arn:aws:cloudfront::768125641662:distribution/E0OTHER"
)
# WAFv2 addresses a CloudFront distribution with the ACCOUNT field EMPTY.
# The association is written in that shape, so the verifier must not
# compare the two ARN STRINGS: it compares the distribution ID they name
# (observed live on 2026-10-22, CI run 39732102371).
WAF_DISTRIBUTION_ARN = (
    "arn:aws:cloudfront::distribution/E1EXAMPLE"
)
WAF_OTHER_DISTRIBUTION_ARN = (
    "arn:aws:cloudfront::distribution/E0OTHER"
)
WEB_ACL_ARN = "arn:aws:wafv2:us-east-1:768125641662:global/webacl/abc"


def _app_stack(outputs=None, stack_status="UPDATE_COMPLETE"):
    return {
        "StackName": APP_STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [
            {"OutputKey": "DistributionArn", "OutputValue": DISTRIBUTION_ARN},
        ],
        "Parameters": [],
    }


def _edge_stack(
    outputs=None,
    parameters=None,
    stack_status="UPDATE_COMPLETE",
):
    return {
        "StackName": EDGE_STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [{"OutputKey": "WebACLArn", "OutputValue": WEB_ACL_ARN}],
        "Parameters": parameters
        if parameters is not None
        else [
            {"ParameterKey": "WafAction", "ParameterValue": "Count"},
            {"ParameterKey": "WafRateLimit", "ParameterValue": "2000"},
        ],
    }


def _default_resources(association_arn):
    """The edge stack's resources as CloudFormation reports them."""
    return [
        {
            "LogicalResourceId": "WafWebACL",
            "ResourceType": "AWS::WAFv2::WebACL",
            "PhysicalResourceId": WEB_ACL_ARN,
        },
        {
            "LogicalResourceId": "WafWebACLAssociation",
            "ResourceType": "AWS::WAFv2::WebACLAssociation",
            "PhysicalResourceId": association_arn,
        },
    ]


class FakeClients:
    """Fake client factory covering cloudformation + wafv2 for the verifier."""

    def __init__(
        self,
        app_stack=None,
        edge_stack=None,
        association_arn=None,
        listed_arns=None,
        resources=None,
        app_stack_missing=False,
        edge_stack_missing=False,
    ):
        self._app_stack = app_stack or _app_stack()
        self._edge_stack = edge_stack or _edge_stack()
        self._association_arn = (
            association_arn if association_arn is not None else DISTRIBUTION_ARN
        )
        self._listed_arns = (
            listed_arns if listed_arns is not None else [DISTRIBUTION_ARN]
        )
        self._resources = (
            resources
            if resources is not None
            else _default_resources(self._association_arn)
        )
        self._app_stack_missing = app_stack_missing
        self._edge_stack_missing = edge_stack_missing
        self.requested_regions = []

    def __call__(self, service, region_name=None):
        self.requested_regions.append((service, region_name))
        if service == "cloudformation":
            if region_name == APP_REGION:
                stacks = [] if self._app_stack_missing else [self._app_stack]
                return SimpleNamespace(
                    describe_stacks=lambda StackName: {"Stacks": stacks}
                )
            assert region_name == EDGE_REGION
            stacks = [] if self._edge_stack_missing else [self._edge_stack]
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": stacks},
                describe_stack_resources=lambda StackName: {
                    "StackResources": self._resources
                },
            )
        if service == "wafv2":
            return SimpleNamespace(
                list_resources_for_web_acl=lambda WebACLArn: {
                    "ResourceArns": self._listed_arns
                }
            )
        raise AssertionError(f"unexpected service requested: {service}")


class VerifyEdgeTests(SimpleTestCase):
    """The happy path and the live reporting."""

    def test_healthy_association_passes(self):
        clients = FakeClients()
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_it_reads_both_regions(self):
        clients = FakeClients()
        verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertIn(("cloudformation", APP_REGION), clients.requested_regions)
        self.assertIn(("cloudformation", EDGE_REGION), clients.requested_regions)
        self.assertIn(("wafv2", EDGE_REGION), clients.requested_regions)

    def test_block_mode_is_reported(self):
        """The live mode must be visible: Count is the canary, Block is live."""
        edge_stack = _edge_stack(
            parameters=[
                {"ParameterKey": "WafAction", "ParameterValue": "Block"},
                {"ParameterKey": "WafRateLimit", "ParameterValue": "2000"},
            ]
        )
        clients = FakeClients(edge_stack=edge_stack)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_association_is_found_by_resource_type(self):
        """A renamed logical resource must not hide the association."""
        resources = [
            {
                "LogicalResourceId": "SomeOtherName",
                "ResourceType": "AWS::WAFv2::WebACLAssociation",
                "PhysicalResourceId": DISTRIBUTION_ARN,
            }
        ]
        clients = FakeClients(resources=resources)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_wrapped_association_arn_is_accepted(self):
        """CloudFormation may report the association id wrapped in an ARN."""
        wrapped = (
            "arn:aws:cloudfront::768125641662:distribution/E1EXAMPLE/"
            "association/abc"
        )
        clients = FakeClients(association_arn=wrapped)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_waf_shaped_association_passes(self):
        """WAFv2 reports the association in ITS ARN shape (no account).

        The account-bearing form and the account-less form name the SAME
        distribution, so a healthy association must not be reported as a
        detached one.
        """
        clients = FakeClients(
            association_arn=WAF_DISTRIBUTION_ARN,
            listed_arns=[WAF_DISTRIBUTION_ARN],
        )
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_action_is_read_from_outputs_when_parameters_are_absent(self):
        """A stack that only exports Action/RateLimit is still reportable."""
        edge_stack = _edge_stack(
            outputs=[
                {"OutputKey": "WebACLArn", "OutputValue": WEB_ACL_ARN},
                {"OutputKey": "Action", "OutputValue": "Block"},
                {"OutputKey": "RateLimit", "OutputValue": "2000"},
            ],
            parameters=[],
        )
        clients = FakeClients(edge_stack=edge_stack)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_rolled_back_edge_stack_is_reported_but_still_verified(self):
        """UPDATE_ROLLBACK_COMPLETE describes the last GOOD deploy."""
        edge_stack = _edge_stack(stack_status="UPDATE_ROLLBACK_COMPLETE")
        clients = FakeClients(edge_stack=edge_stack)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)


class VerifyEdgeFailureTests(SimpleTestCase):
    """Every way the edge control can be detached must fail loudly."""

    def _assert_fails(self, clients):
        with self.assertRaises(RuntimeError):
            verify_edge.run(
                APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
                client_factory=clients,
            )

    def test_association_on_another_distribution_is_detected(self):
        self._assert_fails(
            FakeClients(association_arn=OTHER_DISTRIBUTION_ARN)
        )

    def test_waf_shaped_association_on_another_distribution_is_detected(self):
        """The same detachment, in the ARN shape WAFv2 actually reports."""
        self._assert_fails(
            FakeClients(
                association_arn=WAF_OTHER_DISTRIBUTION_ARN,
                listed_arns=[WAF_OTHER_DISTRIBUTION_ARN],
            )
        )

    def test_detached_web_acl_resource_list_is_detected(self):
        self._assert_fails(
            FakeClients(listed_arns=[OTHER_DISTRIBUTION_ARN])
        )

    def test_waf_shaped_detached_web_acl_resource_list_is_detected(self):
        self._assert_fails(
            FakeClients(listed_arns=[WAF_OTHER_DISTRIBUTION_ARN])
        )

    def test_malformed_distribution_arn_output_is_detected(self):
        """An output that names no distribution cannot be verified."""
        self._assert_fails(
            FakeClients(
                app_stack=_app_stack(
                    outputs=[
                        {
                            "OutputKey": "DistributionArn",
                            "OutputValue": "not-an-arn",
                        },
                    ]
                )
            )
        )

    def test_missing_association_resource_is_detected(self):
        resources = [
            {
                "LogicalResourceId": "WafWebACL",
                "ResourceType": "AWS::WAFv2::WebACL",
                "PhysicalResourceId": WEB_ACL_ARN,
            }
        ]
        self._assert_fails(FakeClients(resources=resources))

    def test_association_without_physical_id_is_detected(self):
        resources = [
            {
                "LogicalResourceId": "WafWebACLAssociation",
                "ResourceType": "AWS::WAFv2::WebACLAssociation",
                "PhysicalResourceId": "",
            }
        ]
        self._assert_fails(FakeClients(resources=resources))

    def test_missing_distribution_arn_output_is_detected(self):
        self._assert_fails(
            FakeClients(app_stack=_app_stack(outputs=[]))
        )

    def test_missing_web_acl_arn_output_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(outputs=[]))
        )

    def test_missing_app_stack_is_detected(self):
        self._assert_fails(FakeClients(app_stack_missing=True))

    def test_missing_edge_stack_is_detected(self):
        self._assert_fails(FakeClients(edge_stack_missing=True))

    def test_unhealthy_edge_stack_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(stack_status="ROLLBACK_COMPLETE"))
        )

    def test_in_progress_edge_stack_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(stack_status="UPDATE_IN_PROGRESS"))
        )

    def test_failed_edge_stack_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(stack_status="CREATE_FAILED"))
        )

    def test_unhealthy_app_stack_is_detected(self):
        self._assert_fails(
            FakeClients(app_stack=_app_stack(stack_status="ROLLBACK_COMPLETE"))
        )

    def test_unknown_action_is_detected(self):
        """An unreportable live mode is a failure, not an 'unknown'."""
        edge_stack = _edge_stack(parameters=[])
        self._assert_fails(FakeClients(edge_stack=edge_stack))

    def test_invalid_action_is_detected(self):
        edge_stack = _edge_stack(
            parameters=[
                {"ParameterKey": "WafAction", "ParameterValue": "Deny"},
                {"ParameterKey": "WafRateLimit", "ParameterValue": "2000"},
            ]
        )
        self._assert_fails(FakeClients(edge_stack=edge_stack))

    def test_missing_rate_limit_is_detected(self):
        edge_stack = _edge_stack(
            parameters=[
                {"ParameterKey": "WafAction", "ParameterValue": "Count"},
            ]
        )
        self._assert_fails(FakeClients(edge_stack=edge_stack))

    def test_non_numeric_rate_limit_is_detected(self):
        edge_stack = _edge_stack(
            parameters=[
                {"ParameterKey": "WafAction", "ParameterValue": "Count"},
                {"ParameterKey": "WafRateLimit", "ParameterValue": "many"},
            ]
        )
        self._assert_fails(FakeClients(edge_stack=edge_stack))

    def test_non_positive_rate_limit_is_detected(self):
        edge_stack = _edge_stack(
            parameters=[
                {"ParameterKey": "WafAction", "ParameterValue": "Count"},
                {"ParameterKey": "WafRateLimit", "ParameterValue": "0"},
            ]
        )
        self._assert_fails(FakeClients(edge_stack=edge_stack))


class VerifyEdgeCliTests(SimpleTestCase):
    """The CLI must exit non-zero on a failed check."""

    def test_main_returns_one_on_failure(self):
        clients = FakeClients(association_arn=OTHER_DISTRIBUTION_ARN)
        code = verify_edge.main(
            [
                "--app-stack-name", APP_STACK,
                "--app-region", APP_REGION,
                "--edge-stack-name", EDGE_STACK,
                "--edge-region", EDGE_REGION,
            ],
            client_factory=clients,
        )
        self.assertEqual(code, 1)

    def test_main_returns_zero_on_success(self):
        code = verify_edge.main(
            [
                "--app-stack-name", APP_STACK,
                "--app-region", APP_REGION,
                "--edge-stack-name", EDGE_STACK,
                "--edge-region", EDGE_REGION,
            ],
            client_factory=FakeClients(),
        )
        self.assertEqual(code, 0)

    def test_main_defaults_target_the_two_stacks(self):
        """The CLI defaults must be the app stack (us-east-2) and the edge
        stack (us-east-1) the deploy job actually creates."""
        clients = FakeClients()
        code = verify_edge.main([], client_factory=clients)
        self.assertEqual(code, 0)
        self.assertIn(("cloudformation", "us-east-2"), clients.requested_regions)
        self.assertIn(("cloudformation", "us-east-1"), clients.requested_regions)
        self.assertIn(("wafv2", "us-east-1"), clients.requested_regions)
