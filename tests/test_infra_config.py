"""Compute configuration is code: `modal_infra.json` declares every fleet machine spec.

A green here means: `modal_infra.json` parses, every declared function carries the resource keys
Modal fixes at deploy time, and nothing declares a GPU (Boltz stays on the hosted design
API; BindCraft, which needed one, is gone from the design path). It does not mean anything
was deployed. `modal_app.py` is checked for the import-time read and the content pin without
importing it, since `modal` is not a dependency of this package.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "modal_infra.json"
APP = ROOT / "modal_app.py"

RESOURCE_KEYS = {"cpu", "memory", "gpu", "timeout"}


def _infra():
    return json.loads(INFRA.read_text(encoding="utf-8"))


def test_infra_declares_every_function_with_a_spec():
    infra = _infra()
    assert infra["schema"] == "bindi.infra.v1"
    assert infra["generator"] == "boltzgen", "Boltz is the generator"
    functions = infra["functions"]
    assert functions, "modal_infra.json declares no functions"
    for name, spec in functions.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name
        assert isinstance(spec["cpu"], (int, float)) and spec["cpu"] > 0
        assert isinstance(spec["memory"], int) and spec["memory"] > 0
        assert isinstance(spec["timeout"], int) and spec["timeout"] > 0
        assert str(spec.get("purpose") or "").strip(), f"{name} has no stated purpose"
        assert not (set(spec) - RESOURCE_KEYS - {"purpose"}), f"{name} has unknown keys"


def test_no_function_declares_a_gpu_and_the_file_says_why():
    infra = _infra()
    assert all("gpu" not in spec for spec in infra["functions"].values())
    statement = infra["gpu_statement"]
    assert "hosted design API" in statement
    assert "BindCraft" in statement and "removed" in statement


def test_image_carries_infra_json_so_a_container_can_read_its_own_spec():
    infra = _infra()
    assert infra["image"]["local_files"]["modal_infra.json"].endswith("modal_infra.json")


def test_modal_app_reads_infra_at_import_time_and_pins_its_hash():
    source = APP.read_text(encoding="utf-8")
    assert "INFRA: dict = json.loads(INFRA_PATH.read_text" in source, (
        "modal_infra.json must be read at module import, since Modal fixes resources at deploy time"
    )
    assert "add_local_file" in source, "modal_infra.json must be added into the image"
    assert "INFRA_SHA256" in source and "hashlib.sha256" in source
    assert "**resources(" in source, "decorators must be parameterised from modal_infra.json"
    for name in _infra()["functions"]:
        assert f'resources("{name}")' in source, f"{name} is declared but never deployed"


def test_app_can_find_the_config_at_the_path_the_image_bakes_it_to():
    """A container imports modal_app.py from /root, not from the repo root.

    The image puts the config under its own remote path, so reading it relative to
    ``__file__`` alone fails at import and the function dies before it runs. This pins the
    fallback and pins that it agrees with the remote path the image actually uses.
    """
    source = APP.read_text(encoding="utf-8")
    remote = _infra()["image"]["local_files"]["modal_infra.json"]
    container_root = str(Path(remote).parent)
    assert f'CONTAINER_ROOT = pathlib.Path("{container_root}")' in source, (
        f"modal_app.py must fall back to {remote}, where the image puts the config"
    )
    assert "def _infra_path()" in source
    assert "candidate.is_file()" in source


def test_the_image_installs_what_bindi_imports():
    """bindi reads hosted design specs with pyyaml; a missing dep is an import-time death."""
    pins = [p.split(">=")[0].split("==")[0].lower() for p in _infra()["image"]["pip_install"]]
    for required in ("biopython", "gemmi", "numpy", "pyyaml"):
        assert required in pins, f"{required} is imported by bindi but not in the image"
