"""Read a design target from a synthetic clinical-immunology case record.

The seam. Before this module the design target was only ever a literal string in the
campaign policy (`policy.boltz.target`, e.g. "hPDL1") resolved by
`policy.resolve_boltz_campaign`; nothing could take a target from a patient's own
record. `resolve_target` adds one source of truth above that literal: a case record
written by the clinical half (HLA type, mutant peptide, wild-type counterpart), with the
policy literal kept as the fixture fallback. Nothing else in the pipeline changes.

Validation here is hand-rolled against `schemas/immunology_case.v1.schema.json` (the repo
has no runtime dependencies and does not vendor a JSON-Schema validator). It checks the
required fields, the enums and the peptide/HLA/id patterns listed in that file. It is not
a general JSON-Schema implementation and does not claim to be.

A record is a design target only when `signature.signed` is true. An unsigned record is a
PLACEHOLDER: it may drive a fixture-backed run, never a real design run.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .provenance import sequence_text, validate_sequence_field

CASE_SCHEMA = "bindi.immunology_case.v1"
SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "immunology_case.v1.schema.json"

#: Environment override for the case record; the CLI's `--case` takes precedence.
CASE_ENV = "BINDI_CASE_FILE"

AA_RE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]{8,15}$")
HLA_I_RE = re.compile(r"^HLA-[ABC]\*[0-9]{2}:[0-9]{2}$")
CASE_ID_RE = re.compile(r"^SYNTH-[A-Za-z0-9._-]+$")

CONSENT_SCOPES = {"research_only", "clinical_care", "none"}
SENSITIVITIES = {"public", "internal", "sensitive"}
RELEASES = {"not_a_release", "released"}


class CaseError(ValueError):
    """The case record is missing, malformed, or not a synthetic record."""


class DesignHold(Exception):
    """A real design run was requested while the hold is in force."""


def load_case(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise CaseError(f"case record not found: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - message only
        raise CaseError(f"case record is not valid JSON: {source}: {exc}") from exc
    problems = validate_case(document)
    if problems:
        raise CaseError(f"case record {source} failed validation: " + "; ".join(problems))
    return document


def validate_case(case: Any) -> list[str]:
    """Return the list of problems with a case record; empty means it validates."""
    problems: list[str] = []
    if not isinstance(case, dict):
        return ["case record must be a JSON object"]
    if case.get("schema") != CASE_SCHEMA:
        problems.append(f"schema must be {CASE_SCHEMA!r}, found {case.get('schema')!r}")

    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not CASE_ID_RE.match(case_id):
        problems.append("case_id must match ^SYNTH-[A-Za-z0-9._-]+$ (synthetic ids only)")

    presentation = case.get("presentation")
    if not isinstance(presentation, dict):
        problems.append("presentation is required and must be an object")
    else:
        for field in ("summary", "diagnosis", "stage"):
            if not str(presentation.get(field) or "").strip():
                problems.append(f"presentation.{field} is required")

    hla = case.get("hla")
    restriction = None
    if not isinstance(hla, dict):
        problems.append("hla is required and must be an object")
    else:
        class_i = hla.get("class_i")
        if not isinstance(class_i, list) or not class_i:
            problems.append("hla.class_i must be a non-empty list of class I alleles")
            class_i = []
        for allele in class_i:
            if not isinstance(allele, str) or not HLA_I_RE.match(allele):
                problems.append(f"hla.class_i entry is not a class I allele: {allele!r}")
        restriction = hla.get("restriction_allele")
        if restriction is not None:
            if not HLA_I_RE.match(str(restriction)):
                problems.append(f"hla.restriction_allele is not a class I allele: {restriction!r}")
            elif restriction not in class_i:
                problems.append(
                    f"hla.restriction_allele {restriction!r} is not in hla.class_i {class_i!r}"
                )

    neo = case.get("neoantigen")
    if not isinstance(neo, dict):
        problems.append("neoantigen is required and must be an object")
    else:
        for field in ("gene", "variant"):
            if not str(neo.get(field) or "").strip():
                problems.append(f"neoantigen.{field} is required")
        mutant = neo.get("mutant_peptide")
        wild = neo.get("wild_type_peptide")
        mutant_seq = None
        wild_seq = None
        for name, value in (("mutant_peptide", mutant), ("wild_type_peptide", wild)):
            problems.extend(
                validate_sequence_field(value, path=f"neoantigen.{name}")
                if value is not None
                else [f"neoantigen.{name} is required"]
            )
            seq = sequence_text(value)
            if seq and not AA_RE.match(seq):
                problems.append(
                    f"neoantigen.{name}.sequence must be 8-15 one-letter residues, found {seq!r}"
                )
            if name == "mutant_peptide":
                mutant_seq = seq
            else:
                wild_seq = seq
        if mutant_seq and wild_seq and AA_RE.match(mutant_seq) and AA_RE.match(wild_seq):
            if mutant_seq == wild_seq:
                problems.append(
                    "neoantigen.mutant_peptide equals wild_type_peptide: there is nothing to "
                    "design against and no margin could be stated"
                )
            elif len(mutant_seq) != len(wild_seq):
                problems.append(
                    "neoantigen.mutant_peptide and wild_type_peptide differ in length; the "
                    "wild-type counterpart must be the same peptide without the variant"
                )

    consent = case.get("consent")
    if not isinstance(consent, dict):
        problems.append("consent is required and must be an object")
    else:
        for field in ("obtained", "design_use"):
            if not isinstance(consent.get(field), bool):
                problems.append(f"consent.{field} is required and must be true or false")
        if consent.get("scope") not in CONSENT_SCOPES:
            problems.append(f"consent.scope must be one of {sorted(CONSENT_SCOPES)}")

    data_class = case.get("data_class")
    if not isinstance(data_class, dict):
        problems.append("data_class is required and must be an object")
    else:
        if data_class.get("synthetic") is not True:
            problems.append(
                "data_class.synthetic must be true: bindi refuses any record not marked synthetic"
            )
        if data_class.get("sensitivity") not in SENSITIVITIES:
            problems.append(f"data_class.sensitivity must be one of {sorted(SENSITIVITIES)}")
        if data_class.get("release") not in RELEASES:
            problems.append(f"data_class.release must be one of {sorted(RELEASES)}")

    signature = case.get("signature")
    if not isinstance(signature, dict) or not isinstance(signature.get("signed"), bool):
        problems.append("signature.signed is required and must be true or false")

    return problems


def is_signed(case: dict[str, Any]) -> bool:
    signature = case.get("signature") or {}
    return bool(signature.get("signed")) and bool(signature.get("signed_by"))


def case_path_from(policy: dict[str, Any] | None = None, explicit: str | Path | None = None) -> Path | None:
    """Case record path: explicit (CLI) > policy.case > environment > none (fixture)."""
    if explicit:
        return Path(explicit)
    if policy:
        design_block = policy.get("boltz") or policy.get("bindcraft") or {}
        from_policy = policy.get("case") or design_block.get("case")
        if from_policy:
            return Path(from_policy)
    from_env = os.environ.get(CASE_ENV)
    if from_env:
        return Path(from_env)
    return None


def resolve_target(
    policy: dict[str, Any],
    *,
    case: dict[str, Any] | None = None,
    case_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve the design target, from a case record when one is given, else the fixture.

    The returned record always says which of the two it came from (`source`) and whether
    the design hold applies (`signed`). It never invents a peptide.
    """
    # `bindcraft` is read only so an existing policy file still resolves; Boltz is the generator.
    design_block = policy.get("boltz") or policy.get("bindcraft") or {}
    if case is None:
        path = case_path_from(policy, case_path)
        if path is not None:
            case = load_case(path)
    if case is None:
        literal = design_block.get("target")
        if not literal:
            raise CaseError(
                "no case record and no policy.boltz.target: nothing to design against"
            )
        return {
            "source": "fixture",
            "target_id": literal,
            "case_id": None,
            "signed": False,
            "restriction_allele": None,
            "mutant_peptide": None,
            "wild_type_peptide": None,
            "gene": None,
            "variant": None,
            "note": (
                "Target is the literal campaign target, not a patient's record. A fixture "
                "target is not personalised to anyone."
            ),
        }

    problems = validate_case(case)
    if problems:
        raise CaseError("case record failed validation: " + "; ".join(problems))

    hla = case.get("hla") or {}
    neo = case.get("neoantigen") or {}
    restriction = hla.get("restriction_allele") or (hla.get("class_i") or [None])[0]
    return {
        "source": "case_record",
        "target_id": f"{case['case_id']}:{neo.get('gene')}:{neo.get('variant')}",
        "case_id": case["case_id"],
        "signed": is_signed(case),
        "restriction_allele": restriction,
        "mutant_peptide": neo.get("mutant_peptide"),
        "wild_type_peptide": neo.get("wild_type_peptide"),
        "gene": neo.get("gene"),
        "variant": neo.get("variant"),
        "consent_design_use": bool((case.get("consent") or {}).get("design_use")),
        "note": (
            "Target taken from the patient case record. Unsigned records are PLACEHOLDERs: "
            "fixture-backed runs only."
        ),
    }


