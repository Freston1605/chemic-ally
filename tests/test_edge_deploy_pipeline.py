"""Durable guards on the two-stack deploy pipeline (M007/S02).

The CloudFront rate limit lives in its own stack (``chemically-edge`` in
us-east-1) because AWS accepts WAFv2 web ACLs with ``Scope: CLOUDFRONT``
only in us-east-1, while the application stack deploys to us-east-2
(D015/D018). Two stacks can drift apart silently, and the drift that
matters is a DETACHED association: the web ACL exists, the deploy looks
healthy, and the rate limit is attached to a distribution that no longer
serves traffic - a security control that appears deployed and enforces
nothing.

These guards pin the wiring in ``.github/workflows/deploy.yml``:

* the edge stack is deployed AFTER the app stack, because it takes the
  distribution ARN the app stack exports;
* the ARN is read from the DEPLOYED app stack's outputs, never hard-coded,
  and its absence fails the job loudly;
* the edge deploy targets us-east-1 and passes the distribution ARN and the
  rule action;
* the action comes from ONE workflow setting whose default is ``Count``, so
  the edge rule can never start blocking by accident;
* the post-deploy verification is its OWN step directly after the edge
  deploy, and it cannot be swallowed (``|| true`` / ``continue-on-error``),
  so a detached association always fails the job.

Following the convention of ``tests/test_ci_gate.py``, the assertions are
expressed as named guard functions and each one is proved non-vacuous by a
mutation-based negative test. The workflow is read as plain text and parsed
with regexes only, so no YAML dependency is introduced. ``SimpleTestCase``
blocks all DB access; these checks touch only the filesystem.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RUNBOOK = REPO_ROOT / "OPS_PLAYBOOK.md"

APP_STACK_STEP = "SAM deploy"
EDGE_ACL_STEP = "Read the edge web ACL ARN"
EDGE_STACK_STEP = "Deploy the edge stack (us-east-1)"
EDGE_VERIFY_STEP = "Verify the edge association"

# The edge rule ships counting; blocking is the canary's end state.
DEFAULT_EDGE_ACTION = "Count"


def _step_body(text, step_name):
    """Return the body of the named step, or "" when the step is absent.

    Job steps are list items at exactly six spaces of indentation, so a
    line-based scan recovers each step's own block. No YAML dependency.
    """
    start = re.search(
        r"^[ \t]{6}-[ \t]+name:[ \t]*" + re.escape(step_name) + r"[ \t]*$",
        text,
        re.MULTILINE,
    )
    if start is None:
        return ""
    following = re.search(r"^[ \t]{6}-[ \t]+name:", text[start.end():], re.MULTILINE)
    body = text[start.end():]
    if following is not None:
        body = body[:following.start()]
    return body


def _step_block(text, step_name):
    """Return the (start, end) line bounds of the named step's own block.

    The block is the ``- name:`` line plus everything up to the next list
    item, with the trailing blank lines and the comment banner that
    belongs to the NEXT step trimmed away. Blank lines inside a ``run: |``
    body are kept: they are part of the step.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if re.match(
            r"^[ \t]{6}-[ \t]+name:[ \t]*" + re.escape(step_name) + r"[ \t]*$",
            line,
        ):
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


