"""Invoke BoltzGen design for a resolved campaign."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .boltz import (
    LEGACY_RANK_STAGE,
    LEGACY_RANKED_NAME,
    RANKED_METRICS,
    RANKED_STAGE,
    build_boltzgen_command,
    resolve_design_spec_path,
)
from .case import assert_design_allowed, case_path_from, resolve_target
from .policy import resolve_boltz_campaign


def generate(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
    dry_run: bool = False,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    base = workdir or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)
    boltz, gallery_path = resolve_boltz_campaign(policy, workdir=base)

    campaign_dir = Path(boltz["project_folder"])
    campaign_dir.mkdir(parents=True, exist_ok=True)
    campaign_file = campaign_dir / "campaign.json"
    campaign_file.write_text(json.dumps(boltz, indent=2) + "\n", encoding="utf-8")

    if dry_run:
        _write_dry_run_outputs(campaign_dir)
        return {
            "status": "dry_run",
            "campaign_file": str(campaign_file),
            "project_folder": str(campaign_dir),
            "gallery": str(gallery_path),
        }

    # The seam's hold: when the campaign names a patient case record, a real design run
    # only starts if that record is signed, consents to design use, and the Scientist's
    # conditions (human-reference gate pass, stated wild-type margin, established structural
    # margin threshold) are satisfied. Nothing here loosens for a fixture target.
    if case_path_from(policy) is not None:
        assert_design_allowed(resolve_target(policy))

    design_spec = resolve_design_spec_path(boltz, base)
    cmd = build_boltzgen_command(boltz, workdir=base, design_spec=design_spec, extra_args=extra_args)
    completed = subprocess.run(cmd, cwd=base, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"boltzgen run failed with exit code {completed.returncode}")

    return {
        "status": "completed",
        "campaign_file": str(campaign_file),
        "project_folder": str(campaign_dir),
        "gallery": str(gallery_path),
        "design_spec": str(design_spec),
    }


def _write_dry_run_outputs(campaign_dir: Path) -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "sample_ranked.csv"
    text = fixture.read_text(encoding="utf-8") if fixture.exists() else (
        "design,rank,design_to_target_iptm,Binder_Sequence\n"
        "demo_seq1,1,0.72,ACDEFGHIKLMNPQRSTVWY\n"
        "demo_seq2,2,0.68,ACDEFGHIKLMNPQRSTVWYAC\n"
    )
    # BoltzGen layout
    ranked_dir = campaign_dir / RANKED_STAGE
    ranked_dir.mkdir(parents=True, exist_ok=True)
    (ranked_dir / RANKED_METRICS).write_text(text, encoding="utf-8")
    # Legacy layout (older campaigns / fixtures)
    legacy_dir = campaign_dir / LEGACY_RANK_STAGE
    legacy_dir.mkdir(parents=True, exist_ok=True)
    if fixture.exists():
        shutil.copy(fixture, legacy_dir / LEGACY_RANKED_NAME)
    else:
        (legacy_dir / LEGACY_RANKED_NAME).write_text(text, encoding="utf-8")
