"""bindi's Modal app: compute configuration as code, read from `modal_infra.json` at import time.

Modal fixes a function's resources when the app module is imported and deployed, not when
the function is called: `cpu`, `memory`, `gpu` and `timeout` are arguments to the
`@app.function(...)` decorator. So `modal_infra.json` is not cloud state to mutate — it is read
here, at definition time, and a config change takes effect by redeploying a commit. Every
machine spec is therefore traceable to a SHA rather than to a dashboard.

The same file is baked into the image with `Image.add_local_file`, so a container can read
back the exact config it was built from; every function records the file's sha256 in its
own output, which is what ties a run to a spec.

Nothing here deploys, and no GPU work is declared: Boltz, the generator, stays on the
hosted design API (no self-hosting), and BindCraft has been removed from the design path.
No function here can clear the margin stage; bindi's gates and holds run unchanged
remotely and their refusals are returned verbatim.

    modal deploy modal_app.py      # deliberately NOT run by this module
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import modal

REPO_ROOT = pathlib.Path(__file__).resolve().parent
INFRA_PATH = REPO_ROOT / "modal_infra.json"

#: Read at import time. The decorators below are parameterised from this document.
INFRA: dict = json.loads(INFRA_PATH.read_text(encoding="utf-8"))

#: Recorded in every function's output so a result names the config it ran under.
INFRA_SHA256: str = hashlib.sha256(INFRA_PATH.read_bytes()).hexdigest()

REMOTE_INFRA_PATH = INFRA["image"]["local_files"]["modal_infra.json"]
REMOTE_SRC = INFRA["image"]["local_dirs"]["src"]

#: Keys of a function spec that Modal accepts as decorator arguments.
RESOURCE_KEYS = ("cpu", "memory", "gpu", "timeout")


def _image() -> modal.Image:
    spec = INFRA["image"]
    image = modal.Image.debian_slim(python_version=spec["python_version"])
    if spec.get("apt_install"):
        image = image.apt_install(*spec["apt_install"])
    if spec.get("pip_install"):
        image = image.pip_install(*spec["pip_install"])
    for local, remote in spec.get("local_dirs", {}).items():
        image = image.add_local_dir(str(REPO_ROOT / local), remote_path=remote)
    for local, remote in spec.get("local_files", {}).items():
        # The config travels with the image, so a container can read back its own spec.
        image = image.add_local_file(str(REPO_ROOT / local), remote_path=remote)
    return image


image = _image()
app = modal.App(INFRA["app_name"], image=image)


def resources(name: str) -> dict:
    """Decorator kwargs for one fleet function, straight from `modal_infra.json`."""
    spec = INFRA["functions"][name]
    return {key: spec[key] for key in RESOURCE_KEYS if key in spec}


def _config_provenance() -> dict:
    """What the container was built with, read back from the image itself."""
    remote = pathlib.Path(REMOTE_INFRA_PATH)
    record = {
        "infra_file": REMOTE_INFRA_PATH,
        "infra_sha256_at_deploy": INFRA_SHA256,
        "app": INFRA["app_name"],
        "generator": INFRA.get("generator"),
    }
    if remote.exists():
        payload = remote.read_bytes()
        record["infra_sha256_in_image"] = hashlib.sha256(payload).hexdigest()
        record["infra_sha256_matches"] = record["infra_sha256_in_image"] == INFRA_SHA256
        record["functions"] = sorted(json.loads(payload).get("functions", {}))
    else:  # pragma: no cover - only if the image was built without the file
        record["infra_sha256_in_image"] = None
        record["infra_sha256_matches"] = False
    return record


def _bindi_path() -> None:
    import sys

    if REMOTE_SRC not in sys.path:
        sys.path.insert(0, REMOTE_SRC)


def _fixture_env() -> None:
    """Point the two output gates at the pinned synthetic fixtures in the image."""
    import os

    fixtures = INFRA["image"]["local_dirs"]["fixtures"]
    os.environ.setdefault(
        "BINDI_HUMAN_REFERENCE_FASTA", f"{fixtures}/human_reference_synthetic.fasta"
    )
    os.environ.setdefault(
        "BINDI_HUMAN_REFERENCE_SOURCE",
        "fixtures/human_reference_synthetic.fasta (synthetic; NOT the UniProt human proteome)",
    )
    os.environ.setdefault(
        "BINDI_HAZARD_SCREEN_FASTA", f"{fixtures}/hazard_screen_synthetic.fasta"
    )
    os.environ.setdefault(
        "BINDI_HAZARD_SCREEN_SOURCE",
        "fixtures/hazard_screen_synthetic.fasta (synthetic motif; NOT a hazard inventory)",
    )


@app.function(name="run_gates", **resources("run_gates"))
def run_gates(target: dict, designs: list | None = None) -> dict:
    """Both output gates, with real mmseqs2 and BLAST+. Records are returned verbatim."""
    import shutil

    _bindi_path()
    _fixture_env()
    from bindi.gates import hazard_screen_gate, human_reference_gate

    return {
        "config": _config_provenance(),
        "tools": {t: shutil.which(t) for t in ("mmseqs", "blastp", "makeblastdb")},
        "human_reference": human_reference_gate(designs or [], target=target),
        "hazard_screen": hazard_screen_gate(designs or [], target=target),
    }


@app.function(name="curate_campaign", **resources("curate_campaign"))
def curate_campaign(policy: dict, project_folder: str | None = None) -> dict:
    """Build a gallery from an already-ranked campaign directory."""
    from pathlib import Path

    _bindi_path()
    from bindi.curate import curate

    result = curate(policy, project_folder=Path(project_folder) if project_folder else None)
    return {"config": _config_provenance(), "curate": result}


@app.function(name="case_run", **resources("case_run"))
def case_run(case: dict, policy: dict | None = None) -> dict:
    """One immunology case end to end. The hold and the margin refusal are enforced inside."""
    import json as _json
    import tempfile
    from pathlib import Path

    _bindi_path()
    _fixture_env()
    from bindi.e2e import case_run as _case_run

    workdir = Path(tempfile.mkdtemp())
    case_file = workdir / "case.json"
    case_file.write_text(_json.dumps(case), encoding="utf-8")
    result = _case_run(case_file, policy=policy, workdir=workdir)
    trace = [
        _json.loads(line)
        for line in Path(result["trace"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {"config": _config_provenance(), "result": result, "trace": trace}


@app.function(name="ipsae_score", **resources("ipsae_score"))
def ipsae_score(pae: list, cif_text: str) -> dict:
    """CPU ipSAE scoring from a Boltz PAE matrix and a predicted structure. No GPU."""
    _bindi_path()
    from bindi.ipsae import parse_predicted_cif, score_predicted_complex

    parsed = parse_predicted_cif(cif_text)
    return {"config": _config_provenance(), "ipsae": score_predicted_complex(pae, parsed)}


@app.function(name="sequence_design_repeat_noise", **resources("sequence_design_repeat_noise"))
def sequence_design_repeat_noise(
    case: dict, repeats: int = 20, seed_start: int = 1000, scorer_id: str | None = None
) -> dict:
    """Repeat-noise spread for the sequence-level designer. Never clears the margin stage."""
    _bindi_path()
    from bindi import design_engine

    report = design_engine.run_repeat_noise_report(
        case, repeats=repeats, seed_start=seed_start, scorer_id=scorer_id
    )
    return {"config": _config_provenance(), "report": report}


@app.local_entrypoint()
def main() -> None:
    """Print the configuration this app would deploy. Runs nothing remotely."""
    print(f"app: {INFRA['app_name']}")
    print(f"modal_infra.json sha256: {INFRA_SHA256}")
    print(f"generator: {INFRA.get('generator')}")
    for name in sorted(INFRA["functions"]):
        print(f"  {name}: {resources(name)}")
