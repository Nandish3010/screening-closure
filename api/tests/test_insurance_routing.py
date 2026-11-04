"""T-A102 to T-A106 (A40, ADR-054): insurance-aware referral routing.

A case may carry a synthetic insurance scheme on its genesis event. Routing and re-routing prefer providers in the scheme's
network (config/providers.yaml `referral.networks`); when none is left in network the referral goes to the best provider out
of it, flagged, and is never blocked; a case with no scheme (or `unknown`) is routed exactly as before. In-memory stores."""

from __future__ import annotations

import copy
import dataclasses
import json
from uuid import UUID

import pytest
import yaml

from app import agency, insurance, payloads, providers, readmodel
from app.agency import tools as agency_tools
from app.config import settings as app_settings
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import AUTOMATION_ACTOR
from test_automation import (  # noqa: F401  (helpers only; the tests of that module are not imported)
    HUDA_LOGIN, KARIM, LAYLA_LOGIN, RANA, client, log, login, model, new_case, owned_to_patient_acknowledged, post, reject, scan,
    world,
)

PATHWAY = load_pathway(app_settings.pathway_path)
GASTRO = "colorectal_fit"
CORNICHE, AL_NOOR, MARINA = "Corniche Gastro Unit", "Al Noor Endoscopy Centre", "Marina Digestive Health"


@pytest.fixture
def fresh():
    with world():
        yield


@pytest.fixture(scope="module")
def shipped():
    return providers.get_providers()


def owner():
    return login(*LAYLA_LOGIN[:2], LAYLA_LOGIN[2])


def case_with(scheme, *, referral_to=CORNICHE):
    """A bowel case at patient_acknowledged with a referral to `referral_to` pending (a person asked for it)."""
    extra = {} if scheme is None else {"insurance_scheme": scheme}
    cid = new_case(**extra)
    post(cid, "accept_ownership", login("primary_care_owner", "Dr. Layla Haddad", "Riverside Primary Care"))
    post(cid, "contact_patient", owner())
    post(cid, "patient_acknowledge", login("patient", "pat", patient_ref="Patient/1"))
    post(cid, "handoff_requested", owner(), {"to_provider": referral_to})
    return cid


def requests(cid):
    return [(e.payload["to_provider"], e.payload.get("network"), e.payload.get("auto")) for e in log(cid) if e.type == "handoff_requested"]


SPECIALISTS = {CORNICHE: KARIM, AL_NOOR: RANA, MARINA: HUDA_LOGIN}


def reject_pending(cid):
    reject(cid, SPECIALISTS[model(cid).pending_handoff["to_provider"]])


# ---------------------------------------------------------------------------------------------------------------------
# T-A102 routing unit tests (app.providers)
# ---------------------------------------------------------------------------------------------------------------------


def test_a_known_scheme_goes_to_the_first_provider_in_network_and_names_it(shipped):
    r = shipped.route(GASTRO, "basic", [])
    assert (r.provider, r.network) == (CORNICHE, insurance.IN_NETWORK)
    # a scheme the first provider does not accept: the next one that does, not the next in order
    custom = dataclasses.replace(shipped, networks={**shipped.networks, CORNICHE: frozenset({"thiqa"}), AL_NOOR: frozenset({"thiqa"})})
    r = custom.route(GASTRO, "basic", [])
    assert (r.provider, r.network) == (MARINA, insurance.IN_NETWORK)
    assert custom.route(GASTRO, "thiqa", []).provider == CORNICHE


def test_when_none_is_in_network_the_best_out_of_network_provider_is_chosen_and_flagged(shipped):
    custom = dataclasses.replace(shipped, networks={n: frozenset({"thiqa"}) for n in shipped.reroute_list(GASTRO)})
    r = custom.route(GASTRO, "basic", [])
    assert (r.provider, r.network) == (CORNICHE, insurance.OUT_OF_NETWORK)  # first in the order, never None
    assert custom.route(GASTRO, "basic", [CORNICHE]).provider == AL_NOOR
    # the in-network ones asked and used up: what is left is out of network, flagged
    r = shipped.route(GASTRO, "enhanced", [CORNICHE, AL_NOOR])
    assert (r.provider, r.network) == (MARINA, insurance.OUT_OF_NETWORK)
    assert shipped.route(GASTRO, "enhanced", list(shipped.reroute_list(GASTRO))) is None  # every provider asked: exhausted


