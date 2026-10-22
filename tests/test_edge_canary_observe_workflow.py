"""Durable guards on the dispatchable, read-only canary observation workflow.

M007/S06/T01: the edge rule is CREATED by the very deploy that runs the
Count observer, so on that deploy the observation window legitimately
holds no datapoint -- the observer must report that as "warming up" and
exit 0, and the milestone needs the window re-read AFTER traffic has
flowed WITHOUT redeploying the site. A redeploy per observation is not
an observation tool.

``.github/workflows/edge_canary_observe.yml`` is that tool, and it is the
one workflow in this repository that must never be able to change what is
live. These guards pin its shape:

* it is DISPATCH-ONLY -- no push, no pull request, no schedule: it runs
  when a human asks for an observation and never otherwise;
* the window and the expected action are DISPATCH INPUTS, so the same
  observer runs with the same arguments the deploy job uses;
* it contains no write verb of any kind: no stack write, no image build,
  no cluster access. It reads;
* it RUNS THE OBSERVER (scripts/verify_edge_canary.py) against the edge
  stack in us-east-1 and writes the JSON record;
* it UPLOADS the record as the artifact ``edge-canary-count-record`` --
  the same name the deploy job uses, so the budget review (M007/S06/T02)
  can read either one;
* it CONFIGURES the AWS credentials, because a read-only observer still
  needs to be allowed to read;
* the workflow still parses as YAML: a workflow that does not parse never
  runs, and every other guard would then pass on a file GitHub ignores.

Following the convention of ``tests/test_edge_canary_pipeline.py``
(M007/S05) and ``tests/test_edge_deploy_pipeline.py`` (M007/S02), the
assertions are named guard functions proved non-vacuous by
mutation-based negative tests: each case mutates a copy of the tracked
workflow and asserts that the corresponding guard rejects it. The
workflow is parsed with PyYAML (shipped by the pinned cfn-lint dev
dependency), so the guards see the real document and not a regex
approximation of it. ``SimpleTestCase`` blocks all DB access.
"""

from pathlib import Path

import yaml
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
OBSERVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "edge_canary_observe.yml"

#: The artifact the record is uploaded as (the deploy job uses the same
#: name on purpose: the budget review must be able to read either).
ARTIFACT_NAME = "edge-canary-count-record"

#: The JSON record the observer writes.
RECORD_FILE = "edge-canary-count-record.json"

#: The observer the workflow must run.
OBSERVER = "scripts/verify_edge_canary.py"

#: The credentials action, pinned like every other action in this repo.
CREDENTIALS_ACTION = "aws-actions/configure-aws-credentials@v5"

#: The upload action, pinned like every other action in this repo.
UPLOAD_ACTION = "actions/upload-artifact@v5"

#: The verbs that would change something. None of them may appear in the
#: workflow -- not in a run body, not in a with-block, not anywhere.
FORBIDDEN_VERBS = (
    "sam deploy",
    "setup-sam",
    "aws cloudformation deploy",
    "cloudformation create-stack",
    "cloudformation update-stack",
    "cloudformation delete-stack",
    "create-stack",
    "update-stack",
    "delete-stack",
    "docker build",
    "docker/build-push",
    "docker push",
    "docker login",
    "kubectl",
    "helm upgrade",
    "helm install",
    "terraform apply",
    "terraform destroy",
)


def _document(text):
    """Return the parsed workflow, or None when it does not parse."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _triggers(document):
    """Return the workflow's triggers.

    PyYAML reads the ``on:`` key as the boolean True (YAML 1.1), so both
    spellings are looked up.
    """
    if not isinstance(document, dict):
        return None
    for key in ("on", True):
        if key in document:
            return document[key]
    return None


def _steps(text):
    """Return the workflow's steps, or None when there are none."""
    document = _document(text)
    if not isinstance(document, dict):
        return None
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return None
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if isinstance(steps, list):
            return steps
    return None


