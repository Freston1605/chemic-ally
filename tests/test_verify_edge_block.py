"""Unit tests for the bounded edge-block probe (scripts/verify_edge_block.py).

M007/S05/T02: switching the edge rate rule from Count to Block is only
meaningful if the switch is PROVEN against the live distribution. These
tests pin every gate of the probe:

* S1/S2 the safety bounds (``--confirm-live``, the expensive-path refusal);
* G1 the edge stack describes the live deployment;
* G2 the web ACL holds the EdgeRateLimit rule;
* G3 the live action is Block (or Count for a dry run);
* G4 the live ceiling and evaluation window are reportable;
* G5 the probe path is inside the rule's scope-down -- checked BEFORE a
  single request is sent;
* G6 the origin answered BEFORE the first block, and the block was
  observed repeatedly;
* G7 the WAF's own BlockedRequests counter moved;
* G8 the origin-side Lambda counter did not grow by the blocked count;
* G9 the ordinary site still serves 200.

Plus the classification of all four response kinds (an edge block, an
origin-guard refusal, an answered request and a transport error), the
request cap, the pace, the GET-only transport and the JSON record (written
even on a failing run).

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients, all HTTP goes through
an injected ``http_get`` that replays a scripted sequence, and the clock
and the sleep are injected too -- no network access, no database.
"""

import base64
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from chemically.middleware import REFUSAL_BODY as MIDDLEWARE_REFUSAL_BODY

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_edge_block.py"

_spec = importlib.util.spec_from_file_location("verify_edge_block", MODULE_PATH)
verify_edge_block = importlib.util.module_from_spec(_spec)
sys.modules["verify_edge_block"] = verify_edge_block
_spec.loader.exec_module(verify_edge_block)

EDGE_REGION = "us-east-1"
APP_REGION = "us-east-2"
EDGE_STACK = "chemically-edge"
APP_STACK = "chemically"
SITE_URL = "https://chemic-ally.xyz"
WEB_ACL_ARN = "arn:aws:wafv2:us-east-1:768125641662:global/webacl/abc"
FUNCTION_NAME = "chemically-app-ChemicalAppFunction-ABCDEFG"
FUNCTION_ARN = (
    "arn:aws:lambda:us-east-2:768125641662:function:" + FUNCTION_NAME
)

# The dimension set AWS publishes the WAF counters with. A value that is
# NOT the obvious one proves the probe uses what ListMetrics reported and
# does not guess a dimension.
DISCOVERED_DIMENSIONS = [
    {"Name": "WebACL", "Value": WEB_ACL_ARN},
    {"Name": "Rule", "Value": "EdgeRateLimit"},
    {"Name": "Region", "Value": "eu-central-1"},
]

# The edge's own 403 (CloudFront's WAF error document). It is NOT the
# origin guard's refusal body.
EDGE_BLOCK_BODY = "Request blocked by AWS WAF custom rule."
EDGE_BLOCK_HEADERS = {"x-amz-cf-id": "abc123", "Content-Type": "text/xml"}

FIXED_NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


def _stack(outputs=None, stack_status="UPDATE_COMPLETE", name=EDGE_STACK):
    return {
        "StackName": name,
        "StackStatus": stack_status,
        "Outputs": outputs
        if outputs is not None
        else [{"OutputKey": "WebACLArn", "OutputValue": WEB_ACL_ARN}],
        "Parameters": [],
    }


def _app_stack(outputs=None, stack_status="UPDATE_COMPLETE"):
    return _stack(
        outputs=outputs
        if outputs is not None
        else [{"OutputKey": "LambdaFunctionArn", "OutputValue": FUNCTION_ARN}],
        stack_status=stack_status,
        name=APP_STACK,
    )


def _scope_statement(prefixes, encode=False):
    statements = []
    for prefix in prefixes:
        search = prefix
        if encode:
            search = base64.b64encode(prefix.encode("utf-8")).decode("ascii")
        statements.append(
            {
                "ByteMatchStatement": {
                    "FieldToMatch": {"UriPath": {}},
                    "PositionalConstraint": "STARTS_WITH",
                    "SearchString": search,
                    "TextTransformations": [
                        {"Priority": 0, "Type": "NONE"}
                    ],
                }
            }
        )
    return {"OrStatement": {"Statements": statements}}


