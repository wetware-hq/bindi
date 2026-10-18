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

# Chief Scientist (clinical): signed-off homology gate thresholds for bindi.
CHIEF_SCIENTIST = "Chief Scientist"

# Independent global trigger: refuse when the reported alignment identity is at or above this
# percent. Chief Scientist ruling: kept alongside the window rule; either may refuse.
HOMOLOGY_GATE_GLOBAL_IDENTITY_PERCENT = 70

# Refuse on any local alignment window of at least this many residues. Chief Scientist
# ruling: class-I-sized span; catches self-epitope / motif leakage global identity misses.
HOMOLOGY_GATE_WINDOW_MIN_LENGTH = 8

# Chief Scientist ruling: refuse when any WINDOW_MIN_LENGTH window is at or above this
# percent identity (80% or better).
HOMOLOGY_GATE_WINDOW_MIN_IDENTITY_PERCENT = 80

# MMseqs2 search floor so alignments below the global threshold are still reported for the
# window rule. Chief Scientist ruling: must stay below the window identity threshold.
HOMOLOGY_GATE_SEARCH_MIN_SEQ_ID_PERCENT = 30

HUMAN_REFERENCE_HIT_IDENTITY_PERCENT = HOMOLOGY_GATE_GLOBAL_IDENTITY_PERCENT
HUMAN_REFERENCE_WINDOW_LENGTH = HOMOLOGY_GATE_WINDOW_MIN_LENGTH
HUMAN_REFERENCE_WINDOW_IDENTITY_PERCENT = HOMOLOGY_GATE_WINDOW_MIN_IDENTITY_PERCENT
HUMAN_REFERENCE_SEARCH_MIN_SEQ_ID_PERCENT = HOMOLOGY_GATE_SEARCH_MIN_SEQ_ID_PERCENT

HAZARD_SCREEN_HIT_IDENTITY_PERCENT = HOMOLOGY_GATE_GLOBAL_IDENTITY_PERCENT
HAZARD_SCREEN_WINDOW_LENGTH = HOMOLOGY_GATE_WINDOW_MIN_LENGTH
HAZARD_SCREEN_WINDOW_IDENTITY_PERCENT = HOMOLOGY_GATE_WINDOW_MIN_IDENTITY_PERCENT

HAZARD_SCREEN_METHOD = "homology_based"
HAZARD_SCREEN_METHOD_LIMITS = (
    "Homology to a pinned hazard reference FASTA only (MMseqs2 + BLAST+). Not structural, "
    "not immunogenicity, not toxicity in cell or in vivo, and not a complete hazard "
    "inventory. A pass means no hit at the Chief Scientist thresholds in this reference; "
    "it does not mean the design is safe to show or release."
)

DEFAULT_REFERENCE_SOURCE = "unconfigured"

MMSEQS_ALIGN_FORMAT = (
    "query,target,pident,evalue,qstart,qend,tstart,tend,qaln,taln"
)
BLAST_ALIGN_FORMAT = (
    "6 qseqid sseqid pident evalue qstart qend sstart send qseq sseq"
)


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


def _format_percent(raw: str) -> str:
    try:
        value = float(raw)
    except ValueError:
        return "0"
    if value <= 1.0:
        value *= 100.0
    return str(int(round(value)))


def _format_evalue(raw: str) -> str:
    return str(raw).strip() if raw is not None else ""


def _trace_hit(hit: dict[str, str] | None) -> dict[str, str] | None:
    """Hit fields written to the trace for human review (coordinates + E-value)."""
    if not hit:
        return None
    return {
        "tool": hit["tool"],
        "target_id": hit.get("target_id", ""),
        "identity_percent": hit.get("identity_percent", "0"),
        "evalue": hit.get("evalue", ""),
        "query_start": hit.get("qstart", ""),
        "query_end": hit.get("qend", ""),
        "subject_start": hit.get("tstart", ""),
        "subject_end": hit.get("tend", ""),
    }


