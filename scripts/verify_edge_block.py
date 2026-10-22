#!/usr/bin/env python3
"""Bounded probe that proves the edge block (M007/S05/T02).

Switching the edge rate rule from ``Count`` to ``Block`` is only meaningful
if the switch is PROVEN against the live distribution: a request that trips
the limit must come back as a CloudFront refusal and the origin must never
see it. A template assertion cannot prove that, and the CI run that carries
the flip is the only moment the proof can be taken. This script is that
proof, and it is deliberately bounded.

The probe proves five things, in this order:

1. the LIVE web ACL holds the ``EdgeRateLimit`` rule in ``Block`` mode --
   read from the ACL, never from the stack parameter that merely requested
   it (a declared control is not a live one);
2. the probe's path is inside the rule's scope-down, so the burst can trip
   the rule at all (checked BEFORE a single request is sent);
3. the origin ANSWERED the same URL from the same client a moment before
   the trip, and the edge then refused it with a 403 that is NOT the origin
   guard's refusal body -- the edge refused it, not the middleware;
4. the WAF's own ``BlockedRequests`` counter increased, and the increase is
   compared with the number of observed blocks (other traffic may have been
   blocked too; that is information, not a failure);
5. the Lambda ``Invocations`` counter did NOT grow by the blocked count --
   the origin never saw the requests the edge refused.

SAFETY. This is the only deliberate traffic this project generates against
production, so the bounds are hard requirements, not defaults:

* ``--confirm-live`` must be present or nothing is sent at all;
* only GET requests are ever sent;
* the number of requests is capped by ``--requests`` (default 2600) and
  paced at ``--rate`` (default 12/s);
* the default path is the cheap read-only ``/hplc/api/levels/``; the
  CPU-bound solver path ``/calculate/equilibria`` is refused unless
  ``--allow-expensive-path`` is passed;
* the script can never change the rule: it only calls read APIs.

Everything is injectable -- ``client_factory``, ``http_get``, ``sleep`` and
``now`` -- so every branch is unit-testable without the network. AWS API
failures are RAISED, never swallowed: a throttling error or a lost
connection must never be mistaken for a green check.

Exit codes: 0 = every gate passed, 1 = a gate failed (or an AWS error
surfaced).
"""

import argparse
import base64
import binascii
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

#: The CloudWatch namespace the WAF publishes its counters into.
WAF_METRIC_NAMESPACE = "AWS/WAFV2"

#: The rule this probe is about (the edge template's rule name).
RULE_NAME = "EdgeRateLimit"

#: The three counters the WAF publishes for a rule.
ALLOWED_REQUESTS = "AllowedRequests"
COUNTED_REQUESTS = "CountedRequests"
BLOCKED_REQUESTS = "BlockedRequests"
COUNTER_NAMES = (ALLOWED_REQUESTS, COUNTED_REQUESTS, BLOCKED_REQUESTS)

#: The origin side of the proof: the Lambda function's own invocations.
LAMBDA_METRIC_NAMESPACE = "AWS/Lambda"
LAMBDA_METRIC_NAME = "Invocations"

#: The default path: a cheap, read-only API path inside the rule's scope.
DEFAULT_PATH = "/hplc/api/levels/"

#: The request cap. The probe never sends more than this.
DEFAULT_REQUESTS = 2600

#: The pace: requests per second from the probe's single source address.
DEFAULT_RATE_PER_SECOND = 12

#: The HTTP timeout of every request the probe sends.
DEFAULT_TIMEOUT_SECONDS = 10

#: How long the probe waits for the counters to be published.
DEFAULT_METRIC_WAIT_SECONDS = 180

#: How often the counters are polled while waiting.
METRIC_POLL_SECONDS = 15

#: The public site (also the burst's target host).
DEFAULT_SITE_URL = "https://chemic-ally.xyz"

#: The body of the origin guard's refusal. MUST stay byte-identical to
#: ``chemically.middleware.REFUSAL_BODY`` -- a test asserts it, so the
#: probe cannot drift from the code it witnesses. A 403 with this body did
#: NOT come from the edge: it came from the origin, which means the request
#: did not arrive through CloudFront at all.
REFUSAL_BODY = "Forbidden: request did not arrive through the CloudFront origin."

#: The path that runs the CPU-bound equilibrium solver. The probe refuses
#: it unless the operator explicitly allows it.
EXPENSIVE_PATH = "/calculate/equilibria"

#: The stacks the probe reads.
DEFAULT_EDGE_STACK_NAME = "chemically-edge"
DEFAULT_EDGE_REGION = "us-east-1"
DEFAULT_APP_STACK_NAME = "chemically"
DEFAULT_APP_REGION = "us-east-2"

#: How many blocked responses must FOLLOW the first one. The block has to
#: be proven repeatedly, not by a single lucky response.
DEFAULT_CONFIRM_BLOCKED_COUNT = 25

#: The CloudWatch query period for the counters (both WAF and Lambda).
METRIC_PERIOD_SECONDS = 60

#: How far back the baseline is read before the burst.
METRIC_LOOKBACK_MINUTES = 15

