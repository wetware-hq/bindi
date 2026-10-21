"""The hosted Boltz API: the only generator path bindi has.

Boltz self-hosting is forbidden by standing instruction and there is no local `boltzgen`
binary any more, so generation happens on the official hosted API at
``https://api.boltz.bio``. Documented at https://api.boltz.bio/docs/guides/authentication/
and https://api.boltz.bio/docs/guides/protein-design/ (both read 2026-10-21).

Authentication, per the official docs: the credential is read from the environment variable
``BOLTZ_API_KEY`` and sent as the ``x-api-key`` request header (an OAuth
``Authorization: Bearer`` token is the interactive alternative and is not used here).
The key value is never logged, echoed, written to a trace, or returned by any function in
this module. Without a key every entry point fails closed.

The official Python client is ``pip install boltz-api``. bindi does not take that
dependency: the two calls it makes are a read-only auth check and one JSON POST, so it
speaks the documented REST interface over the standard library instead. A `transport`
callable can be injected, which is how the tests exercise submission without a network.

**Submission spends real money.** `submit_design` cannot run unless the APPR human spend
gate in `bindi.spend_gate` is open for the exact request body being submitted, and the
design hold in `bindi.case` has already cleared upstream in `generate`. Reading docs,
building a request body, hashing it and asking the gate for its status are all free and
make no paid call.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import boltz as boltz_mod
from . import spend_gate

#: Official base URL; `BOLTZ_BASE_URL` overrides it (the docs set that variable too).
DEFAULT_BASE_URL = "https://api.boltz.bio"
BASE_URL_ENV = "BOLTZ_BASE_URL"

#: The credential. Read from the environment, sent as a header, never printed.
API_KEY_ENV = "BOLTZ_API_KEY"
AUTH_HEADER = "x-api-key"

#: Read-only: identity of the caller. The one network call bindi may make for free.
AUTH_ME_PATH = "/compute/v1/auth/me"

#: Paid: creates a protein design run. Behind the APPR gate.
DESIGN_PATH = "/compute/v1/protein/design"

#: Prices a request without creating a run (per the API docs). Still never called
#: automatically: a caller asks for it explicitly and passes the price to the gate.
ESTIMATE_COST_PATH = "/compute/v1/protein/design/estimate-cost"

#: The run object's own pipeline id for protein design (distinct from `boltzmol`).
PIPELINE = "boltzprot"

OFFICIAL_CLIENT = "boltz-api"
DOCS_URL = "https://api.boltz.bio/docs/guides/protein-design/"

#: Request shapes the design endpoint accepts.
#:
#: "legacy" is `{num_proteins, target, binder_specification}`. The docs keep it supported
#: for migration, and it is the shape this project has actually had validated against the
#: live API for a three-chain pMHC target with a de-novo binder
#: (/workspace/wetware/binder-design/v2/boltz-api-pmhc-schema-2026-10-20.md, which also
#: records that the type-discriminated branch rejected a scaffold-free binder entity).
#: It is therefore the default here. "binder" is the type-discriminated shape the docs
#: recommend for new integrations; selectable, and to be made the default once a
#: scaffold-free binder body has been accepted.
REQUEST_SHAPES = ("legacy", "binder")
DEFAULT_REQUEST_SHAPE = "legacy"

CREDENTIAL_MISSING_REASON = (
    f"{API_KEY_ENV} is not set, so the hosted Boltz API cannot be reached. Boltz is the "
    "generator and runs only on the official hosted API: there is no local binary and "
    "self-hosting is not permitted. Supply a workspace API key in the environment; bindi "
    "never reads a key from the repository and never prints one."
)


class BoltzCredentialMissing(RuntimeError):
    """No `BOLTZ_API_KEY` in the environment. Every entry point fails closed."""


class BoltzApiError(RuntimeError):
    """The hosted API returned an error, or could not be reached."""


Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, Any]]]


def base_url() -> str:
    import os

    return (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def require_api_key() -> str:
    """The API key, or a clear refusal. The value is returned for a header and nothing else."""
    import os

    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise BoltzCredentialMissing(CREDENTIAL_MISSING_REASON)
    return key


def has_api_key() -> bool:
    """True when a key is configured. Says nothing about the value."""
    import os

    return bool((os.environ.get(API_KEY_ENV) or "").strip())


def _headers() -> dict[str, str]:
    return {
        AUTH_HEADER: require_api_key(),
        "content-type": "application/json",
        "accept": "application/json",
    }


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float = 30.0
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8") or "{}"
            return response.status, json.loads(payload)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network-shaped failure
        detail = exc.read().decode("utf-8", "replace")
        raise BoltzApiError(f"{method} {url} failed with HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network-shaped failure
        raise BoltzApiError(f"{method} {url} could not be reached: {exc.reason}") from exc


def _call(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    send = transport or _urllib_transport
    status, document = send(method, url, _headers(), body)
    if status >= 400:
        raise BoltzApiError(f"{method} {url} failed with HTTP {status}: {document}")
    return document


def auth_check(*, transport: Transport | None = None) -> dict[str, Any]:
    """`GET /compute/v1/auth/me`. Read-only: it creates no run and spends nothing."""
    document = _call("GET", AUTH_ME_PATH, transport=transport)
    return {
        "endpoint": f"{base_url()}{AUTH_ME_PATH}",
        "authenticated": True,
        "auth_header": AUTH_HEADER,
        "response": document,
    }


# ---- request building (free: no network, no spend) --------------------------------


def load_design_spec(path: str | Path) -> dict[str, Any]:
    """Read a design specification. YAML (`.yaml`/`.yml`) or JSON, as the API itself accepts.

    YAML needs PyYAML. When it is missing this refuses with an actionable message rather
    than guessing at the document's meaning.
    """
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise BoltzApiError(
                f"reading the YAML design spec {source} needs PyYAML (pip install pyyaml), or "
                "supply the same specification as JSON. Nothing was guessed."
            ) from exc
        document = yaml.safe_load(text)
    else:
        document = json.loads(text)
    if not isinstance(document, dict):
        raise BoltzApiError(f"design spec {source} must be a mapping, found {type(document).__name__}")
    return document


def build_design_request(
    boltz: dict[str, Any],
    design_spec: dict[str, Any],
    *,
    shape: str = DEFAULT_REQUEST_SHAPE,
) -> dict[str, Any]:
    """Build the `POST /compute/v1/protein/design` body. Pure: no network, no spend.

    The design spec supplies the science (`target`, `binder_specification`/`binder`); the
    policy `boltz` block supplies the job parameters through `boltz.job_parameters`.
    """
    if shape not in REQUEST_SHAPES:
        raise ValueError(f"request shape must be one of {REQUEST_SHAPES}, found {shape!r}")

    params = boltz_mod.job_parameters(boltz)
    target = design_spec.get("target")
    if not isinstance(target, dict) or not target.get("entities"):
        raise BoltzApiError(
            "design spec must define target.entities (the fixed context chains); see "
            f"{DOCS_URL}"
        )
    binder = design_spec.get("binder_specification") or design_spec.get("binder")
    if not isinstance(binder, dict) or not binder.get("entities"):
        raise BoltzApiError(
            "design spec must define binder_specification.entities (the designed chain); see "
            f"{DOCS_URL}"
        )
    binder = dict(binder)
    binder.setdefault("modality", params["modality"])

    num_proteins = int(design_spec.get("num_proteins") or params["num_proteins"])
    if num_proteins < boltz_mod.MIN_NUM_PROTEINS:
        num_proteins = boltz_mod.MIN_NUM_PROTEINS

    if shape == "legacy":
        request: dict[str, Any] = {
            "num_proteins": num_proteins,
            "target": target,
            "binder_specification": binder,
        }
    else:
        request = {
            "type": "binder",
            "num_proteins": num_proteins,
            "target": target,
            "binder": binder,
        }
        if design_spec.get("templates"):
            request["templates"] = design_spec["templates"]
    # `global_design_filters` is omitted deliberately unless the spec sets it: the API
    # default excludes cysteine from designed regions, which is what we want.
    for optional in ("global_design_filters", "rules"):
        if design_spec.get(optional) is not None:
            request[optional] = design_spec[optional]
    return request


def request_sha256(request: dict[str, Any]) -> str:
    """Content hash of a request body. This is what an APPR approval is pinned to."""
    return hashlib.sha256(canonical_request_bytes(request)).hexdigest()


def canonical_request_bytes(request: dict[str, Any]) -> bytes:
    return json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")


def design_provenance(
    request: dict[str, Any],
    *,
    design_spec_path: str | Path | None = None,
    run: dict[str, Any] | None = None,
    submitted: bool,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run provenance for the trace, in the shape the rest of bindi records provenance."""
    record: dict[str, Any] = {
        "generator": boltz_mod.GENERATOR_ID,
        "generator_name": boltz_mod.GENERATOR_NAME,
        "generator_engine": boltz_mod.GENERATOR_ENGINE,
        "tool": boltz_mod.GENERATOR_TOOL,
        "self_hosted": False,
        "endpoint": f"{base_url()}{DESIGN_PATH}",
        "auth_header": AUTH_HEADER,
        "credential_env": API_KEY_ENV,
        "official_client": OFFICIAL_CLIENT,
        "pipeline": PIPELINE,
        "request_shape": "binder" if request.get("type") == "binder" else "legacy",
        "request_sha256": request_sha256(request),
        "num_proteins": request.get("num_proteins"),
        "modality": (request.get("binder_specification") or request.get("binder") or {}).get(
            "modality"
        ),
        "submitted": bool(submitted),
        "job_id": (run or {}).get("id"),
        "status": (run or {}).get("status"),
        "pipeline_version": (run or {}).get("pipeline_version"),
        "livemode": (run or {}).get("livemode"),
        "workspace_id": (run or {}).get("workspace_id"),
    }
    if design_spec_path is not None:
        path = Path(design_spec_path)
        record["design_spec"] = str(path)
        if path.exists():
            record["design_spec_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if gate is not None:
        record["spend_gate"] = {
            "gate": gate.get("gate"),
            "open": gate.get("open"),
            "approved_by": gate.get("approved_by"),
            "cap_usd": gate.get("cap_usd"),
            "estimated_cost_usd": gate.get("estimated_cost_usd"),
        }
    return record


# ---- paid calls, behind the gate --------------------------------------------------


def estimate_cost(
    request: dict[str, Any], *, transport: Transport | None = None
) -> dict[str, Any]:
    """Price a request. Documented as creating no run; bindi never calls it on its own."""
    return _call("POST", ESTIMATE_COST_PATH, payload=request, transport=transport)


def submit_design(
    request: dict[str, Any],
    *,
    estimated_cost_usd: float | None = None,
    approval_file: str | Path | None = None,
    approval: dict[str, Any] | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Submit a design run. **Spends money.** Refused unless the APPR gate is open.

    The gate is checked before the credential is even read, so a closed gate cannot reach
    the network. `spend_gate.SpendGateClosed` propagates: on_deny is HALT, not continue.
    """
    sha = request_sha256(request)
    gate = spend_gate.assert_open(
        request_sha256=sha,
        estimated_cost_usd=estimated_cost_usd,
        approval=approval,
        approval_file=approval_file,
    )
    run = _call("POST", DESIGN_PATH, payload=request, transport=transport)
    return {
        "run": run,
        "provenance": design_provenance(request, run=run, submitted=True, gate=gate),
    }