def _subject_span_for_query_window(
    hit: dict[str, str],
    query_start: int,
    query_end: int,
) -> tuple[str, str]:
    qaln = hit.get("qaln") or ""
    taln = hit.get("taln") or ""
    subject_positions: list[int] = []
    t_pos = int(hit.get("tstart") or "1")
    tend = int(hit.get("tend") or "1")
    t_step = 1 if tend >= t_pos else -1
    q_idx = 0
    for qc, tc in zip(qaln, taln):
        if qc != "-":
            q_idx += 1
            if query_start <= q_idx <= query_end and tc != "-":
                subject_positions.append(t_pos)
        if tc != "-":
            t_pos += t_step
    if not subject_positions:
        return hit.get("tstart", ""), hit.get("tend", "")
    return str(min(subject_positions)), str(max(subject_positions))


def _best_window_in_alignment(
    qaln: str,
    taln: str,
    *,
    window_length: int,
    hit: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Best identity in any query window from tool-reported local alignment strings."""
    if not qaln or not taln or len(qaln) != len(taln):
        return None
    matches: list[int] = []
    query_index = 0
    for qc, tc in zip(qaln, taln):
        if qc == "-":
            continue
        query_index += 1
        matches.append(1 if qc == tc else 0)
    if len(matches) < window_length:
        return None
    best_identity = -1
    best_start = 0
    for start in range(0, len(matches) - window_length + 1):
        window = matches[start : start + window_length]
        identity = int(round(100 * sum(window) / window_length))
        if identity > best_identity:
            best_identity = identity
            best_start = start
    qs = best_start + 1
    qe = best_start + window_length
    window = {
        "identity_percent": str(best_identity),
        "query_start": str(qs),
        "query_end": str(qe),
        "window_length": str(window_length),
    }
    if hit:
        subj_start, subj_end = _subject_span_for_query_window(hit, qs, qe)
        window["subject_start"] = subj_start
        window["subject_end"] = subj_end
        window["evalue"] = hit.get("evalue", "")
    return window


def _window_from_hit(
    hit: dict[str, str] | None,
    *,
    window_length: int,
) -> dict[str, str] | None:
    if not hit:
        return None
    qaln = hit.get("qaln") or ""
    taln = hit.get("taln") or hit.get("sseq") or ""
    if qaln and taln:
        window = _best_window_in_alignment(
            qaln, taln, window_length=window_length, hit=hit
        )
        if window:
            window = dict(window)
            window["tool"] = hit["tool"]
            window["target_id"] = hit.get("target_id", "")
            return window
    return None


def _parse_mmseqs_alignments(text: str) -> dict[str, list[dict[str, str]]]:
    by_query: dict[str, list[dict[str, str]]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        qid, target_id, pident, evalue = parts[0], parts[1], parts[2], parts[3]
        qstart, qend, tstart, tend = parts[4], parts[5], parts[6], parts[7]
        qaln, taln = parts[8], parts[9]
        entry = {
            "tool": "mmseqs2",
            "target_id": target_id,
            "identity_percent": _format_percent(pident),
            "evalue": _format_evalue(evalue),
            "qstart": qstart,
            "qend": qend,
            "tstart": tstart,
            "tend": tend,
            "qaln": qaln,
            "taln": taln,
        }
        by_query.setdefault(qid, []).append(entry)
    return by_query


def _parse_blast_alignments(text: str) -> dict[str, list[dict[str, str]]]:
    by_query: dict[str, list[dict[str, str]]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        qid, target_id, pident, evalue = parts[0], parts[1], parts[2], parts[3]
        qstart, qend, sstart, send = parts[4], parts[5], parts[6], parts[7]
        qseq, sseq = parts[8], parts[9]
        entry = {
            "tool": "blastp",
            "target_id": target_id,
            "identity_percent": _format_percent(pident),
            "evalue": _format_evalue(evalue),
            "qstart": qstart,
            "qend": qend,
            "tstart": sstart,
            "tend": send,
            "qaln": qseq,
            "taln": sseq,
        }
        by_query.setdefault(qid, []).append(entry)
    return by_query


def _best_global_hit(hits: list[dict[str, str]] | None) -> dict[str, str] | None:
    if not hits:
        return None
    return max(hits, key=lambda h: int(h["identity_percent"]))


def _global_verdict(
    mm: dict[str, str] | None,
    bl: dict[str, str] | None,
    *,
    threshold_percent: int,
) -> dict[str, Any]:
    mm_refuse = mm is not None and int(mm["identity_percent"]) >= threshold_percent
    bl_refuse = bl is not None and int(bl["identity_percent"]) >= threshold_percent
    verdict = "refuse" if (mm_refuse or bl_refuse) else "pass"
    return {
        "verdict": verdict,
        "threshold_identity_percent": threshold_percent,
        "mmseqs2": _trace_hit(mm),
        "blastp": _trace_hit(bl),
    }


def _window_verdict(
    mm: dict[str, str] | None,
    bl: dict[str, str] | None,
    *,
    window_length: int,
    window_identity_percent: int,
) -> dict[str, Any]:
    mm_window = _window_from_hit(mm, window_length=window_length)
    bl_window = _window_from_hit(bl, window_length=window_length)
    windows = [w for w in (mm_window, bl_window) if w]
    best = (
        max(windows, key=lambda w: int(w["identity_percent"]))
        if windows
        else None
    )
    mm_refuse = (
        mm_window is not None
        and int(mm_window["identity_percent"]) >= window_identity_percent
    )
    bl_refuse = (
        bl_window is not None
        and int(bl_window["identity_percent"]) >= window_identity_percent
    )
    verdict = "refuse" if (mm_refuse or bl_refuse) else "pass"
    def _trace_window(window: dict[str, str] | None) -> dict[str, str] | None:
        if not window:
            return None
        return {
            "tool": window.get("tool", ""),
            "target_id": window.get("target_id", ""),
            "identity_percent": window.get("identity_percent", ""),
            "evalue": window.get("evalue", ""),
            "query_start": window.get("query_start", ""),
            "query_end": window.get("query_end", ""),
            "subject_start": window.get("subject_start", ""),
            "subject_end": window.get("subject_end", ""),
            "window_length": window.get("window_length", str(window_length)),
        }

    return {
        "verdict": verdict,
        "window_length": window_length,
        "window_identity_percent_threshold": window_identity_percent,
        "mmseqs2": _trace_window(mm_window),
        "blastp": _trace_window(bl_window),
        "best_window": _trace_window(best),
    }


def _run_mmseqs(
    reference: Path,
    query_fasta: Path,
    work: Path,
    *,
    search_min_seq_id_percent: int,
) -> tuple[dict[str, list[dict[str, str]]], str | None]:
    mmseqs = _tool_path(MMSEQS_ENV, "mmseqs")
    if not shutil.which(mmseqs):
        return {}, f"mmseqs2 binary not found ({mmseqs})"
    ref_db = work / "refDB"
    query_db = work / "queryDB"
    result_db = work / "resultDB"
    tmp = work / "mmseqs_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    m8 = work / "mmseqs.m8"
    min_seq_id = str(search_min_seq_id_percent / 100.0)
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
            "-a",
        ],
        [
            mmseqs,
            "convertalis",
            str(query_db),
            str(ref_db),
            str(result_db),
            str(m8),
            "--format-output",
            MMSEQS_ALIGN_FORMAT,
        ],
    ):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return {}, f"mmseqs2 failed ({' '.join(args[:3])}): {detail or proc.returncode}"
    return _parse_mmseqs_alignments(m8.read_text(encoding="utf-8") if m8.exists() else ""), None


def _run_blast(
    reference: Path, query_fasta: Path, work: Path
) -> tuple[dict[str, list[dict[str, str]]], str | None]:
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
            BLAST_ALIGN_FORMAT,
            "-max_target_seqs",
            "5",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return {}, f"blastp failed: {detail or proc.returncode}"
    return _parse_blast_alignments(proc.stdout), None


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


def _merge_rules(
    queries: list[dict[str, str]],
    mmseqs_hits: dict[str, list[dict[str, str]]],
    blast_hits: dict[str, list[dict[str, str]]],
    *,
    global_threshold_percent: int,
    window_length: int,
    window_identity_percent: int,
) -> list[dict[str, Any]]:
    per_sequence: list[dict[str, Any]] = []
    for meta in queries:
        qid = meta["query_id"]
        mm = _best_global_hit(mmseqs_hits.get(qid))
        bl = _best_global_hit(blast_hits.get(qid))
        global_rule = _global_verdict(mm, bl, threshold_percent=global_threshold_percent)
        window_rule = _window_verdict(
            mm,
            bl,
            window_length=window_length,
            window_identity_percent=window_identity_percent,
        )
        refuse = global_rule["verdict"] == "refuse" or window_rule["verdict"] == "refuse"
        refuse_rule = None
        refuse_detail = None
        if global_rule["verdict"] == "refuse":
            refuse_rule = "global_identity"
            refuse_detail = (
                f"global identity at or above {global_threshold_percent}% "
                f"(mmseqs2={mm['identity_percent'] if mm else 'none'}, "
                f"blastp={bl['identity_percent'] if bl else 'none'})"
            )
        elif window_rule["verdict"] == "refuse":
            refuse_rule = "local_window"
            best = window_rule.get("best_window") or {}
            refuse_detail = (
                f"local window {window_length} residues at "
                f"{best.get('identity_percent', '?')}% identity on query "
                f"{best.get('query_start', '?')}-{best.get('query_end', '?')} "
                f"via {best.get('tool', '?')}"
            )
        per_sequence.append(
            {
                "query_id": qid,
                "sequence": meta.get("sequence"),
                "role": meta.get("role"),
                "design": meta.get("design"),
                "global_identity": global_rule,
                "local_window": window_rule,
                "refuse": refuse,
                "refuse_rule": refuse_rule,
                "refuse_detail": refuse_detail,
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
    global_threshold_percent: int,
    window_length: int,
    window_identity_percent: int,
    search_min_seq_id_percent: int,
    tmp_prefix: str,
    method: str | None = None,
    method_limits: str | None = None,
) -> dict[str, Any]:
    designs = designs or []
    record: dict[str, Any] = {
        "gate": gate_name,
        "tool": "mmseqs2+blastp",
        "threshold_identity_percent": global_threshold_percent,
        "window_length": window_length,
        "window_identity_percent_threshold": window_identity_percent,
        "reference_path": None,
        "reference_version": None,
        "reference_sha256": None,
        "per_sequence": [],
        "verdict": None,
    }
    if method is not None:
        record["method"] = method
    if method_limits is not None:
        record["method_limits"] = method_limits

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
            path,
            query_fasta,
            work,
            search_min_seq_id_percent=search_min_seq_id_percent,
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
            _merge_rules(
                queries_meta,
                mmseqs_hits,
                blast_hits,
                global_threshold_percent=global_threshold_percent,
                window_length=window_length,
                window_identity_percent=window_identity_percent,
            ),
            target=target,
        )
        record["per_sequence"] = per_sequence

    refusing = [row for row in record["per_sequence"] if row.get("refuse")]
    if refusing:
        record["status"] = "refuse"
        record["verdict"] = "refuse"
        reasons = []
        for row in refusing:
            rule = row.get("refuse_rule") or "unknown"
            detail = row.get("refuse_detail") or row["query_id"]
            reasons.append(f"{row['query_id']} ({rule}: {detail})")
        record["reason"] = "; ".join(reasons)
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
        global_threshold_percent=HUMAN_REFERENCE_HIT_IDENTITY_PERCENT,
        window_length=HUMAN_REFERENCE_WINDOW_LENGTH,
        window_identity_percent=HUMAN_REFERENCE_WINDOW_IDENTITY_PERCENT,
        search_min_seq_id_percent=HUMAN_REFERENCE_SEARCH_MIN_SEQ_ID_PERCENT,
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
        global_threshold_percent=HAZARD_SCREEN_HIT_IDENTITY_PERCENT,
        window_length=HAZARD_SCREEN_WINDOW_LENGTH,
        window_identity_percent=HAZARD_SCREEN_WINDOW_IDENTITY_PERCENT,
        search_min_seq_id_percent=HOMOLOGY_GATE_SEARCH_MIN_SEQ_ID_PERCENT,
        tmp_prefix="bindi-hazard-",
        method=HAZARD_SCREEN_METHOD,
        method_limits=HAZARD_SCREEN_METHOD_LIMITS,
    )
