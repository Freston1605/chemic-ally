"""Durable guards on the origin guard's rollout wiring in CI (M007/S04).

The two-phase rollout of the origin header guard is only safe if it is
ORDERED and OBSERVABLE: the CloudFront origin must stamp the header before
anything refuses a request, and the refusal must be provable against the
live infrastructure. The witness (``scripts/verify_origin_guard.py``, M007/S04
T01) is the proof; these guards pin that the deploy job actually RUNS it, in
the right order, and cannot swallow its verdict.

Without this wiring the deploy job ships the guard's switch without ever
looking at the result: the switch can be flipped to "true" on a distribution
whose origin header is not live (or not yet propagated to the edges) and the
job would still report success. That is exactly the failure this milestone
exists to prevent -- CloudFront not stamping the header while Lambda already
refuses header-less requests takes the whole site down.

These guards pin in ``.github/workflows/deploy.yml``:

* a PRE-DEPLOY preflight that runs the witness with ``--expect header-live``
  BEFORE the "SAM deploy" step, gated on the enforcement switch, so a deploy
  that would enforce can only run when the header is already live and
  settled across the edges;
* a POST-DEPLOY witness that runs the witness with ``--expect auto`` and the
  declared switch value AFTER the DNS sync and AFTER the deployment check, so
  it proves which phase is actually live;
* neither step may be swallowed (``|| true`` / ``continue-on-error``);
* the switch is still ONE workflow setting defaulting to "false", still
  passed as the ``EnforceOriginGuard`` parameter override, and the secret is
  still passed (regression on M007/S03's contract);
* the deploy job still fails loudly when the ``ORIGIN_SECRET`` secret is
  missing (regression on M006/S03's contract);
* the workflow still parses as YAML.

Following the convention of ``tests/test_edge_deploy_pipeline.py`` (M007/S02)
and ``tests/test_origin_guard_rollout.py`` (M007/S03), the assertions are
named guard functions proved non-vacuous by mutation-based negative tests.
The workflow is read as plain text and parsed with regexes only; the single
YAML dependency (PyYAML, shipped by the pinned cfn-lint dev dependency) is
used for ONE check -- that the workflow still parses. ``SimpleTestCase``
blocks all DB access; these checks touch only the filesystem.
"""

import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# The two steps this slice adds to the deploy job.
PREFLIGHT_STEP = "Preflight: the origin header must be live before enforcement"
POST_DEPLOY_STEP = "Verify the origin guard rollout"

# The pre-existing steps the new ones are ordered against.
APP_DEPLOY_STEP = "SAM deploy"
DNS_SYNC_STEP = "Sync Cloudflare DNS records"
VERIFY_DEPLOY_STEP = "Verify deployment"
ORIGIN_SECRET_STEP = "Verify the origin secret is configured"

# The switch owns the rollout; phase 1 (header live, guard inert) is the
# default, so no deploy can refuse traffic by accident.
DEFAULT_ENFORCE = "false"


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
    item, with the trailing blank lines and the comment banner that belongs
    to the NEXT step trimmed away. Blank lines inside a ``run: |`` body are
    kept: they are part of the step.
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


