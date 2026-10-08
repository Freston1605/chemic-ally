#!/usr/bin/env python3
"""Cloudflare-backed deploy helpers for ChemicAlly.

Route53 is intentionally not used. CloudFront still needs an ACM certificate
(us-east-1) covering the apex and www names, and ACM's DNS validation records
are written into the Cloudflare zone instead of Route53.

Subcommands
-----------
ensure-cert   Provision (or reuse) the ACM certificate and create its DNS
              validation CNAMEs in Cloudflare. Prints only the certificate ARN
              on stdout so callers can capture it; diagnostics go to stderr.
sync-records  Point the apex and www CNAMEs at the current CloudFront
              distribution (read from the CloudFormation stack outputs).

Environment
-----------
CLOUDFLARE_API_TOKEN  Cloudflare API token with Zone:DNS:Edit on the zone.
CLOUDFLARE_ZONE_ID    Cloudflare zone id for the domain.

Usage
-----
    python scripts/cloudflare_deploy.py ensure-cert --domain chemic-ally.xyz
    python scripts/cloudflare_deploy.py sync-records --domain chemic-ally.xyz \
        --stack chemically
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import boto3

CF_API = "https://api.cloudflare.com/client/v4"


def _log(message):
    print(message, file=sys.stderr)


def cf_request(method, path, token, payload=None):
    """Call the Cloudflare API and return the ``result`` field."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{CF_API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode())
    if not body.get("success"):
        raise SystemExit(
            f"Cloudflare API {method} {path} failed: {body.get('errors')}"
        )
    return body["result"]


def upsert_cname(token, zone_id, name, content):
    """Create or update a DNS-only CNAME record in Cloudflare."""
    name = name.rstrip(".")
    query = f"/zones/{zone_id}/dns_records?type=CNAME&name={name}"
    existing = cf_request("GET", query, token)
    payload = {
        "type": "CNAME",
        "name": name,
        "content": content,
        "ttl": 1,
        "proxied": False,
    }
    if existing:
        record_id = existing[0]["id"]
        cf_request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", token, payload)
        _log(f"Cloudflare DNS updated: {name} -> {content}")
    else:
        cf_request("POST", f"/zones/{zone_id}/dns_records", token, payload)
        _log(f"Cloudflare DNS created: {name} -> {content}")


def _find_issued_cert(acm, names):
    for summary in acm.list_certificates(CertificateStatuses=["ISSUED"])[
        "CertificateSummaryList"
    ]:
        detail = acm.describe_certificate(
            CertificateArn=summary["CertificateArn"]
        )["Certificate"]
        san = set(detail.get("SubjectAlternativeNames", []))
        san.add(detail["DomainName"])
        if names.issubset(san):
            return summary["CertificateArn"]
    return None


def cmd_ensure_cert(args):
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    zone_id = os.environ["CLOUDFLARE_ZONE_ID"]
    www = args.www or f"www.{args.domain}"
    names = {args.domain, www}

    acm = boto3.client("acm", region_name=args.region)
    arn = _find_issued_cert(acm, names)
    if arn:
        _log(f"Reusing issued certificate {arn}")
    else:
        arn = acm.request_certificate(
            DomainName=args.domain,
            SubjectAlternativeNames=[www],
            ValidationMethod="DNS",
            IdempotencyToken="chemically_cloudfront_cert",
        )["CertificateArn"]
        _log(f"Requested certificate {arn}")

    deadline = time.time() + args.timeout
    while True:
        detail = acm.describe_certificate(CertificateArn=arn)["Certificate"]
        status = detail["Status"]
        if status == "ISSUED":
            break
        if status in ("FAILED", "VALIDATION_TIMED_OUT", "REVOKED"):
            raise SystemExit(
                f"Certificate {arn} entered terminal state {status}"
            )
        for option in detail.get("DomainValidationOptions", []):
            record = option.get("ResourceRecord")
            if record:
                upsert_cname(
                    token, zone_id, record["Name"], record["Value"]
                )
        if time.time() > deadline:
            raise SystemExit(
                "Timed out waiting for certificate validation "
                f"(status={status}). Is the registrar delegating the zone "
                "to Cloudflare yet?"
            )
        time.sleep(15)

    print(arn)


def cmd_sync_records(args):
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    zone_id = os.environ["CLOUDFLARE_ZONE_ID"]

    cfn = boto3.client("cloudformation", region_name=args.region)
    stack = cfn.describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    if "CloudFrontDomain" not in outputs:
        raise SystemExit("Stack output CloudFrontDomain not found")
    cf_domain = outputs["CloudFrontDomain"]

    upsert_cname(token, zone_id, args.domain, cf_domain)
    upsert_cname(token, zone_id, f"www.{args.domain}", cf_domain)
    print(f"{args.domain} and www.{args.domain} -> {cf_domain}")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--domain", default="chemic-ally.xyz")

    parser = argparse.ArgumentParser(description="Cloudflare deploy helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    cert = sub.add_parser(
        "ensure-cert", parents=[common], help="provision/validate ACM cert"
    )
    cert.add_argument("--www", default=None)
    cert.add_argument("--region", default="us-east-1")
    cert.add_argument("--timeout", type=int, default=1800)
    cert.set_defaults(func=cmd_ensure_cert)

    sync = sub.add_parser(
        "sync-records", parents=[common], help="point DNS at CloudFront"
    )
    sync.add_argument("--stack", default="chemically")
    sync.add_argument("--region", default="us-east-2")
    sync.set_defaults(func=cmd_sync_records)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
