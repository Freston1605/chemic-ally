"""Durable guards on the edge canary's runbook procedure (M007/S05/T04).

The edge canary is a PROCEDURE. The observer (M007/S05/T01), the probe
(M007/S05/T02) and the CI wiring (M007/S05/T03) make the Count -> Block
switch provable, but a procedure nobody can follow is not a control: if
the runbook does not say how long to watch, what the counters mean, what
"no data" is not, how to flip, what the proof shows, which evidence to
keep and how to roll back, the flip is a parameter change nobody can
check -- and the budget review (M007/S06) has no procedure to point at.

``OPS_PLAYBOOK.md`` now carries the section "Canarying the edge rate rule:
Count first, then Block", placed between the two-stack deployment section
and the origin guard's rollout section. These guards pin that section so
it cannot be quietly deleted or hollowed out:

* the section exists, is not a stub, and sits where it is supposed to sit;
* it names both phases and says which one is the DEFAULT (Count);
* it states that CI owns the switch -- a manual deploy is reverted by the
  next CI run unless the workflow setting is changed at the same time;
* it says the window is a judgement call about traffic, not a constant;
* it quotes the observer command with the real script path and the real
  flags, and says what the observer proves;
* it names the three counters, the ``AWS/WAFV2`` namespace and the
  ``EdgeRateLimit`` rule;
* it states the "no data is not zero traffic" rule;
* it states the ceiling-breach rule (and that the ceiling is NOT retuned
  here);
* it names the budget-review hand-off;
* it documents the flip, what the probe proves, the burst's safety
  bounds, the evidence to keep, the rollback and the us-east-1 read
  permissions the canary needs;
* it says what to read when the canary looks wrong;
* it names the CI step names the operator will look for.

Every match AND every mutation is scoped to the canary section alone
(MEM073). Two unrelated switches legitimately share the phrase "reverted
by the next CI run" -- the edge rule's and the origin guard's -- so a
document-wide match or a document-wide mutation would couple two
procedures and break the other section's guard.

The guards also tie the runbook to the repository it describes: the
scripts it tells the operator to run exist, and the CI step names it
quotes are the step names the deploy workflow really has. A runbook that
names a step that was renamed sends the operator looking for a step that
is not there.

Following the convention of ``tests/test_origin_guard_runbook.py``
(M007/S04), ``tests/test_edge_deploy_pipeline.py`` (M007/S02) and
``tests/test_edge_canary_pipeline.py`` (M007/S05/T03), the assertions are
named guard functions proved non-vacuous by mutation-based negative
tests. The runbook is read as plain text and matched with regexes only;
``SimpleTestCase`` blocks all DB access; these checks touch only the
filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = REPO_ROOT / "OPS_PLAYBOOK.md"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

#: The heading of the canary procedure.
CANARY_HEADING = "Canarying the edge rate rule: Count first, then Block"

#: The section the canary procedure must follow ...
TWO_STACK_HEADING = (
    "The two-stack deployment: app stack (us-east-2) and edge stack "
    "(us-east-1)"
)

#: ... and the section it must precede.
ORIGIN_GUARD_HEADING = (
    "Rolling out the origin guard: header first, enforcement second"
)

#: The deploy job's step names the runbook quotes. They must exist in the
#: deploy workflow, otherwise the operator is told to read a step that is
#: not there.
ACTION_READ_STEP = "Read the live edge rule action"
OBSERVER_STEP = "Observe the edge canary (Count mode)"
PROBE_STEP = "Prove the edge block (Block mode)"

#: The observer command, exactly as the runbook quotes it.
OBSERVER_COMMAND = (
    "python scripts/verify_edge_canary.py"
    " --edge-stack-name chemically-edge"
    " --edge-region us-east-1"
    " --expect-action Count"
    " --minutes 1440"
    " --site-url https://chemic-ally.xyz"
    " --record-out edge-canary-count-record.json"
)

#: The probe command, exactly as the runbook quotes it.
PROBE_COMMAND = (
    "python scripts/verify_edge_block.py"
    " --edge-stack-name chemically-edge"
    " --edge-region us-east-1"
    " --site-url https://chemic-ally.xyz"
    " --path /hplc/api/levels/"
    " --confirm-live"
    " --record-out edge-canary-block-record.json"
)

#: The scripts the runbook tells the operator (or the deploy job) to run.
OBSERVER_SCRIPT = REPO_ROOT / "scripts" / "verify_edge_canary.py"
PROBE_SCRIPT = REPO_ROOT / "scripts" / "verify_edge_block.py"

#: The three counters the runbook must explain.
COUNTERS = ("AllowedRequests", "CountedRequests", "BlockedRequests")


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

    The document repeats a few phrases (the "reverted by the next CI run"
    warning belongs to TWO different switches), so a document-wide
    replacement would mutate the wrong occurrence and the negative case
    would prove nothing (MEM073).
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
    # EVERY occurrence is replaced: a phrase the runbook repeats (a
    # counter name, a quoted step name) must disappear completely for the
    # mutation to mean "the operator is no longer told this".
    return flat[:start] + body.replace(old, new) + flat[end:]


def _move_section_to_the_end(flat, heading):
    """Return the flat text with one section moved to the very end."""
    span = _section_span(flat, heading)
    if span is None:
        raise AssertionError(
            "the runbook no longer contains the section {!r}; the negative "
            "case cannot be constructed and needs to be updated.".format(
                heading
            )
        )
    start, end = span
    chunk = flat[start:end]
    rest = (flat[:start] + flat[end:]).rstrip()
    return rest + " " + chunk


def _canary_section_exists_and_is_not_a_stub(flat):
    """The runbook must carry the canary procedure, and it must be a body.

    A heading with two lines under it is not a procedure: an operator
    cannot execute a canary from a title.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    return len(section) > 1500


