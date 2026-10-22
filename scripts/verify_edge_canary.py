#!/usr/bin/env python3
"""Read-only observer for the edge rate rule's Count window (M007/S05/T01).

The edge rate limit lives in its own stack (``chemically-edge`` in
us-east-1) because AWS accepts WAFv2 web ACLs with ``Scope: CLOUDFRONT``
only in us-east-1, while the application stack deploys to us-east-2
(D015/D018). The rule ships in ``Count`` mode first: it measures the
traffic against the inherited 2000/300s ceiling without refusing a single
request. A canary that cannot be READ is a canary that proves nothing, so
this script is the observer that turns "the rule is counting" into numbers
a budget review can decide on.

What it reads:

* the LIVE edge stack (status + ``WebACLArn`` output);
* the LIVE web ACL (``wafv2:GetWebACL``) -- the rule's action, limit and
  evaluation window as ENFORCED, not the stack parameter that merely
  REQUESTED them. A declared control is not a live one;
* the WAF's own counters (``AllowedRequests`` / ``CountedRequests`` /
  ``BlockedRequests``) in ``AWS/WAFV2``, with the dimension set DISCOVERED
  through ``cloudwatch:ListMetrics`` and never guessed;
* the ordinary site, which must still serve 200 (the "no legitimate
  request was refused" observation, taken live rather than assumed).

Three states are reported per counter and they are NOT interchangeable:

* ``value`` -- the metric exists and has datapoints in the window;
* ``observed-zero`` -- the metric exists but has no datapoint in the
  window (nothing was counted);
* ``unknown`` -- the metric was not published for this web ACL at all.
  This is NOT zero: it means we are not looking at the right metric.

The WINDOW as a whole has its own state, and it is the state that keeps
"no data is not zero traffic" honest:

* ``observed`` -- the window holds at least one datapoint;
* ``warming_up`` -- the window holds NO datapoint and the rule is YOUNGER
  than the window, so there is nothing to observe yet. The edge rule is
  CREATED by the very deploy that runs this observer, so the first run
  legitimately observes nothing; that run is healthy and exits 0. The
  traffic figures stay ``null`` -- never 0 -- so no downstream reader can
  mistake "nothing observed" for "no traffic";
* ``no_observation`` -- the window holds NO datapoint and the rule was
  old enough to have been observed. An empty window on a live rule is a
  real problem and fails the run.

Everything here is read-only. It calls ``cloudformation:DescribeStacks``,
``wafv2:GetWebACL``, ``cloudwatch:ListMetrics`` and
``cloudwatch:GetMetricStatistics``, and it issues ONE HTTP GET. Nothing is
created, changed or deleted.

AWS API failures are RAISED, never swallowed: a throttling error or a lost
connection must never be mistaken for a green check. Exit codes: 0 = every
gate passed, 1 = a gate failed (or an AWS error surfaced).

``run``/``main`` take an injectable ``client_factory``, ``http_get`` and
``now`` so the unit tests exercise the full control flow with fake clients
instead of reaching AWS.
"""

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

#: The CloudWatch namespace the WAF publishes its counters into.
WAF_METRIC_NAMESPACE = "AWS/WAFV2"

#: The rule this observer is about (the edge template's rule name).
RULE_NAME = "EdgeRateLimit"

#: The conventional name of the web ACL's own metric (reported so an
#: operator can find the ACL's metrics in the console).
ACL_METRIC_NAME = "ChemicAllyEdgeWebAcl"

#: The three counters the WAF publishes for a rule.
ALLOWED_REQUESTS = "AllowedRequests"
COUNTED_REQUESTS = "CountedRequests"
BLOCKED_REQUESTS = "BlockedRequests"
COUNTER_NAMES = (ALLOWED_REQUESTS, COUNTED_REQUESTS, BLOCKED_REQUESTS)

#: CloudFront is a global service; its API endpoint lives in us-east-1.
CLOUDFRONT_REGION = "us-east-1"

