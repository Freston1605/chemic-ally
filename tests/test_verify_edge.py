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
* a missing DistributionArn output (app-stack drift) -> non-zero;
* a missing WebACLArn output (edge-stack drift) -> non-zero;
* an unhealthy edge stack (ROLLBACK_COMPLETE / *_IN_PROGRESS) -> non-zero;
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


class FakeClients:
    """Fake client factory covering cloudformation + wafv2 for the verifier."""

    def __init__(
        self,
        app_stack=None,
        edge_stack=None,
        association_arn=None,
        listed_arns=None,
    ):
        self._app_stack = app_stack or _app_stack()
        self._edge_stack = edge_stack or _edge_stack()
        self._association_arn = (
            association_arn if association_arn is not None else DISTRIBUTION_ARN
        )
        self._listed_arns = (
            listed_arns if listed_arns is not None else [DISTRIBUTION_ARN]
        )
        self.requested_regions = []

    def __call__(self, service, region_name=None):
        self.requested_regions.append((service, region_name))
        if service == "cloudformation":
            if region_name == APP_REGION:
                return SimpleNamespace(
                    describe_stacks=lambda StackName: {"Stacks": [self._app_stack]}
                )
            assert region_name == EDGE_REGION
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [self._edge_stack]},
                describe_stack_resources=lambda StackName: {
                    "StackResources": [
                        {
                            "LogicalResourceId": "WafWebACL",
                            "PhysicalResourceId": WEB_ACL_ARN,
                        },
                        {
                            "LogicalResourceId": "WafWebACLAssociation",
                            "PhysicalResourceId": self._association_arn,
                        },
                    ]
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

    def test_detached_web_acl_resource_list_is_detected(self):
        self._assert_fails(
            FakeClients(listed_arns=[OTHER_DISTRIBUTION_ARN])
        )

    def test_missing_distribution_arn_output_is_detected(self):
        self._assert_fails(
            FakeClients(app_stack=_app_stack(outputs=[]))
        )

    def test_missing_web_acl_arn_output_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(outputs=[]))
        )

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
