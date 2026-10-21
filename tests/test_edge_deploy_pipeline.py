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
* the post-deploy verification runs after the edge deploy and fails the job
  when the association is missing or points at another distribution.

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

APP_STACK_STEP = "SAM deploy"
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


def _edge_stack_is_deployed_after_the_app_stack(text):
    """The edge stack must be deployed after the app stack."""
    app = _step_index(text, APP_STACK_STEP)
    edge = _step_index(text, EDGE_STACK_STEP)
    if app < 0 or edge < 0:
        return False
    return app < edge


def _edge_stack_reads_the_arn_from_the_deployed_stack(text):
    """The ARN must come from the app stack's outputs, not from a literal."""
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "describe-stacks" not in body:
        return False
    if "DistributionArn" not in body:
        return False
    if "--stack-name chemically" not in body:
        return False
    # A literal ARN in the workflow would be a stale association waiting to
    # happen; the ARN must be read, never written down.
    return re.search(r"distribution/[A-Z0-9]{6,}", body) is None


def _edge_stack_deploy_fails_loudly_without_the_arn(text):
    """A missing DistributionArn output must fail the job."""
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "::error::" not in body:
        return False
    if not re.search(r"exit 1", body):
        return False
    return re.search(r'\[ -z "\$dist_arn" \]', body) is not None


def _edge_stack_targets_us_east_1(text):
    """The edge deploy must target us-east-1 (the only region that accepts it)."""
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "--region us-east-1" not in body:
        return False
    if "edge-template.yaml" not in body:
        return False
    # The stack name is declared once in the job's env block, so the deploy
    # step and the verification step can never disagree about it.
    env = _job_env(text)
    if not re.search(r"^      EDGE_STACK_NAME:[ \t]*chemically-edge[ \t]*$",
                     env, re.MULTILINE):
        return False
    return "EDGE_STACK_NAME" in body


def _edge_stack_passes_the_arn_and_the_action(text):
    """The edge deploy must pass the distribution ARN and the rule action."""
    body = _step_body(text, EDGE_STACK_STEP)
    if not body:
        return False
    if "DistributionArn=" not in body:
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


class EdgeDeployPipelineTests(SimpleTestCase):
    """M007/S02: the deploy job must deploy both stacks, in order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_edge_stack_is_deployed_after_the_app_stack(self):
        self.assertTrue(
            _edge_stack_is_deployed_after_the_app_stack(self.workflow),
            "the edge stack must be deployed AFTER the app stack: it takes "
            "the distribution ARN the app stack exports.",
        )

    def test_edge_stack_reads_the_arn_from_the_deployed_stack(self):
        self.assertTrue(
            _edge_stack_reads_the_arn_from_the_deployed_stack(self.workflow),
            "the edge deploy must read DistributionArn from the deployed "
            "app stack's outputs; a literal ARN would attach the rule to a "
            "stale distribution.",
        )

    def test_edge_stack_fails_loudly_without_the_arn(self):
        self.assertTrue(
            _edge_stack_deploy_fails_loudly_without_the_arn(self.workflow),
            "a missing DistributionArn output must fail the deploy job "
            "loudly instead of deploying an unassociated edge stack.",
        )

    def test_edge_stack_targets_us_east_1(self):
        self.assertTrue(
            _edge_stack_targets_us_east_1(self.workflow),
            "the edge deploy must target us-east-1 with edge-template.yaml "
            "and the chemically-edge stack: a CLOUDFRONT-scoped web ACL can "
            "only be created there.",
        )

    def test_edge_stack_passes_the_arn_and_the_action(self):
        self.assertTrue(
            _edge_stack_passes_the_arn_and_the_action(self.workflow),
            "the edge deploy must pass DistributionArn, WafRateLimit and "
            "WafAction to the edge stack.",
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

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.workflow,
            f"the workflow no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.workflow.replace(old, new, 1)

    def test_edge_deploy_before_the_app_deploy_is_detected(self):
        mutated = self._mutate(
            "      - name: Deploy the edge stack (us-east-1)",
            "      - name: Edge stack placeholder (us-east-1)",
        )
        self.assertFalse(
            _edge_stack_is_deployed_after_the_app_stack(mutated),
            "the ordering guard must fail when the edge step is renamed "
            "away or moved.",
        )

    def test_hardcoded_distribution_arn_is_detected(self):
        mutated = self._mutate(
            "          dist_arn=$(aws cloudformation describe-stacks \\",
            "          dist_arn=arn:aws:cloudfront::768125641662:distribution/"
            "E1EXAMPLE\n"
            "          # dist_arn=$(aws cloudformation describe-stacks \\",
        )
        self.assertFalse(
            _edge_stack_reads_the_arn_from_the_deployed_stack(mutated),
            "the ARN guard must fail when a literal distribution ARN "
            "replaces the read from the deployed stack.",
        )

    def test_missing_error_handling_is_detected(self):
        mutated = self._mutate(
            '            echo "::error::The chemically stack did not export '
            'DistributionArn; the edge stack cannot be associated with the '
            'live distribution."\n            exit 1\n',
            "",
        )
        self.assertFalse(
            _edge_stack_deploy_fails_loudly_without_the_arn(mutated),
            "the guard must fail when the deploy stops failing loudly on a "
            "missing ARN.",
        )

    def test_wrong_region_is_detected(self):
        mutated = self._mutate(
            "            --region us-east-1 \\",
            "            --region us-east-2 \\",
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
            _edge_stack_passes_the_arn_and_the_action(mutated),
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
