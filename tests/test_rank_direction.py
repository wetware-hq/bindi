"""Pin the ranking direction so it cannot silently flip, and pin the suppression branch
for a metric whose direction is not confirmed.

Context: `i_pDAE` is bindi's default `rank_on`. Its direction cannot be confirmed — the
name reads as interface predicted aligned error (lower-better), while bindi's own text and
fixtures treat it as a higher-better confidence metric, and it is not a BindCraft output
column (the upstream interface aligned-error column is `i_pAE`). So the gallery must be
shown unordered with the rank suppressed, never ranked on a guessed sign.
"""
import csv
import json
import tempfile
from pathlib import Path

from bindi.curate import (
    HIGHER_BETTER_CONFIRMED,
    LOWER_BETTER_CONFIRMED,
    UNCONFIRMED_DIRECTION,
    _direction,
    _rank_rows,
    curate,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_confirmed_directions_are_pinned():
    # Confidence scores: higher is better (sort sign -1).
    assert _direction("i_pTM") == -1
    assert _direction("pTM") == -1
    assert _direction("pLDDT") == -1
    # Interface aligned error (upstream `i_pAE`, normalised 0-1): lower is better (sign +1).
    assert _direction("i_pAE") == 1
    assert _direction("pAE") == 1


def test_i_pdae_direction_is_unconfirmed_not_assumed():
    # Must stay unconfirmed: never silently given a direction either way.
    assert _direction("i_pDAE") is None
    assert "i_pDAE" in UNCONFIRMED_DIRECTION
    assert "i_pDAE" not in HIGHER_BETTER_CONFIRMED
    assert "i_pDAE" not in LOWER_BETTER_CONFIRMED


def test_rank_rows_two_case_test_on_confirmed_metrics():
    rows = [
        {"design": "lo_err", "i_pAE": "0.20", "i_pTM": "0.55"},
        {"design": "hi_err", "i_pAE": "0.80", "i_pTM": "0.90"},
    ]
    # Lower i_pAE must rank first; if this flips, the sign has been inverted.
    assert [r["design"] for r in _rank_rows([dict(r) for r in rows], ["i_pAE"])] == ["lo_err", "hi_err"]
    # Higher i_pTM must rank first.
    assert [r["design"] for r in _rank_rows([dict(r) for r in rows], ["i_pTM"])] == ["hi_err", "lo_err"]


def test_gallery_suppresses_rank_when_ranking_on_i_pdae():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "results" / "p"
        (proj / "3_Ranked").mkdir(parents=True)
        (proj / "3_Ranked" / "!_Ranked.csv").write_text(
            (FIXTURES / "sample_ranked_disagree.csv").read_text(encoding="utf-8"), encoding="utf-8"
        )
        policy = {
            "bindcraft": {"project_folder": str(proj)},
            "curation": {"rank_on": ["i_pDAE"], "top_n": 10},
            "gallery": "gallery",
        }
        curate(policy, workdir=Path(tmp))
        manifest = json.loads((Path(tmp) / "gallery" / "index.json").read_text(encoding="utf-8"))

        assert manifest.get("rank_suppressed") is True
        assert manifest.get("ordering") == "unordered"
        # No entry may carry a rank, and nothing may be presented as "best".
        assert all("rank" not in entry for entry in manifest["entries"])
        # The written ranked.csv must not carry a rank column either.
        with (Path(tmp) / "gallery" / "ranked.csv").open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        assert "rank" not in header
