"""ipSAE as a VETO-ONLY filter: it may refuse a design, and it may never bless one.

WHY THIS NEEDS NO SIGNED THRESHOLD
----------------------------------
`margin.SIGNED_STRUCTURAL_SCORER_IDS` is empty and stays empty: no structural scorer in
this repository has a signed margin threshold, so no scorer may certify that a design is
good. Acting on the *negative* result is a different claim, and a weaker one. A poor
interface-confidence score is a reason to drop a design; a good one is not a reason to keep
it, promote it, rank it higher or call it a binder. Subtracting candidates can only shrink
what is shown; it can never manufacture evidence that something works. That asymmetry, not
a threshold, is what licenses this filter.

The asymmetry is enforced by construction, not by convention:

* `VetoDecision` has no passing state to read. There is `vetoed`, and there is
  `not_vetoed`, which is explicitly "this filter has nothing to say". Truth-testing a
  decision raises, so `if decision:` cannot quietly become a pass.
* A decision may carry an ipSAE number ONLY when it is a veto (enforced in
  `__post_init__`). A surviving design therefore has no ipSAE value attached to it
  anywhere, so nothing downstream can rank, sort, promote, gate or report on one.
* `apply_veto` returns the surviving design dicts **unmodified and in their original
  order** — the same objects that went in. The filter can delete; it cannot annotate,
  score, reorder or add.
* With no veto floor configured the filter is inert: it vetoes nothing and still asserts
  nothing. There is no default floor in this module, because a number in code here would
  be a threshold in all but name.

This is NOT a way to clear the margin stage. The margin stage refuses by construction and
is untouched by anything here: see `margin._threshold_fields`, which has no branch that can
return `established`. A vetoed design is dropped; a surviving design is exactly as
unvalidated as it was before the filter ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from . import ipsae_margin

FILTER_ID = "ipsae_veto_only_v1"
SCORER_ID = ipsae_margin.SCORER_ID
EVIDENCE_LEVEL = ipsae_margin.EVIDENCE_LEVEL

#: Policy key for the floor below which a design is vetoed. Read from
#: `policy.curation.<key>`. Deliberately has NO default in code: an unset floor makes the
#: filter inert rather than inventing a number.
VETO_FLOOR_POLICY_KEY = "ipsae_veto_floor"

#: What surviving this filter means. Carried on every record this module writes.
SURVIVOR_MEANING = (
    "Surviving this filter is not evidence of anything. The filter only removes designs "
    "whose predicted interface confidence is below the configured veto floor; it does not "
    "pass, bless, rank, promote or certify the designs it leaves alone, and no ipSAE value "
    "is recorded for a surviving design precisely so that none can be read as a result."
)

VETO_MEANING = (
    "A veto means one model's confidence in its own predicted interface was below the "
    "floor configured for this campaign. It is a reason to stop spending attention on a "
    "design, not a measurement that the design does not bind."
)

NO_FLOOR_REASON = (
    "No veto floor is configured (policy.curation."
    + VETO_FLOOR_POLICY_KEY
    + " is unset), so the filter removed nothing. An unset floor is inert, never a pass: "
    "no default is supplied here because a number in code would be a threshold in all but "
    "name, and no ipSAE threshold has been signed off."
)

NO_PREDICTION_REASON = (
    "No Boltz prediction artifacts exist for this design, so ipSAE was not computed and the "
    "design was not vetoed. A missing prediction is an absence of evidence, never evidence "
    "against a design."
)


class VetoOnlyViolation(AssertionError):
    """Something tried to read a veto-only result as a pass. That is a bug, not a verdict."""


@dataclass(frozen=True)
class VetoDecision:
    """One design's veto outcome. There is no passing state here, by design.

    `score` may be set only when `vetoed` is true: the number exists to justify a removal
    and for no other purpose. A surviving design carries no ipSAE value at all.
    """

    design: str
    vetoed: bool
    reason: str
    score: float | None = None
    caveat: str = field(default=ipsae_margin.CAVEAT)

    def __post_init__(self) -> None:
        if self.score is not None and not self.vetoed:
            raise VetoOnlyViolation(
                "a surviving design may not carry an ipSAE score: this filter vetoes, it "
                "never blesses, and a number attached to a survivor would be read as "
                "evidence of binding"
            )

    def __bool__(self) -> bool:  # pragma: no cover - exercised by test, never by callers
        raise VetoOnlyViolation(
            "a veto-only decision has no truth value; read .vetoed explicitly. There is no "
            "'passed' state to test for."
        )

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "design": self.design,
            "verdict": "vetoed" if self.vetoed else "not_vetoed",
            "reason": self.reason,
            "asserts": VETO_MEANING if self.vetoed else SURVIVOR_MEANING,
            "evidence_level": EVIDENCE_LEVEL,
            "caveat": self.caveat,
        }
        if self.vetoed and self.score is not None:
            record["ipsae"] = f"{self.score:.6f}"
            record["units"] = ipsae_margin.UNITS
        return record


def veto_floor_from_policy(policy: dict[str, Any] | None) -> float | None:
    """The configured veto floor, or None. Never a default: unset means inert."""
    curation = ((policy or {}).get("curation") or {})
    raw = curation.get(VETO_FLOOR_POLICY_KEY)
    if raw is None:
        return None
    return float(raw)


def veto_designs(
    designs: Sequence[dict[str, Any]],
    *,
    score_for: Callable[[dict[str, Any]], float],
    veto_floor: float | None,
) -> dict[str, Any]:
    """Run the veto-only filter and return its record. Removes nothing by itself.

    `score_for(design)` returns that design's ipSAE for the campaign's interface, or raises
    `ipsae_margin.MissingPredictionError` when the complex was never predicted. Scoring is
    attempted only when a floor is configured: with no floor there is nothing a number
    could be used for, and computing one anyway would only create a value to be misread.
    """
    decisions: list[VetoDecision] = []
    if veto_floor is None:
        for design in designs:
            decisions.append(
                VetoDecision(
                    design=str(design.get("design") or ""),
                    vetoed=False,
                    reason=NO_FLOOR_REASON,
                )
            )
    else:
        for design in designs:
            name = str(design.get("design") or "")
            try:
                score = float(score_for(design))
            except ipsae_margin.MissingPredictionError:
                decisions.append(
                    VetoDecision(design=name, vetoed=False, reason=NO_PREDICTION_REASON)
                )
                continue
            if score < veto_floor:
                decisions.append(
                    VetoDecision(
                        design=name,
                        vetoed=True,
                        reason=(
                            "predicted interface confidence is below the campaign's "
                            "configured veto floor"
                        ),
                        score=score,
                    )
                )
            else:
                # Deliberately no score on the record: see SURVIVOR_MEANING.
                decisions.append(
                    VetoDecision(
                        design=name,
                        vetoed=False,
                        reason=(
                            "not below the configured veto floor, which asserts nothing "
                            "about this design"
                        ),
                    )
                )
    vetoed = [d.design for d in decisions if d.vetoed]
    return {
        "filter": FILTER_ID,
        "mode": "veto_only",
        "status": "inactive" if veto_floor is None else "applied",
        "scorer_id": SCORER_ID,
        "evidence_level": EVIDENCE_LEVEL,
        "may_refuse_a_design": True,
        "may_pass_rank_or_certify_a_design": False,
        "needs_signed_threshold": False,
        "why_no_signed_threshold_is_needed": (
            "The filter acts only on the negative result. A poor score removes a design; a "
            "good score asserts nothing and is not even recorded. Subtracting candidates "
            "cannot manufacture evidence that one works, so no signed threshold is implied "
            "and none is claimed. Certifying a design would need one, and this filter never "
            "certifies."
        ),
        "survivors_mean": SURVIVOR_MEANING,
        "caveat": ipsae_margin.CAVEAT,
        "veto_floor_configured": veto_floor is not None,
        "vetoed_designs": vetoed,
        "vetoed_count": len(vetoed),
        "decisions": [d.as_record() for d in decisions],
        "margin_stage_unaffected": (
            "This filter is not the margin stage and cannot clear it. The margin stage "
            "refuses by construction; vetoing designs does not change that."
        ),
    }


def apply_veto(
    designs: Sequence[dict[str, Any]], record: dict[str, Any]
) -> list[dict[str, Any]]:
    """The surviving designs: the same objects, same order, nothing added.

    Subtraction only. Nothing here may annotate, score, reorder or promote a design, which
    is why this returns the original dicts rather than copies carrying a verdict.
    """
    vetoed = set(record.get("vetoed_designs") or ())
    return [d for d in designs if str(d.get("design") or "") not in vetoed]


def scorer_from_artifacts(
    artifacts: Callable[[str], Any],
    *,
    chain_pair: tuple[str, str] | None = None,
) -> Callable[[dict[str, Any]], float]:
    """Adapt per-design Boltz artifacts to `veto_designs`' `score_for`.

    `artifacts(design_name)` returns a mapping with `pae_npz` and `predicted_cif`, or None
    when the complex has no prediction, in which case scoring refuses rather than
    returning a number.
    """

    def score_for(design: dict[str, Any]) -> float:
        name = str(design.get("design") or "")
        found = artifacts(name)
        if not found:
            raise ipsae_margin.MissingPredictionError(
                f"no Boltz prediction artifacts for design {name!r}; ipSAE was not computed"
            )
        return ipsae_margin.score_complex_min_ipsae(
            found["pae_npz"], found["predicted_cif"], chain_pair=chain_pair
        )

    return score_for


def iter_vetoed(record: dict[str, Any]) -> Iterable[str]:
    return iter(record.get("vetoed_designs") or ())
