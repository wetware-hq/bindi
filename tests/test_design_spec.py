"""The immunology campaign resolves a real design spec, and says what it still needs.

A green here means: `campaigns/nyeso1_a0201_pmhc.yaml` parses, describes the three-chain
NY-ESO-1 157-165 / HLA-A*02:01 pMHC complex that `CASE.md` specifies, carries its own
provenance and its unresolved questions in the file, and the immunology campaign policy
resolves it so a fleet run no longer stops at a missing design spec. It does not mean the
spec has been submitted, priced here, or scientifically signed off.
"""

import json
from pathlib import Path

from bindi import boltz_hosted
from bindi.boltz import resolve_design_spec_path
from bindi.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "campaigns" / "nyeso1_a0201_pmhc.yaml"
POLICY_PATH = ROOT / "campaigns" / "neoantigen_placeholder.json"


def _spec():
    return boltz_hosted.load_design_spec(SPEC_PATH)


def test_immunology_policy_resolves_a_design_spec():
    policy = load_policy(POLICY_PATH)
    path = resolve_design_spec_path(policy["boltz"], ROOT)
    assert path == SPEC_PATH
    assert policy["boltz"]["design_spec_target_id"] == "NYESO1-A0201"


def test_spec_describes_the_case_target():
    spec = _spec()
    assert spec["target_id"] == "NYESO1-A0201"
    assert "CASE.md" in spec["case_reference"]
    entities = spec["target"]["entities"]
    assert [e["chain_ids"][0] for e in entities] == ["A", "B", "C"]
    assert [len(e["value"]) for e in entities] == [275, 99, 9]
    peptide = entities[2]
    assert peptide["value"] == "SLLMWITQC", "NY-ESO-1 157-165, per CASE.md"
    assert peptide["epitope_residues"] == list(range(9))
    binder = spec["binder_specification"]
    assert binder["modality"] == "custom_protein"
    assert binder["entities"][0]["value"] == "60..100"


def test_the_negative_design_panel_is_the_selected_one_and_no_campaign_size_is_set():
    """Rewritten 2026-10-21: the panel is no longer absent, it is CHOSEN, by Mathew.

    It was previously an error for the spec to carry a non_binding_residues panel at all,
    because picking one was an unmade scientific decision. Mathew made it on 2026-10-21,
    accepting OD-2's recommendation of the TARGETED panel. What the test now pins is that
    the panel in the spec is exactly that panel and not an invented one: the residues are
    the source document's, the restriction triad is left contactable, and the peptide
    chain is untouched. Campaign size is still Mathew's to set and still absent.
    """
    spec = _spec()
    chains = {e["chain_ids"][0]: e for e in spec["target"]["entities"]}
    expected_a = (
        list(range(56, 62))
        + list(range(76, 84))
        + list(range(137, 149))
        + list(range(166, 180))
        + list(range(180, 275))
    )
    assert chains["A"]["non_binding_residues"] == expected_a
    assert chains["B"]["non_binding_residues"] == list(range(99))
    assert "non_binding_residues" not in chains["C"]
    assert not {64, 68, 154} & set(expected_a), "restriction triad stays contactable"
    assert "num_proteins" not in spec, "campaign size is the policy's, and Mathew's, to set"
    assert "global_design_filters" not in spec


def test_spec_and_policy_record_what_still_needs_mathew():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert "NEEDS MATHEW" in text
    # The case-record mismatch and the absent panel were resolved on 2026-10-21; the spec
    # must still record HOW they were resolved, and what is still outstanding.
    for topic in (
        "CASE RECORD: RESOLVED",
        "NEGATIVE DESIGN: SELECTED",
        "non_binding_residues",
        "CAMPAIGN SIZE",
        "JUDGEMENT, not verified",
        "STILL OUTSTANDING",
    ):
        assert topic in text
    assert "SUBMITTING THIS SPEC SPENDS MONEY" in text
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy["_needs_mathew"], "the policy must carry the unresolved questions too"
    assert any("UNSIGNED" in note for note in policy["_needs_mathew"])
    assert any("sign-off is still outstanding" in note for note in policy["_needs_mathew"])


def test_designed_chain_is_the_only_designed_region():
    spec = _spec()
    designed = [
        entity
        for entity in spec["binder_specification"]["entities"]
        if entity["type"] == "designed_protein"
    ]
    assert len(designed) == 1 and designed[0]["chain_ids"] == ["D"]
    for entity in spec["target"]["entities"]:
        assert entity["type"] == "protein", "target chains are fixed context, never designed"
