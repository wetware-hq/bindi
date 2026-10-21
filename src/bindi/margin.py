"""Wild-type margin hook: score a design against the wild-type complex, not the mutant alone.

The Scientist's condition on the first real design run is that every design is scored
against the wild-type complex with a *stated* margin. bindi cannot compute that today:
there is no structural scorer in this repo whose threshold has been signed off for the
Boltz-based pipeline, and no real design run has happened, so this module is a hook, not a
scorer. With no scorer injected it returns
`status: "not_computed"` and names what is missing; the design hold reads that status and
refuses to start.

`bindi.ipsae_margin` is registered here as a *candidate* structural scorer
(`CANDIDATE_STRUCTURAL_SCORERS`): structure-derived, CPU-only, and explicitly not signed
off. Registering it wires the plumbing and changes nothing about whether the stage passes.

A real scorer is a config swap, not a code change: pass a callable (or set
`BINDI_MARGIN_SCORER` to an `module:function` path) that takes (peptide, design) and
returns a float pass-signal score. The margin is then
`mutant_complex_score - wild_type_complex_score`, in relative pass-signal units only.
Never an absolute affinity: see `data.looks_like_absolute_affinity`.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from . import ipsae_margin
from .provenance import sequence_text

SCORER_ENV = "BINDI_MARGIN_SCORER"

UNITS = (
    "relative pass-signal units from the scorer named in `scorer`; a difference between two "
    "complexes in the same campaign, never an absolute affinity or potency value"
)

NOT_COMPUTED_REASON = (
    "No margin against the wild-type complex was computed. What is missing is a structural "
    "scorer whose margin threshold has been signed off for the Boltz-based pipeline: none is "
    "wired, and no design run has produced designs to score. A margin is stated only when a "
    "scorer is injected and a real design run has produced designs to score; it clears the "
    "margin stage only when that scorer's threshold has been signed off."
)

SEQUENCE_SCORER_IDS = frozenset({"class_bucket_v1", "blosum_position_v2"})

#: No structural scorer is recognised. BindCraft-on-Modal was the only scorer the signed
#: margin rule ever named, and it has been removed from the design path. Boltz is the
#: generator; no Boltz-derived structural margin threshold has been signed off, so there is
#: no scorer id this module may accept. This set stays empty until a threshold is signed.
SIGNED_STRUCTURAL_SCORER_IDS: frozenset[str] = frozenset()

#: Candidate structural scorers: structure-derived, selectable, and NOT signed off.
#: Being here means the plumbing exists and the quantity is named honestly. It does not
#: mean the scorer can clear the margin stage — that requires its id to appear in
#: `SIGNED_STRUCTURAL_SCORER_IDS`, which is empty and is signed content, not a code edit.
#: Each entry carries its own evidence level and a pointer to the threshold proposal.
CANDIDATE_STRUCTURAL_SCORERS: dict[str, dict[str, Any]] = {
    ipsae_margin.SCORER_ID: ipsae_margin.registration(),
}

#: Ids of structure-derived scorers that exist at all. Disjoint from the signed set.
CANDIDATE_STRUCTURAL_SCORER_IDS = frozenset(CANDIDATE_STRUCTURAL_SCORERS)

MISSING_REQUIREMENT = (
    "a structural scorer whose margin threshold has been signed off for the Boltz-based "
    "pipeline"
)

THRESHOLD_NOT_ESTABLISHED_REASON = (
    "Margin threshold is not established: no structural scorer whose margin threshold has "
    "been signed off for the Boltz-based pipeline exists in this repository. Boltz is the "
    "generator; BindCraft-on-Modal, the only scorer the signed margin rule previously named, "
    "has been removed from the design path, and no replacement threshold has been signed off "
    "by the Chief Scientist. Sequence-level scorers remain selectable for reproduction and "
    "never clear the margin stage. Until such a threshold is signed, the immunology margin "
    "stage refuses by construction and no margin number is written."
)


def candidate_structural_scorer(scorer_id: str) -> dict[str, Any]:
    """A candidate's registry entry, or a refusal naming the candidates that exist."""
    try:
        return CANDIDATE_STRUCTURAL_SCORERS[scorer_id]
    except KeyError:
        raise ValueError(
            f"unknown structural scorer {scorer_id!r}; candidates are "
            f"{sorted(CANDIDATE_STRUCTURAL_SCORERS)} and none has a signed threshold"
        ) from None


