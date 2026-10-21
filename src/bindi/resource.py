"""Resource descriptor for frontier intelligence recruitment."""

from __future__ import annotations

from typing import Any

from . import VERSION

RESOURCE_SCHEMA = "wetware.resource.v1"


def resource_descriptor() -> dict[str, Any]:
    return {
        "schema": RESOURCE_SCHEMA,
        "name": "bindi",
        "version": VERSION,
        "description": "Generate and curate protein binders for a target via the hosted Boltz design API.",
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
            "name": "Boltz API",
            "url": "https://api.boltz.bio/docs/",
            "hosting": "hosted service (no model weights are downloaded or run locally)",
        },
        "container": {
            "dockerfile": "containers/Dockerfile",
            "entrypoint": ["bindi"],
        },
        "agent": "AGENT.md",
        "commands": {
            "generate": "bindi generate <campaign.json>",
            "curate": "bindi curate <campaign.json> [--project <folder>]",
            "run": "bindi run <campaign.json>",
            "recruit": "bindi recruit",
            "job": "bindi job <job_id> [--results]",
        },
    }
