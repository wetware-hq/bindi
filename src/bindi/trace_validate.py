"""Hand-rolled validation for case trace records that carry sequence fields."""

from __future__ import annotations

from typing import Any

from .provenance import validate_sequence_field

TRACE_SCHEMA = "bindi.case_trace.v1"

SEQUENCE_KEYS = frozenset({"mutant_peptide", "wild_type_peptide"})


def validate_trace_record(record: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["trace record must be a JSON object"]
    if record.get("schema") != TRACE_SCHEMA:
        problems.append(f"schema must be {TRACE_SCHEMA!r}")
    for key in SEQUENCE_KEYS:
        if key not in record:
            continue
        value = record[key]
        if key == "wild_type_peptide" and isinstance(value, dict) and value.get("absent"):
            # An EXPLICIT absence, used by a shared cancer-testis antigen record where no
            # distinct wild-type counterpart exists (see bindi.case). This is not a hole in
            # the check: a sequence is still validated whenever one is present, and an
            # absence must say so in words and carry no sequence. A trace that simply
            # omitted the peptide, or carried an empty one, still fails.
            if value.get("sequence") is not None:
                problems.append(f"{key}: an explicit absence must carry no sequence")
            if not str(value.get("absent_reason") or "").strip():
                problems.append(f"{key}.absent_reason is required for an explicit absence")
            continue
        problems.extend(validate_sequence_field(value, path=key))
    per_sequence = record.get("per_sequence")
    if per_sequence is not None:
        if not isinstance(per_sequence, list):
            problems.append("per_sequence must be a list when present")
        else:
            for index, row in enumerate(per_sequence):
                if isinstance(row, dict) and "sequence" in row:
                    problems.extend(
                        validate_sequence_field(row["sequence"], path=f"per_sequence[{index}].sequence")
                    )
    return problems
