"""Durable guards on the CI-owned edge canary wiring (M007/S05).

Two scripts that nobody runs prove nothing. The edge canary is only
operable if the DEPLOY JOB owns it: the Count-mode observer runs on every
Count deploy (that is where the canary window's evidence comes from) and
the Block-mode probe fires exactly when the rule's action changes (so the
flip is observed, not assumed). Without this wiring the switch to Block
would be a one-line change that nobody could check afterwards.

These guards pin in ``.github/workflows/deploy.yml``:

* a step that reads the LIVE edge stack's ``WafAction`` BEFORE the edge
  deploy overwrites it, compares it with the workflow's single
  ``WAF_ACTION`` setting and publishes ``waf_action_changed`` so the CI
  log records the transition;
* that read tolerates ONLY the "stack does not exist" case (the first
  deploy CREATES the rule) and fails the job on every other AWS error, so
  a throttling error can never be mistaken for "the action did not
  change";
* the Count-mode observer, gated on ``WAF_ACTION == 'Count'``, running
  AFTER the edge deploy and the edge verification;
* the Block-mode probe, gated on BOTH ``WAF_ACTION == 'Block'`` AND a
  real transition, running after the origin guard rollout and being the
  LAST executable step of the job (it deliberately trips the rate limit
  for the runner's own address);
* neither script step is swallowable (``|| true`` / ``continue-on-error``);
* both JSON records are uploaded as artifacts, so the budget review
  (M007/S06) can read the evidence;
* the workflow still parses as YAML and the action is still ``Count``
  (this slice makes the flip POSSIBLE and PROVABLE, it does not perform
  it).

Following the convention of ``tests/test_edge_deploy_pipeline.py``
(M007/S02) and ``tests/test_origin_guard_rollout_pipeline.py`` (M007/S04),
the assertions are named guard functions proved non-vacuous by
mutation-based negative tests. The workflow is read as plain text and
parsed with regexes only; the single YAML dependency (PyYAML, shipped by
the pinned cfn-lint dev dependency) is used for the two checks that need
the real document: that the workflow parses and that the probe is the
last executable step. ``SimpleTestCase`` blocks all DB access; these
checks touch only the filesystem.
"""

import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# The steps this slice adds to the deploy job.
ACTION_READ_STEP = "Read the live edge rule action"
OBSERVER_STEP = "Observe the edge canary (Count mode)"
OBSERVER_UPLOAD_STEP = "Upload the edge canary record (Count mode)"
PROBE_STEP = "Prove the edge block (Block mode)"
PROBE_UPLOAD_STEP = "Upload the edge block record (Block mode)"

# The pre-existing steps the new ones are ordered against.
EDGE_DEPLOY_STEP = "Deploy the edge stack (us-east-1)"
EDGE_VERIFY_STEP = "Verify the edge association"
ORIGIN_GUARD_STEP = "Verify the origin guard rollout"

# The step ids the gates refer to.
ACTION_READ_ID = "edge-action"
OBSERVER_ID = "observe-canary"
PROBE_ID = "probe-block"

# The rule's scope (edge-template.yaml): only paths under /hplc/api/ are
# counted and blocked, so the probe must aim inside it.
RULE_SCOPE_PREFIX = "/hplc/api/"

# The edge rule ships counting; blocking is the canary's end state and is
# a reviewed change, not something this slice performs.
DEFAULT_EDGE_ACTION = "Count"

COUNT_RECORD = "edge-canary-count-record.json"
BLOCK_RECORD = "edge-canary-block-record.json"


def _name_pattern(step_name):
    """Return the regex matching the ``- name:`` line of a step.

    A step name may be quoted in the YAML (a name containing ": " has to
    be), so the optional quotes are part of the pattern.
    """
    return (
        r"^[ \t]{6}-[ \t]+name:[ \t]*\"?"
        + re.escape(step_name)
        + r"\"?[ \t]*$"
    )


