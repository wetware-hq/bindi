"""Compute configuration is code: `infra.json` declares every fleet machine spec.

A green here means: `infra.json` parses, every declared function carries the resource keys
Modal fixes at deploy time, and nothing declares a GPU (Boltz stays on the hosted design
API; BindCraft, which needed one, is gone from the design path). It does not mean anything
was deployed. `modal_app.py` is checked for the import-time read and the content pin without
importing it, since `modal` is not a dependency of this package.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra.json"
APP = ROOT / "modal_app.py"

RESOURCE_KEYS = {"cpu", "memory", "gpu", "timeout"}


def _infra():
    return json.loads(INFRA.read_text(encoding="utf-8"))


def test_infra_declares_every_function_with_a_spec():
    infra = _infra()
    assert infra["schema"] == "bindi.infra.v1"
    assert infra["generator"] == "boltzgen", "Boltz is the generator"
    functions = infra["functions"]
    assert functions, "infra.json declares no functions"
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
    assert infra["image"]["local_files"]["infra.json"].endswith("infra.json")


def test_modal_app_reads_infra_at_import_time_and_pins_its_hash():
    source = APP.read_text(encoding="utf-8")
    assert "INFRA: dict = json.loads(INFRA_PATH.read_text" in source, (
        "infra.json must be read at module import, since Modal fixes resources at deploy time"
    )
    assert "add_local_file" in source, "infra.json must be added into the image"
    assert "INFRA_SHA256" in source and "hashlib.sha256" in source
    assert "**resources(" in source, "decorators must be parameterised from infra.json"
    for name in _infra()["functions"]:
        assert f'resources("{name}")' in source, f"{name} is declared but never deployed"
