"""Wild-type margin hook: score a design against the wild-type complex, not the mutant alone.

The Scientist's condition on the first real design run is that every design is scored
against the wild-type complex with a *stated* margin. bindi cannot compute that today:
there is no structural scorer in this repo and no real design run has happened, so this
module is a hook, not a scorer. With no scorer injected it returns
`status: "not_computed"` and names what is missing; the design hold reads that status and
refuses to start.

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

from .provenance import sequence_text

SCORER_ENV = "BINDI_MARGIN_SCORER"

UNITS = (
    "relative pass-signal units from the scorer named in `scorer`; a difference between two "
    "complexes in the same campaign, never an absolute affinity or potency value"
)

NOT_COMPUTED_REASON = (
    "No structural scorer is wired and no design run has happened, so no margin against the "
    "wild-type complex exists. A margin is stated only when a scorer is injected and a real "
    "design run has produced designs to score."
)


def load_scorer(spec: str | None = None) -> Callable[[str, str], float] | None:
    """Resolve a scorer from `module:function`; None when nothing is configured."""
    spec = spec or os.environ.get(SCORER_ENV)
    if not spec:
        return None
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"{SCORER_ENV} must look like 'module:function', found {spec!r}")
    return getattr(importlib.import_module(module_name), func_name)


def score_wild_type_margin(
    target: dict[str, Any],
    designs: list[dict[str, Any]] | None = None,
    *,
    scorer: Callable[[str, str], float] | None = None,
    scorer_spec: str | None = None,
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
        return record

    record["scorer"] = getattr(scorer, "__qualname__", repr(scorer))
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
    return record
