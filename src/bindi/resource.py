"""Resource descriptor for frontier intelligence recruitment."""

from __future__ import annotations

from typing import Any

RESOURCE_SCHEMA = "wetware.resource.v1"
VERSION = "0.1.0"


def resource_descriptor() -> dict[str, Any]:
    return {
        "schema": RESOURCE_SCHEMA,
        "name": "bindi",
        "version": VERSION,
        "description": "Generate and curate protein binders for a target via BindCraft2.",
        "capabilities": ["generate", "curate"],
        "inputs": {
            "campaign": {
                "type": "policy",
                "schema": "bindi.policy.v1",
                "description": "Protein target and design parameters; fill at runtime from a natural-language query.",
            }
        },
        "outputs": {
            "gallery": {
                "type": "directory",
                "schema": "bindi.gallery.v1",
                "description": "Curated binders ranked and scored for selection.",
            }
        },
        "tool": {
            "name": "BindCraft2",
            "url": "https://github.com/PacesaLab/BindCraft2",
        },
        "container": {
            "dockerfile": "containers/Dockerfile",
            "entrypoint": ["bindi"],
        },
        "commands": {
            "generate": "bindi generate <campaign.json>",
            "curate": "bindi curate <campaign.json> [--project <folder>]",
            "run": "bindi run <campaign.json>",
            "recruit": "bindi recruit",
        },
    }
