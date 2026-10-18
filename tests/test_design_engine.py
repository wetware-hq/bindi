"""Sequence-level design engine, provenance, and repeat-noise measurement."""

import json
from pathlib import Path

import pytest

from bindi import case as case_mod
from bindi import design_engine, gates, provenance
from bindi.e2e import EXIT_OK, case_run
from bindi.provenance import validate_sequence_field

ROOT = Path(__file__).resolve().parents[1]
SIGNED_BENCH = ROOT / "evidence" / "immunology_case_signed_benchmark.v1.json"
EVIDENCE_OUT = ROOT / "evidence" / "sequence_design_repeat_noise.v1.json"


def test_design_tool_sequence_requires_version_and_evidence_level():
    field = provenance.design_tool_sequence(
        "ACDEFGHIK",
        tool="bindi.sequence_designer",
        scoring_function="bindi.design_engine.score_binder_peptide",
        tool_version="1.0.0",
    )
    assert validate_sequence_field(field) == []
    assert field["evidence_level"] == provenance.EVIDENCE_LEVEL_SEQUENCE_ONLY
    assert field["provenance"]["version"] == "1.0.0"


def test_scorer_easy_pair_margin_exceeds_hard_on_average():
    hard = design_engine.benchmark_pair_from_case(
        case_mod.load_case(SIGNED_BENCH), "PAIR-HARD"
    )
    easy = design_engine.benchmark_pair_from_case(
        case_mod.load_case(SIGNED_BENCH), "PAIR-EASY"
    )
    hard_m, hard_w = design_engine.pair_peptides(hard)
    easy_m, easy_w = design_engine.pair_peptides(easy)
    hard_margin = design_engine.top_margin_for_pair(hard_m, hard_w, seed=7)["top_margin"]
    easy_margin = design_engine.top_margin_for_pair(easy_m, easy_w, seed=7)["top_margin"]
    assert abs(easy_margin) > abs(hard_margin)


def test_repeat_noise_report_has_required_statistics():
    case = case_mod.load_case(SIGNED_BENCH)
    report = design_engine.run_repeat_noise_report(case, repeats=5, seed_start=2000)
    for pair_id in ("PAIR-HARD", "PAIR-EASY"):
        dist = report["pairs"][pair_id]["distribution"]
        assert dist["n"] == 5
        assert "mean" in dist and "std" in dist
        assert len(dist["per_seed_margins"]) == 5
    easy_eval = report["pairs"]["PAIR-EASY"]["margin_vs_wild_type"]
    assert "threshold_pending_signoff" in easy_eval
    assert "separates_at_least_threshold" in easy_eval
    hard_eval = report["pairs"]["PAIR-HARD"]["margin_vs_wild_type"]
    assert "pass" not in json.dumps(hard_eval).lower()


def test_committed_repeat_noise_evidence_matches_generator():
    case = case_mod.load_case(SIGNED_BENCH)
    expected = design_engine.run_repeat_noise_report(case, repeats=20, seed_start=1000)
    on_disk = json.loads(EVIDENCE_OUT.read_text(encoding="utf-8"))
    assert on_disk["pairs"]["PAIR-EASY"]["distribution"]["per_seed_margins"] == expected[
        "pairs"
    ]["PAIR-EASY"]["distribution"]["per_seed_margins"]


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
    assert by_stage["design"]["evidence_level"] == design_engine.EVIDENCE_LEVEL
    assert by_stage["margin_wild_type"]["status"] == "stated"
    assert by_stage["margin_wild_type"]["evidence_level"] == design_engine.EVIDENCE_LEVEL
    letter = json.loads(Path(result["letter"]).read_text(encoding="utf-8"))
    assert letter["evidence_level"] == design_engine.EVIDENCE_LEVEL
    for entry in letter["shown_sequences"]:
        assert entry["provenance"]["kind"] == "design_tool"
        assert entry["evidence_level"] == design_engine.EVIDENCE_LEVEL
