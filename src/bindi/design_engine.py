"""Sequence-level binder designer: stochastic search with explicit, named scorers.

This is not structure-validated design. Candidates come from seeded point mutations on a
fixed starter binder. Scorers are selectable by id (see ``SCORER_REGISTRY``).

Scorer ``blosum_position_v2`` (default)
---------------------------------------
Terms and coefficients (higher total is better):

1. **BLOSUM62 sliding-window contact** — for each register aligning a window of
   ``len(peptide)`` binder residues with the peptide, sum
   ``BLOSUM62_COEFF * position_weight[i] * BLOSUM62(peptide[i], binder[offset+i])``.
   Matrix from Biopython ``Bio.Align.substitution_matrices`` (not duplicated here).
2. **Formal-charge complementarity** — at each aligned position with both sides charged:
   ``CHARGE_COMPLEMENT_COEFF_V2 * (-q_peptide * q_binder)`` (same q table as v1).
3. **Length prior** — ``-LENGTH_PRIOR_WEIGHT * abs(len(binder) - LENGTH_PRIOR_CENTER)``.
4. **Cysteine penalty** — ``-CYS_PENALTY`` per Cys in the binder.
5. **Proline-in-window penalty** — on the best register only,
   ``-PROLINE_IN_WINDOW_PENALTY`` per Pro in the aligned window.

Position weights (9-mers, class I anchor emphasis): P2 index 1 → 2.0, P3 index 2 → 1.75,
P9 index 8 → 2.5; all other positions → 1.0. For other peptide lengths, uniform 1.0.

Scorer ``class_bucket_v1`` (retained for reproduction)
----------------------------------------------------
See ``score_binder_peptide_class_bucket_v1`` docstring (failed PAIR-HARD positive control).
"""

from __future__ import annotations

import csv
import json
import random
import statistics
from pathlib import Path
from typing import Any, Callable

from Bio.Align import substitution_matrices

from .provenance import design_tool_sequence, sequence_text

TOOL_NAME = "bindi.sequence_designer"
TOOL_VERSION = "1.1.0"
EVIDENCE_LEVEL = "sequence_level_not_structure_validated"

DEFAULT_BINDER_SEED = (
    "EVQLVESGGGLVQPGGSLRLSAAASGFTFSDYAMSWVRQAPGKGLEWVSAI"
)

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"

# Shared structural priors (both scorers).
_LENGTH_PRIOR_WEIGHT = 0.04
_LENGTH_PRIOR_CENTER = 48
_CYS_PENALTY = 2.5
_PROLINE_IN_WINDOW_PENALTY = 0.15

_CHARGE: dict[str, float] = {
    "D": -1.0,
    "E": -1.0,
    "K": 1.0,
    "R": 1.0,
    "H": 0.25,
}

# --- class_bucket_v1 (frozen; reproduces evidence v1) ---------------------------------

_HYDRO_CLASS: dict[str, str] = {}
for _aa in "IVLMWF":
    _HYDRO_CLASS[_aa] = "hydrophobic"
for _aa in "STNQ":
    _HYDRO_CLASS[_aa] = "polar"
for _aa in "DE":
    _HYDRO_CLASS[_aa] = "acidic"
for _aa in "KRH":
    _HYDRO_CLASS[_aa] = "basic"
for _aa in "AGP":
    _HYDRO_CLASS[_aa] = "small"
for _aa in "CY":
    _HYDRO_CLASS[_aa] = "special"

_CLASS_COMPAT: dict[tuple[str, str], float] = {
    ("hydrophobic", "hydrophobic"): 1.0,
    ("hydrophobic", "small"): 0.35,
    ("polar", "polar"): 0.5,
    ("polar", "charged"): 0.25,
    ("acidic", "basic"): 1.0,
    ("small", "small"): 0.15,
}

_HYDRO_COMPLEMENT_WEIGHT_V1 = 0.9
_CHARGE_COMPLEMENT_WEIGHT_V1 = 6.0

