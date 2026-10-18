"""Invoke BindCraft2 design for a resolved campaign."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .case import assert_design_allowed, case_path_from, resolve_target
from .policy import resolve_bindcraft_campaign

RANKED_REL = Path("3_Ranked") / "!_Ranked.csv"


def bindcraft_executable() -> str:
    return os.environ.get("BINDCRAFT_CMD", "bindcraft")


def generate(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
    dry_run: bool = False,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    base = workdir or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)
    bindcraft, gallery_path = resolve_bindcraft_campaign(policy, workdir=base)

    campaign_dir = Path(bindcraft["project_folder"])
    campaign_dir.mkdir(parents=True, exist_ok=True)
    campaign_file = campaign_dir / "campaign.json"
    campaign_file.write_text(json.dumps(bindcraft, indent=2) + "\n", encoding="utf-8")

    if dry_run:
        _write_dry_run_outputs(campaign_dir)
        return {
            "status": "dry_run",
            "campaign_file": str(campaign_file),
            "project_folder": str(campaign_dir),
            "gallery": str(gallery_path),
        }

    # The seam's hold: when the campaign names a patient case record, a real design run
    # only starts if that record is signed, consents to design use, and the Scientist's two
    # conditions (human-reference gate pass, stated wild-type margin) are satisfied. Nothing
    # here loosens for a fixture target; a fixture campaign keeps its previous behaviour.
    if case_path_from(policy) is not None:
        assert_design_allowed(resolve_target(policy))

    cmd = [bindcraft_executable(), "design", str(campaign_file)]
    if extra_args:
        cmd.extend(extra_args)
    completed = subprocess.run(cmd, cwd=base, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"bindcraft design failed with exit code {completed.returncode}")

    return {
        "status": "completed",
        "campaign_file": str(campaign_file),
        "project_folder": str(campaign_dir),
        "gallery": str(gallery_path),
    }


def _write_dry_run_outputs(campaign_dir: Path) -> None:
    ranked_dir = campaign_dir / "3_Ranked"
    ranked_dir.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "sample_ranked.csv"
    if fixture.exists():
        shutil.copy(fixture, ranked_dir / "!_Ranked.csv")
    else:
        (ranked_dir / "!_Ranked.csv").write_text(
            "design,rank,i_pDAE,Binder_Sequence\n"
            "demo_seq1,1,0.72,ACDEFGHIKLMNPQRSTVWY\n"
            "demo_seq2,2,0.68,ACDEFGHIKLMNPQRSTVWYAC\n",
            encoding="utf-8",
        )
