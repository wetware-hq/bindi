"""Build a ranked gallery directory from a BoltzGen campaign folder."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
from pathlib import Path
from typing import Any, Iterable

from . import data, ensemble
from . import boltz as boltz_mod
from .policy import normalize_policy, resolve_boltz_campaign

GALLERY_SCHEMA = "bindi.gallery.v1"

# bindi's only generator path. Must stay deployable (see src/bindi/data/tool_tiers.toml).
DEPLOYABLE_TOOLS = ["BoltzGen"]

RELATIVE_CLAIM = (
    "Relative ranking among the designs in this campaign only. No absolute affinity, "
    "Kd/Ki/IC50, or kcal/mol value is predicted or implied; ranking is never a safety claim."
)
FIXTURE_PATH_STATEMENT = (
    "This run used bundled fixtures with no design compute: it exercises record-keeping "
    "and curation layout only, not binding chemistry. No design in this gallery has been "
    "shown to bind."
)
STRUCTURES_NOT_RUN_REASON = (
    "No structure files were copied into the gallery (none were found under the campaign "
    "folder for the selected designs)."
)
PROVENANCE_STATEMENT = (
    "In-silico hypotheses until confirmed experimentally (affinity, specificity, "
    "developability, and safety; for therapeutics, through the appropriate regulatory path)."
)
ENSEMBLE_BASIS = (
    "Scorer ensemble ('multiverse') consensus ranking across the higher-better metrics "
    "present in this campaign. Relative-only discipline follows Sampson et al., "
    "J Mol Biol 436, 168640, 2024, doi:10.1016/j.jmb.2024.168640 "
    "(data: https://github.com/schrodinger/protein-fep-benchmark)."
)

# The rank column is a pass-signal ordering, not potency. Boltz-2 confidence scores (ipTM etc.)
# reflect predicted complex quality, not measured binding affinity.
RANK_MEANING = (
    "Rank is a pass-signal ordering from Boltz-2/BoltzGen confidence metrics (e.g. design_to_target_iptm), "
    "not an affinity or potency ordering."
)

# ---- Ranking direction registry -------------------------------------------------
# A metric may order the gallery only when its direction is CONFIRMED against the
# upstream source. BindCraft/AlphaFold2 confidence scores are higher-better; the
# interface aligned-error metric is `i_pAE`, normalised to 0-1 and LOWER-better
# (default accept filter Average_i_pAE < 0.35; Pacesa et al. 2025,
# doi:10.1038/s41586-025-09429-6).
HIGHER_BETTER_CONFIRMED = {
    "i_pTM",
    "pTM",
    "pLDDT",
    "Interface_BuriedArea",
    "Interface_Residues",
    "design_to_target_iptm",
    "design_iptm",
    "design_iiptm",
    "design_ptm",
    "design_plddt",
    "quality_score",
}
LOWER_BETTER_CONFIRMED = {
    "i_pAE",
    "pAE",
    "min_design_to_target_pae",
    "design_ipsae_min",
    "design_to_target_ipsae",
}

# `i_pDAE` has NO confirmed direction. It is not a BindCraft output column (that is
# `i_pAE`), bindi computes it nowhere, and bindi's own text calls it an "interface
# confidence metric" (higher-better) while the name reads as an aligned error
# (lower-better). Name and usage conflict and there is no upstream anchor, so the
# direction cannot be confirmed. A gallery ranking requested on an unconfirmed metric
# does NOT guess a sign: candidates are shown unordered with the rank column suppressed
# (Physician's ruling), so a mislabel is surfaced rather than hidden by a sign flip.
UNCONFIRMED_DIRECTION = {"i_pDAE"}

RANK_SUPPRESSED_MEANING = (
    "Rank suppressed: the requested ranking metric has no confirmed direction, so "
    "candidates are shown unordered. Imposing an order would assert a direction the "
    "code cannot justify."
)


def _direction(metric: str) -> int | None:
    """-1 if higher-better, +1 if lower-better (ascending sort), None if unconfirmed."""
    if metric in HIGHER_BETTER_CONFIRMED:
        return -1
    if metric in LOWER_BETTER_CONFIRMED:
        return 1
    return None


def _unconfirmed_metrics(rank_on: list[str]) -> list[str]:
    """Metrics asked to order the gallery whose direction is not confirmed."""
    return [m for m in rank_on if _direction(m) is None]

# Five disclosure lines required on every gallery manifest (clinical-leads ruling).
DISCLOSURES = [
    "For testing only, not for use with real patients.",
    "Candidate order is from the published trial, not computed here.",
    "The prediction step is present but unverified.",
    "Identity and sign-off gates are specified but unbuilt.",
    "No hazard screen has been applied to this output.",
]

# This artefact is never a release: no hazard screen has been applied.
NOT_A_RELEASE_REASON = (
    "No hazard screen has been applied to this output, so the release condition has not "
    "been met."
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _metric_value(row: dict[str, str], metric: str) -> float | None:
    raw = row.get(metric, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _rank_rows(
    rows: list[dict[str, str]],
    rank_on: list[str],
    higher_better: dict[str, bool] | None = None,
) -> list[dict[str, str]]:
    if not rows:
        return rows
    higher_better = higher_better or {}

    def sort_key(row: dict[str, str]) -> tuple:
        keys: list[Any] = []
        for metric in rank_on:
            value = _metric_value(row, metric)
            if value is None:
                keys.append((1, 0.0))
                continue
            if metric in higher_better:
                sign = -1 if higher_better[metric] else 1
            else:
                direction = _direction(metric)
                if direction is None:
                    # No confirmed direction: never order on it (would be a guess).
                    keys.append((0, 0.0))
                    continue
                sign = direction
            keys.append((0, sign * value))
        return tuple(keys)

    ordered = sorted(rows, key=sort_key)
    for index, row in enumerate(ordered, start=1):
        row["rank"] = str(index)
    return ordered


def _locate_ranked_table(project_folder: Path) -> Path | None:
    return boltz_mod.locate_ranked_table(project_folder)


def _select_universes(rows: list[dict[str, str]], curation: dict[str, Any]) -> list[str]:
    """Universes come from curation.universes when set, else the available higher-better
    metrics present in the ranked table with at least one numeric value."""
    def has_values(metric: str) -> bool:
        return any(_metric_value(row, metric) is not None for row in rows)

    requested = curation.get("universes")
    if requested:
        return [u for u in requested if has_values(u)]
    columns = list(rows[0].keys()) if rows else []
    return [c for c in columns if c in ensemble.HIGHER_BETTER and has_values(c)]


def _disagreement_summary(summary: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    values = [s["disagreement"] for s in summary.values()]
    if not values:
        return None
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
    }


def _reproducibility(boltz: dict[str, Any]) -> dict[str, Any]:
    """Reproducible-given-seed, never 'deterministic': record seed and trajectory count
    where the campaign provides them."""
    trajectories = boltz.get("number_of_trajectories")
    if trajectories is None:
        trajectories = boltz.get("trajectories")
    if trajectories is None:
        trajectories = boltz.get("num_designs")
    return {
        "model": "reproducible-given-seed",
        "seed": boltz.get("seed"),
        "trajectories": trajectories,
    }


def _structures_manifest(copied_count: int) -> dict[str, Any]:
    block: dict[str, Any] = {"copied": copied_count}
    if copied_count:
        block["status"] = "copied"
    else:
        block["status"] = "not_run"
        block["reason"] = STRUCTURES_NOT_RUN_REASON
    return block


def curate(
    policy: dict[str, Any],
    *,
    project_folder: Path | str | None = None,
    workdir: Path | None = None,
    use_boltz_rank: bool = False,
    use_ensemble: bool = False,
    fixture_path: bool = False,
) -> dict[str, Any]:
    policy = normalize_policy(policy)
    base = workdir or Path.cwd()
    boltz, gallery_path = resolve_boltz_campaign(policy, workdir=base)
    curation = policy.get("curation") or {}
    rank_on = list(curation.get("rank_on") or ["design_to_target_iptm"])
    unconfirmed = _unconfirmed_metrics(rank_on)
    top_n = int(
        curation.get("top_n")
        or boltz.get("budget")
        or boltz.get("number_of_final_designs")
        or 10
    )

    # Ensemble is OFF by default: it ships as code but is unvalidated, so it writes nothing
    # to the manifest and does not reorder unless explicitly enabled.
    ensemble_enabled = bool(use_ensemble or curation.get("ensemble") is True)
    # The gallery is shown unordered with rank suppressed when ranking is requested on a
    # metric with no confirmed direction (default i_pDAE), unless the opt-in, unvalidated
    # ensemble is explicitly enabled (which ranks by cross-universe consensus instead).
    rank_suppressed = bool(unconfirmed) and not ensemble_enabled

    # Deployable-path licence guard: nothing evaluation_only may be a dependency here.
    data.assert_deployable(DEPLOYABLE_TOOLS)

    campaign_root = Path(project_folder or boltz["project_folder"])
    rerank = use_boltz_rank
    if rerank:
        ranked_csv = boltz_mod.maybe_rerank_with_boltzgen(
            boltz, campaign_root, rank_on, workdir=base
        )
    else:
        ranked_csv = _locate_ranked_table(campaign_root)
    if ranked_csv is None:
        raise FileNotFoundError(f"no ranked table under {campaign_root}")

    rows = [boltz_mod.normalize_row(row) for row in _read_csv(ranked_csv)]
    if rank_suppressed:
        # Physician's ruling for an unconfirmed direction: show candidates unordered
        # with the rank column suppressed. Drop any upstream rank and present in a
        # neutral (design-id) order that implies no quality ranking.
        for row in rows:
            row.pop("rank", None)
        rows = sorted(rows, key=lambda row: (row.get("design") or row.get("Design") or ""))
    elif "rank" not in (rows[0].keys() if rows else {}):
        rows = _rank_rows(rows, rank_on)
    else:
        rows = sorted(rows, key=lambda row: int(row.get("rank") or 999999))

    # Multiverse ensemble (only when explicitly enabled): order by consensus across universes.
    ensemble_summary: dict[str, dict[str, Any]] = {}
    ensemble_block: dict[str, Any] | None = None
    if ensemble_enabled:
        universes = _select_universes(rows, curation)
        ensemble_available = len(universes) >= 2
        fallback: str | None = None
        if ensemble_available:
            try:
                ensemble_summary = ensemble.summarise(rows, universes)
                rows = ensemble.order(rows, universes)
                for index, row in enumerate(rows, start=1):
                    row["rank"] = str(index)
            except ensemble.NotComparable as exc:
                ensemble_available = False
                ensemble_summary = {}
                fallback = f"ensemble not comparable ({exc}); fell back to rank_on={rank_on}"
        if not ensemble_available and fallback is None:
            fallback = (
                f"fewer than two higher-better universes present ({universes or 'none'}); "
                f"fell back to rank_on={rank_on}"
            )
        ensemble_block = {
            "method": "scorer_ensemble (multiverse heuristic)",
            "available": ensemble_available,
            "universes": universes if ensemble_available else [],
            "fallback": fallback,
            "basis": ENSEMBLE_BASIS,
            "disagreement_summary": _disagreement_summary(ensemble_summary),
            "triage": ensemble.triage(ensemble_summary) if ensemble_summary else [],
        }

    selected = rows[:top_n]
    gallery_path.mkdir(parents=True, exist_ok=True)
    structures_dir = gallery_path / "structures"
    structures_dir.mkdir(exist_ok=True)

    copied: list[str] = []
    entries: list[dict[str, Any]] = []
    for row in selected:
        design = row.get("design") or row.get("Design") or ""
        score = {metric: row.get(metric) for metric in rank_on if metric in row}
        file_name = row.get("file_name") or ""
        structure_sources: list[Path] = []
        if file_name:
            for sub in (
                boltz_mod.REFOLD_CIF,
                Path("intermediate_designs_inverse_folded"),
                Path("."),
            ):
                candidate = campaign_root / sub / file_name
                if candidate.exists():
                    structure_sources.append(candidate)
        if not structure_sources and design:
            structure_sources = list(campaign_root.glob(f"**/{design}*.cif"))
        structure_rel = None
        if structure_sources:
            source = structure_sources[0]
            dest = structures_dir / source.name
            if not dest.exists():
                shutil.copy2(source, dest)
            structure_rel = f"structures/{dest.name}"
            copied.append(str(dest))
        entry: dict[str, Any] = {
            "design": design,
            "scores": score,
            "sequence": row.get("Binder_Sequence"),
            "structure": structure_rel,
        }
        if not rank_suppressed:
            entry["rank"] = int(row.get("rank") or 0)
        summary = ensemble_summary.get(design) if ensemble_enabled else None
        if summary is not None:
            entry["ensemble"] = {
                "mean_rank": summary["mean_rank"],
                "worst_rank": summary["worst_rank"],
                "worst_universe": summary["worst_universe"],
                "spread": summary["spread"],
                "disagreement": summary["disagreement"],
                "by_universe": summary["by_universe"],
            }
        entries.append(entry)

    provenance = {
        "status": "in_silico_hypothesis",
        "statement": PROVENANCE_STATEMENT,
    }
    if ensemble_enabled:
        provenance["ensemble_basis"] = ENSEMBLE_BASIS

    manifest = {
        "schema": GALLERY_SCHEMA,
        "campaign": str(campaign_root),
        "source_table": str(ranked_csv),
        "rank_on": rank_on,
        "rank_meaning": RANK_MEANING,
        "top_n": top_n,
        "claim": RELATIVE_CLAIM,
        "disclosures": list(DISCLOSURES),
        "release": False,
        "not_a_release": NOT_A_RELEASE_REASON,
        "reproducibility": _reproducibility(boltz),
        "provenance": provenance,
        "benchmark": data.benchmark_record(),
        "tooling": {"path": "deployable", "tools": list(DEPLOYABLE_TOOLS)},
        "entries": entries,
        "structures": _structures_manifest(len(copied)),
    }
    if fixture_path:
        manifest["fixture_path_statement"] = FIXTURE_PATH_STATEMENT
    if rank_suppressed:
        manifest["rank_meaning"] = RANK_SUPPRESSED_MEANING
        manifest["rank_suppressed"] = True
        manifest["ordering"] = "unordered"
        manifest["rank_suppressed_reason"] = (
            "Ranking requested on " + ", ".join(unconfirmed) + ", whose direction is "
            "unconfirmed (legacy bindi metric from the BindCraft era; BoltzGen exposes "
            "design_to_target_iptm and min_design_to_target_pae instead). "
            "Candidates are shown unordered rather than ranked on a direction the code cannot justify."
        )
    if ensemble_block is not None:
        manifest["ensemble"] = ensemble_block
    (gallery_path / "index.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if selected:
        _write_csv(gallery_path / "ranked.csv", selected, selected[0].keys())
    else:
        (gallery_path / "ranked.csv").write_text("", encoding="utf-8")

    return {
        "schema": GALLERY_SCHEMA,
        "gallery": str(gallery_path),
        "count": len(entries),
        "structures_copied": len(copied),
        "manifest": str(gallery_path / "index.json"),
    }
