"""The multiverse heuristic: score each design under several scorers ("universes") and report how it holds up
across them, not just its best number.

Each universe is one scorer already present in the ranked table. A design gets a rank in every universe; the
summary is the mean rank, the worst universe, the spread between best and worst, and a disagreement score the
universes argue over. Wide disagreement is a reason to look, and to spend a wet-lab experiment, not a reason to drop.

This is a ranking heuristic and never a safety claim: it says nothing about whether a design resembles normal human
sequence, and it is meaningful only on candidates that already passed the deterministic gates.
"""

from __future__ import annotations

from typing import Any

HIGHER_BETTER = {
    "i_pDAE",
    "i_pTM",
    "pTM",
    "Interface_BuriedArea",
    "Interface_Residues",
    "design_to_target_iptm",
    "design_iptm",
    "design_ptm",
}
KIND = "scorer_ensemble"


class NotComparable(Exception):
    """A universe is missing from a design's row, so the ensemble would be comparing different candidate sets."""


def _value(row: dict[str, str], metric: str) -> float:
    raw = (row.get(metric) or "").strip()
    if not raw:
        raise NotComparable(f"{row.get('design', '')}/{metric}")
    try:
        return float(raw)
    except ValueError:
        raise NotComparable(f"{row.get('design', '')}/{metric}")


def ranks(rows: list[dict[str, str]], universes: list[str]) -> dict[str, dict[str, int]]:
    """Rank every design in every universe. Rank 1 is best in that universe."""
    out: dict[str, dict[str, int]] = {}
    for metric in universes:
        higher = metric in HIGHER_BETTER
        ordered = sorted(rows, key=lambda r: -_value(r, metric) if higher else _value(r, metric))
        for place, row in enumerate(ordered, start=1):
            out.setdefault(row.get("design") or "", {})[metric] = place
    return out


def summarise(rows: list[dict[str, str]], universes: list[str]) -> dict[str, dict[str, Any]]:
    """Per design: mean rank, worst universe and its rank, spread, and a 0-1 disagreement score."""
    if len(universes) < 2:
        raise NotComparable(f"universes/{len(universes)}")
    per = ranks(rows, universes)
    span = max(len(rows) - 1, 1)
    out = {}
    for design, by_universe in per.items():
        places = list(by_universe.values())
        worst = max(by_universe, key=lambda m: by_universe[m])
        out[design] = {
            "by_universe": by_universe,
            "mean_rank": round(sum(places) / len(places), 3),
            "worst_rank": max(places),
            "worst_universe": worst,
            "spread": max(places) - min(places),
            "disagreement": round((max(places) - min(places)) / span, 3),
        }
    return out


def order(rows: list[dict[str, str]], universes: list[str]) -> list[dict[str, str]]:
    """Order by mean rank, then by worst universe, so a design that fails badly anywhere cannot be smoothed away
    by a good average."""
    summary = summarise(rows, universes)

    def key(row: dict[str, str]) -> tuple:
        s = summary[row.get("design") or ""]
        return (s["mean_rank"], s["worst_rank"], row.get("design") or "")

    return sorted(rows, key=key)


def triage(summary: dict[str, dict[str, Any]], threshold: float = 0.5) -> list[str]:
    """Designs the universes argue over most: the first candidates to send for wet-lab confirmation."""
    args = [d for d, s in summary.items() if s["disagreement"] >= threshold]
    return sorted(args, key=lambda d: (-summary[d]["disagreement"], d))
