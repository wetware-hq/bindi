import json
import shutil
from pathlib import Path

import pytest

from bindi import data
from bindi.curate import DISCLOSURES, curate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _gallery(tmp_path, fixture="sample_ranked_disagree.csv", top_n=3, use_ensemble=False, curation=None):
    project = tmp_path / "results" / "x"
    ranked = project / "3_Ranked"
    ranked.mkdir(parents=True)
    shutil.copy(FIXTURES / fixture, ranked / "!_Ranked.csv")
    cur = {"top_n": top_n}
    if curation:
        cur.update(curation)
    curate(
        {"bindcraft": {"project_folder": str(project)}, "curation": cur, "gallery": "gallery"},
        workdir=tmp_path,
        use_ensemble=use_ensemble,
    )
    return json.loads((tmp_path / "gallery" / "index.json").read_text(encoding="utf-8"))


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


# --- Learning 1: RELATIVE ONLY -------------------------------------------------

def test_claim_field_present_and_relative(tmp_path):
    m = _gallery(tmp_path)
    assert "claim" in m and m["claim"]
    assert "relative" in m["claim"].lower()


def test_no_key_looks_like_absolute_affinity(tmp_path):
    m = _gallery(tmp_path)
    offenders = [k for k in _all_keys(m) if data.looks_like_absolute_affinity(k)]
    assert offenders == [], f"absolute-affinity-looking keys in manifest: {offenders}"
    for entry in m["entries"]:
        for score_key in entry["scores"]:
            assert not data.looks_like_absolute_affinity(score_key), score_key


@pytest.mark.parametrize("name", ["kcal_mol", "Kd", "ki", "IC50", "affinity", "dG", "ddG", "binding_energy"])
def test_absolute_affinity_detector_flags_known_names(name):
    assert data.looks_like_absolute_affinity(name)


@pytest.mark.parametrize("name", ["i_pDAE", "i_pTM", "pTM", "mean_rank", "disagreement", "rank"])
def test_absolute_affinity_detector_passes_legit_metrics(name):
    assert not data.looks_like_absolute_affinity(name)


# --- Learning 2: PROVENANCE / NOT-YET-EXPERIMENTAL -----------------------------

def test_provenance_present(tmp_path):
    m = _gallery(tmp_path)
    prov = m.get("provenance")
    assert prov and prov.get("status") == "in_silico_hypothesis"
    assert "experimental" in prov.get("statement", "").lower()


def test_structures_not_run_recorded_when_none_copied(tmp_path):
    m = _gallery(tmp_path)
    structures = m.get("structures")
    assert structures is not None
    assert structures.get("copied") == 0
    assert structures.get("status") == "not_run"
    reason = structures.get("reason")
    assert reason and reason.strip(), "structures.reason must be recorded when nothing was copied"


def test_structures_copied_status_when_mm_cif_present(tmp_path):
    project = tmp_path / "results" / "with_cif"
    ranked = project / "3_Ranked"
    ranked.mkdir(parents=True)
    shutil.copy(FIXTURES / "sample_ranked.csv", ranked / "!_Ranked.csv")
    cif = project / "pdl1_binder_seq1_model.cif"
    cif.write_text("data_demo\n", encoding="utf-8")
    curate(
        {
            "bindcraft": {"project_folder": str(project)},
            "curation": {"top_n": 1, "rank_on": ["i_pTM"]},
            "gallery": "gallery",
        },
        workdir=tmp_path,
    )
    m = json.loads((tmp_path / "gallery" / "index.json").read_text(encoding="utf-8"))
    structures = m["structures"]
    assert structures["copied"] == 1
    assert structures["status"] == "copied"
    assert "reason" not in structures


# --- Learning 3: BENCHMARK DISCIPLINE ------------------------------------------

def test_benchmark_record_present_and_unchecked(tmp_path):
    m = _gallery(tmp_path)
    bench = m.get("benchmark")
    assert bench is not None
    assert bench.get("status") == "unchecked"
    assert bench.get("value") == "unchecked"
    direct = data.benchmark_record()
    assert direct["status"] == "unchecked"
    assert direct.get("basis")


# --- Learning 4: LICENCE TIERS -------------------------------------------------

def test_deployable_path_rejects_evaluation_only_tools():
    with pytest.raises(data.LicenceTierError):
        data.assert_deployable(["BoltzGen", "FEP+"])
    with pytest.raises(data.LicenceTierError):
        data.assert_deployable(["PyRosetta"])


def test_deployable_path_accepts_deployable_tools():
    data.assert_deployable(["BoltzGen"])  # must not raise


def test_tool_tiers_are_cited():
    tiers = data.tool_tiers()
    assert "BoltzGen" in tiers
    for name, entry in tiers.items():
        assert entry.get("tier") in {"deployable", "evaluation_only"}, name
        assert entry.get("licence"), name
        assert entry.get("citation", "").startswith("http"), name
    assert data.tier_of("FEP+") == "evaluation_only"


