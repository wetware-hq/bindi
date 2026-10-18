"""Sequence-level design engine, provenance, and repeat-noise measurement."""

import json
from pathlib import Path

from bindi import case as case_mod
from bindi import design_engine, gates, provenance
from bindi.e2e import EXIT_OK, case_run
from bindi.provenance import validate_sequence_field

ROOT = Path(__file__).resolve().parents[1]
SIGNED_BENCH = ROOT / "evidence" / "immunology_case_signed_benchmark.v1.json"
EVIDENCE_V1 = ROOT / "evidence" / "sequence_design_repeat_noise.v1.json"
EVIDENCE_V2 = ROOT / "evidence" / "sequence_design_repeat_noise.v2.json"


def test_design_tool_sequence_requires_version_and_evidence_level():
    field = provenance.design_tool_sequence(
        "ACDEFGHIK",
        tool="bindi.sequence_designer",
        scoring_function=design_engine.SCORER_REGISTRY["blosum_position_v2"]["scoring_function"],
        tool_version=design_engine.TOOL_VERSION,
    )
    assert validate_sequence_field(field) == []
    assert field["evidence_level"] == provenance.EVIDENCE_LEVEL_SEQUENCE_ONLY


def test_class_bucket_v1_reproduces_committed_v1_evidence():
    case = case_mod.load_case(SIGNED_BENCH)
    expected = design_engine.run_repeat_noise_report(
        case, repeats=20, seed_start=1000, scorer_id="class_bucket_v1"
    )
    on_disk = json.loads(EVIDENCE_V1.read_text(encoding="utf-8"))
    assert on_disk.get("scorer_id", "class_bucket_v1") == "class_bucket_v1"
    assert on_disk["pairs"]["PAIR-EASY"]["distribution"]["per_seed_margins"] == expected[
        "pairs"
    ]["PAIR-EASY"]["distribution"]["per_seed_margins"]


def test_v2_hard_pair_margin_nonzero_on_at_least_one_seed():
    case = case_mod.load_case(SIGNED_BENCH)
    hard = design_engine.benchmark_pair_from_case(case, "PAIR-HARD")
    mutant, wild = design_engine.pair_peptides(hard)
    margins = design_engine.repeat_noise_distribution(
        mutant, wild, repeats=20, seed_start=1000, scorer_id="blosum_position_v2"
    )["per_seed_margins"]
    assert any(m != 0.0 for m in margins)


def test_v2_committed_repeat_noise_evidence_matches_generator():
    case = case_mod.load_case(SIGNED_BENCH)
    expected = design_engine.run_repeat_noise_report(
        case, repeats=20, seed_start=1000, scorer_id="blosum_position_v2"
    )
    on_disk = json.loads(EVIDENCE_V2.read_text(encoding="utf-8"))
    assert on_disk["scorer_id"] == "blosum_position_v2"
    assert on_disk["pairs"]["PAIR-EASY"]["distribution"]["per_seed_margins"] == expected[
        "pairs"
    ]["PAIR-EASY"]["distribution"]["per_seed_margins"]


def test_repeat_noise_report_has_required_statistics():
    case = case_mod.load_case(SIGNED_BENCH)
    report = design_engine.run_repeat_noise_report(
        case, repeats=5, seed_start=2000, scorer_id="blosum_position_v2"
    )
    assert report["scorer_id"] == "blosum_position_v2"
    for pair_id in ("PAIR-HARD", "PAIR-EASY"):
        dist = report["pairs"][pair_id]["distribution"]
        assert dist["n"] == 5
        assert "mean" in dist and "std" in dist
        assert len(dist["per_seed_margins"]) == 5
    easy_eval = report["pairs"]["PAIR-EASY"]["margin_vs_wild_type"]
    assert "threshold_pending_signoff" in easy_eval
    assert "separates_at_least_threshold" in easy_eval
    hard_eval = report["pairs"]["PAIR-HARD"]["margin_vs_wild_type"]
    assert "separates_at_least_threshold" not in hard_eval


def _configure_gates(monkeypatch, tmp_path):
    human = tmp_path / "clean_human.fasta"
    human.write_text(">unrelated\nACDEFGHIKLMNPQRSTVWYACDEFGHIK\n", encoding="utf-8")
    hazard = ROOT / "fixtures" / "hazard_screen_clean.fasta"
    monkeypatch.setenv(gates.REFERENCE_ENV, str(human))
    monkeypatch.setenv(gates.REFERENCE_SOURCE_ENV, "synthetic-test-human-v1")
    monkeypatch.setenv(gates.HAZARD_ENV, str(hazard))
    monkeypatch.setenv(gates.HAZARD_SOURCE_ENV, "synthetic-test-hazard-v1")


def test_sequence_design_case_run_emits_provenance_and_margin(tmp_path, monkeypatch):
    _configure_gates(monkeypatch, tmp_path)
    result = case_run(
        SIGNED_BENCH,
        workdir=tmp_path,
        sequence_design=True,
        design_seed=99,
        design_scorer_id="blosum_position_v2",
        reference_fasta=tmp_path / "clean_human.fasta",
        hazard_fasta=ROOT / "fixtures" / "hazard_screen_clean.fasta",
    )
    assert result["exit_code"] == EXIT_OK
    assert result["margin_status"] == "stated"
    lines = [
        json.loads(line)
        for line in (tmp_path / "case_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_stage = {line["stage"]: line for line in lines}
    assert by_stage["design"]["status"] == "ok"
    assert by_stage["design"]["scorer_id"] == "blosum_position_v2"
    assert by_stage["design"]["evidence_level"] == design_engine.EVIDENCE_LEVEL
    assert by_stage["margin_wild_type"]["status"] == "stated"
    assert by_stage["margin_wild_type"]["scorer_id"] == "blosum_position_v2"
    letter = json.loads(Path(result["letter"]).read_text(encoding="utf-8"))
    assert letter["evidence_level"] == design_engine.EVIDENCE_LEVEL
