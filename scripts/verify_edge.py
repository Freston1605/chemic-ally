#!/usr/bin/env python3
"""Post-deploy verifier for the edge stack (M007/S02).

The CloudFront rate limit lives in its own stack (``chemically-edge`` in
us-east-1) because AWS accepts WAFv2 web ACLs with ``Scope: CLOUDFRONT``
only in us-east-1, while the application stack deploys to us-east-2
(D015/D018). Two stacks can drift apart silently, and the failure that
matters is a DETACHED association: the web ACL exists, the deployment looks
healthy, and the rate limit is attached to a distribution that no longer
serves traffic -- a security control that appears deployed and enforces
nothing.

This script is the witness that the edge control is bound to the
distribution that is actually live:

* the app stack's ``DistributionArn`` output is the authority on which
  distribution serves traffic (it is built from the distribution resource
  itself, never from a literal);
* the edge stack's ``WebACLAssociation`` resource must reference exactly
  that ARN;
* the web ACL's own resource list must contain that ARN;
* the rule's live action and limit are reported, so the operator can see
  whether the canary (``Count``) or enforcement (``Block``) is in force --
  and an unreportable mode is a failure, not an ``unknown``.

Everything here is read-only: it calls ``cloudformation:DescribeStacks``,
``cloudformation:DescribeStackResources`` and ``wafv2:ListResourcesForWebACL``.
Nothing is created, changed or deleted.

Exit codes: 0 = every check passes, 1 = one or more checks failed. AWS API
failures bubble as raised exceptions so a throttling error or a lost
connection is never mistaken for a green check.

``run`` takes an injectable ``client_factory`` so the unit tests exercise
the full control flow with fake clients instead of reaching AWS.
"""

import argparse
import sys

import boto3

# The edge stack must be in a state whose resources are the live ones. A
# stack that is mid-update is in flux; one that rolled back holds the
# resources of the last GOOD deploy, which is worth reporting but still
# describes what is live.
ACCEPTABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "IMPORT_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
}
UNHEALTHY_STATUS_SUFFIXES = ("_FAILED", "_IN_PROGRESS")
UNHEALTHY_STATUSES = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}

# The association resource inside the edge stack. It is located by its
# RESOURCE TYPE first (the template's logical name is an implementation
# detail that may be renamed); the logical name is only a fallback for
# clients that do not report the type.
ASSOCIATION_RESOURCE_TYPE = "AWS::WAFv2::WebACLAssociation"
ASSOCIATION_LOGICAL_NAME = "WafWebACLAssociation"

# The rule action the edge template allows. Anything else means the stack
# drifted and the live mode cannot be reported to the operator.
VALID_ACTIONS = {"Count", "Block"}


def _stack_outputs(stack):
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


def _stack_parameters(stack):
    return {
        parameter.get("ParameterKey"): parameter.get("ParameterValue")
        for parameter in stack.get("Parameters", []) or []
    }


def _stack_resources(cfn_client, stack_name):
    """Return the stack's resources as a list of raw resource dicts."""
    response = cfn_client.describe_stack_resources(StackName=stack_name)
    return list(response.get("StackResources", []) or [])


def _find_association(resources):
    """Return the WebACLAssociation resource, or None when there is none."""
    for resource in resources:
        if resource.get("ResourceType") == ASSOCIATION_RESOURCE_TYPE:
            return resource
    for resource in resources:
        if resource.get("LogicalResourceId") == ASSOCIATION_LOGICAL_NAME:
            return resource
    return None


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
            f"WARN: stack {stack_name} StackStatus={status} -- the last edge "
            "deploy was rolled back; what is verified below is the last GOOD "
            "deploy"
        )
    else:
        print(f"PASS: stack {stack_name} StackStatus={status}")
    return stack