@pytest.mark.parametrize("scheme", [None, "unknown", "no-such-scheme"])
def test_an_unknown_or_missing_scheme_is_routed_as_before(shipped, scheme):
    for tried in ([], [CORNICHE], [CORNICHE, AL_NOOR], [AL_NOOR], list(shipped.reroute_list(GASTRO))):
        r = shipped.route(GASTRO, scheme, tried)
        nxt = shipped.next_provider(GASTRO, tried)
        assert (r.provider if r else None) == nxt and (r is None or r.network is None)
    assert shipped.network_of(CORNICHE, scheme) is None and shipped.order_for([AL_NOOR, MARINA], scheme) == [AL_NOOR, MARINA]


def test_reroute_after_a_rejection_skips_the_tried_and_stays_in_network(shipped):
    custom = dataclasses.replace(shipped, networks={**shipped.networks, AL_NOOR: frozenset({"thiqa"})})
    assert custom.route(GASTRO, "basic", [CORNICHE]).provider == MARINA  # Al Noor is in the order but not in network
    assert custom.route(GASTRO, "basic", [CORNICHE, MARINA]) == providers.Route(AL_NOOR, insurance.OUT_OF_NETWORK)


def test_the_demo_seed_routes_are_unchanged_for_every_scheme(shipped):
    """ADR-054: the named patient's story (Al Noor declines, the automation asks Corniche) and the second case (handed to
    Corniche) do not move whatever scheme a seeded case carries; Corniche accepts every scheme."""
    for scheme in (None, *insurance.SCHEME_CODES):
        assert shipped.route(GASTRO, scheme, [AL_NOOR]).provider == "Corniche Gastro Unit"
        assert shipped.route(GASTRO, scheme, [AL_NOOR]).network in (None, insurance.IN_NETWORK)
        for pathway, specialty_first in (("cervical_hpv", "Corniche Women's Health Unit"), ("colorectal_fit", CORNICHE)):
            assert shipped.route(pathway, scheme, []).provider == specialty_first or scheme == "unknown"


def test_the_shipped_networks_show_both_outcomes_and_never_empty_a_gastro_scheme(shipped):
    for scheme in insurance.SCHEMES:
        assert len(shipped.in_network_providers(GASTRO, scheme)) >= 2  # the bowel demo never lands out of network
    assert shipped.in_network_providers("chronic_conditions", "basic") == ()  # a Basic family-medicine case is out of network
    assert shipped.route("chronic_conditions", "basic", []).network == insurance.OUT_OF_NETWORK


@pytest.mark.parametrize("edit,needle", [
    (lambda r: r["referral"]["networks"].update({"Nowhere Unit": ["thiqa"]}), "no reroute order"),
    (lambda r: r["referral"]["networks"].update({CORNICHE: ["gold"]}), "drawn from"),
    (lambda r: r["referral"]["networks"].update({CORNICHE: "thiqa"}), "drawn from"),
    (lambda r: r["referral"].update(networks=["thiqa"]), "must map"),
])
def test_a_bad_network_is_refused_at_load(edit, needle):
    raw = yaml.safe_load(open(app_settings.providers_path, encoding="utf-8"))
    raw = copy.deepcopy(raw)
    edit(raw)
    with pytest.raises(providers.ProvidersError, match=needle):
        providers.parse_providers(raw)


def test_a_file_without_networks_still_loads_and_accepts_every_scheme(shipped):
    raw = yaml.safe_load(open(app_settings.providers_path, encoding="utf-8"))
    raw["referral"].pop("networks")
    p = providers.parse_providers(raw)
    assert p.networks == {} and p.network_of(MARINA, "enhanced") == insurance.IN_NETWORK
    assert p.route(GASTRO, "basic", []).provider == CORNICHE


# ---------------------------------------------------------------------------------------------------------------------
# T-A103 the scheme on the genesis event
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["thiqa", "enhanced", "basic", "unknown"])
def test_the_scheme_is_stored_on_the_genesis_event(fresh, scheme):
    cid = new_case(insurance_scheme=scheme)
    assert log(cid)[0].payload["insurance_scheme"] == scheme


def test_a_case_without_a_scheme_has_the_genesis_it_always_had(fresh):
    cid = new_case()
    assert "insurance_scheme" not in log(cid)[0].payload
    row = client.get(f"/cases/{cid}", headers=login("coordinator", "Maryam Al Suwaidi")).json()
    assert row["insurance"] is None


@pytest.mark.parametrize("bad", ["gold", "THIQA", "", 5])
def test_a_scheme_outside_the_list_is_refused_and_nothing_is_written(fresh, bad):
    r = client.post("/cases", json={"patient_ref": "Patient/1", "insurance_scheme": bad}, headers=login("coordinator", "Maryam Al Suwaidi"))
    assert r.status_code == 422
    with pytest.raises(payloads.PayloadSchemaError):
        payloads.validate("case_created", {"insurance_scheme": bad})


