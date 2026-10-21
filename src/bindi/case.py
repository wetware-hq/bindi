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

#: What kind of antigen the record is about. Absent means `private_neoantigen`, so every
#: record written before this field existed validates exactly as it did before.
#:
#: `shared_cancer_testis` (Mathew, 2026-10-21) covers a published shared antigen such as
#: NY-ESO-1: there is no variant and therefore no distinct wild-type counterpart — the self
#: peptide presented in healthy tissue IS the same sequence. Such a record may not carry a
#: wild-type peptide sequence (inventing one would fabricate clinical content), and in
#: exchange it must declare MORE than a private-neoantigen record does: the specificity
#: endpoint it is judged on and the negative-design panel it is judged against. This is a
#: tightening for that class, not a relaxation: a record with no wild-type sequence can
#: never produce a stated wild-type margin, so `design_hold_reasons` keeps the hold in
#: force for it in every case.
ANTIGEN_CLASSES = {"private_neoantigen", "shared_cancer_testis"}
DEFAULT_ANTIGEN_CLASS = "private_neoantigen"

#: The only endpoint a shared-antigen record may declare. The clinical hazard for a
#: neoantigen binder is a binder that engages invariant HLA framework and therefore binds
#: in every patient carrying the restricting allele regardless of their tumour, so the
#: endpoint is on-target minus best decoy across the panel, with the wild-type/self peptide
#: a mandatory panel member.
PANEL_ENDPOINT = "on_target_minus_best_decoy"

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
        antigen_class = neo.get("antigen_class", DEFAULT_ANTIGEN_CLASS)
        if antigen_class not in ANTIGEN_CLASSES:
            problems.append(
                f"neoantigen.antigen_class must be one of {sorted(ANTIGEN_CLASSES)}, found "
                f"{antigen_class!r}"
            )
        if antigen_class == "shared_cancer_testis":
            problems.extend(_shared_antigen_problems(case, neo))
            return problems + _tail_problems(case)
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

    problems.extend(_tail_problems(case))
    return problems


def _tail_problems(case: dict[str, Any]) -> list[str]:
    """Consent, data class and signature checks, shared by every antigen class."""
    problems: list[str] = []
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


def _shared_antigen_problems(case: dict[str, Any], neo: dict[str, Any]) -> list[str]:
    """Checks for a shared cancer-testis antigen record. Stricter, never looser.

    No variant exists, so no distinct wild-type counterpart exists either: the self peptide
    presented in healthy tissue is the same sequence. A wild-type SEQUENCE is therefore
    forbidden here rather than merely optional — the alternative would be an invented
    peptide — and the record must instead say, explicitly, that it is absent and why, which
    endpoint it is judged on, and which negative-design panel it is judged against.

    A record in this class can never yield a stated wild-type margin, so the design hold
    stays in force for it regardless of signature. Nothing here can release anything.
    """
    problems: list[str] = []
    if not str(neo.get("gene") or "").strip():
        problems.append("neoantigen.gene is required")
    if not str(neo.get("variant") or "").strip():
        problems.append(
            "neoantigen.variant is required; for a shared antigen say so in words rather "
            "than inventing an HGVS variant"
        )

    target_peptide = neo.get("mutant_peptide")
    problems.extend(
        validate_sequence_field(target_peptide, path="neoantigen.mutant_peptide")
        if target_peptide is not None
        else ["neoantigen.mutant_peptide is required (the on-target peptide)"]
    )
    seq = sequence_text(target_peptide)
    if seq and not AA_RE.match(seq):
        problems.append(
            "neoantigen.mutant_peptide.sequence must be 8-15 one-letter residues, found "
            f"{seq!r}"
        )

    wild = neo.get("wild_type_peptide")
    if not isinstance(wild, dict):
        problems.append(
            "neoantigen.wild_type_peptide is required and, for a shared cancer-testis "
            "antigen, must be an explicit absence object {absent: true, absent_reason: ...}"
        )
    else:
        if wild.get("absent") is not True:
            problems.append(
                "neoantigen.wild_type_peptide.absent must be true for a shared "
                "cancer-testis antigen: there is no variant, so no distinct wild-type "
                "counterpart exists"
            )
        if wild.get("sequence") is not None:
            problems.append(
                "neoantigen.wild_type_peptide.sequence must NOT be set for a shared "
                "cancer-testis antigen: any sequence here would be an invented peptide"
            )
        if not str(wild.get("absent_reason") or "").strip():
            problems.append("neoantigen.wild_type_peptide.absent_reason is required")

    panel = case.get("specificity_panel")
    if not isinstance(panel, dict):
        problems.append(
            "specificity_panel is required for a shared cancer-testis antigen: with no "
            "wild-type counterpart, specificity is judged against the negative-design panel"
        )
    else:
        if panel.get("endpoint") != PANEL_ENDPOINT:
            problems.append(
                f"specificity_panel.endpoint must be {PANEL_ENDPOINT!r}"
            )
        if panel.get("self_peptide_in_panel") is not True:
            problems.append(
                "specificity_panel.self_peptide_in_panel must be true: the wild-type/self "
                "peptide is a mandatory member of the panel"
            )
        if not str(panel.get("panel_reference") or "").strip():
            problems.append(
                "specificity_panel.panel_reference is required (where the panel is defined)"
            )
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
    antigen_class = neo.get("antigen_class", DEFAULT_ANTIGEN_CLASS)
    wild_type = neo.get("wild_type_peptide")
    wild_absent_reason = None
    if isinstance(wild_type, dict) and wild_type.get("absent"):
        # An explicit absence is passed on as an absence, never as a truthy object: the
        # margin stage must see "there is no wild-type complex", not a dict it could
        # stringify into a peptide.
        wild_absent_reason = wild_type.get("absent_reason")
        wild_type = None
    panel = case.get("specificity_panel") or {}
    extra: dict[str, Any] = {}
    if antigen_class != DEFAULT_ANTIGEN_CLASS:
        extra["antigen_class"] = antigen_class
    if wild_absent_reason:
        extra["wild_type_peptide_absent_reason"] = wild_absent_reason
    if panel:
        extra["margin_endpoint"] = panel.get("endpoint")
        extra["specificity_panel"] = panel
    return {
        "source": "case_record",
        "target_id": f"{case['case_id']}:{neo.get('gene')}:{neo.get('variant')}",
        "case_id": case["case_id"],
        "signed": is_signed(case),
        "restriction_allele": restriction,
        "mutant_peptide": neo.get("mutant_peptide"),
        "wild_type_peptide": wild_type,
        "gene": neo.get("gene"),
        "variant": neo.get("variant"),
        "consent_design_use": bool((case.get("consent") or {}).get("design_use")),
        "note": (
            "Target taken from the patient case record. Unsigned records are PLACEHOLDERs: "
            "fixture-backed runs only."
        ),
        **extra,
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