def _move_step_before(text, step_name, before_step_name):
    """Return the workflow with ``step_name`` moved before ``before_step_name``.

    This is the honest mutation for an ordering guard: renaming a step away
    only proves the guard notices a MISSING step, while moving the step
    proves the guard notices a step in the WRONG PLACE.
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


def _preflight_runs_the_witness_with_header_live(text):
    """The preflight must run the witness and ask for the header-live state.

    ``--expect header-live`` is the PRE-DEPLOY contract: the header is
    stamped on the live origin and has had time to reach every edge. Any
    other expectation would let the deploy proceed without proving the
    header is live.
    """
    body = _step_body(text, PREFLIGHT_STEP)
    if not body:
        return False
    if "scripts/verify_origin_guard.py" not in body:
        return False
    if "--expect header-live" not in body:
        return False
    if "--stack-name chemically" not in body:
        return False
    return "--region ${{ env.AWS_REGION }}" in body


def _preflight_is_gated_on_the_switch(text):
    """The preflight must only run when the deploy would enforce.

    Phase 1 ships the header itself, so the preflight would fail every
    phase-1 deploy (the header is not live yet -- that is the point of
    phase 1). The gate makes the preflight a phase-2 gate.
    """
    body = _step_body(text, PREFLIGHT_STEP)
    if not body:
        return False
    return (
        re.search(
            r"^[ \t]+if:[ \t]*env\.ENFORCE_ORIGIN_GUARD[ \t]*==[ \t]*'true'"
            r"[ \t]*$",
            body,
            re.MULTILINE,
        )
        is not None
    )


def _preflight_runs_before_the_app_deploy(text):
    """The preflight must run BEFORE the deploy that would enforce.

    A preflight that runs after the deploy proves nothing: the guard is
    already enforcing by then.
    """
    preflight = _step_index(text, PREFLIGHT_STEP)
    app_deploy = _step_index(text, APP_DEPLOY_STEP)
    if preflight < 0 or app_deploy < 0:
        return False
    return preflight < app_deploy


def _post_deploy_runs_the_witness_with_auto(text):
    """The post-deploy step must run the witness and detect the live phase.

    ``--expect auto`` detects the phase that is actually live; paired with
    ``--expect-enforcement`` it also fails when the deploy did not take
    effect (the switch says "true" but the Lambda still carries no secret).
    """
    body = _step_body(text, POST_DEPLOY_STEP)
    if not body:
        return False
    if "scripts/verify_origin_guard.py" not in body:
        return False
    if "--expect auto" not in body:
        return False
    if "--expect-enforcement ${{ env.ENFORCE_ORIGIN_GUARD }}" not in body:
        return False
    if "--site-url https://chemic-ally.xyz" not in body:
        return False
    return "--stack-name chemically" in body


def _post_deploy_runs_after_the_dns_sync(text):
    """The witness must run after the DNS sync and after the deploy check.

    The public-site probe has to hit the live alias, so it must run after
    the records point at the (possibly recreated) distribution; and the
    deployment check has to have passed before the rollout is declared.
    """
    post = _step_index(text, POST_DEPLOY_STEP)
    dns = _step_index(text, DNS_SYNC_STEP)
    verify = _step_index(text, VERIFY_DEPLOY_STEP)
    if post < 0 or dns < 0 or verify < 0:
        return False
    return post > dns and post > verify


def _rollout_steps_cannot_be_swallowed(text):
    """Neither new step may be made non-blocking.

    ``|| true`` or ``continue-on-error: true`` would turn the witness into
    a report: the job would go green while the guard refuses every
    legitimate request (or while the header is not live at all).
    """
    for step_name in (PREFLIGHT_STEP, POST_DEPLOY_STEP):
        body = _step_body(text, step_name)
        if not body:
            return False
        if "|| true" in body:
            return False
        if "continue-on-error" in body:
            return False
    return True


def _switch_value(text):
    """Return the switch's current value, or None when it is not readable."""
    env = _job_env(text)
    if not env:
        return None
    settings = re.findall(
        r"^      ENFORCE_ORIGIN_GUARD:[ \t]*(.*)$", env, re.MULTILINE
    )
    if len(settings) != 1:
        return None
    return settings[0].strip().strip('"').strip("'").lower()


def _switch_is_owned_by_one_setting(text):
    """One switch, holding a boolean, still passed to the deploy.

    Regression guard on M007/S03's contract: the new steps read the
    switch, so a second definition would make the rollout order
    ambiguous, and a value that is not a boolean would make the phase
    unreadable. WHICH phase is live is a separate question (the phase
    moved to enforcement on 2026-10-22, once the header had been live and
    settled); this guard only cares that there is exactly one switch and
    that it is a boolean.
    """
    if _switch_value(text) not in ("true", "false"):
        return False
    if "EnforceOriginGuard=${{ env.ENFORCE_ORIGIN_GUARD }}" not in text:
        return False
    return "OriginSecret=${{ secrets.ORIGIN_SECRET }}" in text


def _deploy_fails_loudly_without_the_origin_secret(text):
    """A missing ORIGIN_SECRET must still fail the deploy job.

    Regression guard on M006/S03's contract: the new steps must not be
    mistaken for a replacement of the secret check. An empty secret would
    deploy a Lambda whose origin guard is inert, i.e. the Function-URL
    bypass around the WAF would be back.
    """
    body = _step_body(text, ORIGIN_SECRET_STEP)
    if not body:
        return False
    if "secrets.ORIGIN_SECRET" not in body:
        return False
    if "::error::" not in body:
        return False
    if not re.search(r"exit 1", body):
        return False
    return re.search(r'\[ -z "\$\{ORIGIN_SECRET\}" \]', body) is not None


