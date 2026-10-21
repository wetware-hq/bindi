"""`generate` must never spend money as a side effect of being run."""
from __future__ import annotations

import json

import pytest

from bindi import boltz as api
from bindi.generate import generate

POLICY = {
    "schema": "bindi.policy.v1",
    "boltz": {
        "num_proteins": 10,
        "project_folder": "results/t",
        "target_entities": [{"chain_ids": ["C"], "value": "SLLMWITQC"}],
        "epitope_residues": {"C": [0, 1, 2]},
        "binder": {"modality": "custom_protein", "chain_ids": ["D"], "length": "60..100"},
    },
    "gallery": "gallery",
}


@pytest.fixture
def no_submit(monkeypatch):
    """Estimating is allowed; submitting blows up the test."""
    monkeypatch.setattr(api, "estimate_cost", lambda body: {"estimated_cost_usd": "0.5000"})

    def _boom(*args, **kwargs):
        raise AssertionError("generate submitted a paid design run without approval")

    monkeypatch.setattr(api, "submit", _boom)


def test_without_approval_it_estimates_and_submits_nothing(tmp_path, no_submit):
    result = generate(POLICY, workdir=tmp_path, dry_run=False)
    assert result["status"] == "awaiting_approval"
    assert result["estimate"]["estimated_cost_usd"] == "0.5000"
    body = json.loads((tmp_path / "results" / "t" / "request.json").read_text())
    assert sorted(body) == ["binder_specification", "num_proteins", "target"]
    assert result["request_digest"] == api.request_digest(body)
    assert not (tmp_path / "results" / "t" / "job.json").exists()


def test_blank_approval_is_not_approval(tmp_path, no_submit):
    assert generate(POLICY, workdir=tmp_path, approval="   ")["status"] == "awaiting_approval"


def test_with_an_explicit_approval_record_it_submits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "estimate_cost", lambda body: {"estimated_cost_usd": "0.5000"})
    seen = {}

    def _submit(body, *, approval):
        seen["approval"] = approval
        seen["body"] = body
        return "prot_des_FAKE"

    monkeypatch.setattr(api, "submit", _submit)
    result = generate(POLICY, workdir=tmp_path, approval="Mathew Chu, APPR gate, 2026-10-21")
    assert result["status"] == "submitted"
    assert result["job_id"] == "prot_des_FAKE"
    assert seen["approval"] == "Mathew Chu, APPR gate, 2026-10-21"
    assert "type" not in seen["body"] and "binder" not in seen["body"]
    job = json.loads((tmp_path / "results" / "t" / "job.json").read_text())
    assert job["job_id"] == "prot_des_FAKE"


def test_dry_run_touches_no_api_at_all(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("dry run reached the hosted API")

    monkeypatch.setattr(api, "estimate_cost", _boom)
    monkeypatch.setattr(api, "submit", _boom)
    result = generate(POLICY, workdir=tmp_path, dry_run=True)
    assert result["status"] == "dry_run"
