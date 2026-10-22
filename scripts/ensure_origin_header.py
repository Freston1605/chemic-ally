#!/usr/bin/env python3
"""Ensure the CloudFront origin header is stamped on the live distribution.

The origin header guard (M006/S03, R029) works like this: CloudFront
stamps a custom header on every origin request, and the Django middleware
refuses any request that arrives without it. The header is what makes the
guard possible -- a guard that refuses requests the origin does not stamp
would take the whole site down.

CloudFormation cannot deliver that header to an EXISTING distribution. Its
diff does not detect a change to a nested list property like
``Origins.Items[].OriginCustomHeaders``, so the update is silently dropped
and the distribution keeps the configuration it was created with -- which
predates the guard. Observed live on 2026-10-22: the distribution was
updated (its Comment changed) and the origin custom headers stayed absent.

This script closes that gap from the outside: it reads the live
distribution, and if the origin that serves the Function URL does not
stamp the header, it adds it with ``UpdateDistribution`` and verifies the
result. It is idempotent (a distribution that already stamps the header is
left alone), it changes nothing else in the configuration, and it fails
loudly when it cannot deliver the header.

The secret is read from the ``ORIGIN_SECRET`` environment variable, is
compared in memory, and is never printed, logged or written to disk.

Exit codes: 0 = the header is stamped, 1 = it could not be stamped.
"""

import argparse
import os
import sys

import boto3

# CloudFront is a global service; its API endpoint lives in us-east-1.
CLOUDFRONT_REGION = "us-east-1"

#: The header name the guard expects. The stack's OriginHeaderName
#: parameter is authoritative; this is only the fallback.
DEFAULT_ORIGIN_HEADER_NAME = "X-ChemicAlly-Origin"


def _stack_outputs(stack):
    return {
        output.get("OutputKey"): output.get("OutputValue")
        for output in stack.get("Outputs", []) or []
    }


def _stack_parameters(stack):
    return {
        parameter.get("ParameterKey"): parameter.get("ParameterValue")
        for parameter in stack.get("Parameters", []) or []
    }


def _origin_for_host(config, host):
    """Return the origin item that serves ``host``, or None."""
    origins = (config.get("Origins") or {}).get("Items") or []
    for origin in origins:
        domain = (origin.get("DomainName") or "").lower()
        if domain == (host or "").lower():
            return origin
    return None


def _custom_header_items(origin):
    """Return the origin's custom headers as a list of {HeaderName, ...}.

    The CloudFront API carries them in a structure
    (``CustomHeaders: {Quantity: N, Items: [...]}``), not in a plain list.
    """
    headers = origin.get("CustomHeaders") or {}
    if isinstance(headers, dict):
        return list(headers.get("Items") or [])
    return list(headers)


def _stamped_value(origin, header_name):
    """Return the value the origin stamps for ``header_name``, or None."""
    for header in _custom_header_items(origin):
        if header.get("HeaderName") == header_name:
            return header.get("HeaderValue")
    return None


def _host_of(url):
    """Return the host part of a URL."""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]


def run(
    stack_name,
    region,
    *,
    secret=None,
    client_factory=boto3.client,
):
    """Stamp the header if it is missing; return 0 or raise RuntimeError."""
    secret = secret if secret is not None else os.environ.get("ORIGIN_SECRET", "")
    if not secret:
        raise RuntimeError(
            "the ORIGIN_SECRET environment variable is empty; the origin "
            "header cannot be stamped"
        )

    cfn_client = client_factory("cloudformation", region_name=region)
    stack = cfn_client.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = _stack_outputs(stack)
    parameters = _stack_parameters(stack)

    distribution_arn = outputs.get("DistributionArn")
    function_url = outputs.get("FunctionUrl")
    if not distribution_arn or not function_url:
        raise RuntimeError(
            f"stack {stack_name!r} must export DistributionArn and "
            "FunctionUrl for the origin header to be located"
        )
    distribution_id = distribution_arn.rsplit("/", 1)[-1]
    header_name = parameters.get("OriginHeaderName") or DEFAULT_ORIGIN_HEADER_NAME
    host = _host_of(function_url)

    cloudfront_client = client_factory(
        "cloudfront", region_name=CLOUDFRONT_REGION
    )
    distribution = cloudfront_client.get_distribution(
        Id=distribution_id
    )["Distribution"]
    config = distribution.get("DistributionConfig") or {}
    etag = distribution.get("ETag")

    origin = _origin_for_host(config, host)
    if origin is None:
        raise RuntimeError(
            f"no origin of the live distribution serves the Function URL "
            f"host {host!r}; the origin header cannot be stamped"
        )

    if _stamped_value(origin, header_name) == secret:
        print(
            f"PASS: the origin already stamps {header_name} with the "
            "configured value"
        )
        return 0

    # The CloudFront API carries the origin's custom headers in a structure
    # ({Quantity, Items}); the template's OriginCustomHeaders is the
    # CloudFormation spelling of the same thing.
    headers = _custom_header_items(origin)
    replaced = False
    for header in headers:
        if header.get("HeaderName") == header_name:
            header["HeaderValue"] = secret
            replaced = True
            break
    if not replaced:
        headers.append({"HeaderName": header_name, "HeaderValue": secret})
    origin["CustomHeaders"] = {"Quantity": len(headers), "Items": headers}

    response = cloudfront_client.update_distribution(
        DistributionConfig=config,
        IfMatch=etag,
    )
    updated = (response or {}).get("Distribution") or {}

    # Verify against the state CloudFront now reports, not against what we
    # sent: a silent refusal must not pass as a stamped header.
    verify = cloudfront_client.get_distribution(
        Id=updated.get("Id") or distribution_id
    )["Distribution"]
    verify_origin = _origin_for_host(
        verify.get("DistributionConfig") or {}, host
    )
    if verify_origin is None:
        raise RuntimeError(
            "the distribution no longer serves the Function URL host after "
            "the update; the origin header could not be verified"
        )
    stamped = _stamped_value(verify_origin, header_name)
    if stamped != secret:
        raise RuntimeError(
            "the origin header was not stamped after the update; the guard "
            "may not be enforced until it is"
        )
    print(
        f"PASS: the origin now stamps {header_name} with the configured "
        "value"
    )
    return 0


def main(argv=None, client_factory=boto3.client) -> int:
    parser = argparse.ArgumentParser(
        description="Ensure the CloudFront origin header is stamped"
    )
    parser.add_argument("--stack-name", default="chemically")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args(argv)
    try:
        return run(
            args.stack_name,
            args.region,
            client_factory=client_factory,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
