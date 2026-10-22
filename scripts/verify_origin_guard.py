#!/usr/bin/env python3
"""Live witness for the origin guard's two-phase rollout (M007/S04).

The origin guard's rollout switch exists (M007/S03): one workflow setting
(``ENFORCE_ORIGIN_GUARD``) decides whether the Lambda carries the shared
origin secret. Phase 1 (the default) ships the CloudFront origin header and
leaves the guard INERT; phase 2 hands the secret to the Lambda, and every
request that did not come through CloudFront is refused with a fixed 403.

Flipping that switch is only safe when the origin header is ALREADY live on
the origin that actually serves the Function URL and has had time to reach
every CloudFront edge. The one failure that matters: CloudFront does not
stamp the header on every edge while the Lambda already refuses header-less
requests -- the whole site goes down.

So the rollout must be OBSERVABLE, not declared. This script is the witness:

* ``header-live`` -- the PRE-DEPLOY preflight. The origin header is stamped
  on the LIVE origin with a non-empty value, the header name agrees across
  the stack parameter, the Lambda environment and the distribution, the
  distribution has not changed for at least ``--min-header-age-minutes``
  (CloudFront propagates a configuration change to all edge locations in
  "a few minutes", so a distribution that just changed has not settled), and
  a Lambda that already carries the secret matches the stamped value.
* ``guard-inert`` -- phase 1, AFTER the deploy. Everything above, plus: the
  Lambda carries NO secret, a direct Function-URL call is NOT refused, and
  the public site serves 200.
* ``guard-enforced`` -- phase 2, AFTER the deploy. Everything above, plus:
  the Lambda carries the secret, a direct Function-URL call is refused with
  status 403 AND the exact fixed body, the public site still serves 200 (no
  legitimate request was refused), the refusal metric increased by at least
  one after that refused call, and the refusal alarm exists.
* ``auto`` -- detect the live phase and run that phase's contract. With
  ``--expect-enforcement`` the detected phase must equal the declared switch
  value: a deploy that did not take effect must NOT pass.

Everything here is read-only. It calls ``cloudformation:DescribeStacks``,
``cloudformation:DescribeStackResources``, ``cloudfront:GetDistribution``,
``cloudfront:ListDistributions``, ``lambda:GetFunctionConfiguration`` and
``cloudwatch:GetMetricStatistics`` / ``cloudwatch:DescribeAlarms``, and it
issues two HTTP probes (the public site, and the Function URL directly).
Nothing is created, changed or deleted.

THE SECRET IS NEVER PRINTED. The origin secret is compared in memory and
only booleans are reported. It is never logged, written to a file, or
included in an exception message.

Exit codes: 0 = every check passes, 1 = one or more checks failed. AWS API
failures bubble as raised exceptions so a throttling error or a lost
connection is never mistaken for a green check.

``run``/``main`` take injectable ``client_factory``, ``http_get``, ``sleep``
and ``now`` so the unit tests exercise the full control flow with fake
clients instead of reaching AWS.
"""

import argparse
import hmac
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

# CloudFront is a global service; its API endpoint lives in us-east-1. The
# client is therefore always created there, whatever region the stack is in.
CLOUDFRONT_REGION = "us-east-1"

#: The CloudWatch namespace and metric the guard publishes refusals into
#: (chemically.metrics.METRIC_NAMESPACE / REFUSAL_METRIC_NAME).
METRIC_NAMESPACE = "ChemicAlly"
REFUSAL_METRIC_NAME = "OriginGuardRefusals"

#: The header CloudFront stamps on every origin request. MUST stay
#: byte-identical to ``chemically.middleware.DEFAULT_ORIGIN_HEADER_NAME`` --
#: a test asserts it, so this script cannot drift from the middleware.
DEFAULT_ORIGIN_HEADER_NAME = "X-ChemicAlly-Origin"

#: The body of the refusal. MUST stay byte-identical to
#: ``chemically.middleware.REFUSAL_BODY`` -- a test asserts it. The body
#: deliberately carries no detail about the expected header value.
REFUSAL_BODY = (
    "Forbidden: request did not arrive through the CloudFront origin."
)

