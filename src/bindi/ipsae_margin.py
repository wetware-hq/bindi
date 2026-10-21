"""ipSAE as a CANDIDATE structural scorer for the wild-type margin. Not signed off.

What this is: the plumbing that would let `bindi.ipsae` — Dunbrack-lab interface PAE,
computed on CPU with numpy from Boltz's own ``pae.npz`` and ``*_predicted.cif`` — act as the
margin scorer, *if* a threshold for it were ever signed off. It runs no model, needs no GPU
and self-hosts nothing: it reads artifacts a hosted Boltz run already produced.

What this is NOT: a signed scorer. ``margin.SIGNED_STRUCTURAL_SCORER_IDS`` is empty and
stays empty; registering this candidate does not let the margin stage pass, and nothing in
this module names, derives or defaults a threshold. The measurement that would be needed to
establish one, its cost, and what a signature would attest to are set out in
``docs/PROPOSAL-ipsae-structural-margin.md`` — a proposal to the Chief Scientist, not code
that takes effect.

The honest description of the quantity, which travels with every record this produces:
ipSAE is an *interface-confidence* score derived from a model's predicted aligned error. It
says how confident Boltz is about the relative placement of two chains. It is not an
affinity, not a Kd, not a measurement of binding, and a difference between two ipSAE values
is a difference between two predictions, not between two experiments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from . import ipsae

#: Scorer id. Registered with `margin` as a candidate; absent from the signed set.
SCORER_ID = "ipsae_boltz_cpu_v1"
SCORER_VERSION = "1.0.0"
SCORING_FUNCTION = "bindi.ipsae_margin.score_complex_min_ipsae"

#: Evidence level: what the number is, stated in the name so it cannot be read as binding.
EVIDENCE_LEVEL = "interface_pae_from_boltz_prediction_not_a_binding_measurement"

TOOL_NAME = "ipSAE (Dunbrack-lab interface PAE, CPU)"

THRESHOLD_STATUS = "not_established"
THRESHOLD_PROPOSAL = "docs/PROPOSAL-ipsae-structural-margin.md"

WHAT_IT_MEASURES = (
    "Interface predicted aligned error between two chains of one Boltz-predicted complex, "
    "as ipSAE (Dunbrack lab `ipsae_d0res`), computed on CPU from the run's own pae.npz and "
    "predicted CIF. bindi takes min(A->B, B->A), the conservative direction."
)

WHAT_IT_DOES_NOT_MEASURE = (
    "Binding. ipSAE is a model's confidence in its own interface geometry, not an affinity, "
    "Kd/Ki/IC50, kcal/mol or any measured quantity. A difference between two ipSAE values is "
    "a difference between two predictions of the same model, so a bias shared by both "
    "complexes cancels only to first order and a peptide-dependent bias does not cancel at "
    "all. No experimental anchor exists for it in this repository."
)

#: The caveat that must travel with every record carrying an ipSAE-derived number.
#: Chief Scientist / Mathew, 2026-10-21: recorded in output wording, not only in docs.
CAVEAT = (
    "ipSAE caveat: this repository contains no orthogonal re-predictor and no experimental "
    "anchor for ipSAE, so its error is peptide-dependent and does NOT cancel under "
    "subtraction. Any threshold ever signed off this quantity is provisional until wet-lab "
    "confirmation, and any number derived from it is one model's confidence in its own "
    "predicted geometry, not a measurement of binding."
)

#: Units of the quantity, for records that carry one.
UNITS = (
    "ipSAE (Dunbrack-lab interface PAE, dimensionless 0-1); a prediction-confidence score, "
    "never an affinity"
)

#: PAE cutoff `bindi.ipsae` uses when deciding which residue pairs count as interface. A
#: method parameter of the score itself, not a threshold on the margin: it decides nothing
#: about whether a design passes.
DEFAULT_PAE_CUTOFF = 15.0


class MissingPredictionError(FileNotFoundError):
    """A margin was requested for a complex with no Boltz prediction artifacts.

    Raised rather than returning a number: a missing prediction is a refusal, never a zero.
    """


ArtifactLookup = Callable[[str, str], Mapping[str, Any] | None]


def score_complex_min_ipsae(
    pae_npz: str | Path,
    predicted_cif: str | Path,
    *,
    chain_pair: tuple[str, str] | None = None,
    pae_cutoff: float = 15.0,
) -> float:
    """min_ipsae for one predicted complex. Pure computation on artifacts already on disk.

    `chain_pair` names the two chains whose interface is scored (for a pMHC campaign, the
    designed binder chain and the peptide chain). With no pair given, and exactly two
    polymer chains present, that single interface is used; anything more ambiguous refuses
    rather than picking one.
    """
    for path in (pae_npz, predicted_cif):
        if not Path(path).exists():
            raise MissingPredictionError(
                f"ipSAE needs the Boltz run's own artifacts and {path} is absent. No margin "
                "was computed and no number was substituted for one."
            )
    result = ipsae.score_from_paths(pae_npz, predicted_cif, pae_cutoff=pae_cutoff)
    pairs = result["pairs"]
    if chain_pair is not None:
        key = "|".join(sorted(chain_pair))
        if key not in pairs:
            raise MissingPredictionError(
                f"no interface {key!r} in the prediction; available: {sorted(pairs)}"
            )
    elif len(pairs) == 1:
        key = next(iter(pairs))
    else:
        raise MissingPredictionError(
            "the prediction has more than one chain-pair interface "
            f"({sorted(pairs)}); name chain_pair explicitly rather than guessing which "
            "interface the margin is about"
        )
    return float(pairs[key]["min_ipsae"])


def make_margin_scorer(
    artifacts: ArtifactLookup,
    *,
    chain_pair: tuple[str, str] | None = None,
    pae_cutoff: float = 15.0,
) -> Callable[[str, str], float]:
    """Adapt ipSAE to `margin.score_wild_type_margin`'s `(peptide, design) -> float` scorer.

    `artifacts(peptide, design_sequence)` returns the Boltz artifacts for *that* complex —
    a mapping with `pae_npz` and `predicted_cif` — or None when no prediction exists, in
    which case scoring refuses. `margin` then forms
    `mutant_complex_score - wild_type_complex_score` itself, so the wild-type complex must
    have been predicted too: there is no way to state a margin from the mutant alone.

    This is the "if a threshold were signed" seam. Wiring it changes nothing about whether
    the margin stage passes: the threshold fields come from `margin`, which has no branch
    that can return `established`.
    """

    def score(peptide: str, design_sequence: str) -> float:
        found = artifacts(peptide, design_sequence)
        if not found:
            raise MissingPredictionError(
                "no Boltz prediction artifacts for this complex (peptide "
                f"{peptide[:12]!r}...), so no interface score exists for it. A margin is "
                "refused rather than estimated."
            )
        return score_complex_min_ipsae(
            found["pae_npz"],
            found["predicted_cif"],
            chain_pair=chain_pair,
            pae_cutoff=pae_cutoff,
        )

    score.__qualname__ = f"ipsae_margin_scorer[{SCORER_ID}]"
    return score


def registration() -> dict[str, Any]:
    """The candidate's registry entry, consumed by `margin.CANDIDATE_STRUCTURAL_SCORERS`."""
    return {
        "scorer_id": SCORER_ID,
        "version": SCORER_VERSION,
        "scoring_function": SCORING_FUNCTION,
        "tool": TOOL_NAME,
        "evidence_level": EVIDENCE_LEVEL,
        "structural": True,
        "gpu_required": False,
        "self_hosted": False,
        "runs_a_model": False,
        "inputs": ["pae.npz", "*_predicted.cif"],
        "what_it_measures": WHAT_IT_MEASURES,
        "what_it_does_not_measure": WHAT_IT_DOES_NOT_MEASURE,
        "caveat": CAVEAT,
        "units": UNITS,
        "threshold_status": THRESHOLD_STATUS,
        "threshold_proposal": THRESHOLD_PROPOSAL,
        "signed_off": False,
    }
