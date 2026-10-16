import csv
from pathlib import Path

import pytest

from bindi import ensemble

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _rows(name):
    with (FIXTURES / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_ranks_assign_place_one_to_best():
    rows = _rows("sample_ranked_disagree.csv")
    r = ensemble.ranks(rows, ["i_pDAE", "i_pTM"])
    assert r["pdl1_contested_a"]["i_pDAE"] == 1  # highest i_pDAE
    assert r["pdl1_contested_a"]["i_pTM"] == 3   # lowest i_pTM
    assert r["pdl1_contested_c"]["i_pTM"] == 1


def test_summarise_reports_disagreement():
    rows = _rows("sample_ranked_disagree.csv")
    s = ensemble.summarise(rows, ["i_pDAE", "i_pTM"])
    assert s["pdl1_contested_a"]["disagreement"] == 1.0
    assert s["pdl1_steady_b"]["disagreement"] == 0.0
    assert s["pdl1_contested_a"]["worst_rank"] == 3
    assert s["pdl1_contested_a"]["mean_rank"] == 2.0


def test_order_sorts_by_mean_then_worst():
    rows = _rows("sample_ranked_disagree.csv")
    ordered = [r["design"] for r in ensemble.order(rows, ["i_pDAE", "i_pTM"])]
    # steady_b has mean_rank 2 but worst_rank 2; contested have mean 2 worst 3 -> steady first.
    assert ordered[0] == "pdl1_steady_b"


def test_triage_surfaces_contested_designs():
    rows = _rows("sample_ranked_disagree.csv")
    s = ensemble.summarise(rows, ["i_pDAE", "i_pTM"])
    contested = ensemble.triage(s, threshold=0.5)
    assert set(contested) == {"pdl1_contested_a", "pdl1_contested_c"}
    assert "pdl1_steady_b" not in contested


def test_identical_scorers_produce_no_disagreement():
    rows = _rows("sample_ranked.csv")
    s = ensemble.summarise(rows, ["i_pDAE", "i_pTM"])
    assert all(v["disagreement"] == 0.0 for v in s.values())
    assert ensemble.triage(s) == []


def test_not_comparable_on_fewer_than_two_universes():
    rows = _rows("sample_ranked.csv")
    with pytest.raises(ensemble.NotComparable):
        ensemble.summarise(rows, ["i_pDAE"])


@pytest.mark.parametrize("bad", ["", "   ", "not_a_number"])
def test_not_comparable_on_missing_blank_or_nonnumeric_metric(bad):
    rows = [
        {"design": "a", "i_pDAE": "0.7", "i_pTM": "0.6"},
        {"design": "b", "i_pDAE": "0.5", "i_pTM": bad},
    ]
    with pytest.raises(ensemble.NotComparable):
        ensemble.summarise(rows, ["i_pDAE", "i_pTM"])