def _canary_section_sits_between_the_two_stacks_and_the_origin_guard(flat):
    """The procedure must live next to the two-stack and origin-guard ones.

    The operator reads the two-stack section (how the edge stack is
    deployed) and the origin guard's rollout section (how the other
    switch is flipped). The canary procedure belongs between them: it
    describes the switch the two-stack section hands over, and it must not
    be buried after the section that follows it.
    """
    two_stack = flat.find("### " + TWO_STACK_HEADING)
    canary = flat.find("### " + CANARY_HEADING)
    origin_guard = flat.find("### " + ORIGIN_GUARD_HEADING)
    if two_stack < 0 or canary < 0 or origin_guard < 0:
        return False
    return two_stack < canary < origin_guard


def _canary_section_names_both_phases_and_the_default(flat):
    """Both phases must be named, and the default one must be stated.

    Count (measure, refuse nothing) is the default: a deploy nobody
    reviewed must never refuse traffic. A runbook that names the phases
    without saying which one is live by default leaves the operator to
    guess.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "without touching a single request" not in section:
        return False
    if "Refuses the request at the edge, before it reaches Lambda" not in (
        section
    ):
        return False
    if "Its default is `Count`" not in section:
        return False
    if "`WAF_ACTION`" not in section:
        return False
    return ".github/workflows/deploy.yml" in section


def _canary_section_states_that_ci_owns_the_switch(flat):
    """The runbook must warn that CI reverts a manual Block deploy.

    The action is owned by ONE workflow setting, so the edge stack is
    redeployed with that setting on every CI run. An operator who deploys
    the edge stack by hand and does not read this would believe the rule
    is blocking when the next CI run has already put it back.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "CI owns the switch" not in section:
        return False
    if "reverted by the next CI run" not in section:
        return False
    if "WafAction=Block" not in section:
        return False
    return "WAF_ACTION" in section


def _canary_section_says_the_window_is_a_judgement_call(flat):
    """The window's length must be a decision, not a constant.

    How long the rule counts depends on how much real traffic has to pass
    before the counters mean something. The decision belongs to the
    operator, with the counters in hand -- not to a hard-coded number
    nobody reasoned about.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "how much real traffic has to pass" not in section:
        return False
    if "The decision belongs to the operator" not in section:
        return False
    return "counters in hand" in section


def _canary_section_quotes_the_observer_command(flat):
    """The observer must be documented, runnable, and honest about scope.

    The operator has to be able to run the observer by hand, with the
    real script path and the real flags, and the runbook must say what
    the run proves: the live action, the live ceiling, the counters and
    the fact that the ordinary site still serves 200.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if OBSERVER_COMMAND not in section:
        return False
    if "read from the LIVE web ACL" not in section:
        return False
    if "the ordinary site still serves 200" not in section:
        return False
    return OBSERVER_SCRIPT.is_file()


