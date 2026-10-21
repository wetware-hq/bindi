"""The campaign's case record and the campaign design against the SAME peptide.

A green here means: the NY-ESO-1 case record exists, validates, is grounded in CASE.md,
carries SLLMWITQC, is explicitly UNSIGNED, declares the shared-antigen branch with the
on-target-minus-best-decoy endpoint instead of inventing a wild-type peptide, and leaves
the design hold fully in force. It also pins the targeted negative-design panel in the
design spec, and the fact that the retired placeholder is no longer any campaign's case.
"""

import json
from pathlib import Path

import pytest

from bindi import boltz_hosted, case, margin

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "fixtures" / "immunology_case_NYESO1_A0201.json"
PLACEHOLDER = ROOT / "fixtures" / "immunology_case_PLACEHOLDER.json"
POLICY = ROOT / "campaigns" / "neoantigen_placeholder.json"
SPEC = ROOT / "campaigns" / "nyeso1_a0201_pmhc.yaml"


def _record():
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_the_record_validates_and_carries_the_campaign_peptide():
    record = case.load_case(RECORD)
    neo = record["neoantigen"]
    assert neo["mutant_peptide"]["sequence"] == "SLLMWITQC"
    assert neo["antigen_class"] == "shared_cancer_testis"
    assert record["hla"]["restriction_allele"] == "HLA-A*02:01"
    assert "CASE.md" in neo["mutant_peptide"]["provenance"]["source"]
    assert record["_source"]["path"] == "fleets/NYESO1-A0201/CASE.md"


def test_no_wild_type_peptide_was_invented():
    wild = _record()["neoantigen"]["wild_type_peptide"]
    assert wild["absent"] is True
    assert "sequence" not in wild
    assert "unmutated self sequence" in wild["absent_reason"]


def test_a_fabricated_wild_type_sequence_is_refused_for_a_shared_antigen():
    record = _record()
    record["neoantigen"]["wild_type_peptide"] = {
        "absent": True,
        "absent_reason": "x",
        "sequence": "SLLMWITQA",
    }
    problems = case.validate_case(record)
    assert any("must NOT be set" in p for p in problems)


def test_the_shared_branch_requires_the_panel_endpoint():
    record = _record()
    record.pop("specificity_panel")
    assert any("specificity_panel is required" in p for p in case.validate_case(record))

    record = _record()
    record["specificity_panel"]["self_peptide_in_panel"] = False
    assert any("mandatory member" in p for p in case.validate_case(record))

    record = _record()
    record["specificity_panel"]["endpoint"] = "mutant_minus_wild_type"
    assert any("on_target_minus_best_decoy" in p for p in case.validate_case(record))


def test_existing_private_neoantigen_records_are_unaffected():
    """The new branch is opt-in: records with no antigen_class validate exactly as before."""
    for path in (
        PLACEHOLDER,
        ROOT / "evidence" / "immunology_case_WET_IMM_001.v1.json",
        ROOT / "evidence" / "immunology_case_signed_benchmark.v1.json",
    ):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert "antigen_class" not in document["neoantigen"]
        assert case.validate_case(document) == []


def test_the_record_is_unsigned_and_says_what_signing_would_attest_to():
    record = _record()
    assert record["signature"]["signed"] is False
    assert not case.is_signed(record)
    reason = record["signature"]["placeholder_reason"]
    assert "cannot be supplied by an agent" in reason
    assert record["signature"]["awaiting"]


def test_the_design_hold_is_fully_in_force_for_this_record():
    target = case.resolve_target({}, case=case.load_case(RECORD))
    assert target["margin_endpoint"] == "on_target_minus_best_decoy"
    assert target["wild_type_peptide"] is None, (
        "an explicit absence must reach the margin stage as an absence, not as a dict it "
        "could stringify into a peptide"
    )
    record = margin.score_wild_type_margin(target)
    assert record["status"] == "not_computed", "never 'not_applicable' for a declared endpoint"
    assert record["threshold_status"] == "not_established"
    assert record["clears_margin_threshold"] is False
    assert "population-level safety endpoint" in record["endpoint_is"]
    reasons = case.design_hold_reasons(target, margin=record)
    assert any("not signed" in r for r in reasons)
    assert any("margin is not stated" in r for r in reasons)
    assert any("threshold is not established" in r for r in reasons)
    with pytest.raises(case.DesignHold):
        case.assert_design_allowed(target, margin=record)