#: The alarm the stack creates for the refusal metric (template.yaml:
#: ``${AWS::StackName}-origin-guard-refusals``). It is resolved from the
#: stack's resources when possible, and falls back to this conventional name.
ALARM_NAME_SUFFIX = "-origin-guard-refusals"

#: CloudWatch query window and polling for the refusal metric. AWS documents
#: up to two minutes before a new datapoint is queryable, so the poll has to
#: be patient.
METRIC_PERIOD_SECONDS = 60
METRIC_LOOKBACK_MINUTES = 15
METRIC_POLL_SECONDS = 15

#: The stack must be in a state whose resources are the live ones (copied
#: from scripts/verify_edge.py: the same reasoning, the same states).
ACCEPTABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "IMPORT_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
}
UNHEALTHY_STATUS_SUFFIXES = ("_FAILED", "_IN_PROGRESS")
UNHEALTHY_STATUSES = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}

#: The live phases of the rollout.
PHASE_INERT = "inert"
PHASE_ENFORCED = "enforced"

#: The valid values of ``--expect``.
EXPECT_CHOICES = ("auto", "header-live", "guard-inert", "guard-enforced")


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


def _describe_stack(cfn_client, stack_name, region):
    """Describe a stack and fail when its state is not authoritative."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", []) or []
    if not stacks:
        raise RuntimeError(
            "CloudFormation returned no stack named {!r} in {}".format(
                stack_name, region
            )
        )
    stack = stacks[0]
    status = stack.get("StackStatus")
    if status in UNHEALTHY_STATUSES or any(
        status.endswith(suffix) for suffix in UNHEALTHY_STATUS_SUFFIXES
    ):
        raise RuntimeError(
            "Stack {} StackStatus={!r} does not describe a live "
            "deployment; the origin guard cannot be verified. Recover the "
            "stack first (see OPS_PLAYBOOK.md).".format(stack_name, status)
        )
    if status not in ACCEPTABLE_STACK_STATUSES:
        raise RuntimeError(
            "Stack {} StackStatus={!r} is not a state whose resources are "
            "the live ones".format(stack_name, status)
        )
    if status == "UPDATE_ROLLBACK_COMPLETE":
        print(
            "WARN: stack {} StackStatus={} -- the last deploy was rolled "
            "back; what is verified below is the last GOOD deploy".format(
                stack_name, status
            )
        )
    else:
        print("PASS: stack {} StackStatus={}".format(stack_name, status))
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
        kwargs = {"MaxItems": 100}
        if marker:
            kwargs["Marker"] = marker
        response = cloudfront_client.list_distributions(**kwargs)
        distribution_list = (response or {}).get("DistributionList") or {}
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


def _is_not_found(exc):
    """True when an AWS error means "that distribution does not exist"."""
    response = getattr(exc, "response", None) or {}
    code = response.get("ErrorCode")
    return code in ("ResourceNotFound", "NoSuchDistribution")


def _resolve_distribution(cloudfront_client, distribution_id, domain):
    """Return the live distribution serving ``domain``.

    The distribution id is taken from the stack's ``DistributionArn`` output.
    When that distribution does not exist, the fallback resolves the
    distribution by the ``CloudFrontDomain`` output through
    ``list_distributions`` (the way scripts/verify_distribution_identity.py
    does). Any OTHER AWS error is raised: a throttling error must never be
    mistaken for a missing distribution.
    """
    if distribution_id:
        try:
            response = cloudfront_client.get_distribution(Id=distribution_id)
        except Exception as exc:
            if not _is_not_found(exc):
                raise
            response = None
        distribution = (response or {}).get("Distribution")
        if distribution:
            return distribution
    items = _list_distributions(cloudfront_client)
    for item in items:
        if item.get("DomainName") == domain:
            return item
    raise RuntimeError(
        "no live CloudFront distribution serves {!r} (the domain reported by "
        "the stack); the stack output and the live distributions "
        "disagree".format(domain)
    )


def _origin_for_host(distribution, host):
    """Return the origin that serves ``host``, or None.

    The header must be on the origin that ACTUALLY serves the Function URL,
    not merely somewhere in the distribution's configuration.

    The origins live under the distribution's ``DistributionConfig`` in the
    GetDistribution reply (the top level carries the distribution's own
    fields, not its origins) -- reading them from the wrong level makes the
    witness report that no origin serves the Function URL, which is exactly
    the "the header cannot be verified" state it is meant to detect.
    """
    config = distribution.get("DistributionConfig") or distribution
    origins = (config.get("Origins") or {}).get("Items") or []
    for origin in origins:
        domain = origin.get("DomainName") or ""
        if domain.lower() == (host or "").lower():
            return origin
    return None


def _custom_header_items(origin):
    """Return the origin's custom headers as a list of {HeaderName, ...}.

    The CloudFront API carries the origin's custom headers in a structure
    - ``CustomHeaders: {Quantity: N, Items: [...]}`` - not in a plain
    list, so reading it as a list yields the structure's KEYS (strings) and
    every later access to a header fails. The template's
    ``OriginCustomHeaders`` is the CloudFormation spelling of the same
    thing, and the resource provider maps one onto the other.
    """
    headers = origin.get("CustomHeaders") or {}
    if isinstance(headers, dict):
        return list(headers.get("Items") or [])
    return list(headers)


def _origin_header_value(origin, header_name):
    """Return the stamped value of ``header_name``, or None when absent.

    The CloudFront API calls the origin's custom headers ``CustomHeaders``
    and carries them in a structure (``{Quantity, Items}``);
    CloudFormation's template property is ``OriginCustomHeaders``, and the
    resource provider maps one onto the other. A witness that reads the
    template's name from an API reply finds nothing, and one that reads
    the API's structure as a list sees its own keys -- so a live,
    correctly stamped header gets reported missing.
    """
    for header in _custom_header_items(origin):
        if header.get("HeaderName") == header_name:
            return header.get("HeaderValue")
    return None


def _function_name(outputs, cfn_client, stack_name):
    """Return the name of the Django Lambda function."""
    arn = outputs.get("LambdaFunctionArn")
    if isinstance(arn, str) and ":function:" in arn:
        return arn.split(":function:")[-1] or None
    response = cfn_client.describe_stack_resources(StackName=stack_name)
    for resource in response.get("StackResources", []) or []:
        if resource.get("ResourceType") in (
            "AWS::Lambda::Function",
            "AWS::Serverless::Function",
        ):
            physical_id = resource.get("PhysicalResourceId")
            if physical_id:
                return physical_id
    raise RuntimeError(
        "the live Django function could not be identified (no "
        "LambdaFunctionArn output and no Lambda resource in the stack); the "
        "guard's environment cannot be read"
    )


def _lambda_environment(lambda_client, function_name):
    """Return the Lambda function's environment variables."""
    response = lambda_client.get_function_configuration(
        FunctionName=function_name
    )
    environment = (response or {}).get("Environment") or {}
    return environment.get("Variables") or {}


