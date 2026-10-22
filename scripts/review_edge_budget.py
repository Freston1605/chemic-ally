#!/usr/bin/env python3
"""Budget review: the edge ceiling against the SHIPPED in-app scopes.

M007/S06/T02. The edge canary (M007/S05) produced a Count window: the
traffic the CloudFront rate rule saw, per source IP, without refusing a
single request. This script is the review that turns that observation into
a DECISION about the budgets -- and it is deliberately a report, not an
act. It changes nothing: not the edge rule, not the template, and not the
in-app throttle scopes (D021). The canary outcome is an INPUT to a later
tuning decision, not a licence to change a limit now.

Why a script and not a paragraph of numbers:

* the edge ceiling is read from the canary RECORD (the live web ACL's
  limit and evaluation window) when there is one, and from the edge
  template's declared ``WafRateLimit`` default and ``EvaluationWindowSec``
  otherwise. It is never typed in by hand;
* the in-app budgets are read from the SHIPPED Django settings
  (``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``) and parsed with DRF's OWN
  parser (``SimpleRateThrottle.parse_rate``), so the review states the
  budget DRF would actually enforce. A hard-coded "60/min" in a script
  proves nothing about production (MEM043);
* the binding analysis compares three things: the edge rule's scope-down
  prefixes (read from the template), the endpoints that carry an in-app
  throttle scope, and the in-scope paths that carry NONE. On a throttled
  endpoint the in-app scope is usually the stricter control; on an
  unthrottled in-scope path the edge ceiling is the ONLY bound;
* the observed traffic comes from the record. When there is no observation
  -- no record, a warming-up window, a window with no datapoint -- the
  traffic is recorded as ``null`` WITH THE REASON. It is never 0, and it is
  never "the site is quiet": no data is not zero traffic (the S05 canary
  rule).

Nothing here reaches AWS. The script reads the template, the record and
the shipped settings, and writes one JSON review record. It is
READ-ONLY by construction: there is no AWS client, no network call and no
write to anything but the ``--review-out`` file.

Usage::

    python scripts/review_edge_budget.py \\
        --record edge-canary-count-record.json \\
        --review-out edge-budget-review.json

Exit codes: 0 = the review record was produced, 1 = the review could not
be produced (a loud, explained failure).
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.core.exceptions import ImproperlyConfigured

#: The repository root: ``scripts/`` lives one level below it, and the
#: shipped settings and the edge template are read from it.
REPO_ROOT = Path(__file__).resolve().parents[1]

# The script lives in ``scripts/``, so the repository root is not on the
# path when it is run as a command: the shipped settings module would not
# be importable, and DRF's throttle classes cannot even be imported.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The edge template, relative to the repository root.
DEFAULT_EDGE_TEMPLATE = "edge-template.yaml"

#: The settings module the shipped in-app budgets are read from.
DEFAULT_SETTINGS_MODULE = "config.settings.development"

#: The paths the review MUST protect: the HPLC API (the simulation is
#: CPU-bound and the score submission writes a row) and the equilibria
#: calculator (the solver path). These are the paths the abuse ceiling is
#: FOR, and the review checks the edge rule's scope-down against them --
#: which is why the protected set is declared HERE and not derived from the
#: template: a template that quietly drops a prefix must show up as an
#: unbounded path, not as a smaller review.
PROTECTED_PATH_PREFIXES = ("/hplc/api/", "/calculate/equilibria")

#: The paths the review names explicitly. They are the plan's paths, and
#: the review reports whether the shipped app still serves them.
REQUIRED_REVIEW_PATHS = (
    "/hplc/api/simulate/",
    "/hplc/api/scores/",
    "/hplc/api/levels/",
    "/hplc/api/progress/",
    "/calculate/equilibria",
)

#: The in-app throttle scopes and the environment variables the deployed
#: environment uses to override them (config/settings/production.py).
SCOPE_OVERRIDE_ENV_VARS = {
    "simulate": "THROTTLE_RATE_SIMULATE",
    "scores": "THROTTLE_RATE_SCORES",
}

#: The acceptance pattern production.py applies to an override. A value
#: DRF can parse but production refuses at boot is a deployment that would
#: never start, so the review refuses it too.
PRODUCTION_RATE_PATTERN = re.compile(r"[0-9]+/[0-9]*[smhd][a-z]*")

#: The states the canary record can be in (M007/S05/T01).
OBSERVATION_OBSERVED = "observed"
OBSERVATION_WARMING_UP = "warming_up"
OBSERVATION_NO_OBSERVATION = "no_observation"


class ReviewError(RuntimeError):
    """A loud, explained failure of the review itself."""


#: DRF's throttle base, built on first use (see ``_rate_parser_class``).
_PARSER_CLASS = None


def _rate_parser_class():
    """DRF's own throttle base, with the scope bound.

    DRF parses a throttle rate lazily, at request time, inside
    ``SimpleRateThrottle.parse_rate``. The review uses THAT parser, with
    the scope bound and the rates handed in, so a budget in the review is
    exactly the budget DRF would enforce -- including its refusal to
    understand a malformed rate.

    The import is LAZY on purpose: DRF evaluates
    ``api_settings.DEFAULT_THROTTLE_RATES`` while its throttle classes
    are defined, so importing them needs the Django settings to be
    configured and the settings module to be importable. The review
    therefore imports them only after ``django.setup()``.
    """
    global _PARSER_CLASS
    if _PARSER_CLASS is None:
        from rest_framework.throttling import ScopedRateThrottle

        class _ScopeRateParser(ScopedRateThrottle):
            """DRF's rate parser with the scope bound to one instance."""

            def __init__(self, scope, rates):
                self.scope = scope
                self._rates = rates

            @property
            def THROTTLE_RATES(self):
                return self._rates

        _PARSER_CLASS = _ScopeRateParser
    return _PARSER_CLASS