def _workflow_parses_as_yaml(text):
    """The workflow must still be valid YAML with both new steps.

    A workflow that does not parse is a workflow that never runs, and the
    guards above would then pass on a file GitHub ignores.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(document, dict):
        return False
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return False
    deploy = jobs.get("deploy")
    if not isinstance(deploy, dict):
        return False
    steps = deploy.get("steps") or []
    names = [
        step.get("name") for step in steps if isinstance(step, dict)
    ]
    return PREFLIGHT_STEP in names and POST_DEPLOY_STEP in names


class OriginGuardRolloutPipelineTests(SimpleTestCase):
    """M007/S04: the deploy job must run the witness, in the right order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_preflight_runs_the_witness_with_header_live(self):
        self.assertTrue(
            _preflight_runs_the_witness_with_header_live(self.workflow),
            "the deploy job must run scripts/verify_origin_guard.py with "
            "--expect header-live before a deploy that would enforce.",
        )

    def test_preflight_is_gated_on_the_switch(self):
        self.assertTrue(
            _preflight_is_gated_on_the_switch(self.workflow),
            "the preflight must be gated on ENFORCE_ORIGIN_GUARD == 'true': "
            "phase 1 ships the header, so the preflight would fail every "
            "phase-1 deploy.",
        )

    def test_preflight_runs_before_the_app_deploy(self):
        self.assertTrue(
            _preflight_runs_before_the_app_deploy(self.workflow),
            "the preflight must run BEFORE the SAM deploy step: a preflight "
            "that runs after the deploy proves nothing.",
        )

    def test_post_deploy_runs_the_witness_with_auto(self):
        self.assertTrue(
            _post_deploy_runs_the_witness_with_auto(self.workflow),
            "the deploy job must run scripts/verify_origin_guard.py with "
            "--expect auto and the declared switch value after every "
            "deploy.",
        )

    def test_post_deploy_runs_after_the_dns_sync(self):
        self.assertTrue(
            _post_deploy_runs_after_the_dns_sync(self.workflow),
            "the rollout witness must run AFTER the Cloudflare DNS sync and "
            "after the deployment check: the public-site probe has to hit "
            "the live alias.",
        )

    def test_rollout_steps_cannot_be_swallowed(self):
        self.assertTrue(
            _rollout_steps_cannot_be_swallowed(self.workflow),
            "neither the preflight nor the rollout witness may be made "
            "non-blocking ('|| true' / 'continue-on-error'): a witness that "
            "cannot fail the job is a report.",
        )

    def test_switch_is_owned_by_one_setting(self):
        self.assertTrue(
            _switch_is_owned_by_one_setting(self.workflow),
            "the deploy job must keep ONE ENFORCE_ORIGIN_GUARD setting "
            "holding a boolean, pass it as the EnforceOriginGuard "
            "parameter override, and still pass the OriginSecret.",
        )

    def test_the_live_phase_is_the_enforced_one(self):
        """Phase 2 (M007/S04, 2026-10-22): the guard enforces.

        The origin header has been live and settled on the distribution for
        hours - the deploy job's own witness proves it on every run - so
        the guard may now refuse direct Function-URL calls. The rollback is
        one change: set the switch back to false.
        """
        self.assertEqual(
            _switch_value(self.workflow),
            "true",
            "the origin guard must now enforce: the header has been live "
            "and settled since the phase-1 deploy, and the preflight "
            "re-checks that precondition on every deploy.",
        )

    def test_deploy_fails_loudly_without_the_origin_secret(self):
        self.assertTrue(
            _deploy_fails_loudly_without_the_origin_secret(self.workflow),
            "the deploy job must still fail loudly when the ORIGIN_SECRET "
            "secret is missing (M006/S03): the new steps do not replace it.",
        )

    def test_workflow_parses_as_yaml(self):
        self.assertTrue(
            _workflow_parses_as_yaml(self.workflow),
            ".github/workflows/deploy.yml must parse as YAML and contain "
            "both rollout steps in the deploy job.",
        )


