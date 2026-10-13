"""Unit tests for the deploy verifier (scripts/verify_deploy.py).

R012: the deploy job's verification step must gate on real AWS state instead
of the old ``aws lambda get-function ... --function-name *-DjangoFunction-* ||
echo`` decoration, which can never fail the job. Each test pins one gate:

* a healthy stack (UPDATE_COMPLETE) with Active/Successful functions -> 0;
* LastUpdateStatus=Failed -> non-zero (the failed SAM update is caught);
* LastUpdateStatus absent (fresh create) -> 0;
* StackStatus=ROLLBACK_COMPLETE -> non-zero;
* missing LambdaFunctionArn Output -> non-zero (template drift);
* --expect-tag mismatch -> non-zero (stale/missed deploy caught).

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients — no network access.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_deploy.py"

_spec = importlib.util.spec_from_file_location("verify_deploy", MODULE_PATH)
verify_deploy = importlib.util.module_from_spec(_spec)
sys.modules["verify_deploy"] = verify_deploy
_spec.loader.exec_module(verify_deploy)

REGION = "us-east-2"
STACK = "chemically"
DJANGO_ARN = (
    "arn:aws:lambda:us-east-2:768125641662"
    ":function:chemically-DjangoFunction"
)
MIGRATE_ARN = (
    "arn:aws:lambda:us-east-2:768125641662"
    ":function:chemically-MigrateFunction"
)


def _stack(stack_status="UPDATE_COMPLETE", outputs=None, parameters=None):
    return {
        "StackName": STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [
            {"OutputKey": "LambdaFunctionArn", "OutputValue": DJANGO_ARN},
            {"OutputKey": "MigrateFunctionArn", "OutputValue": MIGRATE_ARN},
        ],
        "Parameters": parameters
        if parameters is not None
        else [{"ParameterKey": "ImageTag", "ParameterValue": "123"}],
    }


def _function_config(arn, state="Active", last_update_status="Successful"):
    return {"FunctionArn": arn, "State": state, "LastUpdateStatus": last_update_status}


class FakeClients:
    """Fake client factory covering cloudformation + lambda for the verifier."""

    def __init__(self, stack, django_config=None, migrate_config=None):
        self._stack = stack
        self._django_config = django_config or _function_config(DJANGO_ARN)
        self._migrate_config = migrate_config

    def __call__(self, service, region_name=None):
        assert region_name == REGION
        if service == "cloudformation":
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [self._stack]}
            )
        if service == "lambda":
            configs = {
                DJANGO_ARN: self._django_config,
                MIGRATE_ARN: self._migrate_config or _function_config(MIGRATE_ARN),
            }
            return SimpleNamespace(
                get_function_configuration=lambda FunctionName: configs[FunctionName]
            )
        raise AssertionError(f"unexpected service requested: {service}")


class VerifyDeployTests(SimpleTestCase):
    def test_healthy_stack_succeeds(self):
        """UPDATE_COMPLETE + Active/Successful functions -> exit 0."""
        factory = FakeClients(_stack())
        exit_code = verify_deploy.run(
            STACK, REGION, expect_tag="123", client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_fresh_create_with_absent_last_update_status_succeeds(self):
        """A just-created stack reports no LastUpdateStatus yet -> exit 0."""
        factory = FakeClients(
            _stack(
                stack_status="CREATE_COMPLETE",
                parameters=[{"ParameterKey": "ImageTag", "ParameterValue": "124"}],
            ),
            django_config={"FunctionArn": DJANGO_ARN, "State": "Active"},
            migrate_config={"FunctionArn": MIGRATE_ARN, "State": "Active"},
        )
        exit_code = verify_deploy.run(
            STACK, REGION, expect_tag="124", client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_failed_last_update_status_is_nonzero(self):
        """LastUpdateStatus=Failed must fail the deploy verification."""
        factory = FakeClients(
            _stack(),
            django_config=_function_config(
                DJANGO_ARN, last_update_status="Failed"
            ),
        )
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_in_progress_last_update_status_is_nonzero(self):
        """LastUpdateStatus=InProgress means the update never finished."""
        factory = FakeClients(
            _stack(),
            django_config=_function_config(
                DJANGO_ARN, last_update_status="InProgress"
            ),
        )
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_non_active_state_is_nonzero(self):
        """State=Pending would be a function that never went live."""
        factory = FakeClients(
            _stack(), django_config=_function_config(DJANGO_ARN, state="Pending")
        )
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_rollback_complete_stack_is_nonzero(self):
        """A rolled-back stack means the deploy did not stick."""
        factory = FakeClients(_stack(stack_status="ROLLBACK_COMPLETE"))
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_update_in_progress_stack_is_nonzero(self):
        """A stack still mid-update is not a healthy terminal state."""
        factory = FakeClients(_stack(stack_status="UPDATE_IN_PROGRESS"))
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_delete_complete_stack_is_nonzero(self):
        """A deleted stack must not verify as deployed."""
        factory = FakeClients(_stack(stack_status="DELETE_COMPLETE"))
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_missing_lambda_function_arn_output_is_nonzero(self):
        """A template without the LambdaFunctionArn Output has drifted."""
        factory = FakeClients(_stack(outputs=[]))
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)

    def test_expect_tag_mismatch_is_nonzero(self):
        """A stack still on an older ImageTag proves a stale deploy."""
        factory = FakeClients(
            _stack(
                parameters=[
                    {"ParameterKey": "ImageTag", "ParameterValue": "99"}
                ]
            )
        )
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, expect_tag="130", client_factory=factory)

    def test_expect_tag_match_passes(self):
        """The happy --expect-tag path advances with each run number."""
        factory = FakeClients(_stack())
        exit_code = verify_deploy.run(
            STACK, REGION, expect_tag="123", client_factory=factory
        )
        self.assertEqual(exit_code, 0)

    def test_verifies_both_functions_when_migrate_present(self):
        """When MigrateFunctionArn exists, its config is checked too."""
        factory = FakeClients(
            _stack(),
            migrate_config=_function_config(
                MIGRATE_ARN, last_update_status="Failed"
            ),
        )
        with self.assertRaises(RuntimeError):
            verify_deploy.run(STACK, REGION, client_factory=factory)


class VerifyDeployMainArgParsing(SimpleTestCase):
    def test_main_requires_region(self):
        """--region is required; argparse errors exit non-zero (SystemExit 2)."""
        with self.assertRaises(SystemExit) as raised:
            verify_deploy.main(["--stack-name", "chemically"])
        self.assertEqual(raised.exception.code, 2)