#: The origin-side slack: max(ORIGIN_SLACK_MINIMUM, 10% of answered).
ORIGIN_SLACK_MINIMUM = 50
ORIGIN_SLACK_FRACTION = 0.1

#: The four kinds a response can be. They are mutually exclusive and every
#: response is classified into EXACTLY one of them.
KIND_BLOCKED = "blocked"
KIND_ORIGIN_REFUSAL = "origin_refusal"
KIND_ANSWERED = "answered"
KIND_TRANSPORT_ERROR = "transport_error"
KINDS = (KIND_BLOCKED, KIND_ORIGIN_REFUSAL, KIND_ANSWERED, KIND_TRANSPORT_ERROR)

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


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _stack_outputs(stack):
    """Return the stack's outputs as a dict."""
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


def _floor_to_minute(moment):
    """Truncate a datetime to the start of its minute."""
    return moment.replace(second=0, microsecond=0)


def _decode_search_string(value):
    """Return the rule's search string, decoded when it is base64.

    WAFv2 reports ``SearchString`` base64-encoded in some SDK responses.
    Both forms are accepted so the scope check works either way.
    """
    if not isinstance(value, str) or not value:
        return ""
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return value
    if decoded.startswith("/"):
        return decoded
    return value


def _http_get(url, timeout):
    """GET ``url`` and return ``(status, body_text, headers)``.

    HTTP error responses are RETURNED rather than raised, so their status
    and body can be classified (a 403 refusal is the expected answer of
    the edge). Transport failures raise, and the caller classifies them.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.read().decode("utf-8", "replace"),
                dict(response.headers or {}),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            exc.read().decode("utf-8", "replace"),
            dict(exc.headers or {}),
        )


def _classify(status, body):
    """Classify one response into EXACTLY one of the four kinds.

    ``blocked`` -- a 403 that is NOT the origin guard's refusal body: the
    edge refused it;
    ``origin_refusal`` -- a 403 whose body IS the guard's refusal body: a
    serious anomaly (the request did not arrive through CloudFront). It is
    NEVER counted as an edge block;
    ``answered`` -- any other status (200, 503, 429, ...): the ORIGIN
    answered, so the origin saw the request. A 503 counts as "answered" --
    the origin answered even when its database is unavailable.
    """
    if status == 403:
        if body == REFUSAL_BODY:
            return KIND_ORIGIN_REFUSAL
        return KIND_BLOCKED
    return KIND_ANSWERED


# --------------------------------------------------------------------------
# The gates. Each one is a named function so it can be pinned by a test.
# --------------------------------------------------------------------------


def gate_confirm_live(confirm_live):
    """S1: the operator must confirm that real traffic will be sent."""
    if not confirm_live:
        raise RuntimeError(
            "this sends real traffic to the live site; pass --confirm-live"
        )
    print("PASS: --confirm-live was passed (real traffic will be sent)")


def gate_path_is_not_expensive(path, allow_expensive_path):
    """S2: the CPU-bound solver path is refused unless it is allowed."""
    if allow_expensive_path:
        return
    if path.startswith(EXPENSIVE_PATH):
        raise RuntimeError(
            "the probe path {!r} runs the CPU-bound equilibrium solver; "
            "the probe refuses it because a burst there would load the "
            "origin, not the edge. Use the cheap read-only API path, or "
            "pass --allow-expensive-path if you really mean it.".format(path)
        )


def gate_stack_live(cfn_client, stack_name, region):
    """G1: the edge stack exists and describes the live deployment."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = (response or {}).get("Stacks") or []
    if not stacks:
        raise RuntimeError(
            "CloudFormation returned no stack named {!r} in {}".format(
                stack_name, region
            )
        )
    stack = stacks[0]
    status = stack.get("StackStatus")
    if status in UNHEALTHY_STATUSES or any(
        str(status).endswith(suffix) for suffix in UNHEALTHY_STATUS_SUFFIXES
    ):
        raise RuntimeError(
            "stack {} StackStatus={!r} does not describe a live deployment; "
            "the edge rule cannot be read. Recover the stack first (see "
            "OPS_PLAYBOOK.md, 'The two-stack deployment')".format(
                stack_name, status
            )
        )
    if status not in ACCEPTABLE_STACK_STATUSES:
        raise RuntimeError(
            "stack {} StackStatus={!r} is not a state whose resources are "
            "the live ones".format(stack_name, status)
        )
    if status == "UPDATE_ROLLBACK_COMPLETE":
        print(
            "WARN: stack {} StackStatus={} -- the last edge deploy was "
            "rolled back; what is probed below belongs to the last GOOD "
            "deploy".format(stack_name, status)
        )
    else:
        print("PASS: stack {} StackStatus={}".format(stack_name, status))
    return stack