def test_the_scheme_never_holds_a_case(fresh):
    """Every scheme, `unknown` included, takes the same path to the same states as no scheme."""
    for scheme in (None, *insurance.SCHEME_CODES):
        cid = case_with(scheme)
        assert model(cid).state == "patient_acknowledged" and model(cid).pending_handoff["to_provider"] == CORNICHE


# ---------------------------------------------------------------------------------------------------------------------
# T-A104 the referral request records the network; reroute after rejection (the API and the automation)
# ---------------------------------------------------------------------------------------------------------------------


def test_a_persons_referral_is_stamped_with_the_network_by_the_server(fresh):
    cid = case_with("enhanced", referral_to=MARINA)  # Marina Digestive Health does not accept Enhanced
    assert requests(cid) == [(MARINA, "out_of_network", None)]
    assert model(cid).pending_handoff["to_provider"] == MARINA  # not blocked
    cid = case_with("enhanced", referral_to=AL_NOOR)
    assert requests(cid) == [(AL_NOOR, "in_network", None)]


def test_a_network_a_client_sends_is_replaced_by_the_servers(fresh):
    cid = new_case(insurance_scheme="enhanced")
    post(cid, "accept_ownership", login(*LAYLA_LOGIN))
    post(cid, "contact_patient", owner())
    post(cid, "patient_acknowledge", login("patient", "pat", patient_ref="Patient/1"))
    post(cid, "handoff_requested", owner(), {"to_provider": MARINA, "network": "in_network"})
    assert requests(cid) == [(MARINA, "out_of_network", None)]
    no_scheme = new_case()
    post(no_scheme, "accept_ownership", login(*LAYLA_LOGIN))
    post(no_scheme, "contact_patient", owner())
    post(no_scheme, "patient_acknowledge", login("patient", "pat", patient_ref="Patient/1"))
    post(no_scheme, "handoff_requested", owner(), {"to_provider": MARINA, "network": "in_network"})
    assert "network" not in log(no_scheme)[-1].payload  # no scheme: the event is what it always was


@pytest.mark.parametrize("scheme", [None, "unknown"])
def test_without_a_known_scheme_the_log_has_no_network_and_the_reroute_order_is_the_old_one(fresh, scheme):
    cid = case_with(scheme)
    reject_pending(cid)
    scan()
    reject_pending(cid)
    scan()
    assert requests(cid) == [(CORNICHE, None, None), (AL_NOOR, None, True), (MARINA, None, True)]
    assert all("network" not in e.payload for e in log(cid) if e.type == "handoff_requested")


def test_reroute_asks_only_in_network_providers_while_there_are_some(fresh):
    cid = case_with("basic")  # Corniche, Al Noor and Marina all accept Basic
    reject_pending(cid)
    assert scan()["rerouted"] == 1
    assert requests(cid)[-1] == (AL_NOOR, "in_network", True)
    assert log(cid)[-1].actor == AUTOMATION_ACTOR


def test_reroute_goes_out_of_network_when_none_is_left_flagged_and_not_blocked(fresh):
    cid = case_with("enhanced")  # Marina Digestive Health does not accept Enhanced
    reject_pending(cid)
    scan()
    assert requests(cid)[-1] == (AL_NOOR, "in_network", True)
    reject_pending(cid)
    out = scan()
    assert out["rerouted"] == 1 and "reroute_exhausted" not in out
    assert requests(cid)[-1] == (MARINA, "out_of_network", True)
    assert model(cid).pending_handoff["to_provider"] == MARINA  # asked: the referral goes ahead
    reject_pending(cid)
    assert scan()["reroute_exhausted"] == 1  # only an empty list reaches a person, as before


def test_reroute_is_idempotent_with_a_scheme(fresh):
    cid = case_with("enhanced")
    reject_pending(cid)
    scan()
    assert scan().get("rerouted") is None
    assert len(requests(cid)) == 2


# ---------------------------------------------------------------------------------------------------------------------
# T-A105 the read model, the coordinator and the patient
# ---------------------------------------------------------------------------------------------------------------------


def coordinator():
    return login("coordinator", "Maryam Al Suwaidi")