#: The observation window: one day of counting by default.
DEFAULT_WINDOW_MINUTES = 1440

#: The public site, probed for a 200.
DEFAULT_SITE_URL = "https://chemic-ally.xyz"

#: The edge stack the deploy job creates.
DEFAULT_EDGE_STACK_NAME = "chemically-edge"
DEFAULT_EDGE_REGION = CLOUDFRONT_REGION

#: The HTTP timeout of the single site probe.
DEFAULT_TIMEOUT_SECONDS = 30

#: The action the edge template allows.
VALID_ACTIONS = {"Count", "Block"}

#: The evaluation windows AWS WAFv2 accepts for a rate-based rule.
VALID_EVALUATION_WINDOWS = (60, 120, 300, 600)

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

#: The three states a counter can be in. They are mutually exclusive and
#: none of them may be reported as another one.
STATE_VALUE = "value"
STATE_OBSERVED_ZERO = "observed-zero"
STATE_UNKNOWN = "unknown"

#: The states the WINDOW as a whole can be in.
OBSERVATION_STATE_OBSERVED = "observed"
OBSERVATION_STATE_WARMING_UP = "warming_up"
OBSERVATION_STATE_NO_OBSERVATION = "no_observation"

#: The message a young rule with an empty window produces.
WARMING_UP_MESSAGE = (
    "the rule is younger than the window; no observation is expected yet"
)

#: The message an empty window produces. "No data" is not "zero traffic".
NO_OBSERVATIONS_MESSAGE = (
    "the canary window contains no observations -- no data is not zero "
    "traffic"
)


def _stack_outputs(stack):
    """Return the stack's outputs as a dict."""
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


def _as_utc_datetime(value):
    """Coerce a CloudFormation timestamp into an aware datetime (or None).

    CloudFormation returns ``CreationTimestamp`` as a datetime, but a
    caller (a test, a future API shape) may hand over an ISO-8601 string.
    A naive value is read as UTC, which is what CloudFormation means.
    An unreadable value returns None: the caller then refuses to relax
    anything on the strength of a timestamp it could not read.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _http_get(url, timeout):
    """GET ``url`` and return ``(status, body_text)``.

    HTTP error responses are returned rather than raised so their status
    can be asserted; transport failures become a RuntimeError.
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


# --------------------------------------------------------------------------
# The gates. Each one is a named function so it can be pinned by a test.
# --------------------------------------------------------------------------


def _is_missing_stack(exc):
    """True when an AWS error means "that stack does not exist".

    This is the ONE documented fallback: a stack that was never deployed
    is a loud, explained failure. Every other AWS error (throttling, a
    lost connection, a denied permission) is raised unchanged, because a
    transport failure must never be mistaken for a green check.
    """
    response = getattr(exc, "response", None) or {}
    code = response.get("ErrorCode")
    if code in ("ResourceNotFound", "StackNotFoundException"):
        return True
    return code == "ValidationError" and "does not exist" in str(exc)


def gate_stack_live(cfn_client, stack_name, region):
    """G1: the edge stack exists and describes the live deployment."""
    try:
        response = cfn_client.describe_stacks(StackName=stack_name)
    except Exception as exc:
        if _is_missing_stack(exc):
            raise RuntimeError(
                "the edge stack {!r} does not exist in {}; the edge control "
                "is not deployed, so there is nothing to observe".format(
                    stack_name, region
                )
            ) from None
        raise
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
            "the edge counters cannot be read. Recover the stack first (see "
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
            "rolled back; what is observed below belongs to the last GOOD "
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
            "observed"
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


def _action_name(rule):
    """Return the live rule's action as a name.

    The WAFv2 API reports a rule's action as a STRUCTURE - {"Count": {}},
    {"Block": {}} - not as the bare name, so the name has to be read out
    of the single key it carries.
    """
    action = rule.get("Action")
    if isinstance(action, dict):
        keys = [key for key in action if key]
        if len(keys) != 1:
            raise RuntimeError(
                "the live rule reports the action {!r}, which does not "
                "name exactly one mode".format(action)
            )
        return keys[0]
    return action


