"""Unit tests for the budget review engine (scripts/review_edge_budget.py).

M007/S06/T02. The budget review must be reproducible and auditable, and it
must never turn "no observation" into "zero traffic". These tests pin every
branch of the review:

* the edge ceiling comes from the canary RECORD when there is one, and from
  the edge TEMPLATE's declared default otherwise;
* the in-app budgets are read from the SHIPPED settings and parsed with
  DRF's own parser -- never hard-coded;
* a deployed override (THROTTLE_RATE_SIMULATE / THROTTLE_RATE_SCORES) is
  applied when it is passed, and a value production would refuse at boot is
  refused here too;
* the binding analysis classifies the shipped paths: the throttled
  endpoints (the in-app scope is the stricter control), the in-scope paths
  that carry NO in-app throttle (the edge ceiling is the ONLY bound) and
  the paths outside the scope-down (deliberately unbounded);
* the observed traffic is recorded when the window was observed and is
  NULL WITH A REASON when it was not -- no record, a warming-up window, a
  window with no datapoint, a tampered record;
* the verdict is explicit ("keep" or "retune") and the review states that
  it does NOT change the in-app scopes (D021).

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. Nothing here touches AWS, the
network or the database: the review is read-only by construction.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "review_edge_budget.py"
EDGE_TEMPLATE = REPO_ROOT / "edge-template.yaml"

_spec = importlib.util.spec_from_file_location(
    "review_edge_budget", MODULE_PATH
)
review_edge_budget = importlib.util.module_from_spec(_spec)
sys.modules["review_edge_budget"] = review_edge_budget
_spec.loader.exec_module(review_edge_budget)


def _record(**overrides):
    """A canary record as the observer writes it."""
    record = {
        "action": "Count",
        "limit": 2000,
        "evaluation_window_seconds": 300,
        "observation_state": "observed",
        "observation_state_reason": None,
        "scoped_requests": 1234,
        "observed_rate_per_window": 1234 / 288.0,
        "window_start": "2026-10-21T12:00:00+00:00",
        "window_end": "2026-10-22T12:00:00+00:00",
        "window_minutes": 1440,
        "ceiling_reached": False,
        "status": "pass",
    }
    record.update(overrides)
    return record


def _write_record(directory, record):
    """Write a record to disk and return its path."""
    path = Path(directory) / "edge-canary-count-record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return str(path)


def _write_mutated_template(directory, old, new):
    """Write a copy of the edge template with one substitution."""
    source = EDGE_TEMPLATE.read_text(encoding="utf-8")
    assert old in source, "the mutation target is not in the template"
    path = Path(directory) / "edge-template-mutated.yaml"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return str(path)


def _entry(review_record, path):
    """The binding-analysis entry for one path."""
    for entry in review_record["binding_analysis"]["paths"]:
        if entry["path"] == path:
            return entry
    raise AssertionError(
        "the review does not classify {!r}; it classifies {}".format(
            path,
            [entry["path"] for entry in
             review_record["binding_analysis"]["paths"]],
        )
    )


class CeilingSourceTests(SimpleTestCase):
    """The edge ceiling is read, never typed in."""

    def test_without_a_record_the_declared_ceiling_is_used(self):
        review_record = review_edge_budget.review()
        ceiling = review_record["edge_ceiling"]
        self.assertEqual(ceiling["source"], "edge-template")
        self.assertEqual(ceiling["limit"], 2000)
        self.assertEqual(ceiling["evaluation_window_seconds"], 300)
        self.assertAlmostEqual(
            ceiling["requests_per_second_per_source_ip"], 2000 / 300.0
        )

    def test_the_scope_down_prefixes_are_read_from_the_template(self):
        review_record = review_edge_budget.review()
        self.assertEqual(
            review_record["edge_ceiling"]["scope_down_prefixes"],
            ["/hplc/api/", "/calculate/equilibria"],
        )

    def test_a_record_states_the_ceiling_that_was_observed(self):
        record = _record(limit=500, evaluation_window_seconds=60)
        review_record = review_edge_budget.review(record=record)
        ceiling = review_record["edge_ceiling"]
        self.assertEqual(ceiling["source"], "canary-record")
        self.assertEqual(ceiling["limit"], 500)
        self.assertEqual(ceiling["evaluation_window_seconds"], 60)

    def test_a_tampered_limit_falls_back_to_the_template_and_says_so(self):
        record = _record(limit="2000", evaluation_window_seconds=300)
        review_record = review_edge_budget.review(record=record)
        self.assertEqual(
            review_record["edge_ceiling"]["source"], "edge-template"
        )
        self.assertTrue(
            any("unusable limit" in problem
                for problem in review_record["problems"]),
            "a tampered limit must be reported, not silently ignored: "
            "{}".format(review_record["problems"]),
        )

    def test_a_template_without_a_usable_ceiling_is_refused(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = _write_mutated_template(
            directory, "    Default: 2000\n", "    Default: not-a-number\n"
        )
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.review(edge_template=path)

    def test_a_missing_template_is_refused(self):
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.review(edge_template="no-such-template.yaml")

    def test_a_record_that_is_not_json_is_refused(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = Path(directory) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.load_record(str(path))

    def test_a_record_that_is_not_an_object_is_refused(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = Path(directory) / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.load_record(str(path))


class InAppBudgetTests(SimpleTestCase):
    """The in-app budgets are read from the SHIPPED settings."""

    def test_the_shipped_rates_are_read_and_parsed(self):
        review_record = review_edge_budget.review()
        budgets = review_record["in_app_budgets"]["scopes"]
        self.assertEqual(budgets["simulate"]["rate"], "60/min")
        self.assertEqual(budgets["simulate"]["requests"], 60)
        self.assertEqual(budgets["simulate"]["period_seconds"], 60)
        self.assertAlmostEqual(
            budgets["simulate"]["requests_per_second"], 1.0
        )
        self.assertEqual(budgets["scores"]["rate"], "30/min")
        self.assertAlmostEqual(budgets["scores"]["requests_per_second"], 0.5)

    def test_the_rates_are_parsed_by_drf(self):
        review_record = review_edge_budget.review()
        scopes = review_record["in_app_budgets"]["scopes"]
        self.assertIn("parse_rate", scopes["simulate"]["parsed_by"])

    def test_the_review_states_that_production_can_override_the_rates(self):
        review_record = review_edge_budget.review()
        budgets = review_record["in_app_budgets"]
        self.assertEqual(
            budgets["production_override_env_vars"],
            {
                "simulate": "THROTTLE_RATE_SIMULATE",
                "scores": "THROTTLE_RATE_SCORES",
            },
        )
        self.assertIn("THROTTLE_RATE_SIMULATE", budgets[
            "production_override_note"
        ])

    def test_a_deployed_override_is_used(self):
        review_record = review_edge_budget.review(
            overrides={"simulate": "120/min", "scores": "5/min"}
        )
        budgets = review_record["in_app_budgets"]
        self.assertEqual(
            budgets["overrides_applied"],
            {"simulate": "120/min", "scores": "5/min"},
        )
        self.assertEqual(budgets["scopes"]["simulate"]["rate"], "120/min")
        self.assertAlmostEqual(
            budgets["scopes"]["simulate"]["requests_per_second"], 2.0
        )
        self.assertEqual(
            budgets["shipped_rates"]["simulate"], "60/min",
            "the shipped value must still be reported as read",
        )

    def test_an_override_production_would_refuse_is_refused(self):
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.review(overrides={"simulate": "fast"})

    def test_a_malformed_rate_cannot_be_parsed(self):
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.parse_rates({"simulate": "sixty per minute"})

    def test_a_rate_that_is_not_a_string_cannot_be_parsed(self):
        with self.assertRaises(review_edge_budget.ReviewError):
            review_edge_budget.parse_rates({"simulate": 60})


class PathClassificationTests(SimpleTestCase):
    """The paths are classified from the shipped app and the template."""

    def test_the_throttled_endpoints_carry_their_in_app_scope(self):
        review_record = review_edge_budget.review()
        self.assertEqual(
            _entry(review_record, "/hplc/api/simulate/")["throttle_scope"],
            "simulate",
        )
        self.assertEqual(
            _entry(review_record, "/hplc/api/scores/")["throttle_scope"],
            "scores",
        )

    def test_the_unthrottled_in_scope_paths_carry_no_scope(self):
        review_record = review_edge_budget.review()
        for path in (
            "/hplc/api/levels/",
            "/hplc/api/progress/",
            "/calculate/equilibria",
        ):
            entry = _entry(review_record, path)
            self.assertIsNone(
                entry["throttle_scope"],
                "{} must carry no in-app throttle".format(path),
            )
            self.assertTrue(entry["in_edge_scope"])

    def test_a_path_outside_the_scope_down_is_not_in_edge_scope(self):
        review_record = review_edge_budget.review()
        entry = _entry(review_record, "/calculate/dilution")
        self.assertFalse(entry["in_edge_scope"])
        self.assertEqual(entry["binding_control"], "none")

    def test_the_binding_analysis_names_every_required_path(self):
        review_record = review_edge_budget.review()
        paths = [
            entry["path"]
            for entry in review_record["binding_analysis"]["paths"]
        ]
        for required in review_edge_budget.REQUIRED_REVIEW_PATHS:
            self.assertIn(required, paths)

    def test_a_scope_down_that_drops_a_prefix_leaves_a_path_unbounded(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = _write_mutated_template(
            directory,
            "                        SearchString: /hplc/api/\n",
            "                        SearchString: /hplc/other/\n",
        )
        review_record = review_edge_budget.review(edge_template=path)
        entry = _entry(review_record, "/hplc/api/levels/")
        self.assertFalse(entry["in_edge_scope"])
        self.assertTrue(
            any(
                finding["code"] == "not_covered_by_the_edge_rule"
                for finding in review_record["findings"]
            ),
            "a protected path the edge rule no longer covers must be "
            "reported: {}".format(review_record["findings"]),
        )
        self.assertEqual(review_record["verdict"], "retune")


class BindingAnalysisTests(SimpleTestCase):
    """What actually bounds each path."""

    def test_the_in_app_scope_is_the_stricter_control_on_a_throttled_path(self):
        review_record = review_edge_budget.review()
        entry = _entry(review_record, "/hplc/api/simulate/")
        self.assertEqual(entry["binding_control"], "in_app_scope")
        self.assertAlmostEqual(entry["in_app_requests_per_second"], 1.0)
        self.assertAlmostEqual(
            review_record["binding_analysis"][
                "edge_ceiling_requests_per_second"
            ],
            2000 / 300.0,
        )

    def test_the_edge_ceiling_is_the_only_bound_on_an_unthrottled_path(self):
        review_record = review_edge_budget.review()
        entry = _entry(review_record, "/hplc/api/levels/")
        self.assertEqual(entry["binding_control"], "edge_ceiling")
        self.assertIn(
            "/hplc/api/levels/",
            review_record["binding_analysis"]["edge_only_in_scope_paths"],
        )

    def test_the_equilibria_path_is_bounded_only_at_the_edge(self):
        review_record = review_edge_budget.review()
        entry = _entry(review_record, "/calculate/equilibria")
        self.assertEqual(entry["binding_control"], "edge_ceiling")
        self.assertIsNone(entry["throttle_scope"])

    def test_a_loose_in_app_scope_leaves_the_edge_ceiling_binding(self):
        review_record = review_edge_budget.review(
            overrides={"simulate": "10000/min"}
        )
        entry = _entry(review_record, "/hplc/api/simulate/")
        self.assertEqual(entry["binding_control"], "edge_ceiling")

    def test_a_tighter_in_app_scope_becomes_the_binding_control(self):
        review_record = review_edge_budget.review(
            overrides={"scores": "1/min"}
        )
        entry = _entry(review_record, "/hplc/api/scores/")
        self.assertEqual(entry["binding_control"], "in_app_scope")


class ObservedTrafficTests(SimpleTestCase):
    """No observation is never zero traffic."""

    def test_without_a_record_the_traffic_is_null_with_a_reason(self):
        review_record = review_edge_budget.review()
        traffic = review_record["observed_traffic"]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertIsNone(traffic["observed_rate_per_window"])
        self.assertIn("UNKNOWN, not zero", traffic["reason"])

    def test_a_warming_up_window_is_null_with_the_recorded_reason(self):
        record = _record(
            observation_state="warming_up",
            observation_state_reason="the rule is younger than the window",
            scoped_requests=None,
            observed_rate_per_window=None,
            ceiling_reached=None,
        )
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertIn("younger than the window", traffic["reason"])

    def test_a_window_with_no_datapoint_is_null_with_a_reason(self):
        record = _record(
            observation_state="no_observation",
            scoped_requests=None,
            observed_rate_per_window=None,
            ceiling_reached=None,
        )
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertIn("UNKNOWN, not zero", traffic["reason"])

    def test_an_observed_window_reports_the_traffic(self):
        record = _record(scoped_requests=42, observed_rate_per_window=0.5)
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertEqual(traffic["scoped_requests"], 42)
        self.assertAlmostEqual(traffic["observed_rate_per_window"], 0.5)
        self.assertEqual(traffic["observation_state"], "observed")

    def test_a_tampered_record_cannot_turn_no_observation_into_zero(self):
        record = _record(
            observation_state="warming_up",
            scoped_requests=0,
            observed_rate_per_window=0.0,
        )
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(
            traffic["scoped_requests"],
            "a warming-up record must not report zero traffic",
        )
        self.assertIsNone(traffic["observed_rate_per_window"])

    def test_a_record_with_an_unknown_state_is_not_treated_as_observed(self):
        record = _record(
            observation_state="totally-unknown",
            scoped_requests=7,
            observed_rate_per_window=1.0,
        )
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertIn("UNKNOWN, not zero", traffic["reason"])

    def test_an_observed_record_without_a_count_is_refused(self):
        record = _record(scoped_requests=None, observed_rate_per_window=None)
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertIn("UNKNOWN, not zero", traffic["reason"])


class VerdictTests(SimpleTestCase):
    """The verdict is explicit and it changes nothing."""

    def test_an_observed_quiet_window_keeps_the_budgets(self):
        review_record = review_edge_budget.review(record=_record())
        self.assertEqual(review_record["verdict"], "keep")
        self.assertEqual(review_record["verdict_basis"], "observed")

    def test_a_reached_ceiling_asks_for_a_retune(self):
        record = _record(ceiling_reached=True)
        review_record = review_edge_budget.review(record=record)
        self.assertEqual(review_record["verdict"], "retune")
        self.assertIn("ceiling", review_record["verdict_reason"])

    def test_without_an_observation_the_verdict_is_structural_only(self):
        review_record = review_edge_budget.review()
        self.assertEqual(review_record["verdict"], "keep")
        self.assertEqual(review_record["verdict_basis"], "structural")
        self.assertIn("NOT evidence", review_record["verdict_reason"])

    def test_the_review_states_that_it_changes_no_scope(self):
        review_record = review_edge_budget.review(record=_record())
        self.assertFalse(
            review_record["in_app_scopes_changed_by_this_review"]
        )
        self.assertIn("D021", review_record["decision_note"])

    def test_the_review_changes_no_shipped_setting(self):
        from django.conf import settings

        before = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        review_edge_budget.review(record=_record())
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], before
        )


class CliTests(SimpleTestCase):
    """The CLI produces the review record and fails loudly."""

    def test_the_cli_writes_the_review_record(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        record_path = _write_record(directory, _record())
        out = Path(directory) / "edge-budget-review.json"
        code = review_edge_budget.main(
            [
                "--record",
                record_path,
                "--review-out",
                str(out),
                "--edge-template",
                str(EDGE_TEMPLATE),
            ]
        )
        self.assertEqual(code, 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("verdict", written)
        self.assertIn("edge_ceiling", written)
        self.assertIn("in_app_budgets", written)
        self.assertIn("binding_analysis", written)
        self.assertIn("observed_traffic", written)

    def test_the_cli_fails_loudly_on_a_broken_record(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = Path(directory) / "broken.json"
        path.write_text("{", encoding="utf-8")
        code = review_edge_budget.main(["--record", str(path)])
        self.assertEqual(code, 1)

    def test_the_cli_prints_the_record_when_no_output_is_given(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = review_edge_budget.main([])
        self.assertEqual(code, 0)
        printed = json.loads(buffer.getvalue())
        self.assertIn("verdict", printed)


class MutationTests(SimpleTestCase):
    """The guards are load-bearing, not decoration.

    The mutation is applied to the TRACKED source of the review and the
    mutated module is imported: if the guard were removed, the scenario
    below would fail -- and the positive case above would go red with it.
    A guard that cannot fail is a comment.
    """

    def _load_mutated_module(self, old, new):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            old,
            source,
            "the review source no longer contains {!r}; the mutation cannot "
            "be constructed and the negative case needs to be "
            "updated.".format(old),
        )
        mutated_source = source.replace(old, new, 1)
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "review_edge_budget_mutated.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(mutated_source)
        name = "review_edge_budget_mutated_{}".format(abs(hash(mutated_source)))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_a_review_that_reports_zero_for_a_missing_observation_is_detected(
        self,
    ):
        module = self._load_mutated_module(
            '    if state == OBSERVATION_OBSERVED:\n',
            '    if state == OBSERVATION_OBSERVED or True:\n',
        )
        record = _record(
            observation_state="warming_up",
            scoped_requests=0,
            observed_rate_per_window=0.0,
        )
        traffic = module.review(record=record)["observed_traffic"]
        self.assertEqual(
            traffic["scoped_requests"],
            0,
            "the mutation was supposed to turn the missing observation into "
            "a zero traffic figure; if it did not, the mutation never "
            "exercised the guard this test pins",
        )

    def test_a_review_that_ignores_the_recorded_ceiling_is_detected(self):
        module = self._load_mutated_module(
            "    if limit is None:\n"
            "        limit, window = ceiling_from_template(template)\n",
            "    if limit is None or True:\n"
            "        limit, window = ceiling_from_template(template)\n",
        )
        review_record = module.review(record=_record(limit=500))
        self.assertEqual(
            review_record["edge_ceiling"]["limit"],
            2000,
            "the mutation was supposed to ignore the ceiling the record "
            "states; if it did not, the mutation never exercised the guard "
            "this test pins",
        )
        self.assertEqual(
            review_record["edge_ceiling"]["source"], "edge-template"
        )

    def test_the_unmutated_review_passes_the_same_scenarios(self):
        record = _record(
            observation_state="warming_up",
            scoped_requests=0,
            observed_rate_per_window=0.0,
        )
        traffic = review_edge_budget.review(record=record)[
            "observed_traffic"
        ]
        self.assertIsNone(traffic["scoped_requests"])
        self.assertEqual(
            review_edge_budget.review(record=_record(limit=500))[
                "edge_ceiling"
            ]["source"],
            "canary-record",
        )
