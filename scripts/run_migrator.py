#!/usr/bin/env python3
"""Deploy-job helper that runs production migrations via ``MigrateFunction``.

Wired into ``.github/workflows/deploy.yml`` after the SAM deploy (D017). The
migration itself is delivered by a real Lambda (``config/migrate.py``), but the
invocation is gated on RDS availability because D006 has production running
with no database at all:

* no DB instance reports ``DBInstanceStatus=available`` -> print
  ``RDS absent (D006) - migrations skipped`` and exit 0 (the deploy proceeds);
* otherwise the migrator Lambda is invoked synchronously and its payload is
  checked. Exit 1 when ``FunctionError`` is present or the payload is
  missing/not ``ok`` — ``aws lambda invoke`` exits 0 even when the function
  throws, so the payload must be inspected explicitly.

Exit codes: 0 = skipped or clean, 1 = migrator failed. AWS API failures
(missing stack, missing ``MigrateFunctionArn`` Output, throttling, etc.)
bubble as raised exceptions so the deploy job fails loudly with the traceback
in the logs.

``run`` takes an injectable ``client_factory`` so the unit tests exercise the
full control flow with fake clients instead of reaching AWS.
"""

import argparse
import json
import logging
import sys

import boto3

logger = logging.getLogger("run_migrator")

SKIP_MESSAGE = "RDS absent (D006) - migrations skipped"


def _resolve_function_arn(cfn_client, stack_name):
    """Return the ``MigrateFunctionArn`` Output of the given stack."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    for stack in response.get("Stacks", []):
        for output in stack.get("Outputs", []):
            if output.get("OutputKey") == "MigrateFunctionArn":
                return output["OutputValue"]
    raise KeyError(
        f"Stack {stack_name!r} has no MigrateFunctionArn Output; "
        "the template drifted from D017 (MigrateFunction missing)"
    )


def _rds_available(rds_client):
    """True when at least one DB instance reports status ``available``."""
    instances = []
    marker = None
    while True:
        kwargs = {"Marker": marker} if marker else {}
        response = rds_client.describe_db_instances(**kwargs)
        instances.extend(response.get("DBInstances", []))
        marker = response.get("Marker")
        if not marker:
            break
    return any(
        instance.get("DBInstanceStatus") == "available" for instance in instances
    )


def _invoke_migrator(lambda_client, function_arn):
    """Invoke the migrator Lambda and classify the (payload, not exit code).

    Returns ``(exit_code, message)``: 0 when the payload reports ``ok``,
    1 for ``FunctionError``, a missing payload, or a non-ok/invalid payload.
    """
    response = lambda_client.invoke(
        FunctionName=function_arn,
        InvocationType="RequestResponse",
    )
    function_error = response.get("FunctionError")
    payload = response.get("Payload")

    if function_error is not None:
        detail = ""
        if payload is not None:
            detail = payload.read().decode("utf-8", "replace")[:500]
        return 1, f"Migrator Lambda reported FunctionError={function_error}: {detail}"
    if payload is None:
        return 1, "Migrator Lambda response was missing its Payload"
    try:
        body = json.loads(payload.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 1, "Migrator Lambda payload was not valid JSON"
    status = body.get("status") if isinstance(body, dict) else None
    if status != "ok":
        return 1, f"Migrator Lambda payload not ok: {body!r}"[:500]
    return 0, "Migrator Lambda reported ok"


def run(stack_name, region, *, client_factory=boto3.client) -> int:
    """Resolve the stack, gate on RDS, invoke the migrator. Returns exit code."""
    cfn_client = client_factory("cloudformation", region_name=region)
    function_arn = _resolve_function_arn(cfn_client, stack_name)
    logger.info("Resolved migrator ARN from stack %s", stack_name)

    rds_client = client_factory("rds", region_name=region)
    if not _rds_available(rds_client):
        print(SKIP_MESSAGE)
        return 0

    lambda_client = client_factory("lambda", region_name=region)
    exit_code, message = _invoke_migrator(lambda_client, function_arn)
    if exit_code:
        logger.error(message)
    else:
        logger.info(message)
    return exit_code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run prod migrations via Lambda")
    parser.add_argument(
        "--stack-name", default="chemically", help="CloudFormation stack name"
    )
    parser.add_argument("--region", required=True, help="AWS region")
    args = parser.parse_args(argv)
    return run(args.stack_name, args.region)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
