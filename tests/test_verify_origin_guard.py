"""Unit tests for the live origin-guard witness (scripts/verify_origin_guard.py).

M007/S04: the origin guard's rollout switch exists, but nobody can flip it
safely. There must be an executable witness that says "the origin header is
live and settled, the guard MAY now enforce" BEFORE the flip, and "the guard
is enforcing, a direct call is refused with the fixed body, the refusal was
counted, and the public site still serves" AFTER it.

The failure that matters is CloudFront not stamping the origin header on
every edge while the Lambda already refuses header-less requests: the whole
site goes down.

Each test pins one gate of the witness:

* header-live on a healthy distribution -> 0;
* the header missing from the LIVE origin -> non-zero;
* the header present on a DIFFERENT origin -> non-zero;
* an empty header value -> non-zero;
* a distribution changed too recently to have propagated -> non-zero;
* guard-inert (phase 1) -> 0, and non-zero when the guard is secretly
  enforcing or a direct call is refused;
* guard-enforced (phase 2) -> 0, and non-zero when the direct call is not
  refused, the body differs, the site is not 200, the metric does not move,
  the alarm is missing or the secret does not match;
* auto with a contradicting --expect-enforcement -> non-zero;
* the script's constants are byte-identical to the middleware's;
* the secret NEVER appears in the witness's output;
* an unhealthy stack and a missing stack output -> non-zero.

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` and all HTTP through an injected ``http_get``:
no test ever touches the network.
"""

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from chemically.metrics import METRIC_NAMESPACE, REFUSAL_METRIC_NAME
from chemically.middleware import DEFAULT_ORIGIN_HEADER_NAME, REFUSAL_BODY

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_origin_guard.py"

_spec = importlib.util.spec_from_file_location(
    "verify_origin_guard", MODULE_PATH
)
verify_origin_guard = importlib.util.module_from_spec(_spec)
sys.modules["verify_origin_guard"] = verify_origin_guard
_spec.loader.exec_module(verify_origin_guard)

STACK_NAME = "chemically"
REGION = "us-east-2"
NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)

HEADER_NAME = "X-ChemicAlly-Origin"
SECRET = "origin-secret-fixture-value-never-committed"
FUNCTION_HOST = "abcdefghij.lambda-url.us-east-2.on.aws"
FUNCTION_URL = "https://{}/".format(FUNCTION_HOST)
SITE_URL = "https://chemic-ally.xyz"
DISTRIBUTION_ID = "E1EXAMPLE"
DISTRIBUTION_DOMAIN = "d111111abcdef8.cloudfront.net"
DISTRIBUTION_ARN = (
    "arn:aws:cloudfront::768125641662:distribution/{}".format(DISTRIBUTION_ID)
)
FUNCTION_ARN = (
    "arn:aws:lambda:us-east-2:768125641662:function:chemically-DjangoFunction"
)
ALARM_NAME = "chemically-origin-guard-refusals"

DEFAULT_OUTPUTS = {
    "DistributionArn": DISTRIBUTION_ARN,
    "CloudFrontDomain": DISTRIBUTION_DOMAIN,
    "FunctionUrl": FUNCTION_URL,
    "LambdaFunctionArn": FUNCTION_ARN,
}


def _outputs(overrides=None):
    outputs = dict(DEFAULT_OUTPUTS)
    if overrides is not None:
        outputs = overrides
    return [
        {"OutputKey": key, "OutputValue": value}
        for key, value in outputs.items()
    ]


def _outputs_without(key):
    return _outputs(
        {k: v for k, v in DEFAULT_OUTPUTS.items() if k != key}
    )


def _stack(outputs=None, parameters=None, status="UPDATE_COMPLETE", resources=None):
    return {
        "StackName": STACK_NAME,
        "StackStatus": status,
        "Outputs": _outputs() if outputs is None else outputs,
        "Parameters": (
            [{"ParameterKey": "OriginHeaderName", "ParameterValue": HEADER_NAME}]
            if parameters is None
            else parameters
        ),
        "Resources": [] if resources is None else resources,
    }