# ---------------------------------------------------------------------------
# The edge template (read, never assumed)
# ---------------------------------------------------------------------------


def resolve_path(candidate):
    """Resolve a path argument against the CWD and then the repo root."""
    path = Path(candidate)
    if path.exists():
        return path
    return REPO_ROOT / candidate


#: The CloudFormation statement types the scope-down is built from.
STATEMENT_TYPES = (
    "ByteMatchStatement",
    "AndStatement",
    "OrStatement",
    "NotStatement",
)


def _statement_parts(statement):
    """Return (type, body) for a statement in either YAML shape.

    CloudFormation accepts a statement written as ``{"Type": "...",
    ...}`` or as a single-key mapping (``{"ByteMatchStatement": {...}}``).
    The edge template uses the second shape, and the review reads both.
    """
    if not isinstance(statement, dict):
        return None, {}
    declared = statement.get("Type")
    if isinstance(declared, str):
        return declared, statement
    for key in STATEMENT_TYPES:
        if isinstance(statement.get(key), dict):
            return key, statement[key]
    return None, {}


def _collect_search_strings(statement, found):
    """Collect every UriPath SearchString in a scope-down statement."""
    if not isinstance(statement, dict):
        return found
    type_name, body = _statement_parts(statement)
    if type_name == "ByteMatchStatement":
        field = body.get("FieldToMatch") or {}
        search = body.get("SearchString")
        if isinstance(field, dict) and "UriPath" in field:
            if isinstance(search, str):
                found.append(search)
    for key in ("AndStatement", "OrStatement", "NotStatement"):
        nested = statement.get(key)
        if isinstance(nested, dict):
            for child in nested.get("Statements") or []:
                _collect_search_strings(child, found)
    return found


