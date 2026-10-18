"""Structural sequence provenance and binding-claim rules.

A language model can emit peptide-like strings that look like success; the schema and this
validator block pairing those sequences with binding, affinity, or margin fields unless
provenance names a scoring design tool.
"""

from __future__ import annotations

import re
from typing import Any

SEQUENCE_FIELD_SCHEMA = "bindi.sequence_field.v1"
AA_FULL_RE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$")

PROVENANCE_KINDS = frozenset({"design_tool", "fixture", "reference"})

EVIDENCE_LEVEL_SEQUENCE_ONLY = "sequence_level_not_structure_validated"

BINDING_CLAIM_FIELDS = frozenset(
    {
        "margin",
        "mutant_complex_score",
        "wild_type_complex_score",
        "binding",
        "affinity",
    }
)


def sequence_text(field: Any) -> str | None:
    """Return the amino-acid string from a sequence field object or a bare string."""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        seq = field.get("sequence")
        return seq if isinstance(seq, str) else None
    return None


def validate_sequence_field(value: Any, *, path: str = "sequence_field") -> list[str]:
    """Refuse invalid sequence fields; empty list means valid."""
    problems: list[str] = []
    if not isinstance(value, dict):
        return [f"{path} must be an object with sequence and provenance"]
    seq = value.get("sequence")
    if not isinstance(seq, str) or not seq or not AA_FULL_RE.match(seq):
        problems.append(f"{path}.sequence must be a non-empty one-letter amino-acid string")
    prov = value.get("provenance")
    if not isinstance(prov, dict):
        problems.append(f"{path}.provenance is required")
        return problems
    kind = prov.get("kind")
    if kind not in PROVENANCE_KINDS:
        problems.append(
            f"{path}.provenance.kind must be one of {sorted(PROVENANCE_KINDS)}, found {kind!r}"
        )
    elif kind == "design_tool":
        if not str(prov.get("tool") or "").strip():
            problems.append(f"{path}.provenance.tool is required when kind is design_tool")
        if not str(prov.get("version") or "").strip():
            problems.append(f"{path}.provenance.version is required when kind is design_tool")
        if not str(prov.get("scoring_function") or "").strip():
            problems.append(
                f"{path}.provenance.scoring_function is required when kind is design_tool"
            )
    evidence = value.get("evidence_level")
    if evidence is not None and evidence != EVIDENCE_LEVEL_SEQUENCE_ONLY:
        problems.append(
            f"{path}.evidence_level must be {EVIDENCE_LEVEL_SEQUENCE_ONLY!r} when set, found {evidence!r}"
        )
    claim_fields = [name for name in BINDING_CLAIM_FIELDS if name in value]
    if kind != "design_tool" and claim_fields:
        problems.append(
            f"{path} has provenance.kind={kind!r} but carries binding claim field(s) "
            f"{claim_fields!r}: only design_tool provenance may include margin or affinity claims"
        )
    extra = set(value.keys()) - {"sequence", "provenance", "evidence_level"} - BINDING_CLAIM_FIELDS
    if extra:
        problems.append(f"{path} has unexpected field(s): {sorted(extra)}")
    return problems


def fixture_sequence(sequence: str, *, source: str = "bindi fixture") -> dict[str, Any]:
    return {
        "sequence": sequence,
        "provenance": {"kind": "fixture", "source": source},
    }


def reference_sequence(sequence: str, *, source: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "provenance": {"kind": "reference", "source": source},
    }


def design_tool_sequence(
    sequence: str,
    *,
    tool: str,
    scoring_function: str,
    tool_version: str,
    evidence_level: str = EVIDENCE_LEVEL_SEQUENCE_ONLY,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "evidence_level": evidence_level,
        "provenance": {
            "kind": "design_tool",
            "tool": tool,
            "version": tool_version,
            "scoring_function": scoring_function,
        },
    }