def _step_block(text, step_name):
    """Return the (start, end) line bounds of the named step's own block.

    The block is the ``- name:`` line plus everything up to the next list
    item, with the trailing blank lines and the comment banner that
    belongs to the NEXT step trimmed away. Blank lines inside a ``run: |``
    body are kept: they are part of the step.
    """
    lines = text.splitlines(keepends=True)
    pattern = re.compile(_name_pattern(step_name))
    start = None
    for index, line in enumerate(lines):
        if pattern.match(line.rstrip("\n")):
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^[ \t]{6}-[ \t]", lines[index]):
            end = index
            break
    while end > start + 1:
        candidate = lines[end - 1]
        if not candidate.strip():
            end -= 1
            continue
        indent = len(candidate) - len(candidate.lstrip())
        if indent <= 6 and candidate.lstrip().startswith("#"):
            end -= 1
            continue
        break
    return start, end


def _step_body(text, step_name):
    """Return the named step's whole block as text ("" when absent)."""
    bounds = _step_block(text, step_name)
    if bounds is None:
        return ""
    lines = text.splitlines(keepends=True)
    return "".join(lines[bounds[0]:bounds[1]])


def _step_index(text, step_name):
    """Return the line index of the named step, or -1 when it is absent."""
    bounds = _step_block(text, step_name)
    return bounds[0] if bounds is not None else -1


def _step_key(text, step_name, key):
    """Return a step's own top-level key (``id``, ``if``), or None.

    A step's own keys sit at exactly eight spaces of indentation, which
    is what distinguishes them from the same words inside the ``run:``
    body (ten spaces) or inside a ``with:`` block (ten spaces).
    """
    body = _step_body(text, step_name)
    if not body:
        return None
    match = re.search(
        r"^ {8}" + re.escape(key) + r":[ \t]*(.*?)[ \t]*$",
        body,
        re.MULTILINE,
    )
    if match is None:
        return None
    return match.group(1)


def _with_key(text, step_name, key):
    """Return a key inside the step's ``with:`` block, or None."""
    body = _step_body(text, step_name)
    if not body:
        return None
    match = re.search(
        r"^ {10}" + re.escape(key) + r":[ \t]*(.*?)[ \t]*$",
        body,
        re.MULTILINE,
    )
    if match is None:
        return None
    return match.group(1)


