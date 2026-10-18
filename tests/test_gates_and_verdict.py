"""Human-reference gate, hazard screen, run verdict, and sequence provenance."""

import json
from pathlib import Path

import pytest

from bindi import gates, provenance
from bindi.e2e import EXIT_OK, EXIT_VERDICT_FAIL, case_run
from bindi.provenance import validate_sequence_field
from bindi.verdict import evaluate_run_verdict

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = ROOT / "fixtures" / "immunology_case_PLACEHOLDER.json"
HUMAN_REF_HIT = ROOT / "fixtures" / "human_reference_synthetic.fasta"
HAZARD_REF = ROOT / "fixtures" / "hazard_screen_synthetic.fasta"

# Global BLAST identity ~57% but contains an 8-residue exact human stretch (window rule fires).
WINDOW_ONLY_BINDER = (
    "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"
    "AKQRQISF"
    "YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY"
)
WINDOW_ONLY_HUMAN_REF = (
    "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGLYTHMKALRPDEDRLSPLGGFVESFTVQ"
)


def _clean_human_ref(tmp_path: Path) -> Path:
    path = tmp_path / "clean_human.fasta"
    path.write_text(">unrelated\nACDEFGHIKLMNPQRSTVWYACDEFGHIK\n", encoding="utf-8")
    return path


def _configure_gates(
    monkeypatch,
    tmp_path,
    *,
    human_ref: Path | None = None,
    hazard_ref: Path | None = None,
):
    human = human_ref or _clean_human_ref(tmp_path)
    hazard = hazard_ref or (ROOT / "fixtures" / "hazard_screen_clean.fasta")
    monkeypatch.setenv(gates.REFERENCE_ENV, str(human))
    monkeypatch.setenv(gates.REFERENCE_SOURCE_ENV, "synthetic-test-human-v1")
    monkeypatch.setenv(gates.HAZARD_ENV, str(hazard))
    monkeypatch.setenv(gates.HAZARD_SOURCE_ENV, "synthetic-test-hazard-v1")
    return human, hazard