def test_the_case_page_says_why_this_provider_in_plain_words(fresh):
    cid = case_with("basic", referral_to=AL_NOOR)
    ins = client.get(f"/cases/{cid}", headers=coordinator()).json()["insurance"]
    assert ins["scheme"] == "basic" and ins["label"] == "Basic" and ins["synthetic"] is True
    assert ins["providers"] == [{"name": n, "network": "in_network"} for n in (CORNICHE, AL_NOOR, MARINA)]
    assert ins["referral"] == {"provider": AL_NOOR, "network": "in_network", "out_of_network": False, "auto": False,
                               "reason": "This unit accepts the patient's Basic plan."}
    reject_pending(cid)
    scan()
    ref = client.get(f"/cases/{cid}", headers=coordinator()).json()["insurance"]["referral"]
    assert ref["provider"] == CORNICHE and ref["auto"] is True  # Corniche was not asked yet
    assert ref["reason"] == "Chosen because it accepts the patient's Basic plan and is the first such unit in the programme's referral order."


def test_an_out_of_network_referral_is_flagged_with_a_plain_reason_on_the_row_and_the_page(fresh):
    cid = case_with("enhanced", referral_to=AL_NOOR)
    reject_pending(cid)
    scan()  # to Corniche (in network)
    reject_pending(cid)
    scan()  # nothing in network is left: Marina Digestive Health, out of network
    page = client.get(f"/cases/{cid}", headers=coordinator()).json()["insurance"]["referral"]
    assert page["out_of_network"] is True and page["provider"] == MARINA
    assert page["reason"].startswith("Out of network: no remaining unit for this service accepts the patient's Enhanced plan")
    assert "not blocked" in page["reason"]
    row = next(r for r in client.get("/cases", headers=coordinator()).json() if r["case_id"] == cid)
    assert row["insurance"]["referral"]["out_of_network"] is True  # the worklist row carries it for the coordinator


def test_readmodel_insurance_of(shipped):
    from app.events import Event
    from datetime import UTC, datetime

    at = datetime(2026, 1, 5, tzinfo=UTC)
    cid = UUID(int=7)
    genesis = Event(cid, "case_created", at, "system:intake", {"insurance_scheme": "thiqa"})
    assert readmodel.insurance_of([Event(cid, "case_created", at, "system:intake", {})], PATHWAY) is None
    assert readmodel.insurance_of([genesis], PATHWAY)["referral"] is None
    unknown = readmodel.insurance_of([Event(cid, "case_created", at, "system:intake", {"insurance_scheme": "unknown"})], PATHWAY)
    assert unknown["label"] == "Unknown" and {p["network"] for p in unknown["providers"]} == {None}
    req = Event(cid, "handoff_requested", at, "primary_care_owner:x", {"to_provider": CORNICHE, "network": "in_network"})
    assert readmodel.insurance_of([genesis, req], PATHWAY)["referral"]["network"] == "in_network"
    assert insurance.reason("basic", None, True) is None


def test_a_patient_never_sees_the_scheme_the_network_or_the_reason(fresh):
    cid = case_with("enhanced", referral_to=MARINA)
    patient = login("patient", "pat", patient_ref="Patient/1")
    page = client.get(f"/cases/{cid}", headers=patient).json()
    rows = client.get("/cases", headers=patient).json()
    timeline = client.get(f"/cases/{cid}/timeline", headers=patient).json()
    for seen in (page, rows, timeline):
        text = json.dumps(seen).lower()
        for needle in ("insurance", "enhanced", "network", "out of network", "scheme"):
            assert needle not in text, needle


def test_the_scheme_is_in_the_staff_page_for_every_staff_role_that_may_read_the_case(fresh):
    cid = case_with("thiqa", referral_to=CORNICHE)
    for who in (coordinator(), login("supervisor", "Dr. Noura Al Hammadi"), owner()):
        assert client.get(f"/cases/{cid}", headers=who).json()["insurance"]["scheme"] == "thiqa"


# ---------------------------------------------------------------------------------------------------------------------
# T-A106 the agency recommends in network first
# ---------------------------------------------------------------------------------------------------------------------


def test_the_agencys_provider_list_puts_the_schemes_network_first(fresh):
    base = list(agency.config.providers())
    cid = case_with("enhanced")
    assert agency_tools.read_providers(None) == base  # no case: the configured order
    ordered = agency_tools.read_providers(None, case_id=UUID(cid))
    assert ordered[-1] == MARINA and sorted(ordered) == sorted(base)  # Marina does not accept Enhanced: last
    plain = case_with(None)
    assert agency_tools.read_providers(None, case_id=UUID(plain)) == base
    unknown = case_with("unknown")
    assert agency_tools.read_providers(None, case_id=UUID(unknown)) == base