def read_edge_template(path):
    """Read the DECLARED ceiling and scope-down prefixes from the template.

    ``edge-template.yaml`` deliberately uses long-form intrinsics, so a
    plain YAML loader can read it (``template.yaml`` uses short tags and
    cannot be parsed this way -- do not try).
    """
    path = resolve_path(path)
    if not path.exists():
        raise ReviewError(
            "the edge template {!r} does not exist; the review cannot "
            "state the declared ceiling".format(str(path))
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ReviewError(
            "the edge template {!r} is not readable YAML: {}".format(
                str(path), exc
            )
        ) from exc
    if not isinstance(document, dict):
        raise ReviewError(
            "the edge template {!r} does not describe a stack".format(
                str(path)
            )
        )

    parameters = document.get("Parameters") or {}
    rate_limit = parameters.get("WafRateLimit") or {}
    limit = rate_limit.get("Default")
    action = (parameters.get("WafAction") or {}).get("Default")

    statement = {}
    for resource in (document.get("Resources") or {}).values():
        if not isinstance(resource, dict):
            continue
        if resource.get("Type") != "AWS::WAFv2::WebACL":
            continue
        for rule in resource.get("Properties", {}).get("Rules") or []:
            candidate = (rule.get("Statement") or {}).get(
                "RateBasedStatement"
            )
            if candidate:
                statement = candidate
                break
        if statement:
            break

    window = statement.get("EvaluationWindowSec")
    prefixes = _collect_search_strings(
        statement.get("ScopeDownStatement") or {}, []
    )
    return {
        "path": str(path),
        "limit": limit,
        "evaluation_window_seconds": window,
        "action": action,
        "scope_down_prefixes": prefixes,
    }


def _usable_int(value):
    """Return *value* when it is a usable positive integer, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def ceiling_from_template(template):
    """The declared ceiling, or a loud failure when it cannot be read."""
    limit = _usable_int(template.get("limit"))
    window = _usable_int(template.get("evaluation_window_seconds"))
    problems = []
    if limit is None:
        problems.append(
            "the edge template declares no usable WafRateLimit default "
            "({!r})".format(template.get("limit"))
        )
    if window is None:
        problems.append(
            "the edge template declares no usable EvaluationWindowSec "
            "({!r})".format(template.get("evaluation_window_seconds"))
        )
    if problems:
        raise ReviewError(
            "the declared edge ceiling cannot be read: {}".format(
                "; ".join(problems)
            )
        )
    return limit, window


# ---------------------------------------------------------------------------
# The canary record
# ---------------------------------------------------------------------------


def load_record(path):
    """Read the canary count record. Returns the record dict or None."""
    if path is None:
        return None
    path = resolve_path(path)
    if not path.exists():
        raise ReviewError(
            "the canary record {!r} does not exist".format(str(path))
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ReviewError(
            "the canary record {!r} is not valid JSON: {}".format(
                str(path), exc
            )
        ) from exc
    except OSError as exc:
        raise ReviewError(
            "the canary record {!r} could not be read: {}".format(
                str(path), exc
            )
        ) from exc
    if not isinstance(data, dict):
        raise ReviewError(
            "the canary record {!r} is not a JSON object".format(str(path))
        )
    return data


def ceiling_from_record(record):
    """The ceiling the record states, with the problems found on the way.

    A record that states an unusable limit is NOT trusted: the review
    falls back to the template and says so, instead of inventing a
    ceiling or failing with a number nobody can check.
    """
    if record is None:
        return None, None, []
    problems = []
    limit = record.get("limit")
    window = record.get("evaluation_window_seconds")
    if limit is not None and _usable_int(limit) is None:
        problems.append(
            "the record states an unusable limit ({!r})".format(limit)
        )
        limit = None
    if window is not None and _usable_int(window) is None:
        problems.append(
            "the record states an unusable evaluation window "
            "({!r})".format(window)
        )
        window = None
    if limit is None or window is None:
        return None, None, problems
    return limit, window, problems


def observed_traffic(record):
    """The observed traffic, or ``null`` WITH THE REASON.

    Never 0 for a missing observation: no data is not zero traffic.
    """
    empty = {
        "scoped_requests": None,
        "observed_rate_per_window": None,
        "observation_state": None,
        "window_start": None,
        "window_end": None,
        "window_minutes": None,
        "ceiling_reached": None,
        "reason": None,
    }
    if record is None:
        empty["reason"] = (
            "no canary count record was supplied, so the traffic is "
            "UNKNOWN, not zero"
        )
        return empty

    empty["observation_state"] = record.get("observation_state")
    empty["window_start"] = record.get("window_start")
    empty["window_end"] = record.get("window_end")
    empty["window_minutes"] = record.get("window_minutes")
    empty["ceiling_reached"] = record.get("ceiling_reached")
    state = record.get("observation_state")

    if state == OBSERVATION_OBSERVED:
        scoped = record.get("scoped_requests")
        rate = record.get("observed_rate_per_window")
        if isinstance(scoped, bool) or not isinstance(scoped, int):
            empty["reason"] = (
                "the record claims an observed window but carries no usable "
                "scoped request count ({!r}); the traffic is UNKNOWN, not "
                "zero".format(scoped)
            )
            return empty
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            empty["reason"] = (
                "the record claims an observed window but carries no usable "
                "rate per window ({!r}); the traffic is UNKNOWN, not "
                "zero".format(rate)
            )
            return empty
        empty["scoped_requests"] = scoped
        empty["observed_rate_per_window"] = float(rate)
        empty["reason"] = (
            "the window holds a datapoint: {} scoped request(s) over {} "
            "window(s)".format(scoped, empty["window_minutes"])
        )
        return empty

    stated = record.get("observation_state_reason")
    if state == OBSERVATION_WARMING_UP:
        empty["reason"] = stated or (
            "the rule is younger than the observation window, so nothing "
            "could have been observed yet; the traffic is UNKNOWN, not zero"
        )
        return empty
    if state == OBSERVATION_NO_OBSERVATION:
        empty["reason"] = stated or (
            "the window holds no datapoint on a rule old enough to have "
            "been counted; the traffic is UNKNOWN, not zero"
        )
        return empty

    empty["reason"] = (
        "the record does not state a known observation state ({!r}); the "
        "traffic is UNKNOWN, not zero".format(state)
    )
    return empty


# ---------------------------------------------------------------------------
# The shipped in-app budgets
# ---------------------------------------------------------------------------


def django_settings(settings_module=None):
    """Return the Django settings object and the module it came from."""
    module = (
        settings_module
        or os.environ.get("DJANGO_SETTINGS_MODULE")
        or DEFAULT_SETTINGS_MODULE
    )
    # The script lives in ``scripts/``, so the repository root is not on
    # the path when it is run as a command: the shipped settings module
    # would not be importable.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from django.conf import settings

    if not settings.configured:
        os.environ["DJANGO_SETTINGS_MODULE"] = module
        import django

        django.setup()
    return settings, module


def read_shipped_rates(settings_module=None):
    """Read the in-app budgets as the SHIPPED settings declare them."""
    settings, module = django_settings(settings_module)
    rest_framework = getattr(settings, "REST_FRAMEWORK", None) or {}
    rates = rest_framework.get("DEFAULT_THROTTLE_RATES")
    if not isinstance(rates, dict) or not rates:
        raise ReviewError(
            "the shipped settings ({}) declare no "
            "REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']; there is no "
            "in-app budget to review".format(module)
        )
    return dict(rates), module


def apply_overrides(rates, overrides):
    """Apply the deployed overrides; refuse what production would refuse."""
    effective = dict(rates)
    applied = {}
    for scope, override in sorted((overrides or {}).items()):
        if override is None:
            continue
        if not PRODUCTION_RATE_PATTERN.fullmatch(override):
            raise ReviewError(
                "the override for scope {!r} ({!r}) is not a shape the "
                "deployed environment accepts; production.py refuses it at "
                "boot, so a deployment with it would never start".format(
                    scope, override
                )
            )
        applied[scope] = override
        effective[scope] = override
    return effective, applied


def parse_rates(rates):
    """Parse every scope's rate with DRF's own parser."""
    budgets = {}
    parser_class = _rate_parser_class()
    for scope in sorted(rates):
        raw = rates[scope]
        parser = parser_class(scope, rates)
        try:
            parsed = parser.parse_rate(parser.get_rate())
        except ImproperlyConfigured as exc:
            raise ReviewError(
                "the scope {!r} has no rate to enforce: {}".format(scope, exc)
            ) from exc
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ReviewError(
                "the rate for scope {!r} ({!r}) cannot be parsed by DRF: "
                "{}".format(scope, raw, exc)
            ) from exc
        if not parsed or parsed[0] is None or parsed[1] is None:
            raise ReviewError(
                "the rate for scope {!r} ({!r}) is not a rate DRF can "
                "enforce".format(scope, raw)
            )
        requests, period = parsed
        budgets[scope] = {
            "rate": raw,
            "requests": requests,
            "period_seconds": period,
            "requests_per_second": requests / float(period),
            "parsed_by": "rest_framework SimpleRateThrottle.parse_rate",
        }
    return budgets


# ---------------------------------------------------------------------------
# The shipped paths
# ---------------------------------------------------------------------------


def _walk_url_patterns(resolver, prefix=""):
    """Yield (path, view) for every URL pattern the app serves."""
    from django.urls.resolvers import URLResolver

    for entry in resolver.url_patterns:
        route = str(entry.pattern)
        if route.startswith("^") and route.endswith("$"):
            route = route[1:-1]
        full = prefix + route
        if isinstance(entry, URLResolver):
            for item in _walk_url_patterns(entry, full):
                yield item
            continue
        callback = getattr(entry, "callback", None)
        view_class = getattr(callback, "view_class", None)
        scope = getattr(view_class, "throttle_scope", None)
        yield {
            "path": "/" + full,
            "view": (
                "{}.{}".format(
                    view_class.__module__, view_class.__name__
                )
                if view_class is not None
                else getattr(callback, "__name__", "unknown")
            ),
            "throttle_scope": scope,
        }


def shipped_paths():
    """The paths the shipped app serves, with the throttle scope of each.

    The scope-down of the edge rule is read from the template, but the
    PATHS are read from the shipped urlconf and the shipped views: a view
    that declares ``throttle_scope`` is throttled, a view that does not is
    not, whatever the script believes.
    """
    from django.urls import get_resolver

    paths = list(_walk_url_patterns(get_resolver()))
    seen = {}
    for entry in paths:
        literal = re.split(r"[<{]", entry["path"])[0]
        entry = dict(entry)
        entry["literal_path"] = literal
        # A dynamic segment collapses onto its literal prefix; keep the
        # first occurrence so the review does not list the same prefix
        # twice.
        seen.setdefault(literal, entry)
    return list(seen.values())


# ---------------------------------------------------------------------------
# The binding analysis
# ---------------------------------------------------------------------------


def in_edge_scope(path, prefixes):
    """True when the edge rule's scope-down covers this path."""
    return any(path.startswith(prefix) for prefix in prefixes)


def analyse_paths(paths, prefixes, budgets, edge_rate):
    """The binding analysis: what actually bounds each path."""
    entries = []
    for entry in paths:
        literal = entry["literal_path"]
        scope = entry["throttle_scope"]
        budget = budgets.get(scope) if scope else None
        protected = any(
            literal.startswith(prefix) for prefix in PROTECTED_PATH_PREFIXES
        )
        covered = in_edge_scope(literal, prefixes)
        if scope and budget is None:
            binding = "unbounded"
            reason = (
                "the view declares the throttle scope {!r} but the shipped "
                "settings declare no rate for it, so DRF would raise "
                "ImproperlyConfigured on the first request and there is NO "
                "budget".format(scope)
            )
        elif budget is not None:
            if budget["requests_per_second"] < edge_rate:
                binding = "in_app_scope"
                reason = (
                    "the in-app scope {!r} allows {:.4g} request(s)/s, which "
                    "is stricter than the edge ceiling's {:.4g} request(s)/s, "
                    "so the in-app scope is the binding control".format(
                        scope,
                        budget["requests_per_second"],
                        edge_rate,
                    )
                )
            elif budget["requests_per_second"] > edge_rate:
                binding = "edge_ceiling"
                reason = (
                    "the in-app scope {!r} allows {:.4g} request(s)/s, which "
                    "is looser than the edge ceiling's {:.4g} request(s)/s, "
                    "so the edge ceiling is the binding control".format(
                        scope,
                        budget["requests_per_second"],
                        edge_rate,
                    )
                )
            else:
                binding = "both"
                reason = (
                    "the in-app scope {!r} and the edge ceiling allow the "
                    "same {:.4g} request(s)/s, so both bind equally".format(
                        scope, edge_rate
                    )
                )
        elif covered:
            binding = "edge_ceiling"
            reason = (
                "this path carries NO in-app throttle, so the edge ceiling "
                "({:.4g} request(s)/s per source IP) is the ONLY bound on "
                "it".format(edge_rate)
            )
        else:
            binding = "none"
            reason = (
                "this path is outside the edge rule's scope-down and "
                "carries no in-app throttle; it is deliberately unbounded "
                "(ordinary browsing is not throttled)"
            )
        entries.append(
            {
                "path": literal,
                "route": entry["path"],
                "view": entry["view"],
                "protected": protected,
                "in_edge_scope": covered,
                "throttle_scope": scope,
                "in_app_requests_per_second": (
                    budget["requests_per_second"] if budget else None
                ),
                "binding_control": binding,
                "binding_reason": reason,
                "unbounded": binding == "unbounded",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------


def review(
    record=None,
    edge_template=DEFAULT_EDGE_TEMPLATE,
    overrides=None,
    settings_module=None,
):
    """Produce the review record. Pure: it reads and returns, never writes."""
    template = read_edge_template(edge_template)
    prefixes = list(template["scope_down_prefixes"])
    if not prefixes:
        raise ReviewError(
            "the edge template {!r} declares no scope-down prefix, so the "
            "edge rule would count nothing; the review cannot classify any "
            "path".format(template["path"])
        )

    problems = []
    limit, window, record_problems = ceiling_from_record(record)
    problems.extend(record_problems)
    if limit is None:
        limit, window = ceiling_from_template(template)
        ceiling_source = "edge-template"
        if record is not None:
            problems.append(
                "the record states no usable ceiling, so the DECLARED "
                "ceiling from the template was used instead"
            )
    else:
        ceiling_source = "canary-record"
    edge_rate = limit / float(window)

    shipped, module = read_shipped_rates(settings_module)
    effective, applied = apply_overrides(shipped, overrides)
    budgets = parse_rates(effective)

    paths = shipped_paths()
    entries = analyse_paths(paths, prefixes, budgets, edge_rate)

    traffic = observed_traffic(record)
    findings = []

    for entry in entries:
        if entry["unbounded"]:
            findings.append(
                {
                    "level": "error",
                    "code": "unbounded_path",
                    "path": entry["path"],
                    "message": entry["binding_reason"],
                }
            )
        elif (
            entry["protected"]
            and not entry["in_edge_scope"]
            and entry["throttle_scope"] is None
        ):
            findings.append(
                {
                    "level": "error",
                    "code": "not_covered_by_the_edge_rule",
                    "path": entry["path"],
                    "message": (
                        "the path is protected by the review and carries no "
                        "in-app throttle, but the edge rule's scope-down "
                        "({}) does not cover it, so nothing bounds it".format(
                            ", ".join(prefixes)
                        )
                    ),
                }
            )

    missing_required = [
        path
        for path in REQUIRED_REVIEW_PATHS
        if not any(entry["path"] == path for entry in entries)
    ]
    for path in missing_required:
        findings.append(
            {
                "level": "warning",
                "code": "named_path_not_served",
                "path": path,
                "message": (
                    "the review names this path, but the shipped app does "
                    "not serve it, so it could not be reviewed"
                ),
            }
        )

    if traffic["ceiling_reached"] is True:
        findings.append(
            {
                "level": "warning",
                "code": "ceiling_reached",
                "path": None,
                "message": (
                    "the canary counted requests the edge would have "
                    "refused, so at least one source address hit the "
                    "ceiling in the observed window"
                ),
            }
        )
    if traffic["observation_state"] == OBSERVATION_NO_OBSERVATION:
        findings.append(
            {
                "level": "warning",
                "code": "no_observation",
                "path": None,
                "message": (
                    "the canary window holds no datapoint on a rule old "
                    "enough to have been counted, so the ceiling is "
                    "unverified"
                ),
            }
        )

    retune_reasons = [
        finding["message"]
        for finding in findings
        if finding["level"] == "error"
    ]
    if traffic["ceiling_reached"] is True:
        retune_reasons.append(
            "a source address reached the edge ceiling in the observed "
            "window, so the ceiling is tighter than the observed traffic "
            "allows"
        )
    if retune_reasons:
        verdict = "retune"
        verdict_basis = (
            "observed" if traffic["scoped_requests"] is not None else
            "structural"
        )
        verdict_reason = "; ".join(retune_reasons)
    else:
        verdict = "keep"
        verdict_basis = (
            "observed" if traffic["scoped_requests"] is not None else
            "structural"
        )
        if verdict_basis == "observed":
            verdict_reason = (
                "every protected path is bounded, the in-app scopes are the "
                "stricter control on the throttled endpoints, and the "
                "observed window did not reach the ceiling"
            )
        else:
            verdict_reason = (
                "every protected path is bounded, but NO traffic was "
                "observed, so this verdict rests on the structural analysis "
                "alone: it is NOT evidence that the ceiling is adequate, "
                "only that nothing observed warrants changing a budget "
                "today"
            )

    in_scope = [entry for entry in entries if entry["in_edge_scope"]]
    throttled = [
        entry for entry in in_scope if entry["throttle_scope"] is not None
    ]
    edge_only = [
        entry
        for entry in in_scope
        if entry["throttle_scope"] is None
    ]

    review_record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "verdict_basis": verdict_basis,
        "verdict_reason": verdict_reason,
        "in_app_scopes_changed_by_this_review": False,
        "decision_note": (
            "This review does NOT change any in-app throttle scope (D021). "
            "The canary outcome is an INPUT to a later tuning decision, "
            "not a licence to change a limit now."
        ),
        "edge_ceiling": {
            "source": ceiling_source,
            "limit": limit,
            "evaluation_window_seconds": window,
            "requests_per_second_per_source_ip": edge_rate,
            "scope_down_prefixes": prefixes,
            "action": (
                record.get("action")
                if isinstance(record, dict) and record.get("action")
                else template.get("action")
            ),
            "template_path": template["path"],
        },
        "in_app_budgets": {
            "source": "the shipped Django settings",
            "settings_module": module,
            "shipped_rates": shipped,
            "overrides_applied": applied,
            "production_override_env_vars": SCOPE_OVERRIDE_ENV_VARS,
            "production_override_note": (
                "the deployed environment can override these budgets with "
                "THROTTLE_RATE_SIMULATE and THROTTLE_RATE_SCORES; the "
                "values below are the shipped defaults unless an override "
                "is listed in overrides_applied"
            ),
            "scopes": budgets,
        },
        "binding_analysis": {
            "edge_ceiling_requests_per_second": edge_rate,
            "paths": entries,
            "in_edge_scope_paths": [entry["path"] for entry in in_scope],
            "throttled_in_scope_paths": [
                entry["path"] for entry in throttled
            ],
            "edge_only_in_scope_paths": [
                entry["path"] for entry in edge_only
            ],
            "protected_paths_not_covered_by_the_edge_rule": [
                entry["path"]
                for entry in entries
                if entry["protected"]
                and not entry["in_edge_scope"]
            ],
        },
        "observed_traffic": traffic,
        "record": {
            "supplied": record is not None,
            "observation_state": traffic["observation_state"],
        },
        "findings": findings,
        "problems": problems,
    }
    return review_record


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def _print_summary(review_record):
    ceiling = review_record["edge_ceiling"]
    scopes = review_record["in_app_budgets"]["scopes"]
    print(
        "PASS: the edge ceiling is {} request(s) per {}s "
        "({:.4g} request(s)/s per source IP) [source: {}]".format(
            ceiling["limit"],
            ceiling["evaluation_window_seconds"],
            ceiling["requests_per_second_per_source_ip"],
            ceiling["source"],
        )
    )
    for scope in sorted(scopes):
        budget = scopes[scope]
        print(
            "PASS: the in-app scope {!r} allows {} ({:.4g} request(s)/s), "
            "read from the shipped settings".format(
                scope, budget["rate"], budget["requests_per_second"]
            )
        )
    overrides = review_record["in_app_budgets"]["overrides_applied"]
    if overrides:
        print(
            "NOTE: the deployed overrides {} were applied to the shipped "
            "budgets".format(
                ", ".join(
                    "{}={}".format(scope, value)
                    for scope, value in sorted(overrides.items())
                )
            )
        )
    else:
        print(
            "NOTE: production can override the in-app budgets with "
            "THROTTLE_RATE_SIMULATE / THROTTLE_RATE_SCORES; the shipped "
            "values were used"
        )
    for entry in review_record["binding_analysis"]["paths"]:
        if entry["binding_control"] == "in_app_scope":
            print(
                "PASS: {} is bounded by the in-app scope {!r} -- {}".format(
                    entry["path"], entry["throttle_scope"],
                    entry["binding_reason"],
                )
            )
        elif entry["binding_control"] == "edge_ceiling" and (
            entry["in_edge_scope"]
        ):
            print(
                "NOTE: {} carries NO in-app throttle -- {}".format(
                    entry["path"], entry["binding_reason"]
                )
            )
    traffic = review_record["observed_traffic"]
    if traffic["scoped_requests"] is None:
        print(
            "NOTE: no traffic figure is available -- {} (null, never 0: no "
            "data is not zero traffic)".format(traffic["reason"])
        )
    else:
        print(
            "PASS: {} scoped request(s) observed, {:.4g} request(s) per "
            "window".format(
                traffic["scoped_requests"],
                traffic["observed_rate_per_window"],
            )
        )
    for finding in review_record["findings"]:
        print(
            "{}: {} {}".format(
                "ERROR" if finding["level"] == "error" else "WARNING",
                finding["code"],
                finding["message"],
            )
        )
    print(
        "VERDICT: {} -- {}".format(
            review_record["verdict"], review_record["verdict_reason"]
        )
    )
    print("NOTE: " + review_record["decision_note"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Review the edge rate ceiling against the shipped in-app "
            "throttle scopes (read-only)"
        )
    )
    parser.add_argument(
        "--record",
        default=None,
        help="the canary count record JSON (optional)",
    )
    parser.add_argument(
        "--review-out",
        default=None,
        help="where to write the review record JSON (stdout when omitted)",
    )
    parser.add_argument(
        "--edge-template",
        default=DEFAULT_EDGE_TEMPLATE,
        help="the edge template to read the declared ceiling from",
    )
    parser.add_argument(
        "--simulate-rate",
        default=None,
        help=(
            "the deployed THROTTLE_RATE_SIMULATE override (the shipped "
            "value is used when omitted)"
        ),
    )
    parser.add_argument(
        "--scores-rate",
        default=None,
        help=(
            "the deployed THROTTLE_RATE_SCORES override (the shipped value "
            "is used when omitted)"
        ),
    )
    args = parser.parse_args(argv)

    overrides = {
        "simulate": args.simulate_rate,
        "scores": args.scores_rate,
    }
    try:
        record = load_record(args.record)
        review_record = review(
            record=record,
            edge_template=args.edge_template,
            overrides=overrides,
        )
    except ReviewError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1

    if args.review_out:
        destination = Path(args.review_out)
        if destination.parent and not destination.parent.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(review_record, handle, indent=2, sort_keys=True)
            handle.write("\n")
        _print_summary(review_record)
        print("PASS: the review record was written to {}".format(
            str(destination)
        ))
    else:
        print(json.dumps(review_record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