def design_hold_reasons(
    target: dict[str, Any],
    *,
    human_reference: dict[str, Any] | None = None,
    margin: dict[str, Any] | None = None,
) -> list[str]:
    """Why a real design run may not start. Empty list means nothing here blocks it.

    Three conditions, in the order the room set them: the Physician's signed synthetic
    patient, the Scientist's human-reference gate, and the Scientist's stated margin
    against the wild-type complex.
    """
    reasons: list[str] = []
    if target.get("source") != "case_record":
        reasons.append(
            "target came from a fixture, not a signed patient record: designing against a "
            "fixture target would produce unchecked numbers a day early"
        )
    if not target.get("signed"):
        reasons.append(
            "case record is not signed (signature.signed / signed_by): the Physician's signed "
            "synthetic immunology patient does not exist yet"
        )
    if target.get("source") == "case_record" and not target.get("consent_design_use"):
        reasons.append("consent.design_use is false: the record does not consent to design use")
    if (human_reference or {}).get("status") != "pass":
        reasons.append(
            "human-reference gate has not passed (status="
            f"{(human_reference or {}).get('status', 'absent')!r}): the Scientist's condition is "
            "that it runs on the output before anything is shown"
        )
    if (margin or {}).get("status") != "stated":
        reasons.append(
            "wild-type margin is not stated (status="
            f"{(margin or {}).get('status', 'absent')!r}): the Scientist's condition is scoring "
            "against the wild-type complex with a stated margin, not the mutant alone"
        )
    if (margin or {}).get("threshold_status") != "established":
        reasons.append(
            "structural margin threshold is not established (status="
            f"{(margin or {}).get('threshold_status', 'absent')!r}): what is missing is a "
            "structural scorer whose margin threshold has been signed off for the Boltz-based "
            "pipeline — room BIO decision USE structure, and sequence-only scorers do not "
            "clear the margin stage"
        )
    return reasons


def assert_design_allowed(
    target: dict[str, Any],
    *,
    human_reference: dict[str, Any] | None = None,
    margin: dict[str, Any] | None = None,
) -> None:
    reasons = design_hold_reasons(target, human_reference=human_reference, margin=margin)
    if reasons:
        raise DesignHold("design hold in force: " + "; ".join(reasons))
