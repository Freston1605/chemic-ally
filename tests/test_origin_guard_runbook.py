"""Durable guards on the origin guard's rollout runbook (M007/S04).

The origin guard's rollout is a PROCEDURE, and a procedure nobody can follow
is not a control. ``OPS_PLAYBOOK.md`` used to describe the guard as a
"Priority 2" hardening item with a manual curl pair and a rollout note that
described the first deploy that enables the guard as a known one-time risk.
That is not a procedure: it did not say which phase was live, how to tell,
what had to be true before the switch could be flipped, how to flip it, how
to roll it back, or what evidence to keep.

The two-phase rollout (header first, enforcement second) is now documented in
the runbook section "Rolling out the origin guard: header first, enforcement
second". These guards pin that section so it cannot be quietly deleted or
hollowed out:

* the section exists and is not a stub;
* it names both phases and says which one is the DEFAULT (phase 1: the header
  is live, the guard is inert);
* it documents the pre-flight command (``--expect header-live``) and the
  deploy job's own automatic pre-flight step;
* it documents how to flip the single ``ENFORCE_ORIGIN_GUARD`` setting and
  how to roll it back;
* it states the CI-owns-the-switch consequence: a manual deploy is reverted
  by the next CI run, so a rollback that must hold is made in the workflow;
* it states the secret-rotation rule: never rotate ``ORIGIN_SECRET`` in the
  same deploy that enables enforcement;
* it names the evidence to keep (both phases' CI runs, the refusal metric
  reading, the public site serving 200);
* the old "Priority 2" rollout note points at the new section instead of
  describing the rollout as a future risk.

The guards also tie the runbook to the repository it describes: the scripts
it tells the operator to run exist, and the CI step names it quotes are the
step names the deploy workflow really has. A runbook that names a step that
was renamed is a runbook that sends the operator looking for a step that does
not exist.

Following the convention of ``tests/test_origin_guard_rollout_pipeline.py``
(M007/S04) and ``tests/test_edge_deploy_pipeline.py`` (M007/S02), the
assertions are named guard functions proved non-vacuous by mutation-based
negative tests. The runbook is read as plain text and matched with regexes
only; ``SimpleTestCase`` blocks all DB access; these checks touch only the
filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = REPO_ROOT / "OPS_PLAYBOOK.md"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

#: The heading of the new procedure. The old "Priority 2" note and the
#: secret-rotation entry both refer to it by this exact title.
ROLLOUT_HEADING = "Rolling out the origin guard: header first, enforcement second"

#: The heading of the hardening item that used to carry the rollout note.
PRIORITY_2_HEADING = "Priority 2 \u2014 Restrict Function URL"

#: The CI steps the runbook quotes by name. They must exist in the deploy
#: workflow, otherwise the operator is told to read a step that is not there.
PREFLIGHT_STEP = "Preflight: the origin header must be live before enforcement"
ROLLOUT_WITNESS_STEP = "Verify the origin guard rollout"

#: The witness script the pre-flight command runs.
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_origin_guard.py"


def _flat(text):
    """Return the text with every run of whitespace collapsed to one space.

    The runbook is hard-wrapped, so a phrase can straddle a line break. A
    flat text makes the guards independent of the wrapping, which is what
    lets the negative tests mutate a phrase as an operator would read it.
    """
    return re.sub(r"\s+", " ", text).strip()


def _section_span(flat, heading):
    """Return the (start, end) offsets of a ``###`` section in flat text.

    The section runs from its heading to the next ``###`` heading. Returns
    None when the heading is absent.
    """
    match = re.search(r"### " + re.escape(heading), flat)
    if match is None:
        return None
    rest = flat[match.end():]
    following = re.search(r" ### ", rest)
    end = match.end() + (following.start() if following else len(rest))
    return match.start(), end


def _section(flat, heading):
    """Return the flat text of a ``###`` section ("" when it is absent)."""
    span = _section_span(flat, heading)
    if span is None:
        return ""
    return flat[span[0]:span[1]]


def _replace_in_section(flat, heading, old, new):
    """Return the flat text with ``old`` replaced inside one section only.

    The document repeats a few phrases (the edge stack's own "reverted by
    the next CI run" warning, for instance), so a document-wide replacement
    would mutate the wrong occurrence and the negative case would prove
    nothing.
    """
    span = _section_span(flat, heading)
    if span is None:
        raise AssertionError(
            "the runbook no longer contains the section {!r}; the negative "
            "case cannot be constructed and needs to be updated.".format(
                heading
            )
        )
    start, end = span
    body = flat[start:end]
    if old not in body:
        raise AssertionError(
            "the section {!r} no longer contains {!r}; the negative case "
            "cannot be constructed and needs to be updated.".format(
                heading, old
            )
        )
    # EVERY occurrence is replaced: a phrase the runbook repeats (the metric
    # name, a quoted step name) must disappear completely for the mutation
    # to mean "the operator is no longer told this".
    return flat[:start] + body.replace(old, new) + flat[end:]


