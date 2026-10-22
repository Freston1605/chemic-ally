"""Unit tests for the origin-header ensurer (scripts/ensure_origin_header.py).

The origin header guard refuses any request the CloudFront origin does not
stamp. CloudFormation cannot deliver that header to an EXISTING
distribution -- its diff does not detect a change to a nested list property
like ``Origins.Items[].OriginCustomHeaders`` -- so the header was silently
absent in production while the template declared it.

This script closes that gap from the outside: it adds the header when it is
missing and verifies the result against the state CloudFront reports.

Each test pins one behaviour:

* an origin that already stamps the header -> no write at all;
* an origin without the header -> exactly one update, carrying the header
  and nothing else changed;
* a header stamped with a DIFFERENT value -> it is replaced;
* an update CloudFront silently refuses -> non-zero (the guard must not be
  enabled against an unstamped origin);
* the secret is never printed and never leaves the process.

scripts/ is not a package, so the module is loaded with
importlib.util.spec_from_file_location. All AWS interaction goes through an
injected ``client_factory`` returning fake clients - no network access.
"""

import copy
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "ensure_origin_header.py"

_spec = importlib.util.spec_from_file_location(
    "ensure_origin_header", MODULE_PATH
)
ensure_origin_header = importlib.util.module_from_spec(_spec)
sys.modules["ensure_origin_header"] = ensure_origin_header
_spec.loader.exec_module(ensure_origin_header)

REGION = "us-east-2"
STACK = "chemically"
SECRET = "test-only-origin-secret"
HEADER_NAME = "X-ChemicAlly-Origin"
DISTRIBUTION_ID = "ED6ELRIZI1Y80"
FUNCTION_HOST = "gaxjhbac2bt73uizilf33qdbse0llanf.lambda-url.us-east-2.on.aws"
DISTRIBUTION_ARN = (
    f"arn:aws:cloudfront::768125641662:distribution/{DISTRIBUTION_ID}"
)


def _stack():
    return {
        "StackName": STACK,
        "StackStatus": "UPDATE_COMPLETE",
        "Outputs": [
            {"OutputKey": "DistributionArn", "OutputValue": DISTRIBUTION_ARN},
            {
                "OutputKey": "FunctionUrl",
                "OutputValue": f"https://{FUNCTION_HOST}/",
            },
        ],
        "Parameters": [
            {"ParameterKey": "OriginHeaderName", "ParameterValue": HEADER_NAME},
            {"ParameterKey": "OriginSecret", "ParameterValue": "****"},
        ],
    }


def _distribution(headers=None):
    return {
        "Id": DISTRIBUTION_ID,
        "ETag": "ETAG1",
        "DomainName": "d3joi59ifa013d.cloudfront.net",
        "Status": "Deployed",
        "DistributionConfig": {
            "Comment": "ChemicAlly public distribution (us-east-2 app stack)",
            "Enabled": True,
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": "DjangoLambda",
                        "DomainName": FUNCTION_HOST,
                        "CustomOriginConfig": {
                            "OriginProtocolPolicy": "https-only"
                        },
                        "CustomHeaders": headers or [],
                    }
                ],
            },
        },
    }


class FakeCloudFront:
    """A fake CloudFront client that records the updates it received."""

    def __init__(self, distribution, refuse_update=False):
        self._distribution = distribution
        self._refuse_update = refuse_update
        self.updates = []

    def get_distribution(self, Id):
        # A copy, so the script's in-place edit of the configuration it
        # read cannot leak back into what the next read reports.
        return {"Distribution": copy.deepcopy(self._distribution)}

    def update_distribution(self, DistributionConfig, IfMatch):
        self.updates.append(
            {"config": DistributionConfig, "etag": IfMatch}
        )
        if self._refuse_update:
            # CloudFront accepted the call but did not apply the header.
            return {"Distribution": self._distribution}
        self._distribution["DistributionConfig"] = copy.deepcopy(
            DistributionConfig
        )
        return {"Distribution": self._distribution}