def gate_web_acl_holds_rule(web_acl, rule_name=RULE_NAME):
    """G2: the web ACL exists and holds the EdgeRateLimit rule."""
    if not web_acl:
        raise RuntimeError(
            "the web ACL could not be read; the live rule cannot be "
            "probed"
        )
    rules = web_acl.get("Rules") or []
    for rule in rules:
        if rule.get("Name") == rule_name:
            return rule
    raise RuntimeError(
        "the live web ACL holds no rule named {!r} (it holds {}); the edge "
        "stack drifted from the deploy contract".format(
            rule_name, sorted(str(r.get("Name")) for r in rules)
        )
    )


def gate_action_is_blocking(action, expect_no_block):
    """G3: the live action is the one the probe is allowed to prove."""
    if expect_no_block:
        if action != "Count":
            raise RuntimeError(
                "the live rule is in {!r} mode but --expect-no-block was "
                "requested; a Count rule cannot refuse, so the probe would "
                "observe a block that the live mode cannot produce".format(
                    action
                )
            )
        print(
            "PASS: the live rule is in Count mode; the probe asserts that "
            "NO edge block occurs"
        )
        return action
    if action != "Block":
        raise RuntimeError(
            "the rule is not blocking; the probe cannot prove a block that "
            "is not configured (the live action is {!r})".format(action)
        )
    print("PASS: the live rule action is Block (read from the LIVE web ACL)")
    return action


def gate_limit_and_window(rule):
    """G4: the live ceiling and evaluation window are reportable."""
    statement = rule.get("RateBasedStatement") or {}
    limit = statement.get("Limit")
    window = statement.get("EvaluationWindowSec")
    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        raise RuntimeError(
            "the live rule reports the rate limit {!r}, which is not a "
            "number".format(limit)
        ) from None
    if limit_value <= 0:
        raise RuntimeError(
            "the live rule reports the rate limit {!r}, which cannot be a "
            "ceiling".format(limit)
        )
    try:
        window_value = int(window)
    except (TypeError, ValueError):
        raise RuntimeError(
            "the live rule reports the evaluation window {!r}, which is not "
            "a number".format(window)
        ) from None
    if window_value <= 0:
        raise RuntimeError(
            "the live rule reports the evaluation window {!r}, which cannot "
            "be an evaluation window".format(window)
        )
    print(
        "PASS: the live ceiling is {}/{}s".format(limit_value, window_value)
    )
    return limit_value, window_value


def _scope_search_strings(rule):
    """The path prefixes the rule's scope-down covers."""
    statement = (
        (rule.get("RateBasedStatement") or {}).get("ScopeDownStatement") or {}
    )
    or_statement = statement.get("OrStatement") or {}
    statements = list(or_statement.get("Statements") or [])
    if not statements:
        statements = [statement]
    prefixes = []
    for entry in statements:
        byte_match = (entry or {}).get("ByteMatchStatement") or {}
        field = byte_match.get("FieldToMatch") or {}
        if "UriPath" not in field:
            continue
        if byte_match.get("PositionalConstraint") != "STARTS_WITH":
            continue
        search = _decode_search_string(byte_match.get("SearchString"))
        if search:
            prefixes.append(search)
    return prefixes


def gate_path_in_scope(path, prefixes):
    """G5: the probe path is inside the rule's scope-down.

    This runs BEFORE a single request is sent: a path outside the scope
    would never trip the rule, so the burst would prove nothing.
    """
    if not prefixes:
        raise RuntimeError(
            "the live rule declares no UriPath scope-down statement; the "
            "probe path cannot be proven to be in scope"
        )
    for prefix in prefixes:
        if path.startswith(prefix):
            print(
                "PASS: the probe path {} is inside the rule's scope "
                "({})".format(path, prefix)
            )
            return prefix
    raise RuntimeError(
        "the probe path is not in the rule's scope; the probe would never "
        "trip the rule (path {!r}, scope {})".format(path, prefixes)
    )


def gate_burst_evidence(burst, expect_no_block, confirm_blocked_count,
                        evaluation_window_seconds):
    """G6: the burst proves what it claims to prove.

    In Block mode: the limit must have tripped, the origin must have
    answered BEFORE the first block, and the block must have been observed
    repeatedly. In Count mode (--expect-no-block): no edge block may have
    happened at all.
    """
    if burst["origin_refusal"]:
        raise RuntimeError(
            "{} response(s) carried the origin guard's refusal body; the "
            "request did NOT arrive through CloudFront. That is a serious "
            "anomaly and is never counted as an edge block.".format(
                burst["origin_refusal"]
            )
        )
    if expect_no_block:
        if burst["blocked"]:
            raise RuntimeError(
                "the probe observed {} edge block(s) while the live rule is "
                "in Count mode; a Count rule cannot refuse".format(
                    burst["blocked"]
                )
            )
        if not burst["answered"]:
            raise RuntimeError(
                "no request reached the origin: {} transport error(s) and "
                "no answer; the probe proved nothing".format(
                    burst["transport_error"]
                )
            )
        print(
            "PASS: no edge block occurred in Count mode and {} request(s) "
            "reached the origin".format(burst["answered"])
        )
        return

    if burst["first_blocked_index"] is None:
        raise RuntimeError(
            "the burst sent {} request(s) and the limit never tripped; no "
            "edge block was observed, so nothing was proven. Raise --requests "
            "or --rate if the ceiling was not reached.".format(
                burst["requests_sent"]
            )
        )
    if not burst["answered_before_first_block"]:
        extra = ""
        if burst["transport_error"]:
            extra = (
                " Note: {} request(s) could not be made at all, so an "
                "answer may have been lost to a transport failure.".format(
                    burst["transport_error"]
                )
            )
        raise RuntimeError(
            "the source address was already over the ceiling when the probe "
            "started (no request reached the origin); the evaluation window "
            "is {} seconds -- re-run after it resets{}".format(
                evaluation_window_seconds, extra
            )
        )
    wanted = 1 + confirm_blocked_count
    if burst["blocked"] < wanted:
        raise RuntimeError(
            "the edge block was observed {} time(s) but {} are required "
            "(the first block plus --confirm-blocked-count {}); a single "
            "lucky response is not a proof".format(
                burst["blocked"], wanted, confirm_blocked_count
            )
        )
    print(
        "PASS: the edge refused {} request(s) after the origin had answered "
        "{} request(s) first".format(burst["blocked"],
                                     burst["answered_before_first_block"])
    )