# --- blosum_position_v2 -------------------------------------------------------------

_BLOSUM62 = substitution_matrices.load("BLOSUM62")
BLOSUM62_COEFF = 1.0
CHARGE_COMPLEMENT_COEFF_V2 = 3.0

_ANCHOR_WEIGHTS_LEN9: dict[int, float] = {1: 2.0, 2: 1.75, 8: 2.5}


def _position_weights(peptide_len: int) -> list[float]:
    if peptide_len == 9:
        return [_ANCHOR_WEIGHTS_LEN9.get(i, 1.0) for i in range(peptide_len)]
    return [1.0] * peptide_len


def _blosum62(a: str, b: str) -> float:
    return float(_BLOSUM62[a, b])


def _class_compat(pep_res: str, bind_res: str) -> float:
    pep_class = _HYDRO_CLASS.get(pep_res, "small")
    bind_class = _HYDRO_CLASS.get(bind_res, "small")
    if pep_class in {"acidic", "basic"} or bind_class in {"acidic", "basic"}:
        pep_bucket = "charged" if pep_class in {"acidic", "basic"} else pep_class
        bind_bucket = "charged" if bind_class in {"acidic", "basic"} else bind_class
        return _CLASS_COMPAT.get(
            (pep_bucket, bind_bucket), _CLASS_COMPAT.get((bind_bucket, pep_bucket), 0.0)
        )
    return _CLASS_COMPAT.get(
        (pep_class, bind_class), _CLASS_COMPAT.get((bind_class, pep_class), 0.0)
    )


def _sliding_window_best(
    peptide: str,
    binder: str,
    *,
    window_score_fn: Callable[[str, str, int], float],
) -> tuple[float, int]:
    best_window = float("-inf")
    best_prolines = 0
    for offset in range(len(binder) - len(peptide) + 1):
        window_score = window_score_fn(peptide, binder, offset)
        prolines = sum(
            1 for i in range(len(peptide)) if binder[offset + i] == "P"
        )
        if window_score > best_window:
            best_window = window_score
            best_prolines = prolines
    return best_window, best_prolines


def _structural_adjustments(binder: str, prolines_in_best_window: int) -> float:
    length_term = -_LENGTH_PRIOR_WEIGHT * abs(len(binder) - _LENGTH_PRIOR_CENTER)
    cys_term = -_CYS_PENALTY * binder.count("C")
    proline_term = -_PROLINE_IN_WINDOW_PENALTY * prolines_in_best_window
    return length_term + cys_term + proline_term


def score_binder_peptide_class_bucket_v1(peptide: str, binder: str) -> float:
    """Class-bucket v1 scorer (PAIR-HARD blind to M→V); kept for reproduction only."""
    peptide = peptide.upper()
    binder = binder.upper()
    if not peptide or not binder or len(binder) < len(peptide):
        return float("-inf")

    def window_score(pep: str, bind: str, offset: int) -> float:
        total = 0.0
        for index, pep_res in enumerate(pep):
            bind_res = bind[offset + index]
            total += _HYDRO_COMPLEMENT_WEIGHT_V1 * _class_compat(pep_res, bind_res)
            q_pep = _CHARGE.get(pep_res, 0.0)
            q_bind = _CHARGE.get(bind_res, 0.0)
            if q_pep != 0.0 and q_bind != 0.0:
                total += _CHARGE_COMPLEMENT_WEIGHT_V1 * (-q_pep * q_bind)
        return total

    best_window, best_prolines = _sliding_window_best(peptide, binder, window_score_fn=window_score)
    return best_window + _structural_adjustments(binder, best_prolines)


