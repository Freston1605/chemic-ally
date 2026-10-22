"""Unit tests for the edge canary observer (scripts/verify_edge_canary.py).

M007/S05/T01: the Count phase of the edge canary is only worth running if
its traffic can be READ. These tests pin every gate of the observer:

* G1 the edge stack exists and describes the live deployment;
* G2 the web ACL exists and holds the EdgeRateLimit rule;
* G3 the live action is reportable and matches the declared action;
* G4 the live limit and evaluation window are reportable and sane;
* G5 the counters could be discovered and read;
* G6 in Count mode BlockedRequests must be 0;
* G7 in Count mode AllowedRequests must be > 0;
* G8 the ordinary site must serve 200;
* G9 a ceiling breach is reported loudly but does NOT fail the run.

Plus the three-state metric handling (value / observed-zero / unknown), the
dimension set that ``ListMetrics`` reported (the discovery is not
decoration), the JSON record (written even on a failing run) and the
guarantee that AWS API errors are raised rather than swallowed.

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients and all HTTP goes through
an injected ``http_get`` -- no network access, no database.
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_edge_canary.py"

_spec = importlib.util.spec_from_file_location("verify_edge_canary", MODULE_PATH)
verify_edge_canary = importlib.util.module_from_spec(_spec)
sys.modules["verify_edge_canary"] = verify_edge_canary
_spec.loader.exec_module(verify_edge_canary)

EDGE_REGION = "us-east-1"
EDGE_STACK = "chemically-edge"
SITE_URL = "https://chemic-ally.xyz"
WEB_ACL_ARN = "arn:aws:wafv2:us-east-1:768125641662:global/webacl/abc"

# The dimension set AWS publishes the WAF counters with. A value that is
# NOT the obvious one proves the observer uses what ListMetrics reported
# and does not guess a dimension.
DISCOVERED_DIMENSIONS = [
    {"Name": "WebACL", "Value": WEB_ACL_ARN},
    {"Name": "Rule", "Value": "EdgeRateLimit"},
    {"Name": "Region", "Value": "eu-central-1"},
]

FIXED_NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


class AwsApiError(Exception):
    """A stand-in for a botocore error (throttling, lost connection)."""


class AwsClientError(Exception):
    """A botocore-shaped error: it carries an ErrorCode."""

    def __init__(self, message, code):
        super().__init__(message)
        self.response = {"ErrorCode": code, "ErrorMessage": message}


def _stack(outputs=None, stack_status="UPDATE_COMPLETE"):
    return {
        "StackName": EDGE_STACK,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [{"OutputKey": "WebACLArn", "OutputValue": WEB_ACL_ARN}],
        "Parameters": [],
    }


def _rule(action="Count", limit=2000, window=300, name="EdgeRateLimit"):
    rule = {"Name": name, "Action": action}
    if limit is not None:
        rule["RateBasedStatement"] = {"Limit": limit}
        if window is not None:
            rule["RateBasedStatement"]["EvaluationWindowSec"] = window
    return rule


def _web_acl(rules=None):
    return {
        "Name": "ChemicAllyEdgeWebAcl",
        "Id": "abc",
        "Rules": rules if rules is not None else [_rule()],
    }


def _metric(name, dimensions=None):
    return {
        "Namespace": "AWS/WAFV2",
        "MetricName": name,
        "Dimensions": list(
            dimensions if dimensions is not None else DISCOVERED_DIMENSIONS
        ),
    }


def _default_metrics():
    return [
        _metric("AllowedRequests"),
        _metric("CountedRequests"),
        _metric("BlockedRequests"),
    ]


def _default_datapoints():
    return {
        "AllowedRequests": [{"Sum": 120.0}],
        "CountedRequests": [],
        "BlockedRequests": [],
    }


class FakeCloudFormation:
    """Only the read calls the observer is allowed to make."""

    def __init__(self, stack=None, missing=False, error=None):
        self.stack = stack if stack is not None else _stack()
        self.missing = missing
        self.error = error
        self.calls = []

    def describe_stacks(self, StackName):
        self.calls.append(("describe_stacks", StackName))
        if self.error is not None:
            raise self.error
        stacks = [] if self.missing else [self.stack]
        return {"Stacks": stacks}


class FakeWaf:
    def __init__(self, web_acl=None, error=None):
        self.web_acl = web_acl if web_acl is not None else _web_acl()
        self.error = error
        self.calls = []

    def get_web_acl(self, WebACLArn):
        self.calls.append(("get_web_acl", WebACLArn))
        if self.error is not None:
            raise self.error
        return {"WebACL": self.web_acl}


class FakeCloudWatch:
    def __init__(self, metrics=None, datapoints=None, error=None):
        self.metrics = metrics if metrics is not None else _default_metrics()
        self.datapoints = (
            datapoints if datapoints is not None else _default_datapoints()
        )
        self.error = error
        self.list_metrics_calls = []
        self.statistics_calls = []

    def list_metrics(self, Namespace, Dimensions):
        self.list_metrics_calls.append(
            {"Namespace": Namespace, "Dimensions": Dimensions}
        )
        if self.error is not None:
            raise self.error
        return {"Metrics": self.metrics}

    def get_metric_statistics(
        self, Namespace, MetricName, Dimensions, Statistics, Period,
        StartTime, EndTime,
    ):
        self.statistics_calls.append(
            {
                "Namespace": Namespace,
                "MetricName": MetricName,
                "Dimensions": Dimensions,
                "Statistics": Statistics,
                "Period": Period,
                "StartTime": StartTime,
                "EndTime": EndTime,
            }
        )
        if self.error is not None:
            raise self.error
        return {"Datapoints": self.datapoints.get(MetricName, [])}


class FakeClients:
    """The injectable client factory."""

    def __init__(self, cfn=None, waf=None, cloudwatch=None):
        self.cfn = cfn if cfn is not None else FakeCloudFormation()
        self.waf = waf if waf is not None else FakeWaf()
        self.cloudwatch = (
            cloudwatch if cloudwatch is not None else FakeCloudWatch()
        )
        self.requested = []

    def __call__(self, service, region_name=None):
        self.requested.append((service, region_name))
        if service == "cloudformation":
            return self.cfn
        if service == "wafv2":
            return self.waf
        if service == "cloudwatch":
            return self.cloudwatch
        raise AssertionError(
            "the observer must not use the service {!r}".format(service)
        )


def _http_get_ok(url, timeout):
    return 200, "<html>chemic-ally</html>"


def _record_path(case):
    """A temporary path for the JSON record (removed after the test)."""
    directory = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, directory, True)
    return os.path.join(directory, "canary-record.json")


def _run(clients=None, http_get=None, **kwargs):
    kwargs.setdefault("client_factory", clients or FakeClients())
    kwargs.setdefault("http_get", http_get or _http_get_ok)
    kwargs.setdefault("now", lambda: FIXED_NOW)
    return verify_edge_canary.run(**kwargs)


class CanaryObserverHappyPathTests(SimpleTestCase):
    """A healthy Count window passes and reports the live numbers."""

    def test_healthy_count_window_passes(self):
        self.assertEqual(_run(), 0)

    def test_it_reads_the_edge_region_only(self):
        clients = FakeClients()
        _run(clients)
        self.assertEqual(
            clients.requested,
            [
                ("cloudformation", EDGE_REGION),
                ("wafv2", EDGE_REGION),
                ("cloudwatch", EDGE_REGION),
            ],
        )

    def test_the_site_is_probed_once(self):
        calls = []

        def http_get(url, timeout):
            calls.append(url)
            return 200, ""

        _run(http_get=http_get)
        self.assertEqual(calls, [SITE_URL])

    def test_it_reports_the_live_action_limit_and_window(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _run()
        output = buffer.getvalue()
        self.assertIn("PASS: the live rule action is Count", output)
        self.assertIn("PASS: the live ceiling is 2000/300s", output)

    def test_block_mode_is_reported_when_expected(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Block")]))
        clients = FakeClients(waf=waf)
        result = _run(clients, expect_action="Block")
        self.assertEqual(result, 0)


class CanaryObserverStackGateTests(SimpleTestCase):
    """G1: the edge stack must describe the live deployment."""

    def test_rolled_back_stack_is_refused(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=_stack(stack_status="ROLLBACK_COMPLETE"))
        )
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_in_progress_stack_is_refused(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(
                stack=_stack(stack_status="UPDATE_IN_PROGRESS")
            )
        )
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_failed_stack_is_refused(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=_stack(stack_status="CREATE_FAILED"))
        )
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_missing_stack_is_refused(self):
        clients = FakeClients(cfn=FakeCloudFormation(missing=True))
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_rolled_back_stack_is_a_failure_not_a_warning(self):
        """A refused status must not be downgraded to a warning."""
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=_stack(stack_status="ROLLBACK_COMPLETE"))
        )
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("does not describe a live deployment", str(ctx.exception))

    def test_rollback_complete_deploy_is_still_observed(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(
                stack=_stack(stack_status="UPDATE_ROLLBACK_COMPLETE")
            )
        )
        self.assertEqual(_run(clients), 0)

    def test_a_stack_that_does_not_exist_is_a_loud_failure(self):
        """The one documented fallback: "no such stack" is explained."""
        clients = FakeClients(
            cfn=FakeCloudFormation(
                error=AwsClientError(
                    "Stack with id {} does not exist".format(EDGE_STACK),
                    "ValidationError",
                )
            )
        )
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("does not exist", str(ctx.exception))

    def test_another_cloudformation_error_is_not_swallowed(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(
                error=AwsClientError("Rate exceeded", "ThrottlingException")
            )
        )
        with self.assertRaises(AwsClientError):
            _run(clients)

    def test_missing_web_acl_arn_output_is_refused(self):
        clients = FakeClients(cfn=FakeCloudFormation(stack=_stack(outputs=[])))
        with self.assertRaises(RuntimeError):
            _run(clients)


class CanaryObserverAclGateTests(SimpleTestCase):
    """G2: the live web ACL must hold the EdgeRateLimit rule."""

    def test_web_acl_without_the_rule_is_refused(self):
        waf = FakeWaf(
            web_acl=_web_acl(rules=[_rule(name="SomeOtherRule")])
        )
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("EdgeRateLimit", str(ctx.exception))

    def test_unreadable_web_acl_is_refused(self):
        waf = FakeWaf(web_acl={})
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError):
            _run(clients)


class CanaryObserverActionGateTests(SimpleTestCase):
    """G3: the live action must be reportable and match the declaration."""

    def test_unknown_action_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Allow")]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("cannot be reported", str(ctx.exception))

    def test_action_contradicting_the_declaration_is_refused(self):
        """A switch that did not take effect must NOT pass."""
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Block")]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, expect_action="Count")
        self.assertIn("did not take effect", str(ctx.exception))

    def test_the_action_is_read_from_the_live_acl_not_the_stack(self):
        """The stack parameter may say Count while the ACL enforces Block."""
        stack = _stack()
        stack["Parameters"] = [
            {"ParameterKey": "WafAction", "ParameterValue": "Count"}
        ]
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Block")]))
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=stack), waf=waf
        )
        with self.assertRaises(RuntimeError):
            _run(clients, expect_action="Count")


class CanaryObserverLimitGateTests(SimpleTestCase):
    """G4: the live limit and evaluation window must be reportable."""

    def test_missing_limit_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(limit=None)]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("no rate limit", str(ctx.exception))

    def test_non_positive_limit_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(limit=0)]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_missing_evaluation_window_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(window=None)]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError):
            _run(clients)

    def test_impossible_evaluation_window_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(window=7)]))
        clients = FakeClients(waf=waf)
        with self.assertRaises(RuntimeError):
            _run(clients)


class CanaryObserverCounterGateTests(SimpleTestCase):
    """G5: the counters must be discoverable and readable."""

    def test_a_window_without_observations_is_refused(self):
        cloudwatch = FakeCloudWatch(datapoints={})
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("no data is not zero traffic", str(ctx.exception))

    def test_the_empty_window_message_is_exact(self):
        cloudwatch = FakeCloudWatch(datapoints={})
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertEqual(
            str(ctx.exception),
            "the canary window contains no observations -- no data is not "
            "zero traffic",
        )

    def test_counters_that_do_not_exist_at_all_are_refused(self):
        cloudwatch = FakeCloudWatch(metrics=[_metric("ChemicAllyEdgeWebAcl")])
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("not looking at the right metric", str(ctx.exception))

    def test_the_relaxed_mode_tolerates_an_empty_window(self):
        cloudwatch = FakeCloudWatch(datapoints={})
        clients = FakeClients(cloudwatch=cloudwatch)
        self.assertEqual(_run(clients, require_data=False), 0)


class CanaryObserverThreeStateTests(SimpleTestCase):
    """value / observed-zero / unknown are three different outcomes."""

    def test_the_three_states_are_distinct(self):
        cloudwatch = FakeCloudWatch(
            metrics=[
                _metric("AllowedRequests"),
                _metric("CountedRequests"),
            ],
            datapoints={
                "AllowedRequests": [{"Sum": 42.0}],
                "CountedRequests": [],
            },
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        record_path = _record_path(self)
        _run(clients, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        metrics = record["metrics"]
        self.assertEqual(metrics["AllowedRequests"]["state"], "value")
        self.assertEqual(metrics["AllowedRequests"]["value"], 42.0)
        self.assertEqual(metrics["CountedRequests"]["state"], "observed-zero")
        self.assertEqual(metrics["CountedRequests"]["value"], 0)
        self.assertEqual(metrics["BlockedRequests"]["state"], "unknown")
        self.assertIsNone(metrics["BlockedRequests"]["value"])

    def test_an_unknown_counter_is_never_reported_as_zero(self):
        cloudwatch = FakeCloudWatch(
            metrics=[_metric("AllowedRequests"), _metric("CountedRequests")],
            datapoints={
                "AllowedRequests": [{"Sum": 42.0}],
                "CountedRequests": [],
            },
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        record_path = _record_path(self)
        _run(clients, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertNotEqual(
            record["metrics"]["BlockedRequests"]["value"], 0
        )


class CanaryObserverDimensionTests(SimpleTestCase):
    """The dimension set is DISCOVERED, never guessed."""

    def test_the_discovered_dimensions_are_the_ones_queried(self):
        cloudwatch = FakeCloudWatch()
        clients = FakeClients(cloudwatch=cloudwatch)
        _run(clients)
        self.assertEqual(len(cloudwatch.statistics_calls), 3)
        for call in cloudwatch.statistics_calls:
            self.assertEqual(call["Dimensions"], DISCOVERED_DIMENSIONS)
            self.assertEqual(call["Namespace"], "AWS/WAFV2")
            self.assertEqual(call["Statistics"], ["Sum"])
            self.assertEqual(call["Period"], 300)

    def test_the_region_dimension_is_not_guessed(self):
        """A guessed region would silently return no data."""
        dimensions = [
            {"Name": "WebACL", "Value": WEB_ACL_ARN},
            {"Name": "Rule", "Value": "EdgeRateLimit"},
            {"Name": "Region", "Value": "ap-southeast-2"},
        ]
        cloudwatch = FakeCloudWatch(
            metrics=[_metric(name, dimensions) for name in (
                "AllowedRequests", "CountedRequests", "BlockedRequests"
            )]
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        _run(clients)
        for call in cloudwatch.statistics_calls:
            self.assertEqual(call["Dimensions"], dimensions)

    def test_list_metrics_is_scoped_to_the_web_acl(self):
        cloudwatch = FakeCloudWatch()
        clients = FakeClients(cloudwatch=cloudwatch)
        _run(clients)
        self.assertEqual(
            cloudwatch.list_metrics_calls[0]["Dimensions"],
            [{"Name": "WebACL", "Value": WEB_ACL_ARN}],
        )


class CanaryObserverBlockGateTests(SimpleTestCase):
    """G6: a Count rule cannot have refused anything."""

    def test_non_zero_blocked_requests_in_count_mode_is_refused(self):
        cloudwatch = FakeCloudWatch(
            datapoints={
                "AllowedRequests": [{"Sum": 120.0}],
                "CountedRequests": [],
                "BlockedRequests": [{"Sum": 3.0}],
            }
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("SOMETHING ELSE refused", str(ctx.exception))


class CanaryObserverTrafficGateTests(SimpleTestCase):
    """G7: real traffic must have flowed through the scoped paths."""

    def test_zero_allowed_requests_is_refused(self):
        cloudwatch = FakeCloudWatch(
            datapoints={
                "AllowedRequests": [{"Sum": 0.0}],
                "CountedRequests": [],
                "BlockedRequests": [],
            }
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("no traffic matched the rule's scope-down", str(
            ctx.exception
        ))

    def test_an_unpublished_allowed_counter_is_refused(self):
        cloudwatch = FakeCloudWatch(
            metrics=[_metric("CountedRequests"), _metric("BlockedRequests")],
            datapoints={
                "CountedRequests": [{"Sum": 1.0}],
                "BlockedRequests": [],
            },
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients)
        self.assertIn("scope-down cannot be proven", str(ctx.exception))


class CanaryObserverSiteGateTests(SimpleTestCase):
    """G8: the ordinary site must still serve 200."""

    def test_a_site_that_does_not_serve_200_is_refused(self):
        def http_get(url, timeout):
            return 503, "unavailable"

        with self.assertRaises(RuntimeError) as ctx:
            _run(http_get=http_get)
        self.assertIn("instead of 200", str(ctx.exception))

    def test_a_refused_site_is_refused(self):
        def http_get(url, timeout):
            return 403, "Forbidden"

        with self.assertRaises(RuntimeError):
            _run(http_get=http_get)


class CanaryObserverCeilingTests(SimpleTestCase):
    """G9: a ceiling breach is loud, and it does not fail the run."""

    def test_a_ceiling_breach_does_not_fail_the_run(self):
        cloudwatch = FakeCloudWatch(
            datapoints={
                "AllowedRequests": [{"Sum": 120.0}],
                "CountedRequests": [{"Sum": 5.0}],
                "BlockedRequests": [],
            }
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = _run(clients)
        self.assertEqual(result, 0)
        self.assertIn("WARNING", buffer.getvalue())
        self.assertIn("the ceiling was reached", buffer.getvalue())

    def test_a_ceiling_breach_is_recorded(self):
        cloudwatch = FakeCloudWatch(
            datapoints={
                "AllowedRequests": [{"Sum": 120.0}],
                "CountedRequests": [{"Sum": 5.0}],
                "BlockedRequests": [],
            }
        )
        clients = FakeClients(cloudwatch=cloudwatch)
        record_path = _record_path(self)
        _run(clients, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertTrue(record["ceiling_reached"])

    def test_a_quiet_window_records_no_ceiling_breach(self):
        clients = FakeClients()
        record_path = _record_path(self)
        _run(clients, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertFalse(record["ceiling_reached"])


class CanaryObserverRecordTests(SimpleTestCase):
    """The record is the evidence the budget review reads."""

    def test_the_record_carries_the_observed_numbers(self):
        clients = FakeClients()
        record_path = _record_path(self)
        _run(
            clients,
            record_out=record_path,
            window_minutes=1440,
            site_url=SITE_URL,
        )
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["window_minutes"], 1440)
        self.assertEqual(record["action"], "Count")
        self.assertEqual(record["limit"], 2000)
        self.assertEqual(record["evaluation_window_seconds"], 300)
        self.assertEqual(record["site_status"], 200)
        self.assertEqual(record["scoped_requests"], 120.0)
        self.assertAlmostEqual(
            record["observed_rate_per_window"], 120.0 / 288.0, places=6
        )
        self.assertEqual(
            record["discovered_metric_names"],
            ["AllowedRequests", "BlockedRequests", "CountedRequests"],
        )
        self.assertIn("window_start", record)
        self.assertIn("window_end", record)
        self.assertIn("observed_at", record)
        self.assertEqual(record["metrics"]["AllowedRequests"]["state"], "value")
        self.assertEqual(
            record["metrics"]["AllowedRequests"]["dimensions"],
            DISCOVERED_DIMENSIONS,
        )

    def test_the_record_is_written_even_when_the_run_fails(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=_stack(stack_status="ROLLBACK_COMPLETE"))
        )
        record_path = _record_path(self)
        with self.assertRaises(RuntimeError):
            _run(clients, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["status"], "fail")
        self.assertIn("error", record)

    def test_the_record_window_spans_the_requested_minutes(self):
        clients = FakeClients()
        record_path = _record_path(self)
        _run(clients, record_out=record_path, window_minutes=60)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        start = datetime.fromisoformat(record["window_start"])
        end = datetime.fromisoformat(record["window_end"])
        self.assertEqual((end - start).total_seconds(), 3600)


class CanaryObserverAwsErrorTests(SimpleTestCase):
    """AWS API errors are raised, never mistaken for a green check."""

    def test_a_throttling_error_is_raised(self):
        cloudwatch = FakeCloudWatch(error=AwsApiError("Rate exceeded"))
        clients = FakeClients(cloudwatch=cloudwatch)
        with self.assertRaises(AwsApiError):
            _run(clients)

    def test_a_stack_read_error_is_raised(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(error=AwsApiError("connection reset"))
        )
        with self.assertRaises(AwsApiError):
            _run(clients)

    def test_a_web_acl_read_error_is_raised(self):
        clients = FakeClients(
            waf=FakeWaf(error=AwsApiError("access denied"))
        )
        with self.assertRaises(AwsApiError):
            _run(clients)


class CanaryObserverConstantsTests(SimpleTestCase):
    """The constants are duplicated in this script, so they are pinned."""

    def test_the_constants_are_pinned(self):
        self.assertEqual(
            verify_edge_canary.WAF_METRIC_NAMESPACE, "AWS/WAFV2"
        )
        self.assertEqual(verify_edge_canary.RULE_NAME, "EdgeRateLimit")
        self.assertEqual(
            verify_edge_canary.ACL_METRIC_NAME, "ChemicAllyEdgeWebAcl"
        )
        self.assertEqual(
            verify_edge_canary.COUNTER_NAMES,
            ("AllowedRequests", "CountedRequests", "BlockedRequests"),
        )
        self.assertEqual(verify_edge_canary.CLOUDFRONT_REGION, "us-east-1")
        self.assertEqual(verify_edge_canary.DEFAULT_WINDOW_MINUTES, 1440)
        self.assertEqual(
            verify_edge_canary.DEFAULT_SITE_URL, "https://chemic-ally.xyz"
        )
        self.assertEqual(
            verify_edge_canary.DEFAULT_EDGE_STACK_NAME, "chemically-edge"
        )
        self.assertEqual(
            verify_edge_canary.DEFAULT_EDGE_REGION, "us-east-1"
        )


class CanaryObserverCliTests(SimpleTestCase):
    """The CLI must exit non-zero on a failed gate and zero on a good one."""

    def test_main_returns_zero_on_success(self):
        code = verify_edge_canary.main(
            [],
            client_factory=FakeClients(),
            http_get=_http_get_ok,
            now=lambda: FIXED_NOW,
        )
        self.assertEqual(code, 0)

    def test_main_returns_one_on_failure(self):
        clients = FakeClients(
            cfn=FakeCloudFormation(stack=_stack(stack_status="ROLLBACK_COMPLETE"))
        )
        code = verify_edge_canary.main(
            [],
            client_factory=clients,
            http_get=_http_get_ok,
            now=lambda: FIXED_NOW,
        )
        self.assertEqual(code, 1)

    def test_main_defaults_to_the_edge_stack_and_the_canary_action(self):
        clients = FakeClients()
        code = verify_edge_canary.main(
            [],
            client_factory=clients,
            http_get=_http_get_ok,
            now=lambda: FIXED_NOW,
        )
        self.assertEqual(code, 0)
        self.assertEqual(clients.cfn.calls, [("describe_stacks", EDGE_STACK)])
        self.assertEqual(clients.requested[0][1], EDGE_REGION)