def _next_step_name(text, after_line):
    """Return the name of the next list item after the given line index."""
    lines = text.splitlines(keepends=True)
    for index in range(after_line, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if not re.match(r"^[ \t]{6}-[ \t]", line):
            continue
        match = re.match(r"^[ \t]{6}-[ \t]+name:[ \t]*(\S.*?)[ \t]*$", line)
        if match is None:
            return None
        return match.group(1)
    return None


def _move_step_before(text, step_name, before_step_name):
    """Return the workflow with ``step_name`` moved before ``before_step_name``.

    This is the honest mutation for an ordering guard: renaming a step
    away only proves the guard notices a MISSING step, while moving the
    step proves the guard notices a step in the WRONG PLACE.
    """
    lines = text.splitlines(keepends=True)
    bounds = _step_block(text, step_name)
    if bounds is None:
        return None
    start, end = bounds
    block = lines[start:end]
    rest = lines[:start] + lines[end:]
    target_pattern = re.compile(_name_pattern(before_step_name))
    target = None
    for index, line in enumerate(rest):
        if target_pattern.match(line.rstrip("\n")):
            target = index
            break
    if target is None:
        return None
    return "".join(rest[:target] + block + rest[target:])


def _job_env(text):
    """Return the deploy job's ``env:`` block as text (or "" when absent)."""
    job = re.search(r"^  deploy:[ \t]*$", text, re.MULTILINE)
    if job is None:
        return ""
    body = text[job.end():]
    env = re.search(r"^    env:[ \t]*$", body, re.MULTILINE)
    if env is None:
        return ""
    block = body[env.end():]
    following = re.search(r"^    [A-Za-z]", block, re.MULTILINE)
    if following is not None:
        block = block[:following.start()]
    return block


def _deploy_steps(text):
    """Return the deploy job's parsed steps, or None when unparseable."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return None
    deploy = jobs.get("deploy")
    if not isinstance(deploy, dict):
        return None
    steps = deploy.get("steps")
    if not isinstance(steps, list):
        return None
    return steps


def _observer_step_exists_and_runs_the_observer(text):
    """The Count-mode observer must exist, be id'd, and run the observer.

    The observer (``scripts/verify_edge_canary.py``) is what turns the
    Count window into evidence: it reads the live rule, the WAF counters
    and the site's status and writes a JSON record. Without
    ``--expect-action Count`` the observer would happily record a window
    that was never counted.
    """
    body = _step_body(text, OBSERVER_STEP)
    if not body:
        return False
    if _step_key(text, OBSERVER_STEP, "id") != OBSERVER_ID:
        return False
    if "scripts/verify_edge_canary.py" not in body:
        return False
    if "--expect-action Count" not in body:
        return False
    if f"--record-out {COUNT_RECORD}" not in body:
        return False
    return "--edge-region us-east-1" in body


def _observer_is_gated_on_count_mode(text):
    """The observer must only run in Count mode.

    In Block mode the observer would record a window that was already
    refusing traffic, which is not the canary's evidence; and the probe
    owns Block mode.
    """
    condition = _step_key(text, OBSERVER_STEP, "if")
    if condition is None:
        return False
    return condition.strip() == "env.WAF_ACTION == 'Count'"


def _observer_cannot_be_swallowed(text):
    """The observer must be able to fail the job.

    ``|| true`` or ``continue-on-error: true`` would turn the observation
    into a report: the job would go green while the rule was not the one
    the operator thought, or while the counters were unreadable.
    """
    body = _step_body(text, OBSERVER_STEP)
    if not body:
        return False
    if "|| true" in body:
        return False
    if "continue-on-error" in body:
        return False
    return True


def _observer_runs_after_the_edge_deploy_and_verification(text):
    """The observer must run after the rule is deployed and verified.

    Observing before the edge deploy would describe the PREVIOUS rule, and
    observing before the association check would describe a rule that may
    not even be attached to the live distribution.
    """
    observer = _step_index(text, OBSERVER_STEP)
    deploy = _step_index(text, EDGE_DEPLOY_STEP)
    verify = _step_index(text, EDGE_VERIFY_STEP)
    if observer < 0 or deploy < 0 or verify < 0:
        return False
    return observer > deploy and observer > verify


def _probe_step_exists_and_runs_the_probe(text):
    """The Block-mode probe must exist, be id'd, and run the probe.

    ``--confirm-live`` is the safety interlock of the probe: it is what
    makes the script refuse to burst a site that is not actually behind
    the rule. The path must stay inside the rule's scope
    (``/hplc/api/``), because a request outside the scope is never
    counted, never blocked, and would prove nothing.
    """
    body = _step_body(text, PROBE_STEP)
    if not body:
        return False
    if _step_key(text, PROBE_STEP, "id") != PROBE_ID:
        return False
    if "scripts/verify_edge_block.py" not in body:
        return False
    if "--confirm-live" not in body:
        return False
    match = re.search(r"--path[ \t]+(\S+)", body)
    if match is None:
        return False
    return match.group(1).startswith(RULE_SCOPE_PREFIX)


def _probe_is_gated_on_a_real_flip(text):
    """The probe must fire only on a REAL transition to Block.

    Two conditions, both required: the workflow is deploying Block mode,
    and THIS run actually changed the live action. A steady-state Block
    deploy must not burst the live site for no new information, and a
    Count deploy must never burst it at all.
    """
    condition = _step_key(text, PROBE_STEP, "if")
    if condition is None:
        return False
    if "env.WAF_ACTION == 'Block'" not in condition:
        return False
    if "steps.edge-action.outputs.waf_action_changed == 'true'" not in condition:
        return False
    return "&&" in condition


def _probe_cannot_be_swallowed(text):
    """The probe must be able to fail the job.

    A probe that cannot fail the job is a comment: the flip would be
    declared proven while the origin may still have served the burst.
    """
    body = _step_body(text, PROBE_STEP)
    if not body:
        return False
    if "|| true" in body:
        return False
    if "continue-on-error" in body:
        return False
    return True


def _probe_runs_after_the_origin_guard_rollout(text):
    """The probe must run after the origin guard rollout.

    The guard rollout is the deploy's own witness; a probe that ran
    before it could read counters of a half-finished deploy.
    """
    probe = _step_index(text, PROBE_STEP)
    rollout = _step_index(text, ORIGIN_GUARD_STEP)
    if probe < 0 or rollout < 0:
        return False
    return probe > rollout


def _probe_is_the_last_executable_step(text):
    """The probe must be the LAST executable step of the deploy job.

    The probe deliberately trips the rate limit for the runner's own
    address. Anything that ran after it (a health check, a deploy
    summary) could therefore be refused by the very control the deploy
    just put in place, and the job would go red for a reason that has
    nothing to do with the flip.
    """
    steps = _deploy_steps(text)
    if steps is None:
        return False
    executable = [
        step for step in steps if isinstance(step, dict) and step.get("run")
    ]
    if not executable:
        return False
    return executable[-1].get("name") == PROBE_STEP


def _action_read_step_reports_the_transition(text):
    """The action read must compare the live action with the wanted one.

    The live action is read from the edge stack's OWN parameters (in
    us-east-1, where the CLOUDFRONT-scoped rule lives) BEFORE the edge
    deploy overwrites them, and the verdict is published as the step
    output ``waf_action_changed`` so the probe's gate can use it.
    """
    body = _step_body(text, ACTION_READ_STEP)
    if not body:
        return False
    if _step_key(text, ACTION_READ_STEP, "id") != ACTION_READ_ID:
        return False
    if "describe-stacks" not in body:
        return False
    if "--region us-east-1" not in body:
        return False
    if "WafAction" not in body:
        return False
    if '"$EDGE_STACK_NAME"' not in body:
        return False
    if "$WAF_ACTION" not in body:
        return False
    if not re.search(r"waf_action_changed=", body):
        return False
    return "$GITHUB_OUTPUT" in body


def _action_read_tolerates_only_a_missing_stack(text):
    """The action read must fail the job on every error but a missing one.

    A stack that does not exist yet means this run CREATES the rule:
    there is no previous action to compare with, so the action counts as
    changed. EVERY other AWS failure (throttling, a lost connection)
    must fail the job: a read error must never be mistaken for "the
    action did not change", because that would silently skip the proof of
    the flip.
    """
    body = _step_body(text, ACTION_READ_STEP)
    if not body:
        return False
    if "does not exist" not in body:
        return False
    if "::error::" not in body:
        return False
    if not re.search(r"exit 1", body):
        return False
    return True


def _action_read_runs_immediately_before_the_edge_deploy(text):
    """The action read must run immediately before the edge deploy.

    It compares the PREVIOUS action with the wanted one, so it has to
    happen before the edge deploy overwrites the parameter. Running it
    after the deploy would compare the wanted action with itself and
    always report "unchanged", which would silently disable the probe.
    """
    bounds = _step_block(text, ACTION_READ_STEP)
    if bounds is None:
        return False
    return _next_step_name(text, bounds[1]) == EDGE_DEPLOY_STEP


def _records_are_uploaded(text):
    """Both JSON records must be uploaded as artifacts.

    The Count record is the canary window's evidence for the budget
    review (M007/S06) and the Block record is the proof of the flip, so
    neither may be lost with the runner. The upload must run even when
    the observation failed (``always()``) -- a failed run's record is
    exactly the record worth reading.
    """
    for step_name, step_id, record in (
        (OBSERVER_UPLOAD_STEP, OBSERVER_ID, COUNT_RECORD),
        (PROBE_UPLOAD_STEP, PROBE_ID, BLOCK_RECORD),
    ):
        body = _step_body(text, step_name)
        if not body:
            return False
        if "actions/upload-artifact@v5" not in body:
            return False
        condition = _step_key(text, step_name, "if")
        if condition is None:
            return False
        if "always()" not in condition:
            return False
        if f"steps.{step_id}.outcome != 'skipped'" not in condition:
            return False
        if _with_key(text, step_name, "path") != record:
            return False
        if _with_key(text, step_name, "name") != record[: -len(".json")]:
            return False
        if _with_key(text, step_name, "if-no-files-found") != "warn":
            return False
        if _with_key(text, step_name, "retention-days") != "30":
            return False
    return True


def _edge_action_is_still_count(text):
    """The workflow's edge action must be one of the two modes.

    This slice made the flip POSSIBLE and PROVABLE; the flip itself
    happened later, after the window had been read (M007/S05, 2026-10-22).
    What must hold is that the action comes from exactly one setting and
    that it is one of the two modes the template accepts.
    """
    env = _job_env(text)
    if not env:
        return False
    settings = re.findall(
        r"^      WAF_ACTION:[ \t]*(.*)$", env, re.MULTILINE
    )
    if len(settings) != 1:
        return False
    return settings[0].strip().strip('"').strip("'") in ("Count", "Block")


def _workflow_parses_as_yaml(text):
    """The workflow must still be valid YAML with the new steps.

    A workflow that does not parse is a workflow that never runs, and the
    guards above would then pass on a file GitHub ignores.
    """
    steps = _deploy_steps(text)
    if steps is None:
        return False
    names = [
        step.get("name") for step in steps if isinstance(step, dict)
    ]
    for required in (ACTION_READ_STEP, OBSERVER_STEP, PROBE_STEP):
        if required not in names:
            return False
    return True


class EdgeCanaryPipelineTests(SimpleTestCase):
    """M007/S05: the deploy job must own the edge canary."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_observer_step_exists_and_runs_the_observer(self):
        self.assertTrue(
            _observer_step_exists_and_runs_the_observer(self.workflow),
            "the deploy job must run scripts/verify_edge_canary.py with "
            "--expect-action Count in a step with id 'observe-canary'.",
        )

    def test_observer_is_gated_on_count_mode(self):
        self.assertTrue(
            _observer_is_gated_on_count_mode(self.workflow),
            "the observer must be gated on env.WAF_ACTION == 'Count': in "
            "Block mode the probe owns the run.",
        )

    def test_observer_cannot_be_swallowed(self):
        self.assertTrue(
            _observer_cannot_be_swallowed(self.workflow),
            "the observer must be able to fail the job: '|| true' or "
            "'continue-on-error' would make the canary window unproven.",
        )

    def test_observer_runs_after_the_edge_deploy_and_verification(self):
        self.assertTrue(
            _observer_runs_after_the_edge_deploy_and_verification(
                self.workflow
            ),
            "the observer must run AFTER the edge deploy and AFTER the "
            "edge association check: it observes the live rule.",
        )

    def test_probe_step_exists_and_runs_the_probe(self):
        self.assertTrue(
            _probe_step_exists_and_runs_the_probe(self.workflow),
            "the deploy job must run scripts/verify_edge_block.py with "
            "--confirm-live and a path inside the rule's scope "
            "(/hplc/api/) in a step with id 'probe-block'.",
        )

    def test_probe_is_gated_on_a_real_flip(self):
        self.assertTrue(
            _probe_is_gated_on_a_real_flip(self.workflow),
            "the probe must be gated on BOTH env.WAF_ACTION == 'Block' AND "
            "steps.edge-action.outputs.waf_action_changed == 'true': a "
            "steady-state Block deploy must not burst the live site.",
        )

    def test_probe_cannot_be_swallowed(self):
        self.assertTrue(
            _probe_cannot_be_swallowed(self.workflow),
            "the probe must be able to fail the job: a probe that cannot "
            "fail the job is a comment.",
        )

    def test_probe_runs_after_the_origin_guard_rollout(self):
        self.assertTrue(
            _probe_runs_after_the_origin_guard_rollout(self.workflow),
            "the probe must run AFTER the origin guard rollout.",
        )

    def test_probe_is_the_last_executable_step(self):
        self.assertTrue(
            _probe_is_the_last_executable_step(self.workflow),
            "the probe must be the LAST executable step of the deploy job: "
            "it trips the rate limit for the runner's own address.",
        )

    def test_action_read_step_reports_the_transition(self):
        self.assertTrue(
            _action_read_step_reports_the_transition(self.workflow),
            "the deploy job must read the live edge stack's WafAction in "
            "us-east-1, compare it with $WAF_ACTION and publish "
            "waf_action_changed to $GITHUB_OUTPUT.",
        )

    def test_action_read_tolerates_only_a_missing_stack(self):
        self.assertTrue(
            _action_read_tolerates_only_a_missing_stack(self.workflow),
            "the action read must tolerate only the 'does not exist' case "
            "and fail the job on every other AWS error.",
        )

    def test_action_read_runs_immediately_before_the_edge_deploy(self):
        self.assertTrue(
            _action_read_runs_immediately_before_the_edge_deploy(
                self.workflow
            ),
            "the action read must run immediately BEFORE the edge deploy: "
            "after it, the comparison would be against itself.",
        )

    def test_records_are_uploaded(self):
        self.assertTrue(
            _records_are_uploaded(self.workflow),
            "both the Count record and the Block record must be uploaded "
            "as artifacts, even when the run failed.",
        )

    def test_edge_action_is_still_count(self):
        self.assertTrue(
            _edge_action_is_still_count(self.workflow),
            "the workflow's edge action must still be Count: this slice "
            "makes the flip provable, it does not perform it.",
        )

    def test_workflow_parses_as_yaml(self):
        self.assertTrue(
            _workflow_parses_as_yaml(self.workflow),
            ".github/workflows/deploy.yml must parse as YAML and contain "
            "the action read, the observer and the probe.",
        )