def _origin(domain=FUNCTION_HOST, headers=None):
    if headers is None:
        headers = [
            {"HeaderName": HEADER_NAME, "HeaderValue": SECRET},
        ]
    return {
        "Id": "DjangoLambda",
        "DomainName": domain,
        "CustomHeaders": {"Quantity": len(headers), "Items": headers},
    }


def _distribution(origins=None, last_modified=None, distribution_id=None):
    if origins is None:
        origins = [_origin()]
    if last_modified is None:
        last_modified = NOW - timedelta(hours=2)
    # The real GetDistribution reply carries the origins under
    # DistributionConfig, not at the top level. The fake mirrors that
    # shape, so the witness cannot pass by reading the wrong level.
    return {
        "Id": DISTRIBUTION_ID if distribution_id is None else distribution_id,
        "ARN": DISTRIBUTION_ARN,
        "DomainName": DISTRIBUTION_DOMAIN,
        "LastModifiedTime": last_modified,
        "DistributionConfig": {
            "Comment": "ChemicAlly public distribution",
            "Origins": {"Items": origins},
        },
    }


def _environment(secret=SECRET, header_name=HEADER_NAME):
    env = {"ORIGIN_HEADER_NAME": header_name}
    if secret is not None:
        env["ORIGIN_HEADER_SECRET"] = secret
    return env


class FakeClients:
    """Fake client factory covering the four services the witness reads."""

    def __init__(
        self,
        stack=None,
        distribution=None,
        distributions=None,
        environment=None,
        metric_sums=None,
        alarms=None,
        lambda_error=None,
    ):
        self.stack = _stack() if stack is None else stack
        self.distribution = (
            _distribution() if distribution is None else distribution
        )
        self.distributions = distributions
        self.environment = (
            _environment() if environment is None else environment
        )
        self.metric_sums = metric_sums
        self.alarms = alarms
        self.lambda_error = lambda_error
        self.requested = []
        self.metric_calls = 0

    def __call__(self, service, region_name=None):
        self.requested.append((service, region_name))
        if service == "cloudformation":
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [self.stack]},
                describe_stack_resources=lambda StackName: {
                    "StackResources": self.stack.get("Resources", [])
                },
            )
        if service == "cloudfront":
            return SimpleNamespace(
                get_distribution=lambda Id: {
                    "Distribution": self.distribution
                },
                list_distributions=lambda **kwargs: {
                    "DistributionList": {
                        "Items": self.distributions
                        if self.distributions is not None
                        else [self.distribution],
                        "IsTruncated": False,
                    }
                },
            )
        if service == "lambda":
            if self.lambda_error is not None:
                raise self.lambda_error
            return SimpleNamespace(
                get_function_configuration=lambda FunctionName: {
                    "FunctionName": FunctionName,
                    "Environment": {"Variables": self.environment},
                }
            )
        if service == "cloudwatch":
            return SimpleNamespace(
                get_metric_statistics=self._get_metric_statistics,
                describe_alarms=self._describe_alarms,
            )
        raise AssertionError("unexpected service requested: {}".format(service))

    def _get_metric_statistics(self, **kwargs):
        self.metric_calls += 1
        if self.metric_sums is None:
            total = 0.0
        elif isinstance(self.metric_sums, (int, float)):
            total = self.metric_sums
        else:
            index = min(self.metric_calls - 1, len(self.metric_sums) - 1)
            total = self.metric_sums[index]
        return {"Datapoints": [{"Sum": total, "Unit": "Count"}]}

    def _describe_alarms(self, AlarmNames=None):
        if self.alarms is None:
            alarms = [
                {
                    "AlarmName": ALARM_NAME,
                    "StateValue": "OK",
                    "MetricName": "OriginGuardRefusals",
                }
            ]
        else:
            alarms = self.alarms
        if AlarmNames:
            wanted = set(AlarmNames)
            alarms = [a for a in alarms if a.get("AlarmName") in wanted]
        return {"MetricAlarms": alarms}


