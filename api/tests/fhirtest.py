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


# ---- a fake FHIR server for the unit tests -----------------------------------------------------------------


class FakeFhirClient:
    """Stands in for HAPI: stores what a transaction PUTs, resolves references against what it
    holds (HAPI enforces referential integrity, so this does too) and can be switched off."""

    def __init__(self, existing=()):
        from app.fhir_projection import FhirError

        self._error = FhirError
        self.resources: dict[str, dict] = {}
        self.existing = set(existing)
        self.down = False
        self.refuse: str | None = None  # a message: answer every transaction with HTTP 4xx
        self.transactions = 0
        self.exists_calls: list[str] = []

    def exists(self, ref: str) -> bool:
        self.exists_calls.append(ref)
        if self.down:
            raise self._error("HAPI unreachable: fake")
        return ref in self.existing or ref in self.resources

    def transaction(self, bundle: dict) -> None:
        self.transactions += 1
        if self.down:
            raise self._error("HAPI unreachable: fake")
        if self.refuse:
            raise self._error(f"HAPI answered HTTP 400: {self.refuse}")
        assert bundle["type"] == "transaction"
        incoming = {}
        for entry in bundle["entry"]:
            res = entry["resource"]
            assert entry["request"] == {"method": "PUT", "url": f"{res['resourceType']}/{res['id']}"}
            incoming[f"{res['resourceType']}/{res['id']}"] = res
        known = set(self.resources) | set(incoming) | self.existing
        for ref, res in incoming.items():
            for target in _references(res):
                if target not in known:
                    raise self._error(f"HAPI answered HTTP 400: HAPI-1094: Resource {target} not found, specified in {ref}")
        self.resources.update(incoming)

    def of_type(self, resource_type: str) -> list[dict]:
        return [r for r in self.resources.values() if r["resourceType"] == resource_type]


class PoisonFake(FakeFhirClient):
    """Refuses any transaction that writes the Task of a poisoned case, whatever else is in it."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.poison: set[str] = set()

    def transaction(self, bundle):
        ids = {e["resource"]["id"] for e in bundle["entry"]}
        if any(f"task-{c}" in ids for c in self.poison):
            self.transactions += 1
            raise self._error("HAPI answered HTTP 422: HAPI-0000 this resource is refused for good")
        super().transaction(bundle)


def _references(node):
    if isinstance(node, dict):
        ref = node.get("reference")
        if isinstance(ref, str) and "/" in ref and not ref.startswith("#"):
            yield ref
        for value in node.values():
            yield from _references(value)
    elif isinstance(node, list):
        for item in node:
            yield from _references(item)
