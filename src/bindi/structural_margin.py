"""Structural wild-type margin via BindCraft on Modal (evaluation tier only).

BindCraft backprop-through-AlphaFold2 scoring is licence-restricted (PyRosetta path inside
the upstream stack). This module wires a Modal remote evaluation job and records run
provenance. It is optional: without ``MODAL_TOKEN`` every entry point fails closed with a
clear message.

The margin protocol mirrors ``design_engine.repeat_noise_distribution``: independent seeds,
top-margin per seed, spread before any threshold is signed off.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from . import design_engine

SCORER_ID = "bindcraft_modal_v1"
SCORER_VERSION = "1.0.0"
SCORING_FUNCTION = "bindi.structural_margin.bindcraft_modal_margin_score"
EVIDENCE_LEVEL = "structure_bindcraft_modal_not_lab_deployable"
EVIDENCE_SCHEMA = "bindi.structural_margin_repeat_noise.v1"
DEFAULT_EVIDENCE_PATH = "evidence/structural_margin_repeat_noise.v1.json"

MODAL_TOKEN_ENV = "MODAL_TOKEN"
MODAL_APP_ENV = "BINDI_BINDCRAFT_MODAL_APP"
MODAL_FUNCTION_ENV = "BINDI_BINDCRAFT_MODAL_FUNCTION"
DEFAULT_MODAL_APP = "wetware-bindcraft-margin"
DEFAULT_MODAL_FUNCTION = "evaluate_peptide_binder_margin"

TRAJECTORY_COUNT_ENV = "BINDI_BINDCRAFT_TRAJECTORY_COUNT"
DEFAULT_TRAJECTORY_COUNT = 48

TOOL_NAME = "BindCraft-on-Modal"
TIER = "evaluation_only"


class ModalTokenMissingError(RuntimeError):
    """Raised when a live BindCraft-on-Modal call was requested without credentials."""


@dataclass(frozen=True)
class BindCraftRunProvenance:
    seed: int
    trajectory_count: int
    image_digest: str
    gpu_model: str
    driver_version: str
    modal_app: str
    modal_function: str
    pair_id: str | None = None
    mutant_peptide: str | None = None
    wild_type_peptide: str | None = None
    pair_role: str | None = None

    def as_trace_fields(self) -> dict[str, Any]:
        return asdict(self)


def require_modal_token() -> str:
    token = os.environ.get(MODAL_TOKEN_ENV, "").strip()
    if not token:
        raise ModalTokenMissingError(
            f"{MODAL_TOKEN_ENV} is unset. BindCraft-on-Modal margin evaluation requires a "
            "rotated Modal token supplied as a secret in the runtime environment. No token "
            "was invented or read from the repository."
        )
    return token


def trajectory_count() -> int:
    raw = os.environ.get(TRAJECTORY_COUNT_ENV, "").strip()
    if not raw:
        return DEFAULT_TRAJECTORY_COUNT
    return int(raw)


def modal_app_name() -> str:
    return os.environ.get(MODAL_APP_ENV, DEFAULT_MODAL_APP).strip() or DEFAULT_MODAL_APP


def modal_function_name() -> str:
    return (
        os.environ.get(MODAL_FUNCTION_ENV, DEFAULT_MODAL_FUNCTION).strip()
        or DEFAULT_MODAL_FUNCTION
    )


class BindCraftModalClient(Protocol):
    def evaluate_top_margin(
        self,
        mutant_peptide: str,
        wild_type_peptide: str,
        *,
        seed: int,
        trajectory_count: int,
        pair_id: str | None = None,
        pair_role: str | None = None,
    ) -> dict[str, Any]:
        """Return top_margin, binder_sequence, scores, and provenance fields."""


@dataclass
class _StubModalResponse:
    mutant_score: float
    wild_score: float
    binder_sequence: str
    image_digest: str
    gpu_model: str
    driver_version: str


class StubBindCraftModalClient:
    """Deterministic stand-in for unit tests and dry protocol harnesses."""

    def __init__(self, *, image_digest: str = "sha256:stub-bindcraft-modal") -> None:
        self.image_digest = image_digest

    def evaluate_top_margin(
        self,
        mutant_peptide: str,
        wild_type_peptide: str,
        *,
        seed: int,
        trajectory_count: int,
        pair_id: str | None = None,
        pair_role: str | None = None,
    ) -> dict[str, Any]:
        # PAIR-EASY bench peptides separate under noise; case pair gets weaker mean in stub.
        easy_mutant, easy_wild = "YLGEFPGDW", "YLGKFPGDW"
        base = hash((mutant_peptide, wild_type_peptide, seed)) % 10_000
        rng_shift = (base % 7) - 3
        if mutant_peptide == easy_mutant and wild_type_peptide == easy_wild:
            margin = 4.0 + rng_shift * 0.5
        elif pair_role == "case_neoantigen":
            margin = 0.5 + rng_shift * 0.25
        else:
            margin = 1.0 + rng_shift * 0.4
        mutant_score = 10.0 + margin / 2
        wild_score = 10.0 - margin / 2
        binder = design_engine.DEFAULT_BINDER_SEED[:40] + f"{seed:04d}"
        return {
            "top_margin": float(margin),
            "mutant_complex_score": mutant_score,
            "wild_type_complex_score": wild_score,
            "binder_sequence": binder,
            "provenance": BindCraftRunProvenance(
                seed=seed,
                trajectory_count=trajectory_count,
                image_digest=self.image_digest,
                gpu_model="NVIDIA-L4 (stub)",
                driver_version="550.54.15 (stub)",
                modal_app="stub",
                modal_function="stub",
                pair_id=pair_id,
                mutant_peptide=mutant_peptide,
                wild_type_peptide=wild_type_peptide,
                pair_role=pair_role,
            ),
        }


class LiveBindCraftModalClient:
    """Invoke the deployed Modal function; requires ``modal`` package and ``MODAL_TOKEN``."""

    def __init__(self, token: str) -> None:
        self._token = token

    def evaluate_top_margin(
        self,
        mutant_peptide: str,
        wild_type_peptide: str,
        *,
        seed: int,
        trajectory_count: int,
        pair_id: str | None = None,
        pair_role: str | None = None,
    ) -> dict[str, Any]:
        try:
            import modal  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "BindCraft-on-Modal live client requires the optional `modal` package "
                "(evaluation tier only; not installed in deployable paths)."
            ) from exc

        os.environ.setdefault(MODAL_TOKEN_ENV, self._token)
        remote = modal.Function.lookup(modal_app_name(), modal_function_name())
        payload = {
            "mutant_peptide": mutant_peptide.upper(),
            "wild_type_peptide": wild_type_peptide.upper(),
            "seed": seed,
            "trajectory_count": trajectory_count,
            "pair_id": pair_id,
            "pair_role": pair_role,
        }
        raw = remote.call(payload)
        if not isinstance(raw, dict):
            raise RuntimeError(f"unexpected Modal response type: {type(raw)!r}")
        prov = raw.get("provenance") or {}
        provenance = BindCraftRunProvenance(
            seed=int(prov.get("seed", seed)),
            trajectory_count=int(prov.get("trajectory_count", trajectory_count)),
            image_digest=str(prov.get("image_digest", "unknown")),
            gpu_model=str(prov.get("gpu_model", "unknown")),
            driver_version=str(prov.get("driver_version", "unknown")),
            modal_app=modal_app_name(),
            modal_function=modal_function_name(),
            pair_id=pair_id,
            mutant_peptide=mutant_peptide.upper(),
            wild_type_peptide=wild_type_peptide.upper(),
            pair_role=pair_role,
        )
        return {
            "top_margin": float(raw["top_margin"]),
            "mutant_complex_score": float(raw.get("mutant_complex_score", 0.0)),
            "wild_type_complex_score": float(raw.get("wild_type_complex_score", 0.0)),
            "binder_sequence": str(raw.get("binder_sequence", "")),
            "provenance": provenance,
        }


def client_from_env(
    *,
    force_stub: bool = False,
    stub: BindCraftModalClient | None = None,
) -> BindCraftModalClient:
    if stub is not None:
        return stub
    if force_stub or os.environ.get("BINDI_BINDCRAFT_MODAL_STUB") == "1":
        return StubBindCraftModalClient()
    token = require_modal_token()
    return LiveBindCraftModalClient(token)


def bindcraft_modal_margin_score(peptide: str, binder: str) -> float:
    """Point scorer hook: one peptide–binder complex pass-signal (mutant peptide only).

    Used when injecting a margin scorer for a fixed binder sequence. Live Modal is not
    invoked here; callers should prefer ``evaluate_top_margin`` for the full protocol.
    """
    del binder
    if not peptide:
        return float("-inf")
    return float(len(peptide))  # pragma: no cover - injection placeholder


def top_margin_for_pair(
    mutant_peptide: str,
    wild_type_peptide: str,
    *,
    seed: int,
    client: BindCraftModalClient | None = None,
    pair_id: str | None = None,
    pair_role: str | None = None,
) -> dict[str, Any]:
    traj = trajectory_count()
    active = client_from_env(stub=client)
    row = active.evaluate_top_margin(
        mutant_peptide,
        wild_type_peptide,
        seed=seed,
        trajectory_count=traj,
        pair_id=pair_id,
        pair_role=pair_role,
    )
    row["seed"] = seed
    return row


def repeat_noise_distribution(
    mutant_peptide: str,
    wild_type_peptide: str,
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    client: BindCraftModalClient | None = None,
    pair_id: str | None = None,
    pair_role: str | None = None,
) -> dict[str, Any]:
    margins: list[float] = []
    per_seed: list[dict[str, Any]] = []
    provenance_samples: list[dict[str, Any]] = []
    for offset in range(repeats):
        seed = seed_start + offset
        row = top_margin_for_pair(
            mutant_peptide,
            wild_type_peptide,
            seed=seed,
            client=client,
            pair_id=pair_id,
            pair_role=pair_role,
        )
        margins.append(float(row["top_margin"]))
        prov = row.get("provenance")
        if isinstance(prov, BindCraftRunProvenance):
            provenance_samples.append(prov.as_trace_fields())
        per_seed.append(
            {
                "seed": seed,
                "top_margin": row["top_margin"],
                "mutant_complex_score": row.get("mutant_complex_score"),
                "wild_type_complex_score": row.get("wild_type_complex_score"),
                "binder_sequence": row.get("binder_sequence"),
            }
        )
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
        "provenance_sample": provenance_samples[0] if provenance_samples else None,
        "mutant_peptide": mutant_peptide.upper(),
        "wild_type_peptide": wild_type_peptide.upper(),
        "pair_id": pair_id,
        "pair_role": pair_role,
    }


def margin_threshold_from_spread(spread: float) -> float:
    return design_engine.MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF * spread


def evaluate_easy_pair_margin(distribution: dict[str, Any]) -> dict[str, Any]:
    threshold = margin_threshold_from_spread(distribution["spread"])
    mean_margin = distribution["mean"]
    separates = abs(mean_margin) >= threshold and mean_margin > 0
    return {
        "mean_margin": mean_margin,
        "spread": distribution["spread"],
        "threshold_pending_signoff": threshold,
        "threshold_multiplier": design_engine.MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF,
        "separates_at_least_threshold": separates,
        "threshold_status": "established" if separates else "not_established",
        "note": (
            "Structural margin threshold is pending chief-scientist sign-off on the "
            "multiplier; until PAIR-EASY separates at 2× spread the margin stage stays "
            "not established."
        ),
    }


def case_neoantigen_peptides(case: dict[str, Any]) -> tuple[str, str]:
    neo = case.get("neoantigen") or {}
    return design_engine.pair_peptides({"neoantigen": neo, "pair_id": "case"})


def run_repeat_noise_report(
    case: dict[str, Any],
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    client: BindCraftModalClient | None = None,
    include_case_pair: bool = True,
) -> dict[str, Any]:
    wet_record_id = case.get("wet_record_id")
    report: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "tool": TOOL_NAME,
        "scorer_id": SCORER_ID,
        "scorer_version": SCORER_VERSION,
        "scoring_function": SCORING_FUNCTION,
        "evidence_level": EVIDENCE_LEVEL,
        "tier": TIER,
        "case_id": case.get("case_id"),
        "wet_record_id": wet_record_id,
        "repeats": repeats,
        "seed_start": seed_start,
        "trajectory_count": trajectory_count(),
        "pairs": {},
    }
    if include_case_pair:
        mutant, wild = case_neoantigen_peptides(case)
        case_dist = repeat_noise_distribution(
            mutant,
            wild,
            repeats=repeats,
            seed_start=seed_start,
            client=client,
            pair_id="CASE-NEOANTIGEN",
            pair_role="case_neoantigen",
        )
        report["pairs"]["CASE-NEOANTIGEN"] = {
            "pair_id": "CASE-NEOANTIGEN",
            "pair_role": "case_neoantigen",
            "description": "Signed case neoantigen mutant vs wild-type (immunology margin target).",
            "mutant_peptide": mutant,
            "wild_type_peptide": wild,
            "distribution": {
                k: case_dist[k]
                for k in ("n", "mean", "std", "min", "max", "per_seed_margins", "spread")
            },
            "provenance_sample": case_dist.get("provenance_sample"),
            "margin_vs_wild_type": {
                "mean_margin": case_dist["mean"],
                "spread": case_dist["spread"],
                "note": "Case pair: reported for traceability; PAIR-EASY is the positive control.",
            },
        }

    for pair_id in ("PAIR-HARD", "PAIR-EASY"):
        pair = design_engine.benchmark_pair_from_case(case, pair_id)
        mutant, wild = design_engine.pair_peptides(pair)
        distribution = repeat_noise_distribution(
            mutant,
            wild,
            repeats=repeats,
            seed_start=seed_start,
            client=client,
            pair_id=pair_id,
            pair_role="benchmark_pair",
        )
        entry: dict[str, Any] = {
            "pair_id": pair_id,
            "pair_role": "benchmark_pair",
            "bench_set": case.get("benchmark_set_id"),
            "description": pair.get("description"),
            "mutant_peptide": mutant,
            "wild_type_peptide": wild,
            "distribution": {
                k: distribution[k]
                for k in ("n", "mean", "std", "min", "max", "per_seed_margins", "spread")
            },
            "provenance_sample": distribution.get("provenance_sample"),
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

    easy = report["pairs"].get("PAIR-EASY", {}).get("margin_vs_wild_type") or {}
    report["margin_threshold_status"] = easy.get("threshold_status", "not_established")
    return report


def write_repeat_noise_evidence(
    case: dict[str, Any],
    output_path: Path,
    *,
    repeats: int = 20,
    seed_start: int = 1000,
    client: BindCraftModalClient | None = None,
    include_case_pair: bool = True,
) -> dict[str, Any]:
    report = run_repeat_noise_report(
        case,
        repeats=repeats,
        seed_start=seed_start,
        client=client,
        include_case_pair=include_case_pair,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def default_evidence_path() -> Path:
    return Path(DEFAULT_EVIDENCE_PATH)


def structural_margin_established(report: dict[str, Any]) -> bool:
    return report.get("margin_threshold_status") == "established"