def _rule(
    action="Block",
    limit=2000,
    window=300,
    scope=("/hplc/api/",),
    encode_scope=False,
    name="EdgeRateLimit",
):
    rule = {
        "Name": name,
        "Action": action,
        "RateBasedStatement": {
            "Limit": limit,
            "EvaluationWindowSec": window,
            "AggregateKeyType": "IP",
            "ScopeDownStatement": _scope_statement(scope, encode_scope),
        },
    }
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


class FakeCloudFormation:
    """Only the read calls the probe is allowed to make."""

    def __init__(self, stack=None, resources=None, error=None):
        self.stack = stack if stack is not None else _stack()
        self.resources = resources if resources is not None else []
        self.error = error
        self.calls = []

    def describe_stacks(self, StackName):
        self.calls.append(("describe_stacks", StackName))
        if self.error is not None:
            raise self.error
        return {"Stacks": [self.stack]}

    def describe_stack_resources(self, StackName):
        self.calls.append(("describe_stack_resources", StackName))
        if self.error is not None:
            raise self.error
        return {"StackResources": list(self.resources)}


class FakeWaf:
    def __init__(self, web_acl=None, error=None):
        self.web_acl = web_acl if web_acl is not None else _web_acl()
        self.error = error
        self.calls = []

    def get_web_acl(self, ARN):
        # The WAFv2 GetWebACL request parameter is ARN (botocore rejects
        # WebACLArn for this operation).
        self.calls.append(("get_web_acl", ARN))
        if self.error is not None:
            raise self.error
        return {"WebACL": self.web_acl}


class FakeCloudWatch:
    """A CloudWatch that replays a scripted value per metric.

    The first read of a counter is the BASELINE (before the burst); the
    reads after it are the AFTER reads.
    """

    def __init__(self, metrics=None, values=None, error=None, namespace=None):
        self.metrics = metrics if metrics is not None else _default_metrics()
        self.values = values if values is not None else {}
        self.error = error
        self.namespace = namespace
        self.list_metrics_calls = []
        self.statistics_calls = []
        self.reads = {}

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
        script = self.values.get(MetricName)
        if script is None:
            return {"Datapoints": []}
        index = self.reads.get(MetricName, 0)
        self.reads[MetricName] = index + 1
        value = script[min(index, len(script) - 1)]
        if value is None:
            return {"Datapoints": []}
        return {"Datapoints": [{"Sum": value, "Timestamp": EndTime}]}


class FakeClients:
    """The injectable client factory, aware of the two regions."""

    def __init__(
        self,
        edge_cfn=None,
        app_cfn=None,
        waf=None,
        edge_cloudwatch=None,
        app_cloudwatch=None,
    ):
        self.edge_cfn = edge_cfn if edge_cfn is not None else FakeCloudFormation()
        self.app_cfn = app_cfn if app_cfn is not None else FakeCloudFormation(
            stack=_app_stack()
        )
        self.waf = waf if waf is not None else FakeWaf()
        self.edge_cloudwatch = (
            edge_cloudwatch
            if edge_cloudwatch is not None
            else FakeCloudWatch()
        )
        self.app_cloudwatch = (
            app_cloudwatch if app_cloudwatch is not None else FakeCloudWatch()
        )
        self.requested = []

    def __call__(self, service, region_name=None):
        self.requested.append((service, region_name))
        if service == "cloudformation":
            if region_name == EDGE_REGION:
                return self.edge_cfn
            return self.app_cfn
        if service == "wafv2":
            return self.waf
        if service == "cloudwatch":
            if region_name == EDGE_REGION:
                return self.edge_cloudwatch
            return self.app_cloudwatch
        raise AssertionError(
            "the probe must not use the service {!r}".format(service)
        )