def _alarm_name(cfn_client, stack_name):
    """Return the name of the stack's CloudWatch alarm."""
    response = cfn_client.describe_stack_resources(StackName=stack_name)
    for resource in response.get("StackResources", []) or []:
        if resource.get("ResourceType") == "AWS::CloudWatch::Alarm":
            physical_id = resource.get("PhysicalResourceId")
            if physical_id:
                return physical_id
    return "{}{}".format(stack_name, ALARM_NAME_SUFFIX)


def _http_get(url, timeout):
    """GET ``url`` and return ``(status, body_text)``.

    HTTP error responses are returned rather than raised, so their status
    and body can be asserted (a 403 refusal is the expected answer of the
    direct probe). Transport failures become a RuntimeError.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "could not reach {}: {}".format(url, exc.reason)
        ) from None


def _check_header_live(facts, min_header_age_minutes, now):
    """Run the pre-deploy preflight: the header is live and settled."""
    last_modified = facts.get("last_modified")
    if not isinstance(last_modified, datetime):
        raise RuntimeError(
            "the live distribution reports no LastModifiedTime; the "
            "CloudFront propagation of the origin header cannot be judged"
        )
    age_minutes = (now() - last_modified).total_seconds() / 60.0
    if age_minutes < min_header_age_minutes:
        raise RuntimeError(
            "the CloudFront distribution was modified {:.1f} minutes ago, "
            "which is less than the required {} minutes: the origin header "
            "has not propagated to every edge yet".format(
                age_minutes, min_header_age_minutes
            )
        )
    print(
        "PASS: the distribution has not changed for {:.1f} minutes (the "
        "origin header has had time to propagate)".format(age_minutes)
    )

    stamped = facts["stamped_value"]
    if stamped is None:
        raise RuntimeError(
            "the origin that serves the Function URL does not stamp the "
            "header {!r}; the guard may not enforce: legitimate requests "
            "that miss the header would be refused".format(
                facts["header_name"]
            )
        )
    if not str(stamped).strip():
        raise RuntimeError(
            "the origin that serves the Function URL stamps the header "
            "{!r} with an EMPTY value; the guard may not enforce".format(
                facts["header_name"]
            )
        )
    print(
        "PASS: the live origin serves the Function URL and stamps the "
        "header {!r} with a non-empty value".format(facts["header_name"])
    )

    lambda_header_name = facts["lambda_header_name"]
    if lambda_header_name != facts["header_name"]:
        raise RuntimeError(
            "the origin header name disagrees: the stack parameter says "
            "{!r}, the Lambda environment says {!r}. A differently spelled "
            "header is a different header, so the guard would refuse "
            "every legitimate request".format(
                facts["header_name"], lambda_header_name
            )
        )
    print(
        "PASS: the header name {!r} agrees across the stack parameter and "
        "the Lambda environment".format(facts["header_name"])
    )

    secret = facts["secret"]
    if secret:
        if not hmac.compare_digest(
            str(secret).encode("utf-8"), str(stamped).encode("utf-8")
        ):
            raise RuntimeError(
                "the Lambda's origin secret does NOT match the value the "
                "distribution stamps; the guard would refuse every request "
                "that came through CloudFront"
            )
        print(
            "PASS: the Lambda's secret matches the distribution's stamped "
            "origin header"
        )


def _check_guard_inert(facts, http_get, timeout_seconds):
    """Run the phase-1 contract: the guard is live and cannot refuse."""
    if facts["secret"]:
        raise RuntimeError(
            "the Lambda still carries ORIGIN_HEADER_SECRET: the guard is "
            "ENFORCING, not the declared inert phase"
        )
    status, _ = http_get(facts["function_url"], timeout_seconds)
    if status == 403:
        raise RuntimeError(
            "a direct Function-URL call was refused with 403 although the "
            "guard is inert"
        )
    print("PASS: a direct Function-URL call is not refused (the guard is inert)")

    site_status, _ = http_get(facts["site_url"], timeout_seconds)
    if site_status != 200:
        raise RuntimeError(
            "the public site returned status={} instead of 200; a "
            "legitimate request would be refused".format(site_status)
        )
    print("PASS: the public site serves 200")


def _metric_sum(cloudwatch_client, end_time):
    """Return the sum of the refusal metric over the recent window."""
    response = cloudwatch_client.get_metric_statistics(
        Namespace=METRIC_NAMESPACE,
        MetricName=REFUSAL_METRIC_NAME,
        Statistics=["Sum"],
        Period=METRIC_PERIOD_SECONDS,
        StartTime=end_time - timedelta(minutes=METRIC_LOOKBACK_MINUTES),
        EndTime=end_time,
    )
    total = 0.0
    for datapoint in (response or {}).get("Datapoints") or []:
        total += float(datapoint.get("Sum") or 0)
    return total


def _wait_for_refusal_metric(
    cloudwatch_client, baseline, wait_seconds, sleep, now
):
    """Poll the refusal metric until it moved, or the wait is over.

    AWS documents up to two minutes before a new datapoint is queryable, so
    the poll has to be patient; ``sleep`` is injected so the tests do not
    wait.
    """
    attempts = max(
        1, int(math.ceil(float(wait_seconds) / float(METRIC_POLL_SECONDS)))
    )
    total = baseline
    for attempt in range(attempts):
        total = _metric_sum(cloudwatch_client, now())
        if total >= baseline + 1:
            return total
        if attempt < attempts - 1:
            sleep(METRIC_POLL_SECONDS)
    return total


def _alarm(cloudwatch_client, alarm_name):
    """Return the alarm CloudWatch reports, or None when it does not exist."""
    response = cloudwatch_client.describe_alarms(AlarmNames=[alarm_name])
    alarms = (response or {}).get("MetricAlarms") or []
    return alarms[0] if alarms else None


def _check_guard_enforced(
    facts,
    cloudwatch_client,
    http_get,
    timeout_seconds,
    metric_wait_seconds,
    sleep,
    now,
):
    """Run the phase-2 contract: the guard refuses, counts, and the site
    still serves."""
    if not facts["secret"]:
        raise RuntimeError(
            "the Lambda carries no ORIGIN_HEADER_SECRET: the guard is not "
            "enforcing"
        )

    # The baseline is read BEFORE the deliberate refused call, so the
    # comparison proves that THIS refusal was counted.
    baseline = _metric_sum(cloudwatch_client, now())

    status, body = http_get(facts["function_url"], timeout_seconds)
    if status != 403:
        raise RuntimeError(
            "a direct Function-URL call returned status={} instead of 403; "
            "the guard is not enforcing".format(status)
        )
    if body != REFUSAL_BODY:
        raise RuntimeError(
            "the refusal body is not the fixed refusal body; the middleware "
            "and this witness have drifted apart"
        )
    print("PASS: a direct Function-URL call is refused with the fixed 403 body")

    site_status, _ = http_get(facts["site_url"], timeout_seconds)
    if site_status != 200:
        raise RuntimeError(
            "the public site returned status={} instead of 200; a "
            "legitimate request would be refused".format(site_status)
        )
    print("PASS: the public site serves 200 (no legitimate request refused)")

    total = _wait_for_refusal_metric(
        cloudwatch_client, baseline, metric_wait_seconds, sleep, now
    )
    if total < baseline + 1:
        raise RuntimeError(
            "the refusal metric did not increase within {} seconds (it "
            "stayed at {}); the refused call was not counted".format(
                metric_wait_seconds, total
            )
        )
    print("PASS: the refusal metric increased after the refused call")

    alarm = _alarm(cloudwatch_client, facts["alarm_name"])
    if alarm is None:
        raise RuntimeError(
            "the refusal alarm {} does not exist; a caller bypassing "
            "CloudFront would not page anybody".format(facts["alarm_name"])
        )
    state = alarm.get("StateValue")
    if state == "ALARM":
        print(
            "WARN: the refusal alarm {} is in ALARM -- direct calls are "
            "being refused right now".format(facts["alarm_name"])
        )
    else:
        print(
            "PASS: the refusal alarm exists (state={})".format(state)
        )


def run(
    stack_name,
    region,
    site_url,
    expect="auto",
    expect_enforcement=None,
    metric_wait_seconds=180,
    min_header_age_minutes=10,
    timeout_seconds=30,
    *,
    client_factory=boto3.client,
    http_get=None,
    sleep=time.sleep,
    now=None,
):
    """Run the witness; return 0 or raise RuntimeError."""
    http_get = http_get if http_get is not None else _http_get
    now = now if now is not None else (lambda: datetime.now(timezone.utc))

    cfn_client = client_factory("cloudformation", region_name=region)
    stack = _describe_stack(cfn_client, stack_name, region)
    outputs = _stack_outputs(stack)
    parameters = _stack_parameters(stack)

    distribution_arn = outputs.get("DistributionArn")
    if not distribution_arn:
        raise RuntimeError(
            "the stack does not export a DistributionArn output; the live "
            "distribution cannot be identified"
        )
    domain = outputs.get("CloudFrontDomain")
    if not domain:
        raise RuntimeError(
            "the stack does not export a CloudFrontDomain output; the live "
            "distribution cannot be identified"
        )
    function_url = outputs.get("FunctionUrl")
    if not function_url:
        raise RuntimeError(
            "the stack does not export a FunctionUrl output; the origin "
            "that serves the application cannot be identified"
        )

    header_name = (
        parameters.get("OriginHeaderName") or DEFAULT_ORIGIN_HEADER_NAME
    )

    cloudfront_client = client_factory(
        "cloudfront", region_name=CLOUDFRONT_REGION
    )
    distribution = _resolve_distribution(
        cloudfront_client,
        _distribution_id_from_arn(distribution_arn),
        domain,
    )

    host = urllib.parse.urlparse(function_url).netloc
    origin = _origin_for_host(distribution, host)
    if origin is None:
        raise RuntimeError(
            "no origin of the live distribution serves the Function URL "
            "host {!r}; the origin header cannot be verified".format(host)
        )

    lambda_client = client_factory("lambda", region_name=region)
    function_name = _function_name(outputs, cfn_client, stack_name)
    environment = _lambda_environment(lambda_client, function_name)

    facts = {
        "header_name": header_name,
        "lambda_header_name": environment.get("ORIGIN_HEADER_NAME"),
        "secret": environment.get("ORIGIN_HEADER_SECRET"),
        "stamped_value": _origin_header_value(origin, header_name),
        "last_modified": distribution.get("LastModifiedTime"),
        "function_url": function_url,
        "site_url": site_url,
        "alarm_name": _alarm_name(cfn_client, stack_name),
    }

    _check_header_live(facts, min_header_age_minutes, now)

    detected_phase = PHASE_ENFORCED if facts["secret"] else PHASE_INERT
    print("PASS: the live phase is {!r}".format(detected_phase))

    if expect == "header-live":
        return 0

    if expect == "auto":
        if expect_enforcement is not None:
            declared = PHASE_ENFORCED if expect_enforcement else PHASE_INERT
            if declared != detected_phase:
                raise RuntimeError(
                    "the deploy declared enforcement={} but the live phase "
                    "is {!r}; the deploy did not take effect".format(
                        declared, detected_phase
                    )
                )
        target_phase = detected_phase
    elif expect == "guard-inert":
        target_phase = PHASE_INERT
    else:
        target_phase = PHASE_ENFORCED

    if target_phase == PHASE_INERT:
        _check_guard_inert(facts, http_get, timeout_seconds)
        return 0

    cloudwatch_client = client_factory("cloudwatch", region_name=region)
    _check_guard_enforced(
        facts,
        cloudwatch_client,
        http_get,
        timeout_seconds,
        metric_wait_seconds,
        sleep,
        now,
    )
    return 0


def main(argv=None, client_factory=boto3.client, http_get=None, sleep=time.sleep,
         now=None):
    parser = argparse.ArgumentParser(
        description="Verify the live origin guard rollout"
    )
    parser.add_argument(
        "--stack-name", default="chemically", help="CloudFormation stack name"
    )
    parser.add_argument("--region", default="us-east-2", help="AWS region")
    parser.add_argument(
        "--site-url",
        default="https://chemic-ally.xyz",
        help="the public site, probed for a 200",
    )
    parser.add_argument(
        "--expect",
        choices=list(EXPECT_CHOICES),
        default="auto",
        help="which live contract to verify",
    )
    parser.add_argument(
        "--expect-enforcement",
        choices=["true", "false"],
        default=None,
        help=(
            "with --expect auto: the switch value the deploy intended; the "
            "detected live phase must equal it"
        ),
    )
    parser.add_argument(
        "--metric-wait-seconds",
        type=int,
        default=180,
        help="how long to wait for the refusal metric to move",
    )
    parser.add_argument(
        "--min-header-age-minutes",
        type=int,
        default=10,
        help="how long the distribution must have been unchanged",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30,
        help="HTTP timeout of each probe",
    )
    args = parser.parse_args(argv)

    expect_enforcement = None
    if args.expect_enforcement is not None:
        expect_enforcement = args.expect_enforcement == "true"

    try:
        return run(
            args.stack_name,
            args.region,
            args.site_url,
            expect=args.expect,
            expect_enforcement=expect_enforcement,
            metric_wait_seconds=args.metric_wait_seconds,
            min_header_age_minutes=args.min_header_age_minutes,
            timeout_seconds=args.timeout_seconds,
            client_factory=client_factory,
            http_get=http_get,
            sleep=sleep,
            now=now,
        )
    except RuntimeError as exc:
        print("FAIL: {}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
