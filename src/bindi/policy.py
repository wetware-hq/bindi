"""Campaign policy: BindCraft2 settings plus bindi curation metadata."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA = "bindi.policy.v1"
def load_policy(path: str | Path | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload is not None:
        document = deepcopy(payload)
    elif path is None:
        raise ValueError("policy path or payload required")
    else:
        text = Path(path).read_text(encoding="utf-8")
        document = json.loads(text)
    return normalize_policy(document)


def normalize_policy(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise TypeError("policy must be a JSON object")
    if document.get("schema") == SCHEMA:
        return document
    if "bindcraft" in document or "curation" in document or "gallery" in document:
        return document
    # Bare BindCraft2 campaign JSON is accepted as the bindcraft block.
    return {
        "schema": SCHEMA,
        "bindcraft": deepcopy(document),
        "curation": {},
        "gallery": "gallery",
    }


def resolve_bindcraft_campaign(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Return BindCraft2 campaign dict and the gallery directory path."""
    policy = normalize_policy(policy)
    bindcraft = deepcopy(policy.get("bindcraft") or {})
    if not bindcraft:
        raise ValueError("policy.bindcraft is empty")

    base = workdir or Path.cwd()
    gallery_name = policy.get("gallery") or "gallery"
    gallery_path = Path(gallery_name)
    if not gallery_path.is_absolute():
        gallery_path = base / gallery_path

    project_folder = bindcraft.get("project_folder") or "results/campaign"
    project_path = Path(project_folder)
    if not project_path.is_absolute():
        project_path = base / project_path
    bindcraft["project_folder"] = str(project_path)

    return bindcraft, gallery_path


def policy_from_nl_stub(query: str, **overrides: Any) -> dict[str, Any]:
    """Minimal helper for agents filling a campaign from natural language."""
    bindcraft = {
        "target": overrides.pop("target", "hPDL1"),
        "modality": overrides.pop("modality", "binder"),
        "number_of_final_designs": overrides.pop("number_of_final_designs", 5),
        "project_folder": overrides.pop("project_folder", "results/campaign"),
    }
    bindcraft.update(overrides)
    return {
        "schema": SCHEMA,
        "query": query,
        "bindcraft": bindcraft,
        "curation": {"rank_on": ["i_pDAE"], "top_n": bindcraft["number_of_final_designs"]},
        "gallery": "gallery",
    }
