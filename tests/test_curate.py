import json
from pathlib import Path

from bindi.curate import curate
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