def _live_rule_settings(edge_stack, edge_stack_name):
    """Resolve the deployed action and limit; fail when they are unknown."""
    parameters = _stack_parameters(edge_stack)
    outputs = _stack_outputs(edge_stack)
    action = parameters.get("WafAction") or outputs.get("Action")
    limit = parameters.get("WafRateLimit") or outputs.get("RateLimit")
    if not action:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} does not report the rule action "
            "(WafAction parameter / Action output); the live mode cannot be "
            "reported and the edge stack drifted from the deploy contract"
        )
    if action not in VALID_ACTIONS:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} reports the rule action {action!r}, "
            f"which is not one of {sorted(VALID_ACTIONS)}; the live mode "
            "cannot be reported"
        )
    if not limit:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} does not report the rate limit "
            "(WafRateLimit parameter / RateLimit output); the live ceiling "
            "cannot be reported"
        )
    try:
        limit_value = int(str(limit))
    except (TypeError, ValueError):
        raise RuntimeError(
            f"Stack {edge_stack_name!r} reports the rate limit {limit!r}, "
            "which is not a number"
        ) from None
    if limit_value <= 0:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} reports the rate limit {limit!r}, "
            "which cannot be a live ceiling"
        )
    return action, limit_value


def run(
    app_stack_name,
    app_region,
    edge_stack_name,
    edge_region,
    *,
    client_factory=boto3.client,
):
    """Verify the edge association; return 0 or raise RuntimeError."""
    app_client = client_factory("cloudformation", region_name=app_region)
    edge_client = client_factory("cloudformation", region_name=edge_region)
    waf_client = client_factory("wafv2", region_name=edge_region)

    app_stack = _describe_stack(app_client, app_stack_name, app_region)
    edge_stack = _describe_stack(edge_client, edge_stack_name, edge_region)

    app_outputs = _stack_outputs(app_stack)
    distribution_arn = app_outputs.get("DistributionArn")
    if not distribution_arn:
        raise RuntimeError(
            f"Stack {app_stack_name!r} does not export a DistributionArn "
            "output; the template drifted from the deploy contract and the "
            "live distribution cannot be identified"
        )

    edge_outputs = _stack_outputs(edge_stack)
    web_acl_arn = edge_outputs.get("WebACLArn")
    if not web_acl_arn:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} does not export a WebACLArn output; "
            "the edge stack drifted from the deploy contract"
        )

    resources = _stack_resources(edge_client, edge_stack_name)
    association = _find_association(resources)
    if association is None:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} declares no WebACLAssociation "
            "resource; the rate rule is not attached to anything"
        )
    associated_arn = association.get("PhysicalResourceId")
    if not associated_arn:
        raise RuntimeError(
            f"Stack {edge_stack_name!r} declares a WebACLAssociation without "
            "a physical id; the rate rule is not attached to anything"
        )
    # The association's physical id is the distribution it binds the web ACL
    # to. Some CloudFormation versions report it wrapped in the resource
    # ARN, so an ARN that CONTAINS the live distribution ARN is accepted --
    # the distribution ARN is unique, so this stays a tight check.
    if associated_arn != distribution_arn and distribution_arn not in associated_arn:
        raise RuntimeError(
            f"the edge stack associates the rate rule with {associated_arn!r} "
            f"while the live distribution is {distribution_arn!r}; the edge "
            "control is attached to a distribution that does not serve "
            "traffic"
        )
    print(
        "PASS: the edge association targets the live distribution "
        f"({distribution_arn})"
    )

    listed = waf_client.list_resources_for_web_acl(WebACLArn=web_acl_arn)
    listed_arns = listed.get("ResourceArns", []) or []
    if distribution_arn not in listed_arns:
        raise RuntimeError(
            f"the web ACL {web_acl_arn} is associated with {listed_arns!r} "
            f"instead of the live distribution {distribution_arn!r}; the "
            "association is detached"
        )
    print(f"PASS: the web ACL itself reports the live distribution ({web_acl_arn})")

    action, limit = _live_rule_settings(edge_stack, edge_stack_name)
    print(f"PASS: the live edge rule action is {action} (limit {limit})")
    if action == "Block":
        print(
            "NOTE: the edge rule is BLOCKING. The canary is over and the "
            "inherited 2000/300s ceiling is being enforced at the edge."
        )
    else:
        print(
            "NOTE: the edge rule is COUNTING. It measures the traffic "
            "without refusing anything - that is the canary mode."
        )

    return 0


def main(argv=None, client_factory=boto3.client) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the edge stack's WAF association"
    )
    parser.add_argument("--app-stack-name", default="chemically")
    parser.add_argument("--app-region", default="us-east-2")
    parser.add_argument("--edge-stack-name", default="chemically-edge")
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
