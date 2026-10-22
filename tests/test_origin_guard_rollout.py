"""Durable guards on the origin guard's two-phase rollout switch (M007/S03).

The origin header guard has to be rolled out in two phases: the CloudFront
origin must stamp the header BEFORE anything can refuse a request, or every
legitimate request dies. The ``EnforceOriginGuard`` boolean makes that order
expressible with one secret and one switch -- phase 1 ships the header with
the guard inert, phase 2 hands the secret to Lambda and enforcement begins.

These guards pin that contract in ``template.yaml`` and in the deploy
workflow:

* the switch exists, is a Boolean and defaults to FALSE (phase 1 is the
  default, so a deploy can never refuse traffic by accident);
* the Lambda environment variable that carries the secret is GATED on the
  switch, and "off" means the variable is absent (AWS::NoValue), not
  present-and-empty;
* the header NAME is never gated - CloudFront stamps the header from the
  first deploy onwards, which is exactly what phase 1 needs;
* the shared secret is still a NoEcho parameter with no committed default;
* the deploy pipeline owns the switch: one workflow setting, defaulting to
  false, passed as the parameter override, and the secret is still passed
  so the CloudFront header can be stamped in phase 1.

Following the convention of ``tests/test_origin_header_guard.py`` (M006/S03)
and ``tests/test_edge_deploy_pipeline.py`` (M007/S02), the assertions are
named guard functions proved non-vacuous by mutation-based negative tests.
The tracked files are read as plain text and parsed with regexes only, so no
YAML dependency is introduced. ``SimpleTestCase`` blocks all DB access.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "template.yaml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

ENFORCE_PARAMETER = "EnforceOriginGuard"
SECRET_ENV_VAR = "ORIGIN_HEADER_SECRET"
HEADER_ENV_VAR = "ORIGIN_HEADER_NAME"


def _entry_bodies(text, section):
    """Map entry name -> body text for the entries under ``section:``."""
    start = re.search(
        r"^" + re.escape(section) + r":[ \t]*$", text, re.MULTILINE
    )
    if start is None:
        return {}
    body = text[start.end():]
    following = re.search(r"^[A-Za-z][A-Za-z0-9_]*:[ \t]*$", body, re.MULTILINE)
    if following is not None:
        body = body[:following.start()]

    entries = {}
    keys = list(
        re.finditer(r"^  (?!\#)(\S[^\n:]*):[ \t]*$", body, re.MULTILINE)
    )
    for index, key in enumerate(keys):
        end = keys[index + 1].start() if index + 1 < len(keys) else len(body)
        entries[key.group(1)] = body[key.end():end]
    return entries


def _parameter(text, parameter_name):
    """Return the body of a parameter, or "" when it is not declared."""
    return _entry_bodies(text, "Parameters").get(parameter_name, "")


def _env_var_entries(text):
    """Map environment variable name -> its value text (Globals block).

    The variables live under ``Globals: Function: Environment:
    Variables:`` and are keyed at eight spaces of indentation; a value may
    span several lines (a short-form ``!If`` with a block sequence), so the
    body runs until the next key at the same indentation.
    """
    start = re.search(r"^[ \t]+Variables:[ \t]*$", text, re.MULTILINE)
    if start is None:
        return {}
    body = text[start.end():]
    entries = {}
    key_re = re.compile(r"^        ([A-Za-z_][A-Za-z0-9_]*):(.*)$")
    lines = body.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        match = key_re.match(lines[index].rstrip("\n"))
        if match is None:
            index += 1
            continue
        name = match.group(1)
        value = match.group(2)
        index += 1
        while index < len(lines):
            line = lines[index]
            if key_re.match(line.rstrip("\n")):
                break
            if line.strip() and not line.startswith("          "):
                break
            value += "\n" + line.rstrip("\n")
            index += 1
        entries[name] = value.strip()
    return entries


def _enforce_parameter_defaults_to_false(text):
    """The switch must exist and default to false.

    CloudFormation has NO Boolean parameter type (cfn-lint enforces it), so
    the switch is a String restricted to "true"/"false". The guard pins
    that shape: a Boolean parameter would fail the deploy.
    """
    parameter = _parameter(text, ENFORCE_PARAMETER)
    if not parameter:
        return False
    if re.search(r"Type:[ \t]*Boolean", parameter) is not None:
        return False
    if re.search(r"Type:[ \t]*String", parameter) is None:
        return False
    default = re.search(r"Default:[ \t]*\"?(\w+)\"?", parameter)
    if default is None:
        return False
    if default.group(1).lower() != "false":
        return False
    allowed = re.search(r"AllowedValues:(.*)", parameter, re.DOTALL)
    if allowed is None:
        return False
    return '"true"' in allowed.group(1) and '"false"' in allowed.group(1)


def _lambda_secret_is_gated_on_the_switch(text):
    """The Lambda secret variable must be gated, and "off" must mean absent.

    The gate is a short-form ``!If`` on ``EnforceOriginGuard`` whose
    else-branch is ``AWS::NoValue`` -- the variable is then REMOVED from
    the environment instead of being present and empty, so "the guard is
    off" has exactly one meaning.
    """
    value = _env_var_entries(text).get(SECRET_ENV_VAR, "")
    if not value:
        return False
    if not value.startswith("!If"):
        return False
    if "EnforceOriginGuardEnabled" not in value:
        return False
    if "!Ref OriginSecret" not in value:
        return False
    return "!Ref AWS::NoValue" in value


def _header_name_is_never_gated(text):
    """The header NAME is always deployed: phase 1 needs the header live."""
    value = _env_var_entries(text).get(HEADER_ENV_VAR, "")
    if not value:
        return False
    return "!Ref OriginHeaderName" in value and "!If" not in value


def _secret_parameter_is_noecho(text):
    """The shared secret stays a NoEcho parameter with no committed default."""
    parameter = _parameter(text, "OriginSecret")
    if not parameter:
        return False
    if re.search(r"NoEcho:[ \t]*true", parameter) is None:
        return False
    return re.search(r"Default:[ \t]*\S", parameter) is None


def _workflow_owns_the_switch(text):
    """One workflow setting, defaulting to false, passed to the deploy."""
    env = re.search(
        r"^    env:[ \t]*$", text, re.MULTILINE
    )
    if env is None:
        return False
    block = text[env.end():]
    following = re.search(r"^    [A-Za-z]", block, re.MULTILINE)
    if following is not None:
        block = block[:following.start()]
    setting = re.search(
        r"^      ENFORCE_ORIGIN_GUARD:[ \t]*\"?(\w+)\"?[ \t]*$",
        block,
        re.MULTILINE,
    )
    if setting is None:
        return False
    if setting.group(1).lower() != "false":
        return False
    return (
        "EnforceOriginGuard=${{ env.ENFORCE_ORIGIN_GUARD }}" in text
    )


def _workflow_still_passes_the_secret(text):
    """The secret must still reach CloudFormation: the header needs it."""
    return "OriginSecret=${{ secrets.ORIGIN_SECRET }}" in text


class OriginGuardRolloutTests(SimpleTestCase):
    """M007/S03: the guard's rollout must be expressible as two phases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_switch_exists_and_defaults_to_false(self):
        self.assertTrue(
            _enforce_parameter_defaults_to_false(self.template),
            "template.yaml must declare the EnforceOriginGuard Boolean "
            "parameter with default false: the first deploy must ship the "
            "header with the guard inert.",
        )

    def test_lambda_secret_is_gated_on_the_switch(self):
        self.assertTrue(
            _lambda_secret_is_gated_on_the_switch(self.template),
            "the Lambda ORIGIN_HEADER_SECRET variable must be gated on "
            "EnforceOriginGuard, with AWS::NoValue as the else-branch so "
            "'off' means the variable is absent.",
        )

    def test_header_name_is_never_gated(self):
        self.assertTrue(
            _header_name_is_never_gated(self.template),
            "ORIGIN_HEADER_NAME must always be deployed: phase 1 needs "
            "CloudFront stamping the header from the very first deploy.",
        )

    def test_secret_parameter_is_still_noecho(self):
        self.assertTrue(
            _secret_parameter_is_noecho(self.template),
            "the OriginSecret parameter must stay NoEcho with no committed "
            "default.",
        )

    def test_workflow_owns_the_switch(self):
        self.assertTrue(
            _workflow_owns_the_switch(self.workflow),
            "the deploy job must own the switch through one setting "
            "(ENFORCE_ORIGIN_GUARD, default false) passed as the "
            "EnforceOriginGuard parameter override.",
        )

    def test_workflow_still_passes_the_secret(self):
        self.assertTrue(
            _workflow_still_passes_the_secret(self.workflow),
            "the deploy job must keep passing OriginSecret: phase 1 needs "
            "the secret to stamp the CloudFront origin header.",
        )