def _runbook_has_the_rollout_section(flat):
    """The runbook must carry the rollout procedure, and it must be a body.

    A heading with two lines under it is not a procedure: an operator cannot
    execute a rollout from a title.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    return len(section) > 1000


def _runbook_names_both_phases_and_the_default(flat):
    """Both phases must be named, and the default one must be stated.

    Phase 1 (header live, guard inert) is the default: a deploy nobody
    reviewed must never refuse traffic. A runbook that names the phases
    without saying which one is live by default leaves the operator to
    guess.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if "Phase 1 (the default)" not in section:
        return False
    if "Phase 2" not in section:
        return False
    if 'default is `"false"`' not in section:
        return False
    if "ORIGIN_HEADER_SECRET" not in section:
        return False
    if "inert" not in section.lower():
        return False
    return "ENFORCE_ORIGIN_GUARD" in section


def _runbook_documents_the_preflight_command(flat):
    """The pre-flight must be documented, runnable, and automatic.

    The operator has to be able to run the pre-flight by hand, and the
    deploy job must run it as its own step before a deploy that would
    enforce -- otherwise a flip without a live header takes the site down.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if "python scripts/verify_origin_guard.py --expect header-live" not in (
        section
    ):
        return False
    if PREFLIGHT_STEP not in section:
        return False
    if "before the SAM deploy" not in section:
        return False
    # The command must reference a script that exists in the repository.
    return VERIFY_SCRIPT.is_file()


def _runbook_documents_how_to_flip_and_how_to_roll_back(flat):
    """The flip and the rollback must both be spelled out.

    The flip is ONE setting, and the rollback is that same setting back to
    "false" -- nothing else changes in either direction.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if 'ENFORCE_ORIGIN_GUARD: "true"' not in section:
        return False
    if 'ENFORCE_ORIGIN_GUARD: "false"' not in section:
        return False
    if "Change that single setting to" not in section:
        return False
    if "The rollback." not in section:
        return False
    if "Flip the setting back to" not in section:
        return False
    return "Push to `main`" in section


def _runbook_states_that_ci_owns_the_switch(flat):
    """The runbook must warn that CI reverts a manual deploy of the switch.

    The switch is owned by ONE workflow setting, so the deploy job passes it
    on every run. An operator who flips the stack by hand and does not read
    this would believe the guard is enforcing (or inert) when the next CI
    run has already put it back.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if "CI owns the switch" not in section:
        return False
    if "reverted by the next CI run" not in section:
        return False
    if "EnforceOriginGuard=" not in section:
        return False
    return ".github/workflows/deploy.yml" in section


def _runbook_states_the_secret_rotation_rule(flat):
    """The runbook must forbid rotating the secret while enforcing.

    A rotation changes the stamped value and the Lambda's secret together,
    and the new stamped value needs "a few minutes" to reach every edge.
    During that window legitimate requests are refused -- so the rotation
    happens in a phase-1 deploy, never in the deploy that enforces.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if re.search(
        r"never rotate[^.]*ORIGIN_SECRET[^.]*in the same deploy that "
        r"enables enforcement",
        section,
    ) is None:
        return False
    if "phase-1 deploy" not in section:
        return False
    if "a few minutes" not in section:
        return False
    return True


