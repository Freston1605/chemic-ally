#!/usr/bin/env python3
"""Post-deploy verifier for the edge stack (M007/S02 + M007/S06).

The CloudFront rate limit lives in its own stack (``chemically-edge`` in
us-east-1) because AWS accepts WAFv2 web ACLs with ``Scope: CLOUDFRONT``
only in us-east-1, while the application stack deploys to us-east-2
(D015/D018).

HOW THE RULE IS ATTACHED (M007/S06, corrected on 2026-10-22): the WAFv2
API does not accept a CloudFront distribution ARN at all -- every shape was
rejected live with ``WAFInvalidParameterException ... field:
RESOURCE_ARN``. AWS's own CLI reference for ``associate-web-acl`` says so
explicitly: "Use this for all resource types except for Amazon CloudFront
distributions. For Amazon CloudFront, call ``UpdateDistribution`` for the
distribution and provide the Amazon Resource Name (ARN) of the web ACL in
the web ACL ID."

So the attachment lives on the DISTRIBUTION: the edge stack creates the web
ACL and exports its ARN, and the application stack sets the distribution's
``WebACLId`` to that ARN. This script proves that attachment is real:

* both stacks are in a state whose resources are the live ones;
* the edge stack exports a web ACL ARN;
* the app stack exports the live distribution's ARN;
* the LIVE distribution (read through the CloudFront API, not through a
  template assertion) carries exactly that web ACL ARN as its ``WebACLId``.

A rule that exists but is not attached is a control that enforces nothing,
and that is the failure this script exists to make visible.

Everything here is read-only: it calls ``cloudformation:DescribeStacks`` and
``cloudfront:GetDistribution``. Nothing is created, changed or deleted.

Exit codes: 0 = every check passes, 1 = one or more checks failed. AWS API
failures bubble as raised exceptions so a throttling error or a lost
connection is never mistaken for a green check.

``run`` takes an injectable ``client_factory`` so the unit tests exercise
the full control flow with fake clients instead of reaching AWS.
"""

import argparse
import sys

import boto3

# CloudFront is a global service; its API endpoint lives in us-east-1. The
# client is therefore always created there, whatever region the stack is in.
CLOUDFRONT_REGION = "us-east-1"

# The stack must be in a state whose resources are the live ones. A stack
# that is mid-update is in flux, so what it reports is not authoritative; a
# create that rolled back (ROLLBACK_COMPLETE) has no live resources at all.
ACCEPTABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "IMPORT_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
}
UNHEALTHY_STATUS_SUFFIXES = ("_FAILED", "_IN_PROGRESS")
UNHEALTHY_STATUSES = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}


def _stack_outputs(stack):
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


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


def _describe_stack(cfn_client, stack_name, region):
    """Describe a stack and fail when its state is not authoritative."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", []) or []
    if not stacks:
        raise RuntimeError(
            f"CloudFormation returned no stack named {stack_name!r} in "
            f"{region}"
        )
    stack = stacks[0]
    status = stack.get("StackStatus")
    if status in UNHEALTHY_STATUSES or any(
        status.endswith(suffix) for suffix in UNHEALTHY_STATUS_SUFFIXES
    ):
        raise RuntimeError(
            f"Stack {stack_name} StackStatus={status!r} does not describe a "
            "live deployment; the edge control cannot be verified. Recover "
            "the stack first (see OPS_PLAYBOOK.md, 'The two-stack "
            "deployment')."
        )
    if status not in ACCEPTABLE_STACK_STATUSES:
        raise RuntimeError(
            f"Stack {stack_name} StackStatus={status!r} is not a state whose "
            "resources are the live ones"
        )
    if status == "UPDATE_ROLLBACK_COMPLETE":
        print(
            f"WARN: stack {stack_name} StackStatus={status} -- the last "
            "deploy was rolled back; what is verified below is the last GOOD "
            "deploy"
        )
    else:
        print(f"PASS: stack {stack_name} StackStatus={status}")
    return stack


def run(
    app_stack_name,
    app_region,
    edge_stack_name,
    edge_region,
    *,
    client_factory=boto3.client,
):
    """Verify the edge attachment; return 0 or raise RuntimeError."""
    app_client = client_factory("cloudformation", region_name=app_region)
    edge_client = client_factory("cloudformation", region_name=edge_region)
    cloudfront_client = client_factory(
        "cloudfront", region_name=CLOUDFRONT_REGION
    )

    app_stack = _describe_stack(app_client, app_stack_name, app_region)
    edge_stack = _describe_stack(edge_client, edge_stack_name, edge_region)

    app_outputs = _stack_outputs(app_stack)
    edge_outputs = _stack_outputs(edge_stack)

    web_acl_arn = edge_outputs.get("WebACLArn")
    if not web_acl_arn:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} does not export a WebACLArn output; "
            "the edge stack drifted from the deploy contract and there is no "
            "rule to attach."
        )

    distribution_arn = app_outputs.get("DistributionArn")
    if not distribution_arn:
        raise RuntimeError(
            f"Stack {app_stack_name!r} does not export a DistributionArn "
            "output; the live distribution cannot be identified."
        )
    distribution_id = _distribution_id_from_arn(distribution_arn)
    if not distribution_id:
        raise RuntimeError(
            "the DistributionArn output of stack {!r} is not a CloudFront "
            "distribution ARN: {!r}".format(app_stack_name, distribution_arn)
        )

    response = cloudfront_client.get_distribution(Id=distribution_id)
    distribution = (response or {}).get("Distribution") or {}
    config = distribution.get("DistributionConfig") or {}
    attached = config.get("WebACLId")
    if not attached:
        raise RuntimeError(
            "the live distribution {} carries NO web ACL: the edge rate "
            "limit is not attached, so it enforces nothing. The edge stack "
            "exports {} -- the app stack must set the distribution's "
            "WebACLId to it.".format(distribution_id, web_acl_arn)
        )
    if attached != web_acl_arn:
        raise RuntimeError(
            "the live distribution is attached to the web ACL {} while the "
            "edge stack exports {}; the distribution is protected by a "
            "different rule than the one this pipeline deploys".format(
                attached, web_acl_arn
            )
        )
    print(
        "PASS: the live distribution {} carries the edge web ACL ({})".format(
            distribution_id, web_acl_arn
        )
    )
    return 0


def main(argv=None, client_factory=boto3.client) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the edge stack's WAF attachment"
    )
    parser.add_argument(
        "--app-stack-name", default="chemically", help="app stack name"
    )
    parser.add_argument("--app-region", default="us-east-2")
    parser.add_argument(
        "--edge-stack-name", default="chemically-edge", help="edge stack name"
    )
    parser.add_argument("--edge-region", default="us-east-1")
    args = parser.parse_args(argv)
    try:
        return run(
            args.app_stack_name,
            args.app_region,
            args.edge_stack_name,
            args.edge_region,
            client_factory=client_factory,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