class ScriptedHttp:
    """Replays a scripted sequence of responses and records the URLs.

    The ordinary site probe is a different URL from the burst, so it is
    answered separately (the site's answer is what the "the site still
    serves 200" gate reads).
    """

    def __init__(self, script=None, default=None, site=None):
        self.script = list(script or [])
        self.default = default if default is not None else (
            200,
            "<html>chemic-ally</html>",
            {"Content-Type": "text/html"},
        )
        self.site = site
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if url == SITE_URL:
            return self.site if self.site is not None else self.default
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self.default


def _answered(count=1, status=200):
    return [(status, "answer", {"Content-Type": "text/plain"})] * count


def _blocked(count=1):
    return [(403, EDGE_BLOCK_BODY, dict(EDGE_BLOCK_HEADERS))] * count


def _origin_refusal(count=1):
    return [(403, MIDDLEWARE_REFUSAL_BODY, {"Content-Type": "text/plain"})] * count


def _waf_values(blocked_after=30.0, allowed_after=130.0):
    return {
        "AllowedRequests": [100.0, allowed_after],
        "CountedRequests": [0.0, 0.0],
        "BlockedRequests": [0.0, blocked_after],
    }


def _lambda_values(baseline=10.0, after=13.0):
    return {"Invocations": [baseline, after]}


def _record_path(case, name="probe-record.json"):
    directory = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, directory, True)
    return os.path.join(directory, name)


def _run(clients=None, http_get=None, **kwargs):
    kwargs.setdefault("client_factory", clients or FakeClients())
    kwargs.setdefault("http_get", http_get or ScriptedHttp())
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("now", lambda: FIXED_NOW)
    kwargs.setdefault("confirm_live", True)
    kwargs.setdefault("requests", 20)
    kwargs.setdefault("rate_per_second", 1000)
    kwargs.setdefault("confirm_blocked_count", 2)
    kwargs.setdefault("metric_wait_seconds", 1)
    kwargs.setdefault("metric_poll_seconds", 1)
    return verify_edge_block.run(**kwargs)


def _happy_clients(http_script=None):
    """The clients of a probe that proves everything it claims."""
    edge_cloudwatch = FakeCloudWatch(values=_waf_values())
    app_cloudwatch = FakeCloudWatch(values=_lambda_values())
    clients = FakeClients(
        edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
    )
    script = http_script
    if script is None:
        script = _answered(3) + _blocked(3)
    return clients, ScriptedHttp(script)


