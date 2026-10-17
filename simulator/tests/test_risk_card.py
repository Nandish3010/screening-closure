"""D10 (T-D9, T-D12): the risk score card says what it must, and its generated blocks, the SVG and the
JSON are the ones the committed model produced. Byte-for-byte regeneration is in
test_risk_training.py::test_t_d12_committed_artefacts_are_byte_identical_to_a_fresh_run."""

import json
import re
import xml.dom.minidom

from app.risk import report
from simulator.sim import export_training as X

CARD = X.CARD.read_text()
RISK = json.loads((X.RESULTS_DIR / "risk.json").read_text())


def section(title: str) -> str:
    m = re.search(rf"^(#+) {re.escape(title)}", CARD, re.M)
    assert m, f"card has no section {title!r}"
    level = len(m.group(1))
    m = re.search(rf"^#{{{level}}} {re.escape(title)}.*?(?=^#{{1,{level}}} |\Z)", CARD, re.S | re.M)
    assert m, f"card has no section {title!r}"
    return m.group(0)


def test_the_card_states_intended_use_exclusions_and_that_the_data_are_synthetic():
    assert "worklist" in section("1. Intended use") and "only" in section("1. Intended use").lower()
    not_for = section("2. Not for")
    for phrase in ("Closing a case", "Escalating", "care decision", "Patient-facing"):
        assert phrase in not_for, phrase
    assert "not a validated model" in CARD and "synthetic" in section("3. Training data").lower()
    assert "tests/test_risk_boundary.py" in not_for  # the hard rule is tied to the test that enforces it


def test_the_card_has_every_required_section():
    for title in ("1. Intended use", "2. Not for", "3. Training data", "4. Features", "5. Performance",
                  "6. Fairness check by language and channel", "7. Coefficients", "8. Limits and retraining"):
        section(title)
    assert "Retraining procedure" in CARD and "export_training" in CARD


def test_the_features_table_lists_every_feature_the_model_uses():
    features = section("4. Features")
    for name in ("stage", "days_since_result", "prior_escalations", "prior_bounces", "consent_withdrawn",
                 "provider_changed", "contact_valid", "language", "channel", "age_band"):
        assert f"`{name}`" in features, name


def test_the_generated_blocks_in_the_card_are_the_ones_the_committed_results_give():
    blocks = report.card_blocks(RISK)
    assert report.splice(CARD, blocks) == CARD  # splicing the current numbers changes nothing
    assert set(blocks) == set(re.findall(r"<!-- risk:(\w+):begin -->", CARD))
    assert "Tolerance: ECE at or below 0.05" in blocks["performance"]


def test_the_card_reports_the_tolerance_verdict_honestly():
    verdict = "The calibration tolerance is met" if RISK["tolerance"]["met"] else "The calibration tolerance is NOT met"
    assert verdict in CARD
    if not RISK["tolerance"]["met"]:
        assert "must not be read as calibrated" in CARD


def test_the_fairness_table_covers_language_and_channel_with_ece_and_auc():
    fairness = report.card_blocks(RISK)["fairness"]
    for group in ("| Language | ar |", "| Language | en |", "| Language | other |", "| Preferred channel | sms |",
                  "| Preferred channel | whatsapp |", "| Preferred channel | phone |", "| Preferred channel | letter |"):
        assert group in fairness, group
    assert "AUC" in fairness and "ECE" in fairness
    for feature in ("language", "channel"):
        for g in RISK["groups"][feature]:
            assert g["n"] > 0 and g["auc"] is not None and g["ece"] is not None


def test_the_reliability_svg_is_committed_well_formed_and_linked_from_card_and_report():
    svg = (X.RESULTS_DIR / "calibration.svg").read_text()
    assert xml.dom.minidom.parseString(svg).documentElement.tagName == "svg"
    assert svg == report.reliability_svg(RISK["heldout"])  # drawn from the committed numbers
    assert "../results/risk/calibration.svg" in CARD
    assert "calibration.svg" in (X.RESULTS_DIR / "training.md").read_text()
    assert report.calibration_table(RISK["heldout"]) in CARD  # the table form of the plot


def test_the_training_report_has_the_required_tables():
    md = (X.RESULTS_DIR / "training.md").read_text()
    for needle in ("AUC", "Brier score", "ECE", "## Calibration table", "## Coefficients", "| Bin |"):
        assert needle in md, needle
    assert md.count("\n| ") > 50  # the calibration, fairness and coefficient tables are all there
    assert len(RISK["heldout"]["calibration"]) == 10