def gate_waf_counter_moved(before, after, expect_no_block):
    """G7: the WAF's own counter agrees with what the probe observed."""
    delta = _blocked_delta(before, after)
    if expect_no_block:
        if delta > 0:
            raise RuntimeError(
                "the WAF's {} counter increased by {} while the live rule is "
                "in Count mode; something else refused traffic".format(
                    BLOCKED_REQUESTS, delta
                )
            )
        print(
            "PASS: the WAF's {} counter did not move in Count "
            "mode".format(BLOCKED_REQUESTS)
        )
        return delta
    if delta < 1:
        raise RuntimeError(
            "the WAF's {} counter did not move (it went from {} to {}); the "
            "WAF did not record the refusal, so the edge block is not "
            "proven".format(
                BLOCKED_REQUESTS,
                _counter_value(before, BLOCKED_REQUESTS),
                _counter_value(after, BLOCKED_REQUESTS),
            )
        )
    print(
        "PASS: the WAF's {} counter increased by {}".format(
            BLOCKED_REQUESTS, delta
        )
    )
    return delta


def gate_origin_saw_no_blocks(invocations, answered, slack,
                              expect_no_block=False):
    """G8: the origin did not see the requests the edge refused.

    In Count mode (a dry run) nothing was refused at the edge, so the
    origin legitimately sees every probe request plus whatever else the
    site serves. An excess is then reported, not failed: it is not an
    edge failure, because no request was refused.
    """
    if invocations > answered + slack:
        if expect_no_block:
            print(
                "NOTE: the origin recorded {} Lambda invocation(s) while the "
                "probe saw {} answered request(s) (slack {}); in Count mode "
                "the origin sees every request plus other traffic, so this "
                "is reported and NOT treated as an edge failure".format(
                    invocations, answered, slack
                )
            )
            return
        raise RuntimeError(
            "the origin recorded {} Lambda invocation(s) while the probe "
            "saw {} answered request(s) (slack {}); the requests the edge "
            "refused DID reach the origin".format(
                invocations, answered, slack
            )
        )
    print(
        "PASS: the origin recorded {} invocation(s) for {} answered "
        "request(s) (slack {}): the refused requests never reached the "
        "origin".format(invocations, answered, slack)
    )


def gate_site_serves_200(status):
    """G9: the ordinary site still serves 200 after the burst."""
    if status != 200:
        raise RuntimeError(
            "the public site returned status={} instead of 200 after the "
            "burst; ordinary browsing was affected".format(status)
        )
    print("PASS: the public site still serves 200 (ordinary browsing intact)")


# --------------------------------------------------------------------------
# Reading the counters
# --------------------------------------------------------------------------


def _discover_metrics(cloudwatch_client, web_acl_arn):
    """Discover the metrics AWS publishes for this web ACL.

    The dimension set is DISCOVERED, never guessed: a guessed dimension set
    silently returns no data.
    """
    response = cloudwatch_client.list_metrics(
        Namespace=WAF_METRIC_NAMESPACE,
        Dimensions=[{"Name": "WebACL", "Value": web_acl_arn}],
    )
    return list((response or {}).get("Metrics") or [])


def _counter_dimensions(discovered, metric_name):
    """The dimension set the discovery reported for a counter."""
    for entry in discovered:
        if entry.get("MetricName") == metric_name:
            return list(entry.get("Dimensions") or [])
    return None


def _read_counter(cloudwatch_client, namespace, metric_name, dimensions,
                  start_time, end_time, period=METRIC_PERIOD_SECONDS):
    """Read one counter over a window; return its total and its datapoints."""
    response = cloudwatch_client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        Statistics=["Sum"],
        Period=period,
        StartTime=start_time,
        EndTime=end_time,
    )
    datapoints = list((response or {}).get("Datapoints") or [])
    total = 0.0
    for datapoint in datapoints:
        total += float(datapoint.get("Sum") or 0)
    return {"value": total, "datapoints": datapoints}