def _canary_section_names_the_three_counters(flat):
    """The three counters must be named, with their namespace and rule."""
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    for counter in COUNTERS:
        if counter not in section:
            return False
    if "AWS/WAFV2" not in section:
        return False
    return "EdgeRateLimit" in section


def _canary_section_states_that_no_data_is_not_zero_traffic(flat):
    """An empty window must not be read as a quiet site.

    The observer distinguishes three states per counter (value, observed
    zero, unknown) precisely because "no datapoints" is not "zero
    requests". The runbook must carry that rule, because it is the rule
    that stops an operator from reading an empty window as "the site was
    quiet, flip it".
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "no data is not zero traffic" not in section.lower():
        return False
    return "--require-data" in section


def _canary_section_states_the_ceiling_breach_rule(flat):
    """The ceiling-breach rule must be stated, with the ceiling's status.

    ``CountedRequests > 0`` means real requests WOULD have been refused.
    The ceiling is inherited and is NOT retuned here (D021): the recorded
    numbers are the input to the budget review, not a licence to change
    the limit inside a canary.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "CountedRequests > 0" not in section:
        return False
    if "2000/300s" not in section:
        return False
    if "WOULD have been refused" not in section:
        return False
    return "D021" in section


def _canary_section_states_the_budget_review_handoff(flat):
    """The recorded numbers must be handed to the budget review.

    The observer's JSON record is the evidence the budget review reads,
    and the review -- not the canary -- decides whether 2000/300s stays.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "`edge-canary-count-record` artifact" not in section:
        return False
    if "budget review" not in section:
        return False
    return "decides whether 2000/300s stays" in section


def _canary_section_documents_the_flip(flat):
    """The flip must be spelled out as ONE reviewed change.

    The switch is a single workflow setting, and the deploy job does the
    proving by itself: the operator must not have to run the probe by
    hand to make the flip real.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "Change `WAF_ACTION` to `Block`" not in section:
        return False
    if "push to `main`" not in section:
        return False
    return "runs the block probe automatically" in section


def _canary_section_documents_what_the_probe_proves(flat):
    """The probe's proof must be documented, claim by claim.

    The probe proves five things at the edge: a 403 that is not the
    origin guard's body, an origin answer before the trip, a moved
    WAF counter, an origin-side counter that did NOT grow, and an
    ordinary site that still serves 200.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "/hplc/api/levels/" not in section:
        return False
    if "403 whose body is NOT the origin guard's" not in section:
        return False
    if "WAS answered by the origin" not in section:
        return False
    if "the origin never saw them" not in section:
        return False
    return "still serves 200 afterwards" in section


def _canary_section_states_the_burst_safety_bounds(flat):
    """The burst's bounds must be stated, so nobody runs it unbounded.

    The probe is the only deliberate traffic this project sends to
    production. It is capped, paced, confined to one cheap read-only
    path, and it runs last so nothing after it can be affected.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if re.search(r"`--requests`, default 2600(?![0-9])", section) is None:
        return False
    if re.search(r"`--rate`, default 12/s(?![0-9])", section) is None:
        return False
    if "per source IP" not in section:
        return False
    if "equilibria solver path is never used by the probe" not in section:
        return False
    return "LAST step of the deploy job" in section


def _canary_section_names_the_evidence_to_keep(flat):
    """The evidence to keep must be named.

    A canary nobody can prove afterwards is a canary nobody can roll
    back confidently: the CI run, the observer's lines, the probe's
    lines, both artifacts, the counter readings and the green live probe.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "CI run URL" not in section:
        return False
    if "edge-canary-count-record" not in section:
        return False
    if "edge-canary-block-record" not in section:
        return False
    if "counter readings" not in section:
        return False
    return "live-probe" in section


def _canary_section_says_what_to_read_when_it_looks_wrong(flat):
    """The operator must be told what to read when the canary misbehaves.

    The observer and the probe print a FAIL line that names the contract
    that failed; the BlockedRequests counter and the live-probe job are
    the two other signals.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "What to read when the canary looks wrong" not in section:
        return False
    if "FAIL line" not in section:
        return False
    if "`BlockedRequests` counter" not in section:
        return False
    return "live-probe" in section


