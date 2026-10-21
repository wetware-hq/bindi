"""Registering ipSAE as a candidate structural scorer does not let the margin stage pass.

A green here means: the candidate is registered with an id and an evidence level that names
what the quantity is, the plumbing to compute a margin from it exists, and *none of that
changes the refusal*. `SIGNED_STRUCTURAL_SCORER_IDS` is still an empty frozenset, no
combination of inputs yields `threshold_status: "established"`, the refusal wording is
byte-identical to what it was before the candidate existed, no threshold number appears in
any module, and the design hold still names the missing requirement. It says nothing about
whether ipSAE should ever be signed off; that is `docs/PROPOSAL-ipsae-structural-margin.md`,
a proposal, not code.
"""

from pathlib import Path

import pytest

from bindi import case as case_mod
from bindi import ipsae_margin, margin

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "docs" / "PROPOSAL-ipsae-structural-margin.md"

# The refusal wording, pinned verbatim. If a change to the margin stage edits either of
# these strings, this test fails and the change has to be argued for explicitly.
EXPECTED_THRESHOLD_REASON = (
    "Margin threshold is not established: no structural scorer whose margin threshold has "
    "been signed off for the Boltz-based pipeline exists in this repository. Boltz is the "
    "generator; BindCraft-on-Modal, the only scorer the signed margin rule previously named, "
    "has been removed from the design path, and no replacement threshold has been signed off "
    "by the Chief Scientist. Sequence-level scorers remain selectable for reproduction and "
    "never clear the margin stage. Until such a threshold is signed, the immunology margin "
    "stage refuses by construction and no margin number is written."
)
EXPECTED_MISSING_REQUIREMENT = (
    "a structural scorer whose margin threshold has been signed off for the Boltz-based "
    "pipeline"
)


# ---- the candidate is registered, and honestly described ---------------------------

def test_candidate_is_registered_with_its_own_evidence_level():
    entry = margin.candidate_structural_scorer(ipsae_margin.SCORER_ID)
    assert ipsae_margin.SCORER_ID == "ipsae_boltz_cpu_v1"
    assert entry["evidence_level"] == (
        "interface_pae_from_boltz_prediction_not_a_binding_measurement"
    )
    assert entry["structural"] is True
    assert entry["gpu_required"] is False
    assert entry["self_hosted"] is False, "ipSAE reads artifacts; it hosts no model"
    assert entry["runs_a_model"] is False
    assert entry["signed_off"] is False
    assert entry["threshold_status"] == "not_established"
    assert entry["threshold_proposal"] == "docs/PROPOSAL-ipsae-structural-margin.md"
    assert "not an affinity" in entry["what_it_does_not_measure"]


def test_an_unknown_structural_scorer_is_refused_by_name():
    with pytest.raises(ValueError) as exc:
        margin.candidate_structural_scorer("something_invented")
    assert "none has a signed threshold" in str(exc.value)


# ---- and it still cannot clear the margin ------------------------------------------

def test_signed_set_is_still_empty_and_the_candidate_is_not_in_it():
    assert margin.SIGNED_STRUCTURAL_SCORER_IDS == frozenset()
    assert ipsae_margin.SCORER_ID not in margin.SIGNED_STRUCTURAL_SCORER_IDS
    assert not margin.is_signed_structural_scorer(ipsae_margin.SCORER_ID)
    assert not (
        margin.CANDIDATE_STRUCTURAL_SCORER_IDS & margin.SIGNED_STRUCTURAL_SCORER_IDS
    ), "a candidate is never also signed"


def test_no_input_combination_yields_an_established_threshold():
    ids = [None, ipsae_margin.SCORER_ID, "blosum_position_v2", "class_bucket_v1", "whatever"]
    levels = [None, ipsae_margin.EVIDENCE_LEVEL, "sequence_level_not_structure_validated"]
    for scorer_id in ids:
        for level in levels:
            fields = margin._threshold_fields(scorer_id=scorer_id, evidence_level=level)
            assert fields["threshold_status"] == "not_established"
            assert fields["clears_margin_threshold"] is False


def test_the_candidate_is_classified_as_unsigned_in_the_record():
    fields = margin._threshold_fields(
        scorer_id=ipsae_margin.SCORER_ID, evidence_level=ipsae_margin.EVIDENCE_LEVEL
    )
    assert fields["threshold_scorer_class"] == "candidate_structural_not_signed"
    assert fields["threshold_proposal"] == "docs/PROPOSAL-ipsae-structural-margin.md"