def _read_waf_counters(cloudwatch_client, discovered, start_time, end_time):
    """Read the WAF's three counters over a window."""
    counters = {}
    for name in COUNTER_NAMES:
        dimensions = _counter_dimensions(discovered, name)
        if dimensions is None:
            counters[name] = {
                "state": "unknown",
                "value": 0.0,
                "datapoints": [],
                "dimensions": [],
            }
            continue
        read = _read_counter(
            cloudwatch_client,
            WAF_METRIC_NAMESPACE,
            name,
            dimensions,
            start_time,
            end_time,
        )
        counters[name] = {
            "state": "value" if read["datapoints"] else "observed-zero",
            "value": read["value"],
            "datapoints": read["datapoints"],
            "dimensions": dimensions,
        }
    return counters


def _covers_window(datapoints, moment, period=METRIC_PERIOD_SECONDS):
    """True when the read covers the minute the burst ended in."""
    threshold = _floor_to_minute(moment) - timedelta(seconds=period)
    for datapoint in datapoints:
        timestamp = datapoint.get("Timestamp")
        if timestamp is None:
            continue
        if timestamp >= threshold:
            return True
    return False


def _poll(read_fn, ready_fn, sleep, metric_wait_seconds, metric_poll_seconds):
    """Poll the counters until they are ready or the wait is over."""
    attempts = 0
    max_attempts = max(
        1,
        int(math.ceil(float(metric_wait_seconds) / float(metric_poll_seconds))),
    )
    result = read_fn()
    attempts += 1
    while attempts < max_attempts and not ready_fn(result):
        sleep(metric_poll_seconds)
        result = read_fn()
        attempts += 1
    return result, attempts


# --------------------------------------------------------------------------
# The burst
# --------------------------------------------------------------------------


def _burst_url(site_url, path, index):
    """The URL of one burst request."""
    base = "{}{}".format(site_url.rstrip("/"), path)
    return "{}?canary={}".format(base, index)


def _run_burst(http_get, site_url, path, requests, rate_per_second, sleep,
               timeout_seconds, confirm_blocked_count, expect_no_block):
    """Send the bounded burst and classify every response.

    The burst stops when the limit has tripped AND the block has been
    confirmed ``confirm_blocked_count`` further times, or when the request
    cap is reached -- never beyond.
    """
    if requests <= 0:
        raise RuntimeError("--requests must be at least 1")
    if rate_per_second <= 0:
        raise RuntimeError("--rate must be greater than 0")
    burst = {
        "requests_sent": 0,
        "blocked": 0,
        "answered": 0,
        "origin_refusal": 0,
        "transport_error": 0,
        "first_blocked_index": None,
        "first_blocked_headers": None,
        "answered_before_first_block": 0,
        "statuses": {},
    }
    wanted_blocks = 1 + confirm_blocked_count
    for index in range(requests):
        if index:
            sleep(1.0 / float(rate_per_second))
        url = _burst_url(site_url, path, index)
        try:
            status, body, headers = http_get(url, timeout_seconds)
        except Exception:  # noqa: BLE001 - a transport failure is data
            status, body, headers = None, None, {}
            kind = KIND_TRANSPORT_ERROR
        else:
            kind = _classify(status, body)
        burst["requests_sent"] += 1
        burst[kind] += 1
        if status is not None:
            key = str(status)
            burst["statuses"][key] = burst["statuses"].get(key, 0) + 1
        if kind == KIND_BLOCKED:
            if burst["first_blocked_index"] is None:
                burst["first_blocked_index"] = index
                burst["first_blocked_headers"] = dict(headers or {})
                # How many requests the ORIGIN answered before the edge
                # started refusing: that is the proof that the same URL,
                # from the same client, was served a moment earlier.
                burst["answered_before_first_block"] = burst["answered"]
        if (index + 1) % 100 == 0:
            print(
                "NOTE: {} request(s) sent (blocked={} answered={} "
                "origin_refusal={} transport_error={})".format(
                    burst["requests_sent"],
                    burst["blocked"],
                    burst["answered"],
                    burst["origin_refusal"],
                    burst["transport_error"],
                )
            )
        if expect_no_block and kind == KIND_BLOCKED:
            break
        if kind == KIND_BLOCKED and burst["first_blocked_index"] == index:
            if not burst["answered_before_first_block"]:
                # The very first response was already a block: the source
                # address was over the ceiling before the probe started.
                break
        if burst["blocked"] >= wanted_blocks:
            break
    return burst


# --------------------------------------------------------------------------
# The origin-side counter
# --------------------------------------------------------------------------


def _function_name(outputs, cfn_client, stack_name):
    """Return the name of the Django Lambda function.

    The name comes from the app stack's ``LambdaFunctionArn`` output (the
    part after ``:function:``), with the ``describe_stack_resources``
    fallback used by scripts/verify_origin_guard.py.
    """
    arn = outputs.get("LambdaFunctionArn")
    if isinstance(arn, str) and ":function:" in arn:
        return arn.split(":function:")[-1] or None
    response = cfn_client.describe_stack_resources(StackName=stack_name)
    for resource in (response or {}).get("StackResources", []) or []:
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
        "origin-side counter cannot be read"
    )