def _all_strings(value):
    """Yield every string in a parsed document (comments are not parsed)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _without_comment_lines(text):
    """Return the workflow text with its comment lines removed."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def _workflow_parses_as_yaml(text):
    """The workflow must still be valid YAML.

    A workflow that does not parse is a workflow that never runs, and the
    other guards would then pass on a file GitHub ignores.
    """
    document = _document(text)
    if not isinstance(document, dict):
        return False
    return isinstance(document.get("jobs"), dict)


def _is_dispatch_only(text):
    """The workflow must be dispatch-only.

    It observes what is already deployed; it must never run on its own.
    A push trigger would turn every deploy into an observation run, and a
    schedule would keep hammering the WAF counters for nothing.
    """
    document = _document(text)
    if not isinstance(document, dict):
        return False
    triggers = _triggers(document)
    if not isinstance(triggers, dict):
        return False
    return set(triggers.keys()) == {"workflow_dispatch"}


def _declares_the_dispatch_inputs(text):
    """The window and the expected action must be dispatch inputs.

    They are the two arguments the deploy job's observer step uses, and
    they are what an operator changes per observation (``-f minutes=60
    -f expect_action=Count``).
    """
    document = _document(text)
    if not isinstance(document, dict):
        return False
    triggers = _triggers(document)
    if not isinstance(triggers, dict):
        return False
    dispatch = triggers.get("workflow_dispatch")
    if not isinstance(dispatch, dict):
        return False
    inputs = dispatch.get("inputs")
    if not isinstance(inputs, dict):
        return False
    for name in ("minutes", "expect_action"):
        if name not in inputs:
            return False
        if not isinstance(inputs[name], dict):
            return False
    return True


def _contains_no_write_verbs(text):
    """The workflow must not be able to change anything.

    It is dispatched by a human who wants to READ the Count window. A
    single write verb in it would make an observation run a production
    change, which is exactly what this workflow must never be.
    """
    document = _document(text)
    if not isinstance(document, dict):
        return False
    haystacks = list(_all_strings(document))
    haystacks.append(_without_comment_lines(text))
    for haystack in haystacks:
        lowered = haystack.lower()
        for verb in FORBIDDEN_VERBS:
            if verb in lowered:
                return False
    return True


def _runs_the_observer(text):
    """The workflow must run the observer, with the deploy job's arguments.

    The observer reads the live edge stack, the live web ACL and the WAF
    counters and writes the JSON record. It must run against the edge
    stack in us-east-1 (the CLOUDFRONT-scoped rule only lives there) and
    it must take the window and the expected action from the dispatch
    inputs, so the record describes the window the operator asked for.
    """
    steps = _steps(text)
    if steps is None:
        return False
    body = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str) and OBSERVER in run:
            body = run
            break
    if body is None:
        return False
    if "--edge-stack-name" not in body:
        return False
    if "--edge-region us-east-1" not in body:
        return False
    if "--expect-action" not in body or "inputs.expect_action" not in body:
        return False
    if "--minutes" not in body or "inputs.minutes" not in body:
        return False
    if "--site-url https://chemic-ally.xyz" not in body:
        return False
    if f"--record-out {RECORD_FILE}" not in body:
        return False
    return True


def _uploads_the_record(text):
    """The record must be uploaded as the canary count record.

    The record is the evidence the budget review reads, so it may not be
    lost with the runner. The upload must run even when the observation
    failed (``always()``): a failed run's record is exactly the record
    worth reading.
    """
    steps = _steps(text)
    if steps is None:
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not isinstance(uses, str) or UPLOAD_ACTION not in uses:
            continue
        with_block = step.get("with")
        if not isinstance(with_block, dict):
            return False
        if with_block.get("name") != ARTIFACT_NAME:
            return False
        if with_block.get("path") != RECORD_FILE:
            return False
        condition = step.get("if")
        if not isinstance(condition, str) or "always()" not in condition:
            return False
        return True
    return False