class ProbeHappyPathTests(SimpleTestCase):
    """A Block-mode probe that proves everything passes."""

    def test_a_proof_that_holds_passes(self):
        clients, http = _happy_clients()
        self.assertEqual(_run(clients, http_get=http), 0)

    def test_the_record_carries_the_evidence(self):
        clients, http = _happy_clients()
        record_path = _record_path(self)
        _run(clients, http_get=http, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["action"], "Block")
        self.assertEqual(record["limit"], 2000)
        self.assertEqual(record["evaluation_window_seconds"], 300)
        self.assertEqual(record["requests_sent"], 6)
        self.assertEqual(record["blocked"], 3)
        self.assertEqual(record["answered"], 3)
        self.assertEqual(record["origin_refusal"], 0)
        self.assertEqual(record["transport_error"], 0)
        self.assertEqual(record["first_blocked_index"], 3)
        self.assertEqual(record["waf_blocked_delta"], 30.0)
        self.assertEqual(record["origin_invocations"], 3.0)
        self.assertEqual(record["origin_slack"], 50)
        self.assertEqual(record["site_status"], 200)
        self.assertIsNotNone(record["burst_started_at"])
        self.assertIsNotNone(record["burst_ended_at"])
        self.assertIsNotNone(record["finished_at"])

    def test_the_first_blocked_response_headers_are_recorded(self):
        clients, http = _happy_clients()
        record_path = _record_path(self)
        _run(clients, http_get=http, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(
            record["first_blocked_headers"].get("x-amz-cf-id"), "abc123"
        )

    def test_the_record_is_written_even_on_a_failing_run(self):
        """A failed probe still produces evidence."""
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=FakeCloudWatch(values=_waf_values()),
            app_cloudwatch=FakeCloudWatch(values=_lambda_values()),
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        record_path = _record_path(self)
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["status"], "fail")
        self.assertIn("error", record)
        self.assertEqual(record["action"], "Count")

    def test_the_probe_reads_only_the_services_it_needs(self):
        clients, http = _happy_clients()
        _run(clients, http_get=http)
        self.assertEqual(
            sorted(set(clients.requested)),
            [
                ("cloudformation", EDGE_REGION),
                ("cloudformation", APP_REGION),
                ("cloudwatch", EDGE_REGION),
                ("cloudwatch", APP_REGION),
                ("wafv2", EDGE_REGION),
            ],
        )

    def test_the_waf_dimensions_are_the_discovered_ones(self):
        clients, http = _happy_clients()
        _run(clients, http_get=http)
        for call in clients.edge_cloudwatch.statistics_calls:
            self.assertEqual(call["Dimensions"], DISCOVERED_DIMENSIONS)
            self.assertEqual(call["Namespace"], "AWS/WAFV2")
            self.assertEqual(call["Statistics"], ["Sum"])

    def test_the_origin_counter_is_read_with_the_function_dimension(self):
        clients, http = _happy_clients()
        _run(clients, http_get=http)
        calls = clients.app_cloudwatch.statistics_calls
        self.assertTrue(calls)
        for call in calls:
            self.assertEqual(call["Namespace"], "AWS/Lambda")
            self.assertEqual(call["MetricName"], "Invocations")
            self.assertEqual(
                call["Dimensions"],
                [{"Name": "FunctionName", "Value": FUNCTION_NAME}],
            )

    def test_the_output_names_the_proof(self):
        clients, http = _happy_clients()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _run(clients, http_get=http)
        output = buffer.getvalue()
        self.assertIn("PASS: the live rule action is Block", output)
        self.assertIn("PASS: the edge refused 3 request(s)", output)
        self.assertIn("PASS: the public site still serves 200", output)


class ProbeSafetyGateTests(SimpleTestCase):
    """S1/S2: the bounds that keep the probe safe."""

    def test_without_confirm_live_nothing_is_sent(self):
        clients = FakeClients()
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, confirm_live=False)
        self.assertIn("pass --confirm-live", str(ctx.exception))
        self.assertEqual(http.calls, [])
        self.assertEqual(clients.requested, [])

    def test_the_expensive_solver_path_is_refused(self):
        clients, http = _happy_clients()
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, path="/calculate/equilibria")
        self.assertIn("CPU-bound equilibrium solver", str(ctx.exception))
        self.assertEqual(http.calls, [])

    def test_the_expensive_path_can_be_allowed_explicitly(self):
        clients, http = _happy_clients()
        waf = FakeWaf(
            web_acl=_web_acl(
                rules=[_rule(scope=("/calculate/equilibria",))]
            )
        )
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=FakeCloudWatch(values=_waf_values()),
            app_cloudwatch=FakeCloudWatch(values=_lambda_values()),
        )
        result = _run(
            clients,
            http_get=http,
            path="/calculate/equilibria",
            allow_expensive_path=True,
        )
        self.assertEqual(result, 0)

    def test_the_request_cap_is_never_exceeded(self):
        clients, http = _happy_clients(http_script=_answered(50))
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http, requests=7)
        self.assertEqual(len(http.calls), 7)

    def test_the_burst_is_paced_at_the_requested_rate(self):
        clients, http = _happy_clients(http_script=_answered(50))
        sleeps = []
        with self.assertRaises(RuntimeError):
            _run(
                clients,
                http_get=http,
                requests=5,
                rate_per_second=4,
                sleep=sleeps.append,
            )
        self.assertEqual(len(sleeps), 4)
        for seconds in sleeps:
            self.assertAlmostEqual(seconds, 0.25)

    def test_only_get_requests_are_ever_sent(self):
        """The real transport must issue GETs and nothing else."""
        captured = []

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html"}

            def read(self):
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured.append(request.get_method())
            return FakeResponse()

        with mock.patch.object(
            verify_edge_block.urllib.request, "urlopen", fake_urlopen
        ):
            status, body, headers = verify_edge_block._http_get(
                "https://chemic-ally.xyz/hplc/api/levels/", 10
            )
        self.assertEqual(captured, ["GET"])
        self.assertEqual(status, 200)

    def test_the_real_transport_returns_an_http_error_response(self):
        """A 403 is data, not an exception."""

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {}, None
            )

        with mock.patch.object(
            verify_edge_block.urllib.request, "urlopen", fake_urlopen
        ):
            status, body, headers = verify_edge_block._http_get(
                "https://chemic-ally.xyz/hplc/api/levels/", 10
            )
        self.assertEqual(status, 403)
        self.assertEqual(body, "")


