import json
from pathlib import Path

from bindi.curate import FIXTURE_PATH_STATEMENT, curate
from bindi.run import run


def test_dry_run_pipeline_builds_gallery(tmp_path):
    policy = {
        "schema": "bindi.policy.v1",
        "bindcraft": {
            "target": "hPDL1",
            "project_folder": "results/demo",
            "number_of_final_designs": 2,
        },
        "curation": {"rank_on": ["i_pDAE"], "top_n": 2},
        "gallery": "gallery",
    }
    outcome = run(policy, workdir=tmp_path, dry_run=True)
    gallery = Path(outcome["curate"]["gallery"])
    manifest = json.loads((gallery / "index.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "bindi.gallery.v1"
    assert len(manifest["entries"]) == 2
    assert (gallery / "ranked.csv").exists()
    assert manifest.get("fixture_path_statement") == FIXTURE_PATH_STATEMENT
    structures = manifest.get("structures")
    assert structures and structures.get("copied") == 0
    assert structures.get("status") == "not_run"
    assert structures.get("reason")


def test_fixture_path_statement_required_when_fixture_path_used(tmp_path):
    """Fails if fixture_path_statement is missing or empty when the fixture path was used."""
    project = tmp_path / "results" / "fixture_only"
    ranked_dir = project / "3_Ranked"
    ranked_dir.mkdir(parents=True)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "sample_ranked.csv"
    (ranked_dir / "!_Ranked.csv").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    curate(
        {"bindcraft": {"project_folder": str(project)}, "curation": {"top_n": 1}, "gallery": "gallery"},
        workdir=tmp_path,
        fixture_path=True,
    )
    manifest = json.loads((tmp_path / "gallery" / "index.json").read_text(encoding="utf-8"))
    statement = manifest.get("fixture_path_statement")
    assert statement and statement.strip(), "fixture_path_statement must be non-empty when fixture path was used"
    assert statement == FIXTURE_PATH_STATEMENT


def test_curate_existing_campaign(tmp_path):
    project = tmp_path / "results" / "existing"
    ranked_dir = project / "3_Ranked"
    ranked_dir.mkdir(parents=True)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "sample_ranked.csv"
    (ranked_dir / "!_Ranked.csv").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    policy = {
        "bindcraft": {"project_folder": str(project)},
        "curation": {"top_n": 1},
        "gallery": "gallery",
    }
    result = curate(policy, workdir=tmp_path)
    assert result["count"] == 1