def gate_action_matches(rule, expect_action):
    """G3: the live action is reportable and matches what was declared."""
    action = _action_name(rule)
    if action not in VALID_ACTIONS:
        raise RuntimeError(
            "the live rule reports the action {!r}, which is not one of "
            "{}; the live mode cannot be reported".format(
                action, sorted(VALID_ACTIONS)
            )
        )
    if expect_action and action != expect_action:
        raise RuntimeError(
            "the live rule is in {0!r} mode but the deploy declared {1!r}; "
            "the switch did not take effect".format(action, expect_action)
        )
    print(
        "PASS: the live rule action is {} (read from the LIVE web ACL)".format(
            action
        )
    )
    return action


def _rate_based_statement(rule):
    """Return the rule's rate-based statement.

    The WAFv2 API nests it: ``Statement: {RateBasedStatement: {...}}``. A
    reader that looks for the statement at the rule's top level finds
    nothing and reports a missing ceiling for a rule that has one.
    """
    statement = rule.get("Statement")
    if isinstance(statement, dict):
        nested = statement.get("RateBasedStatement")
        if isinstance(nested, dict):
            return nested
    return rule.get("RateBasedStatement") or {}


def gate_limit_and_window(rule):
    """G4: the live limit and evaluation window are reportable and sane."""
    statement = _rate_based_statement(rule)
    limit = statement.get("Limit")
    if limit is None:
        raise RuntimeError(
            "the live rule reports no rate limit; the ceiling the traffic "
            "is measured against cannot be reported"
        )
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
    window = statement.get("EvaluationWindowSec")
    try:
        window_value = int(window)
    except (TypeError, ValueError):
        raise RuntimeError(
            "the live rule reports the evaluation window {!r}, which is not "
            "a number".format(window)
        ) from None
    if window_value not in VALID_EVALUATION_WINDOWS:
        raise RuntimeError(
            "the live rule reports the evaluation window {!r}, which is not "
            "one of {}".format(window, list(VALID_EVALUATION_WINDOWS))
        )
    print(
        "PASS: the live ceiling is {}/{}s".format(limit_value, window_value)
    )
    return limit_value, window_value


def gate_counters_readable(discovered_names, metrics, require_data=True):
    """G5: the counters could be discovered and read."""
    discovered = [name for name in COUNTER_NAMES if name in discovered_names]
    if not discovered:
        raise RuntimeError(
            "the {} namespace publishes none of the counters {} for this "
            "web ACL; we are not looking at the right metric".format(
                WAF_METRIC_NAMESPACE, list(COUNTER_NAMES)
            )
        )
    if require_data:
        with_data = [
            name for name in discovered if metrics[name]["state"] == STATE_VALUE
        ]
        if not with_data:
            raise RuntimeError(NO_OBSERVATIONS_MESSAGE)
    return discovered


def gate_no_blocks_in_count_mode(action, metrics):
    """G6: in Count mode the rule cannot have refused anything."""
    if action != "Count":
        print(
            "NOTE: the rule is in {} mode; the BlockedRequests counter is "
            "expected to move".format(action)
        )
        return
    blocked = metrics[BLOCKED_REQUESTS]
    value = blocked.get("value")
    if value is not None and value > 0:
        raise RuntimeError(
            "the rule is in Count mode but BlockedRequests is {}; a Count "
            "rule cannot refuse, so SOMETHING ELSE refused that traffic and "
            "it must be surfaced before the canary is trusted".format(value)
        )
    print("PASS: BlockedRequests is 0 in Count mode (nothing was refused)")


