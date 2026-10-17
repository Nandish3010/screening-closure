"""Helpers for the integration tests that talk to the running HAPI server (A12, A15).

Everything the tests create carries the `meta.tag` below, so test resources can be identified
(and removed with `DELETE [base]/<Type>?_tag=<system>|<code>` if HAPI has cascade/expunge on)."""

import json
import os
import urllib.error
import urllib.request

FHIR_URL = os.environ.get("TEST_FHIR_URL", "http://localhost:8080/fhir").rstrip("/")
TAG_SYSTEM = "https://screening-closure.example/test"
TAG_CODE = "closure-engine-test"
TAG = {"system": TAG_SYSTEM, "code": TAG_CODE}


def reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{FHIR_URL}/metadata", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def request(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{FHIR_URL}/{path.lstrip('/')}", data=data, method=method,
        headers={"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors="replace")


def put_tagged(resource: dict):
    """PUT a resource with a client-chosen id, tagged as test data."""
    resource = {**resource, "meta": {**resource.get("meta", {}), "tag": [TAG]}}
    return request("PUT", f"{resource['resourceType']}/{resource['id']}", resource)


def get(ref: str):
    return request("GET", ref)
