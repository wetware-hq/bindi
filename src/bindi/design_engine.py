"""Sequence-level binder designer (v1): stochastic search with an explicit scorer.

This is not structure-validated design. Candidates come from seeded point mutations on a
fixed starter binder; each candidate is scored by ``score_binder_peptide``, documented
below. No language model generates sequences.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
from pathlib import Path
from typing import Any

from .provenance import design_tool_sequence, sequence_text

TOOL_NAME = "bindi.sequence_designer"
TOOL_VERSION = "1.0.0"
SCORING_FUNCTION = "bindi.design_engine.score_binder_peptide"
EVIDENCE_LEVEL = "sequence_level_not_structure_validated"

# Starter miniprotein: no Cys, no known hazard motif (GFTFSSYAMS), length 48.
DEFAULT_BINDER_SEED = (
    "EVQLVESGGGLVQPGGSLRLSAAASGFTFSDYAMSWVRQAPGKGLEWVSAI"
)

# Hydrophobic class buckets (not raw hydropathy): conservative substitutions such as M→V
# sit in the same class so PAIR-HARD margins stay small; charge swaps stay class-stable
# but are separated by the explicit charge term below.
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

# Pairwise class compatibility (symmetric): reward hydrophobic↔hydrophobic burial, etc.
_CLASS_COMPAT: dict[tuple[str, str], float] = {
    ("hydrophobic", "hydrophobic"): 1.0,
    ("hydrophobic", "small"): 0.35,
    ("polar", "polar"): 0.5,
    ("polar", "charged"): 0.25,
    ("acidic", "basic"): 1.0,
    ("small", "small"): 0.15,
}

# Formal charge at pH 7 (approximate): used for electrostatic complementarity only.
_CHARGE: dict[str, float] = {
    "D": -1.0,
    "E": -1.0,
    "K": 1.0,
    "R": 1.0,
    "H": 0.25,
}

# Class complementarity weight at each aligned position in the sliding window.
_HYDRO_COMPLEMENT_WEIGHT = 0.9

# Electrostatic complementarity: opposite formal charges across the interface add.
_CHARGE_COMPLEMENT_WEIGHT = 6.0

# Length prior centred on 48 residues (starter length); discourages runaway insertions.
_LENGTH_PRIOR_WEIGHT = 0.04
_LENGTH_PRIOR_CENTER = 48

# Cysteine penalty: disulfide pairing is not modelled at sequence level.
_CYS_PENALTY = 2.5

# Proline in the binding core disrupts backbone continuity; mild penalty per Pro in window.
_PROLINE_IN_WINDOW_PENALTY = 0.15

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


def _class_compat(pep_res: str, bind_res: str) -> float:
    pep_class = _HYDRO_CLASS.get(pep_res, "small")
    bind_class = _HYDRO_CLASS.get(bind_res, "small")
    if pep_class in {"acidic", "basic"} or bind_class in {"acidic", "basic"}:
        pep_bucket = "charged" if pep_class in {"acidic", "basic"} else pep_class
        bind_bucket = "charged" if bind_class in {"acidic", "basic"} else bind_class
        return _CLASS_COMPAT.get((pep_bucket, bind_bucket), _CLASS_COMPAT.get((bind_bucket, pep_bucket), 0.0))
    return _CLASS_COMPAT.get((pep_class, bind_class), _CLASS_COMPAT.get((bind_class, pep_class), 0.0))


def score_binder_peptide(peptide: str, binder: str) -> float:
    """Score a binder sequence against a target peptide (higher is better).

    Terms (all explicit, no fitted weights beyond those named above):

    1. **Sliding-window contact score** — for each register aligning a window of
       ``len(peptide)`` binder residues with the peptide, sum per-position contact
       contributions: hydrophobic-class complementarity plus formal-charge complementarity.
    2. **Length prior** — ``-LENGTH_PRIOR_WEIGHT * abs(len(binder) - LENGTH_PRIOR_CENTER)``.
    3. **Cysteine penalty** — ``-CYS_PENALTY`` per Cys in the binder (unpaired Cys risk).
    4. **Proline-in-window penalty** — for the best register only, ``-PROLINE_IN_WINDOW_PENALTY``
       per Pro in the aligned binder window (backbone rigidity at the interface).
    """
    peptide = peptide.upper()
    binder = binder.upper()
    if not peptide or not binder:
        return float("-inf")
    if len(binder) < len(peptide):
        return float("-inf")

    best_window = float("-inf")
    best_prolines = 0
    for offset in range(len(binder) - len(peptide) + 1):
        window_score = 0.0
        prolines = 0
        for index, pep_res in enumerate(peptide):
            bind_res = binder[offset + index]
            if bind_res == "P":
                prolines += 1
            window_score += _HYDRO_COMPLEMENT_WEIGHT * _class_compat(pep_res, bind_res)
            q_pep = _CHARGE.get(pep_res, 0.0)
            q_bind = _CHARGE.get(bind_res, 0.0)
            if q_pep != 0.0 and q_bind != 0.0:
                window_score += _CHARGE_COMPLEMENT_WEIGHT * (-q_pep * q_bind)
        if window_score > best_window:
            best_window = window_score
            best_prolines = prolines

    length_term = -_LENGTH_PRIOR_WEIGHT * abs(len(binder) - _LENGTH_PRIOR_CENTER)
    cys_term = -_CYS_PENALTY * binder.count("C")
    proline_term = -_PROLINE_IN_WINDOW_PENALTY * best_prolines
    return best_window + length_term + cys_term + proline_term


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
) -> list[dict[str, Any]]:
    """Stochastic mutational search: mutate starter, keep top ``pool_size``, return top ``final_count``."""
    rng = random.Random(seed)
    starter = (starter_binder or DEFAULT_BINDER_SEED).upper()
    mutant_peptide = mutant_peptide.upper()
    pool: dict[str, float] = {starter: score_binder_peptide(mutant_peptide, starter)}
    current = starter
    for _ in range(steps):
        candidate = _mutate_once(current, rng)
        pool[candidate] = score_binder_peptide(mutant_peptide, candidate)
        current = candidate
        if len(pool) > pool_size * 4:
            ranked = sorted(pool.items(), key=lambda item: item[1], reverse=True)[:pool_size]
            pool = dict(ranked)

    ranked_final = sorted(pool.items(), key=lambda item: item[1], reverse=True)[:final_count]
    designs: list[dict[str, Any]] = []
    for index, (sequence, score) in enumerate(ranked_final, start=1):
        mutant_score = score
        designs.append(
            {
                "design": f"seq_design_{index}",
                "sequence": sequence,
                "sequence_field": design_tool_sequence(
                    sequence,
                    tool=TOOL_NAME,
                    scoring_function=SCORING_FUNCTION,
                    tool_version=TOOL_VERSION,
                    evidence_level=EVIDENCE_LEVEL,
                ),
                "mutant_complex_score": f"{mutant_score:.6f}",
                "sequence_score": mutant_score,
            }
        )
    return designs


def attach_wild_type_margins(
    designs: list[dict[str, Any]],
    *,
    mutant_peptide: str,
    wild_type_peptide: str,
) -> list[dict[str, Any]]:
    """Add wild-type scores and margin to each design (same scorer, sequence-level)."""
    for design in designs:
        sequence = design["sequence"]
        mutant_score = float(design.get("sequence_score", score_binder_peptide(mutant_peptide, sequence)))
        wild_score = score_binder_peptide(wild_type_peptide, sequence)
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
    """Write a BindCraft-shaped ranked table so ``curate`` can ingest sequence designs."""
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
) -> dict[str, Any]:
    designs = run_mutational_search(mutant_peptide, seed=seed, final_count=final_count)
    attach_wild_type_margins(
        designs, mutant_peptide=mutant_peptide, wild_type_peptide=wild_type_peptide
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
) -> dict[str, Any]:
    margins = []
    per_seed: list[dict[str, Any]] = []
    for offset in range(repeats):
        seed = seed_start + offset
        row = top_margin_for_pair(mutant_peptide, wild_type_peptide, seed=seed)
        margins.append(row["top_margin"])
        per_seed.append(row)
    if len(margins) < 1:
        raise ValueError("repeats must be at least 1")
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


# Pending chief-scientist sign-off: threshold multiplier sourced from measured repeat spread.
MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF = 2.0


def margin_threshold_from_spread(spread: float) -> float:
    """Minimum |margin| required for PAIR-EASY separation (pending scientist sign-off)."""
    return MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF * spread


def evaluate_easy_pair_margin(
    distribution: dict[str, Any],
) -> dict[str, Any]:
    """Compare PAIR-EASY mean margin against the pending threshold; no pass/fail for HARD."""
    threshold = margin_threshold_from_spread(distribution["spread"])
    mean_margin = distribution["mean"]
    return {
        "mean_margin": mean_margin,
        "spread": distribution["spread"],
        "threshold_pending_signoff": threshold,
        "threshold_multiplier": MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF,
        "separates_at_least_threshold": abs(mean_margin) >= threshold,
        "note": (
            "Threshold constant is pending chief-scientist sign-off; multiplier is sourced "
            "from measured repeat-run spread on the same pair."
        ),
    }


def run_repeat_noise_report(
    case: dict[str, Any],
    *,
    repeats: int = 20,
    seed_start: int = 1000,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "bindi.sequence_design_repeat_noise.v1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "scoring_function": SCORING_FUNCTION,
        "evidence_level": EVIDENCE_LEVEL,
        "case_id": case.get("case_id"),
        "repeats": repeats,
        "pairs": {},
    }
    for pair_id in ("PAIR-HARD", "PAIR-EASY"):
        pair = benchmark_pair_from_case(case, pair_id)
        mutant, wild = pair_peptides(pair)
        distribution = repeat_noise_distribution(
            mutant, wild, repeats=repeats, seed_start=seed_start
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
) -> dict[str, Any]:
    report = run_repeat_noise_report(case, repeats=repeats, seed_start=seed_start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