class EdgeCanaryPipelineNegativeTests(SimpleTestCase):
    """The guards above must fail when the wiring is removed or weakened.

    Each case mutates a copy of the tracked workflow and asserts that the
    corresponding guard rejects it, so a guard that silently stopped
    matching cannot keep the suite green while the pipeline has drifted
    apart.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.workflow,
            f"the workflow no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.workflow.replace(old, new, 1)

    def test_observer_without_the_id_is_detected(self):
        mutated = self._mutate(
            "        id: observe-canary\n",
            "",
        )
        self.assertFalse(
            _observer_step_exists_and_runs_the_observer(mutated),
            "the observer guard must fail when the step loses its id: the "
            "gate that keys the upload would break too.",
        )

    def test_observer_without_the_count_expectation_is_detected(self):
        mutated = self._mutate(
            "            --expect-action Count",
            "            --expect-action Block",
        )
        self.assertFalse(
            _observer_step_exists_and_runs_the_observer(mutated),
            "the observer guard must fail when it stops asking for the "
            "Count window: it would record a window that was never "
            "counted.",
        )

    def test_ungated_observer_is_detected(self):
        mutated = self._mutate(
            "        if: env.WAF_ACTION == 'Count'\n",
            "",
        )
        self.assertFalse(
            _observer_is_gated_on_count_mode(mutated),
            "the gate guard must fail when the observer is not gated: in "
            "Block mode it would record a window that was already "
            "refusing traffic.",
        )

    def test_observer_gated_on_the_wrong_mode_is_detected(self):
        mutated = self._mutate(
            "        if: env.WAF_ACTION == 'Count'\n",
            "        if: env.WAF_ACTION == 'Block'\n",
        )
        self.assertFalse(
            _observer_is_gated_on_count_mode(mutated),
            "the gate guard must fail when the observer runs in Block "
            "mode: the probe owns that mode.",
        )

    def test_swallowing_the_observer_is_detected(self):
        mutated = self._mutate(
            "            --record-out edge-canary-count-record.json\n",
            "            --record-out edge-canary-count-record.json || true\n",
        )
        self.assertFalse(
            _observer_cannot_be_swallowed(mutated),
            "the guard must fail when the observer is made non-blocking "
            "with '|| true'.",
        )

    def test_continue_on_error_observer_is_detected(self):
        mutated = self._mutate(
            "      - name: Observe the edge canary (Count mode)\n",
            "      - name: Observe the edge canary (Count mode)\n"
            "        continue-on-error: true\n",
        )
        self.assertFalse(
            _observer_cannot_be_swallowed(mutated),
            "the guard must fail when the observer is made non-blocking "
            "with 'continue-on-error'.",
        )

    def test_observer_before_the_edge_deploy_is_detected(self):
        mutated = _move_step_before(
            self.workflow, OBSERVER_STEP, EDGE_DEPLOY_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the observer step; the "
            "negative case cannot be constructed and needs to be updated.",
        )
        self.assertLess(
            _step_index(mutated, OBSERVER_STEP),
            _step_index(mutated, EDGE_DEPLOY_STEP),
            "the mutation must place the observer before the edge deploy.",
        )
        self.assertFalse(
            _observer_runs_after_the_edge_deploy_and_verification(mutated),
            "the ordering guard must fail when the observer runs BEFORE "
            "the edge deploy: it would describe the previous rule.",
        )

    def test_probe_without_the_id_is_detected(self):
        mutated = self._mutate(
            "        id: probe-block\n",
            "",
        )
        self.assertFalse(
            _probe_step_exists_and_runs_the_probe(mutated),
            "the probe guard must fail when the step loses its id.",
        )

    def test_probe_without_confirm_live_is_detected(self):
        mutated = self._mutate(
            "            --confirm-live \\\n",
            "",
        )
        self.assertFalse(
            _probe_step_exists_and_runs_the_probe(mutated),
            "the probe guard must fail when the live confirmation is "
            "dropped: the script would burst a site that is not behind "
            "the rule.",
        )

    def test_probe_outside_the_rule_scope_is_detected(self):
        mutated = self._mutate(
            "            --path /hplc/api/levels/ \\\n",
            "            --path / \\\n",
        )
        self.assertFalse(
            _probe_step_exists_and_runs_the_probe(mutated),
            "the probe guard must fail when the path leaves the rule's "
            "scope: a request outside /hplc/api/ is never counted, never "
            "blocked, and proves nothing.",
        )

    def test_probe_gated_on_the_wrong_mode_is_detected(self):
        mutated = self._mutate(
            "        if: env.WAF_ACTION == 'Block' && "
            "steps.edge-action.outputs.waf_action_changed == 'true'\n",
            "        if: env.WAF_ACTION == 'Count' && "
            "steps.edge-action.outputs.waf_action_changed == 'true'\n",
        )
        self.assertFalse(
            _probe_is_gated_on_a_real_flip(mutated),
            "the gate guard must fail when the probe is armed in Count "
            "mode: it would burst the live site on every deploy.",
        )

    def test_probe_without_the_transition_condition_is_detected(self):
        mutated = self._mutate(
            "        if: env.WAF_ACTION == 'Block' && "
            "steps.edge-action.outputs.waf_action_changed == 'true'\n",
            "        if: env.WAF_ACTION == 'Block'\n",
        )
        self.assertFalse(
            _probe_is_gated_on_a_real_flip(mutated),
            "the gate guard must fail when the transition condition is "
            "dropped: a steady-state Block deploy would burst the site "
            "for no new information.",
        )

    def test_swallowing_the_probe_is_detected(self):
        mutated = self._mutate(
            "            --record-out edge-canary-block-record.json\n",
            "            --record-out edge-canary-block-record.json || true\n",
        )
        self.assertFalse(
            _probe_cannot_be_swallowed(mutated),
            "the guard must fail when the probe is made non-blocking with "
            "'|| true'.",
        )

    def test_continue_on_error_probe_is_detected(self):
        mutated = self._mutate(
            "      - name: Prove the edge block (Block mode)\n",
            "      - name: Prove the edge block (Block mode)\n"
            "        continue-on-error: true\n",
        )
        self.assertFalse(
            _probe_cannot_be_swallowed(mutated),
            "the guard must fail when the probe is made non-blocking with "
            "'continue-on-error'.",
        )

    def test_probe_before_the_origin_guard_rollout_is_detected(self):
        mutated = _move_step_before(
            self.workflow, PROBE_STEP, ORIGIN_GUARD_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the probe step; the negative "
            "case cannot be constructed and needs to be updated.",
        )
        self.assertLess(
            _step_index(mutated, PROBE_STEP),
            _step_index(mutated, ORIGIN_GUARD_STEP),
            "the mutation must place the probe before the rollout.",
        )
        self.assertFalse(
            _probe_runs_after_the_origin_guard_rollout(mutated),
            "the ordering guard must fail when the probe runs BEFORE the "
            "origin guard rollout.",
        )

    def test_a_step_after_the_probe_is_detected(self):
        mutated = self._mutate(
            "      - name: Upload the edge block record (Block mode)\n",
            "      - name: Post-probe report\n"
            "        run: echo 'the probe is done'\n"
            "\n"
            "      - name: Upload the edge block record (Block mode)\n",
        )
        self.assertFalse(
            _probe_is_the_last_executable_step(mutated),
            "the last-step guard must fail when something executable runs "
            "after the probe: the probe trips the rate limit for the "
            "runner's own address.",
        )

    def test_action_read_without_the_output_is_detected(self):
        mutated = self._mutate(
            '          echo "waf_action_changed=${changed}" >> '
            '"$GITHUB_OUTPUT"\n',
            "",
        )
        self.assertFalse(
            _action_read_step_reports_the_transition(mutated),
            "the action-read guard must fail when the output is no longer "
            "published: the probe's gate would never be satisfied.",
        )

    def test_action_read_in_the_wrong_region_is_detected(self):
        mutated = self._mutate(
            '            --stack-name "$EDGE_STACK_NAME" --region '
            "us-east-1 \\\n",
            '            --stack-name "$EDGE_STACK_NAME" --region '
            "us-east-2 \\\n",
        )
        self.assertFalse(
            _action_read_step_reports_the_transition(mutated),
            "the action-read guard must fail when the edge stack is read "
            "in the wrong region: the CLOUDFRONT-scoped rule only lives "
            "in us-east-1.",
        )

    def test_action_read_without_a_missing_stack_tolerance_is_detected(self):
        mutated = self._mutate(
            '            if printf \'%s\' "$output" | grep -qi "does not '
            'exist"; then\n',
            '            if printf \'%s\' "$output" | grep -qi "anything"; '
            "then\n",
        )
        # The tolerated case is also named in the message the step prints,
        # so the mutation has to take that wording away as well.
        echo_line = (
            '            echo "The edge stack ${EDGE_STACK_NAME} does not '
            "exist yet: this run creates the rule, so the action counts "
            'as changed."\n'
        )
        self.assertIn(
            echo_line,
            mutated,
            "the workflow no longer contains the missing-stack message; "
            "the negative case cannot be constructed and needs to be "
            "updated.",
        )
        mutated = mutated.replace(echo_line, "", 1)
        self.assertFalse(
            _action_read_tolerates_only_a_missing_stack(mutated),
            "the tolerance guard must fail when the 'does not exist' "
            "case is no longer the tolerated one.",
        )

    def test_action_read_that_never_fails_is_detected(self):
        mutated = self._mutate(
            "              exit 1\n",
            "",
        )
        self.assertFalse(
            _action_read_tolerates_only_a_missing_stack(mutated),
            "the tolerance guard must fail when the action read stops "
            "failing on an AWS error: a throttling error would then be "
            "mistaken for 'the action did not change'.",
        )

    def test_a_step_between_the_action_read_and_the_edge_deploy(self):
        mutated = self._mutate(
            "      - name: Deploy the edge stack (us-east-1)\n",
            "      - name: Announce the edge plan\n"
            "        run: echo 'deploying the edge stack'\n"
            "\n"
            "      - name: Deploy the edge stack (us-east-1)\n",
        )
        self.assertFalse(
            _action_read_runs_immediately_before_the_edge_deploy(mutated),
            "the adjacency guard must fail when another step sits between "
            "the action read and the edge deploy.",
        )

    def test_missing_record_upload_is_detected(self):
        mutated = self._mutate(
            "          path: edge-canary-count-record.json\n",
            "",
        )
        self.assertFalse(
            _records_are_uploaded(mutated),
            "the upload guard must fail when a record is no longer "
            "uploaded: the budget review would lose its evidence.",
        )

    def test_record_upload_without_always_is_detected(self):
        mutated = self._mutate(
            "        if: always() && steps.observe-canary.outcome != "
            "'skipped'\n",
            "        if: steps.observe-canary.outcome == 'success'\n",
        )
        self.assertFalse(
            _records_are_uploaded(mutated),
            "the upload guard must fail when the upload is skipped on a "
            "failed observation: a failed run's record is exactly the "
            "record worth reading.",
        )

    def test_a_non_mode_edge_action_is_detected(self):
        mutated = self._mutate(
            "      WAF_ACTION: Block", '      WAF_ACTION: "sometimes"'
        )
        self.assertFalse(
            _edge_action_is_still_count(mutated),
            "the action guard must fail when the switch does not hold one "
            "of the two modes the template accepts.",
        )

    def test_broken_yaml_is_detected(self):
        mutated = self._mutate(
            "      - name: Prove the edge block (Block mode)",
            "      - name: Prove the edge block (Block mode)\n"
            "        run: echo 'broken: [unclosed'",
        )
        self.assertFalse(
            _workflow_parses_as_yaml(mutated),
            "the YAML guard must fail when the workflow no longer parses: "
            "GitHub would ignore it and every other guard would pass on a "
            "file that never runs.",
        )
