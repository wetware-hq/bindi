"""Offline tests for BindCraft Modal worker helpers (no GPU / no deploy)."""

import pytest

from bindi.bindcraft_worker import (
    APP_NAME,
    FUNCTION_NAME,
    MarginJob,
    normalize_payload,
    run_margin_job,
)


def test_normalize_payload_uppercases_and_defaults_traj():
    job = normalize_payload(
        {
            "mutant_peptide": "ylgefp gdw",
            "wild_type_peptide": "ylgkfp gdw",
            "seed": 7,
        }
    )
    assert job.mutant_peptide == "YLGEFPGDW"
    assert job.wild_type_peptide == "YLGKFPGDW"
    assert job.seed == 7
    assert job.trajectory_count == 1


def test_normalize_payload_rejects_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        normalize_payload(
            {
                "mutant_peptide": "AAA",
                "wild_type_peptide": "AAAA",
                "seed": 1,
                "trajectory_count": 1,
            }
        )


def test_run_margin_job_picks_best_trajectory():
    job = MarginJob(
        mutant_peptide="AAA",
        wild_type_peptide="AAB",
        seed=100,
        trajectory_count=3,
        pair_id="PAIR-EASY",
        pair_role="benchmark_pair",
    )

    def score_complex(binder: str, peptide: str, seed: int) -> float:
        # Higher score when binder length matches peptide length and peptide is mutant.
        base = float(len(binder))
        return base + (1.0 if peptide == "AAA" else 0.0)

    result = run_margin_job(job, score_complex=score_complex, gpu_model="test-gpu")
    assert result["top_margin"] >= 1.0
    assert result["mutant_complex_score"] > result["wild_type_complex_score"]
    assert result["binder_sequence"]
    prov = result["provenance"]
    assert prov["seed"] == 100
    assert prov["trajectory_count"] == 3
    assert prov["gpu_model"] == "test-gpu"
    assert prov["modal_app"] == APP_NAME
    assert prov["modal_function"] == FUNCTION_NAME


def test_live_client_uses_modal_from_name_and_remote(monkeypatch):
    from bindi import structural_margin

    captured: dict[str, object] = {}

    class _Remote:
        def remote(self, payload: dict) -> dict:
            captured["payload"] = payload
            return {
                "top_margin": 2.5,
                "mutant_complex_score": 11.0,
                "wild_type_complex_score": 8.5,
                "binder_sequence": "ACDEFG",
                "provenance": {
                    "seed": payload["seed"],
                    "trajectory_count": payload["trajectory_count"],
                    "image_digest": "sha256:test",
                    "gpu_model": "A100-80GB",
                    "driver_version": "550.0",
                },
            }

    class _Function:
        @staticmethod
        def from_name(app: str, fn: str) -> _Remote:
            captured["app"] = app
            captured["fn"] = fn
            return _Remote()

    fake_modal = type("modal", (), {"Function": _Function})()
    monkeypatch.setitem(__import__("sys").modules, "modal", fake_modal)
    monkeypatch.setenv(structural_margin.MODAL_TOKEN_ENV, "present-not-used-for-auth")
    client = structural_margin.LiveBindCraftModalClient("present-not-used-for-auth")
    row = client.evaluate_top_margin(
        "YLGEFPGDW",
        "YLGKFPGDW",
        seed=42,
        trajectory_count=1,
        pair_id="PAIR-EASY",
    )
    assert captured["app"] == structural_margin.DEFAULT_MODAL_APP
    assert captured["fn"] == structural_margin.DEFAULT_MODAL_FUNCTION
    assert captured["payload"]["trajectory_count"] == 1
    assert row["top_margin"] == 2.5
    assert row["provenance"].gpu_model == "A100-80GB"