def score_binder_peptide_blosum_position_v2(peptide: str, binder: str) -> float:
    """BLOSUM62 + position-weighted sliding window (substitution-aware)."""
    peptide = peptide.upper()
    binder = binder.upper()
    if not peptide or not binder or len(binder) < len(peptide):
        return float("-inf")

    weights = _position_weights(len(peptide))

    def window_score(pep: str, bind: str, offset: int) -> float:
        total = 0.0
        for index, pep_res in enumerate(pep):
            bind_res = bind[offset + index]
            total += BLOSUM62_COEFF * weights[index] * _blosum62(pep_res, bind_res)
            q_pep = _CHARGE.get(pep_res, 0.0)
            q_bind = _CHARGE.get(bind_res, 0.0)
            if q_pep != 0.0 and q_bind != 0.0:
                total += CHARGE_COMPLEMENT_COEFF_V2 * (-q_pep * q_bind)
        return total

    best_window, best_prolines = _sliding_window_best(peptide, binder, window_score_fn=window_score)
    return best_window + _structural_adjustments(binder, best_prolines)


# Back-compat alias used in committed v1 evidence metadata.
score_binder_peptide = score_binder_peptide_class_bucket_v1

ScorerFn = Callable[[str, str], float]

SCORER_REGISTRY: dict[str, dict[str, Any]] = {
    "class_bucket_v1": {
        "version": "1.0.0",
        "callable": score_binder_peptide_class_bucket_v1,
        "scoring_function": "bindi.design_engine.score_binder_peptide_class_bucket_v1",
        "evidence_schema": "bindi.sequence_design_repeat_noise.v1",
        "default_evidence_path": "evidence/sequence_design_repeat_noise.v1.json",
    },
    "blosum_position_v2": {
        "version": "2.0.0",
        "callable": score_binder_peptide_blosum_position_v2,
        "scoring_function": "bindi.design_engine.score_binder_peptide_blosum_position_v2",
        "evidence_schema": "bindi.sequence_design_repeat_noise.v2",
        "default_evidence_path": "evidence/sequence_design_repeat_noise.v2.json",
    },
}

DEFAULT_SCORER_ID = "blosum_position_v2"

# Legacy constant: default export name for the old single-scorer era.
SCORING_FUNCTION = SCORER_REGISTRY[DEFAULT_SCORER_ID]["scoring_function"]


def resolve_scorer(scorer_id: str | None = None) -> tuple[str, ScorerFn, dict[str, Any]]:
    sid = scorer_id or DEFAULT_SCORER_ID
    if sid not in SCORER_REGISTRY:
        raise ValueError(
            f"unknown scorer_id {sid!r}; choose from {sorted(SCORER_REGISTRY)}"
        )
    meta = SCORER_REGISTRY[sid]
    return sid, meta["callable"], meta


def _mutate_once(sequence: str, rng: random.Random) -> str:
    position = rng.randrange(len(sequence))
    choices = [aa for aa in AMINO_ACIDS if aa != sequence[position]]
    replacement = rng.choice(choices)
    return sequence[:position] + replacement + sequence[position + 1 :]


def run_mutational_search(
    mutant_peptide: str,
    *,
    seed: int,
    starter_binder: str | None = None,
    steps: int = 600,
    pool_size: int = 24,
    final_count: int = 3,
    scorer_id: str | None = None,
) -> list[dict[str, Any]]:
    """Stochastic mutational search scored by the selected scorer."""
    sid, score_fn, meta = resolve_scorer(scorer_id)

    rng = random.Random(seed)
    starter = (starter_binder or DEFAULT_BINDER_SEED).upper()
    mutant_peptide = mutant_peptide.upper()
    pool: dict[str, float] = {starter: score_fn(mutant_peptide, starter)}
    current = starter
    for _ in range(steps):
        candidate = _mutate_once(current, rng)
        pool[candidate] = score_fn(mutant_peptide, candidate)
        current = candidate
        if len(pool) > pool_size * 4:
            ranked = sorted(pool.items(), key=lambda item: item[1], reverse=True)[:pool_size]
            pool = dict(ranked)

    ranked_final = sorted(pool.items(), key=lambda item: item[1], reverse=True)[:final_count]
    designs: list[dict[str, Any]] = []
    scoring_function = meta["scoring_function"]

    for index, (sequence, score) in enumerate(ranked_final, start=1):
        designs.append(
            {
                "design": f"seq_design_{index}",
                "sequence": sequence,
                "sequence_field": design_tool_sequence(
                    sequence,
                    tool=TOOL_NAME,
                    scoring_function=scoring_function,
                    tool_version=TOOL_VERSION,
                    evidence_level=EVIDENCE_LEVEL,
                ),
                "mutant_complex_score": f"{score:.6f}",
                "sequence_score": score,
                "scorer_id": sid,
            }
        )
    return designs


