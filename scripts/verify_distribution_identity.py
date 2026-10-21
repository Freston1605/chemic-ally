#!/usr/bin/env python3
"""Capture and compare the live CloudFront distribution identity (M007/S01).

The us-east-1 edge stack associates the WAF rule with the live CloudFront
distribution by ARN. CloudFormation updates a distribution IN PLACE when its
logical ID is unchanged, so the distribution ID -- and therefore the ARN --
survives an ordinary deploy; it changes only when the stack is deleted and
recreated. A silently replaced distribution would leave the edge association
pointing at a distribution that no longer serves traffic: the control would
look deployed and enforce nothing.

This script is the operator-side (and CI-side) witness of that identity:

* before a deploy it PRINTS the live distribution's ID, ARN and domain, so
  the identity can be captured and compared afterwards;
* after a deploy it compares the live identity against the captured one and
  fails when the ID changed.

Everything here is read-only: it calls ``cloudformation:DescribeStacks`` and
``cloudfront:ListDistributions`` and mutates nothing.

The distribution is resolved from the CloudFront API by the domain the stack
reports (the ``CloudFrontDomain`` Output), NOT from the ``DistributionArn``
Output: that Output only exists once the WAF-free template has been
deployed at least once, while the pre-deploy capture has to work against the
CURRENT (pre-M007) stack as well. When the stack DOES export
``DistributionArn``, it is cross-checked against the resolved distribution,
so a stale export cannot pass unnoticed.

Usage::

    # capture the identity before a deploy
    python scripts/verify_distribution_identity.py --region us-east-2

    # after the deploy: the ID must be the captured one
    python scripts/verify_distribution_identity.py --region us-east-2 \
        --expect-distribution-id "$BEFORE_ID"

Exit codes: 0 = the identity was resolved (and matched, when an expected ID
was given), 1 = a check failed. AWS API failures are NOT swallowed: they
propagate as raised exceptions, so a throttling error or a lost connection
is never mistaken for a green check.

``run`` takes an injectable ``client_factory`` so the unit tests exercise the
full control flow with fake clients instead of reaching AWS.
"""

import argparse
import sys

import boto3

# The stack must be in a state whose resources are the live ones. A stack
# that is mid-update is in flux, so the identity it reports is not
# authoritative; a create that rolled back (ROLLBACK_COMPLETE) has no live
# resources at all.
ACCEPTABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "IMPORT_COMPLETE",
    # The last UPDATE was rolled back: the stack still holds the resources
    # of the last GOOD deploy, so the identity is still authoritative --
    # but the runbook must know that the previous deploy did not stick.
    "UPDATE_ROLLBACK_COMPLETE",
}
UNHEALTHY_STATUS_SUFFIXES = ("_FAILED", "_IN_PROGRESS")
UNHEALTHY_STATUSES = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}

# CloudFront is a global service; its API endpoint lives in us-east-1. The
# client is therefore always created there, whatever region the stack is in.
CLOUDFRONT_REGION = "us-east-1"

# AWS caps a list_distributions page at 100 items per response; the loop
# below follows NextMarker until the list is exhausted.
PAGE_SIZE = 100


def _stack_outputs(stack):
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