def test_a_margin_computed_from_the_candidate_still_does_not_clear_the_stage():
    """The "if a threshold were signed" path, exercised: a margin is stated, and refused."""
    artifacts = {("MUT", "BINDER"): 0.80, ("WT", "BINDER"): 0.55}
    record = margin.score_wild_type_margin(
        {"mutant_peptide": "MUT", "wild_type_peptide": "WT"},
        [{"design": "d1", "sequence": "BINDER"}],
        scorer=lambda peptide, design: artifacts[(peptide, design)],
        scorer_id=ipsae_margin.SCORER_ID,
        evidence_level=ipsae_margin.EVIDENCE_LEVEL,
    )
    assert record["status"] == "stated"
    assert record["threshold_status"] == "not_established"
    assert record["clears_margin_threshold"] is False
    assert record["threshold_scorer_class"] == "candidate_structural_not_signed"
    reasons = case_mod.design_hold_reasons(
        {"source": "case_record", "signed": True, "consent_design_use": True},
        human_reference={"status": "pass"},
        margin=record,
    )
    assert any("structural margin threshold is not established" in r for r in reasons)


def test_the_refusal_wording_is_unchanged():
    assert margin.THRESHOLD_NOT_ESTABLISHED_REASON == EXPECTED_THRESHOLD_REASON
    assert margin.MISSING_REQUIREMENT == EXPECTED_MISSING_REQUIREMENT
    record = margin.score_wild_type_margin(
        {"mutant_peptide": "YLMSYNGVV", "wild_type_peptide": "YLDSYNGVV"}, []
    )
    assert record["status"] == "not_computed"
    assert record["threshold_reason"] == EXPECTED_THRESHOLD_REASON
    assert record["threshold_missing"] == EXPECTED_MISSING_REQUIREMENT


def test_no_threshold_number_lives_in_code():
    for module in ("margin.py", "ipsae_margin.py"):
        source = (ROOT / "src" / "bindi" / module).read_text(encoding="utf-8")
        assert '"established"' not in source.replace('"not_established"', ""), (
            f"{module} must contain no path that names an established threshold"
        )
        assert "MARGIN_THRESHOLD" not in source
        assert "SPREAD_MULTIPLIER" not in source
    assert ipsae_margin.THRESHOLD_STATUS == "not_established"
    assert not hasattr(ipsae_margin, "MARGIN_THRESHOLD")


# ---- the plumbing itself refuses rather than inventing a number ---------------------

def test_the_scorer_refuses_when_a_prediction_is_missing(tmp_path):
    scorer = ipsae_margin.make_margin_scorer(lambda peptide, design: None)
    with pytest.raises(ipsae_margin.MissingPredictionError) as exc:
        scorer("YLMSYNGVV", "BINDERSEQ")
    assert "refused rather than estimated" in str(exc.value)

    with pytest.raises(ipsae_margin.MissingPredictionError):
        ipsae_margin.score_complex_min_ipsae(
            tmp_path / "absent_pae.npz", tmp_path / "absent.cif"
        )


def test_the_scorer_refuses_an_ambiguous_interface(monkeypatch, tmp_path):
    pae = tmp_path / "pae.npz"
    cif = tmp_path / "x_predicted.cif"
    pae.write_bytes(b"")
    cif.write_bytes(b"")
    monkeypatch.setattr(
        ipsae_margin.ipsae,
        "score_from_paths",
        lambda *a, **k: {"pairs": {"A|B": {"min_ipsae": 0.5}, "B|C": {"min_ipsae": 0.4}}},
    )
    with pytest.raises(ipsae_margin.MissingPredictionError) as exc:
        ipsae_margin.score_complex_min_ipsae(pae, cif)
    assert "rather than guessing which" in str(exc.value)
    # Naming the interface explicitly is how you resolve it, not a default.
    assert ipsae_margin.score_complex_min_ipsae(pae, cif, chain_pair=("B", "A")) == 0.5


# ---- the proposal is a document, not code -------------------------------------------

def test_the_proposal_exists_and_states_what_a_signature_would_attest_to():
    assert PROPOSAL.exists(), "the threshold proposal must live in the repository"
    raw = PROPOSAL.read_text(encoding="utf-8")
    text = " ".join(raw.split())  # prose is hard-wrapped; match on words, not line breaks
    for heading in (
        "What ipSAE measures",
        "What ipSAE does not measure",
        "The measurement that would establish a threshold",
        "whether it should carry over",
        "Cost through the APPR gate",
        "What a signature would be attesting to",
    ):
        assert heading in text, f"the proposal must cover: {heading}"
    assert "ipsae_boltz_cpu_v1" in text
    assert "20 independent" in text, "the spread protocol must name its seed count"
    assert "spread is measured first" in text, "spread must precede any named threshold"
    assert "2.0 \u00d7 spread" in text, "the prior rule's multiplier must be stated"
    assert "$15.00" in text and "$10.00" in text, "the cost must be stated in dollars"
    assert "nothing has been spent" in text