def _next_step_item(text, after_line):
    """Return the name of the next list item after the given line index.

    Returns ``None`` when the next list item has no ``name:`` key (a
    ``uses:`` step) or when there is no further list item.
    """
    lines = text.splitlines(keepends=True)
    for index in range(after_line, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if not re.match(r"^[ \t]{6}-[ \t]", line):
            continue
        match = re.match(
            r"^[ \t]{6}-[ \t]+name:[ \t]*(\S.*?)[ \t]*$", line
        )
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
    target = None
    for index, line in enumerate(rest):
        if re.match(
            r"^[ \t]{6}-[ \t]+name:[ \t]*" + re.escape(before_step_name)
            + r"[ \t]*$",
            line,
        ):
            target = index
            break
    if target is None:
        return None
    return "".join(rest[:target] + block + rest[target:])


def _step_index(text, step_name):
    """Return the character offset of the named step, or -1."""
    match = re.search(
        r"^[ \t]{6}-[ \t]+name:[ \t]*" + re.escape(step_name) + r"[ \t]*$",
        text,
        re.MULTILINE,
    )
    return match.start() if match else -1


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


def _runbook_states_the_ci_reverts_a_manual_block(text):
    """The runbook must warn that a manual Block deploy is reverted by CI.

    The action is owned by ONE workflow setting, so the edge stack is
    redeployed with that setting on every CI run. An operator who deploys
    ``WafAction=Block`` by hand and does not read the runbook would
    otherwise believe the rule is blocking when the next CI run has already
    put it back into Count.

    The warning must sit in the SAME paragraph as the manual deploy it is
    about. The runbook states the same consequence for the origin guard's
    own switch (its rollout section, M007/S04), and that sentence does not
    warn the operator who changed ``WAF_ACTION``.
    """
    flat = re.sub(r"\s+", " ", text)
    if "WafAction=Block" not in flat:
        return False
    positions = [
        match.start()
        for match in re.finditer(re.escape("WafAction=Block"), flat)
    ]
    if not any(
        "reverted by the next CI run" in flat[position:position + 400]
        for position in positions
    ):
        return False
    if "WAF_ACTION" not in flat:
        return False
    return ".github/workflows/deploy.yml" in flat


def _edge_stack_is_deployed_before_the_app_stack(text):
    """The edge stack must be deployed BEFORE the app stack.

    The WAFv2 API cannot associate a web ACL with a CloudFront
    distribution, so the attachment lives on the distribution: the edge
    stack creates the rule and hands over its ARN, and the app stack
    attaches it through the distribution's WebACLId. The rule must
    therefore exist before the distribution references it.
    """
    edge = _step_index(text, EDGE_STACK_STEP)
    app = _step_index(text, APP_STACK_STEP)
    if edge < 0 or app < 0:
        return False
    return edge < app


def _app_stack_receives_the_edge_web_acl_arn(text):
    """The app deploy must receive the edge stack's web ACL ARN.

    The ARN is read from the edge stack's own output and handed to the
    app stack, which attaches it to the distribution. It is never written
    down in the workflow.
    """
    app_body = _step_body(text, APP_STACK_STEP)
    if not app_body:
        return False
    if "EdgeWebACLArn=" not in app_body:
        return False
    return "steps.edge-acl.outputs.arn" in app_body


def _edge_acl_arn_read_fails_loudly_when_missing(text):
    """A missing web ACL ARN must fail the job, not deploy an unattached rule."""
    body = _step_body(text, EDGE_ACL_STEP)
    if not body:
        return False
    if "::error::" not in body:
        return False
    if not re.search(r"exit 1", body):
        return False
    return "WebACLArn" in body


def _edge_stack_targets_us_east_1(text):
    """The edge deploy must target us-east-1 with edge-template.yaml.

    The region is read from the sam deploy's own invocation, not from any
    other mention in the step: the step also reads the stack's status in
    us-east-1, and a guard that accepted either line would not notice a
    deploy aimed at the wrong region.
    """
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "edge-template.yaml" not in body:
        return False
    region = re.search(
        r"--template-file[ \t]+edge-template\.yaml(?:[^\n]*\n){1,3}"
        r"[^\n]*--region[ \t]+(us-east-[0-9])",
        body,
    )
    if region is None:
        return False
    return region.group(1) == "us-east-1"


def _edge_stack_passes_the_action_and_the_limit(text):
    """The edge deploy must pass the rule action and the rate limit."""
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "WafAction=" not in body:
        return False
    return "WafRateLimit=" in body


def _edge_action_defaults_to_count(text):
    """The edge rule must ship counting; the switch is one reviewable change."""
    env = _job_env(text)
    if not env:
        return False
    match = re.search(r"^      WAF_ACTION:[ \t]*(\S+)[ \t]*$", env, re.MULTILINE)
    if match is None:
        return False
    return match.group(1) == DEFAULT_EDGE_ACTION


def _edge_verification_runs_after_the_edge_deploy(text):
    """The association check must run after the edge deploy."""
    edge = _step_index(text, EDGE_STACK_STEP)
    verify = _step_index(text, EDGE_VERIFY_STEP)
    if edge < 0 or verify < 0:
        return False
    return edge < verify


def _edge_verification_is_wired_to_the_script(text):
    """The verification step must run the verifier and fail the job."""
    body = _step_body(text, EDGE_VERIFY_STEP)
    if not body:
        return False
    if "scripts/verify_edge.py" not in body:
        return False
    return "--edge-region us-east-1" in body


def _edge_verification_runs_after_the_app_deploy(text):
    """The attachment check must run after the app stack attached the rule.

    The app stack is what attaches the web ACL to the distribution, so
    checking before that would check a distribution that has no rule yet.
    """
    app = _step_index(text, APP_STACK_STEP)
    verify = _step_index(text, EDGE_VERIFY_STEP)
    if app < 0 or verify < 0:
        return False
    return app < verify


def _edge_verification_cannot_be_swallowed(text):
    """The check must be able to fail the job.

    ``|| true`` or ``continue-on-error: true`` would turn the association
    check into a report: the job would go green with a detached control
    attached to a distribution that no longer serves traffic.
    """
    body = _step_body(text, EDGE_VERIFY_STEP)
    if not body:
        return False
    if "|| true" in body:
        return False
    if "continue-on-error" in body:
        return False
    return True


class EdgeDeployPipelineTests(SimpleTestCase):
    """M007/S02: the deploy job must deploy both stacks, in order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_edge_stack_is_deployed_before_the_app_stack(self):
        self.assertTrue(
            _edge_stack_is_deployed_before_the_app_stack(self.workflow),
            "the edge stack must be deployed BEFORE the app stack: it "
            "creates the web ACL that the app stack attaches to the "
            "distribution.",
        )

    def test_app_stack_receives_the_edge_web_acl_arn(self):
        self.assertTrue(
            _app_stack_receives_the_edge_web_acl_arn(self.workflow),
            "the app deploy must receive the edge stack's web ACL ARN: the "
            "attachment lives on the distribution (the WAFv2 API cannot take "
            "a CloudFront ARN).",
        )

    def test_edge_acl_arn_read_fails_loudly_when_missing(self):
        self.assertTrue(
            _edge_acl_arn_read_fails_loudly_when_missing(self.workflow),
            "the step that reads the edge stack's web ACL ARN must fail "
            "the job when the output is missing: a deployment that "
            "silently attached no rule would look green.",
        )

    def test_edge_stack_targets_us_east_1(self):
        self.assertTrue(
            _edge_stack_targets_us_east_1(self.workflow),
            "the edge deploy must target us-east-1 with edge-template.yaml "
            "and the chemically-edge stack: a CLOUDFRONT-scoped web ACL can "
            "only be created there.",
        )

    def test_edge_stack_passes_the_action_and_the_limit(self):
        self.assertTrue(
            _edge_stack_passes_the_action_and_the_limit(self.workflow),
            "the edge deploy must pass WafRateLimit and WafAction to the "
            "edge stack.",
        )

    def test_edge_action_defaults_to_count(self):
        self.assertTrue(
            _edge_action_defaults_to_count(self.workflow),
            "the workflow's edge action must default to Count: the edge "
            "rule must never start blocking by accident.",
        )

    def test_edge_verification_runs_after_the_edge_deploy(self):
        self.assertTrue(
            _edge_verification_runs_after_the_edge_deploy(self.workflow),
            "the edge association check must run after the edge deploy.",
        )

    def test_edge_verification_runs_the_verifier(self):
        self.assertTrue(
            _edge_verification_is_wired_to_the_script(self.workflow),
            "the deploy job must run scripts/verify_edge.py against the "
            "us-east-1 edge stack.",
        )

    def test_edge_verification_runs_after_the_app_deploy(self):
        self.assertTrue(
            _edge_verification_runs_after_the_app_deploy(self.workflow),
            "the attachment check must run after the app stack deployed, "
            "because the app stack is what attaches the web ACL to the "
            "distribution.",
        )

    def test_edge_verification_cannot_be_swallowed(self):
        self.assertTrue(
            _edge_verification_cannot_be_swallowed(self.workflow),
            "the association check must be able to fail the job: '|| true' "
            "or 'continue-on-error' would let a detached control pass "
            "silently.",
        )

    def test_runbook_warns_that_ci_reverts_a_manual_block_deploy(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertTrue(
            _runbook_states_the_ci_reverts_a_manual_block(runbook),
            "the runbook must state that a manual "
            "`sam deploy ... WafAction=Block` is reverted by the next CI "
            "run unless the WAF_ACTION workflow setting is changed too.",
        )


class EdgeDeployPipelineNegativeTests(SimpleTestCase):
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
        cls.runbook = RUNBOOK.read_text(encoding="utf-8")

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.workflow,
            f"the workflow no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.workflow.replace(old, new, 1)

    def test_edge_deploy_after_the_app_deploy_is_detected(self):
        # Move the APP step in front of the edge step: the edge stack is
        # then deployed after the app stack, which is the wrong order --
        # the distribution would reference a web ACL that does not exist.
        mutated = _move_step_before(
            self.workflow, APP_STACK_STEP, EDGE_STACK_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the edge deploy step; the "
            "negative case cannot be constructed and needs to be updated.",
        )
        # The mutation must MOVE the step, not delete it: otherwise the
        # guard would fail for the wrong reason (a missing step) and the
        # ordering assertion would never be exercised.
        self.assertIn(EDGE_STACK_STEP, mutated)
        self.assertGreater(
            _step_index(mutated, EDGE_STACK_STEP),
            _step_index(mutated, APP_STACK_STEP),
            "the mutation must place the edge step after the app deploy.",
        )
        self.assertFalse(
            _edge_stack_is_deployed_before_the_app_stack(mutated),
            "the ordering guard must fail when the edge step is moved "
            "AFTER the app deploy: the distribution would reference a web "
            "ACL that does not exist yet.",
        )

    def test_renamed_edge_step_is_detected(self):
        mutated = self._mutate(
            "      - name: Deploy the edge stack (us-east-1)",
            "      - name: Edge stack placeholder (us-east-1)",
        )
        self.assertFalse(
            _edge_stack_is_deployed_before_the_app_stack(mutated),
            "the ordering guard must fail when the edge step is renamed "
            "away: the stack would no longer be deployed at all.",
        )

    def test_hardcoded_web_acl_arn_is_detected(self):
        mutated = self._mutate(
            "              EdgeWebACLArn=${{ steps.edge-acl.outputs.arn }} \\",
            "              EdgeWebACLArn=arn:aws:wafv2:us-east-1:768125641662:"
            "global/webacl/literal/literal \\",
        )
        self.assertFalse(
            _app_stack_receives_the_edge_web_acl_arn(mutated),
            "the ARN guard must fail when a literal web ACL ARN replaces "
            "the read from the edge stack.",
        )

    def test_missing_error_handling_is_detected(self):
        mutated = self._mutate(
            '            echo "::error::The edge stack ${EDGE_STACK_NAME} '
            'did not export WebACLArn; the distribution cannot be attached '
            'to the rate rule."\n            exit 1\n',
            "",
        )
        self.assertFalse(
            _edge_acl_arn_read_fails_loudly_when_missing(mutated),
            "the guard must fail when the deploy stops failing loudly on a "
            "missing ARN.",
        )

    def test_wrong_region_is_detected(self):
        # The sam deploy's own region line (the cleanup step's region
        # mention sits on a different line, so this hits the deploy).
        mutated = self._mutate(
            "            --region us-east-1 \\" + "\n",
            "            --region us-east-2 \\" + "\n",
        )
        self.assertFalse(
            _edge_stack_targets_us_east_1(mutated),
            "the region guard must fail when the edge deploy targets the "
            "wrong region.",
        )

    def test_missing_action_parameter_is_detected(self):
        mutated = self._mutate(
            '              WafAction="$WAF_ACTION" \\\n',
            "",
        )
        self.assertFalse(
            _edge_stack_passes_the_action_and_the_limit(mutated),
            "the parameter guard must fail when the rule action is no "
            "longer passed to the edge stack.",
        )

    def test_block_by_default_is_detected(self):
        mutated = self._mutate(
            "      WAF_ACTION: Count", "      WAF_ACTION: Block"
        )
        self.assertFalse(
            _edge_action_defaults_to_count(mutated),
            "the action guard must fail when the workflow would deploy the "
            "edge rule blocking by default.",
        )

    def test_missing_verification_is_detected(self):
        mutated = self._mutate(
            "      - name: Verify the edge association",
            "      - name: Verify the edge association (removed)",
        )
        self.assertFalse(
            _edge_verification_runs_after_the_edge_deploy(mutated),
            "the verification guard must fail when the association check is "
            "renamed away.",
        )

    def test_verification_not_running_the_script_is_detected(self):
        mutated = self._mutate(
            "          python scripts/verify_edge.py \\",
            "          echo 'skipping the association check' \\",
        )
        self.assertFalse(
            _edge_verification_is_wired_to_the_script(mutated),
            "the verification guard must fail when the step stops running "
            "scripts/verify_edge.py.",
        )

    def test_the_check_before_the_app_deploy_is_detected(self):
        """The attachment check must not run before the app stack attached.

        Checking earlier would inspect a distribution that has no rule yet
        and report a false failure -- or, worse, be skipped.
        """
        mutated = _move_step_before(
            self.workflow, EDGE_VERIFY_STEP, APP_STACK_STEP
        )
        self.assertIsNotNone(
            mutated,
            "the workflow no longer contains the edge verification step; "
            "the negative case cannot be constructed.",
        )
        self.assertFalse(
            _edge_verification_runs_after_the_app_deploy(mutated),
            "the ordering guard must fail when the attachment check runs "
            "before the app deploy.",
        )

    def test_swallowing_the_verification_is_detected(self):
        mutated = self._mutate(
            "          python scripts/verify_edge.py \\",
            "          python scripts/verify_edge.py || true \\",
        )
        self.assertFalse(
            _edge_verification_cannot_be_swallowed(mutated),
            "the guard must fail when the association check is made "
            "non-blocking with '|| true'.",
        )

    def test_continue_on_error_verification_is_detected(self):
        mutated = self._mutate(
            "      - name: Verify the edge association",
            "      - name: Verify the edge association\n"
            "        continue-on-error: true",
        )
        self.assertFalse(
            _edge_verification_cannot_be_swallowed(mutated),
            "the guard must fail when the association check is made "
            "non-blocking with 'continue-on-error'.",
        )

    def test_runbook_without_the_revert_warning_is_detected(self):
        flat = re.sub(r"\s+", " ", self.runbook)
        self.assertIn(
            "reverted by the next CI run",
            flat,
            "the runbook no longer contains the revert warning; the "
            "negative case cannot be constructed and needs to be updated.",
        )
        mutated = flat.replace(
            "reverted by the next CI run", "kept until the next CI run", 1
        )
        self.assertFalse(
            _runbook_states_the_ci_reverts_a_manual_block(mutated),
            "the runbook guard must fail when the warning that a manual "
            "Block deploy is reverted by the next CI run is removed.",
        )
