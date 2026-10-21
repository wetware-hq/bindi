"""The hosted Boltz API is the only generator path, and submission stays behind APPR.

A green here means: there is no local generator left to execute, the client authenticates
the way the official docs say (`BOLTZ_API_KEY` in an `x-api-key` header), it fails closed
without a key, the credential never reaches a request body, a trace or a provenance record,
and a paid submission is refused unless the APPR human spend gate is open for the exact
request body being submitted. Every test here uses an injected transport or no transport at
all: no test makes a network call, and no test submits a job.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bindi import boltz as boltz_mod
from bindi import boltz_hosted, spend_gate
from bindi.generate import generate
from bindi.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "campaigns" / "nyeso1_a0201_pmhc.yaml"
FAKE_KEY = "sk_bc_ws_test_FAKE-KEY-NOT-A-CREDENTIAL"


def _explode(*args, **kwargs):  # any network attempt is a test failure
    raise AssertionError("a network call was attempted; this test must not touch the API")


@pytest.fixture()
def fake_key(monkeypatch):
    monkeypatch.setenv(boltz_hosted.API_KEY_ENV, FAKE_KEY)
    monkeypatch.delenv(spend_gate.APPROVAL_ENV, raising=False)
    monkeypatch.setattr(boltz_hosted, "_urllib_transport", _explode)
    return FAKE_KEY


def _request():
    spec = boltz_hosted.load_design_spec(SPEC)
    return boltz_hosted.build_design_request({"modality": "binder", "num_designs": 10}, spec)


def _approval(tmp_path, sha, **overrides):
    record = {
        "schema": spend_gate.SCHEMA,
        "gate": "APPR",
        "approved": True,
        "approved_by": "Mathew Chu (test fixture, not a real approval)",
        "cap_usd": 15.0,
        "request_sha256": sha,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "on_deny": "HALT",
    }
    record.update(overrides)
    path = tmp_path / "appr.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


# ---- there is no local generator any more -----------------------------------------

def test_no_local_binary_path_remains():
    for gone in ("boltzgen_executable", "build_boltzgen_command", "maybe_rerank_with_boltzgen"):
        assert not hasattr(boltz_mod, gone), f"{gone} executes a local generator; it must be gone"
    assert not hasattr(boltz_mod, "GENERATOR_CMD_ENV")
    for module in ("boltz.py", "generate.py"):
        source = (ROOT / "src" / "bindi" / module).read_text(encoding="utf-8")
        assert "subprocess" not in source, f"{module} must not execute a process for generation"
        assert "BOLTZGEN_CMD" not in source
    assert boltz_mod.GENERATOR_ENGINE == "hosted_boltz_api"


def test_job_parameters_translate_policy_to_api_names():
    params = boltz_mod.job_parameters({"modality": "binder", "num_designs": 300, "campaign_name": "c1"})
    assert params["num_proteins"] == 300
    assert params["modality"] == "custom_protein"
    assert params["name"] == "c1"
    # The API floor is 10; a smaller campaign number is raised and the request recorded.
    small = boltz_mod.job_parameters({"modality": "peptide", "num_designs": 3})
    assert small["num_proteins"] == boltz_mod.MIN_NUM_PROTEINS == 10
    assert small["num_proteins_requested"] == 3
    assert small["modality"] == "peptide"


# ---- authentication ----------------------------------------------------------------

def test_missing_key_fails_closed_before_any_network(monkeypatch):
    monkeypatch.delenv(boltz_hosted.API_KEY_ENV, raising=False)
    monkeypatch.setattr(boltz_hosted, "_urllib_transport", _explode)
    with pytest.raises(boltz_hosted.BoltzCredentialMissing) as exc:
        boltz_hosted.auth_check()
    assert boltz_hosted.API_KEY_ENV in str(exc.value)
    assert not boltz_hosted.has_api_key()


def test_auth_check_is_a_read_only_get_with_the_documented_header(fake_key):
    seen = {}

    def transport(method, url, headers, body):
        seen.update(method=method, url=url, headers=headers, body=body)
        return 200, {"principal_type": "api_key", "key_type": "workspace", "mode": "live"}

    out = boltz_hosted.auth_check(transport=transport)
    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.boltz.bio/compute/v1/auth/me"
    assert seen["body"] is None, "an auth check sends no body and creates no work"
    assert seen["headers"][boltz_hosted.AUTH_HEADER] == fake_key
    assert "authorization" not in {k.lower() for k in seen["headers"]}
    assert out["auth_header"] == "x-api-key"


def test_the_credential_never_reaches_a_request_or_a_provenance_record(fake_key):
    request = _request()
    provenance = boltz_hosted.design_provenance(request, design_spec_path=SPEC, submitted=False)
    blob = json.dumps({"request": request, "provenance": provenance})
    assert FAKE_KEY not in blob
    assert provenance["credential_env"] == boltz_hosted.API_KEY_ENV
    assert provenance["self_hosted"] is False
    assert provenance["endpoint"] == "https://api.boltz.bio/compute/v1/protein/design"
    assert provenance["submitted"] is False
    assert provenance["design_spec_sha256"]


# ---- request building is free ------------------------------------------------------

def test_request_body_matches_the_documented_design_shape():
    request = _request()
    assert set(request) == {"num_proteins", "target", "binder_specification"}
    assert request["num_proteins"] >= boltz_mod.MIN_NUM_PROTEINS
    chains = [entity["chain_ids"][0] for entity in request["target"]["entities"]]
    assert chains == ["A", "B", "C"]
    peptide = request["target"]["entities"][2]
    assert peptide["value"] == "SLLMWITQC"
    assert peptide["epitope_residues"] == list(range(9)), "0-based, per chain, 0-8 for a 9-mer"
    assert request["binder_specification"]["modality"] == "custom_protein"
    assert request["binder_specification"]["entities"][0]["value"] == "60..100"
    assert "global_design_filters" not in request, "omitted on purpose: keeps the API default"


def test_type_discriminated_shape_is_selectable_and_legacy_is_the_default():
    spec = boltz_hosted.load_design_spec(SPEC)
    typed = boltz_hosted.build_design_request({"modality": "binder"}, spec, shape="binder")
    assert typed["type"] == "binder" and "binder" in typed
    assert boltz_hosted.DEFAULT_REQUEST_SHAPE == "legacy"
    with pytest.raises(ValueError):
        boltz_hosted.build_design_request({}, spec, shape="whatever")


def test_a_spec_without_a_designed_chain_is_refused():
    with pytest.raises(boltz_hosted.BoltzApiError):
        boltz_hosted.build_design_request({}, {"target": {"entities": [{"value": "AAA"}]}})


# ---- the APPR spend gate -----------------------------------------------------------

def test_submission_is_refused_when_no_approval_exists(fake_key):
    request = _request()
    with pytest.raises(spend_gate.SpendGateClosed) as exc:
        boltz_hosted.submit_design(request, estimated_cost_usd=0.5, transport=_explode)
    assert "nothing submitted, nothing spent" in str(exc.value)
    assert spend_gate.APPROVAL_ENV in str(exc.value)


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"approved": False}, "approved is not true"),
        ({"approved_by": "  "}, "approved_by is empty"),
        ({"cap_usd": 0}, "cap_usd must be a positive number"),
        ({"cap_usd": 0.01}, "exceeds the approved cap"),
        ({"request_sha256": "0" * 64}, "does not match the request"),
        ({"on_deny": "CONTINUE"}, "on_deny"),
        ({"expires_at": "2000-01-01T00:00:00Z"}, "expired"),
        ({"schema": "something.else"}, "schema must be"),
    ],
)
def test_every_broken_approval_keeps_the_gate_closed(fake_key, tmp_path, overrides, fragment):
    request = _request()
    sha = boltz_hosted.request_sha256(request)
    path = _approval(tmp_path, sha, **overrides)
    with pytest.raises(spend_gate.SpendGateClosed) as exc:
        boltz_hosted.submit_design(
            request, estimated_cost_usd=0.5, approval_file=path, transport=_explode
        )
    assert fragment in str(exc.value)


def test_an_unpriced_job_is_never_submitted(fake_key, tmp_path):
    request = _request()
    path = _approval(tmp_path, boltz_hosted.request_sha256(request))
    with pytest.raises(spend_gate.SpendGateClosed) as exc:
        boltz_hosted.submit_design(request, approval_file=path, transport=_explode)
    assert "no estimated cost" in str(exc.value)


def test_an_open_gate_submits_exactly_one_post_and_records_provenance(fake_key, tmp_path):
    """The only test that lets a submission through, and it goes to an injected transport."""
    request = _request()
    sha = boltz_hosted.request_sha256(request)
    path = _approval(tmp_path, sha)
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, json.loads(body)))
        return 200, {
            "id": "prot_des_run_test",
            "status": "pending",
            "pipeline": "boltzprot",
            "pipeline_version": "1.0",
            "livemode": False,
            "workspace_id": "ws_test",
        }

    out = boltz_hosted.submit_design(
        request, estimated_cost_usd=0.5, approval_file=path, transport=transport
    )
    assert len(calls) == 1
    method, url, headers, sent = calls[0]
    assert (method, url) == ("POST", "https://api.boltz.bio/compute/v1/protein/design")
    assert headers[boltz_hosted.AUTH_HEADER] == FAKE_KEY
    assert sent == request, "the approved body is what is sent, byte for byte"
    prov = out["provenance"]
    assert prov["job_id"] == "prot_des_run_test"
    assert prov["submitted"] is True
    assert prov["request_sha256"] == sha
    assert prov["pipeline"] == "boltzprot"
    assert prov["spend_gate"]["open"] is True
    assert prov["spend_gate"]["approved_by"].startswith("Mathew")
    assert FAKE_KEY not in json.dumps(prov)


def test_gate_status_is_free_to_ask_and_closed_by_default(monkeypatch):
    monkeypatch.delenv(spend_gate.APPROVAL_ENV, raising=False)
    status = spend_gate.gate_status(request_sha256="a" * 64, estimated_cost_usd=1.0)
    assert status["open"] is False
    assert status["on_deny"] == "HALT"
    assert status["problems"]
    assert not hasattr(spend_gate, "approve"), "nothing inside bindi may open this gate"


# ---- generate() stops at the gate ---------------------------------------------------

def test_generate_refuses_a_real_run_while_the_gate_is_closed(fake_key, tmp_path):
    policy = load_policy(None, payload={
        "boltz": {
            "modality": "binder",
            "design_spec": "campaigns/nyeso1_a0201_pmhc.yaml",
            "project_folder": str(tmp_path / "results"),
            "num_designs": 10,
        },
        "gallery": str(tmp_path / "gallery"),
    })
    result = generate(policy, workdir=ROOT, dry_run=False, transport=_explode)
    assert result["status"] == "refused"
    assert "nothing was spent" in result["reason"]
    assert result["provenance"]["submitted"] is False
    assert result["provenance"]["spend_gate"]["open"] is False
    # The request body is written for review, which is what an approval is pinned to.
    assert Path(result["design_request"]).exists()
    assert not (Path(result["project_folder"]) / "run.json").exists()


def test_generate_dry_run_touches_no_api_and_needs_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv(boltz_hosted.API_KEY_ENV, raising=False)
    monkeypatch.setattr(boltz_hosted, "_urllib_transport", _explode)
    policy = load_policy(None, payload={
        "boltz": {"project_folder": str(tmp_path / "results")},
        "gallery": str(tmp_path / "gallery"),
    })
    result = generate(policy, workdir=tmp_path, dry_run=True)
    assert result["status"] == "dry_run"