def attach_wild_type_margins(
    designs: list[dict[str, Any]],
    *,
    mutant_peptide: str,
    wild_type_peptide: str,
    scorer_id: str | None = None,
) -> list[dict[str, Any]]:
    _, score_fn, _ = resolve_scorer(scorer_id)
    for design in designs:
        sequence = design["sequence"]
        mutant_score = float(
            design.get("sequence_score", score_fn(mutant_peptide, sequence))
        )
        wild_score = score_fn(wild_type_peptide, sequence)
        margin = mutant_score - wild_score
        design["wild_type_complex_score"] = f"{wild_score:.6f}"
        design["mutant_complex_score"] = f"{mutant_score:.6f}"
        design["margin"] = f"{margin:.6f}"
        field = dict(design["sequence_field"])
        field["mutant_complex_score"] = design["mutant_complex_score"]
        field["wild_type_complex_score"] = design["wild_type_complex_score"]
        field["margin"] = design["margin"]
        field["evidence_level"] = EVIDENCE_LEVEL
        design["sequence_field"] = field
    return designs


def write_ranked_table(path: Path, designs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, design in enumerate(designs, start=1):
        score = float(design.get("sequence_score", 0.0))
        rows.append(
            {
                "design": design["design"],
                "rank": str(index),
                "i_pTM": f"{score:.6f}",
                "Binder_Sequence": design["sequence"],
            }
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["design", "rank", "i_pTM", "Binder_Sequence"]
        )
        writer.writeheader()
        writer.writerows(rows)


def benchmark_pair_from_case(case: dict[str, Any], pair_id: str) -> dict[str, Any]:
    pairs = case.get("benchmark_pairs") or []
    for entry in pairs:
        if entry.get("pair_id") == pair_id:
            return entry
    raise KeyError(f"benchmark pair {pair_id!r} not found in case record")


def pair_peptides(pair: dict[str, Any]) -> tuple[str, str]:
    neo = pair.get("neoantigen") or {}
    mutant = sequence_text(neo.get("mutant_peptide")) or ""
    wild = sequence_text(neo.get("wild_type_peptide")) or ""
    if not mutant or not wild:
        raise ValueError(f"pair {pair.get('pair_id')} missing mutant or wild_type peptide")
    return mutant, wild


def top_margin_for_pair(
    mutant_peptide: str,
    wild_type_peptide: str,
    *,
    seed: int,
    final_count: int = 3,
    scorer_id: str | None = None,
) -> dict[str, Any]:
    designs = run_mutational_search(
        mutant_peptide, seed=seed, final_count=final_count, scorer_id=scorer_id
    )
    attach_wild_type_margins(
        designs,
        mutant_peptide=mutant_peptide,
        wild_type_peptide=wild_type_peptide,
        scorer_id=scorer_id,
    )
    best = max(designs, key=lambda row: float(row["margin"]))
    return {
        "seed": seed,
        "top_margin": float(best["margin"]),
        "top_mutant_score": float(best["mutant_complex_score"]),
        "top_wild_type_score": float(best["wild_type_complex_score"]),
        "design": best["design"],
    }


def repeat_noise_distribution(
    mutant_peptide: str,
    wild_type_peptide: str,
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    scorer_id: str | None = None,
) -> dict[str, Any]:
    margins = []
    per_seed: list[dict[str, Any]] = []
    for offset in range(repeats):
        seed = seed_start + offset
        row = top_margin_for_pair(
            mutant_peptide, wild_type_peptide, seed=seed, scorer_id=scorer_id
        )
        margins.append(row["top_margin"])
        per_seed.append(row)
    spread = statistics.pstdev(margins) if len(margins) > 1 else 0.0
    return {
        "n": len(margins),
        "mean": statistics.fmean(margins),
        "std": spread,
        "min": min(margins),
        "max": max(margins),
        "per_seed_margins": margins,
        "per_seed": per_seed,
        "spread": spread,
    }


MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF = 2.0


def margin_threshold_from_spread(spread: float) -> float:
    return MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF * spread


def evaluate_easy_pair_margin(distribution: dict[str, Any]) -> dict[str, Any]:
    threshold = margin_threshold_from_spread(distribution["spread"])
    mean_margin = distribution["mean"]
    separates = abs(mean_margin) >= threshold and mean_margin > 0
    return {
        "mean_margin": mean_margin,
        "spread": distribution["spread"],
        "threshold_pending_signoff": threshold,
        "threshold_multiplier": MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF,
        "separates_at_least_threshold": separates,
        "threshold_status": "not_established",
        "clears_margin_threshold": False,
        "note": (
            "Sequence-level scorer: does not establish the immunology margin threshold (room BIO "
            "USE structure). Threshold constant is pending chief-scientist sign-off; multiplier "
            "is sourced from measured repeat-run spread on the same pair."
        ),
    }


def run_repeat_noise_report(
    case: dict[str, Any],
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    scorer_id: str | None = None,
) -> dict[str, Any]:
    sid, _, meta = resolve_scorer(scorer_id)
    report: dict[str, Any] = {
        "schema": meta["evidence_schema"],
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "scorer_id": sid,
        "scorer_version": meta["version"],
        "scoring_function": meta["scoring_function"],
        "evidence_level": EVIDENCE_LEVEL,
        "case_id": case.get("case_id"),
        "wet_record_id": case.get("wet_record_id"),
        "repeats": repeats,
        "seed_start": seed_start,
        "margin_threshold_status": "not_established",
        "pairs": {},
    }
    for pair_id in ("PAIR-HARD", "PAIR-EASY"):
        pair = benchmark_pair_from_case(case, pair_id)
        mutant, wild = pair_peptides(pair)
        distribution = repeat_noise_distribution(
            mutant, wild, repeats=repeats, seed_start=seed_start, scorer_id=sid
        )
        entry: dict[str, Any] = {
            "pair_id": pair_id,
            "description": pair.get("description"),
            "mutant_peptide": mutant,
            "wild_type_peptide": wild,
            "distribution": {
                "n": distribution["n"],
                "mean": distribution["mean"],
                "std": distribution["std"],
                "min": distribution["min"],
                "max": distribution["max"],
                "per_seed_margins": distribution["per_seed_margins"],
            },
        }
        if pair_id == "PAIR-EASY":
            entry["margin_vs_wild_type"] = evaluate_easy_pair_margin(distribution)
        else:
            entry["margin_vs_wild_type"] = {
                "mean_margin": distribution["mean"],
                "spread": distribution["spread"],
                "note": "PAIR-HARD: spread reported only; no threshold attached.",
            }
        report["pairs"][pair_id] = entry
    return report


def write_repeat_noise_evidence(
    case: dict[str, Any],
    output_path: Path,
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    scorer_id: str | None = None,
) -> dict[str, Any]:
    sid, _, meta = resolve_scorer(scorer_id)
    report = run_repeat_noise_report(
        case, repeats=repeats, seed_start=seed_start, scorer_id=sid
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def default_evidence_path(scorer_id: str | None = None) -> Path:
    _, _, meta = resolve_scorer(scorer_id)
    return Path(meta["default_evidence_path"])
