"""Full generate → curate pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .curate import curate
from .generate import generate


def run(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
    dry_run: bool = False,
    use_bindcraft_rank: bool = False,
) -> dict[str, Any]:
    generation = generate(policy, workdir=workdir, dry_run=dry_run)
    curation = curate(
        policy,
        project_folder=generation["project_folder"],
        workdir=workdir,
        use_bindcraft_rank=use_bindcraft_rank and not dry_run,
    )
    return {"generate": generation, "curate": curation}
