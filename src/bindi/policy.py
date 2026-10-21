"""Campaign policy: Boltz (BoltzGen) generator settings plus bindi curation metadata.

Boltz is the generator. The design block is `policy.boltz` and `policy.generator` records
the generator id explicitly, so reading a normalised policy tells you what generates.
`LEGACY_DESIGN_KEY` ("bindcraft") is still *read* so existing policy files on disk keep
loading; it is migrated onto `policy.boltz` with a DeprecationWarning and never written.
"""

from __future__ import annotations

import json
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

from .boltz import GENERATOR_ID, protocol_for_modality

SCHEMA = "bindi.policy.v1"

#: Design block key. Boltz is the generator.
DESIGN_KEY = "boltz"

#: Read-only backwards compatibility: pre-Boltz policy files used this key. Migrated on
#: load so an existing policy file still opens; never written back out.
LEGACY_DESIGN_KEY = "bindcraft"


def load_policy(path: str | Path | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload is not None:
        document = deepcopy(payload)
    elif path is None:
        raise ValueError("policy path or payload required")
    else:
        text = Path(path).read_text(encoding="utf-8")
        document = json.loads(text)
    return normalize_policy(document)


def _normalize_boltz_block(block: dict[str, Any]) -> dict[str, Any]:
    """Fill Boltz (BoltzGen) defaults, accepting legacy campaign field names."""
    boltz = deepcopy(block)
    if "budget" not in boltz and boltz.get("number_of_final_designs") is not None:
        boltz["budget"] = boltz["number_of_final_designs"]
    if "num_designs" not in boltz:
        final_n = int(boltz.get("number_of_final_designs") or boltz.get("budget") or 10)
        boltz["num_designs"] = max(final_n * 50, 50)
    if "protocol" not in boltz:
        boltz["protocol"] = protocol_for_modality(boltz.get("modality"))
    return boltz


def normalize_policy(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise TypeError("policy must be a JSON object")
    if document.get("schema") == SCHEMA:
        doc = document
    elif (
        DESIGN_KEY in document
        or LEGACY_DESIGN_KEY in document
        or "curation" in document
        or "gallery" in document
    ):
        doc = document
    else:
        # Bare legacy campaign JSON becomes the design block.
        return {
            "schema": SCHEMA,
            "generator": GENERATOR_ID,
            DESIGN_KEY: _normalize_boltz_block(deepcopy(document)),
            "curation": {},
            "gallery": "gallery",
        }

    if doc.get("schema") != SCHEMA:
        doc = {**doc, "schema": SCHEMA}

    if DESIGN_KEY not in doc and LEGACY_DESIGN_KEY in doc:
        warnings.warn(
            f"policy.{LEGACY_DESIGN_KEY} is deprecated; use policy.{DESIGN_KEY}. Boltz "
            "(BoltzGen) is the generator; the legacy key is read for backwards "
            "compatibility only.",
            DeprecationWarning,
            stacklevel=2,
        )
        doc[DESIGN_KEY] = _normalize_boltz_block(doc.pop(LEGACY_DESIGN_KEY))
    elif DESIGN_KEY in doc and LEGACY_DESIGN_KEY in doc:
        doc.pop(LEGACY_DESIGN_KEY, None)

    boltz = doc.get(DESIGN_KEY)
    if isinstance(boltz, dict):
        doc[DESIGN_KEY] = _normalize_boltz_block(boltz)
    doc["generator"] = GENERATOR_ID
    return doc


def resolve_boltz_campaign(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Return the Boltz (BoltzGen) campaign dict and the gallery directory path."""
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


def policy_from_nl_stub(query: str, **overrides: Any) -> dict[str, Any]:
    """Minimal helper for agents filling a campaign from natural language."""
    boltz = {
        "target": overrides.pop("target", "hPDL1"),
        "modality": overrides.pop("modality", "binder"),
        "number_of_final_designs": overrides.pop("number_of_final_designs", 5),
        "project_folder": overrides.pop("project_folder", "results/campaign"),
        "design_spec": overrides.pop("design_spec", "campaigns/pdl1_miniprotein.yaml"),
    }
    boltz.update(overrides)
    boltz = _normalize_boltz_block(boltz)
    return {
        "schema": SCHEMA,
        "generator": GENERATOR_ID,
        "query": query,
        "boltz": boltz,
        "curation": {
            "rank_on": ["design_to_target_iptm"],
            "top_n": boltz.get("budget") or boltz["number_of_final_designs"],
        },
        "gallery": "gallery",
    }