def _origin_slack(answered):
    """The documented slack between answered requests and invocations."""
    return max(
        ORIGIN_SLACK_MINIMUM,
        int(math.ceil(ORIGIN_SLACK_FRACTION * answered)),
    )


def _write_record(path, record):
    """Write the JSON record (the evidence the operator keeps)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run(
    edge_stack_name=DEFAULT_EDGE_STACK_NAME,
    edge_region=DEFAULT_EDGE_REGION,
    app_stack_name=DEFAULT_APP_STACK_NAME,
    app_region=DEFAULT_APP_REGION,
    site_url=DEFAULT_SITE_URL,
    path=DEFAULT_PATH,
    requests=DEFAULT_REQUESTS,
    rate_per_second=DEFAULT_RATE_PER_SECOND,
    confirm_blocked_count=DEFAULT_CONFIRM_BLOCKED_COUNT,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    metric_wait_seconds=DEFAULT_METRIC_WAIT_SECONDS,
    metric_poll_seconds=METRIC_POLL_SECONDS,
    confirm_live=False,
    allow_expensive_path=False,
    allow_missing_origin_metric=False,
    expect_no_block=False,
    record_out=None,
    *,
    client_factory=boto3.client,
    http_get=None,
    sleep=None,
    now=None,
):
    """Prove the edge block; return 0 or raise RuntimeError."""
    http_get = http_get if http_get is not None else _http_get
    sleep = sleep if sleep is not None else time.sleep
    now = now if now is not None else (lambda: datetime.now(timezone.utc))

    started_at = now()
    record = {
        "edge_stack_name": edge_stack_name,
        "edge_region": edge_region,
        "app_stack_name": app_stack_name,
        "app_region": app_region,
        "site_url": site_url,
        "path": path,
        "requests_cap": requests,
        "rate_per_second": rate_per_second,
        "confirm_blocked_count": confirm_blocked_count,
        "expect_no_block": expect_no_block,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "burst_started_at": None,
        "burst_ended_at": None,
        "action": None,
        "limit": None,
        "evaluation_window_seconds": None,
        "scope": [],
        "requests_sent": 0,
        "blocked": 0,
        "answered": 0,
        "origin_refusal": 0,
        "transport_error": 0,
        "first_blocked_index": None,
        "first_blocked_headers": None,
        "answered_before_first_block": 0,
        "statuses": {},
        "waf_counters_before": {},
        "waf_counters_after": {},
        "waf_blocked_delta": 0.0,
        "waf_allowed_delta": 0.0,
        "origin_metric_status": "unknown",
        "origin_invocations": None,
        "origin_slack": None,
        "site_status": None,
        "status": "running",
    }

    try:
        # ---- the safety gates run BEFORE anything is sent ---------------
        gate_confirm_live(confirm_live)
        gate_path_is_not_expensive(path, allow_expensive_path)

        edge_cfn = client_factory("cloudformation", region_name=edge_region)
        waf_client = client_factory("wafv2", region_name=edge_region)
        edge_cloudwatch = client_factory(
            "cloudwatch", region_name=edge_region
        )

        stack = gate_stack_live(edge_cfn, edge_stack_name, edge_region)
        web_acl_arn = _stack_outputs(stack).get("WebACLArn")
        if not web_acl_arn:
            raise RuntimeError(
                "stack {!r} does not export a WebACLArn output; the edge "
                "stack drifted from the deploy contract and the live web "
                "ACL cannot be identified".format(edge_stack_name)
            )
        record["web_acl_arn"] = web_acl_arn

        # The WAFv2 GetWebACL request parameter is ARN (not WebACLArn):
        # passing WebACLArn is a parameter validation error in botocore.
        response = waf_client.get_web_acl(ARN=web_acl_arn)
        web_acl = (response or {}).get("WebACL") or {}
        rule = gate_web_acl_holds_rule(web_acl)
        action = rule.get("Action")
        record["action"] = action
        gate_action_is_blocking(action, expect_no_block)
        limit, evaluation_window = gate_limit_and_window(rule)
        record["limit"] = limit
        record["evaluation_window_seconds"] = evaluation_window

        # ---- the scope check runs BEFORE a single request is sent -------
        scope = _scope_search_strings(rule)
        record["scope"] = scope
        gate_path_in_scope(path, scope)

        # ---- the baselines ---------------------------------------------
        discovered = _discover_metrics(edge_cloudwatch, web_acl_arn)
        record["discovered_metric_names"] = sorted(
            {entry.get("MetricName") for entry in discovered
             if entry.get("MetricName")}
        )
        baseline_end = _floor_to_minute(started_at)
        baseline_start = baseline_end - timedelta(
            minutes=METRIC_LOOKBACK_MINUTES
        )
        before = _read_waf_counters(
            edge_cloudwatch, discovered, baseline_start, baseline_end
        )
        record["waf_counters_before"] = _counter_snapshot(before)

        # ---- the burst -------------------------------------------------
        burst_started_at = now()
        record["burst_started_at"] = burst_started_at.isoformat()
        burst = _run_burst(
            http_get,
            site_url,
            path,
            requests,
            rate_per_second,
            sleep,
            timeout_seconds,
            confirm_blocked_count,
            expect_no_block,
        )
        burst_ended_at = now()
        record["burst_ended_at"] = burst_ended_at.isoformat()
        for key in (
            "requests_sent",
            "blocked",
            "answered",
            "origin_refusal",
            "transport_error",
            "first_blocked_index",
            "first_blocked_headers",
            "answered_before_first_block",
            "statuses",
        ):
            record[key] = burst[key]

        gate_burst_evidence(
            burst,
            expect_no_block,
            confirm_blocked_count,
            evaluation_window,
        )

        # ---- the WAF's own counters, after the burst ------------------
        def read_counters():
            return _read_waf_counters(
                edge_cloudwatch, discovered, baseline_start, burst_ended_at
            )

        def counters_ready(result):
            if expect_no_block:
                return _counters_published(result, burst_ended_at)
            return _blocked_delta(before, result) >= 1

        after, attempts = _poll(
            read_counters,
            counters_ready,
            sleep,
            metric_wait_seconds,
            metric_poll_seconds,
        )
        record["waf_counters_after"] = _counter_snapshot(after)
        record["waf_metric_polls"] = attempts
        blocked_delta = _blocked_delta(before, after)
        record["waf_blocked_delta"] = blocked_delta
        record["waf_allowed_delta"] = _allowed_delta(before, after)
        delta = gate_waf_counter_moved(before, after, expect_no_block)
        if delta != burst["blocked"]:
            print(
                "NOTE: the WAF counted {} blocked request(s) while the probe "
                "observed {}; the difference is other traffic that was "
                "refused too (information, not a failure)".format(
                    delta, burst["blocked"]
                )
            )

        # ---- the origin side ------------------------------------------
        origin = _check_origin_side(
            client_factory,
            app_stack_name,
            app_region,
            baseline_start,
            burst_ended_at,
            metric_wait_seconds,
            metric_poll_seconds,
            sleep,
            allow_missing_origin_metric,
            burst["answered"],
            expect_no_block,
        )
        record["origin_metric_status"] = origin["status"]
        record["origin_invocations"] = origin["invocations"]
        record["origin_slack"] = origin["slack"]

        # ---- the ordinary site -----------------------------------------
        site_status, _body, _headers = http_get(site_url, timeout_seconds)
        record["site_status"] = site_status
        gate_site_serves_200(site_status)

        record["status"] = "pass"
        return 0
    except Exception as exc:  # noqa: BLE001 - re-raised below
        record["status"] = "fail"
        record["error"] = str(exc)
        raise
    finally:
        record["finished_at"] = now().isoformat()
        if record_out:
            _write_record(record_out, record)


def _counter_snapshot(counters):
    """A JSON-safe snapshot of the counters."""
    return {
        name: {
            "state": data.get("state"),
            "value": data.get("value"),
            "dimensions": data.get("dimensions"),
        }
        for name, data in counters.items()
    }


def _counter_value(counters, name):
    """The value of one counter (0.0 when it was not published)."""
    return float((counters.get(name) or {}).get("value") or 0.0)


def _blocked_delta(before, after):
    """How much the WAF's BlockedRequests counter moved."""
    return _counter_value(after, BLOCKED_REQUESTS) - _counter_value(
        before, BLOCKED_REQUESTS
    )


