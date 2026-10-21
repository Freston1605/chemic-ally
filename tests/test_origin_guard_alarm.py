"""Durable guards on the origin guard's refusal alarm (M007/S03).

A caller bypassing CloudFront is precisely the event an operator must see.
The guard counts every refused direct call as a CloudWatch datapoint
(``ChemicAlly/OriginGuardRefusals``); these guards pin the two things that
make that count actionable:

* the Lambda execution role may publish metrics - but only into the
  ``ChemicAlly`` namespace, so the function cannot write arbitrary
  CloudWatch data;
* the stack declares the alarm that watches the refusal metric, with an
  explicit threshold, an explicit statistic and
  ``TreatMissingData: notBreaching`` so a quiet period never reads as an
  alarm.

The alarm lives in the us-east-2 app stack because CloudWatch metrics are
regional and that is where the Lambda publishes the metric.

Following the convention of the rest of the suite, the assertions are named
guard functions proved non-vacuous by mutation-based negative tests. The
template is read as plain text and parsed with regexes only, so no YAML
dependency is introduced. ``SimpleTestCase`` blocks all DB access.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "template.yaml"

ALARM_NAME = "OriginGuardRefusalAlarm"
ROLE_NAME = "LambdaExecutionRole"
METRIC_NAMESPACE = "ChemicAlly"
METRIC_NAME = "OriginGuardRefusals"


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


def _resource(text, logical_name):
    """Return the body of a resource, or "" when it is not declared."""
    return _entry_bodies(text, "Resources").get(logical_name, "")


def _role_may_publish_only_to_the_namespace(text):
    """The role may publish metrics, scoped to the ChemicAlly namespace."""
    role = _resource(text, ROLE_NAME)
    if not role:
        return False
    if "cloudwatch:PutMetricData" not in role:
        return False
    # The grant must be scoped: an unscoped PutMetricData would let the
    # function write into any namespace in the account.
    scoped = re.search(
        r"namespace/" + re.escape(METRIC_NAMESPACE), role
    )
    return scoped is not None


def _alarm_watches_the_refusal_metric(text):
    """The stack must declare the alarm on the refusal metric."""
    alarm = _resource(text, ALARM_NAME)
    if not alarm:
        return False
    if "Type: AWS::CloudWatch::Alarm" not in alarm:
        return False
    if re.search(
        r"^[ \t]*Namespace:[ \t]*" + METRIC_NAMESPACE + r"[ \t]*$",
        alarm,
        re.MULTILINE,
    ) is None:
        return False
    if re.search(
        r"^[ \t]*MetricName:[ \t]*" + METRIC_NAME + r"[ \t]*$",
        alarm,
        re.MULTILINE,
    ) is None:
        return False
    if re.search(r"^[ \t]*Statistic:[ \t]*Sum[ \t]*$", alarm, re.MULTILINE) is None:
        return False
    if re.search(r"^[ \t]*Period:[ \t]*\d+[ \t]*$", alarm, re.MULTILINE) is None:
        return False
    if re.search(
        r"^[ \t]*EvaluationPeriods:[ \t]*\d+[ \t]*$", alarm, re.MULTILINE
    ) is None:
        return False
    if re.search(r"^[ \t]*Threshold:[ \t]*\d+[ \t]*$", alarm, re.MULTILINE) is None:
        return False
    if re.search(
        r"^[ \t]*ComparisonOperator:[ \t]*\S+[ \t]*$", alarm, re.MULTILINE
    ) is None:
        return False
    return re.search(
        r"^[ \t]*TreatMissingData:[ \t]*notBreaching[ \t]*$", alarm, re.MULTILINE
    ) is not None


class OriginGuardAlarmTests(SimpleTestCase):
    """M007/S03: the refusal count must be actionable in the stack."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_role_may_publish_only_to_the_namespace(self):
        self.assertTrue(
            _role_may_publish_only_to_the_namespace(self.template),
            "the Lambda execution role must be granted "
            "cloudwatch:PutMetricData scoped to the ChemicAlly namespace, so "
            "the guard can count refusals without being able to publish "
            "arbitrary metrics.",
        )

    def test_alarm_watches_the_refusal_metric(self):
        self.assertTrue(
            _alarm_watches_the_refusal_metric(self.template),
            "the stack must declare a CloudWatch alarm on "
            "ChemicAlly/OriginGuardRefusals with a statistic, a period, a "
            "threshold and TreatMissingData notBreaching.",
        )


class OriginGuardAlarmNegativeTests(SimpleTestCase):
    """The guards above must fail when the alarm wiring is broken."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def _mutate(self, old, new):
        self.assertIn(
            old,
            self.template,
            f"the template no longer contains {old!r}; the negative case "
            "cannot be constructed and needs to be updated.",
        )
        return self.template.replace(old, new, 1)

    def test_missing_metric_permission_is_detected(self):
        mutated = self._mutate(
            "                  - cloudwatch:PutMetricData",
            "                  - cloudwatch:GetMetricData",
        )
        self.assertFalse(
            _role_may_publish_only_to_the_namespace(mutated),
            "the IAM guard must fail when the role cannot publish metrics.",
        )

    def test_unscoped_metric_permission_is_detected(self):
        mutated = self._mutate(
            '                  - !Sub "arn:aws:cloudwatch:${AWS::Region}:'
            '${AWS::AccountId}:namespace/ChemicAlly"',
            '                  - !Sub "arn:aws:cloudwatch:${AWS::Region}:'
            '${AWS::AccountId}:*"',
        )
        self.assertFalse(
            _role_may_publish_only_to_the_namespace(mutated),
            "the IAM guard must fail when the grant is not scoped to the "
            "ChemicAlly namespace.",
        )

    def test_missing_alarm_is_detected(self):
        mutated = self._mutate(
            "    Type: AWS::CloudWatch::Alarm",
            "    Type: AWS::CloudWatch::Dashboard",
        )
        self.assertFalse(
            _alarm_watches_the_refusal_metric(mutated),
            "the alarm guard must fail when the alarm is not an alarm.",
        )

    def test_alarm_on_the_wrong_metric_is_detected(self):
        mutated = self._mutate(
            f"      MetricName: {METRIC_NAME}",
            "      MetricName: SomethingElse",
        )
        self.assertFalse(
            _alarm_watches_the_refusal_metric(mutated),
            "the alarm guard must fail when the alarm watches another "
            "metric.",
        )

    def test_alarm_without_a_threshold_is_detected(self):
        mutated = self._mutate("      Threshold: 5", "      Threshold:")
        self.assertFalse(
            _alarm_watches_the_refusal_metric(mutated),
            "the alarm guard must fail when the alarm has no threshold.",
        )

    def test_alarm_treating_missing_data_as_breaching_is_detected(self):
        mutated = self._mutate(
            "      TreatMissingData: notBreaching",
            "      TreatMissingData: breaching",
        )
        self.assertFalse(
            _alarm_watches_the_refusal_metric(mutated),
            "the alarm guard must fail when a quiet period would read as an "
            "alarm.",
        )
