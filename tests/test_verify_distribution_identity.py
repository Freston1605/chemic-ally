"""Unit tests for the distribution identity verifier.

The verifier lives in scripts/verify_distribution_identity.py.

M007/S01: the us-east-1 edge stack associates the WAF rule with the live
CloudFront distribution by ARN. CloudFormation updates a distribution IN
PLACE when its logical ID is unchanged, so the distribution ID survives an
ordinary deploy; a replaced distribution gets a NEW id -- and therefore a
NEW ARN -- and the edge association would silently point at a distribution
that no longer serves traffic.

Each test pins one behaviour of the witness that catches that:

* a healthy stack + a live distribution serving the reported domain -> 0,
  and the identity is printed for capture;
* the captured ID matching the live ID -> 0 (the ordinary deploy);
* a DIFFERENT live ID -> failure (the distribution was replaced);
* a stack output that no live distribution serves -> failure;
* a DistributionArn Output that points at another distribution -> failure
  (a stale export would hand the edge stack a dead ARN);
* an unhealthy stack status -> failure (the identity is not authoritative);
* a missing CloudFrontDomain Output -> failure (template drift);
* malformed CloudFront replies (no DistributionList, truncated without a
  NextMarker, an entry without an Id) -> failure with a clear message
  instead of a traceback;
* an AWS API error -> the exception propagates, so a throttling error is
  never mistaken for a green check.

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients -- no network access.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_distribution_identity.py"

_spec = importlib.util.spec_from_file_location(
    "verify_distribution_identity", MODULE_PATH
)
verify_distribution_identity = importlib.util.module_from_spec(_spec)
sys.modules["verify_distribution_identity"] = verify_distribution_identity
_spec.loader.exec_module(verify_distribution_identity)

REGION = "us-east-2"
STACK = "chemically"
DIST_ID = "E1EXAMPLE123"
DIST_DOMAIN = "d111111abcdef8.cloudfront.net"
DIST_ARN = f"arn:aws:cloudfront::768125641662:distribution/{DIST_ID}"


def _stack(
    stack_status="UPDATE_COMPLETE",
    domain=DIST_DOMAIN,
    exported_arn=None,
    outputs=None,
):
    if outputs is None:
        outputs = []
        if domain is not None:
            outputs.append(
                {"OutputKey": "CloudFrontDomain", "OutputValue": domain}
            )
        if exported_arn is not None:
            outputs.append(
                {"OutputKey": "DistributionArn", "OutputValue": exported_arn}
            )
    return {
        "StackName": STACK,
        "StackStatus": stack_status,
        "Outputs": outputs,
    }


def _distribution(distribution_id=DIST_ID, domain=DIST_DOMAIN, arn=DIST_ARN):
    return {
        "Id": distribution_id,
        "DomainName": domain,
        "ARN": arn,
        "Aliases": {"Items": ["chemic-ally.xyz"]},
    }


def _page(items, is_truncated=False, next_marker=None):
    return {
        "DistributionList": {
            "Items": items,
            "IsTruncated": is_truncated,
            "NextMarker": next_marker,
        }
    }


class FakeClients:
    """Fake client factory covering cloudformation + cloudfront."""

    def __init__(self, stack=None, pages=None, error=None):
        self.stack = stack if stack is not None else _stack()
        self.pages = pages if pages is not None else [_page([_distribution()])]
        self.error = error
        self.requested_services = []

    def __call__(self, service, region_name=None):
        self.requested_services.append((service, region_name))
        if self.error is not None and service == self.error[0]:
            raise self.error[1]
        if service == "cloudformation":
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [self.stack]}
            )
        if service == "cloudfront":
            return SimpleNamespace(list_distributions=self._list)
        raise AssertionError(f"unexpected service requested: {service}")

    def _list(self, **kwargs):
        marker = kwargs.get("Marker")
        index = 0 if marker is None else int(marker)
        page = self.pages[index]
        if isinstance(page, Exception):
            raise page
        return page


class ResolveIdentityTests(SimpleTestCase):
    """The happy paths: the identity resolves and can be captured."""

    def test_healthy_stack_resolves_the_live_distribution(self):
        factory = FakeClients()
        exit_code = verify_distribution_identity.run(
            STACK, REGION, client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_cloudfront_client_is_created_in_us_east_1(self):
        """CloudFront is a global service; its API lives in us-east-1."""
        factory = FakeClients()
        verify_distribution_identity.run(STACK, REGION, client_factory=factory)
        self.assertIn(("cloudfront", "us-east-1"), factory.requested_services)
        self.assertIn(("cloudformation", REGION), factory.requested_services)

    def test_expected_distribution_id_matching_succeeds(self):
        """The ordinary deploy: the ID is the one captured before it."""
        factory = FakeClients()
        exit_code = verify_distribution_identity.run(
            STACK,
            REGION,
            expect_distribution_id=DIST_ID,
            client_factory=factory,
        )
        self.assertEqual(exit_code, 0)

    def test_distribution_arn_output_matching_the_live_distribution(self):
        """A stack that already exports the ARN cross-checks cleanly."""
        factory = FakeClients(stack=_stack(exported_arn=DIST_ARN))
        exit_code = verify_distribution_identity.run(
            STACK, REGION, client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_rolled_back_update_still_resolves_the_live_identity(self):
        """UPDATE_ROLLBACK_COMPLETE keeps the last GOOD resources live."""
        factory = FakeClients(stack=_stack(stack_status="UPDATE_ROLLBACK_COMPLETE"))
        exit_code = verify_distribution_identity.run(
            STACK, REGION, client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_paginated_distribution_list_is_followed(self):
        """The live distribution may sit on the second page."""
        factory = FakeClients(
            pages=[
                _page([_distribution("EOTHER")], is_truncated=True,
                      next_marker="1"),
                _page([_distribution()]),
            ]
        )
        exit_code = verify_distribution_identity.run(
            STACK, REGION, client_factory=factory
        )
        self.assertEqual(exit_code, 0)


class ReplacedDistributionTests(SimpleTestCase):
    """A replaced distribution must fail loudly, not pass quietly."""

    def test_changed_distribution_id_is_detected(self):
        """A different live ID means the distribution was replaced."""
        factory = FakeClients(
            pages=[_page([_distribution("E2REPLACED", domain=DIST_DOMAIN)])]
        )
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK,
                REGION,
                expect_distribution_id=DIST_ID,
                client_factory=factory,
            )
        self.assertIn("REPLACED", str(raised.exception))

    def test_stale_distribution_arn_output_is_detected(self):
        """An exported ARN pointing elsewhere would hand the edge a dead ARN."""
        factory = FakeClients(
            stack=_stack(
                exported_arn="arn:aws:cloudfront::768125641662:distribution/ESTALE"
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("DistributionArn", str(raised.exception))

    def test_malformed_distribution_arn_output_is_detected(self):
        """The Output must be a CloudFront distribution ARN, not free text."""
        factory = FakeClients(stack=_stack(exported_arn="not-an-arn"))
        with self.assertRaises(RuntimeError):
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )

    def test_domain_served_by_no_live_distribution_is_detected(self):
        """A stack output that no distribution serves means drift."""
        factory = FakeClients(
            pages=[_page([_distribution("EOTHER", domain="d999.cloudfront.net")])]
        )
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("no live CloudFront distribution", str(raised.exception))


class UnhealthyStateTests(SimpleTestCase):
    """An untrustworthy stack must not yield a trusted identity."""

    def test_rollback_complete_stack_is_rejected(self):
        """A create that rolled back has no live resources at all."""
        factory = FakeClients(stack=_stack(stack_status="ROLLBACK_COMPLETE"))
        with self.assertRaises(RuntimeError):
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )

    def test_delete_complete_stack_is_rejected(self):
        """A deleted stack must not verify as deployed."""
        factory = FakeClients(stack=_stack(stack_status="DELETE_COMPLETE"))
        with self.assertRaises(RuntimeError):
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )

    def test_in_progress_stack_is_rejected(self):
        """A stack mid-update is in flux; its outputs are not authoritative."""
        factory = FakeClients(stack=_stack(stack_status="UPDATE_IN_PROGRESS"))
        with self.assertRaises(RuntimeError):
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )

    def test_missing_cloudfront_domain_output_is_rejected(self):
        """Without the domain output the live distribution is unidentifiable."""
        factory = FakeClients(stack=_stack(domain=None))
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("CloudFrontDomain", str(raised.exception))

    def test_missing_stack_is_rejected(self):
        """CloudFormation returned no stack at all."""
        empty = SimpleNamespace(
            describe_stacks=lambda StackName: {"Stacks": []}
        )
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK,
                REGION,
                client_factory=lambda service, region_name=None: empty,
            )
        self.assertIn("no stack named", str(raised.exception))


class MalformedResponseTests(SimpleTestCase):
    """Malformed AWS replies must fail with a message, not a traceback."""

    def test_missing_distribution_list_is_rejected(self):
        factory = FakeClients(pages=[{"NotADistributionList": {}}])
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("malformed CloudFront response", str(raised.exception))

    def test_non_object_reply_is_rejected(self):
        factory = FakeClients(pages=[["not", "a", "reply"]])
        with self.assertRaises(RuntimeError):
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )

    def test_truncated_list_without_next_marker_is_rejected(self):
        factory = FakeClients(
            pages=[_page([_distribution()], is_truncated=True, next_marker=None)]
        )
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("NextMarker", str(raised.exception))

    def test_distribution_entry_without_id_is_rejected(self):
        factory = FakeClients(pages=[_page([{"DomainName": DIST_DOMAIN}])])
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIn("carries no Id", str(raised.exception))


class ApiFailureTests(SimpleTestCase):
    """An AWS error must never be mistaken for a green check."""

    def test_cloudformation_api_error_propagates(self):
        boom = RuntimeError("connection reset by peer")
        factory = FakeClients(error=("cloudformation", boom))
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIs(raised.exception, boom)

    def test_cloudfront_api_error_propagates(self):
        boom = RuntimeError("throttled")
        factory = FakeClients(error=("cloudfront", boom))
        with self.assertRaises(RuntimeError) as raised:
            verify_distribution_identity.run(
                STACK, REGION, client_factory=factory
            )
        self.assertIs(raised.exception, boom)


class MainArgParsingTests(SimpleTestCase):
    """The CLI surface the runbook calls."""

    def test_region_is_required(self):
        with self.assertRaises(SystemExit) as raised:
            verify_distribution_identity.main(["--stack-name", STACK])
        self.assertEqual(raised.exception.code, 2)

    def test_main_returns_zero_when_the_identity_resolves(self):
        exit_code = verify_distribution_identity.main(
            ["--region", REGION, "--stack-name", STACK],
            client_factory=FakeClients(),
        )
        self.assertEqual(exit_code, 0)

    def test_main_returns_one_when_a_check_fails(self):
        """A failed check exits 1 (the deploy gate must fail loudly)."""
        factory = FakeClients(
            pages=[_page([_distribution("E2REPLACED", domain=DIST_DOMAIN)])]
        )
        exit_code = verify_distribution_identity.main(
            [
                "--region",
                REGION,
                "--stack-name",
                STACK,
                "--expect-distribution-id",
                DIST_ID,
            ],
            client_factory=factory,
        )
        self.assertEqual(exit_code, 1)

    def test_main_accepts_the_expected_distribution_id(self):
        exit_code = verify_distribution_identity.main(
            [
                "--region",
                REGION,
                "--stack-name",
                STACK,
                "--expect-distribution-id",
                DIST_ID,
            ],
            client_factory=FakeClients(),
        )
        self.assertEqual(exit_code, 0)
