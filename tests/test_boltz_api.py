"""The hosted Boltz design client.

Every test here mocks the HTTP layer: the suite must never touch the network and
must never be able to spend money. The one thing checked against reality is the
REQUEST SHAPE, pinned to fixtures/boltz_design_request_legacy.json -- the exact body
the live API accepted on 2026-10-21.
"""
from __future__ import annotations

import io
import json
import pathlib
import urllib.error

import pytest

from bindi import boltz as api

FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "boltz_design_request_legacy.json"


# ---- HTTP double -----------------------------------------------------------------

class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Recorder:
    """Stands in for urllib.request.urlopen and records every call."""

    def __init__(self, payload=None, *, status=None, error_body=""):
        self.payload = payload if payload is not None else {}
        self.status = status
        self.error_body = error_body
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        if self.status is not None:
            raise urllib.error.HTTPError(
                request.full_url, self.status, "error", {},
                io.BytesIO(self.error_body.encode("utf-8")),
            )
        payload = self.payload
        if callable(payload):
            payload = payload(request)
        return _Response(json.dumps(payload).encode("utf-8"))


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("BOLTZ_API_KEY", "test-key-not-a-real-secret")


@pytest.fixture
def http(monkeypatch):
    def install(recorder):
        monkeypatch.setattr(api.urllib.request, "urlopen", recorder)
        return recorder
    return install


def _policy_block():
    return {
        "num_proteins": 10,
        "target_entities": [
            {"chain_ids": ["A"], "value": "GSHSMRYFF"},
            {"chain_ids": ["C"], "value": "SLLMWITQC"},
        ],
        "epitope_residues": {"C": [0, 1, 2]},
        "binder": {"modality": "custom_protein", "chain_ids": ["D"], "length": "60..100"},
    }


# ---- request shape: the load-bearing contract ------------------------------------

def test_builds_the_legacy_shape_and_not_the_type_discriminated_one():
    body = api.build_design_request(_policy_block())
    assert sorted(body) == ["binder_specification", "num_proteins", "target"]
    # The migrated shape's markers must be absent.
    assert "type" not in body
    assert "binder" not in body
    assert body["target"]["type"] == "no_template"
    assert body["binder_specification"]["modality"] == "custom_protein"


def test_type_discriminated_body_is_refused_locally():
    migrated = {
        "type": "binder",
        "num_proteins": 10,
        "target": {"entities": [{"chain_ids": ["C"], "value": "SLLMWITQC"}]},
        "binder": {"modality": "custom_protein", "entities": []},
    }
    with pytest.raises(api.RequestShapeError) as exc:
        api.validate_legacy_request(migrated)
    assert "binder_specification" in str(exc.value)


def test_non_binding_residues_is_refused_at_any_depth():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body["target"]["entities"][0]["non_binding_residues"] = [1, 2, 3]
    with pytest.raises(api.RequestShapeError, match="non_binding_residues"):
        api.validate_legacy_request(body)