def test_the_campaign_policy_points_at_the_new_record_and_the_placeholder_is_retired():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy["case"] == "fixtures/immunology_case_NYESO1_A0201.json"
    assert "_RETIRED" in json.loads(PLACEHOLDER.read_text(encoding="utf-8"))


def test_the_targeted_negative_design_panel_is_wired_into_the_spec():
    spec = boltz_hosted.load_design_spec(SPEC)
    chains = {e["chain_ids"][0]: e for e in spec["target"]["entities"]}
    panel_a = chains["A"]["non_binding_residues"]
    expected = (
        list(range(56, 62))
        + list(range(76, 84))
        + list(range(137, 149))
        + list(range(166, 180))
        + list(range(180, 275))
    )
    assert panel_a == expected, "the TARGETED panel, 0-based, from nonbinding-panel-2026-10-20.md"
    assert len(panel_a) == 135
    assert chains["B"]["non_binding_residues"] == list(range(99)), "all of B2M, every panel"
    assert "non_binding_residues" not in chains["C"], "the peptide is what the binder reads"
    # The restriction triad stays contactable in the targeted panel (0-based 64, 68, 154).
    assert not {64, 68, 154} & set(panel_a)


def test_the_panel_caveats_are_recorded_next_to_the_panel():
    text = SPEC.read_text(encoding="utf-8")
    assert "JUDGEMENT, not verified" in text
    assert "STILL OUTSTANDING" in text
    assert "pending BIO Physician residue-level sign-off" in text
    record = _record()
    assert record["specificity_panel"]["negative_design_residue_panel"] == "targeted"


def test_the_cropping_question_is_written_up_and_the_construct_is_unchanged():
    spec = boltz_hosted.load_design_spec(SPEC)
    chains = {e["chain_ids"][0]: len(e["value"]) for e in spec["target"]["entities"]}
    assert chains == {"A": 275, "B": 99, "C": 9}, "the construct must NOT be cropped"
    proposal = (ROOT / "docs" / "PROPOSAL-ipsae-structural-margin.md").read_text(
        encoding="utf-8"
    )
    assert "NOT an engineering decision: cropping the target construct" in proposal
    assert "already under 512 as shipped" in " ".join(proposal.split())


def test_the_trace_accepts_only_an_EXPLICIT_absence_never_a_missing_peptide():
    from bindi import trace_validate as tv

    base = {"schema": tv.TRACE_SCHEMA}
    ok = dict(base, wild_type_peptide={"absent": True, "absent_reason": "no variant exists"})
    assert tv.validate_trace_record(ok) == []
    for bad in (
        {"absent": True},
        {"absent": True, "absent_reason": "  "},
        {"absent": True, "absent_reason": "x", "sequence": "SLLMWITQC"},
        None,
        {"sequence": ""},
    ):
        assert tv.validate_trace_record(dict(base, wild_type_peptide=bad)), (
            f"an absence must be explicit and carry no sequence; accepted {bad!r}"
        )


def test_a_real_case_run_on_this_record_still_refuses_the_margin(tmp_path, monkeypatch):
    from bindi.e2e import case_run

    monkeypatch.chdir(ROOT)
    case_run(str(RECORD), workdir=str(tmp_path))
    records = [
        json.loads(line)
        for line in (tmp_path / "case_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stage = [r for r in records if r["stage"] == "margin_wild_type"][0]
    assert stage["status"] == "not_computed"
    assert stage["threshold_status"] == "not_established"
    assert stage["threshold_reason"] == margin.THRESHOLD_NOT_ESTABLISHED_REASON
    assert stage["margin_endpoint"] == "on_target_minus_best_decoy"
