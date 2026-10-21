"""The ipSAE veto-only filter may refuse a design and may never bless one.

A green here means the asymmetry is structural, not conventional: there is no passing state
to read, a survivor carries no ipSAE number anywhere, the filter can only subtract, an
unset floor is inert rather than permissive, and none of it touches the margin stage, which
still refuses by construction.
"""

import json

import pytest

from bindi import ipsae_margin, ipsae_veto, margin

DESIGNS = [
    {"design": "d1", "sequence": "AAAA"},
    {"design": "d2", "sequence": "CCCC"},
    {"design": "d3", "sequence": "DDDD"},
]
SCORES = {"d1": 0.10, "d2": 0.90, "d3": 0.42}


def scorer(design):
    name = design["design"]
    if name not in SCORES:
        raise ipsae_margin.MissingPredictionError("no prediction")
    return SCORES[name]


def test_a_decision_has_no_truth_value_to_mistake_for_a_pass():
    decision = ipsae_veto.VetoDecision(design="d1", vetoed=False, reason="x")
    with pytest.raises(ipsae_veto.VetoOnlyViolation):
        bool(decision)
    assert not hasattr(decision, "passed")
    assert not hasattr(decision, "ok")


def test_a_survivor_may_not_carry_a_score_at_all():
    with pytest.raises(ipsae_veto.VetoOnlyViolation) as exc:
        ipsae_veto.VetoDecision(design="d2", vetoed=False, reason="fine", score=0.9)
    assert "never blesses" in str(exc.value)
    # A veto may carry one: the number exists to justify the removal.
    assert ipsae_veto.VetoDecision(design="d1", vetoed=True, reason="low", score=0.1).score == 0.1


def test_the_filter_removes_the_poor_design_and_annotates_nothing_else():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=0.3)
    assert record["vetoed_designs"] == ["d1"]
    survivors = ipsae_veto.apply_veto(DESIGNS, record)
    assert [d["design"] for d in survivors] == ["d2", "d3"], "order preserved"
    assert all(a is b for a, b in zip(survivors, [DESIGNS[1], DESIGNS[2]])), (
        "survivors must be the same objects: the filter subtracts, it never annotates"
    )
    assert all(set(d) == {"design", "sequence"} for d in survivors)


def test_no_ipsae_number_is_recorded_for_any_survivor():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=0.3)
    for decision in record["decisions"]:
        if decision["verdict"] == "not_vetoed":
            assert "ipsae" not in decision, (
                "a survivor's score must not exist anywhere downstream, or something will "
                "rank on it"
            )
    blob = json.dumps(record)
    assert "0.900000" not in blob and "0.420000" not in blob
    assert "0.100000" in blob, "the vetoed design's score is recorded, to justify the veto"


def test_the_record_says_in_its_own_words_that_it_cannot_bless():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=0.3)
    assert record["mode"] == "veto_only"
    assert record["may_refuse_a_design"] is True
    assert record["may_pass_rank_or_certify_a_design"] is False
    assert record["needs_signed_threshold"] is False
    assert "not evidence of anything" in record["survivors_mean"]
    assert "cannot clear it" in record["margin_stage_unaffected"]


def test_an_unset_floor_is_inert_not_permissive():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=None)
    assert record["status"] == "inactive"
    assert record["vetoed_designs"] == []
    assert ipsae_veto.apply_veto(DESIGNS, record) == DESIGNS
    assert all(d["verdict"] == "not_vetoed" for d in record["decisions"])
    assert all("ipsae" not in d for d in record["decisions"])
    assert ipsae_veto.veto_floor_from_policy({}) is None
    assert ipsae_veto.veto_floor_from_policy({"curation": {"ipsae_veto_floor": 0.4}}) == 0.4


def test_no_veto_floor_number_is_hardcoded():
    source = (
        __import__("pathlib").Path(ipsae_veto.__file__).read_text(encoding="utf-8")
    )
    assert "DEFAULT_VETO_FLOOR" not in source
    assert not hasattr(ipsae_veto, "DEFAULT_VETO_FLOOR")


def test_a_missing_prediction_never_vetoes():
    designs = DESIGNS + [{"design": "d4", "sequence": "EEEE"}]
    record = ipsae_veto.veto_designs(designs, score_for=scorer, veto_floor=0.3)
    assert "d4" not in record["vetoed_designs"]
    d4 = [d for d in record["decisions"] if d["design"] == "d4"][0]
    assert "absence of evidence" in d4["reason"]


def test_every_record_carries_the_caveat():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=0.3)
    assert record["caveat"] == ipsae_margin.CAVEAT
    assert all(d["caveat"] == ipsae_margin.CAVEAT for d in record["decisions"])
    for phrase in (
        "no orthogonal re-predictor",
        "does NOT cancel under",
        "provisional until wet-lab confirmation",
    ):
        assert phrase in ipsae_margin.CAVEAT


def test_a_margin_record_from_the_candidate_also_carries_the_caveat():
    scores = {("MUT", "S"): 0.8, ("WT", "S"): 0.5}
    record = margin.score_wild_type_margin(
        {"mutant_peptide": "MUT", "wild_type_peptide": "WT"},
        [{"design": "d1", "sequence": "S"}],
        scorer=lambda p, d: scores[(p, d)],
        scorer_id=ipsae_margin.SCORER_ID,
    )
    assert record["evidence_caveat"] == ipsae_margin.CAVEAT
    assert record["clears_margin_threshold"] is False


def test_vetoing_designs_does_not_let_the_margin_stage_pass():
    record = ipsae_veto.veto_designs(DESIGNS, score_for=scorer, veto_floor=0.3)
    survivors = ipsae_veto.apply_veto(DESIGNS, record)
    after = margin.score_wild_type_margin(
        {"mutant_peptide": "YLMSYNGVV", "wild_type_peptide": "YLDSYNGVV"}, survivors
    )
    assert after["status"] == "not_computed"
    assert after["threshold_status"] == "not_established"
    assert margin.SIGNED_STRUCTURAL_SCORER_IDS == frozenset()