def _resolve_stack(cfn_client, stack_name):
    """Describe the stack and fail when its state is not authoritative."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", []) or []
    if not stacks:
        raise RuntimeError(
            f"CloudFormation returned no stack named {stack_name!r}"
        )
    stack = stacks[0]
    status = stack.get("StackStatus")
    if status in UNHEALTHY_STATUSES or any(
        status.endswith(suffix) for suffix in UNHEALTHY_STATUS_SUFFIXES
    ):
        raise RuntimeError(
            f"Stack {stack_name} StackStatus={status!r} does not describe a "
            "live deployment; the identity of the live site cannot be read "
            "from it. Recover the stack first (see OPS_PLAYBOOK.md, 'the "
            "two-stack deployment' section)."
        )
    if status not in ACCEPTABLE_STACK_STATUSES:
        raise RuntimeError(
            f"Stack {stack_name} StackStatus={status!r} is not a state whose "
            "resources are the live ones"
        )
    if status == "UPDATE_ROLLBACK_COMPLETE":
        print(
            "WARN: stack {} StackStatus={} -- the previous deploy was rolled "
            "back; the identity below belongs to the last GOOD deploy".format(
                stack_name, status
            )
        )
    else:
        print(f"PASS: stack {stack_name} StackStatus={status}")
    return stack


def _distribution_id_from_arn(arn):
    """Return the distribution id in a CloudFront distribution ARN."""
    if not isinstance(arn, str):
        return None
    prefix = "arn:aws:cloudfront::"
    if not arn.startswith(prefix):
        return None
    remainder = arn[len(prefix):]
    account, _, resource = remainder.partition(":")
    if not account or not resource.startswith("distribution/"):
        return None
    return resource[len("distribution/"):] or None


def _list_distributions(cloudfront_client):
    """Return every distribution in the account, following the pagination."""
    items = []
    marker = None
    while True:
        kwargs = {"MaxItems": PAGE_SIZE}
        if marker:
            kwargs["Marker"] = marker
        response = cloudfront_client.list_distributions(**kwargs)
        if not isinstance(response, dict):
            raise RuntimeError(
                "malformed CloudFront response: list_distributions did not "
                "return an object"
            )
        distribution_list = response.get("DistributionList")
        if not isinstance(distribution_list, dict):
            raise RuntimeError(
                "malformed CloudFront response: no DistributionList in the "
                "list_distributions reply"
            )
        items.extend(distribution_list.get("Items") or [])
        if not distribution_list.get("IsTruncated"):
            break
        marker = distribution_list.get("NextMarker")
        if not marker:
            raise RuntimeError(
                "malformed CloudFront response: the distribution list is "
                "truncated but carries no NextMarker"
            )
    return items


def _find_distribution(items, domain):
    """Return the distribution whose cloudfront.net domain is ``domain``."""
    for item in items:
        if item.get("DomainName") == domain:
            return item
    return None


def run(
    stack_name,
    region,
    *,
    expect_distribution_id=None,
    client_factory=boto3.client,
):
    """Resolve the live distribution identity; return 0 or raise RuntimeError.

    ``expect_distribution_id`` is the identity captured before a deploy: when
    it is given and the live ID differs, the distribution was REPLACED, which
    means the edge stack's imported ARN now points at a distribution that no
    longer serves traffic.
    """
    cfn_client = client_factory("cloudformation", region_name=region)
    stack = _resolve_stack(cfn_client, stack_name)
    outputs = _stack_outputs(stack)

    domain = outputs.get("CloudFrontDomain")
    if not domain:
        raise RuntimeError(
            f"Stack {stack_name!r} has no CloudFrontDomain Output; the "
            "template drifted from the deploy contract and the live "
            "distribution cannot be identified"
        )

    cloudfront_client = client_factory(
        "cloudfront", region_name=CLOUDFRONT_REGION
    )
    items = _list_distributions(cloudfront_client)
    distribution = _find_distribution(items, domain)
    if distribution is None:
        raise RuntimeError(
            f"no live CloudFront distribution serves {domain!r} (the domain "
            f"reported by stack {stack_name!r}); the stack output and the "
            "live distributions disagree"
        )

    distribution_id = distribution.get("Id")
    if not distribution_id:
        raise RuntimeError(
            "malformed CloudFront response: the distribution serving "
            f"{domain!r} carries no Id"
        )
    arn = distribution.get("ARN") or ""
    print(f"PASS: distribution id={distribution_id}")
    print(f"PASS: distribution domain={domain}")
    if arn:
        print(f"PASS: distribution arn={arn}")

    exported_arn = outputs.get("DistributionArn")
    if exported_arn:
        exported_id = _distribution_id_from_arn(exported_arn)
        if exported_id is None:
            raise RuntimeError(
                f"the DistributionArn Output of stack {stack_name!r} is not a "
                f"CloudFront distribution ARN: {exported_arn!r}"
            )
        if exported_id != distribution_id:
            raise RuntimeError(
                f"the DistributionArn Output of stack {stack_name!r} points at "
                f"{exported_id} while the live distribution is "
                f"{distribution_id}; the edge stack would associate the rule "
                "with a distribution that no longer serves traffic"
            )
        print(
            "PASS: the DistributionArn Output matches the live distribution "
            f"({exported_id})"
        )

    if expect_distribution_id is not None:
        if expect_distribution_id != distribution_id:
            raise RuntimeError(
                f"the CloudFront distribution was REPLACED: it was "
                f"{expect_distribution_id} before the deploy and is "
                f"{distribution_id} now. The edge stack's imported ARN must "
                "be re-pointed at the new distribution (and the Cloudflare "
                "DNS records re-synced)."
            )
        print(
            "PASS: the distribution ID is unchanged since the pre-deploy "
            f"capture ({distribution_id})"
        )

    print(f"DISTRIBUTION_ID={distribution_id}")
    return 0


def main(argv=None, client_factory=boto3.client) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the live CloudFront distribution identity"
    )
    parser.add_argument(
        "--stack-name", default="chemically", help="CloudFormation stack name"
    )
    parser.add_argument("--region", required=True, help="AWS region")
    parser.add_argument(
        "--expect-distribution-id",
        default=None,
        help=(
            "Distribution ID captured before the deploy; the check fails when "
            "the live distribution is a different one"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return run(
            args.stack_name,
            args.region,
            expect_distribution_id=args.expect_distribution_id,
            client_factory=client_factory,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
