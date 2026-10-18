"""The human-reference gate, as far as it exists.

The Scientist's second condition on a real design run is that the output passes a
human-reference gate before anything is shown: a designed binder that looks like a human
protein is a cross-reactivity problem, not a candidate. **That gate is not built.** bindi
ships no human reference proteome and no homology search, so with nothing configured this
returns `status: "unbuilt"` and the design hold refuses to start. It never returns "pass"
by default, because a gate that passes when it has not looked is worse than no gate.

Configured (`BINDI_HUMAN_REFERENCE_FASTA` or `reference_fasta=`) it runs an exact k-mer
screen: a design fails when any contiguous window of `k` residues occurs verbatim in the
reference. That is a crude screen, not a validated homology search (no substitution
matrix, no alignment, no E-value) and the record says so in `method_limits`. It is the
smallest check that can honestly return a verdict, and it is swapped for a real search by
pointing this hook at one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

REFERENCE_ENV = "BINDI_HUMAN_REFERENCE_FASTA"
DEFAULT_K = 9

METHOD_LIMITS = (
    "Exact k-mer containment only: no alignment, no substitution matrix, no E-value, and no "
    "structural or immunogenicity assessment. A pass means no identical k-residue window was "
    "found in the supplied reference; it does not mean the design is not cross-reactive."
)

UNBUILT_REASON = (
    "No human reference proteome is configured ("
    + REFERENCE_ENV
    + " unset), so nothing was screened. The human-reference gate is specified, not built; "
    "this status is reported rather than passed."
)


def _read_reference(path: Path) -> str:
    sequences: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            sequences.append("\n")
        else:
            sequences.append(line.strip().upper())
    return "".join(sequences)


def human_reference_gate(
    designs: list[dict[str, Any]] | None = None,
    *,
    reference_fasta: str | Path | None = None,
    k: int = DEFAULT_K,
) -> dict[str, Any]:
    """Screen design sequences against a human reference. Honest about being unbuilt."""
    designs = designs or []
    record: dict[str, Any] = {
        "gate": "human_reference",
        "k": k,
        "method": "exact k-mer containment against a supplied reference FASTA",
        "method_limits": METHOD_LIMITS,
        "reference": None,
        "checked": 0,
        "hits": [],
    }

    reference = reference_fasta or os.environ.get(REFERENCE_ENV)
    if not reference:
        record["status"] = "unbuilt"
        record["reason"] = UNBUILT_REASON
        return record

    path = Path(reference)
    if not path.exists():
        record["status"] = "error"
        record["reason"] = f"reference FASTA not found: {path}"
        return record
    record["reference"] = str(path)

    if not designs:
        record["status"] = "not_run"
        record["reason"] = (
            "a reference is configured but there are no design sequences to screen "
            "(fixture-backed run produces none)"
        )
        return record

    haystack = _read_reference(path)
    for design in designs:
        sequence = (design.get("sequence") or "").upper()
        record["checked"] += 1
        for start in range(0, max(0, len(sequence) - k + 1)):
            window = sequence[start : start + k]
            if window in haystack:
                record["hits"].append({"design": design.get("design"), "kmer": window})
                break
    record["status"] = "fail" if record["hits"] else "pass"
    return record