class ProbeScopeTests(SimpleTestCase):
    """G5: the scope check happens BEFORE a single request is sent."""

    def test_a_path_outside_the_scope_sends_nothing(self):
        clients, http = _happy_clients()
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, path="/somewhere/else")
        self.assertIn(
            "the probe path is not in the rule's scope", str(ctx.exception)
        )
        self.assertEqual(http.calls, [])

    def test_the_scope_failure_happens_before_the_burst(self):
        """Not one request is sent -- the assertion is on the fake."""
        clients, http = _happy_clients()
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http, path="/hplc/other")
        self.assertEqual(len(http.calls), 0)

    def test_the_scope_is_read_from_the_live_acl(self):
        clients, http = _happy_clients()
        record_path = _record_path(self)
        _run(clients, http_get=http, record_out=record_path)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["scope"], ["/hplc/api/"])

    def test_a_base64_encoded_search_string_is_understood(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(encode_scope=True)]))
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=FakeCloudWatch(values=_waf_values()),
            app_cloudwatch=FakeCloudWatch(values=_lambda_values()),
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        self.assertEqual(_run(clients, http_get=http), 0)

    def test_a_rule_without_a_scope_down_statement_is_refused(self):
        rule = _rule()
        rule["RateBasedStatement"].pop("ScopeDownStatement")
        waf = FakeWaf(web_acl=_web_acl(rules=[rule]))
        clients = FakeClients(waf=waf)
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("no UriPath scope-down statement", str(ctx.exception))
        self.assertEqual(http.calls, [])


class ProbeClassificationTests(SimpleTestCase):
    """Every response lands in EXACTLY one of the four kinds."""

    def test_the_four_kinds_are_the_documented_ones(self):
        self.assertEqual(
            set(verify_edge_block.KINDS),
            {"blocked", "origin_refusal", "answered", "transport_error"},
        )

    def test_the_four_kinds_are_distinct(self):
        self.assertEqual(
            verify_edge_block._classify(403, EDGE_BLOCK_BODY), "blocked"
        )
        self.assertEqual(
            verify_edge_block._classify(403, MIDDLEWARE_REFUSAL_BODY),
            "origin_refusal",
        )
        self.assertEqual(verify_edge_block._classify(200, "ok"), "answered")
        self.assertEqual(verify_edge_block._classify(503, "busy"), "answered")
        self.assertEqual(verify_edge_block._classify(429, "slow"), "answered")

    def test_an_origin_refusal_is_never_counted_as_an_edge_block(self):
        clients, http = _happy_clients(
            http_script=_answered(3) + _origin_refusal(3)
        )
        record_path = _record_path(self)
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, record_out=record_path)
        self.assertIn(
            "carried the origin guard's refusal body", str(ctx.exception)
        )
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["origin_refusal"], 3)
        self.assertEqual(record["blocked"], 0)

    def test_a_transport_error_is_data_not_a_crash(self):
        script = [RuntimeError("connection reset")] + _answered(2) + _blocked(3)
        clients, http = _happy_clients(http_script=script)
        record_path = _record_path(self)
        self.assertEqual(_run(clients, http_get=http, record_out=record_path), 0)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["transport_error"], 1)
        self.assertEqual(record["answered"], 2)
        self.assertEqual(record["blocked"], 3)

    def test_a_503_is_classified_as_answered(self):
        """The simulator database may be down; that is still the origin."""
        clients, http = _happy_clients(
            http_script=_answered(3, status=503) + _blocked(3)
        )
        record_path = _record_path(self)
        self.assertEqual(_run(clients, http_get=http, record_out=record_path), 0)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["answered"], 3)
        self.assertEqual(record["blocked"], 3)
        self.assertEqual(record["statuses"].get("503"), 3)


