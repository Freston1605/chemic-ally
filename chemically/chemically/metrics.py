"""CloudWatch metric emission for the origin header guard (M007/S03).

The origin guard refuses any request that did not arrive through the
CloudFront origin. Until now that refusal was visible only as a middleware
``WARNING`` log line, which nobody pages on: a caller bypassing CloudFront
is precisely the event an operator must see, and detection that depends on
somebody grepping logs is not detection.

This module makes the refusal countable. It publishes ONE datapoint per
refused direct call into the ``ChemicAlly`` namespace under the metric
``OriginGuardRefusals``, which the application stack's CloudWatch alarm
watches (M007/S03/T03).

Two properties matter:

* **Best effort.** The refusal is the outcome; the metric is a side
  effect. A CloudWatch failure -- no credentials, throttling, a missing
  region -- must be logged and swallowed. It must never turn a refused
  request into a 500, and it must never change the fixed refusal body.
* **No dimensions.** A dimension would split the metric into one time
  series per dimension value, and the stack's alarm watches the plain
  metric -- it would never fire. The metric is therefore a single
  undimensioned series: refusals per five minutes, across all paths.

The CloudWatch client is created lazily and cached, so importing this
module costs nothing and a local runserver without AWS credentials is
completely unaffected.
"""

import logging
import os

logger = logging.getLogger(__name__)

#: The CloudWatch namespace every ChemicAlly metric lives in.
METRIC_NAMESPACE = "ChemicAlly"

#: The metric that counts refused direct calls.
REFUSAL_METRIC_NAME = "OriginGuardRefusals"

# Cached CloudWatch client. None means "not created yet"; the client is
# created on first use so that importing this module never touches AWS.
_client = None

#: Bounds on the CloudWatch call. The refusal is on the request's critical
#: path, so the metric must not be able to hang the refusal: botocore's
#: defaults (60s connect, 60s read, 5 legacy-mode attempts) can outlast the
#: 30s Lambda timeout, which would turn the refusal into a gateway error
#: instead of the fixed 403.
#:
#: The bounds are deliberately tight (2s connect, 3s read, a single
#: attempt): the metric is best-effort by design, and a refusal that
#: takes seconds to answer is a refusal that looks like an outage. The
#: endpoint (a VPC interface endpoint with PRIVATE DNS, so the Lambda
#: resolves the monitoring API to the endpoint instead of the public
#: address it cannot reach) is what makes the publish succeed; these
#: bounds are only the safety net.
CONNECT_TIMEOUT_SECONDS = 2
READ_TIMEOUT_SECONDS = 3
MAX_ATTEMPTS = 1


def _cloudwatch_client():
    """Return a cached CloudWatch client, or None when one cannot be made."""
    global _client
    if _client is None:
        try:
            import boto3
            from botocore.config import Config

            client_config = Config(
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                read_timeout=READ_TIMEOUT_SECONDS,
                retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"},
            )
            region = os.environ.get("AWS_REGION") or os.environ.get(
                "AWS_DEFAULT_REGION"
            )
            if region:
                _client = boto3.client(
                    "cloudwatch", region_name=region, config=client_config
                )
            else:
                _client = boto3.client("cloudwatch", config=client_config)
        except Exception:  # noqa: BLE001 - no AWS, no metrics, no failure
            logger.warning(
                "Could not create a CloudWatch client; origin guard refusal "
                "metrics will not be published",
                exc_info=True,
            )
            return None
    return _client


def publish_refusal_metric(client=None):
    """Publish one datapoint for a refused direct call.

    Returns True when the datapoint was published, False when it could
    not be. Never raises: the caller is on a refusal path and the refusal
    must not become a server error.
    """
    try:
        client = client if client is not None else _cloudwatch_client()
        if client is None:
            return False
        client.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": REFUSAL_METRIC_NAME,
                    "Value": 1,
                    "Unit": "Count",
                }
            ],
        )
        return True
    except Exception:  # noqa: BLE001 - the metric must never break a refusal
        logger.warning(
            "Could not publish the origin guard refusal metric; the refusal "
            "itself is unaffected",
            exc_info=True,
        )
        return False