def gate_allowed_requests_positive(action, metrics, require_data=True):
    """G7: in Count mode real traffic must have flowed through the scope."""
    if action != "Count":
        print(
            "NOTE: the rule is in {} mode; the scoped-traffic assertion is "
            "not applied".format(action)
        )
        return
    allowed = metrics[ALLOWED_REQUESTS]
    if allowed.get("state") == STATE_UNKNOWN:
        raise RuntimeError(
            "the {} counter was not published for this web ACL; the rule's "
            "scope-down cannot be proven to match a live path".format(
                ALLOWED_REQUESTS
            )
        )
    value = allowed.get("value") or 0
    if value <= 0:
        if not require_data and allowed.get("state") == STATE_OBSERVED_ZERO:
            print(
                "NOTE: the window holds no {} datapoints and the relaxed "
                "mode was requested; the scope-down is NOT proven".format(
                    ALLOWED_REQUESTS
                )
            )
            return
        raise RuntimeError(
            "{} is 0 in the window: no traffic matched the rule's "
            "scope-down, so the canary proves nothing about the live "
            "paths".format(ALLOWED_REQUESTS)
        )
    print(
        "PASS: {} is {} -- real traffic flowed through the scoped "
        "paths".format(ALLOWED_REQUESTS, value)
    )


def gate_site_serves_200(status):
    """G8: the ordinary site still serves 200."""
    if status != 200:
        raise RuntimeError(
            "the public site returned status={} instead of 200; a "
            "legitimate request would be refused".format(status)
        )
    print("PASS: the public site serves 200 (no legitimate request refused)")


def gate_ceiling_reported(action, metrics, record):
    """G9: a ceiling breach is reported loudly, and it does NOT fail."""
    counted = metrics[COUNTED_REQUESTS]
    value = counted.get("value")
    if value is not None and value > 0:
        record["ceiling_reached"] = True
        print(
            "WARNING: the ceiling was reached -- CountedRequests is {} in "
            "the window, so at least one source address hit the limit and "
            "those requests WOULD have been refused in Block mode. The "
            "decision about the ceiling belongs to the budget review, so "
            "this does NOT fail the run.".format(value)
        )
        return True
    record["ceiling_reached"] = False
    print(
        "PASS: the ceiling was not reached in this window "
        "(CountedRequests={})".format(
            "unknown" if value is None else value
        )
    )
    return False


def gate_window_covers_the_rule(stack_created_at, window_start):
    """G10: could the rule have been observed for the WHOLE window?

    The edge rule is CREATED by the very deploy that runs this observer,
    so on that run the observation window legitimately holds no datapoint.
    The rule's age comes from the edge stack's ``CreationTimestamp`` (the
    stack that holds the rule -- it is already read by ``gate_stack_live``)
    and the window's start; the question is exact: could the rule have
    been counted for the WHOLE window?

    Returns True when it could (the rule existed at or before the window
    started) and False when it could not (the rule is younger than the
    window). An unreadable timestamp is NOT a licence to relax anything:
    the gate then returns True, so an empty window on a rule of unknown
    age stays a failure.
    """
    created = _as_utc_datetime(stack_created_at)
    if created is None:
        print(
            "NOTE: the edge stack reports no readable CreationTimestamp; "
            "the rule's age cannot be established, so an empty window is "
            "NOT excused"
        )
        return True
    start = _as_utc_datetime(window_start)
    covers = created <= start
    if covers:
        print(
            "PASS: the rule existed for the whole window (created at {}, "
            "the window starts at {})".format(
                created.isoformat(), start.isoformat()
            )
        )
    else:
        print(
            "NOTE: the rule is younger than the window (created at {}, the "
            "window starts at {})".format(
                created.isoformat(), start.isoformat()
            )
        )
    return covers


# --------------------------------------------------------------------------
# Reading the counters
# --------------------------------------------------------------------------


def _get_web_acl(waf_client, web_acl_arn):
    """Read the live web ACL. AWS errors are raised, never swallowed."""
    # The WAFv2 GetWebACL request parameter is ARN (not WebACLArn):
    # passing WebACLArn is a parameter validation error in botocore.
    response = waf_client.get_web_acl(ARN=web_acl_arn)
    web_acl = (response or {}).get("WebACL") or {}
    return web_acl


