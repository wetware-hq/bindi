"""Boltz is the generator, and it runs on the official hosted Boltz API.

There is no local generator. `boltzgen` is never executed: Boltz self-hosting is forbidden
by standing instruction, so the only generator path is `https://api.boltz.bio` via
`bindi.boltz_hosted`. This module holds the parts that are not the network call: the job
parameter translation (`job_parameters`), the design-spec resolver, the ranked-table layout
this repo reads, and the column normalisation curation needs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Generator identity, recorded in policies and traces so the generator is never implicit.
GENERATOR_ID = "boltzgen"
GENERATOR_NAME = "BoltzGen"
GENERATOR_FAMILY = "Boltz"
GENERATOR_TOOL = "BoltzGen"  # as tiered in src/bindi/data/tool_tiers.toml
GENERATOR_ENGINE = "hosted_boltz_api"

# BoltzGen writes ranked metrics here (filtering step).
RANKED_STAGE = Path("final_ranked_designs")
RANKED_METRICS = "all_designs_metrics.csv"
REFOLD_CIF = Path("intermediate_designs_inverse_folded") / "refold_cif"

# Legacy pre-Boltz ranked-table layout (still accepted when curating old campaigns).
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

#: `num_proteins` floor documented by the API (10..1_000_000).
MIN_NUM_PROTEINS = 10

#: bindi modality -> hosted API `modality` for a binder design job.
MODALITY_TO_API: dict[str, str] = {
    "binder": "custom_protein",
    "miniprotein": "custom_protein",
    "protein": "custom_protein",
    "custom_protein": "custom_protein",
    "peptide": "peptide",
    "nanobody": "nanobody",
    "vhh": "nanobody",
    "antibody": "antibody",
    "fab": "antibody",
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


def protocol_for_modality(modality: str | None) -> str:
    key = (modality or "binder").strip().lower()
    return MODALITY_TO_PROTOCOL.get(key, "protein-anything")


def modality_for_api(modality: str | None) -> str:
    """Hosted-API binder modality for a bindi modality name."""
    key = (modality or "binder").strip().lower()
    return MODALITY_TO_API.get(key, "custom_protein")


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
    """Unify BoltzGen and legacy ranked rows for bindi curation."""
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
            "policy.boltz.design_spec is required for a hosted Boltz design run "
            f"(target={target!r} alone is not enough). "
            "Add a design specification YAML; see campaigns/nyeso1_a0201_pmhc.yaml."
        )
    path = Path(spec)
    if not path.is_absolute():
        path = workdir / path
    if not path.exists():
        raise FileNotFoundError(f"design_spec not found: {path}")
    return path


def job_parameters(boltz: dict[str, Any]) -> dict[str, Any]:
    """Translate a policy `boltz` block into hosted-API job parameters.

    This is the surviving half of what used to build a `boltzgen run ...` command line: the
    parameter translation, with no process execution. `bindi.boltz_hosted` turns these into
    the request body for `POST /compute/v1/protein/design`.

    `num_proteins` is the API's name for how many designs to generate; it accepts
    10..1_000_000, so a smaller campaign number is raised to the API minimum and the raw
    request is recorded either way.
    """
    requested = boltz.get("num_designs")
    if requested is None:
        requested = boltz.get("budget") or boltz.get("number_of_final_designs")
    requested = int(requested or MIN_NUM_PROTEINS)
    params: dict[str, Any] = {
        "num_proteins": max(requested, MIN_NUM_PROTEINS),
        "num_proteins_requested": requested,
        "protocol": str(boltz.get("protocol") or protocol_for_modality(boltz.get("modality"))),
        "modality": modality_for_api(boltz.get("modality")),
    }
    if boltz.get("campaign_name"):
        params["name"] = str(boltz["campaign_name"])
    if boltz.get("seed") is not None:
        params["seed"] = int(boltz["seed"])
    return params