def _canary_section_documents_the_rollback(flat):
    """The rollback must be as findable as the flip.

    The rollback is the same setting, back to Count, and it is a
    parameter change rather than a code change -- so it can be made in
    minutes, and it must hold, which means it must be made in the
    workflow.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "Set `WAF_ACTION` back to `Count`" not in section:
        return False
    if "push to `main`" not in section:
        return False
    if "parameter change, not a code change" not in section:
        return False
    return "CI owns the switch" in section


def _canary_section_names_the_required_permissions(flat):
    """The permissions the canary needs must be named.

    The canary needs CloudWatch READ in us-east-1 -- which the edge
    deploy alone does not need -- plus the origin-side Lambda read in
    us-east-2.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    for permission in (
        "wafv2:GetWebACL",
        "cloudformation:DescribeStacks",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricStatistics",
    ):
        if permission not in section:
            return False
    if "us-east-1" not in section:
        return False
    return "us-east-2" in section


def _canary_section_names_the_ci_step_names(flat, workflow):
    """Every CI step name the runbook quotes must exist in the workflow.

    A runbook that says "read the 'Prove the edge block (Block mode)'
    step" while the workflow calls the step something else sends the
    operator looking for a step that does not exist.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    for step_name in (ACTION_READ_STEP, OBSERVER_STEP, PROBE_STEP):
        if step_name not in section:
            return False
        if step_name not in workflow:
            return False
    return True


def _two_stack_section_points_at_the_canary_procedure(flat):
    """The two-stack section must point at the ONE canary procedure.

    The two-stack section keeps the raw switch command (that is the
    deploy recipe), but the procedure itself -- window, counters, flip,
    evidence, rollback -- lives in exactly one place. Without the
    pointer the runbook would carry two canary procedures: the command in
    one section and the procedure in another.
    """
    section = _section(flat, TWO_STACK_HEADING)
    if not section:
        return False
    return CANARY_HEADING in section


def _the_scripts_the_runbook_names_exist(flat):
    """The scripts the runbook names must exist in the repository.

    A runbook that tells the operator to run a script that was renamed
    or deleted is worse than no runbook: it sends the operator to a
    command that fails at 3am.
    """
    section = _section(flat, CANARY_HEADING)
    if not section:
        return False
    if "scripts/verify_edge_canary.py" not in section:
        return False
    if "scripts/verify_edge_block.py" not in section:
        return False
    if OBSERVER_COMMAND not in section:
        return False
    if PROBE_COMMAND not in section:
        return False
    return OBSERVER_SCRIPT.is_file() and PROBE_SCRIPT.is_file()


class EdgeCanaryRunbookTests(SimpleTestCase):
    """M007/S05/T04: the runbook must carry the canary procedure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.runbook = _flat(RUNBOOK.read_text(encoding="utf-8"))
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_canary_section_exists_and_is_not_a_stub(self):
        self.assertTrue(
            _canary_section_exists_and_is_not_a_stub(self.runbook),
            "OPS_PLAYBOOK.md must carry the section 'Canarying the edge "
            "rate rule: Count first, then Block'.",
        )

    def test_canary_section_sits_between_the_two_stacks_and_the_guard(self):
        self.assertTrue(
            _canary_section_sits_between_the_two_stacks_and_the_origin_guard(
                self.runbook
            ),
            "the canary section must sit immediately after the two-stack "
            "section and immediately before the origin guard section.",
        )

    def test_canary_section_names_both_phases_and_the_default(self):
        self.assertTrue(
            _canary_section_names_both_phases_and_the_default(self.runbook),
            "the canary section must name the Count and Block phases and "
            "say that Count is the default.",
        )

    def test_canary_section_states_that_ci_owns_the_switch(self):
        self.assertTrue(
            _canary_section_states_that_ci_owns_the_switch(self.runbook),
            "the canary section must state that a manual Block deploy is "
            "reverted by the next CI run unless the workflow setting is "
            "changed too.",
        )

    def test_canary_section_says_the_window_is_a_judgement_call(self):
        self.assertTrue(
            _canary_section_says_the_window_is_a_judgement_call(
                self.runbook
            ),
            "the canary section must say the window's length is decided by "
            "the operator from the traffic, not by a constant.",
        )

    def test_canary_section_quotes_the_observer_command(self):
        self.assertTrue(
            _canary_section_quotes_the_observer_command(self.runbook),
            "the canary section must quote the observer command with the "
            "real script path and the real flags.",
        )

    def test_canary_section_names_the_three_counters(self):
        self.assertTrue(
            _canary_section_names_the_three_counters(self.runbook),
            "the canary section must name AllowedRequests, CountedRequests "
            "and BlockedRequests, the AWS/WAFV2 namespace and the "
            "EdgeRateLimit rule.",
        )

    def test_canary_section_states_that_no_data_is_not_zero_traffic(self):
        self.assertTrue(
            _canary_section_states_that_no_data_is_not_zero_traffic(
                self.runbook
            ),
            "the canary section must state that no data is not zero "
            "traffic.",
        )

    def test_canary_section_states_the_ceiling_breach_rule(self):
        self.assertTrue(
            _canary_section_states_the_ceiling_breach_rule(self.runbook),
            "the canary section must state the ceiling-breach rule and that "
            "the ceiling is not retuned here.",
        )

    def test_canary_section_states_the_budget_review_handoff(self):
        self.assertTrue(
            _canary_section_states_the_budget_review_handoff(self.runbook),
            "the canary section must hand the observer's record to the "
            "budget review.",
        )

    def test_canary_section_documents_the_flip(self):
        self.assertTrue(
            _canary_section_documents_the_flip(self.runbook),
            "the canary section must document the flip to WAF_ACTION=Block "
            "and the push to main.",
        )

    def test_canary_section_documents_what_the_probe_proves(self):
        self.assertTrue(
            _canary_section_documents_what_the_probe_proves(self.runbook),
            "the canary section must document what the block probe sends "
            "and what it proves at the edge.",
        )

    def test_canary_section_states_the_burst_safety_bounds(self):
        self.assertTrue(
            _canary_section_states_the_burst_safety_bounds(self.runbook),
            "the canary section must state the burst's bound: capped, paced "
            "and confined to one cheap read-only path.",
        )

    def test_canary_section_names_the_evidence_to_keep(self):
        self.assertTrue(
            _canary_section_names_the_evidence_to_keep(self.runbook),
            "the canary section must name the evidence to keep.",
        )

    def test_canary_section_says_what_to_read_when_it_looks_wrong(self):
        self.assertTrue(
            _canary_section_says_what_to_read_when_it_looks_wrong(
                self.runbook
            ),
            "the canary section must tell the operator what to read when "
            "the canary looks wrong.",
        )

    def test_canary_section_documents_the_rollback(self):
        self.assertTrue(
            _canary_section_documents_the_rollback(self.runbook),
            "the canary section must document the rollback to "
            "WAF_ACTION=Count.",
        )

    def test_canary_section_names_the_required_permissions(self):
        self.assertTrue(
            _canary_section_names_the_required_permissions(self.runbook),
            "the canary section must name the us-east-1 read permissions "
            "the canary needs.",
        )

    def test_canary_section_names_the_ci_step_names(self):
        self.assertTrue(
            _canary_section_names_the_ci_step_names(
                self.runbook, self.workflow
            ),
            "the CI step names the canary section quotes must exist in "
            ".github/workflows/deploy.yml.",
        )

    def test_two_stack_section_points_at_the_canary_procedure(self):
        self.assertTrue(
            _two_stack_section_points_at_the_canary_procedure(self.runbook),
            "the two-stack section must point at the canary procedure so "
            "the runbook carries exactly one.",
        )

    def test_the_scripts_the_runbook_names_exist(self):
        self.assertTrue(
            _the_scripts_the_runbook_names_exist(self.runbook),
            "the scripts the canary section names must exist in the "
            "repository.",
        )


