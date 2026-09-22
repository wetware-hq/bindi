from bindi.policy import load_policy, normalize_policy, resolve_bindcraft_campaign


def test_bare_bindcraft_json_normalizes():
    raw = {"target": "hPDL1", "modality": "binder", "project_folder": "results/x"}
    policy = normalize_policy(raw)
    assert policy["schema"] == "bindi.policy.v1"
    assert policy["bindcraft"]["target"] == "hPDL1"


def test_resolve_paths(tmp_path):
    policy = load_policy(None, payload={"bindcraft": {"project_folder": "results/run"}, "gallery": "out"})
    campaign, gallery = resolve_bindcraft_campaign(policy, workdir=tmp_path)
    assert campaign["project_folder"] == str(tmp_path / "results" / "run")
    assert gallery == tmp_path / "out"
