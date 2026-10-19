"""BoltzGen (Boltz-1/Boltz-2) binder-design integration helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

# BoltzGen writes ranked metrics here (filtering step).
RANKED_STAGE = Path("final_ranked_designs")
RANKED_METRICS = "all_designs_metrics.csv"
REFOLD_CIF = Path("intermediate_designs_inverse_folded") / "refold_cif"

# Legacy BindCraft2 layout (still accepted when curating old campaigns).
LEGACY_RANK_STAGE = Path("3_Ranked")
LEGACY_RANKED_NAME = "!_Ranked.csv"

# Map bindi / legacy rank_on names to BoltzGen CSV columns.
METRIC_ALIASES: dict[str, str] = {
    "i_pTM": "design_to_target_iptm",
    "ipTM": "design_to_target_iptm",
    "iptm": "design_to_target_iptm",
    "pTM": "design_ptm",
    "i_pAE": "min_design_to_target_pae",
    "ipAE": "min_design_to_target_pae",
    "pAE": "min_design_to_target_pae",
    "i_pLDDT": "design_plddt",
    "pLDDT": "design_plddt",
}

MODALITY_TO_PROTOCOL: dict[str, str] = {
    "binder": "protein-anything",
    "miniprotein": "protein-anything",
    "protein": "protein-anything",
    "peptide": "peptide-anything",
    "nanobody": "nanobody-anything",
    "vhh": "nanobody-anything",
    "antibody": "antibody-anything",
    "fab": "antibody-anything",
}


def boltzgen_executable() -> str:
    return os.environ.get("BOLTZGEN_CMD", os.environ.get("BINDCRAFT_CMD", "boltzgen"))


def protocol_for_modality(modality: str | None) -> str:
    key = (modality or "binder").strip().lower()
    return MODALITY_TO_PROTOCOL.get(key, "protein-anything")


def locate_ranked_table(project_folder: Path) -> Path | None:
    boltz = project_folder / RANKED_STAGE / RANKED_METRICS
    if boltz.exists():
        return boltz
    legacy = project_folder / LEGACY_RANK_STAGE / LEGACY_RANKED_NAME
    if legacy.exists():
        return legacy
    flat = project_folder / "ranked.csv"
    if flat.exists():
        return flat
    return None


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """Unify BoltzGen and legacy BindCraft ranked rows for bindi curation."""
    out = dict(row)
    if not (out.get("design") or out.get("Design")):
        ident = out.get("id") or out.get("sample_id") or ""
        if ident:
            out["design"] = ident
    if not out.get("Binder_Sequence") and out.get("sequence"):
        out["Binder_Sequence"] = out["sequence"]
    if not out.get("rank") and out.get("final_rank"):
        out["rank"] = str(out["final_rank"])
    for alias, source in METRIC_ALIASES.items():
        if not (out.get(alias) or "").strip() and (out.get(source) or "").strip():
            out[alias] = out[source]
    return out


def resolve_design_spec_path(boltz: dict[str, Any], workdir: Path) -> Path:
    spec = boltz.get("design_spec") or boltz.get("spec")
    if not spec:
        target = boltz.get("target")
        raise ValueError(
            "policy.boltz.design_spec is required for BoltzGen runs "
            f"(target={target!r} alone is not enough). "
            "Add a design specification YAML; see campaigns/pdl1_miniprotein.yaml."
        )
    path = Path(spec)
    if not path.is_absolute():
        path = workdir / path
    if not path.exists():
        raise FileNotFoundError(f"design_spec not found: {path}")
    return path


def build_boltzgen_command(
    boltz: dict[str, Any],
    *,
    workdir: Path,
    design_spec: Path,
    extra_args: list[str] | None = None,
    filtering_only: bool = False,
) -> list[str]:
    project = Path(boltz["project_folder"])
    cmd: list[str] = [
        boltzgen_executable(),
        "run",
        str(design_spec),
        "--output",
        str(project),
        "--protocol",
        str(boltz.get("protocol") or protocol_for_modality(boltz.get("modality"))),
    ]
    num_designs = boltz.get("num_designs")
    budget = boltz.get("budget") or boltz.get("number_of_final_designs")
    if num_designs is not None:
        cmd.extend(["--num_designs", str(int(num_designs))])
    if budget is not None:
        cmd.extend(["--budget", str(int(budget))])
    if boltz.get("alpha") is not None:
        cmd.extend(["--alpha", str(boltz["alpha"])])
    if boltz.get("devices") is not None:
        cmd.extend(["--devices", str(boltz["devices"])])
    if boltz.get("reuse"):
        cmd.append("--reuse")
    if filtering_only:
        cmd.extend(["--steps", "filtering", "--reuse"])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def maybe_rerank_with_boltzgen(
    boltz: dict[str, Any],
    project_folder: Path,
    rank_on: list[str],
    *,
    workdir: Path,
) -> Path | None:
    """Re-run BoltzGen filtering; rank_on is recorded in the manifest but filtering uses BoltzGen defaults."""
    spec = boltz.get("design_spec") or boltz.get("spec")
    if not spec:
        return locate_ranked_table(project_folder)
    design_spec = Path(spec)
    if not design_spec.is_absolute():
        design_spec = workdir / design_spec
    if not design_spec.exists():
        return locate_ranked_table(project_folder)
    cmd = build_boltzgen_command(
        {**boltz, "project_folder": str(project_folder)},
        workdir=workdir,
        design_spec=design_spec,
        filtering_only=True,
    )
    try:
        subprocess.run(cmd, cwd=workdir, check=False, capture_output=True)
    except OSError:
        return locate_ranked_table(project_folder)
    return locate_ranked_table(project_folder)
