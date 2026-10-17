import warnings

import pytest
import yaml

from simulator.sim import DEFAULT_PARAMS
from simulator.sim.params import AssumedParamsWarning, ModelParams, ParamError, load_params, parse_params


def test_rejects_key_missing_source():
    with pytest.raises(ParamError, match="missing source"):
        parse_params(yaml.safe_load("a:\n  value: 1\n  status: assumed\n"))


def test_rejects_key_missing_status_or_value():
    with pytest.raises(ParamError, match="missing status"):
        parse_params({"a": {"value": 1, "source": "x"}})
    with pytest.raises(ParamError, match="missing value"):
        parse_params({"a": {"source": "x", "status": "assumed"}})


def test_rejects_empty_source_and_null_value_and_stray_children():
    with pytest.raises(ParamError, match="empty"):
        parse_params({"a": {"value": 1, "source": "  ", "status": "assumed"}})
    with pytest.raises(ParamError, match="null"):
        parse_params({"a": {"value": None, "source": "x", "status": "assumed"}})
    with pytest.raises(ParamError, match="mixes"):
        parse_params({"a": {"value": 1, "source": "x", "status": "assumed", "child": {}}})


def test_notes_are_allowed_and_nested_groups_flatten(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("g:\n  h: {value: [1, 2], source: s, status: sourced, notes: n}\n")
    p = load_params(f, warn=False)
    assert p.get("g.h") == [1, 2] and p.param("g.h").notes == "n"


def test_default_params_load_and_warn_on_assumed():
    with pytest.warns(AssumedParamsWarning, match="assumed"):
        p = load_params(DEFAULT_PARAMS)
    assert p.assumed()  # placeholders still exist
    ModelParams.from_params(p)  # every key the simulator needs is present


def test_layering_overrides_later_files(tmp_path):
    f = tmp_path / "over.yaml"
    f.write_text("referral_rejection_rate: {value: 0.2, source: test, status: sourced}\n")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = load_params(DEFAULT_PARAMS, f)
    assert p.float("referral_rejection_rate") == 0.2
    assert p.param("referral_rejection_rate").status == "sourced"
    assert p.hash() != load_params(DEFAULT_PARAMS, warn=False).hash()


def test_missing_required_key_is_named(tmp_path):
    f = tmp_path / "small.yaml"
    f.write_text("x: {value: 1, source: s, status: assumed}\n")
    with pytest.raises(ParamError, match="acknowledgement_delay_days|missing parameter"):
        ModelParams.from_params(load_params(f, warn=False))


def test_every_default_leaf_has_provenance():
    p = load_params(DEFAULT_PARAMS, warn=False)
    for row in p.rows():
        assert row.source and row.status


def test_pathway_yaml_is_the_single_source_for_the_confirmatory_sla(ctx):
    """The DoH 31-working-day KPI lives in the pathway YAML; the sourced parameter must agree with it."""
    assert ctx.pathway.slas_working_days["complete_confirmatory"] == ctx.mp.followup_target_working_days
