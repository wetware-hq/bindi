"""Cited data files and the checks that read them.

Keeping tiers, the benchmark record and the absolute-affinity rule here (data + thin
readers) means new tools or a real benchmark are added by editing the TOML, not core code.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data"

# A relative ranking is the only claim bindi makes. Absolute affinity (a kcal/mol number,
# a Kd/Ki/IC50, or an absolute dG/ddG) is exactly what a single-structure method cannot
# predict (Sampson et al. 2024), so no manifest key may look like one.
ABSOLUTE_AFFINITY_RE = re.compile(
    r"(kcal|k[_\s-]?cal|\bk[di]\b|ic50|affinity|\bdg\b|\bddg\b|delta[_\s-]?g|binding[_\s-]?energy)",
    re.IGNORECASE,
)


def looks_like_absolute_affinity(name: str) -> bool:
    """True if a field or score key reads like an absolute affinity / energy number."""
    return bool(ABSOLUTE_AFFINITY_RE.search(name or ""))


def _load(name: str) -> dict[str, Any]:
    with (_DATA / name).open("rb") as handle:
        return tomllib.load(handle)


def tool_tiers() -> dict[str, dict[str, str]]:
    """tool name -> {tier, licence, citation}."""
    return _load("tool_tiers.toml").get("tools", {})


def tier_of(tool: str) -> str | None:
    entry = tool_tiers().get(tool)
    return entry.get("tier") if entry else None


class LicenceTierError(Exception):
    """A tool marked evaluation_only was put in a deployable path."""


def assert_deployable(tools: list[str]) -> None:
    """Raise if any tool used in a deployable path is evaluation_only (or unknown)."""
    tiers = tool_tiers()
    bad = []
    for tool in tools:
        entry = tiers.get(tool)
        if entry is None:
            bad.append(f"{tool} (untiered)")
        elif entry.get("tier") != "deployable":
            bad.append(f"{tool} ({entry.get('tier')})")
    if bad:
        raise LicenceTierError(
            "not deployable: " + ", ".join(bad) + " — see src/bindi/data/tool_tiers.toml"
        )


def benchmark_record() -> dict[str, Any]:
    """The cited, data-file-driven record of how the ranking has been checked. Status is
    'unchecked' until real paired (ranking, measured relative affinity) data exists."""
    return _load("benchmark.toml")