def test_the_body_the_live_api_accepted_still_validates():
    api.validate_legacy_request(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_policy_reproduces_the_accepted_body_byte_for_byte():
    accepted = json.loads(FIXTURE.read_text(encoding="utf-8"))
    target = accepted["target"]
    spec = accepted["binder_specification"]
    rebuilt = api.build_design_request({
        "num_proteins": accepted["num_proteins"],
        "target_entities": [
            {"chain_ids": e["chain_ids"], "value": e["value"]} for e in target["entities"]
        ],
        "epitope_residues": target["epitope_residues"],
        "binder": {
            "modality": spec["modality"],
            "entities": [
                {"type": e["type"], "chain_ids": e["chain_ids"], "value": e["value"]}
                for e in spec["entities"]
            ],
        },
    })
    assert api.canonical_json(rebuilt) == api.canonical_json(accepted)


def test_a_bare_target_name_fails_loudly_rather_than_guessing():
    with pytest.raises(api.RequestShapeError, match="target_entities"):
        api.build_design_request({"num_proteins": 10, "target": "hPDL1",
                                  "binder": {"chain_ids": ["D"], "length": "60..100"}})


def test_canonical_json_is_key_order_independent():
    a = api.canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
    b = api.canonical_json({"a": {"c": 3, "d": 2}, "b": 1})
    assert a == b
    assert api.request_digest({"b": 1}) == api.request_digest({"b": 1})


# ---- spend control ---------------------------------------------------------------

def test_submit_without_approval_raises_and_sends_nothing(key, http):
    recorder = http(_Recorder({"id": "prot_des_MUSTNOTHAPPEN"}))
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for bad in (None, "", "   "):
        with pytest.raises(api.SpendNotApproved):
            api.submit(body, approval=bad)
    assert recorder.calls == []


def test_submit_requires_approval_as_a_keyword_only_argument():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(TypeError):
        api.submit(body, "someone said yes")  # positional approval must not work


def test_estimate_cost_hits_only_the_free_endpoint(key, http):
    recorder = http(_Recorder({"estimated_cost_usd": "0.5000"}))
    out = api.estimate_cost(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert out["estimated_cost_usd"] == "0.5000"
    assert len(recorder.calls) == 1
    request = recorder.calls[0]
    assert request.full_url.endswith("/protein/design/estimate-cost")
    assert request.method == "POST"


def test_status_and_results_are_get_only(key, http):
    recorder = http(_Recorder({"status": "running"}))
    api.get_status("prot_des_x")
    assert recorder.calls[0].method == "GET"
    assert recorder.calls[0].full_url.endswith("/protein/design/prot_des_x")

    recorder = http(_Recorder({"data": [], "has_more": False}))
    api.list_results("prot_des_x", limit=100)
    assert recorder.calls[0].method == "GET"
    assert "/protein/design/prot_des_x/results?" in recorder.calls[0].full_url
    assert "limit=100" in recorder.calls[0].full_url


def test_iter_results_follows_the_cursor(key, http):
    pages = [
        {"data": [{"id": "r1"}], "has_more": True, "last_id": "r1"},
        {"data": [{"id": "r2"}], "has_more": False, "last_id": "r2"},
    ]
    recorder = http(_Recorder(lambda request: pages.pop(0)))
    assert [r["id"] for r in api.iter_results("prot_des_x")] == ["r1", "r2"]
    assert "after_id=r1" in recorder.calls[1].full_url


# ---- failure behaviour -----------------------------------------------------------

def test_validation_error_is_raised_with_the_apis_own_text_and_not_retried(key, http):
    body = (
        '{"code":"VALIDATION_ERROR","message":"Request validation failed. '
        'binder_specification is required. 1 other field needs attention.",'
        '"details":{"templates":{"inputName":"templates","message":"is required"}}}'
    )
    recorder = http(_Recorder(status=400, error_body=body))
    with pytest.raises(api.BoltzAPIError) as exc:
        api.estimate_cost(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert exc.value.status == 400
    assert exc.value.code == "VALIDATION_ERROR"
    assert "binder_specification is required" in str(exc.value)
    assert "templates" in str(exc.value)
    assert len(recorder.calls) == 1  # a 4xx is a fact, not a flake: no retry


def test_server_error_is_also_not_retried(key, http):
    recorder = http(_Recorder(status=503, error_body="upstream unavailable"))
    with pytest.raises(api.BoltzAPIError):
        api.whoami()
    assert len(recorder.calls) == 1


def test_there_is_no_local_fallback_path():
    source = pathlib.Path(api.__file__).read_text(encoding="utf-8")
    for banned in ("subprocess", "boltzgen", "BOLTZGEN_CMD", "BOLTZ_CACHE"):
        assert banned not in source, f"{banned} is back in the design client"


# ---- the key ---------------------------------------------------------------------

def test_missing_key_raises_before_any_request(monkeypatch, http):
    monkeypatch.delenv("BOLTZ_API_KEY", raising=False)
    recorder = http(_Recorder({}))
    with pytest.raises(RuntimeError, match="BOLTZ_API_KEY"):
        api.whoami()
    assert recorder.calls == []


def test_key_is_sent_as_a_header_and_never_in_an_error_message(monkeypatch, http):
    monkeypatch.setenv("BOLTZ_API_KEY", "sk_secret_value_abc123")
    recorder = http(_Recorder(status=401, error_body='{"code":"UNAUTHORIZED","message":"bad key"}'))
    with pytest.raises(api.BoltzAPIError) as exc:
        api.whoami()
    assert "sk_secret_value_abc123" not in str(exc.value)
    assert "sk_secret_value_abc123" not in exc.value.body
    assert recorder.calls[0].get_header("X-api-key") == "sk_secret_value_abc123"
