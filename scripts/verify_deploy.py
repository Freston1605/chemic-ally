#!/usr/bin/env python3
"""Deploy-job verifier that gates on real AWS state (R012).

Wired into ``.github/workflows/deploy.yml`` in place of the old wildcard
``aws lambda get-function`` step, which (a) cannot resolve ``*`` in a function
name, and (b) sat behind ``|| echo`` so it could never fail the job. This
script makes each check a hard, attributable gate:

* the CloudFormation stack must exist and be in a healthy terminal state —
  ``*_FAILED``, ``*_IN_PROGRESS``, ``*_ROLLBACK_COMPLETE`` and
  ``DELETE_COMPLETE`` all fail (a mid-roll or rolled-back stack means the
  deploy did not stick);
* the stack must expose a ``LambdaFunctionArn`` Output (template contract);
  ``MigrateFunctionArn`` is printed when present;
* with ``--expect-tag``, the stack's ``ImageTag`` parameter must equal the
  tag deployed by this run (``github.run_number``) — a normal deploy always
  advances it, so a stale or skipped deploy cannot pass;
* both functions (Django always, migrator when output present) must report
  ``State == Active`` and a ``LastUpdateStatus`` that is absent, empty, or
  ``Successful``; ``Failed`` and ``InProgress`` fail.

Exit codes: 0 = all checks pass, 1 = one or more checks failed.
AWS API failures (missing stack, throttling) bubble as raised exceptions so
the deploy job fails loudly with the traceback in the logs.

``run`` takes an injectable ``client_factory`` so the unit tests exercise the
full control flow with fake clients instead of reaching AWS.
"""

import argparse
import sys

import boto3

# Fail on every unhealthy terminal/in-progress status suffix. Suffix matching
# keeps this robust as AWS adds new statuses (e.g. IMPORT_ROLLBACK_COMPLETE).
UNHEALTHY_STATUS_SUFFIXES = ("_FAILED", "_IN_PROGRESS")
UNHEALTHY_STATUSES = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}
# Anything not unhealthy must be one of these known terminal states.
HEALTHY_TERMINAL_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}

# Pass only on an explicit success or the "no update has ever run on this
# function" absence. InProgress/Failed/Rollback and any future value fail.
PASSING_LAST_UPDATE_STATUS = {None, "", "Successful"}


def _stack_status(stack):
    return stack.get("StackStatus")


def _stack_outputs(stack):
    return {o.get("OutputKey"): o.get("OutputValue") for o in stack.get("Outputs", [])}


def _stack_parameters(stack):
    return {
        p.get("ParameterKey"): p.get("ParameterValue")
        for p in stack.get("Parameters", [])
    }


def _resolve_stack(cfn_client, stack_name):
    """Describe the stack and fail with a clear message when unhealthy."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])
    if not stacks:
        raise RuntimeError(f"CloudFormation returned no stack named {stack_name!r}")
    stack = stacks[0]
    status = _stack_status(stack)
    unhealthy = (
        any(status.endswith(suffix) for suffix in UNHEALTHY_STATUS_SUFFIXES)
        or status in UNHEALTHY_STATUSES
    )
    # Defensive: even a status that matches neither list must be a known
    # healthy terminal state, not some unknown value.
    if unhealthy or status not in HEALTHY_TERMINAL_STATUSES:
        raise RuntimeError(
            f"Stack {stack_name} StackStatus={status!r} is not a healthy "
            "terminal state (rejected *_FAILED, *_IN_PROGRESS, "
            "*_ROLLBACK_COMPLETE, DELETE_COMPLETE)"
        )
    print(f"PASS: Stack {stack_name} StackStatus={status}")
    return stack


def _check_outputs(stack, stack_name):
    """Require LambdaFunctionArn, print MigrateFunctionArn when present."""
    outputs = _stack_outputs(stack)
    arn = outputs.get("LambdaFunctionArn")
    if not arn:
        raise RuntimeError(
            f"Stack {stack_name!r} has no LambdaFunctionArn Output; "
            "the template drifted from the deploy contract"
        )
    print(f"PASS: LambdaFunctionArn={arn}")
    migrate_arn = outputs.get("MigrateFunctionArn")
    if migrate_arn:
        print(f"PASS: MigrateFunctionArn={migrate_arn}")
    return arn, migrate_arn


def _check_image_tag(stack, stack_name, expect_tag):
    """Assert the stack's ImageTag parameter matches this run's tag."""
    if expect_tag is None:
        return
    image_tag = _stack_parameters(stack).get("ImageTag")
    if image_tag != expect_tag:
        raise RuntimeError(
            f"Stack {stack_name} ImageTag={image_tag!r} does not match the "
            f"deployed image tag {expect_tag!r}; the deploy did not apply "
            "(stale stack or empty changeset)"
        )
    print(f"PASS: ImageTag={image_tag} matches --expect-tag")


def _check_function(lambda_client, arn):
    """Require State Active and a pass-or-absent LastUpdateStatus."""
    config = lambda_client.get_function_configuration(FunctionName=arn)
    state = config.get("State")
    last_update = config.get("LastUpdateStatus")
    if state != "Active":
        raise RuntimeError(
            f"Function {arn} State={state!r} is not Active; Lambda update did "
            "not complete"
        )
    if last_update not in PASSING_LAST_UPDATE_STATUS:
        raise RuntimeError(
            f"Function {arn} LastUpdateStatus={last_update!r} is not "
            "Successful; the deployment update failed or is still running"
        )
    print(
        f"PASS: {arn} State=Active LastUpdateStatus="
        f"{last_update if last_update else 'absent'}"
    )


def run(stack_name, region, *, expect_tag=None, client_factory=boto3.client) -> int:
    """Run every verification check. Returns 0 or 1; raises on API errors."""
    cfn_client = client_factory("cloudformation", region_name=region)
    stack = _resolve_stack(cfn_client, stack_name)
    arn, migrate_arn = _check_outputs(stack, stack_name)
    _check_image_tag(stack, stack_name, expect_tag)

    lambda_client = client_factory("lambda", region_name=region)
    _check_function(lambda_client, arn)
    if migrate_arn:
        _check_function(lambda_client, migrate_arn)
    print(f"Deploy verification OK for stack {stack_name}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify SAM deploy reached a healthy, current state"
    )
    parser.add_argument(
        "--stack-name", default="chemically", help="CloudFormation stack name"
    )
    parser.add_argument("--region", required=True, help="AWS region")
    parser.add_argument(
        "--expect-tag",
        default=None,
        help="Required ImageTag parameter value (github.run_number)",
    )
    args = parser.parse_args(argv)
    return run(args.stack_name, args.region, expect_tag=args.expect_tag)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