def _run(tmp_path, monkeypatch, **kwargs):
    human_ref = kwargs.pop("human_ref", None)
    hazard_ref = kwargs.pop("hazard_ref", None)
    _configure_gates(
        monkeypatch, tmp_path, human_ref=human_ref, hazard_ref=hazard_ref
    )
    result = case_run(
        PLACEHOLDER,
        workdir=tmp_path,
        reference_fasta=human_ref,
        hazard_fasta=hazard_ref,
        **kwargs,
    )
    lines = [
        json.loads(line)
        for line in (tmp_path / "case_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return result, lines


def test_sequence_field_rejects_binding_claim_without_design_tool_provenance():
    bad = provenance.fixture_sequence("ACDEFGHIK")
    bad["margin"] = "0.5"
    problems = validate_sequence_field(bad)
    assert problems
    assert any("design_tool" in p for p in problems)


def test_missing_reference_refuses_and_does_not_write_letter(tmp_path, monkeypatch):
    monkeypatch.delenv(gates.REFERENCE_ENV, raising=False)
    monkeypatch.delenv(gates.HAZARD_ENV, raising=False)
    monkeypatch.setenv(gates.HAZARD_ENV, str(ROOT / "fixtures" / "hazard_screen_clean.fasta"))
    result = case_run(PLACEHOLDER, workdir=tmp_path)
    lines = [
        json.loads(line)
        for line in (tmp_path / "case_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert result["human_reference_gate"] == "refuse"
    assert result["exit_code"] == EXIT_OK
    assert result["run_verdict"] == "pass"
    assert result.get("letter") is None
    assert not any(line["stage"] == "letter" and line["status"] == "produced" for line in lines)


def test_human_reference_gate_refuses_missing_reference(tmp_path, monkeypatch):
    monkeypatch.delenv(gates.REFERENCE_ENV, raising=False)
    record = gates.human_reference_gate(
        [{"design": "d1", "sequence": "ACDEFGHIKL"}],
        reference_fasta=tmp_path / "missing.fasta",
    )
    assert record["status"] == "refuse"


def test_human_reference_gate_refuses_on_hit(tmp_path):
    reference = tmp_path / "ref.fasta"
    reference.write_text(">synthetic\nMKTAYIAKQRQISFVKSHFSRQ\n", encoding="utf-8")
    hit = gates.human_reference_gate(
        [{"design": "d1", "sequence": "AKQRQISFVKS"}],
        reference_fasta=reference,
        reference_source="test-ref",
    )
    assert hit["status"] == "refuse"
    row = hit["per_sequence"][0]
    assert row["global_identity"]["verdict"] == "refuse"
    assert hit["reference_sha256"]


def test_human_reference_gate_refuses_on_local_window_below_global_threshold(tmp_path):
    reference = tmp_path / "ref.fasta"
    reference.write_text(f">human\n{WINDOW_ONLY_HUMAN_REF}\n", encoding="utf-8")
    record = gates.human_reference_gate(
        [{"design": "chimera", "sequence": WINDOW_ONLY_BINDER}],
        reference_fasta=reference,
        reference_source="window-test",
    )
    assert record["status"] == "refuse"
    row = record["per_sequence"][0]
    assert row["global_identity"]["verdict"] == "pass"
    assert row["local_window"]["verdict"] == "refuse"
    assert row["refuse_rule"] == "local_window"
    assert row["local_window"]["best_window"]["query_start"]
    assert row["local_window"]["best_window"]["query_end"]


def test_human_reference_gate_passes_clean_under_global_and_window(tmp_path):
    reference = tmp_path / "ref.fasta"
    reference.write_text(">synthetic\nMKTAYIAKQRQISFVKSHFSRQ\n", encoding="utf-8")
    clean = gates.human_reference_gate(
        [{"design": "d1", "sequence": "WWWWWWWWWWWW"}],
        reference_fasta=reference,
        reference_source="test-ref",
    )
    assert clean["status"] == "pass"
    row = clean["per_sequence"][0]
    assert row["global_identity"]["verdict"] == "pass"
    assert row["local_window"]["verdict"] == "pass"


def test_human_reference_gate_passes_clean_sequence(tmp_path):
    reference = tmp_path / "ref.fasta"
    reference.write_text(">synthetic\nMKTAYIAKQRQISFVKSHFSRQ\n", encoding="utf-8")
    clean = gates.human_reference_gate(
        [{"design": "d1", "sequence": "WWWWWWWWWWWW"}],
        reference_fasta=reference,
        reference_source="test-ref",
    )
    assert clean["status"] == "pass"


def test_human_reference_gate_refuses_missing_binary(tmp_path, monkeypatch):
    reference = tmp_path / "ref.fasta"
    reference.write_text(">synthetic\nACDEFGHIK\n", encoding="utf-8")
    monkeypatch.setenv(gates.MMSEQS_ENV, "/nonexistent/mmseqs2-binary")
    record = gates.human_reference_gate(
        [{"design": "d1", "sequence": "ACDEFGHIK"}],
        reference_fasta=reference,
    )
    assert record["status"] == "refuse"
    assert "mmseqs2" in record["reason"].lower()


def test_refused_on_hit_is_a_passing_run(tmp_path, monkeypatch):
    result, lines = _run(tmp_path, monkeypatch, human_ref=HUMAN_REF_HIT)
    assert result["exit_code"] == EXIT_OK
    assert result["run_verdict"] == "pass"
    assert result["human_reference_gate"] == "refuse"
    assert result.get("letter") is None
    by_stage = {line["stage"]: line for line in lines}
    assert by_stage["letter"]["status"] == "blocked"
    assert not (tmp_path / "gallery" / "SYNTH-IMM-001" / "letter.json").exists()


def test_clean_pass_with_both_gates_writes_letter(tmp_path, monkeypatch):
    result, lines = _run(tmp_path, monkeypatch)
    assert result["exit_code"] == EXIT_OK
    assert result["run_verdict"] == "pass"
    assert result["human_reference_gate"] == "pass"
    assert result["hazard_screen"] == "pass"
    letter = json.loads(
        Path(result["letter"]).read_text(encoding="utf-8")
    )
    assert letter["shown_sequences"]
    by_stage = {line["stage"]: line for line in lines}
    assert by_stage["letter"]["status"] == "produced"
    assert "human_reference_gate" in [line["stage"] for line in lines]
    assert "hazard_screen" in [line["stage"] for line in lines]


def test_verdict_fails_when_letter_shown_without_gate_block():
    shown = provenance.fixture_sequence("ACDEFGHIKL")
    trace = [
        {
            "schema": "bindi.case_trace.v1",
            "stage": "letter",
            "status": "produced",
            "shown_sequences": [shown],
        }
    ]
    verdict = evaluate_run_verdict(trace)
    assert verdict["verdict"] == "fail"
    assert "human_reference_gate" in verdict["reason"]


def test_case_run_exits_nonzero_when_verdict_fails(tmp_path, monkeypatch):
    _configure_gates(monkeypatch, tmp_path)
    result, lines = _run(tmp_path, monkeypatch)
    assert result["exit_code"] == EXIT_OK
    leaked = [line for line in lines if line["stage"] not in ("human_reference_gate", "hazard_screen")]
    verdict = evaluate_run_verdict(leaked)
    assert verdict["verdict"] == "fail"
    assert evaluate_run_verdict(lines)["verdict"] == "pass"


def test_hazard_refuse_also_passes_run(tmp_path, monkeypatch):
    hazard = tmp_path / "hazard_hit.fasta"
    hazard.write_text(">motif\nGFTFSSYAMS\n", encoding="utf-8")
    result, _ = _run(tmp_path, monkeypatch, hazard_ref=hazard)
    assert result["exit_code"] == EXIT_OK
    assert result["hazard_screen"] == "refuse"
    assert result["run_verdict"] == "pass"