def _discover_metrics(cloudwatch_client, web_acl_arn):
    """Discover the metrics AWS publishes for this web ACL.

    The dimension set is DISCOVERED, never guessed: AWS publishes the WAF
    metrics of a CloudFront-scoped web ACL with a ``Region`` dimension
    among others, and a guessed dimension set silently returns no data.
    """
    response = cloudwatch_client.list_metrics(
        Namespace=WAF_METRIC_NAMESPACE,
        Dimensions=[{"Name": "WebACL", "Value": web_acl_arn}],
    )
    return list((response or {}).get("Metrics") or [])


def _read_counters(cloudwatch_client, discovered, start_time, end_time, period):
    """Read the three counters, each into exactly one of the three states."""
    metrics = {}
    for name in COUNTER_NAMES:
        entries = [
            entry
            for entry in discovered
            if entry.get("MetricName") == name
        ]
        if not entries:
            metrics[name] = {
                "metric_name": name,
                "state": STATE_UNKNOWN,
                "value": None,
                "dimensions": [],
                "datapoints": 0,
            }
            continue
        dimensions = list(entries[0].get("Dimensions") or [])
        total = 0.0
        datapoint_count = 0
        for entry in entries:
            entry_dimensions = list(entry.get("Dimensions") or [])
            response = cloudwatch_client.get_metric_statistics(
                Namespace=WAF_METRIC_NAMESPACE,
                MetricName=name,
                Dimensions=entry_dimensions,
                Statistics=["Sum"],
                Period=period,
                StartTime=start_time,
                EndTime=end_time,
            )
            datapoints = (response or {}).get("Datapoints") or []
            datapoint_count += len(datapoints)
            for datapoint in datapoints:
                total += float(datapoint.get("Sum") or 0)
        metrics[name] = {
            "metric_name": name,
            "state": STATE_VALUE if datapoint_count else STATE_OBSERVED_ZERO,
            "value": total,
            "dimensions": dimensions,
            "datapoints": datapoint_count,
        }
    return metrics


def _scoped_requests(metrics):
    """The requests the rule's scope-down matched."""
    total = 0
    for name in (ALLOWED_REQUESTS, COUNTED_REQUESTS):
        value = metrics[name].get("value")
        if value is not None:
            total += value
    return total


def _window_has_observation(discovered_names, metrics):
    """True when the window holds at least one real datapoint.

    A counter in the ``observed-zero`` state holds NO datapoint (nothing
    was counted), so it is not an observation; only the ``value`` state is.
    """
    for name in COUNTER_NAMES:
        if name not in discovered_names:
            continue
        if (metrics.get(name) or {}).get("state") == STATE_VALUE:
            return True
    return False


def _windows(window_minutes, evaluation_window_seconds):
    """How many evaluation windows the observation window holds."""
    return max(
        1,
        int(
            math.floor(
                float(window_minutes) * 60.0
                / float(evaluation_window_seconds)
            )
        ),
    )