class FakeClients:
    def __init__(self, cloudfront):
        self._cloudfront = cloudfront

    def __call__(self, service, region_name=None):
        if service == "cloudformation":
            return SimpleNamespace(
                describe_stacks=lambda StackName: {"Stacks": [_stack()]}
            )
        if service == "cloudfront":
            return self._cloudfront
        raise AssertionError(f"unexpected service requested: {service}")


class EnsureOriginHeaderTests(SimpleTestCase):
    """The header lands, and only when it has to."""

    def _run(self, cloudfront, secret=SECRET):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = ensure_origin_header.run(
                STACK,
                REGION,
                secret=secret,
                client_factory=FakeClients(cloudfront),
            )
        return result, buffer.getvalue()

    def test_an_already_stamped_origin_is_left_alone(self):
        cloudfront = FakeCloudFront(
            _distribution(
                headers=[{"HeaderName": HEADER_NAME, "HeaderValue": SECRET}]
            )
        )
        result, output = self._run(cloudfront)
        self.assertEqual(result, 0)
        self.assertEqual(cloudfront.updates, [])
        self.assertIn("already stamps", output)

    def test_a_missing_header_is_added(self):
        cloudfront = FakeCloudFront(_distribution())
        result, output = self._run(cloudfront)
        self.assertEqual(result, 0)
        self.assertEqual(len(cloudfront.updates), 1)
        origins = cloudfront.updates[0]["config"]["Origins"]["Items"]
        headers = origins[0]["CustomHeaders"]
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0]["HeaderName"], HEADER_NAME)
        self.assertEqual(headers[0]["HeaderValue"], SECRET)
        self.assertIn("now stamps", output)

    def test_nothing_else_in_the_configuration_changes(self):
        cloudfront = FakeCloudFront(_distribution())
        self._run(cloudfront)
        config = cloudfront.updates[0]["config"]
        self.assertEqual(
            config["Comment"],
            _distribution()["DistributionConfig"]["Comment"],
        )
        self.assertTrue(config["Enabled"])
        self.assertEqual(
            config["Origins"]["Items"][0]["CustomOriginConfig"],
            {"OriginProtocolPolicy": "https-only"},
        )
        self.assertEqual(cloudfront.updates[0]["etag"], "ETAG1")

    def test_a_different_value_is_replaced(self):
        cloudfront = FakeCloudFront(
            _distribution(
                headers=[
                    {"HeaderName": HEADER_NAME, "HeaderValue": "an-old-secret"}
                ]
            )
        )
        result, _ = self._run(cloudfront)
        self.assertEqual(result, 0)
        headers = cloudfront.updates[0]["config"]["Origins"]["Items"][0][
            "CustomHeaders"
        ]
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0]["HeaderValue"], SECRET)

    def test_a_silently_refused_update_fails(self):
        cloudfront = FakeCloudFront(_distribution(), refuse_update=True)
        with self.assertRaises(RuntimeError):
            self._run(cloudfront)

    def test_the_secret_is_never_printed(self):
        cloudfront = FakeCloudFront(_distribution())
        _, output = self._run(cloudfront)
        self.assertNotIn(SECRET, output)

    def test_an_empty_secret_is_refused(self):
        cloudfront = FakeCloudFront(_distribution())
        with self.assertRaises(RuntimeError):
            self._run(cloudfront, secret="")
        self.assertEqual(cloudfront.updates, [])

    def test_an_origin_that_does_not_serve_the_function_url_is_refused(self):
        distribution = _distribution()
        distribution["DistributionConfig"]["Origins"]["Items"][0][
            "DomainName"
        ] = "someone-else.lambda-url.us-east-2.on.aws"
        cloudfront = FakeCloudFront(distribution)
        with self.assertRaises(RuntimeError):
            self._run(cloudfront)
        self.assertEqual(cloudfront.updates, [])


class EnsureOriginHeaderCliTests(SimpleTestCase):
    """The CLI exits non-zero when the header cannot be stamped."""

    def test_main_returns_one_on_failure(self):
        cloudfront = FakeCloudFront(_distribution(), refuse_update=True)
        code = ensure_origin_header.main(
            ["--stack-name", STACK, "--region", REGION],
            client_factory=FakeClients(cloudfront),
        )
        self.assertEqual(code, 1)