class OriginGuardRolloutNegativeTests(SimpleTestCase):
    """The guards above must fail when the rollout wiring is broken."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def _mutate(self, text, old, new):
        self.assertIn(
            old,
            text,
            f"the file no longer contains {old!r}; the negative case cannot "
            "be constructed and needs to be updated.",
        )
        return text.replace(old, new, 1)

    def test_missing_switch_is_detected(self):
        mutated = self._mutate(
            self.template,
            '  EnforceOriginGuard:\n    Type: String\n    Default: "false"',
            '  EnforceOriginGuardRemoved:\n    Type: String\n    Default: "false"',
        )
        self.assertFalse(
            _enforce_parameter_defaults_to_false(mutated),
            "the switch guard must fail when the parameter is renamed away.",
        )

    def test_switch_defaulting_to_true_is_detected(self):
        mutated = self._mutate(
            self.template,
            '  EnforceOriginGuard:\n    Type: String\n    Default: "false"',
            '  EnforceOriginGuard:\n    Type: String\n    Default: "true"',
        )
        self.assertFalse(
            _enforce_parameter_defaults_to_false(mutated),
            "the switch guard must fail when the default would enforce the "
            "guard on the first deploy.",
        )

    def test_boolean_parameter_type_is_detected(self):
        """CloudFormation has no Boolean parameter type."""
        mutated = self._mutate(
            self.template,
            "  EnforceOriginGuard:\n    Type: String",
            "  EnforceOriginGuard:\n    Type: Boolean",
        )
        self.assertFalse(
            _enforce_parameter_defaults_to_false(mutated),
            "the switch guard must fail when the parameter uses a type "
            "CloudFormation does not have.",
        )

    def test_ungated_secret_is_detected(self):
        mutated = self._mutate(
            self.template,
            "        ORIGIN_HEADER_SECRET: !If\n"
            "          - EnforceOriginGuardEnabled\n"
            "          - !Ref OriginSecret\n"
            "          - !Ref AWS::NoValue",
            "        ORIGIN_HEADER_SECRET: !Ref OriginSecret",
        )
        self.assertFalse(
            _lambda_secret_is_gated_on_the_switch(mutated),
            "the gate guard must fail when the Lambda secret is deployed "
            "unconditionally.",
        )

    def test_gate_without_no_value_is_detected(self):
        mutated = self._mutate(
            self.template,
            "          - !Ref AWS::NoValue",
            '          - ""',
        )
        self.assertFalse(
            _lambda_secret_is_gated_on_the_switch(mutated),
            "the gate guard must fail when 'off' leaves the variable present "
            "instead of removing it.",
        )

    def test_gated_header_name_is_detected(self):
        mutated = self._mutate(
            self.template,
            "        ORIGIN_HEADER_NAME: !Ref OriginHeaderName",
            "        ORIGIN_HEADER_NAME: !If\n"
            "          - EnforceOriginGuardEnabled\n"
            "          - !Ref OriginHeaderName\n"
            "          - !Ref AWS::NoValue",
        )
        self.assertFalse(
            _header_name_is_never_gated(mutated),
            "the header guard must fail when the header name is gated: "
            "phase 1 needs the header live from the first deploy.",
        )

    def test_committed_secret_default_is_detected(self):
        mutated = self._mutate(
            self.template,
            "  OriginSecret:\n    Type: String\n    NoEcho: true",
            "  OriginSecret:\n    Type: String\n    NoEcho: true\n"
            "    Default: committed-secret",
        )
        self.assertFalse(
            _secret_parameter_is_noecho(mutated),
            "the secret guard must fail when the parameter carries a "
            "committed default.",
        )

    def test_workflow_without_the_switch_is_detected(self):
        mutated = self._mutate(
            self.workflow,
            '      ENFORCE_ORIGIN_GUARD: "false"',
            '      ENFORCE_ORIGIN_GUARD: "true"',
        )
        self.assertFalse(
            _workflow_owns_the_switch(mutated),
            "the workflow guard must fail when the switch would enforce the "
            "guard without a reviewed change.",
        )

    def test_workflow_without_the_parameter_override_is_detected(self):
        mutated = self._mutate(
            self.workflow,
            "              EnforceOriginGuard=${{ env.ENFORCE_ORIGIN_GUARD }} \\\n",
            "",
        )
        self.assertFalse(
            _workflow_owns_the_switch(mutated),
            "the workflow guard must fail when the switch is not passed to "
            "the deploy.",
        )

    def test_workflow_without_the_secret_is_detected(self):
        mutated = self._mutate(
            self.workflow,
            "              OriginSecret=${{ secrets.ORIGIN_SECRET }} \\\n",
            "",
        )
        self.assertFalse(
            _workflow_still_passes_the_secret(mutated),
            "the workflow guard must fail when the secret is no longer "
            "passed: phase 1 needs it to stamp the CloudFront header.",
        )


class OriginHeaderLandingTests(SimpleTestCase):
    """The origin header must actually reach the live distribution.

    CloudFormation's diff does not detect a change to a nested list
    property like ``Origins.Items[].OriginCustomHeaders``: the update is
    silently dropped and the live distribution keeps serving WITHOUT the
    header -- which is exactly what happened in production (the header was
    never stamped, and the origin-guard witness reported it).

    The distribution's Comment is a plain string CloudFormation DOES diff,
    so it carries a fingerprint of the secret: a changed secret changes the
    Comment, CloudFormation updates the distribution, and the update
    carries the full configuration including the header.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_the_comment_carries_the_secret_fingerprint(self):
        self.assertIn(
            "OriginSecretFingerprint",
            self.template,
            "template.yaml must declare the OriginSecretFingerprint "
            "parameter: it is what makes CloudFormation notice that the "
            "origin header changed.",
        )
        self.assertRegex(
            self.template,
            r"Comment:[^\n]*OriginSecretFingerprint",
            "the distribution's Comment must carry the fingerprint, so a "
            "changed secret always produces a distribution update (the "
            "header change itself is invisible to CloudFormation's diff).",
        )

    def test_the_workflow_passes_the_fingerprint(self):
        self.assertIn(
            "OriginSecretFingerprint=",
            self.workflow,
            "the deploy job must pass the fingerprint to the app stack, or "
            "the distribution would never be updated when the secret "
            "changes.",
        )
        self.assertIn(
            "sha256sum",
            self.workflow,
            "the fingerprint must be a hash of the secret: the secret itself "
            "must never reach the workflow's output or the distribution's "
            "Comment.",
        )