class EdgeCanaryRunbookNegativeTests(SimpleTestCase):
    """The guards above must fail when the runbook is gutted or hollowed.

    Each case mutates a copy of the tracked runbook and asserts that the
    corresponding guard rejects it, so a guard that silently stopped
    matching cannot keep the suite green while the procedure has rotted.

    EVERY mutation is scoped to ONE section: the document repeats the
    "reverted by the next CI run" warning for two different switches, and
    a document-wide mutation would couple the canary procedure to the
    origin guard's rollout (MEM073).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.runbook = _flat(RUNBOOK.read_text(encoding="utf-8"))

    def test_missing_canary_section_is_detected(self):
        mutated = self.runbook.replace(
            "### " + CANARY_HEADING,
            "### Edge canary",
            1,
        )
        self.assertNotIn(
            "### " + CANARY_HEADING,
            mutated,
            "the mutation must remove the section heading itself.",
        )
        self.assertFalse(
            _canary_section_exists_and_is_not_a_stub(mutated),
            "the guard must fail when the canary section is gone: the "
            "procedure would exist nowhere in the runbook.",
        )

    def test_hollowed_out_section_is_detected(self):
        span = _section_span(self.runbook, CANARY_HEADING)
        mutated = self.runbook.replace(
            self.runbook[span[0]:span[1]],
            "### " + CANARY_HEADING + " TODO.",
            1,
        )
        self.assertFalse(
            _canary_section_exists_and_is_not_a_stub(mutated),
            "the guard must fail when the section heading is kept but its "
            "body is removed: a title is not a procedure.",
        )

    def test_section_moved_to_the_end_is_detected(self):
        mutated = _move_section_to_the_end(self.runbook, CANARY_HEADING)
        self.assertFalse(
            _canary_section_sits_between_the_two_stacks_and_the_origin_guard(
                mutated
            ),
            "the guard must fail when the canary section is moved away "
            "from the two-stack section it belongs after.",
        )

    def test_missing_default_phase_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "Its default is `Count`",
            "Its default is `Block`",
        )
        self.assertFalse(
            _canary_section_names_both_phases_and_the_default(mutated),
            "the guard must fail when the runbook no longer says which "
            "phase is the default.",
        )

    def test_missing_block_phase_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "Refuses the request at the edge, before it reaches Lambda",
            "Does something else entirely",
        )
        self.assertFalse(
            _canary_section_names_both_phases_and_the_default(mutated),
            "the guard must fail when the Block phase is no longer "
            "described.",
        )

    def test_missing_ci_owns_the_switch_warning_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "reverted by the next CI run",
            "kept until the next CI run",
        )
        self.assertFalse(
            _canary_section_states_that_ci_owns_the_switch(mutated),
            "the guard must fail when the warning that a manual deploy is "
            "reverted by the next CI run is removed from the canary "
            "section.",
        )

    def test_missing_window_judgement_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "The decision belongs to the operator",
            "The deploy job decides",
        )
        self.assertFalse(
            _canary_section_says_the_window_is_a_judgement_call(mutated),
            "the guard must fail when the window's length stops being the "
            "operator's decision.",
        )

    def test_wrong_observer_command_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "--expect-action Count",
            "--expect-action Block",
        )
        self.assertFalse(
            _canary_section_quotes_the_observer_command(mutated),
            "the guard must fail when the observer command no longer asks "
            "for the Count action.",
        )

    def test_missing_counter_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "CountedRequests",
            "Requests",
        )
        self.assertFalse(
            _canary_section_names_the_three_counters(mutated),
            "the guard must fail when one of the three counters is no "
            "longer named.",
        )

    def test_missing_namespace_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "AWS/WAFV2",
            "AWS/WAF",
        )
        self.assertFalse(
            _canary_section_names_the_three_counters(mutated),
            "the guard must fail when the counters' namespace is no longer "
            "named.",
        )

    def test_missing_no_data_rule_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "No data is not zero traffic",
            "No data means zero traffic",
        )
        self.assertFalse(
            _canary_section_states_that_no_data_is_not_zero_traffic(mutated),
            "the guard must fail when the no-data-is-not-zero-traffic rule "
            "is removed.",
        )

    def test_missing_ceiling_breach_rule_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "CountedRequests > 0",
            "CountedRequests = 0",
        )
        self.assertFalse(
            _canary_section_states_the_ceiling_breach_rule(mutated),
            "the guard must fail when the ceiling-breach rule is removed.",
        )

    def test_missing_budget_review_handoff_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "budget review",
            "the canary",
        )
        self.assertFalse(
            _canary_section_states_the_budget_review_handoff(mutated),
            "the guard must fail when the budget-review hand-off is "
            "removed.",
        )

    def test_missing_flip_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "Change `WAF_ACTION` to `Block`",
            "Change `WAF_ACTION` to `Allow`",
        )
        self.assertFalse(
            _canary_section_documents_the_flip(mutated),
            "the guard must fail when the flip to Block is no longer "
            "documented.",
        )

    def test_missing_origin_side_proof_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "the origin never saw them",
            "the origin saw them",
        )
        self.assertFalse(
            _canary_section_documents_what_the_probe_proves(mutated),
            "the guard must fail when the origin-side proof is removed.",
        )

    def test_unbounded_burst_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "`--requests`, default 2600",
            "`--requests`, default 26000",
        )
        self.assertFalse(
            _canary_section_states_the_burst_safety_bounds(mutated),
            "the guard must fail when the burst's cap is no longer stated.",
        )

    def test_missing_evidence_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "CI run URL",
            "the CI run",
        )
        self.assertFalse(
            _canary_section_names_the_evidence_to_keep(mutated),
            "the guard must fail when the runbook no longer names the "
            "evidence to keep.",
        )

    def test_missing_troubleshooting_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "What to read when the canary looks wrong",
            "Troubleshooting",
        )
        self.assertFalse(
            _canary_section_says_what_to_read_when_it_looks_wrong(mutated),
            "the guard must fail when the runbook stops telling the "
            "operator what to read when the canary looks wrong.",
        )

    def test_missing_rollback_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "Set `WAF_ACTION` back to `Count`",
            "Set `WAF_ACTION` back to `Allow`",
        )
        self.assertFalse(
            _canary_section_documents_the_rollback(mutated),
            "the guard must fail when the rollback is no longer "
            "documented.",
        )

    def test_missing_permission_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "cloudwatch:GetMetricStatistics",
            "cloudwatch:GetMetricData",
        )
        self.assertFalse(
            _canary_section_names_the_required_permissions(mutated),
            "the guard must fail when a required us-east-1 read permission "
            "is no longer named.",
        )

    def test_quoted_step_name_that_does_not_exist_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            PROBE_STEP,
            "Prove the edge block",
        )
        self.assertFalse(
            _canary_section_names_the_ci_step_names(
                mutated, DEPLOY_WORKFLOW.read_text(encoding="utf-8")
            ),
            "the guard must fail when the runbook quotes a CI step name "
            "the workflow does not have.",
        )

    def test_renamed_workflow_step_is_detected(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            OBSERVER_STEP,
            workflow,
            "the workflow no longer contains the step {!r}; the negative "
            "case cannot be constructed and needs to be updated.".format(
                OBSERVER_STEP
            ),
        )
        mutated_workflow = workflow.replace(
            OBSERVER_STEP, "Observe the edge canary", 1
        )
        self.assertFalse(
            _canary_section_names_the_ci_step_names(
                self.runbook, mutated_workflow
            ),
            "the guard must fail when the deploy workflow renames a step "
            "the runbook tells the operator to read.",
        )

    def test_two_stack_pointer_removed_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            TWO_STACK_HEADING,
            CANARY_HEADING,
            "the security section",
        )
        self.assertFalse(
            _two_stack_section_points_at_the_canary_procedure(mutated),
            "the guard must fail when the two-stack section stops pointing "
            "at the one canary procedure.",
        )

    def test_missing_probe_script_is_detected(self):
        mutated = _replace_in_section(
            self.runbook,
            CANARY_HEADING,
            "scripts/verify_edge_block.py",
            "scripts/verify_edge_blocks.py",
        )
        self.assertFalse(
            _the_scripts_the_runbook_names_exist(mutated),
            "the guard must fail when the runbook names a script the "
            "repository does not have.",
        )
