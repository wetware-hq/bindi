"""BindCraft-on-Modal structural margin scorer (stubbed; live path needs MODAL_TOKEN)."""

import json
import os
from pathlib import Path

import pytest

from bindi import case as case_mod
from bindi import margin, structural_margin

ROOT = Path(__file__).resolve().parents[1]
WET_IMM = ROOT / "evidence" / "immunology_case_WET_IMM_001.v1.json"
SIGNED_BENCH = ROOT / "evidence" / "immunology_case_signed_benchmark.v1.json"


def test_modal_token_missing_fails_closed():
    env = os.environ.copy()
    env.pop(structural_margin.MODAL_TOKEN_ENV, None)
    env.pop("BINDI_BINDCRAFT_MODAL_STUB", None)
    with pytest.raises(structural_margin.ModalTokenMissingError):
        structural_margin.client_from_env(force_stub=False)


def test_stub_repeat_noise_includes_case_pair_and_benchmark_pairs():
    case = case_mod.load_case(WET_IMM)
    report = structural_margin.run_repeat_noise_report(
        case, repeats=5, seed_start=3000, client=structural_margin.StubBindCraftModalClient()
    )
    assert report["scorer_id"] == structural_margin.SCORER_ID
    assert report["wet_record_id"] == "WET-IMM-001"
    assert report["pairs"]["CASE-NEOANTIGEN"]["mutant_peptide"] == "YLMSYNGVV"
    assert report["pairs"]["CASE-NEOANTIGEN"]["wild_type_peptide"] == "YLDSYNGVV"
    assert report["pairs"]["CASE-NEOANTIGEN"]["pair_role"] == "case_neoantigen"
    easy = report["pairs"]["PAIR-EASY"]
    assert easy["mutant_peptide"] == "YLGEFPGDW"
    assert len(easy["distribution"]["per_seed_margins"]) == 5
    prov = easy["provenance_sample"]
    assert prov["seed"] == 3000
    assert prov["trajectory_count"] == structural_margin.DEFAULT_TRAJECTORY_COUNT
    assert prov["image_digest"]
    assert prov["gpu_model"]
    assert prov["driver_version"]


def test_stub_pair_easy_can_establish_threshold_when_spread_allows():
    case = case_mod.load_case(SIGNED_BENCH)
    report = structural_margin.run_repeat_noise_report(
        case, repeats=20, seed_start=1000, client=structural_margin.StubBindCraftModalClient()
    )
    easy_eval = report["pairs"]["PAIR-EASY"]["margin_vs_wild_type"]
    assert easy_eval["threshold_status"] == "established"
    assert report["margin_threshold_status"] == "established"


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


def test_bindcraft_on_modal_is_evaluation_only_tier():
    tiers = structural_margin.TIER
    from bindi import data

    assert tiers == "evaluation_only"
    with pytest.raises(data.LicenceTierError):
        data.assert_deployable(["BindCraft-on-Modal"])


def test_wet_imm_case_validates():
    case = case_mod.load_case(WET_IMM)
    assert case_mod.validate_case(case) == []
    assert case["neoantigen"]["mutant_peptide"]["sequence"] == "YLMSYNGVV"