def is_signed_structural_scorer(scorer_id: str | None) -> bool:
    """True only for a scorer whose margin threshold has been signed off. Always False today."""
    return scorer_id in SIGNED_STRUCTURAL_SCORER_IDS


def load_scorer(spec: str | None = None) -> Callable[[str, str], float] | None:
    """Resolve a scorer from `module:function`; None when nothing is configured."""
    spec = spec or os.environ.get(SCORER_ENV)
    if not spec:
        return None
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"{SCORER_ENV} must look like 'module:function', found {spec!r}")
    return getattr(importlib.import_module(module_name), func_name)


def _threshold_fields(
    *,
    scorer_id: str | None,
    evidence_level: str | None,
) -> dict[str, Any]:
    """Threshold verdict for a margin record.

    There is exactly one outcome today: ``not_established``. ``SIGNED_STRUCTURAL_SCORER_IDS``
    is empty, so no scorer id can clear the margin stage, and this function has no branch
    that returns ``established``. That is deliberate: the threshold rule is signed content,
    and admitting a Boltz-derived structural margin is a sign-off decision, not a code edit.
    """
    fields = {
        "threshold_status": "not_established",
        "clears_margin_threshold": False,
        "threshold_reason": THRESHOLD_NOT_ESTABLISHED_REASON,
        "threshold_missing": MISSING_REQUIREMENT,
    }
    if scorer_id in SEQUENCE_SCORER_IDS or evidence_level == "sequence_level_not_structure_validated":
        fields["threshold_scorer_class"] = "sequence_level"
    elif scorer_id in CANDIDATE_STRUCTURAL_SCORER_IDS:
        # A candidate is structure-derived and still unsigned. Saying so is the whole point:
        # the record must not read as though a structural scorer settled anything.
        fields["threshold_scorer_class"] = "candidate_structural_not_signed"
        fields["threshold_proposal"] = CANDIDATE_STRUCTURAL_SCORERS[scorer_id][
            "threshold_proposal"
        ]
    elif scorer_id:
        fields["threshold_scorer_class"] = "unrecognised_scorer"
    return fields


def score_wild_type_margin(
    target: dict[str, Any],
    designs: list[dict[str, Any]] | None = None,
    *,
    scorer: Callable[[str, str], float] | None = None,
    scorer_spec: str | None = None,
    scorer_id: str | None = None,
    evidence_level: str | None = None,
) -> dict[str, Any]:
    """Return the margin record for a case. Honest about not being computed."""
    designs = designs or []
    mutant = sequence_text(target.get("mutant_peptide")) or target.get("mutant_peptide")
    wild = sequence_text(target.get("wild_type_peptide")) or target.get("wild_type_peptide")
    record: dict[str, Any] = {
        "rule": (
            "margin = mutant_complex_score - wild_type_complex_score, stated per design; a "
            "design is only of interest when the margin favours the mutant complex"
        ),
        "units": UNITS,
        "mutant_peptide": mutant,
        "wild_type_peptide": wild,
        "scorer": None,
        "per_design": [],
    }
    if evidence_level:
        record["evidence_level"] = evidence_level

    if not mutant or not wild:
        record["status"] = "not_applicable"
        record["reason"] = (
            "target has no mutant/wild-type peptide pair (fixture target), so no wild-type "
            "complex exists to score against"
        )
        return record

    if scorer is None:
        scorer = load_scorer(scorer_spec)
    if scorer is None or not designs:
        record["status"] = "not_computed"
        record["reason"] = NOT_COMPUTED_REASON + (
            "" if scorer is not None else f" No scorer configured ({SCORER_ENV} unset)."
        ) + ("" if designs else " No designs were produced (fixture-backed run).")
        record.update(
            _threshold_fields(scorer_id=scorer_id, evidence_level=record.get("evidence_level"))
        )
        return record

    record["scorer"] = getattr(scorer, "__qualname__", repr(scorer))
    if scorer_id:
        record["scorer_id"] = scorer_id
    for design in designs:
        sequence = design.get("sequence") or ""
        mutant_score = float(scorer(mutant, sequence))
        wild_score = float(scorer(wild, sequence))
        record["per_design"].append(
            {
                "design": design.get("design"),
                "mutant_complex_score": f"{mutant_score:.6f}",
                "wild_type_complex_score": f"{wild_score:.6f}",
                "margin": f"{mutant_score - wild_score:.6f}",
            }
        )
    record["status"] = "stated"
    record.update(
        _threshold_fields(
            scorer_id=scorer_id or record.get("scorer_id"),
            evidence_level=record.get("evidence_level"),
        )
    )
    return record