class ProbeBurstEvidenceTests(SimpleTestCase):
    """G6: the burst proves that the origin answered FIRST."""

    def test_a_sequence_that_answers_first_and_blocks_later_passes(self):
        clients, http = _happy_clients(
            http_script=_answered(3) + _blocked(3)
        )
        self.assertEqual(_run(clients, http_get=http), 0)

    def test_a_sequence_that_starts_with_a_block_fails(self):
        clients, http = _happy_clients(http_script=_blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn(
            "the source address was already over the ceiling when the probe "
            "started",
            str(ctx.exception),
        )
        self.assertIn("the evaluation window is 300 seconds", str(ctx.exception))
        self.assertEqual(len(http.calls), 1)

    def test_the_burst_that_never_trips_the_limit_fails(self):
        clients, http = _happy_clients(http_script=_answered(20))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, requests=20)
        self.assertIn("the limit never tripped", str(ctx.exception))

    def test_a_single_lucky_block_is_not_a_proof(self):
        clients, http = _happy_clients(http_script=_answered(3) + _blocked(1))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, confirm_blocked_count=5)
        self.assertIn("a single lucky response is not a proof", str(
            ctx.exception
        ))

    def test_the_burst_stops_as_soon_as_the_block_is_confirmed(self):
        clients, http = _happy_clients(
            http_script=_answered(3) + _blocked(20)
        )
        _run(clients, http_get=http, confirm_blocked_count=2)
        # 3 answered + 3 blocked (the first one plus two more).
        burst_calls = [call for call in http.calls if "canary=" in call[0]]
        self.assertEqual(len(burst_calls), 6)
        self.assertEqual(http.calls[-1][0], SITE_URL)


