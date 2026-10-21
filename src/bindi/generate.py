"""Run Boltz design for a resolved campaign, on the official hosted Boltz API.

There is no local generator: `boltzgen` is never executed and self-hosting is forbidden, so
a real run is a submission to https://api.boltz.bio. That submission spends money, so it
sits behind two independent refusals — the design hold in `bindi.case` and the APPR human
spend gate in `bindi.spend_gate` — and this module returns a `refused` result rather than
submitting when either is in force.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import boltz_hosted
from . import spend_gate
from .boltz import (
    LEGACY_RANK_STAGE,
    LEGACY_RANKED_NAME,
    RANKED_METRICS,
    RANKED_STAGE,
    resolve_design_spec_path,
)
from .case import assert_design_allowed, case_path_from, resolve_target
from .policy import resolve_boltz_campaign


def generate(
    policy: dict[str, Any],
    *,
    workdir: Path | None = None,
    dry_run: bool = False,
    estimated_cost_usd: float | None = None,
    approval_file: str | Path | None = None,
    transport: boltz_hosted.Transport | None = None,
) -> dict[str, Any]:
    """Generate for a campaign policy.

    `dry_run` writes the fixture ranked table and touches no network. A real run builds the
    hosted-API request, then submits it only if the APPR gate is open for that exact request
    body; otherwise it returns `status: "refused"` with the gate's own reason, having
    submitted nothing and spent nothing.
    """
    base = workdir or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)
    boltz, gallery_path = resolve_boltz_campaign(policy, workdir=base)

    campaign_dir = Path(boltz["project_folder"])
    campaign_dir.mkdir(parents=True, exist_ok=True)
    campaign_file = campaign_dir / "campaign.json"
    campaign_file.write_text(json.dumps(boltz, indent=2) + "\n", encoding="utf-8")

    if dry_run:
        _write_dry_run_outputs(campaign_dir)
        return {
            "status": "dry_run",
            "campaign_file": str(campaign_file),
            "project_folder": str(campaign_dir),
            "gallery": str(gallery_path),
        }

    # The seam's hold: when the campaign names a patient case record, a real design run
    # only starts if that record is signed, consents to design use, and the Scientist's
    # conditions (human-reference gate pass, stated wild-type margin, established structural
    # margin threshold) are satisfied. Nothing here loosens for a fixture target.
    if case_path_from(policy) is not None:
        assert_design_allowed(resolve_target(policy))

    design_spec_path = resolve_design_spec_path(boltz, base)
    design_spec = boltz_hosted.load_design_spec(design_spec_path)
    request = boltz_hosted.build_design_request(boltz, design_spec)
    sha = boltz_hosted.request_sha256(request)
    request_file = campaign_dir / "design_request.json"
    request_file.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Refusal one: the spend gate. Checked before the credential is read and before any
    # network call, so a closed gate cannot reach the API.
    gate = spend_gate.gate_status(
        request_sha256=sha,
        estimated_cost_usd=estimated_cost_usd,
        approval_file=approval_file,
    )
    if not gate["open"]:
        return {
            "status": "refused",
            "reason": (
                "APPR spend gate is closed, so no job was submitted to the hosted Boltz API "
                "and nothing was spent: " + "; ".join(gate["problems"])
            ),
            "campaign_file": str(campaign_file),
            "project_folder": str(campaign_dir),
            "gallery": str(gallery_path),
            "design_spec": str(design_spec_path),
            "design_request": str(request_file),
            "provenance": boltz_hosted.design_provenance(
                request,
                design_spec_path=design_spec_path,
                submitted=False,
                gate=gate,
            ),
        }

    # Refusal two is upstream: without a key nothing can be submitted at all.
    if not boltz_hosted.has_api_key():
        raise boltz_hosted.BoltzCredentialMissing(boltz_hosted.CREDENTIAL_MISSING_REASON)

    submission = boltz_hosted.submit_design(
        request,
        estimated_cost_usd=estimated_cost_usd,
        approval_file=approval_file,
        transport=transport,
    )
    provenance = boltz_hosted.design_provenance(
        request,
        design_spec_path=design_spec_path,
        run=submission["run"],
        submitted=True,
        gate=gate,
    )
    (campaign_dir / "run.json").write_text(
        json.dumps(submission["run"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "status": "submitted",
        "campaign_file": str(campaign_file),
        "project_folder": str(campaign_dir),
        "gallery": str(gallery_path),
        "design_spec": str(design_spec_path),
        "design_request": str(request_file),
        "job_id": submission["run"].get("id"),
        "provenance": provenance,
    }


def _write_dry_run_outputs(campaign_dir: Path) -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "sample_ranked.csv"
    text = fixture.read_text(encoding="utf-8") if fixture.exists() else (
        "design,rank,design_to_target_iptm,Binder_Sequence\n"
        "demo_seq1,1,0.72,ACDEFGHIKLMNPQRSTVWY\n"
        "demo_seq2,2,0.68,ACDEFGHIKLMNPQRSTVWYAC\n"
    )
    # Hosted-API ranked layout
    ranked_dir = campaign_dir / RANKED_STAGE
    ranked_dir.mkdir(parents=True, exist_ok=True)
    (ranked_dir / RANKED_METRICS).write_text(text, encoding="utf-8")
    # Legacy layout (older campaigns / fixtures)
    legacy_dir = campaign_dir / LEGACY_RANK_STAGE
    legacy_dir.mkdir(parents=True, exist_ok=True)
    if fixture.exists():
        shutil.copy(fixture, legacy_dir / LEGACY_RANKED_NAME)
    else:
        (legacy_dir / LEGACY_RANKED_NAME).write_text(text, encoding="utf-8")
