"""Unit tests for the migrator deploy helper (scripts/run_migrator.py).

R011/D017: the deploy job's migration step must
* return 0 and print exactly ``RDS absent (D006) - migrations skipped`` when
  no RDS instance is available (D006), without invoking Lambda;
* return 0 when the migrator Lambda payload reports ``ok``;
* return 1 when the Lambda response carries ``FunctionError`` (or the payload
  is missing / not ok) — ``aws lambda invoke`` exits 0 even when the function
  throws, so the payload is the only reliable signal.

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients — no network access.
"""

import importlib.util
import io
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_migrator.py"

_spec = importlib.util.spec_from_file_location("run_migrator", MODULE_PATH)
run_migrator = importlib.util.module_from_spec(_spec)
sys.modules["run_migrator"] = run_migrator
_spec.loader.exec_module(run_migrator)

FUNCTION_ARN = (
    "arn:aws:lambda:us-east-2:768125641662"
    ":function:chemically-MigrateFunction"
)
REGION = "us-east-2"
STACK = "chemically"


class FakePayload:
    """Mimics botocore's StreamingBody for a JSON (or raw) payload."""

    def __init__(self, raw):
        self._raw = raw if isinstance(raw, bytes) else raw.encode("utf-8")

    def read(self):
        return self._raw


class FakeClients:
    """Builds a fake boto3 client factory covering the three services used."""

    def __init__(self, stack_outputs, instances, invoke_response=None):
        self._stack_outputs = stack_outputs
        self._instances = instances
        self._invoke_response = invoke_response
        self.calls = {}

    def _cfn(self, region_name):
        self.calls.setdefault("cfn", []).append(region_name)
        return types.SimpleNamespace(
            describe_stacks=lambda StackName: {
                "Stacks": [{"StackName": StackName, "Outputs": self._stack_outputs}]
            }
        )

    def _rds(self, region_name):
        self.calls.setdefault("rds", []).append(region_name)
        return types.SimpleNamespace(
            describe_db_instances=lambda **kwargs: {
                "DBInstances": self._instances,
                "Marker": None,
            }
        )

    def _lambda(self, region_name):
        self.calls.setdefault("lambda", []).append(region_name)
        self.invoke_mock = mock.Mock(return_value=self._invoke_response or {})
        return types.SimpleNamespace(invoke=self.invoke_mock)

    def as_factory(self):
        def client_factory(service, region_name):
            return {
                "cloudformation": self._cfn,
                "rds": self._rds,
                "lambda": self._lambda,
            }[service](region_name)

        return client_factory


def run_helper(clients):
    """Run the helper with fake clients; returns (exit_code, stdout)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = run_migrator.run(
            STACK, REGION, client_factory=clients.as_factory()
        )
    return code, buffer.getvalue()


class RunMigratorTests(SimpleTestCase):
    """Control-flow contract of scripts/run_migrator.py."""

    def test_rds_absent_skips_and_exits_zero(self):
        """D006: no available instance -> exact skip line, exit 0, no invoke."""
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "MigrateFunctionArn",
                            "OutputValue": FUNCTION_ARN}],
            # Instances exist but none is "available".
            instances=[{"DBInstanceIdentifier": "chemically_db",
                        "DBInstanceStatus": "stopped"}],
            invoke_response={"Payload": FakePayload('{"status": "ok"}')},
        )
        code, stdout = run_helper(clients)

        self.assertEqual(code, 0)
        self.assertIn(run_migrator.SKIP_MESSAGE, stdout.splitlines())
        self.assertEqual(clients.calls.get("lambda"), None)

    def test_no_instances_at_all_skips(self):
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "MigrateFunctionArn",
                            "OutputValue": FUNCTION_ARN}],
            instances=[],
        )
        code, stdout = run_helper(clients)

        self.assertEqual(code, 0)
        self.assertEqual(run_migrator.SKIP_MESSAGE, stdout.strip())

    def test_clean_invoke_returns_zero(self):
        """RDS available + payload ok -> invoke the ARN and exit 0."""
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "MigrateFunctionArn",
                            "OutputValue": FUNCTION_ARN}],
            instances=[{"DBInstanceIdentifier": "chemically_db",
                        "DBInstanceStatus": "available"}],
            invoke_response={"Payload": FakePayload('{"status": "ok"}')},
        )
        code, _ = run_helper(clients)

        self.assertEqual(code, 0)
        self.assertEqual(clients.calls["lambda"], [REGION])
        clients.invoke_mock.assert_called_once_with(
            FunctionName=FUNCTION_ARN, InvocationType="RequestResponse"
        )

    def test_function_error_returns_one(self):
        """FunctionError present -> exit 1 even with a JSON payload."""
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "MigrateFunctionArn",
                            "OutputValue": FUNCTION_ARN}],
            instances=[{"DBInstanceStatus": "available"}],
            invoke_response={
                "FunctionError": "Unhandled",
                "Payload": FakePayload('{"errorType": "Runtime.ExitError"}'),
            },
        )
        code, _ = run_helper(clients)

        self.assertEqual(code, 1)

    def test_missing_payload_returns_one(self):
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "MigrateFunctionArn",
                            "OutputValue": FUNCTION_ARN}],
            instances=[{"DBInstanceStatus": "available"}],
            invoke_response={},
        )
        self.assertEqual(run_helper(clients)[0], 1)

    def test_non_ok_and_non_json_payloads_return_one(self):
        rds_ok = {"DBInstanceStatus": "available"}
        bad_bodies = [
            FakePayload('{"status": "failed"}'),
            FakePayload("not json at all"),
            FakePayload('["unexpected", "shape"]'),
        ]
        for body in bad_bodies:
            with self.subTest(body=body._raw):
                clients = FakeClients(
                    stack_outputs=[
                        {"OutputKey": "MigrateFunctionArn",
                         "OutputValue": FUNCTION_ARN}
                    ],
                    instances=[rds_ok],
                    invoke_response={"Payload": body},
                )
                self.assertEqual(run_helper(clients)[0], 1)

    def test_missing_stack_output_bubbles(self):
        """A drifted template (no MigrateFunctionArn Output) fails loudly."""
        clients = FakeClients(
            stack_outputs=[{"OutputKey": "EcrRepositoryUri",
                            "OutputValue": "0123.dkr.ecr"}],
            instances=[{"DBInstanceStatus": "available"}],
        )
        with self.assertRaises(KeyError):
            run_helper(clients)

    def test_main_argparse_returns_run_exit_code(self):
        """main(argv) wires CLI args through to run() and returns its code."""
        with redirect_stdout(io.StringIO()):
            with mock.patch.object(
                run_migrator, "run", return_value=7
            ) as delegate:
                code = run_migrator.main(
                    ["--stack-name", STACK, "--region", REGION]
                )
        delegate.assert_called_once_with(STACK, REGION)
        self.assertEqual(code, 7)