def _write_record(path, record):
    """Write the JSON record (the evidence the budget review reads)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")


def run(
    edge_stack_name=DEFAULT_EDGE_STACK_NAME,
    edge_region=DEFAULT_EDGE_REGION,
    site_url=DEFAULT_SITE_URL,
    expect_action="Count",
    window_minutes=DEFAULT_WINDOW_MINUTES,
    require_data=True,
    record_out=None,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    *,
    client_factory=boto3.client,
    http_get=None,
    now=None,
):
    """Observe the live edge canary window; return 0 or raise RuntimeError."""
    http_get = http_get if http_get is not None else _http_get
    now = now if now is not None else (lambda: datetime.now(timezone.utc))

    observed_at = now()
    window_end = observed_at
    window_start = observed_at - timedelta(minutes=window_minutes)

    record = {
        "edge_stack_name": edge_stack_name,
        "edge_region": edge_region,
        "site_url": site_url,
        "expect_action": expect_action,
        "require_data": require_data,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_minutes": window_minutes,
        "action": None,
        "limit": None,
        "evaluation_window_seconds": None,
        "metrics": {},
        "discovered_metric_names": [],
        "scoped_requests": None,
        "observed_rate_per_window": None,
        "observation_state": None,
        "observation_state_reason": None,
        "stack_created_at": None,
        "ceiling_reached": None,
        "site_status": None,
        "status": "running",
    }

    try:
        cfn_client = client_factory("cloudformation", region_name=edge_region)
        waf_client = client_factory("wafv2", region_name=edge_region)
        cloudwatch_client = client_factory("cloudwatch", region_name=edge_region)

        stack = gate_stack_live(cfn_client, edge_stack_name, edge_region)
        outputs = _stack_outputs(stack)
        web_acl_arn = outputs.get("WebACLArn")
        if not web_acl_arn:
            raise RuntimeError(
                "stack {!r} does not export a WebACLArn output; the edge "
                "stack drifted from the deploy contract and the live web "
                "ACL cannot be identified".format(edge_stack_name)
            )
        record["web_acl_arn"] = web_acl_arn

        web_acl = _get_web_acl(waf_client, web_acl_arn)
        rule = gate_web_acl_holds_rule(web_acl)
        action = gate_action_matches(rule, expect_action)
        limit, evaluation_window = gate_limit_and_window(rule)
        record["action"] = action
        record["limit"] = limit
        record["evaluation_window_seconds"] = evaluation_window

        discovered = _discover_metrics(cloudwatch_client, web_acl_arn)
        record["acl_metric_name"] = ACL_METRIC_NAME
        discovered_names = sorted(
            {entry.get("MetricName") for entry in discovered if entry.get(
                "MetricName"
            )}
        )
        record["discovered_metric_names"] = discovered_names
        print(
            "PASS: the {} namespace publishes {} metric(s) for this web "
            "ACL: {}".format(
                WAF_METRIC_NAMESPACE, len(discovered_names), discovered_names
            )
        )
        if ACL_METRIC_NAME in discovered_names:
            print(
                "PASS: the ACL's own metric {} is published for this web "
                "ACL".format(ACL_METRIC_NAME)
            )
        else:
            print(
                "NOTE: the ACL's own metric {} was not published for this "
                "web ACL".format(ACL_METRIC_NAME)
            )

        metrics = _read_counters(
            cloudwatch_client,
            discovered,
            window_start,
            window_end,
            evaluation_window,
        )
        record["metrics"] = metrics

        # The WINDOW's own state. A window with no datapoint at all is
        # only benign when the rule is YOUNGER than the window: the deploy
        # that CREATES the rule is the deploy that runs this observer, so
        # the first run legitimately observes nothing.
        created = _as_utc_datetime(stack.get("CreationTimestamp"))
        record["stack_created_at"] = (
            created.isoformat() if created is not None else None
        )
        has_observation = _window_has_observation(discovered_names, metrics)
        warming_up = False
        if has_observation:
            record["observation_state"] = OBSERVATION_STATE_OBSERVED
            gate_counters_readable(discovered_names, metrics, require_data)
        else:
            covers_window = gate_window_covers_the_rule(
                stack.get("CreationTimestamp"), window_start
            )
            if not discovered_names and not covers_window:
                # The rule is YOUNGER than the window and has never been
                # evaluated, so CloudWatch has not published a single
                # counter for it yet. That is the warm-up state, not a
                # misread: a rule that has never run cannot have
                # published counters.
                warming_up = True
                record["observation_state"] = OBSERVATION_STATE_WARMING_UP
                record["observation_state_reason"] = (
                    "the rule was created at {}, after the window started "
                    "at {}, and no counter has been published for it yet, so "
                    "the canary window has no data yet".format(
                        record["stack_created_at"], window_start.isoformat()
                    )
                )
                print(WARMING_UP_MESSAGE)
            else:
                # A counter that was never published is a misread, not a
                # warm-up: that failure stands in every other state.
                gate_counters_readable(discovered_names, metrics, False)
            if covers_window and not warming_up:
                record["observation_state"] = OBSERVATION_STATE_NO_OBSERVATION
                record["observation_state_reason"] = (
                    "the window holds no datapoint and the rule existed for "
                    "the whole window, so an empty window is a real problem"
                )
                if require_data:
                    raise RuntimeError(NO_OBSERVATIONS_MESSAGE)
            else:
                warming_up = True
                record["observation_state"] = OBSERVATION_STATE_WARMING_UP
                record["observation_state_reason"] = (
                    "the edge stack was created at {}, after the window "
                    "started at {}, so the rule is younger than the window "
                    "and could not have been observed for the whole "
                    "window".format(
                        record["stack_created_at"], window_start.isoformat()
                    )
                )
                print(WARMING_UP_MESSAGE)

        if has_observation:
            gate_no_blocks_in_count_mode(action, metrics)
            gate_allowed_requests_positive(action, metrics, require_data)
        elif warming_up:
            print(
                "NOTE: the scoped-traffic assertions are not applied while "
                "the rule is warming up: no traffic can have been counted "
                "yet"
            )
        else:
            print(
                "NOTE: the window holds no observation; the scope-down is "
                "NOT proven"
            )

        if has_observation:
            scoped = _scoped_requests(metrics)
            windows = _windows(window_minutes, evaluation_window)
            record["scoped_requests"] = scoped
            record["observed_rate_per_window"] = scoped / float(windows)
            print(
                "PASS: {} scoped request(s) over {} window(s) -> {:.2f} "
                "request(s) per {}s window".format(
                    scoped, windows, record["observed_rate_per_window"],
                    evaluation_window
                )
            )
        else:
            # No data is not zero traffic: the figures stay null, so no
            # downstream reader can mistake "nothing observed" for "no
            # traffic".
            print(
                "NOTE: the window holds no observation, so no traffic figure "
                "is recorded (null, never 0: no data is not zero traffic)"
            )

        site_status, _ = http_get(site_url, timeout_seconds)
        record["site_status"] = site_status
        gate_site_serves_200(site_status)

        if has_observation:
            gate_ceiling_reported(action, metrics, record)
        else:
            record["ceiling_reached"] = None
            print(
                "NOTE: the ceiling could not be observed in this window; no "
                "conclusion about it is recorded"
            )

        record["status"] = "pass"
        return 0
    except Exception as exc:  # noqa: BLE001 - re-raised below
        record["status"] = "fail"
        record["error"] = str(exc)
        raise
    finally:
        record["observed_at"] = observed_at.isoformat()
        if record_out:
            _write_record(record_out, record)


def main(argv=None, client_factory=boto3.client, http_get=None, now=None):
    parser = argparse.ArgumentParser(
        description="Observe the live edge rate rule's Count window"
    )
    parser.add_argument(
        "--edge-stack-name", default=DEFAULT_EDGE_STACK_NAME
    )
    parser.add_argument("--edge-region", default=DEFAULT_EDGE_REGION)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument(
        "--expect-action",
        default="Count",
        help="the live action the deploy declared (Count during the canary)",
    )
    parser.add_argument(
        "--minutes",
        "--window-minutes",
        dest="window_minutes",
        type=int,
        default=DEFAULT_WINDOW_MINUTES,
        help="the size of the observation window in minutes",
    )
    parser.add_argument(
        "--require-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fail when the window holds no observation at all",
    )
    parser.add_argument(
        "--record-out",
        default=None,
        help="write the JSON canary record to this path",
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    args = parser.parse_args(argv)

    try:
        return run(
            edge_stack_name=args.edge_stack_name,
            edge_region=args.edge_region,
            site_url=args.site_url,
            expect_action=args.expect_action,
            window_minutes=args.window_minutes,
            require_data=args.require_data,
            record_out=args.record_out,
            timeout_seconds=args.timeout_seconds,
            client_factory=client_factory,
            http_get=http_get,
            now=now,
        )
    except RuntimeError as exc:
        print("FAIL: {}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
