"""One command, one case, one trace.

`bindi case-run <case.json>` runs a synthetic clinical-immunology case end to end and
writes a single JSONL trace covering every stage through output gates and a run verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import case as case_mod
from . import gates, margin
from .curate import curate
from .generate import generate
from .provenance import fixture_sequence
from .resource import VERSION
from .trace_validate import validate_trace_record
from .verdict import evaluate_run_verdict

TRACE_SCHEMA = "bindi.case_trace.v1"
CLOCK_ENV = "BINDI_CLOCK"
TEST_CLOCK = "1937-01-01T00:00:00Z"

EXIT_OK = 0
EXIT_CASE_ERROR = 1
EXIT_HOLD = 2
EXIT_VERDICT_FAIL = 3


def _now() -> str:
    if os.environ.get(CLOCK_ENV) == "test":
        return TEST_CLOCK
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Trace:
    """Append-only JSONL trace for one case."""

    def __init__(self, path: Path, case_id: str | None) -> None:
        self.path = path
        self.case_id = case_id
        self.lines: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def emit(self, stage: str, status: str, **fields: Any) -> dict[str, Any]:
        record = {
            "schema": TRACE_SCHEMA,
            "ts": _now(),
            "seq": len(self.lines) + 1,
            "case_id": self.case_id,
            "stage": stage,
            "status": status,
            **fields,
        }
        problems = validate_trace_record(record)
        if problems:
            raise ValueError(
                "trace record failed structural validation at "
                f"{stage}: " + "; ".join(problems)
            )
        self.lines.append(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def _designs_from_gallery(gallery_dir: Path) -> list[dict[str, Any]]:
    manifest_path = gallery_dir / "index.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        {"design": entry.get("design"), "sequence": entry.get("sequence")}
        for entry in manifest.get("entries", [])
    ]


def _shown_sequences_from_designs(designs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        fixture_sequence(
            str(design["sequence"]),
            source="fixtures/sample_ranked.csv (fixture-backed binder step)",
        )
        for design in designs
        if design.get("sequence")
    ]


def _letter_payload(
    case: dict[str, Any],
    target: dict[str, Any],
    designs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "bindi.letter.v1",
        "case_id": case.get("case_id"),
        "shown_sequences": _shown_sequences_from_designs(designs),
        "target_mutant": target.get("mutant_peptide"),
        "target_wild_type": target.get("wild_type_peptide"),
    }


def _ward_handoff_payload(
    case: dict[str, Any], target: dict[str, Any], designs: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema": "bindi.ward_handoff.v1",
        "case_id": case.get("case_id"),
        "synthetic": bool((case.get("data_class") or {}).get("synthetic")),
        "presentation": case.get("presentation"),
        "restriction_allele": target.get("restriction_allele"),
        "gene": target.get("gene"),
        "variant": target.get("variant"),
        "consent": case.get("consent"),
        "data_class": case.get("data_class"),
        "signed": target.get("signed"),
        "candidate_count": len(designs),
        "candidates_ranked": False,
        "candidate_note": (
            "Candidates are fixture rows, not designs, and are unranked. No binding evidence "
            "exists for any of them."
        ),
        "release": False,
        "not_a_release": (
            "Fixture-backed run; both output gates passed before this hand-off was written."
        ),
    }


def _emit_gate(trace: Trace, stage: str, gate_record: dict[str, Any]) -> None:
    trace.emit(
        stage,
        gate_record["status"],
        **{k: v for k, v in gate_record.items() if k != "status"},
    )


def case_run(
    case_path: str | Path,
    *,
    policy: dict[str, Any] | None = None,
    workdir: Path | None = None,
    trace_path: Path | None = None,
    allow_design: bool = False,
    reference_fasta: str | Path | None = None,
    reference_source: str | None = None,
    hazard_fasta: str | Path | None = None,
    hazard_source: str | None = None,
    margin_scorer_spec: str | None = None,
) -> dict[str, Any]:
    """Run one synthetic immunology case end to end. Returns {"exit_code", "trace", ...}."""
    base = Path(workdir or Path.cwd())
    base.mkdir(parents=True, exist_ok=True)
    trace_file = Path(trace_path) if trace_path else base / "case_trace.jsonl"
    trace = Trace(trace_file, None)

    trace.emit(
        "run_start",
        "ok",
        bindi_version=VERSION,
        python=platform.python_version(),
        case_file=str(case_path),
        all_data_synthetic=True,
        note="Synthetic patients only. Not a medical device. For testing only.",
    )

    try:
        case = case_mod.load_case(case_path)
    except case_mod.CaseError as exc:
        trace.emit("case_load", "error", reason=str(exc))
        trace.emit("case_summary", "error", exit_code=EXIT_CASE_ERROR)
        return {"exit_code": EXIT_CASE_ERROR, "trace": str(trace_file), "sha256": trace.checksum()}

    trace.case_id = case["case_id"]
    placeholder = not case_mod.is_signed(case)
    trace.emit(
        "case_load",
        "ok",
        case_id=case["case_id"],
        signed=not placeholder,
        placeholder=placeholder,
        data_class=case.get("data_class"),
        consent=case.get("consent"),
        reason=(case.get("signature") or {}).get("placeholder_reason"),
    )

    target = case_mod.resolve_target(policy or {}, case=case)
    neo = case.get("neoantigen") or {}
    mutant_field = neo.get("mutant_peptide")
    wild_field = neo.get("wild_type_peptide")
    trace.emit(
        "target_resolve",
        "ok",
        source=target["source"],
        target_id=target["target_id"],
        restriction_allele=target["restriction_allele"],
        mutant_peptide=mutant_field,
        wild_type_peptide=wild_field,
        signed=target["signed"],
        note=target["note"],
    )

    hold_reasons = case_mod.design_hold_reasons(target)
    if allow_design:
        trace.emit(
            "design",
            "refused",
            requested="real_design_run",
            hold_reasons=hold_reasons,
            note=(
                "A real BindCraft/Modal design run was requested. The hold is enforced here: "
                "nothing was submitted."
            ),
        )
        trace.emit("case_summary", "refused", exit_code=EXIT_HOLD, hold_reasons=hold_reasons)
        return {
            "exit_code": EXIT_HOLD,
            "trace": str(trace_file),
            "sha256": trace.checksum(),
            "hold_reasons": hold_reasons,
        }

    run_policy = dict(policy or {})
    bindcraft = dict(run_policy.get("bindcraft") or {})
    bindcraft.setdefault("campaign_name", case["case_id"])
    bindcraft.setdefault("project_folder", f"results/{case['case_id']}")
    bindcraft.setdefault("number_of_final_designs", 3)
    bindcraft["target"] = target["target_id"]
    run_policy["bindcraft"] = bindcraft
    run_policy.setdefault("curation", {"top_n": 3})
    run_policy.setdefault("gallery", f"gallery/{case['case_id']}")

    generation = generate(run_policy, workdir=base, dry_run=True)
    trace.emit(
        "design",
        "fixture_backed",
        project_folder=generation["project_folder"],
        campaign_file=generation["campaign_file"],
        designs_are_real=False,
        hold_reasons=hold_reasons,
        note=(
            "No design was performed. The ranked table is bindi's fixture "
            "(fixtures/sample_ranked.csv) copied into place; the sequences are invented and "
            "have no relationship to this case's peptide."
        ),
    )

    curation = curate(run_policy, project_folder=generation["project_folder"], workdir=base)
    gallery_dir = Path(curation["gallery"])
    designs = _designs_from_gallery(gallery_dir)
    trace.emit(
        "curate",
        "ok",
        gallery=str(gallery_dir),
        manifest=curation["manifest"],
        count=curation["count"],
        ranked=False,
        note="Gallery built from fixture rows; rank is suppressed for an unconfirmed metric.",
    )

    margin_record = margin.score_wild_type_margin(
        target, designs, scorer_spec=margin_scorer_spec
    )
    trace.emit(
        "margin_wild_type",
        margin_record["status"],
        **{
            k: v
            for k, v in margin_record.items()
            if k not in {"status", "mutant_peptide", "wild_type_peptide"}
        },
    )

    human_gate = gates.human_reference_gate(
        designs,
        target=target,
        reference_fasta=reference_fasta,
        reference_source=reference_source,
    )
    _emit_gate(trace, "human_reference_gate", human_gate)

    hazard_gate = gates.hazard_screen_gate(
        designs,
        target=target,
        hazard_fasta=hazard_fasta,
        hazard_source=hazard_source,
    )
    _emit_gate(trace, "hazard_screen", hazard_gate)

    gates_cleared = human_gate["status"] == "pass" and hazard_gate["status"] == "pass"
    handoff_path = gallery_dir / "ward_handoff.json"
    letter_path = gallery_dir / "letter.json"

    if gates_cleared:
        handoff = _ward_handoff_payload(case, target, designs)
        handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        trace.emit(
            "ward_handoff",
            "built",
            payload=str(handoff_path),
            note="Hand-off record written after both output gates passed.",
        )
        letter = _letter_payload(case, target, designs)
        letter_path.write_text(json.dumps(letter, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        trace.emit(
            "letter",
            "produced",
            payload=str(letter_path),
            shown_sequences=letter["shown_sequences"],
            note="Shown output emitted only after both gates passed.",
        )
    else:
        trace.emit(
            "ward_handoff",
            "blocked",
            note="Output gates refused; no hand-off was written.",
        )
        trace.emit(
            "letter",
            "blocked",
            note="Output gates refused; no letter or other shown output was written.",
        )

    run_verdict = evaluate_run_verdict(trace.lines)
    exit_code = EXIT_OK if run_verdict["verdict"] == "pass" else EXIT_VERDICT_FAIL
    summary_status = (
        "complete_fixture_backed"
        if gates_cleared and exit_code == EXIT_OK
        else "gates_refused"
        if not gates_cleared and exit_code == EXIT_OK
        else "verdict_fail"
    )
    trace.emit(
        "case_summary",
        summary_status,
        exit_code=exit_code,
        run_verdict=run_verdict["verdict"],
        run_verdict_reason=run_verdict["reason"],
        stages=[line["stage"] for line in trace.lines],
        human_reference_gate=human_gate["status"],
        hazard_screen=hazard_gate["status"],
        design_hold=list(hold_reasons),
        release=False,
    )
    return {
        "exit_code": exit_code,
        "trace": str(trace_file),
        "sha256": trace.checksum(),
        "gallery": str(gallery_dir),
        "handoff": str(handoff_path) if gates_cleared else None,
        "letter": str(letter_path) if gates_cleared else None,
        "design_hold": hold_reasons,
        "margin_status": margin_record["status"],
        "human_reference_gate": human_gate["status"],
        "hazard_screen": hazard_gate["status"],
        "run_verdict": run_verdict["verdict"],
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    from .cli import main as cli_main

    return cli_main(["case-run", *(argv or sys.argv[1:])])
