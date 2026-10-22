"""Unit tests for the edge verifier (scripts/verify_edge.py).

M007/S02 + M007/S06: the deploy job's post-deploy check must prove that
the CloudFront rate limit is actually attached to the distribution that
serves traffic.

The attachment lives on the DISTRIBUTION, not on a CloudFormation
association resource: the WAFv2 API cannot associate a web ACL with a
CloudFront distribution (it rejects every distribution ARN), so the edge
stack creates the rule and exports its ARN, and the app stack sets the
distribution's ``WebACLId`` to that ARN. A rule that exists but is not
attached is a control that enforces nothing, and that is the failure this
verifier exists to make visible.

Each test pins one gate:

* both stacks healthy, the edge stack exporting a web ACL ARN, and the live
  distribution carrying exactly that ARN -> 0;
* the live distribution carrying NO web ACL -> non-zero (detached);
* the live distribution carrying a DIFFERENT web ACL -> non-zero (the
  pipeline deploys one rule, the distribution is protected by another);
* a missing WebACLArn / DistributionArn output -> non-zero (template drift);
* an unhealthy stack -> non-zero.

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

DISTRIBUTION_ID = "ED6ELRIZI1Y80"
DISTRIBUTION_ARN = (
    f"arn:aws:cloudfront::768125641662:distribution/{DISTRIBUTION_ID}"
)
WEB_ACL_ARN = (
    "arn:aws:wafv2:us-east-1:768125641662:global/webacl/"
    "chemically-edge-rate-limit/4123c14e-612d-480d-b859-6519ea9074a5"
)
OTHER_WEB_ACL_ARN = (
    "arn:aws:wafv2:us-east-1:768125641662:global/webacl/"
    "some-other-rule/00000000-0000-0000-0000-000000000000"
)


def _app_stack(outputs=None, stack_status="UPDATE_COMPLETE"):
    return {
        "StackName": APP_STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [{"OutputKey": "DistributionArn", "OutputValue": DISTRIBUTION_ARN}],
    }


def _edge_stack(outputs=None, stack_status="UPDATE_COMPLETE"):
    return {
        "StackName": EDGE_STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [{"OutputKey": "WebACLArn", "OutputValue": WEB_ACL_ARN}],
    }


class FakeClients:
    """Fake client factory covering cloudformation + cloudfront."""

    def __init__(
        self,
        app_stack=None,
        edge_stack=None,
        web_acl_id=None,
    ):
        self._app_stack = app_stack or _app_stack()
        self._edge_stack = edge_stack or _edge_stack()
        self._web_acl_id = web_acl_id
        self.requested = []

    def __call__(self, service, region_name=None):
        self.requested.append((service, region_name))
        if service == "cloudformation":
            if region_name == APP_REGION:
                return SimpleNamespace(
                    describe_stacks=lambda StackName: {"Stacks": [self._app_stack]}
                )
            assert region_name == EDGE_REGION
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [self._edge_stack]}
            )
        if service == "cloudfront":
            return SimpleNamespace(
                get_distribution=lambda Id: {
                    "Distribution": {
                        "Id": Id,
                        "DomainName": "d3joi59ifa013d.cloudfront.net",
                        "Status": "Deployed",
                        "DistributionConfig": {
                            "Comment": "ChemicAlly public distribution",
                            "WebACLId": self._web_acl_id,
                        },
                    }
                }
            )
        raise AssertionError(f"unexpected service requested: {service}")


class VerifyEdgeTests(SimpleTestCase):
    """The happy path: the rule is attached to the live distribution."""

    def test_attached_rule_passes(self):
        clients = FakeClients(web_acl_id=WEB_ACL_ARN)
        result = verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertEqual(result, 0)

    def test_the_distribution_is_read_through_the_cloudfront_api(self):
        clients = FakeClients(web_acl_id=WEB_ACL_ARN)
        verify_edge.run(
            APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
            client_factory=clients,
        )
        self.assertIn(("cloudfront", "us-east-1"), clients.requested)


class VerifyEdgeFailureTests(SimpleTestCase):
    """Every way the rule can be detached must fail loudly."""

    def _assert_fails(self, clients):
        with self.assertRaises(RuntimeError):
            verify_edge.run(
                APP_STACK, APP_REGION, EDGE_STACK, EDGE_REGION,
                client_factory=clients,
            )

    def test_no_web_acl_on_the_distribution_is_detected(self):
        self._assert_fails(FakeClients(web_acl_id=None))

    def test_another_web_acl_on_the_distribution_is_detected(self):
        self._assert_fails(FakeClients(web_acl_id=OTHER_WEB_ACL_ARN))

    def test_missing_web_acl_arn_output_is_detected(self):
        self._assert_fails(FakeClients(edge_stack=_edge_stack(outputs=[])))

    def test_missing_distribution_arn_output_is_detected(self):
        self._assert_fails(FakeClients(app_stack=_app_stack(outputs=[])))

    def test_unhealthy_app_stack_is_detected(self):
        self._assert_fails(
            FakeClients(app_stack=_app_stack(stack_status="ROLLBACK_COMPLETE"))
        )

    def test_unhealthy_edge_stack_is_detected(self):
        self._assert_fails(
            FakeClients(edge_stack=_edge_stack(stack_status="CREATE_FAILED"))
        )


class VerifyEdgeCliTests(SimpleTestCase):
    """The CLI must exit non-zero on a failed check."""

    def test_main_returns_zero_on_success(self):
        code = verify_edge.main(
            [
                "--app-stack-name", APP_STACK,
                "--app-region", APP_REGION,
                "--edge-stack-name", EDGE_STACK,
                "--edge-region", EDGE_REGION,
            ],
            client_factory=FakeClients(web_acl_id=WEB_ACL_ARN),
        )
        self.assertEqual(code, 0)

    def test_main_returns_one_on_failure(self):
        code = verify_edge.main(
            [
                "--app-stack-name", APP_STACK,
                "--app-region", APP_REGION,
                "--edge-stack-name", EDGE_STACK,
                "--edge-region", EDGE_REGION,
            ],
            client_factory=FakeClients(web_acl_id=None),
        )
        self.assertEqual(code, 1)