def test_curate_manifest_tooling_is_deployable(tmp_path):
    m = _gallery(tmp_path)
    assert m["tooling"]["path"] == "deployable"
    for tool in m["tooling"]["tools"]:
        assert data.tier_of(tool) == "deployable"


# --- Ruling A: ENSEMBLE OFF BY DEFAULT -----------------------------------------

def test_default_run_emits_no_ensemble_field_anywhere(tmp_path):
    m = _gallery(tmp_path)  # use_ensemble defaults to False
    assert "ensemble" not in m, "top-level ensemble block must be absent by default"
    for entry in m["entries"]:
        assert "ensemble" not in entry, "per-entry ensemble key must be absent by default"
    # The word must not leak in via provenance either.
    assert "ensemble_basis" not in m.get("provenance", {})
    assert "ensemble" not in list(_all_keys(m))


def test_default_run_suppresses_rank_on_unconfirmed_metric(tmp_path):
    # Legacy rank_on i_pDAE has no confirmed direction; gallery is unordered with rank suppressed.
    m = _gallery(tmp_path, top_n=3, curation={"rank_on": ["i_pDAE"]})
    assert m.get("rank_suppressed") is True
    assert m.get("ordering") == "unordered"
    assert all("rank" not in e for e in m["entries"])
    # It must NOT present the worst-by-i_pTM candidate (contested_a) as the top/first "best".
    # Neutral (design-id) order carries no quality claim.
    assert [e["design"] for e in m["entries"]] == [
        "pdl1_contested_a",
        "pdl1_contested_c",
        "pdl1_steady_b",
    ]


def test_enabled_path_emits_full_ensemble(tmp_path):
    m = _gallery(tmp_path, use_ensemble=True, curation={"universes": ["i_pDAE", "i_pTM"]})
    ens = m["ensemble"]
    assert ens["available"] is True
    assert ens["universes"] == ["i_pDAE", "i_pTM"]
    assert ens["disagreement_summary"] is not None
    assert {"n", "min", "max", "mean", "median"} <= set(ens["disagreement_summary"])
    assert any("ensemble" in e for e in m["entries"])
    assert m["provenance"].get("ensemble_basis")


def test_enabled_via_policy_flag(tmp_path):
    m = _gallery(tmp_path, curation={"ensemble": True})
    assert "ensemble" in m
    assert m["ensemble"]["available"] is True


def test_enabled_path_reorders_by_consensus(tmp_path):
    m = _gallery(tmp_path, use_ensemble=True, curation={"universes": ["i_pDAE", "i_pTM"]})
    # steady_b wins consensus (worst_rank 2 vs 3 for the contested pair).
    assert m["entries"][0]["design"] == "pdl1_steady_b"


# --- Ruling B: FIVE DISCLOSURE LINES + NOT-A-RELEASE ---------------------------

def test_five_disclosures_release_and_claim(tmp_path):
    m = _gallery(tmp_path)
    disclosures = m.get("disclosures")
    assert isinstance(disclosures, list)
    for line in DISCLOSURES:
        assert line in disclosures, f"missing disclosure line: {line}"
    # Substance of each of the five, independent of exact punctuation.
    joined = " ".join(disclosures).lower()
    for needle in [
        "not for use with real patients",
        "published trial",
        "unverified",
        "specified but unbuilt",
        "no hazard screen",
    ]:
        assert needle in joined, f"missing disclosure substance: {needle}"
    # Existing claim/provenance kept alongside.
    assert "relative" in m["claim"].lower()
    assert m["provenance"]["status"] == "in_silico_hypothesis"
    # Explicitly not a release, with a reason naming the unapplied hazard screen.
    assert m["release"] is False
    assert "hazard screen" in m["not_a_release"].lower()


# --- Ruling C: WORDING ---------------------------------------------------------

def test_rank_labelled_as_pass_signal_not_potency(tmp_path):
    # When a rank IS shown (ranking on a confirmed-direction metric), it must be labelled a
    # pass-signal ordering, not potency.
    m = _gallery(tmp_path, curation={"rank_on": ["i_pTM"]})
    assert m.get("rank_suppressed") is not True
    meaning = m.get("rank_meaning", "").lower()
    assert "pass" in meaning and "signal" in meaning
    assert "not an affinity" in meaning or "not a potency" in meaning or "not affinity" in meaning


def test_reproducibility_not_deterministic(tmp_path):
    m = _gallery(tmp_path, curation=None)
    repro = m.get("reproducibility")
    assert repro and repro.get("model") == "reproducible-given-seed"
    assert "seed" in repro and "trajectories" in repro
    assert "deterministic" not in json.dumps(m).lower()
