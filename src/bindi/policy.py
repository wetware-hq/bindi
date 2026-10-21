"""Campaign policy: hosted Boltz design settings plus bindi curation metadata."""

from __future__ import annotations

import json
import warnings
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


def _migrate_bindcraft_block(bindcraft: dict[str, Any]) -> dict[str, Any]:
    """Map legacy BindCraft2 campaign fields onto current policy keys."""
    boltz = deepcopy(bindcraft)
    if "budget" not in boltz and boltz.get("number_of_final_designs") is not None:
        boltz["budget"] = boltz["number_of_final_designs"]
    if "num_designs" not in boltz:
        final_n = int(boltz.get("number_of_final_designs") or boltz.get("budget") or 10)
        boltz["num_designs"] = max(final_n * 50, 50)
    # `protocol` is gone on purpose: it named a local design-binary protocol and has
    # no counterpart in the hosted API, which takes a modality on binder_specification.
    boltz.pop("protocol", None)
    return boltz


def normalize_policy(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise TypeError("policy must be a JSON object")
    if document.get("schema") == SCHEMA:
        doc = document
    elif "boltz" in document or "bindcraft" in document or "curation" in document or "gallery" in document:
        doc = document
    else:
        # Bare legacy campaign JSON becomes the design block.
        return {
            "schema": SCHEMA,
            "boltz": _migrate_bindcraft_block(deepcopy(document)),
            "curation": {},
            "gallery": "gallery",
        }

    if doc.get("schema") != SCHEMA:
        doc = {**doc, "schema": SCHEMA}

    if "boltz" not in doc and "bindcraft" in doc:
        warnings.warn(
            "policy.bindcraft is deprecated; use policy.boltz (the design backend is the hosted Boltz API).",
            DeprecationWarning,
            stacklevel=2,
        )
        doc["boltz"] = _migrate_bindcraft_block(doc.pop("bindcraft"))
    elif "boltz" in doc and "bindcraft" in doc:
        doc.pop("bindcraft", None)

    boltz = doc.get("boltz")
    if isinstance(boltz, dict):
        doc["boltz"] = _migrate_bindcraft_block(boltz)
    return doc


def resolve_boltz_campaign(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Return the design campaign dict and the gallery directory path."""
    policy = normalize_policy(policy)
    boltz = deepcopy(policy.get("boltz") or {})
    if not boltz:
        raise ValueError("policy.boltz is empty")

    base = workdir or Path.cwd()
    gallery_name = policy.get("gallery") or "gallery"
    gallery_path = Path(gallery_name)
    if not gallery_path.is_absolute():
        gallery_path = base / gallery_path

    project_folder = boltz.get("project_folder") or "results/campaign"
    project_path = Path(project_folder)
    if not project_path.is_absolute():
        project_path = base / project_path
    boltz["project_folder"] = str(project_path)

    return boltz, gallery_path


def resolve_bindcraft_campaign(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Deprecated alias for :func:`resolve_boltz_campaign`."""
    return resolve_boltz_campaign(policy, workdir=workdir)


def policy_from_nl_stub(query: str, **overrides: Any) -> dict[str, Any]:
    """Minimal helper for agents filling a campaign from natural language."""
    # A stub is a skeleton, not a runnable campaign: target_entities carries the target
    # SEQUENCES the hosted API requires and must be filled in before any real run.
    boltz = {
        "modality": overrides.pop("modality", "binder"),
        "number_of_final_designs": overrides.pop("number_of_final_designs", 5),
        "project_folder": overrides.pop("project_folder", "results/campaign"),
        "target_entities": overrides.pop("target_entities", []),
        "binder": overrides.pop("binder", {"chain_ids": ["D"], "length": "60..100"}),
    }
    target = overrides.pop("target", None)
    if target is not None:
        boltz["target"] = target
    boltz.update(overrides)
    boltz = _migrate_bindcraft_block(boltz)
    return {
        "schema": SCHEMA,
        "query": query,
        "boltz": boltz,
        "curation": {
            "rank_on": ["design_to_target_iptm"],
            "top_n": boltz.get("budget") or boltz["number_of_final_designs"],
        },
        "gallery": "gallery",
    }