def _runbook_names_the_evidence_to_keep(flat):
    """The runbook must say what evidence to keep from both phases.

    A rollout nobody can prove afterwards is a rollout nobody can roll back
    confidently: the CI run, the witness output, the metric reading and the
    proof that no legitimate request was refused.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    if "CI run URL" not in section:
        return False
    if "ChemicAlly/OriginGuardRefusals" not in section:
        return False
    if "the public site served 200" not in section:
        return False
    if "live-probe" not in section:
        return False
    if "The evidence to keep." not in section:
        return False
    return True


def _runbook_points_the_old_rollout_note_at_the_new_section(flat):
    """The old "Priority 2" note must point at the new procedure.

    The note used to describe the rollout as a known one-time risk ("a
    one-time window of seconds"). That framing is wrong now: the rollout is
    a two-phase procedure, and the note must say so and point at it. The
    manual curl commands stay -- they are still the fastest way to see the
    two behaviours by hand.
    """
    section = _section(flat, PRIORITY_2_HEADING)
    if not section:
        return False
    if ROLLOUT_HEADING not in section:
        return False
    if "one-time window of seconds" in section:
        return False
    if "ENFORCE_ORIGIN_GUARD" not in section:
        return False
    if "curl" not in section:
        return False
    if "403" not in section or "200" not in section:
        return False
    return True


def _runbook_quotes_ci_step_names_that_exist(flat, workflow):
    """Every CI step name the runbook quotes must exist in the workflow.

    A runbook that says "read the 'Verify the origin guard rollout' step"
    while the workflow calls the step something else sends the operator
    looking for a step that does not exist.
    """
    section = _section(flat, ROLLOUT_HEADING)
    if not section:
        return False
    for step_name in (PREFLIGHT_STEP, ROLLOUT_WITNESS_STEP):
        if step_name not in section:
            return False
        if step_name not in workflow:
            return False
    return True


class OriginGuardRunbookTests(SimpleTestCase):
    """M007/S04: the runbook must carry the two-phase rollout procedure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.runbook = _flat(RUNBOOK.read_text(encoding="utf-8"))
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_runbook_has_the_rollout_section(self):
        self.assertTrue(
            _runbook_has_the_rollout_section(self.runbook),
            "OPS_PLAYBOOK.md must carry the section 'Rolling out the origin "
            "guard: header first, enforcement second'.",
        )

    def test_runbook_names_both_phases_and_the_default(self):
        self.assertTrue(
            _runbook_names_both_phases_and_the_default(self.runbook),
            "the rollout section must name phase 1 and phase 2 and say that "
            "phase 1 (header live, guard inert) is the default.",
        )

    def test_runbook_documents_the_preflight_command(self):
        self.assertTrue(
            _runbook_documents_the_preflight_command(self.runbook),
            "the rollout section must document "
            "`python scripts/verify_origin_guard.py --expect header-live` "
            "and the deploy job's automatic pre-flight step.",
        )

    def test_runbook_documents_how_to_flip_and_how_to_roll_back(self):
        self.assertTrue(
            _runbook_documents_how_to_flip_and_how_to_roll_back(self.runbook),
            "the rollout section must document the flip to "
            'ENFORCE_ORIGIN_GUARD: "true" and the rollback to "false".',
        )

    def test_runbook_states_that_ci_owns_the_switch(self):
        self.assertTrue(
            _runbook_states_that_ci_owns_the_switch(self.runbook),
            "the rollout section must state that a manual deploy is reverted "
            "by the next CI run unless the workflow setting is changed too.",
        )

    def test_runbook_states_the_secret_rotation_rule(self):
        self.assertTrue(
            _runbook_states_the_secret_rotation_rule(self.runbook),
            "the rollout section must forbid rotating ORIGIN_SECRET in the "
            "same deploy that enables enforcement.",
        )

    def test_runbook_names_the_evidence_to_keep(self):
        self.assertTrue(
            _runbook_names_the_evidence_to_keep(self.runbook),
            "the rollout section must name the evidence to keep: both "
            "phases' CI runs, the refusal metric reading and the public site "
            "serving 200.",
        )

    def test_runbook_points_the_old_rollout_note_at_the_new_section(self):
        self.assertTrue(
            _runbook_points_the_old_rollout_note_at_the_new_section(
                self.runbook
            ),
            "the 'Priority 2 -- Restrict Function URL' rollout note must "
            "point at the new rollout section and must no longer describe "
            "the rollout as a one-time risk.",
        )

    def test_runbook_quotes_ci_step_names_that_exist(self):
        self.assertTrue(
            _runbook_quotes_ci_step_names_that_exist(
                self.runbook, self.workflow
            ),
            "the CI step names the rollout section quotes must exist in "
            ".github/workflows/deploy.yml.",
        )


class OriginGuardRunbookNegativeTests(SimpleTestCase):
    """The guards above must fail when the runbook is gutted or hollowed.

    Each case mutates a copy of the tracked runbook and asserts that the
    corresponding guard rejects it, so a guard that silently stopped
    matching cannot keep the suite green while the procedure has rotted.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.runbook = _flat(RUNBOOK.read_text(encoding="utf-8"))

    def test_missing_rollout_section_is_detected(self):
        mutated = self.runbook.replace(
            "### " + ROLLOUT_HEADING,
            "### Origin guard rollout",
            1,
        )
        self.assertNotIn(
            "### " + ROLLOUT_HEADING,
            mutated,
            "the mutation must remove the section heading itself.",
        )
        self.assertFalse(
            _runbook_has_the_rollout_section(mutated),
            "the guard must fail when the rollout section is gone: the "
            "procedure would exist nowhere in the runbook.",
        )

    def test_hollowed_out_section_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            self.runbook[
                _section_span(self.runbook, ROLLOUT_HEADING)[0]:
                _section_span(self.runbook, ROLLOUT_HEADING)[1]
            ],
            "### " + ROLLOUT_HEADING + " TODO.",
        )
        self.assertFalse(
            _runbook_has_the_rollout_section(mutated),
            "the guard must fail when the section heading is kept but its "
            "body is removed: a title is not a procedure.",
        )

    def test_missing_default_phase_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "Phase 1 (the default)",
            "Phase 1",
        )
        self.assertFalse(
            _runbook_names_both_phases_and_the_default(mutated),
            "the guard must fail when the runbook no longer says which "
            "phase is the default.",
        )

    def test_missing_preflight_command_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "python scripts/verify_origin_guard.py --expect header-live",
            "python scripts/verify_origin_guard.py",
        )
        self.assertFalse(
            _runbook_documents_the_preflight_command(mutated),
            "the guard must fail when the pre-flight command no longer "
            "asks for the header-live state.",
        )

    def test_missing_preflight_step_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            PREFLIGHT_STEP,
            "the pre-flight step",
        )
        self.assertFalse(
            _runbook_documents_the_preflight_command(mutated),
            "the guard must fail when the runbook stops naming the deploy "
            "job's automatic pre-flight step.",
        )

    def test_missing_flip_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            'ENFORCE_ORIGIN_GUARD: "true"',
            'ENFORCE_ORIGIN_GUARD: "on"',
        )
        self.assertFalse(
            _runbook_documents_how_to_flip_and_how_to_roll_back(mutated),
            "the guard must fail when the runbook no longer documents the "
            "flip to \"true\".",
        )

    def test_missing_rollback_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "Flip the setting back to",
            "Change it back to",
        )
        self.assertFalse(
            _runbook_documents_how_to_flip_and_how_to_roll_back(mutated),
            "the guard must fail when the runbook no longer documents how "
            "to roll the switch back.",
        )

    def test_missing_ci_owns_the_switch_warning_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "reverted by the next CI run",
            "kept until the next CI run",
        )
        self.assertFalse(
            _runbook_states_that_ci_owns_the_switch(mutated),
            "the guard must fail when the warning that a manual deploy is "
            "reverted by the next CI run is removed.",
        )

    def test_missing_secret_rotation_rule_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "never rotate `ORIGIN_SECRET` in the same deploy that enables "
            "enforcement",
            "rotate `ORIGIN_SECRET` whenever it is convenient",
        )
        self.assertFalse(
            _runbook_states_the_secret_rotation_rule(mutated),
            "the guard must fail when the secret-rotation rule is removed: "
            "a rotation while enforcing refuses legitimate requests.",
        )

    def test_missing_evidence_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "CI run URL",
            "the CI run",
        )
        self.assertFalse(
            _runbook_names_the_evidence_to_keep(mutated),
            "the guard must fail when the runbook no longer names the "
            "evidence to keep.",
        )

    def test_missing_metric_evidence_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            "ChemicAlly/OriginGuardRefusals",
            "the refusal count",
        )
        self.assertFalse(
            _runbook_names_the_evidence_to_keep(mutated),
            "the guard must fail when the runbook no longer names the "
            "refusal metric the evidence is read from.",
        )

    def test_priority_2_pointing_away_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            PRIORITY_2_HEADING,
            ROLLOUT_HEADING,
            "the security section",
        )
        self.assertFalse(
            _runbook_points_the_old_rollout_note_at_the_new_section(mutated),
            "the guard must fail when the old rollout note stops pointing "
            "at the new procedure.",
        )

    def test_priority_2_still_describing_the_old_risk_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            PRIORITY_2_HEADING,
            "the procedure is",
            "the old note was",
        )
        mutated = mutated.replace(
            "and the deploy job refuses to enable enforcement until the "
            "origin header is live on the live distribution.",
            "a one-time window of seconds",
            1,
        )
        self.assertFalse(
            _runbook_points_the_old_rollout_note_at_the_new_section(mutated),
            "the guard must fail when the old note goes back to describing "
            "the rollout as a one-time risk.",
        )

    def test_quoted_step_name_that_does_not_exist_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            ROLLOUT_HEADING,
            ROLLOUT_WITNESS_STEP,
            "Verify the guard",
        )
        self.assertFalse(
            _runbook_quotes_ci_step_names_that_exist(
                mutated, DEPLOY_WORKFLOW.read_text(encoding="utf-8")
            ),
            "the guard must fail when the runbook quotes a CI step name "
            "the workflow does not have.",
        )

    def test_renamed_workflow_step_is_detected(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            ROLLOUT_WITNESS_STEP,
            workflow,
            "the workflow no longer contains the step {!r}; the negative "
            "case cannot be constructed and needs to be updated.".format(
                ROLLOUT_WITNESS_STEP
            ),
        )
        mutated_workflow = workflow.replace(
            ROLLOUT_WITNESS_STEP, "Verify the guard", 1
        )
        self.assertFalse(
            _runbook_quotes_ci_step_names_that_exist(
                self.runbook, mutated_workflow
            ),
            "the guard must fail when the deploy workflow renames a step "
            "the runbook tells the operator to read.",
        )
