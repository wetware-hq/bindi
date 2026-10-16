import json
import shutil
from pathlib import Path

import pytest

from bindi import data
from bindi.curate import curate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _gallery(tmp_path, fixture="sample_ranked_disagree.csv", top_n=3):
    project = tmp_path / "results" / "x"
    ranked = project / "3_Ranked"
    ranked.mkdir(parents=True)
    shutil.copy(FIXTURES / fixture, ranked / "!_Ranked.csv")
    curate(
        {"bindcraft": {"project_folder": str(project)}, "curation": {"top_n": top_n}, "gallery": "gallery"},
        workdir=tmp_path,
    )
    return json.loads((tmp_path / "gallery" / "index.json").read_text(encoding="utf-8"))


# --- Learning 1: RELATIVE ONLY -------------------------------------------------

def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


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

def test_provenance_and_basis_present(tmp_path):
    m = _gallery(tmp_path)
    prov = m.get("provenance")
    assert prov and prov.get("status") == "in_silico_hypothesis"
    assert "experimental" in prov.get("statement", "").lower()
    assert prov.get("ensemble_basis")


# --- Learning 3: BENCHMARK DISCIPLINE + CONVERGENCE ----------------------------

def test_manifest_records_universes_and_disagreement(tmp_path):
    m = _gallery(tmp_path)
    ens = m["ensemble"]
    assert ens["available"] is True
    assert ens["universes"] == ["i_pDAE", "i_pTM"]
    assert ens["disagreement_summary"] is not None
    assert {"n", "min", "max", "mean", "median"} <= set(ens["disagreement_summary"])


def test_benchmark_record_present_and_unchecked(tmp_path):
    m = _gallery(tmp_path)
    bench = m.get("benchmark")
    assert bench is not None
    assert bench.get("status") == "unchecked"
    assert bench.get("value") == "unchecked"
    # module-level, data-file-driven record
    direct = data.benchmark_record()
    assert direct["status"] == "unchecked"
    assert direct.get("basis")


def test_fewer_than_two_universes_falls_back_without_failing(tmp_path):
    # A table with only one higher-better metric -> fallback, not an error.
    project = tmp_path / "results" / "y"
    ranked = project / "3_Ranked"
    ranked.mkdir(parents=True)
    (ranked / "!_Ranked.csv").write_text(
        "design,rank,i_pDAE,Binder_Sequence\na,1,0.7,AAAA\nb,2,0.5,CCCC\n", encoding="utf-8"
    )
    curate(
        {"bindcraft": {"project_folder": str(project)}, "curation": {"top_n": 2}, "gallery": "g"},
        workdir=tmp_path,
    )
    m = json.loads((tmp_path / "g" / "index.json").read_text(encoding="utf-8"))
    assert m["ensemble"]["available"] is False
    assert m["ensemble"]["fallback"]
    assert all("ensemble" not in e for e in m["entries"])


# --- Learning 4: LICENCE TIERS -------------------------------------------------

def test_deployable_path_rejects_evaluation_only_tools():
    with pytest.raises(data.LicenceTierError):
        data.assert_deployable(["BindCraft2", "FEP+"])
    with pytest.raises(data.LicenceTierError):
        data.assert_deployable(["PyRosetta"])


def test_deployable_path_accepts_deployable_tools():
    data.assert_deployable(["BindCraft2"])  # must not raise


def test_tool_tiers_are_cited():
    tiers = data.tool_tiers()
    assert "BindCraft2" in tiers
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