class ProbeMetricGateTests(SimpleTestCase):
    """G7/G8: the counters must agree with what the probe observed."""

    def test_a_waf_counter_that_did_not_move_fails(self):
        edge_cloudwatch = FakeCloudWatch(
            values=_waf_values(blocked_after=0.0)
        )
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("counter did not move", str(ctx.exception))

    def test_a_waf_counter_that_moved_passes(self):
        edge_cloudwatch = FakeCloudWatch(
            values=_waf_values(blocked_after=30.0)
        )
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        self.assertEqual(_run(clients, http_get=http), 0)

    def test_the_blocked_delta_is_compared_with_the_observed_blocks(self):
        """A different delta is information, not a failure."""
        edge_cloudwatch = FakeCloudWatch(
            values=_waf_values(blocked_after=99.0)
        )
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(_run(clients, http_get=http), 0)
        self.assertIn("other traffic that was refused too", buffer.getvalue())

    def test_an_origin_counter_beyond_the_slack_fails(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(
            values=_lambda_values(baseline=10.0, after=1010.0)
        )
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("DID reach the origin", str(ctx.exception))

    def test_an_unreadable_origin_counter_fails(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(error=RuntimeError("AWS is down"))
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("could not be read", str(ctx.exception))
        self.assertIn("is not a check that passed", str(ctx.exception))

    def test_an_unreadable_origin_counter_may_be_accepted_as_unavailable(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(error=RuntimeError("AWS is down"))
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        record_path = _record_path(self)
        result = _run(
            clients,
            http_get=http,
            record_out=record_path,
            allow_missing_origin_metric=True,
        )
        self.assertEqual(result, 0)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["origin_metric_status"], "unavailable")
        self.assertIsNone(record["origin_invocations"])

    def test_an_unpublished_origin_counter_is_unavailable_not_a_pass(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(values={"Invocations": [10.0, None]})
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("has not been published", str(ctx.exception))

    def test_an_unpublished_origin_counter_may_be_accepted(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(values={"Invocations": [10.0, None]})
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch, app_cloudwatch=app_cloudwatch
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        record_path = _record_path(self)
        result = _run(
            clients,
            http_get=http,
            record_out=record_path,
            allow_missing_origin_metric=True,
        )
        self.assertEqual(result, 0)
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["origin_metric_status"], "unavailable")

    def test_the_function_name_falls_back_to_the_stack_resources(self):
        edge_cloudwatch = FakeCloudWatch(values=_waf_values())
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        app_cfn = FakeCloudFormation(
            stack=_app_stack(outputs=[]),
            resources=[
                {
                    "ResourceType": "AWS::Lambda::Function",
                    "PhysicalResourceId": FUNCTION_NAME,
                }
            ],
        )
        clients = FakeClients(
            edge_cloudwatch=edge_cloudwatch,
            app_cloudwatch=app_cloudwatch,
            app_cfn=app_cfn,
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        self.assertEqual(_run(clients, http_get=http), 0)
        self.assertIn(
            ("describe_stack_resources", APP_STACK), app_cfn.calls
        )


class ProbeSiteGateTests(SimpleTestCase):
    """G9: the ordinary site must still serve 200 after the burst."""

    def test_a_site_that_stops_serving_200_fails(self):
        clients, http = _happy_clients()
        http.site = (503, "down", {})
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn("instead of 200", str(ctx.exception))

    def test_the_site_is_probed_after_the_burst(self):
        clients, http = _happy_clients()
        _run(clients, http_get=http)
        self.assertEqual(http.calls[-1][0], SITE_URL)


class ProbeLiveActionGateTests(SimpleTestCase):
    """G3: the live action must be the one the probe is allowed to prove."""

    def test_a_rule_that_is_not_blocking_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        clients = FakeClients(waf=waf)
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http)
        self.assertIn(
            "the rule is not blocking; the probe cannot prove a block that "
            "is not configured",
            str(ctx.exception),
        )
        self.assertEqual(http.calls, [])

    def test_the_action_is_read_from_the_live_acl_not_the_stack(self):
        stack = _stack()
        stack["Parameters"] = [
            {"ParameterKey": "WafAction", "ParameterValue": "Count"}
        ]
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Block")]))
        clients = FakeClients(
            edge_cfn=FakeCloudFormation(stack=stack),
            waf=waf,
            edge_cloudwatch=FakeCloudWatch(values=_waf_values()),
            app_cloudwatch=FakeCloudWatch(values=_lambda_values()),
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        self.assertEqual(_run(clients, http_get=http), 0)

    def test_a_web_acl_without_the_rule_is_refused(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(name="Other")]))
        clients = FakeClients(waf=waf)
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http)
        self.assertEqual(http.calls, [])

    def test_an_unhealthy_edge_stack_is_refused(self):
        clients = FakeClients(
            edge_cfn=FakeCloudFormation(
                stack=_stack(stack_status="ROLLBACK_COMPLETE")
            )
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http)
        self.assertEqual(http.calls, [])

    def test_a_missing_web_acl_arn_output_is_refused(self):
        clients = FakeClients(
            edge_cfn=FakeCloudFormation(stack=_stack(outputs=[]))
        )
        http = ScriptedHttp(_answered(3) + _blocked(3))
        with self.assertRaises(RuntimeError):
            _run(clients, http_get=http)
        self.assertEqual(http.calls, [])


class ProbeNoBlockModeTests(SimpleTestCase):
    """The Count-mode dry run (--expect-no-block)."""

    def test_a_count_rule_that_blocks_nothing_passes(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        edge_cloudwatch = FakeCloudWatch(values=_waf_values(blocked_after=0.0))
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=edge_cloudwatch,
            app_cloudwatch=app_cloudwatch,
        )
        http = ScriptedHttp(_answered(10))
        self.assertEqual(
            _run(clients, http_get=http, expect_no_block=True), 0
        )

    def test_a_count_rule_that_blocks_something_fails(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        edge_cloudwatch = FakeCloudWatch(values=_waf_values(blocked_after=0.0))
        app_cloudwatch = FakeCloudWatch(values=_lambda_values())
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=edge_cloudwatch,
            app_cloudwatch=app_cloudwatch,
        )
        http = ScriptedHttp(_answered(3) + _blocked(1))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, expect_no_block=True)
        self.assertIn("a Count rule cannot refuse", str(ctx.exception))

    def test_the_dry_run_refuses_a_blocking_rule(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Block")]))
        clients = FakeClients(waf=waf)
        http = ScriptedHttp(_answered(10))
        with self.assertRaises(RuntimeError) as ctx:
            _run(clients, http_get=http, expect_no_block=True)
        self.assertIn("--expect-no-block was requested", str(ctx.exception))
        self.assertEqual(http.calls, [])

    def test_in_count_mode_extra_origin_traffic_is_reported_not_failed(self):
        """Nothing was refused at the edge, so extra invocations are not
        an edge failure."""
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        edge_cloudwatch = FakeCloudWatch(values=_waf_values(blocked_after=0.0))
        app_cloudwatch = FakeCloudWatch(
            values=_lambda_values(baseline=10.0, after=1010.0)
        )
        clients = FakeClients(
            waf=waf,
            edge_cloudwatch=edge_cloudwatch,
            app_cloudwatch=app_cloudwatch,
        )
        http = ScriptedHttp(_answered(10))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = _run(clients, http_get=http, expect_no_block=True)
        self.assertEqual(result, 0)
        self.assertIn("NOT treated as an edge failure", buffer.getvalue())


class ProbeRefusalBodyTests(SimpleTestCase):
    """The probe cannot drift from the middleware it witnesses."""

    def test_the_refusal_body_matches_the_middleware(self):
        self.assertEqual(
            verify_edge_block.REFUSAL_BODY, MIDDLEWARE_REFUSAL_BODY
        )

    def test_the_refusal_body_is_the_documented_one(self):
        self.assertEqual(
            verify_edge_block.REFUSAL_BODY,
            "Forbidden: request did not arrive through the CloudFront origin.",
        )


class ProbeCliTests(SimpleTestCase):
    """The CLI wiring: the flags exist and the defaults are the safe ones."""

    def test_the_defaults_are_the_safe_ones(self):
        self.assertEqual(verify_edge_block.DEFAULT_PATH, "/hplc/api/levels/")
        self.assertEqual(verify_edge_block.DEFAULT_REQUESTS, 2600)
        self.assertEqual(verify_edge_block.DEFAULT_RATE_PER_SECOND, 12)
        self.assertEqual(verify_edge_block.DEFAULT_TIMEOUT_SECONDS, 10)
        self.assertEqual(verify_edge_block.DEFAULT_METRIC_WAIT_SECONDS, 180)
        self.assertEqual(verify_edge_block.METRIC_POLL_SECONDS, 15)
        self.assertEqual(verify_edge_block.DEFAULT_SITE_URL, SITE_URL)
        self.assertEqual(verify_edge_block.RULE_NAME, "EdgeRateLimit")
        self.assertEqual(verify_edge_block.WAF_METRIC_NAMESPACE, "AWS/WAFV2")
        self.assertEqual(verify_edge_block.LAMBDA_METRIC_NAMESPACE, "AWS/Lambda")
        self.assertEqual(verify_edge_block.LAMBDA_METRIC_NAME, "Invocations")

    def test_the_cli_refuses_without_confirm_live(self):
        code = verify_edge_block.main(
            ["--edge-stack-name", EDGE_STACK],
            client_factory=FakeClients(),
            http_get=ScriptedHttp(),
            sleep=lambda seconds: None,
            now=lambda: FIXED_NOW,
        )
        self.assertEqual(code, 1)

    def test_the_cli_reports_a_failure_with_exit_code_one(self):
        waf = FakeWaf(web_acl=_web_acl(rules=[_rule(action="Count")]))
        clients = FakeClients(waf=waf)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = verify_edge_block.main(
                ["--confirm-live"],
                client_factory=clients,
                http_get=ScriptedHttp(),
                sleep=lambda seconds: None,
                now=lambda: FIXED_NOW,
            )
        self.assertEqual(code, 1)
        self.assertIn("FAIL:", buffer.getvalue())