def _allowed_delta(before, after):
    """How much the WAF's AllowedRequests counter moved."""
    return _counter_value(after, ALLOWED_REQUESTS) - _counter_value(
        before, ALLOWED_REQUESTS
    )


def _counters_published(counters, moment):
    """True when the counters cover the minute the burst ended in."""
    for data in counters.values():
        if _covers_window(data.get("datapoints") or [], moment):
            return True
    return False


def _check_origin_side(
    client_factory,
    app_stack_name,
    app_region,
    baseline_start,
    burst_ended_at,
    metric_wait_seconds,
    metric_poll_seconds,
    sleep,
    allow_missing_origin_metric,
    answered,
    expect_no_block=False,
):
    """Read the Lambda Invocations counter and judge it.

    The invocations attributable to the burst window must NOT exceed the
    number of answered responses by more than the documented slack: the
    requests the edge refused must NOT appear as origin invocations.
    """
    slack = _origin_slack(answered)
    app_cfn = client_factory("cloudformation", region_name=app_region)
    app_cloudwatch = client_factory("cloudwatch", region_name=app_region)
    stack = gate_stack_live(app_cfn, app_stack_name, app_region)
    function_name = _function_name(
        _stack_outputs(stack), app_cfn, app_stack_name
    )
    dimensions = [{"Name": "FunctionName", "Value": function_name}]
    print(
        "PASS: the origin-side counter is the invocations of {}".format(
            function_name
        )
    )

    def read():
        return _read_counter(
            app_cloudwatch,
            LAMBDA_METRIC_NAMESPACE,
            LAMBDA_METRIC_NAME,
            dimensions,
            baseline_start,
            burst_ended_at,
        )

    def ready(result):
        return _covers_window(result.get("datapoints") or [], burst_ended_at)

    try:
        baseline = read()
        after, attempts = _poll(
            read,
            ready,
            sleep,
            metric_wait_seconds,
            metric_poll_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - reported as UNAVAILABLE
        if allow_missing_origin_metric:
            print(
                "WARNING: the origin-side counter could not be read ({}) and "
                "--allow-missing-origin-metric was passed, so the check is "
                "reported as UNAVAILABLE and NOT counted as a pass".format(
                    exc
                )
            )
            return {"status": "unavailable", "invocations": None,
                    "slack": slack}
        raise RuntimeError(
            "the origin-side counter ({} invocations of {}) could not be "
            "read: {}. A check that cannot be read is not a check that "
            "passed -- pass --allow-missing-origin-metric to accept the "
            "gap.".format(LAMBDA_METRIC_NAME, function_name, exc)
        ) from None

    if not _covers_window(after.get("datapoints") or [], burst_ended_at):
        if allow_missing_origin_metric:
            print(
                "WARNING: the origin-side counter has not been published for "
                "the burst window and --allow-missing-origin-metric was "
                "passed, so the check is reported as UNAVAILABLE and NOT "
                "counted as a pass"
            )
            return {"status": "unavailable", "invocations": None,
                    "slack": slack}
        raise RuntimeError(
            "the origin-side counter ({} invocations of {}) has not been "
            "published for the burst window; the check is UNAVAILABLE. A "
            "check that cannot be read is not a check that passed -- pass "
            "--allow-missing-origin-metric to accept the gap.".format(
                LAMBDA_METRIC_NAME, function_name
            )
        )

    invocations = float(after.get("value") or 0.0) - float(
        baseline.get("value") or 0.0
    )
    gate_origin_saw_no_blocks(
        invocations, answered, slack, expect_no_block
    )
    return {
        "status": "read",
        "invocations": invocations,
        "slack": slack,
        "attempts": attempts,
    }


def main(argv=None, client_factory=boto3.client, http_get=None, sleep=None,
         now=None):
    parser = argparse.ArgumentParser(
        description="Prove the edge rate rule refuses a request at the edge"
    )
    parser.add_argument(
        "--edge-stack-name", default=DEFAULT_EDGE_STACK_NAME
    )
    parser.add_argument("--edge-region", default=DEFAULT_EDGE_REGION)
    parser.add_argument(
        "--app-stack-name", default=DEFAULT_APP_STACK_NAME
    )
    parser.add_argument("--app-region", default=DEFAULT_APP_REGION)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="the path the burst targets (must be inside the rule's scope)",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=DEFAULT_REQUESTS,
        help="the hard cap on the number of requests the probe may send",
    )
    parser.add_argument(
        "--rate",
        dest="rate_per_second",
        type=float,
        default=DEFAULT_RATE_PER_SECOND,
        help="requests per second",
    )
    parser.add_argument(
        "--confirm-blocked-count",
        type=int,
        default=DEFAULT_CONFIRM_BLOCKED_COUNT,
        help="how many blocked responses must follow the first one",
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--metric-wait-seconds",
        type=float,
        default=DEFAULT_METRIC_WAIT_SECONDS,
        help="how long to wait for the counters to be published",
    )
    parser.add_argument(
        "--metric-poll-seconds",
        type=float,
        default=METRIC_POLL_SECONDS,
        help="how often the counters are polled while waiting",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required: this sends real traffic to the live site",
    )
    parser.add_argument(
        "--allow-expensive-path",
        action="store_true",
        help="allow the CPU-bound equilibrium solver path",
    )
    parser.add_argument(
        "--allow-missing-origin-metric",
        action="store_true",
        help="report the origin-side check as UNAVAILABLE instead of failing",
    )
    parser.add_argument(
        "--expect-no-block",
        action="store_true",
        help="dry run: the rule is in Count mode and NO edge block may occur",
    )
    parser.add_argument(
        "--record-out",
        default=None,
        help="write the JSON probe record to this path",
    )
    args = parser.parse_args(argv)

    try:
        return run(
            edge_stack_name=args.edge_stack_name,
            edge_region=args.edge_region,
            app_stack_name=args.app_stack_name,
            app_region=args.app_region,
            site_url=args.site_url,
            path=args.path,
            requests=args.requests,
            rate_per_second=args.rate_per_second,
            confirm_blocked_count=args.confirm_blocked_count,
            timeout_seconds=args.timeout_seconds,
            metric_wait_seconds=args.metric_wait_seconds,
            metric_poll_seconds=args.metric_poll_seconds,
            confirm_live=args.confirm_live,
            allow_expensive_path=args.allow_expensive_path,
            allow_missing_origin_metric=args.allow_missing_origin_metric,
            expect_no_block=args.expect_no_block,
            record_out=args.record_out,
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