class OriginHeaderDeliveryTests(SimpleTestCase):
    """The header must be delivered to the LIVE distribution.

    CloudFormation's diff does not detect a change to a nested list
    property like ``Origins.Items[].OriginCustomHeaders``, so the header
    was silently absent in production while the template declared it. A
    dedicated step delivers it and verifies it, and it must not be
    swallowable: a guard that refuses requests the origin does not stamp
    would take the whole site down.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    def test_the_deploy_job_ensures_the_header(self):
        self.assertIn(
            "scripts/ensure_origin_header.py",
            self.workflow,
            "the deploy job must run scripts/ensure_origin_header.py: "
            "CloudFormation cannot deliver the origin header to an existing "
            "distribution, so the header would never be stamped.",
        )

    def test_the_ensure_step_carries_the_secret_from_the_environment(self):
        """The secret must come from the environment, never from a file."""
        self.assertRegex(
            self.workflow,
            r"ORIGIN_SECRET: \$\{\{ secrets\.ORIGIN_SECRET \}\}",
            "the ensure step must take the secret from the GitHub secret "
            "through the environment.",
        )

    def test_the_ensure_step_cannot_be_swallowed(self):
        self.assertNotIn(
            "ensure_origin_header.py || true",
            self.workflow,
            "the ensure step must not be swallowable: an unstamped origin "
            "header means the guard must not be enforced.",
        )


class OriginHeaderFieldNameTests(SimpleTestCase):
    """The API's field name is CustomHeaders, not OriginCustomHeaders.

    CloudFormation's template property is ``OriginCustomHeaders`` and the
    CloudFront API's is ``CustomHeaders`` -- the resource provider maps one
    onto the other. A witness or a repair script that reads or writes the
    template's name against the API therefore finds nothing (and the API
    rejects the write), which is exactly how a live, correctly stamped
    header was reported missing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.witness = (REPO_ROOT / "scripts" / "verify_origin_guard.py").read_text(
            encoding="utf-8"
        )
        cls.ensurer = (
            REPO_ROOT / "scripts" / "ensure_origin_header.py"
        ).read_text(encoding="utf-8")

    def test_the_witness_reads_the_api_field(self):
        self.assertIn(
            'origin.get("CustomHeaders")',
            self.witness,
            "the witness must read the origin's CustomHeaders (the API's "
            "name), not the template's OriginCustomHeaders.",
        )
        self.assertIn(
            'headers.get("Items")',
            self.witness,
            "the API carries the headers in a structure ({Quantity, "
            "Items}); reading it as a list yields the structure's keys and "
            "a live header looks missing.",
        )

    def test_the_ensurer_writes_the_api_field(self):
        self.assertIn(
            'origin["CustomHeaders"] = {"Quantity": len(headers), "Items": headers}',
            self.ensurer,
            "the repair script must write CustomHeaders in the API's "
            "structure shape; the API rejects OriginCustomHeaders.",
        )
