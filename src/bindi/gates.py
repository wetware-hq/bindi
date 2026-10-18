"""Output gates: human-reference homology and hazard screening.

Both gates screen binder output (and case target peptides) with MMseqs2 as the primary
search and BLAST+ as an independent cross-check. They fail closed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .provenance import fixture_sequence, reference_sequence, sequence_text

REFERENCE_ENV = "BINDI_HUMAN_REFERENCE_FASTA"
REFERENCE_SOURCE_ENV = "BINDI_HUMAN_REFERENCE_SOURCE"
HAZARD_ENV = "BINDI_HAZARD_SCREEN_FASTA"
HAZARD_SOURCE_ENV = "BINDI_HAZARD_SCREEN_SOURCE"
MMSEQS_ENV = "BINDI_MMSEQS_CMD"
BLASTP_ENV = "BINDI_BLASTP_CMD"
MAKEBLASTDB_ENV = "BINDI_MAKEBLASTDB_CMD"

# Identity (percent, 0–100) at or above which a human-reference hit refuses output.
# Short binders at or above this identity to a human protein are cross-reactivity risk.
HUMAN_REFERENCE_HIT_IDENTITY_PERCENT = 70

# Hazard motifs use full-length identity: only exact or near-exact matches refuse.
HAZARD_SCREEN_HIT_IDENTITY_PERCENT = 100

DEFAULT_REFERENCE_SOURCE = "unconfigured"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_path(env: str, default: str) -> str:
    return os.environ.get(env, default)


def _write_query_fasta(
    path: Path,
    designs: list[dict[str, Any]],
    target: dict[str, Any] | None,
) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    lines: list[str] = []

    def add(qid: str, sequence: str, **meta: str) -> None:
        queries.append({"query_id": qid, "sequence": sequence.upper(), **meta})
        lines.extend([f">{qid}", sequence.upper(), ""])

    if target:
        mutant = sequence_text(target.get("mutant_peptide")) or target.get("mutant_peptide")
        wild = sequence_text(target.get("wild_type_peptide")) or target.get("wild_type_peptide")
        if isinstance(mutant, str) and mutant:
            add("target_mutant_peptide", mutant, role="target_mutant")
        if isinstance(wild, str) and wild:
            add("target_wild_type_peptide", wild, role="target_wild_type")
    for design in designs:
        seq = design.get("sequence")
        if not seq:
            continue
        qid = str(design.get("design") or f"design_{len(queries)}")
        add(qid, str(seq), role="binder", design=qid)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return queries


def _parse_mmseqs_m8(text: str) -> dict[str, dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        qid, target_id, pident = parts[0], parts[1], parts[2]
        entry = {
            "tool": "mmseqs2",
            "target_id": target_id,
            "identity_percent": _format_percent(pident),
        }
        prev = best.get(qid)
        if prev is None or int(entry["identity_percent"]) > int(prev["identity_percent"]):
            best[qid] = entry
    return best


def _parse_blast_tsv(text: str) -> dict[str, dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        qid, target_id, pident, evalue = parts[0], parts[1], parts[2], parts[3]
        entry = {
            "tool": "blastp",
            "target_id": target_id,
            "identity_percent": _format_percent(pident),
            "evalue": str(evalue),
        }
        prev = best.get(qid)
        if prev is None or int(entry["identity_percent"]) > int(prev["identity_percent"]):
            best[qid] = entry
    return best


def _format_percent(raw: str) -> str:
    try:
        value = float(raw)
    except ValueError:
        return "0"
    if value <= 1.0:
        value *= 100.0
    return str(int(round(value)))


def _hit_above_threshold(hit: dict[str, str] | None, threshold_percent: int) -> bool:
    if not hit:
        return False
    return int(hit["identity_percent"]) >= threshold_percent


def _run_mmseqs(
    reference: Path,
    query_fasta: Path,
    work: Path,
    *,
    threshold_percent: int,
) -> tuple[dict[str, dict[str, str]], str | None]:
    mmseqs = _tool_path(MMSEQS_ENV, "mmseqs")
    if not shutil.which(mmseqs):
        return {}, f"mmseqs2 binary not found ({mmseqs})"
    ref_db = work / "refDB"
    query_db = work / "queryDB"
    result_db = work / "resultDB"
    tmp = work / "mmseqs_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    m8 = work / "mmseqs.m8"
    min_seq_id = str(threshold_percent / 100.0)
    for args in (
        [mmseqs, "createdb", str(reference), str(ref_db)],
        [mmseqs, "createdb", str(query_fasta), str(query_db)],
        [
            mmseqs,
            "search",
            str(query_db),
            str(ref_db),
            str(result_db),
            str(tmp),
            "--min-seq-id",
            min_seq_id,
        ],
        [
            mmseqs,
            "convertalis",
            str(query_db),
            str(ref_db),
            str(result_db),
            str(m8),
            "--format-output",
            "query,target,pident",
        ],
    ):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return {}, f"mmseqs2 failed ({' '.join(args[:3])}): {detail or proc.returncode}"
    return _parse_mmseqs_m8(m8.read_text(encoding="utf-8") if m8.exists() else ""), None


def _run_blast(
    reference: Path, query_fasta: Path, work: Path
) -> tuple[dict[str, dict[str, str]], str | None]:
    blastp = _tool_path(BLASTP_ENV, "blastp")
    makeblastdb = _tool_path(MAKEBLASTDB_ENV, "makeblastdb")
    if not shutil.which(blastp):
        return {}, f"blastp binary not found ({blastp})"
    if not shutil.which(makeblastdb):
        return {}, f"makeblastdb binary not found ({makeblastdb})"
    db_prefix = work / "blast_ref"
    proc = subprocess.run(
        [makeblastdb, "-in", str(reference), "-dbtype", "prot", "-out", str(db_prefix)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return {}, f"makeblastdb failed: {detail or proc.returncode}"
    proc = subprocess.run(
        [
            blastp,
            "-query",
            str(query_fasta),
            "-db",
            str(db_prefix),
            "-outfmt",
            "6 qseqid sseqid pident evalue",
            "-max_target_seqs",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return {}, f"blastp failed: {detail or proc.returncode}"
    return _parse_blast_tsv(proc.stdout), None


def _provenance_for_query(
    meta: dict[str, str],
    *,
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    sequence = meta.get("sequence") or ""
    role = meta.get("role")
    if role == "target_mutant":
        field = (target or {}).get("mutant_peptide")
        if isinstance(field, dict):
            return field
        return reference_sequence(sequence, source="case target mutant peptide")
    if role == "target_wild_type":
        field = (target or {}).get("wild_type_peptide")
        if isinstance(field, dict):
            return field
        return reference_sequence(sequence, source="case target wild-type peptide")
    return fixture_sequence(sequence, source="binder output screened by gate")


def _merge_best_hits(
    queries: list[dict[str, str]],
    mmseqs_hits: dict[str, dict[str, str]],
    blast_hits: dict[str, dict[str, str]],
    *,
    threshold_percent: int,
) -> list[dict[str, Any]]:
    per_sequence: list[dict[str, Any]] = []
    for meta in queries:
        qid = meta["query_id"]
        mm = mmseqs_hits.get(qid)
        bl = blast_hits.get(qid)
        hits = [h for h in (mm, bl) if h]
        best = max(hits, key=lambda h: int(h["identity_percent"])) if hits else None
        per_sequence.append(
            {
                "query_id": qid,
                "sequence": meta.get("sequence"),
                "role": meta.get("role"),
                "design": meta.get("design"),
                "mmseqs2": mm,
                "blastp": bl,
                "best_hit": best,
                "refuse": any(_hit_above_threshold(h, threshold_percent) for h in (mm, bl)),
            }
        )
    return per_sequence


def _with_provenance(
    per_sequence: list[dict[str, Any]], *, target: dict[str, Any] | None
) -> list[dict[str, Any]]:
    for row in per_sequence:
        meta = {
            "sequence": row.get("sequence"),
            "role": row.get("role"),
        }
        row["sequence"] = _provenance_for_query(meta, target=target)
    return per_sequence


def _homology_gate(
    gate_name: str,
    designs: list[dict[str, Any]] | None,
    *,
    target: dict[str, Any] | None,
    reference_fasta: str | Path | None,
    reference_env: str,
    reference_source: str | None,
    reference_source_env: str,
    threshold_percent: int,
    tmp_prefix: str,
) -> dict[str, Any]:
    designs = designs or []
    record: dict[str, Any] = {
        "gate": gate_name,
        "tool": "mmseqs2+blastp",
        "threshold_identity_percent": threshold_percent,
        "reference_path": None,
        "reference_version": None,
        "reference_sha256": None,
        "per_sequence": [],
        "verdict": None,
    }

    reference = reference_fasta or os.environ.get(reference_env)
    if not reference:
        record["status"] = "refuse"
        record["verdict"] = "refuse"
        record["reason"] = f"reference not configured ({reference_env} unset); gate fails closed"
        return record

    path = Path(reference)
    if not path.is_file():
        record["status"] = "refuse"
        record["verdict"] = "refuse"
        record["reason"] = f"reference FASTA not found: {path}"
        record["reference_path"] = str(path)
        return record

    version = reference_source or os.environ.get(reference_source_env) or DEFAULT_REFERENCE_SOURCE
    record["reference_path"] = str(path)
    record["reference_version"] = version
    record["reference_sha256"] = _sha256_file(path)

    with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp_name:
        work = Path(tmp_name)
        query_fasta = work / "queries.fa"
        queries_meta = _write_query_fasta(query_fasta, designs, target)
        if not queries_meta:
            record["status"] = "refuse"
            record["verdict"] = "refuse"
            record["reason"] = "no sequences to screen"
            return record

        mmseqs_hits, mm_err = _run_mmseqs(
            path, query_fasta, work, threshold_percent=threshold_percent
        )
        if mm_err:
            record["status"] = "refuse"
            record["verdict"] = "refuse"
            record["reason"] = mm_err
            return record
        blast_hits, bl_err = _run_blast(path, query_fasta, work)
        if bl_err:
            record["status"] = "refuse"
            record["verdict"] = "refuse"
            record["reason"] = bl_err
            return record

        per_sequence = _with_provenance(
            _merge_best_hits(
                queries_meta,
                mmseqs_hits,
                blast_hits,
                threshold_percent=threshold_percent,
            ),
            target=target,
        )
        record["per_sequence"] = per_sequence

    refusing = [row for row in record["per_sequence"] if row.get("refuse")]
    if refusing:
        record["status"] = "refuse"
        record["verdict"] = "refuse"
        record["reason"] = (
            f"hit at or above {threshold_percent}% identity: "
            + ", ".join(row["query_id"] for row in refusing)
        )
        return record

    record["status"] = "pass"
    record["verdict"] = "pass"
    return record


def human_reference_gate(
    designs: list[dict[str, Any]] | None = None,
    *,
    target: dict[str, Any] | None = None,
    reference_fasta: str | Path | None = None,
    reference_source: str | None = None,
) -> dict[str, Any]:
    return _homology_gate(
        "human_reference",
        designs,
        target=target,
        reference_fasta=reference_fasta,
        reference_env=REFERENCE_ENV,
        reference_source=reference_source,
        reference_source_env=REFERENCE_SOURCE_ENV,
        threshold_percent=HUMAN_REFERENCE_HIT_IDENTITY_PERCENT,
        tmp_prefix="bindi-human-ref-",
    )


def hazard_screen_gate(
    designs: list[dict[str, Any]] | None = None,
    *,
    target: dict[str, Any] | None = None,
    hazard_fasta: str | Path | None = None,
    hazard_source: str | None = None,
) -> dict[str, Any]:
    return _homology_gate(
        "hazard_screen",
        designs,
        target=target,
        reference_fasta=hazard_fasta,
        reference_env=HAZARD_ENV,
        reference_source=hazard_source,
        reference_source_env=HAZARD_SOURCE_ENV,
        threshold_percent=HAZARD_SCREEN_HIT_IDENTITY_PERCENT,
        tmp_prefix="bindi-hazard-",
    )
