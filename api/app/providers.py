"""Registration owners and the referral reroute order (A21, ADR-032), from config/providers.yaml.

Pure lookups over a loaded file. Nothing here reads a clock, writes an event or talks to a store: `app.automation`
decides when to use them. The default owner of a provider is a development seed user named in the file; the actor
string is derived from role and name exactly as at login (`app.auth.opaque_user_id`), so the event log holds the opaque
id and never the name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.auth import opaque_user_id


class ProvidersError(ValueError):
    """config/providers.yaml is malformed. Raised at load time."""


@dataclass(frozen=True)
class OwnerSeed:
    provider: str
    role: str
    name: str

    @property
    def user_id(self) -> str:
        return opaque_user_id(self.role, self.name)

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.user_id}"


@dataclass(frozen=True)
class Providers:
    default_provider: str
    owners: dict[str, OwnerSeed]
    specialty_by_pathway: dict[str, str]
    reroute_reasons: tuple[str, ...]
    reroute_order: dict[str, tuple[str, ...]]

    @property
    def registration_providers(self) -> tuple[str, ...]:
        return tuple(self.owners)

    def owner_for(self, registered_provider: str | None) -> OwnerSeed:
        """The default owner for the provider the patient is registered with; the default provider's when the case
        names none or an unknown one (a case is never left without a route to a human)."""
        return self.owners.get(registered_provider or "") or self.owners[self.default_provider]

    def reroute_list(self, pathway_name: str) -> tuple[str, ...]:
        specialty = self.specialty_by_pathway.get(pathway_name)
        return self.reroute_order.get(specialty, ()) if specialty else ()

    def next_provider(self, pathway_name: str, tried) -> str | None:
        """The first provider in the pathway's reroute order that has not been asked yet (case-insensitive), else None:
        the list is exhausted and a human must handle the case."""
        done = {t.strip().casefold() for t in tried if isinstance(t, str)}
        return next((p for p in self.reroute_list(pathway_name) if p.casefold() not in done), None)

    def reroutes_reason(self, reason: str | None) -> bool:
        return reason in self.reroute_reasons


def provider_for_patient(patient_id: str, providers) -> str:
    """Deterministic registration of a Synthea patient id to one of `providers` (sha256, then modulo). Used by the
    cohort pipeline so a re-run assigns the same provider; the same arithmetic is in simulator/create_cases.py."""
    ordered = sorted(providers)
    return ordered[int(hashlib.sha256(patient_id.encode()).hexdigest(), 16) % len(ordered)]


def parse_providers(raw: object) -> Providers:
    if not isinstance(raw, dict):
        raise ProvidersError("providers file must be a mapping")
    registration = raw.get("registration")
    if not isinstance(registration, dict) or not registration:
        raise ProvidersError("providers: `registration` must map at least one provider to its owner")
    owners: dict[str, OwnerSeed] = {}
    for provider, body in registration.items():
        owner = body.get("owner") if isinstance(body, dict) else None
        if not isinstance(provider, str) or not provider.strip() or not isinstance(owner, dict):
            raise ProvidersError(f"providers: registration entry {provider!r} needs an `owner` with role and name")
        role, name = owner.get("role"), owner.get("name")
        if role != "primary_care_owner":
            raise ProvidersError(f"providers: {provider!r}: the default owner must be a primary_care_owner, got {role!r}")
        if not isinstance(name, str) or not name.strip():
            raise ProvidersError(f"providers: {provider!r}: owner name is required")
        owners[provider] = OwnerSeed(provider, role, name)
    default = raw.get("default_provider")
    if default not in owners:
        raise ProvidersError("providers: `default_provider` must be one of the registration providers")
    referral = raw.get("referral") or {}
    specialty = referral.get("specialty_by_pathway") or {}
    order = referral.get("reroute_order") or {}
    if not isinstance(specialty, dict) or not isinstance(order, dict):
        raise ProvidersError("providers: referral.specialty_by_pathway and referral.reroute_order must be mappings")
    for spec, names in order.items():
        if not isinstance(names, list) or not names or len(set(names)) != len(names) or not all(
                isinstance(n, str) and n.strip() for n in names):
            raise ProvidersError(f"providers: reroute order for {spec!r} must be a list of distinct provider names")
    for pathway_name, spec in specialty.items():
        if spec not in order:
            raise ProvidersError(f"providers: pathway {pathway_name!r} names specialty {spec!r} with no reroute order")
    reasons = referral.get("reroute_reasons") or []
    if not isinstance(reasons, list) or not all(isinstance(r, str) and r for r in reasons):
        raise ProvidersError("providers: referral.reroute_reasons must be a list of reason codes")
    return Providers(default, owners, dict(specialty), tuple(reasons),
                     {k: tuple(v) for k, v in order.items()})


def load_providers(path: str | Path) -> Providers:
    return parse_providers(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _cached(path: str) -> Providers:
    return load_providers(path)


def get_providers(path: str | None = None) -> Providers:
    """The providers file named by settings (or `path`), loaded once per process and path."""
    if path is None:
        from app.config import settings

        path = settings.providers_path
    return _cached(str(path))
