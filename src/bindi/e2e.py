"""One command, one case, one trace.

`bindi case-run <case.json>` runs a synthetic clinical-immunology case end to end and
writes a single JSONL trace covering every stage:

    case record -> target extraction -> binder step (fixture-backed) -> wild-type margin
    hook -> human-reference gate -> ward hand-off -> case summary

What is real here: the case record is read and validated, the target is extracted from it,
the fixture-backed binder step and gallery curation really run, and every stage is traced
with a status and an exit code. What is a placeholder: the binder step produces fixture
designs, not designs (no BindCraft, no Modal, no GPU); the margin is a hook that reports
`not_computed`; the human-reference gate reports `unbuilt` unless a reference is supplied;
and the ward hand-off is written to disk but not invoked, because ward has no entry point
for an arbitrary case. Each of those says so in its own trace line.

The design hold is enforced in code, not by convention: `--design` on an unsigned record
raises `case.DesignHold` and the run exits non-zero without calling BindCraft.
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
from .resource import VERSION

TRACE_SCHEMA = "bindi.case_trace.v1"
CLOCK_ENV = "BINDI_CLOCK"
TEST_CLOCK = "1937-01-01T00:00:00Z"

EXIT_OK = 0
EXIT_CASE_ERROR = 1
EXIT_HOLD = 2
EXIT_GATE_FAIL = 3


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


def _ward_handoff_payload(case: dict[str, Any], target: dict[str, Any], designs: list[dict[str, Any]]) -> dict[str, Any]:
    """The facts ward's gates and letter would need. Built, not sent."""
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
            "Fixture-backed run on an unsigned placeholder record; no design run, no margin, "
            "human-reference gate unbuilt, no hazard screen."
        ),
    }


def case_run(
    case_path: str | Path,
    *,
    policy: dict[str, Any] | None = None,
    workdir: Path | None = None,
    trace_path: Path | None = None,
    allow_design: bool = False,
    reference_fasta: str | Path | None = None,
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

    # ---- stage 1: case record -------------------------------------------------
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

    # ---- stage 2: target extraction (the seam) --------------------------------
    target = case_mod.resolve_target(policy or {}, case=case)
    trace.emit(
        "target_resolve",
        "ok",
        source=target["source"],
        target_id=target["target_id"],
        restriction_allele=target["restriction_allele"],
        mutant_peptide=target["mutant_peptide"],
        wild_type_peptide=target["wild_type_peptide"],
        signed=target["signed"],
        note=target["note"],
    )

    # ---- stage 3: binder step (fixture-backed; the hold lives here) -----------
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

    # ---- stage 4: wild-type margin hook ---------------------------------------
    margin_record = margin.score_wild_type_margin(
        target, designs, scorer_spec=margin_scorer_spec
    )
    trace.emit(
        "margin_wild_type",
        margin_record["status"],
        **{k: v for k, v in margin_record.items() if k != "status"},
    )

    # ---- stage 5: human-reference gate ----------------------------------------
    gate_record = gates.human_reference_gate(designs, reference_fasta=reference_fasta)
    trace.emit(
        "human_reference_gate",
        gate_record["status"],
        **{k: v for k, v in gate_record.items() if k != "status"},
    )

    # ---- stage 6: ward hand-off (built, not invoked) --------------------------
    handoff = _ward_handoff_payload(case, target, designs)
    handoff_path = gallery_dir / "ward_handoff.json"
    handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace.emit(
        "ward_handoff",
        "not_invoked",
        payload=str(handoff_path),
        note=(
            "The hand-off record ward's gates and letter would consume is written here, but "
            "ward was not called: ward runs fifteen fixed scenarios (S01-S15) and exposes no "
            "entry point for an arbitrary case, so the ward gates and the letter did NOT run "
            "in this trace."
        ),
    )
    trace.emit(
        "letter",
        "not_produced",
        note=(
            "No letter was produced. Letters come from ward's letters endware, which this run "
            "does not reach."
        ),
    )

    # ---- stage 7: summary ------------------------------------------------------
    blocking = list(hold_reasons)
    exit_code = EXIT_OK
    if gate_record["status"] == "fail":
        exit_code = EXIT_GATE_FAIL
    trace.emit(
        "case_summary",
        "complete_fixture_backed" if exit_code == EXIT_OK else "gate_fail",
        exit_code=exit_code,
        stages=[line["stage"] for line in trace.lines],
        real=[
            "case record read and validated",
            "target extracted from the case record",
            "fixture-backed binder step and gallery",
            "single JSONL trace with per-stage statuses",
        ],
        placeholder=[
            "no design run (no BindCraft, no Modal, no GPU)",
            f"wild-type margin: {margin_record['status']}",
            f"human-reference gate: {gate_record['status']}",
            "ward gates and letter: not invoked",
            "candidates unranked, no binding evidence",
        ],
        design_hold=blocking,
        release=False,
    )
    checksum = trace.checksum()
    return {
        "exit_code": exit_code,
        "trace": str(trace_file),
        "sha256": checksum,
        "gallery": str(gallery_dir),
        "handoff": str(handoff_path),
        "design_hold": blocking,
        "margin_status": margin_record["status"],
        "human_reference_gate": gate_record["status"],
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    from .cli import main as cli_main

    return cli_main(["case-run", *(argv or sys.argv[1:])])