def _configures_aws_credentials(text):
    """The workflow must configure the AWS credentials.

    A read-only observer still needs to be allowed to read: without the
    credentials the run fails with an authentication error, which is
    indistinguishable from an observation failure.
    """
    steps = _steps(text)
    if steps is None:
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not isinstance(uses, str) or CREDENTIALS_ACTION not in uses:
            continue
        with_block = step.get("with")
        if not isinstance(with_block, dict):
            return False
        if "secrets.AWS_ACCESS_KEY_ID" not in str(
            with_block.get("aws-access-key-id", "")
        ):
            return False
        if "secrets.AWS_SECRET_ACCESS_KEY" not in str(
            with_block.get("aws-secret-access-key", "")
        ):
            return False
        if not with_block.get("aws-region"):
            return False
        return True
    return False


class EdgeCanaryObserveWorkflowTests(SimpleTestCase):
    """M007/S06/T01: the observation workflow must be dispatch-only and read-only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = OBSERVE_WORKFLOW.read_text(encoding="utf-8")

    def test_the_workflow_exists(self):
        self.assertTrue(
            OBSERVE_WORKFLOW.exists(),
            ".github/workflows/edge_canary_observe.yml must exist: without "
            "it the Count window can only be read by redeploying the site.",
        )

    def test_the_workflow_parses_as_yaml(self):
        self.assertTrue(
            _workflow_parses_as_yaml(self.workflow),
            ".github/workflows/edge_canary_observe.yml must parse as YAML: "
            "GitHub ignores a workflow that does not parse, and every other "
            "guard would pass on a file that never runs.",
        )

    def test_the_workflow_is_dispatch_only(self):
        self.assertTrue(
            _is_dispatch_only(self.workflow),
            "the observation workflow must be triggered by workflow_dispatch "
            "ONLY: it observes what is already deployed and must never run "
            "on a push or on a schedule.",
        )

    def test_the_dispatch_inputs_are_declared(self):
        self.assertTrue(
            _declares_the_dispatch_inputs(self.workflow),
            "the workflow must declare the 'minutes' and 'expect_action' "
            "dispatch inputs: they are the arguments the deploy job's "
            "observer step uses.",
        )

    def test_the_workflow_contains_no_write_verb(self):
        self.assertTrue(
            _contains_no_write_verbs(self.workflow),
            "the observation workflow must contain NO write verb: no stack "
            "write, no image build, no cluster access. It reads.",
        )

    def test_the_workflow_runs_the_observer(self):
        self.assertTrue(
            _runs_the_observer(self.workflow),
            "the workflow must run scripts/verify_edge_canary.py against the "
            "edge stack in us-east-1 with the dispatch inputs and write "
            "edge-canary-count-record.json.",
        )

    def test_the_workflow_uploads_the_record(self):
        self.assertTrue(
            _uploads_the_record(self.workflow),
            "the workflow must upload the record as the artifact "
            "'edge-canary-count-record' (the same name the deploy job "
            "uses), even when the observation failed.",
        )

    def test_the_workflow_configures_the_aws_credentials(self):
        self.assertTrue(
            _configures_aws_credentials(self.workflow),
            "the workflow must configure the AWS credentials: a read-only "
            "observer still has to be allowed to read.",
        )


class EdgeCanaryObserveWorkflowNegativeTests(SimpleTestCase):
    """The guards above must fail when the workflow is changed.

    Each case mutates a copy of the tracked workflow and asserts that the
    corresponding guard rejects it, so a guard that silently stopped
    matching cannot keep the suite green while the workflow has drifted
    apart.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = OBSERVE_WORKFLOW.read_text(encoding="utf-8")

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.workflow,
            f"the workflow no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.workflow.replace(old, new, 1)

    def test_a_push_trigger_is_detected(self):
        mutated = self._mutate(
            "on:\n  workflow_dispatch:\n",
            "on:\n  push:\n    branches: [main]\n  workflow_dispatch:\n",
        )
        self.assertFalse(
            _is_dispatch_only(mutated),
            "the trigger guard must fail when the workflow also runs on a "
            "push: every deploy would then observe the window.",
        )

    def test_a_missing_dispatch_input_is_detected(self):
        mutated = self._mutate(
            '      minutes:\n        description: "The size of the '
            'observation window in minutes"\n',
            '      window:\n        description: "The size of the '
            'observation window in minutes"\n',
        )
        self.assertFalse(
            _declares_the_dispatch_inputs(mutated),
            "the input guard must fail when the window input is renamed: "
            "the documented dispatch command would stop working.",
        )

    def test_a_write_verb_is_detected(self):
        mutated = self._mutate(
            "          python scripts/verify_edge_canary.py \\\n",
            "          sam deploy --template-file template.yaml\n"
            "          python scripts/verify_edge_canary.py \\\n",
        )
        self.assertFalse(
            _contains_no_write_verbs(mutated),
            "the read-only guard must fail when the workflow gains a stack "
            "write: an observation run must never change what is live.",
        )

    def test_an_image_build_is_detected(self):
        mutated = self._mutate(
            "          python scripts/verify_edge_canary.py \\\n",
            "          docker build -t chemically .\n"
            "          python scripts/verify_edge_canary.py \\\n",
        )
        self.assertFalse(
            _contains_no_write_verbs(mutated),
            "the read-only guard must fail when the workflow gains an image "
            "build.",
        )

    def test_not_running_the_observer_is_detected(self):
        mutated = self._mutate(
            "          python scripts/verify_edge_canary.py \\\n",
            "          python scripts/verify_edge_block.py \\\n",
        )
        self.assertFalse(
            _runs_the_observer(mutated),
            "the observer guard must fail when the workflow stops running "
            "the observer: the record would never be produced.",
        )

    def test_a_hard_coded_expectation_is_detected(self):
        mutated = self._mutate(
            '            --expect-action "${{ inputs.expect_action }}" \\\n',
            "            --expect-action Count \\\n",
        )
        self.assertFalse(
            _runs_the_observer(mutated),
            "the observer guard must fail when the expected action is not "
            "taken from the dispatch input: the operator could not ask for "
            "another mode.",
        )

    def test_a_renamed_artifact_is_detected(self):
        mutated = self._mutate(
            "          name: edge-canary-count-record\n",
            "          name: edge-canary-window-record\n",
        )
        self.assertFalse(
            _uploads_the_record(mutated),
            "the upload guard must fail when the artifact is renamed: the "
            "budget review looks for 'edge-canary-count-record'.",
        )

    def test_an_upload_that_skips_a_failed_run_is_detected(self):
        mutated = self._mutate(
            "        if: always()\n",
            "        if: success()\n",
        )
        self.assertFalse(
            _uploads_the_record(mutated),
            "the upload guard must fail when the upload is skipped on a "
            "failed observation: a failed run's record is exactly the "
            "record worth reading.",
        )

    def test_missing_credentials_are_detected(self):
        mutated = self._mutate(
            "        uses: aws-actions/configure-aws-credentials@v5\n",
            "        uses: actions/checkout@v5\n",
        )
        self.assertFalse(
            _configures_aws_credentials(mutated),
            "the credentials guard must fail when the credentials step is "
            "gone: the run would fail with an authentication error, which "
            "is indistinguishable from an observation failure.",
        )

    def test_broken_yaml_is_detected(self):
        mutated = self._mutate(
            "      - name: Observe the edge canary window\n",
            "      - name: Observe the edge canary window\n"
            "        run: echo 'broken: [unclosed'\n",
        )
        self.assertFalse(
            _workflow_parses_as_yaml(mutated),
            "the YAML guard must fail when the workflow no longer parses: "
            "GitHub would ignore it and every other guard would pass on a "
            "file that never runs.",
        )
