"""Modal GPU worker: BindCraft / AlphaFold2 peptide–binder margin evaluation.

Deploy (authenticated host, evaluation tier only):

    modal deploy src/bindi/bindcraft_worker.py

Smoke test (single trajectory):

    modal run src/bindi/bindcraft_worker.py::smoke --traj 1
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

APP_NAME = "wetware-bindcraft-margin"
FUNCTION_NAME = "evaluate_peptide_binder_margin"

BINDCRAFT_REPO = "https://github.com/martinpacesa/BindCraft.git"
BINDCRAFT_COMMIT = "c0a48d595d4976694aa979438712ac94c16620bb"

WEIGHTS_VOLUME_NAME = "wetware-bindcraft-af2-params-v1"
WEIGHTS_MOUNT = "/weights"
PARAMS_SUBDIR = "params"
PARAMS_TAR_URL = (
    "https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar"
)

DEFAULT_GPU = os.environ.get("BINDCRAFT_MODAL_GPU", "A100-80GB")
DEFAULT_TRAJECTORY_COUNT = 1
DEFAULT_BINDER_SEED = (
    "EVQLVESGGGLVQPGGSLRLSAAASGFTFSDYAMSWVRQAPGKGLEWVSAI"
)
AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"

WORKER_IMAGE_VERSION = "wetware-bindcraft-margin-image-2026-03-26-v1"
WORKER_STACK_VERSION = json.dumps(
    {
        "bindcraft_commit": BINDCRAFT_COMMIT,
        "colabdesign": "git+https://github.com/sokrypton/ColabDesign.git",
        "alphafold_params": "2022-12-06",
        "image_version": WORKER_IMAGE_VERSION,
    },
    sort_keys=True,
)


@dataclass(frozen=True)
class MarginJob:
    mutant_peptide: str
    wild_type_peptide: str
    seed: int
    trajectory_count: int
    pair_id: str | None
    pair_role: str | None


def normalize_payload(payload: dict[str, Any]) -> MarginJob:
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be a dict, got {type(payload)!r}")
    mutant = "".join(str(payload.get("mutant_peptide", "")).split()).upper()
    wild = "".join(str(payload.get("wild_type_peptide", "")).split()).upper()
    if not mutant or not wild:
        raise ValueError("mutant_peptide and wild_type_peptide are required")
    if len(mutant) != len(wild):
        raise ValueError("mutant and wild-type peptides must have the same length")
    seed = int(payload.get("seed", 0))
    traj = int(payload.get("trajectory_count", DEFAULT_TRAJECTORY_COUNT))
    if traj < 1:
        raise ValueError("trajectory_count must be >= 1")
    return MarginJob(
        mutant_peptide=mutant,
        wild_type_peptide=wild,
        seed=seed,
        trajectory_count=traj,
        pair_id=payload.get("pair_id"),
        pair_role=payload.get("pair_role"),
    )


def _mutate_binder(binder: str, traj_seed: int) -> str:
    rng = random.Random(traj_seed)
    if len(binder) < 4:
        return binder
    n_mut = max(1, len(binder) // 12)
    positions = rng.sample(range(len(binder)), k=min(n_mut, len(binder)))
    chars = list(binder)
    for pos in positions:
        choices = [aa for aa in AMINO_ACIDS if aa != chars[pos]]
        chars[pos] = rng.choice(choices)
    return "".join(chars)


def _gpu_metadata() -> tuple[str, str]:
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            text=True,
            timeout=30,
        ).strip()
        line = out.splitlines()[0]
        name, driver = [part.strip() for part in line.split(",", 1)]
        return name, driver
    except Exception:
        return "unknown", "unknown"


def _image_digest_label() -> str:
    return f"sha256:{hashlib.sha256(WORKER_STACK_VERSION.encode()).hexdigest()}"


def run_margin_job(
    job: MarginJob,
    *,
    score_complex: Callable[[str, str, int], float],
    image_digest: str | None = None,
    gpu_model: str | None = None,
    driver_version: str | None = None,
    wall_start: float | None = None,
) -> dict[str, Any]:
    """Pure margin search over trajectories; ``score_complex(binder, peptide, seed)`` is AF2 signal."""
    started = wall_start if wall_start is not None else time.perf_counter()
    best_margin = float("-inf")
    best_row: dict[str, float | str] | None = None

    for traj_idx in range(job.trajectory_count):
        traj_seed = job.seed + traj_idx * 9973
        binder = _mutate_binder(DEFAULT_BINDER_SEED, traj_seed)
        mutant_score = float(score_complex(binder, job.mutant_peptide, traj_seed))
        wild_score = float(score_complex(binder, job.wild_type_peptide, traj_seed + 1))
        margin = mutant_score - wild_score
        if margin > best_margin:
            best_margin = margin
            best_row = {
                "top_margin": margin,
                "mutant_complex_score": mutant_score,
                "wild_type_complex_score": wild_score,
                "binder_sequence": binder,
            }

    if best_row is None:
        raise RuntimeError("no trajectories produced a score")

    gpu_model = gpu_model or "unknown"
    driver_version = driver_version or "unknown"
    elapsed = time.perf_counter() - started
    provenance = {
        "seed": job.seed,
        "trajectory_count": job.trajectory_count,
        "image_digest": image_digest or _image_digest_label(),
        "gpu_model": gpu_model,
        "driver_version": driver_version,
        "modal_app": APP_NAME,
        "modal_function": FUNCTION_NAME,
        "pair_id": job.pair_id,
        "pair_role": job.pair_role,
        "mutant_peptide": job.mutant_peptide,
        "wild_type_peptide": job.wild_type_peptide,
        "worker_stack_version": WORKER_STACK_VERSION,
        "wall_seconds": round(elapsed, 3),
    }
    return {
        **best_row,
        "provenance": provenance,
    }


def evaluate_peptide_binder_margin_impl(payload: dict[str, Any]) -> dict[str, Any]:
    job = normalize_payload(payload)
    params_dir = Path(WEIGHTS_MOUNT) / PARAMS_SUBDIR
    _ensure_alphafold_params(params_dir)
    _link_params_for_bindcraft(params_dir)

    def score_complex(binder: str, peptide: str, seed: int) -> float:
        return _colabdesign_complex_score(
            binder=binder,
            peptide=peptide,
            seed=seed,
            params_dir=params_dir,
        )

    gpu_model, driver_version = _gpu_metadata()
    return run_margin_job(
        job,
        score_complex=score_complex,
        image_digest=_image_digest_label(),
        gpu_model=gpu_model,
        driver_version=driver_version,
    )


def _ensure_alphafold_params(params_dir: Path) -> None:
    marker = params_dir / ".download_complete"
    if marker.is_file():
        return
    params_dir.parent.mkdir(parents=True, exist_ok=True)
    import tarfile
    import urllib.request

    tar_path = params_dir.parent / "alphafold_params_2022-12-06.tar"
    if not tar_path.is_file():
        urllib.request.urlretrieve(PARAMS_TAR_URL, tar_path)
    params_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:") as archive:
        archive.extractall(path=params_dir.parent)
    marker.write_text("ok\n", encoding="utf-8")
    try:
        import modal

        modal.Volume.from_name(WEIGHTS_VOLUME_NAME, create_if_missing=True).commit()
    except Exception:
        pass


def _link_params_for_bindcraft(params_dir: Path) -> None:
    bindcraft_params = Path("/root/bindcraft/params")
    bindcraft_params.parent.mkdir(parents=True, exist_ok=True)
    if not bindcraft_params.exists():
        bindcraft_params.symlink_to(params_dir, target_is_directory=True)


def _colabdesign_complex_score(
    binder: str,
    peptide: str,
    seed: int,
    params_dir: Path,
) -> float:
    import numpy as np
    from colabdesign import clear_mem, mk_afdesign_model

    np.random.seed(seed)
    clear_mem()
    model = mk_afdesign_model(protocol="binder", num_models=1, use_multimer=False)
    model.prep_inputs(seq=peptide, binder_len=len(binder))
    model.set_seq(binder)
    model.predict(models=[0], num_recycles=3, seed=seed)
    log = model.aux.get("log") or {}
    for key in ("i_ptm", "ptm", "plddt"):
        if key in log:
            return float(log[key])
    return 0.0


def _set_up_pyrosetta() -> None:
    import pyrosetta_installer

    pyrosetta_installer.install_pyrosetta()


try:
    import modal
except ImportError:  # pragma: no cover - local tests without modal installed
    modal = None  # type: ignore[assignment,misc]

if modal is not None:
    weights_volume = modal.Volume.from_name(WEIGHTS_VOLUME_NAME, create_if_missing=True)

    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("git", "wget", "aria2", "ffmpeg")
        .pip_install("numpy<2.0")
        .pip_install(
            "pdb-tools==2.4.8",
            "ffmpeg-python==0.2.0",
            "plotly==5.18.0",
            "kaleido==0.2.1",
        )
        .pip_install("git+https://github.com/sokrypton/ColabDesign.git")
        .pip_install("pyrosetta-installer")
        .run_commands(
            f"git clone {BINDCRAFT_REPO} /root/bindcraft",
            f"cd /root/bindcraft && git checkout {BINDCRAFT_COMMIT}",
            "chmod +x /root/bindcraft/functions/dssp",
            "chmod +x /root/bindcraft/functions/DAlphaBall.gcc",
        )
        .run_commands(
            "ln -s /usr/local/lib/python3.*/dist-packages/colabdesign colabdesign || true",
        )
        .run_function(_set_up_pyrosetta)
        .pip_install(
            "numpy<2.0",
            "jax[cuda]<0.7.0",
            "matplotlib==3.8.1",
        )
        .env(
            {
                "BINDCRAFT_MODAL_GPU": DEFAULT_GPU,
                "PYTHONPATH": "/root/bindcraft",
            }
        )
    )

    app = modal.App(APP_NAME, image=image)

    @app.function(
        name=FUNCTION_NAME,
        image=image,
        gpu=DEFAULT_GPU,
        timeout=60 * 60,
        volumes={WEIGHTS_MOUNT: weights_volume},
    )
    def evaluate_peptide_binder_margin(payload: dict[str, Any]) -> dict[str, Any]:
        """Remote entrypoint: opaque peptide margin job (dict in, dict out)."""
        return evaluate_peptide_binder_margin_impl(payload)

    @app.local_entrypoint()
    def smoke(traj: int = 1, seed: int = 42) -> None:
        """Minimal local entrypoint for a one-trajectory GPU smoke test."""
        payload = {
            "mutant_peptide": "YLGEFPGDW",
            "wild_type_peptide": "YLGKFPGDW",
            "seed": seed,
            "trajectory_count": traj,
            "pair_id": "PAIR-EASY",
            "pair_role": "benchmark_pair",
        }
        result = evaluate_peptide_binder_margin.remote(payload)
        print(json.dumps(result, indent=2, sort_keys=True))
