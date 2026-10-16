"""Build a ranked gallery directory from a BindCraft2 campaign folder."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Any, Iterable

from . import data, ensemble
from .policy import normalize_policy, resolve_bindcraft_campaign

GALLERY_SCHEMA = "bindi.gallery.v1"
RANKED_NAME = "!_Ranked.csv"
RANK_STAGE = Path("3_Ranked")

# bindi's only generator path. Must stay deployable (see src/bindi/data/tool_tiers.toml).
DEPLOYABLE_TOOLS = ["BindCraft2"]

RELATIVE_CLAIM = (
    "Relative ranking among the designs in this campaign only. No absolute affinity, "
    "Kd/Ki/IC50, or kcal/mol value is predicted or implied; ranking is never a safety claim."
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
    default_higher = {"i_pDAE", "i_pTM", "pTM", "Interface_BuriedArea", "Interface_Residues"}

    def sort_key(row: dict[str, str]) -> tuple:
        keys: list[Any] = []
        for metric in rank_on:
            value = _metric_value(row, metric)
            if value is None:
                keys.append((1, 0.0))
                continue
            higher = higher_better.get(metric, metric in default_higher)
            keys.append((0, -value if higher else value))
        return tuple(keys)

    ordered = sorted(rows, key=sort_key)
    for index, row in enumerate(ordered, start=1):
        row["rank"] = str(index)
    return ordered


def _locate_ranked_table(project_folder: Path) -> Path | None:
    ranked = project_folder / RANK_STAGE / RANKED_NAME
    if ranked.exists():
        return ranked
    legacy = project_folder / "ranked.csv"
    if legacy.exists():
        return legacy
    return None


def _maybe_rerank_with_bindcraft(
    project_folder: Path,
    rank_on: list[str],
    bindcraft_cmd: str,
) -> Path | None:
    if not rank_on:
        return _locate_ranked_table(project_folder)
    metric = rank_on[0]
    cmd = [bindcraft_cmd, "rank", str(project_folder), "--on", metric]
    for extra in rank_on[1:]:
        cmd.extend(["--on", extra])
    try:
        subprocess.run(cmd, check=False, capture_output=True)
    except OSError:
        return _locate_ranked_table(project_folder)
    custom = project_folder / RANK_STAGE / f"ranked_by_{metric}.csv"
    if custom.exists():
        return custom
    return _locate_ranked_table(project_folder)


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


def curate(
    policy: dict[str, Any],
    *,
    project_folder: Path | str | None = None,
    workdir: Path | None = None,
    use_bindcraft_rank: bool = False,
    bindcraft_cmd: str = "bindcraft",
) -> dict[str, Any]:
    policy = normalize_policy(policy)
    base = workdir or Path.cwd()
    bindcraft, gallery_path = resolve_bindcraft_campaign(policy, workdir=base)
    curation = policy.get("curation") or {}
    rank_on = list(curation.get("rank_on") or ["i_pDAE"])
    top_n = int(curation.get("top_n") or bindcraft.get("number_of_final_designs") or 10)

    # Deployable-path licence guard: nothing evaluation_only may be a dependency here.
    data.assert_deployable(DEPLOYABLE_TOOLS)

    campaign_root = Path(project_folder or bindcraft["project_folder"])
    if use_bindcraft_rank:
        ranked_csv = _maybe_rerank_with_bindcraft(campaign_root, rank_on, bindcraft_cmd)
    else:
        ranked_csv = _locate_ranked_table(campaign_root)
    if ranked_csv is None:
        raise FileNotFoundError(f"no ranked table under {campaign_root}")

    rows = _read_csv(ranked_csv)
    if "rank" not in (rows[0].keys() if rows else {}):
        rows = _rank_rows(rows, rank_on)
    else:
        rows = sorted(rows, key=lambda row: int(row.get("rank") or 999999))

    # Multiverse ensemble: order by consensus across universes when at least two exist.
    universes = _select_universes(rows, curation)
    ensemble_summary: dict[str, dict[str, Any]] = {}
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

    selected = rows[:top_n]
    gallery_path.mkdir(parents=True, exist_ok=True)
    structures_dir = gallery_path / "structures"
    structures_dir.mkdir(exist_ok=True)

    copied: list[str] = []
    entries: list[dict[str, Any]] = []
    for row in selected:
        design = row.get("design") or row.get("Design") or ""
        score = {metric: row.get(metric) for metric in rank_on if metric in row}
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
            "rank": int(row.get("rank") or 0),
            "scores": score,
            "sequence": row.get("Binder_Sequence"),
            "structure": structure_rel,
        }
        summary = ensemble_summary.get(design)
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

    manifest = {
        "schema": GALLERY_SCHEMA,
        "campaign": str(campaign_root),
        "source_table": str(ranked_csv),
        "rank_on": rank_on,
        "top_n": top_n,
        "claim": RELATIVE_CLAIM,
        "provenance": {
            "status": "in_silico_hypothesis",
            "statement": PROVENANCE_STATEMENT,
            "ensemble_basis": ENSEMBLE_BASIS,
        },
        "ensemble": {
            "method": "scorer_ensemble (multiverse heuristic)",
            "available": ensemble_available,
            "universes": universes if ensemble_available else [],
            "fallback": fallback,
            "basis": ENSEMBLE_BASIS,
            "disagreement_summary": _disagreement_summary(ensemble_summary),
            "triage": ensemble.triage(ensemble_summary) if ensemble_summary else [],
        },
        "benchmark": data.benchmark_record(),
        "tooling": {"path": "deployable", "tools": list(DEPLOYABLE_TOOLS)},
        "entries": entries,
    }
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
