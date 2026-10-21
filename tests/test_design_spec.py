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


def test_spec_carries_no_invented_negative_design_and_no_campaign_size():
    spec = _spec()
    for entity in spec["target"]["entities"]:
        assert "non_binding_residues" not in entity, (
            "a non_binding_residues panel is a safety-relevant scientific choice awaiting "
            "sign-off; it must not be invented in the spec"
        )
    assert "num_proteins" not in spec, "campaign size is the policy's, and Mathew's, to set"
    assert "global_design_filters" not in spec


def test_spec_and_policy_record_what_still_needs_mathew():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert "NEEDS MATHEW" in text
    for topic in ("CASE RECORD MISMATCH", "non_binding_residues", "CAMPAIGN SIZE"):
        assert topic in text
    assert "SUBMITTING THIS SPEC SPENDS MONEY" in text
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy["_needs_mathew"], "the policy must carry the unresolved questions too"
    assert any("mismatch" in note.lower() for note in policy["_needs_mathew"])


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
