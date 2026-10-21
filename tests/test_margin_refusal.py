"""The margin stage refuses, and says what is missing.

BindCraft-on-Modal was the only scorer the signed margin threshold rule ever recognised.
It has been removed from the design path (Boltz is the generator), and no Boltz-based
structural margin threshold has been signed off. A green here means: the margin stage
refuses by construction, the refusal names the missing requirement, no sequence-level
scorer can clear it, no code path can return `established`, and no margin number is
invented anywhere. These tests replace the BindCraft-specific ones in the deleted
`tests/test_structural_margin.py`.
"""

from pathlib import Path

from bindi import case as case_mod
from bindi import margin

ROOT = Path(__file__).resolve().parents[1]
WET_IMM = ROOT / "evidence" / "immunology_case_WET_IMM_001.v1.json"


def test_no_structural_scorer_is_recognised():
    assert margin.SIGNED_STRUCTURAL_SCORER_IDS == frozenset(), (
        "a scorer id may only be added here when its margin threshold has been signed off"
    )
    assert not hasattr(margin, "STRUCTURAL_SCORER_ID")


def test_margin_refusal_names_what_is_missing(monkeypatch):
    monkeypatch.delenv(margin.SCORER_ENV, raising=False)
    record = margin.score_wild_type_margin(
        {"mutant_peptide": "YLMSYNGVV", "wild_type_peptide": "YLDSYNGVV"}, []
    )
    assert record["status"] == "not_computed"
    assert "structural scorer whose margin threshold has been signed off" in record["reason"]
    assert "Boltz-based pipeline" in record["reason"]
    assert record["threshold_status"] == "not_established"
    assert record["clears_margin_threshold"] is False
    assert record["threshold_missing"] == margin.MISSING_REQUIREMENT
    assert record["per_design"] == []


def test_no_threshold_field_combination_can_be_established():
    for scorer_id in (None, "blosum_position_v2", "class_bucket_v1", "bindcraft_modal_v1", "anything"):
        for level in (None, "sequence_level_not_structure_validated", "structure_whatever"):
            fields = margin._threshold_fields(scorer_id=scorer_id, evidence_level=level)
            assert fields["threshold_status"] == "not_established"
            assert fields["clears_margin_threshold"] is False


def test_sequence_margin_does_not_clear_threshold():
    record = margin.score_wild_type_margin(
        {"mutant_peptide": "AAA", "wild_type_peptide": "AAB"},
        [{"design": "d1", "sequence": "ACDEFG"}],
        scorer=lambda pep, bind: 1.0,
        scorer_id="blosum_position_v2",
        evidence_level="sequence_level_not_structure_validated",
    )
    assert record["status"] == "stated"
    assert record["threshold_status"] == "not_established"
    assert record["clears_margin_threshold"] is False
    assert record["threshold_scorer_class"] == "sequence_level"


def test_design_hold_requires_established_structural_threshold():
    target = {
        "source": "case_record",
        "signed": True,
        "consent_design_use": True,
    }
    reasons = case_mod.design_hold_reasons(
        target,
        human_reference={"status": "pass"},
        margin={"status": "stated", "threshold_status": "not_established"},
    )
    assert any("structural margin threshold" in r for r in reasons)
    assert any("signed off for the Boltz-based pipeline" in r for r in reasons)


def test_wet_imm_case_validates():
    case = case_mod.load_case(WET_IMM)
    assert case_mod.validate_case(case) == []
    assert case["neoantigen"]["mutant_peptide"]["sequence"] == "YLMSYNGVV"
