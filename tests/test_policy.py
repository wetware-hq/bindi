from bindi.policy import load_policy, normalize_policy, resolve_boltz_campaign


def test_bare_bindcraft_json_normalizes():
    raw = {"target": "hPDL1", "modality": "binder", "project_folder": "results/x"}
    policy = normalize_policy(raw)
    assert policy["schema"] == "bindi.policy.v1"
    assert policy["boltz"]["target"] == "hPDL1"


def test_legacy_bindcraft_key_migrates_to_boltz():
    policy = normalize_policy(
        {"bindcraft": {"target": "hPDL1", "project_folder": "results/x"}, "gallery": "out"}
    )
    assert "bindcraft" not in policy
    assert policy["boltz"]["target"] == "hPDL1"


def test_resolve_paths(tmp_path):
    policy = load_policy(None, payload={"boltz": {"project_folder": "results/run"}, "gallery": "out"})
    campaign, gallery = resolve_boltz_campaign(policy, workdir=tmp_path)
    assert campaign["project_folder"] == str(tmp_path / "results" / "run")
    assert gallery == tmp_path / "out"