class OriginGuardRolloutPipelineNegativeTests(SimpleTestCase):
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

    def test_preflight_without_the_header_live_expectation_is_detected(self):
        mutated = self._mutate(
            "            --expect header-live",
            "            --expect guard-inert",
        )
        self.assertFalse(
            _preflight_runs_the_witness_with_header_live(mutated),
            "the preflight guard must fail when it stops asking for the "
            "header-live state: the header could then be missing at "
            "enforcement time.",
        )

    def test_ungated_preflight_is_detected(self):
        mutated = self._mutate(
            "        if: env.ENFORCE_ORIGIN_GUARD == 'true'\n",
            "",
        )
        self.assertFalse(
            _preflight_is_gated_on_the_switch(mutated),
            "the gate guard must fail when the preflight is not gated on "
            "the switch: phase 1 would be blocked by the very header it "
            "is about to ship.",
        )

    def test_preflight_after_the_app_deploy_is_detected(self):
        mutated = _move_step_before(
            self.workflow, APP_DEPLOY_STEP, PREFLIGHT_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the SAM deploy step; the "
            "negative case cannot be constructed and needs to be updated.",
        )
        self.assertLess(
            _step_index(mutated, APP_DEPLOY_STEP),
            _step_index(mutated, PREFLIGHT_STEP),
            "the mutation must place the app deploy before the preflight.",
        )
        self.assertFalse(
            _preflight_runs_before_the_app_deploy(mutated),
            "the ordering guard must fail when the preflight runs AFTER the "
            "app deploy: the guard would already be enforcing.",
        )

    def test_post_deploy_without_the_auto_expectation_is_detected(self):
        mutated = self._mutate(
            "            --expect auto \\",
            "            --expect header-live \\",
        )
        self.assertFalse(
            _post_deploy_runs_the_witness_with_auto(mutated),
            "the post-deploy guard must fail when the witness stops "
            "detecting the live phase.",
        )

    def test_post_deploy_with_a_hardcoded_enforcement_is_detected(self):
        mutated = self._mutate(
            "            --expect-enforcement ${{ env.ENFORCE_ORIGIN_GUARD }}",
            "            --expect-enforcement true",
        )
        self.assertFalse(
            _post_deploy_runs_the_witness_with_auto(mutated),
            "the post-deploy guard must fail when the declared enforcement "
            "is hard-coded: a deploy that did not take effect would pass.",
        )

    def test_post_deploy_before_the_dns_sync_is_detected(self):
        mutated = _move_step_before(
            self.workflow, POST_DEPLOY_STEP, DNS_SYNC_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the rollout witness step; the "
            "negative case cannot be constructed and needs to be updated.",
        )
        self.assertLess(
            _step_index(mutated, POST_DEPLOY_STEP),
            _step_index(mutated, DNS_SYNC_STEP),
            "the mutation must place the witness before the DNS sync.",
        )
        self.assertFalse(
            _post_deploy_runs_after_the_dns_sync(mutated),
            "the ordering guard must fail when the witness runs BEFORE the "
            "DNS sync: the public-site probe would hit the old alias.",
        )

    def test_swallowing_the_preflight_is_detected(self):
        mutated = self._mutate(
            "            --expect header-live",
            "            --expect header-live || true",
        )
        self.assertFalse(
            _rollout_steps_cannot_be_swallowed(mutated),
            "the guard must fail when the preflight is made non-blocking "
            "with '|| true'.",
        )

    def test_continue_on_error_witness_is_detected(self):
        mutated = self._mutate(
            "      - name: Verify the origin guard rollout",
            "      - name: Verify the origin guard rollout\n"
            "        continue-on-error: true",
        )
        self.assertFalse(
            _rollout_steps_cannot_be_swallowed(mutated),
            "the guard must fail when the rollout witness is made "
            "non-blocking with 'continue-on-error'.",
        )

    def test_a_non_boolean_switch_value_is_detected(self):
        mutated = self._mutate(
            '      ENFORCE_ORIGIN_GUARD: "true"',
            '      ENFORCE_ORIGIN_GUARD: "yes"',
        )
        self.assertFalse(
            _switch_is_owned_by_one_setting(mutated),
            "the switch guard must fail when the switch does not hold a "
            "boolean: the phase would be unreadable.",
        )

    def test_a_second_switch_definition_is_detected(self):
        mutated = self._mutate(
            '      ENFORCE_ORIGIN_GUARD: "true"',
            '      ENFORCE_ORIGIN_GUARD: "true"\n'
            '      ENFORCE_ORIGIN_GUARD: "false"',
        )
        self.assertFalse(
            _switch_is_owned_by_one_setting(mutated),
            "the switch guard must fail when the switch is defined twice: "
            "the rollout order would be ambiguous.",
        )

    def test_missing_parameter_override_is_detected(self):
        mutated = self._mutate(
            "              EnforceOriginGuard=${{ env.ENFORCE_ORIGIN_GUARD }} \\\n",
            "",
        )
        self.assertFalse(
            _switch_is_owned_by_one_setting(mutated),
            "the switch guard must fail when the switch is no longer passed "
            "to the deploy.",
        )

    def test_missing_origin_secret_is_detected(self):
        mutated = self._mutate(
            "              OriginSecret=${{ secrets.ORIGIN_SECRET }} \\\n",
            "",
        )
        self.assertFalse(
            _switch_is_owned_by_one_setting(mutated),
            "the switch guard must fail when the secret is no longer passed: "
            "phase 1 needs it to stamp the CloudFront origin header.",
        )

    def test_silent_missing_origin_secret_is_detected(self):
        mutated = self._mutate(
            "            exit 1\n",
            "",
        )
        self.assertFalse(
            _deploy_fails_loudly_without_the_origin_secret(mutated),
            "the origin-secret guard must fail when the deploy stops "
            "failing loudly on a missing secret.",
        )

    def test_broken_yaml_is_detected(self):
        mutated = self._mutate(
            "      - name: Verify the origin guard rollout",
            "      - name: Verify the origin guard rollout\n"
            "        run: echo 'broken: [unclosed'",
        )
        self.assertFalse(
            _workflow_parses_as_yaml(mutated),
            "the YAML guard must fail when the workflow no longer parses: "
            "GitHub would ignore it and every other guard would pass on a "
            "file that never runs.",
        )
