"""The origin guard's refusal metric (M007/S03).

A caller bypassing CloudFront is precisely the event an operator must see.
Until now that refusal was visible only as a middleware ``WARNING`` log
line, which nobody pages on. The guard therefore publishes one CloudWatch
datapoint per refused direct call, and the application stack declares an
alarm on it.

These tests pin the two properties that matter:

* the refusal is COUNTED -- exactly one datapoint per refused request,
  with the right namespace and metric name and NO dimensions (a dimension
  would split the metric into per-value series and the stack's alarm,
  which watches the plain metric, would never fire);
* the metric never changes the refusal -- the response is still the fixed
  403 body, and a publisher that raises still yields 403 and never a 500.

The publisher is exercised with an injected fake client; the middleware
path is exercised through the real Django test client, so the whole
middleware stack runs.
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from chemically import metrics
from chemically.middleware import DEFAULT_ORIGIN_HEADER_NAME, REFUSAL_BODY

TEST_SECRET = "test-only-origin-secret"


class RecordingClient:
    """A fake CloudWatch client that records put_metric_data calls."""

    def __init__(self, raises=False):
        self.calls = []
        self._raises = raises

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("CloudWatch is unavailable")


class RefusalMetricPublisherTests(SimpleTestCase):
    """The publisher's own contract."""

    def test_it_publishes_one_datapoint_without_dimensions(self):
        client = RecordingClient()
        published = metrics.publish_refusal_metric(client=client)
        self.assertTrue(published)
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["Namespace"], metrics.METRIC_NAMESPACE)
        self.assertEqual(call["Namespace"], "ChemicAlly")
        self.assertEqual(len(call["MetricData"]), 1)
        datapoint = call["MetricData"][0]
        self.assertEqual(datapoint["MetricName"], metrics.REFUSAL_METRIC_NAME)
        self.assertEqual(datapoint["MetricName"], "OriginGuardRefusals")
        self.assertEqual(datapoint["Value"], 1)
        self.assertEqual(datapoint["Unit"], "Count")
        self.assertNotIn(
            "Dimensions",
            datapoint,
            "the refusal metric must carry no dimensions: a dimension would "
            "split it into per-value time series and the stack's alarm "
            "would never fire.",
        )

    def test_a_failing_client_is_swallowed(self):
        client = RecordingClient(raises=True)
        published = metrics.publish_refusal_metric(client=client)
        self.assertFalse(published)

    def test_a_missing_client_is_swallowed(self):
        """No CloudWatch client (no region, no credentials) is not a failure."""
        with mock.patch.object(metrics, "_cloudwatch_client", return_value=None):
            published = metrics.publish_refusal_metric()
        self.assertFalse(published)


class CloudWatchClientFactoryTests(SimpleTestCase):
    """The lazily-created client must never be able to hang a refusal.

    botocore's defaults (60s connect, 60s read, 5 legacy-mode attempts)
    can outlast the 30s Lambda timeout, so a slow CloudWatch would turn a
    refused request into a gateway error instead of the fixed 403. The
    client is therefore created with bounded timeouts.
    """

    def setUp(self):
        super().setUp()
        self._saved_client = metrics._client
        metrics._client = None
        self.addCleanup(self._restore_client)

    def _restore_client(self):
        metrics._client = self._saved_client

    def test_the_client_is_created_with_bounded_timeouts(self):
        with mock.patch("boto3.client") as boto_client:
            client = metrics._cloudwatch_client()
        self.assertIsNotNone(client)
        boto_client.assert_called_once()
        kwargs = boto_client.call_args.kwargs
        config = kwargs["config"]
        self.assertEqual(config.connect_timeout, metrics.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(config.read_timeout, metrics.READ_TIMEOUT_SECONDS)
        self.assertEqual(config.retries["max_attempts"], metrics.MAX_ATTEMPTS)
        self.assertLess(
            metrics.MAX_ATTEMPTS
            * (metrics.CONNECT_TIMEOUT_SECONDS + metrics.READ_TIMEOUT_SECONDS),
            30,
            "the bounded CloudWatch call must finish well inside the 30s "
            "Lambda timeout, or a slow CloudWatch would turn a refused "
            "request into a gateway error instead of a 403.",
        )

    def test_the_client_is_cached(self):
        with mock.patch("boto3.client") as boto_client:
            first = metrics._cloudwatch_client()
            second = metrics._cloudwatch_client()
        self.assertIs(first, second)
        self.assertEqual(boto_client.call_count, 1)

    def test_a_client_that_cannot_be_created_is_swallowed(self):
        with mock.patch("boto3.client", side_effect=RuntimeError("no boto3")):
            self.assertIsNone(metrics._cloudwatch_client())


class RefusalMetricMiddlewareTests(SimpleTestCase):
    """The refusal path: counted, and never changed by the metric."""

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_a_refusal_is_counted_once_and_still_refused(self):
        client = RecordingClient()
        with mock.patch(
            "chemically.middleware.publish_refusal_metric",
            side_effect=lambda: metrics.publish_refusal_metric(client=client),
        ):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 403)
        self.assertIn(REFUSAL_BODY, response.content.decode())
        self.assertEqual(len(client.calls), 1)

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_a_failing_metric_does_not_change_the_refusal(self):
        """The metric is a side effect: it must never become a 500."""
        with mock.patch(
            "chemically.middleware.publish_refusal_metric",
            side_effect=RuntimeError("metric backend exploded"),
        ):
            response = self.client.get("/")
        self.assertEqual(
            response.status_code,
            403,
            "a metric failure must not turn a refused request into a 500.",
        )
        self.assertIn(REFUSAL_BODY, response.content.decode())

    @override_settings(
        ORIGIN_HEADER_SECRET="",
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_an_inert_guard_counts_nothing(self):
        """With no secret configured nothing is refused, so nothing is counted."""
        client = RecordingClient()
        with mock.patch(
            "chemically.middleware.publish_refusal_metric",
            side_effect=lambda: metrics.publish_refusal_metric(client=client),
        ):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            client.calls,
            [],
            "an inert guard must not publish refusal metrics.",
        )

    @override_settings(
        ORIGIN_HEADER_SECRET=TEST_SECRET,
        ORIGIN_HEADER_NAME=DEFAULT_ORIGIN_HEADER_NAME,
    )
    def test_an_accepted_request_counts_nothing(self):
        """A request through CloudFront is served, not refused, not counted."""
        client = RecordingClient()
        with mock.patch(
            "chemically.middleware.publish_refusal_metric",
            side_effect=lambda: metrics.publish_refusal_metric(client=client),
        ):
            response = self.client.get(
                "/", headers={DEFAULT_ORIGIN_HEADER_NAME: TEST_SECRET}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.calls, [])
