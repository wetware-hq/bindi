"""Full generate → curate pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .curate import curate
from .generate import generate

# A hosted design job is asynchronous: there is nothing to curate at submit time.
PENDING_STATUSES = {"awaiting_approval", "submitted"}


def run(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
    dry_run: bool = False,
    approval: str | None = None,
    use_ensemble: bool = False,
) -> dict[str, Any]:
    generation = generate(policy, workdir=workdir, dry_run=dry_run, approval=approval)
    if generation["status"] in PENDING_STATUSES:
        return {
            "generate": generation,
            "curate": {
                "status": "not_run",
                "reason": (
                    "Design runs on the hosted Boltz API are asynchronous. Poll the job, "
                    "pull its results, then curate the campaign folder."
                ),
            },
        }
    curation = curate(
        policy,
        project_folder=generation["project_folder"],
        workdir=workdir,
        use_ensemble=use_ensemble,
        fixture_path=dry_run,
    )
    return {"generate": generation, "curate": curation}