class FakeHttp:
    """Fake HTTP probe: scripted answers per URL, recorded calls."""

    def __init__(self, responses=None):
        self.responses = responses if responses is not None else {}
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError("unexpected HTTP probe of {}".format(url))
        answer = self.responses[url]
        if callable(answer):
            answer = answer(url)
        return answer


def _cli(clients, http=None, argv=None, sleep=None, now=None):
    """Run the witness through its public CLI and return its exit code."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = verify_origin_guard.main(
            argv if argv is not None else ["--expect", "header-live"],
            client_factory=clients,
            http_get=http if http is not None else FakeHttp(),
            sleep=sleep if sleep is not None else (lambda seconds: None),
            now=now if now is not None else (lambda: NOW),
        )
    return code, buffer.getvalue()


class HeaderLiveTests(SimpleTestCase):
    """The pre-deploy preflight: the header must be live AND settled."""

    def test_healthy_distribution_passes(self):
        code, _ = _cli(FakeClients())
        self.assertEqual(code, 0)

    def test_header_missing_from_the_live_origin_fails(self):
        distribution = _distribution(
            origins=[_origin(headers=[])]
        )
        code, _ = _cli(FakeClients(distribution=distribution))
        self.assertNotEqual(code, 0)

    def test_header_on_a_different_origin_fails(self):
        distribution = _distribution(
            origins=[
                _origin(
                    domain="other-origin.example.com",
                    headers=[
                        {"HeaderName": HEADER_NAME, "HeaderValue": SECRET},
                    ],
                ),
                _origin(headers=[]),
            ]
        )
        code, _ = _cli(FakeClients(distribution=distribution))
        self.assertNotEqual(code, 0)

    def test_empty_header_value_fails(self):
        distribution = _distribution(
            origins=[
                _origin(
                    headers=[
                        {"HeaderName": HEADER_NAME, "HeaderValue": ""},
                    ]
                )
            ]
        )
        code, _ = _cli(FakeClients(distribution=distribution))
        self.assertNotEqual(code, 0)

    def test_distribution_changed_too_recently_fails(self):
        distribution = _distribution(
            last_modified=NOW - timedelta(minutes=2)
        )
        code, _ = _cli(FakeClients(distribution=distribution))
        self.assertNotEqual(code, 0)

    def test_a_settled_distribution_passes(self):
        distribution = _distribution(
            last_modified=NOW - timedelta(minutes=30)
        )
        code, _ = _cli(FakeClients(distribution=distribution))
        self.assertEqual(code, 0)


def _inert_http(direct_status=200, direct_body="hello", site_status=200):
    """HTTP answers for the phase-1 contract: nothing is refused."""
    return FakeHttp(
        {
            FUNCTION_URL: (direct_status, direct_body),
            SITE_URL: (site_status, "home page"),
        }
    )


class GuardInertTests(SimpleTestCase):
    """Phase 1: the header is live, the guard cannot refuse anything."""

    def test_inert_phase_passes(self):
        clients = FakeClients(environment=_environment(secret=None))
        http = _inert_http()
        code, _ = _cli(
            clients,
            http,
            argv=["--expect", "guard-inert"],
        )
        self.assertEqual(code, 0)
        self.assertIn(FUNCTION_URL, http.calls)
        self.assertIn(SITE_URL, http.calls)

    def test_a_secret_in_the_lambda_fails(self):
        """The live phase is not the declared phase: the guard would refuse."""
        clients = FakeClients(environment=_environment(secret=SECRET))
        code, _ = _cli(
            clients,
            _inert_http(),
            argv=["--expect", "guard-inert"],
        )
        self.assertNotEqual(code, 0)

    def test_a_refused_direct_call_fails(self):
        """The guard refuses although the switch is off."""
        clients = FakeClients(environment=_environment(secret=None))
        http = _inert_http(direct_status=403, direct_body="refused")
        code, _ = _cli(
            clients,
            http,
            argv=["--expect", "guard-inert"],
        )
        self.assertNotEqual(code, 0)


def _enforced_http(direct_status=403, direct_body=REFUSAL_BODY, site_status=200):
    """HTTP answers for the phase-2 contract: the direct call is refused."""
    return FakeHttp(
        {
            FUNCTION_URL: (direct_status, direct_body),
            SITE_URL: (site_status, "home page"),
        }
    )


class GuardEnforcedTests(SimpleTestCase):
    """Phase 2: the guard refuses, counts, and the site still serves."""

    def _clients(self, metric_sums=(0.0, 1.0), alarms=None, environment=None):
        return FakeClients(
            environment=(
                _environment() if environment is None else environment
            ),
            metric_sums=list(metric_sums),
            alarms=alarms,
        )

    def test_enforced_phase_passes(self):
        code, _ = _cli(
            self._clients(),
            _enforced_http(),
            argv=["--expect", "guard-enforced"],
        )
        self.assertEqual(code, 0)

    def test_a_direct_call_that_is_not_refused_fails(self):
        http = _enforced_http(direct_status=200, direct_body="hello")
        code, _ = _cli(
            self._clients(),
            http,
            argv=["--expect", "guard-enforced"],
        )
        self.assertNotEqual(code, 0)

    def test_a_different_refusal_body_fails(self):
        http = _enforced_http(direct_body="Forbidden: nope.")
        code, _ = _cli(
            self._clients(),
            http,
            argv=["--expect", "guard-enforced"],
        )
        self.assertNotEqual(code, 0)

    def test_a_site_that_is_not_200_fails(self):
        http = _enforced_http(site_status=503)
        code, _ = _cli(
            self._clients(),
            http,
            argv=["--expect", "guard-enforced"],
        )
        self.assertNotEqual(code, 0)

    def test_a_metric_that_does_not_move_fails(self):
        clients = self._clients(metric_sums=(7.0, 7.0))
        code, _ = _cli(
            clients,
            _enforced_http(),
            argv=["--expect", "guard-enforced", "--metric-wait-seconds", "30"],
        )
        self.assertNotEqual(code, 0)

    def test_a_missing_alarm_fails(self):
        clients = self._clients(alarms=[])
        code, _ = _cli(
            clients,
            _enforced_http(),
            argv=["--expect", "guard-enforced"],
        )
        self.assertNotEqual(code, 0)

    def test_a_secret_that_differs_from_the_stamped_value_fails(self):
        clients = self._clients(
            environment=_environment(secret="a-different-secret")
        )
        code, _ = _cli(
            clients,
            _enforced_http(),
            argv=["--expect", "guard-enforced"],
        )
        self.assertNotEqual(code, 0)


class AutoPhaseTests(SimpleTestCase):
    """auto detects the live phase and holds the deploy to its word."""

    def test_auto_detects_the_enforced_phase(self):
        clients = FakeClients(metric_sums=[0.0, 1.0])
        http = _enforced_http()
        code, _ = _cli(
            clients,
            http,
            argv=["--expect", "auto", "--expect-enforcement", "true"],
        )
        self.assertEqual(code, 0)
        self.assertIn(FUNCTION_URL, http.calls)

    def test_auto_detects_the_inert_phase(self):
        clients = FakeClients(environment=_environment(secret=None))
        http = _inert_http()
        code, _ = _cli(
            clients,
            http,
            argv=["--expect", "auto", "--expect-enforcement", "false"],
        )
        self.assertEqual(code, 0)

    def test_auto_contradicting_the_detected_phase_fails(self):
        """A deploy that did not take effect must not pass."""
        clients = FakeClients(metric_sums=[0.0, 1.0])
        http = FakeHttp()
        code, _ = _cli(
            clients,
            http,
            argv=["--expect", "auto", "--expect-enforcement", "false"],
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(http.calls, [])

    def test_auto_contradicting_the_detected_inert_phase_fails(self):
        clients = FakeClients(environment=_environment(secret=None))
        code, _ = _cli(
            clients,
            FakeHttp(),
            argv=["--expect", "auto", "--expect-enforcement", "true"],
        )
        self.assertNotEqual(code, 0)


class ConstantsAndSecretTests(SimpleTestCase):
    """The witness must not drift from the middleware, nor leak the secret."""

    def test_the_constants_match_the_middleware(self):
        self.assertEqual(verify_origin_guard.REFUSAL_BODY, REFUSAL_BODY)
        self.assertEqual(
            verify_origin_guard.DEFAULT_ORIGIN_HEADER_NAME,
            DEFAULT_ORIGIN_HEADER_NAME,
        )

    def test_the_metric_constants_match_the_metrics_module(self):
        """The witness must poll the metric the application publishes."""
        self.assertEqual(verify_origin_guard.METRIC_NAMESPACE, METRIC_NAMESPACE)
        self.assertEqual(
            verify_origin_guard.REFUSAL_METRIC_NAME, REFUSAL_METRIC_NAME
        )

    def test_the_secret_is_never_printed(self):
        clients = FakeClients(metric_sums=[0.0, 1.0])
        code, output = _cli(
            clients,
            _enforced_http(),
            argv=["--expect", "guard-enforced"],
        )
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, output)
        self.assertIn(
            "the Lambda's secret matches the distribution's stamped origin "
            "header",
            output,
        )

    def test_a_lambda_header_name_that_disagrees_fails(self):
        clients = FakeClients(
            environment=_environment(header_name="X-Other-Origin")
        )
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)


class StackContractTests(SimpleTestCase):
    """An unhealthy stack or a missing output is a loud failure."""

    def test_an_unhealthy_stack_fails(self):
        clients = FakeClients(stack=_stack(status="ROLLBACK_COMPLETE"))
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)

    def test_an_in_progress_stack_fails(self):
        clients = FakeClients(stack=_stack(status="UPDATE_IN_PROGRESS"))
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)

    def test_a_missing_function_url_output_fails(self):
        clients = FakeClients(stack=_stack(outputs=_outputs_without("FunctionUrl")))
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)

    def test_a_missing_distribution_arn_output_fails(self):
        clients = FakeClients(
            stack=_stack(outputs=_outputs_without("DistributionArn"))
        )
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)

    def test_a_missing_cloudfront_domain_output_fails(self):
        clients = FakeClients(
            stack=_stack(outputs=_outputs_without("CloudFrontDomain"))
        )
        code, _ = _cli(clients, FakeHttp())
        self.assertNotEqual(code, 0)

    def test_the_alarm_is_resolved_from_the_stack_resources(self):
        stack = _stack(
            resources=[
                {
                    "LogicalResourceId": "OriginGuardRefusalAlarm",
                    "ResourceType": "AWS::CloudWatch::Alarm",
                    "PhysicalResourceId": "custom-alarm-name",
                }
            ]
        )
        clients = FakeClients(
            stack=stack,
            metric_sums=[0.0, 1.0],
            alarms=[{"AlarmName": "custom-alarm-name", "StateValue": "OK"}],
        )
        code, _ = _cli(
            clients,
            _enforced_http(),
            argv=["--expect", "guard-enforced"],
        )
        self.assertEqual(code, 0)

    def test_an_aws_error_is_not_mistaken_for_a_green_check(self):
        class FakeAwsError(Exception):
            pass

        clients = FakeClients(lambda_error=FakeAwsError("throttled"))
        with self.assertRaises(FakeAwsError):
            _cli(clients, FakeHttp())
