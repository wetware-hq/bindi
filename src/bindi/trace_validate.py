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
        if key in record:
            problems.extend(validate_sequence_field(record[key], path=key))
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
