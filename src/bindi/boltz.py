"""Hosted Boltz API design client, plus the ranked-table helpers curation reads.

bindi designs through the **hosted Boltz API** (https://api.boltz.bio/compute/v1).
There is no self-hosted published model in this package: nothing here downloads
model weights, nothing here shells out to a design binary, and nothing here runs a
published model on our own GPU. That path was removed deliberately.

Two things live in this module because they are two halves of one seam -- the Boltz
design engine's input (a design request we submit) and its output (the ranked table
curation reads back). Splitting them would buy a file, not clarity.

REQUEST SHAPE -- read this before changing anything here.
    The design endpoint requires the LEGACY body:

        {"num_proteins": N,
         "target": {"type": "no_template", "entities": [...],
                    "epitope_residues": {"C": [...]}},
         "binder_specification": {"type": "no_template", "modality": "custom_protein",
                                  "entities": [...]}}

    The type-discriminated "migrated" body ({"type": "binder", "target": {...},
    "binder": {...}}) is REJECTED LIVE, verified against the production API on
    2026-10-21 against both /protein/design and /protein/design/estimate-cost:

        HTTP 400 {"code":"VALIDATION_ERROR",
                  "message":"Request validation failed. binder_specification is
                             required. 1 other field needs attention.", ...}

    Boltz's published docs claim the discriminated shape is the preferred one and
    that legacy is merely "still accepted". The live API disagrees. Trust the live
    API. :func:`validate_legacy_request` refuses the discriminated shape locally so
    the failure is loud and free rather than a billed round trip.

SPEND
    :func:`submit` is the only function here that can cost money and it cannot be
    reached without an explicit caller-supplied approval record. Estimating a cost
    and reading a job's status never submit anything as a side effect. The human
    approval gate lives in the fleet spec (APPR, spend_cap_usd, on_deny HALT); this
    client must never become a way around it.

AUTH
    Header ``x-api-key``, read from the ``BOLTZ_API_KEY`` environment variable. The
    key is never logged, echoed, or included in an exception message.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

# ---- hosted API ------------------------------------------------------------------

API_KEY_ENV = "BOLTZ_API_KEY"
DEFAULT_API_BASE = "https://api.boltz.bio/compute/v1"
DESIGN_PATH = "/protein/design"
DEFAULT_TIMEOUT_S = 60

#: Terminal job states (see the Boltz data-retention guide: the retention clock
#: starts when a run reaches one of these, not when it was created).
TERMINAL_STATUSES = {"succeeded", "failed", "stopped", "cancelled", "canceled"}


def api_base() -> str:
    return os.environ.get("BOLTZ_API_BASE", DEFAULT_API_BASE).rstrip("/")


class BoltzAPIError(RuntimeError):
    """Non-2xx from the hosted API. Carries the API's own error text verbatim."""

    def __init__(self, status: int, body: str, *, path: str, code: str | None = None,
                 message: str | None = None) -> None:
        self.status = status
        self.body = body
        self.path = path
        self.code = code
        self.message = message
        detail = message or body or "(empty response body)"
        super().__init__(f"Boltz API {status} on {path}: {code or 'ERROR'}: {detail}")


class SpendNotApproved(RuntimeError):
    """submit() was called without an explicit human approval record."""


class RequestShapeError(ValueError):
    """A design request body is not the legacy shape the live API requires."""


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"{API_KEY_ENV} is not set. bindi designs through the hosted Boltz API; "
            "there is no local fallback."
        )
    return key


def _call(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """One request, one answer. No retries -- a 4xx is a fact, not a flake."""
    url = api_base() + path
    if query:
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
    data = None
    headers = {"x-api-key": _api_key(), "accept": "application/json"}
    if body is not None:
        data = canonical_json(body).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # non-2xx: surface the API's own words
        text = exc.read().decode("utf-8", errors="replace")
        code = message = None
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            code = parsed.get("code")
            message = parsed.get("message")
            details = parsed.get("details")
            if details:
                message = f"{message} details={json.dumps(details, sort_keys=True)}"
        raise BoltzAPIError(exc.code, text, path=path, code=code, message=message) from None
    if not text.strip():
        return {}
    return json.loads(text)


def whoami() -> dict[str, Any]:
    """GET /auth/me -- free, read-only. Proves the key resolves to a workspace."""
    return _call("GET", "/auth/me")


def estimate_cost(body: dict[str, Any]) -> dict[str, Any]:
    """POST /protein/design/estimate-cost -- free, read-only, submits nothing."""
    validate_legacy_request(body)
    return _call("POST", f"{DESIGN_PATH}/estimate-cost", body=body)


def submit(body: dict[str, Any], *, approval: str) -> str:
    """POST /protein/design. THIS SPENDS MONEY.

    ``approval`` is required and must be a non-empty record of the human who
    approved this spend (the APPR gate in the fleet spec). There is no default and
    no truthy-by-accident path: an empty or non-string approval raises.
    Returns the job id.
    """
    if not isinstance(approval, str) or not approval.strip():
        raise SpendNotApproved(
            "submit() requires an explicit approval record naming the human who "
            "approved this spend. Estimating a cost does not approve it."
        )
    validate_legacy_request(body)
    response = _call("POST", DESIGN_PATH, body=body)
    job_id = response.get("id") if isinstance(response, dict) else None
    if not job_id:
        raise BoltzAPIError(
            200, json.dumps(response), path=DESIGN_PATH,
            code="NO_JOB_ID", message="design accepted but no job id in the response",
        )
    return str(job_id)


def get_status(job_id: str) -> dict[str, Any]:
    """GET /protein/design/{id} -- free, read-only."""
    return _call("GET", f"{DESIGN_PATH}/{job_id}")


def list_results(job_id: str, *, limit: int = 100, after_id: str | None = None) -> dict[str, Any]:
    """GET /protein/design/{id}/results -- one cursor page, free and read-only.

    Verified live on 2026-10-21 against a running job: returns
    ``{"data": [], "has_more": false, "first_id": null, "last_id": null}``.
    """
    return _call("GET", f"{DESIGN_PATH}/{job_id}/results",
                 query={"limit": limit, "after_id": after_id})


def iter_results(job_id: str, *, limit: int = 100) -> Iterator[dict[str, Any]]:
    """Walk every result page in order."""
    after_id: str | None = None
    while True:
        page = list_results(job_id, limit=limit, after_id=after_id)
        for item in page.get("data") or []:
            yield item
        if not page.get("has_more"):
            return
        after_id = page.get("last_id")
        if not after_id:
            return


def artifact_urls(result: dict[str, Any]) -> dict[str, str]:
    """Pull download URLs out of one result record.

    HONEST LIMIT: the per-result artifact field names are documented (``artifacts``
    holding short-lived URLs with ``url_expires_at``) but have NOT been observed
    live here -- the only job to date is still running and its results page is
    empty. So this reads whatever URL-bearing entries ``artifacts`` actually
    contains rather than hard-coding names that might be wrong. Confirm against a
    completed job before relying on specific keys, and download promptly: the URLs
    expire independently of the 7-day data-retention window.
    """
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    out: dict[str, str] = {}
    for name, entry in artifacts.items():
        if isinstance(entry, str) and entry.startswith("http"):
            out[name] = entry
        elif isinstance(entry, dict) and isinstance(entry.get("url"), str):
            out[name] = entry["url"]
    return out


def download_artifact(url: str, dest: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> Path:
    """Fetch one short-lived artifact URL to disk. No API key is sent: these are
    pre-signed URLs, and the key must not leak into a storage request."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        dest.write_bytes(response.read())
    return dest


# ---- request construction --------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """The campaign's content-address rule, used for both hashing and the wire body."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def request_digest(body: dict[str, Any]) -> str:
    """sha256 over the canonical JSON -- part of the campaign's content address."""
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


#: Standing prohibition: this key must never appear in a request body.
PROHIBITED_REQUEST_KEYS = ("non_binding_residues",)


def _find_prohibited(node: Any) -> str | None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in PROHIBITED_REQUEST_KEYS:
                return key
            found = _find_prohibited(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_prohibited(item)
            if found:
                return found
    return None


def validate_legacy_request(body: dict[str, Any]) -> None:
    """Refuse anything the live API will refuse, locally and for free."""
    if not isinstance(body, dict):
        raise RequestShapeError("design request body must be a JSON object")
    if "type" in body or "binder" in body:
        raise RequestShapeError(
            "type-discriminated request body refused: the live design endpoint "
            "rejects it with HTTP 400 VALIDATION_ERROR ('binder_specification is "
            "required', 'templates is required'), verified 2026-10-21 against both "
            "/protein/design and /protein/design/estimate-cost. Send the legacy "
            "{num_proteins, target, binder_specification} shape."
        )
    prohibited = _find_prohibited(body)
    if prohibited:
        raise RequestShapeError(f"{prohibited} must never appear in a design request body")
    missing = [k for k in ("num_proteins", "target", "binder_specification") if k not in body]
    if missing:
        raise RequestShapeError(f"legacy design request is missing: {', '.join(missing)}")
    target = body["target"]
    if not isinstance(target, dict) or not target.get("entities"):
        raise RequestShapeError("target.entities is required and must be non-empty")
    spec = body["binder_specification"]
    if not isinstance(spec, dict) or not spec.get("entities"):
        raise RequestShapeError("binder_specification.entities is required and must be non-empty")
    if not spec.get("modality"):
        raise RequestShapeError("binder_specification.modality is required")


#: bindi modality names -> the API's binder_specification.modality.
#: Only "custom_protein" has been exercised against the live API; anything not
#: listed is passed through verbatim rather than silently coerced.
MODALITY_TO_API: dict[str, str] = {
    "binder": "custom_protein",
    "miniprotein": "custom_protein",
    "protein": "custom_protein",
    "custom_protein": "custom_protein",
    "peptide": "custom_protein",
}


def api_modality(modality: str | None) -> str:
    key = (modality or "binder").strip().lower()
    return MODALITY_TO_API.get(key, key)


def _entities(raw: Any, *, field: str, entity_type: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise RequestShapeError(f"policy.boltz.{field} must be a non-empty list")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RequestShapeError(f"each entry in policy.boltz.{field} must be an object")
        chain_ids = item.get("chain_ids") or ([item["chain_id"]] if item.get("chain_id") else None)
        value = item.get("value") or item.get("sequence") or item.get("length")
        if not chain_ids or not value:
            raise RequestShapeError(
                f"each entry in policy.boltz.{field} needs chain_ids and a value "
                "(a sequence, or a '60..100' length range for a designed chain)"
            )
        out.append({
            "type": item.get("type") or entity_type,
            "chain_ids": [str(c) for c in chain_ids],
            "value": str(value),
        })
    return out


def build_design_request(boltz: dict[str, Any]) -> dict[str, Any]:
    """Build the legacy design body from a campaign's ``boltz`` policy block.

    The hosted API takes sequences, not a target name and not a design-spec YAML:
    a campaign written for the old local runner will fail here, loudly, naming the
    key it is missing.
    """
    num = (
        boltz.get("num_proteins")
        or boltz.get("num_designs")
        or boltz.get("number_of_final_designs")
        or boltz.get("budget")
    )
    if num is None:
        raise RequestShapeError(
            "policy.boltz.num_proteins is required (num_designs / "
            "number_of_final_designs / budget are accepted as aliases)"
        )
    raw_target = boltz.get("target_entities") or boltz.get("target")
    if isinstance(raw_target, str):
        raise RequestShapeError(
            f"policy.boltz.target is a bare name ({raw_target!r}). The hosted Boltz API "
            "takes explicit sequences: set policy.boltz.target_entities to a list of "
            "{chain_ids, value} objects."
        )
    target: dict[str, Any] = {
        "type": "no_template",
        "entities": _entities(raw_target, field="target_entities", entity_type="protein"),
    }
    epitope = boltz.get("epitope_residues")
    if epitope:
        if not isinstance(epitope, dict):
            raise RequestShapeError("policy.boltz.epitope_residues must be a {chain: [index]} map")
        target["epitope_residues"] = {
            str(chain): [int(i) for i in residues] for chain, residues in epitope.items()
        }
    binder = boltz.get("binder") or {}
    if not isinstance(binder, dict):
        raise RequestShapeError("policy.boltz.binder must be an object")
    raw_binder_entities = binder.get("entities")
    if raw_binder_entities is None:
        chain_ids = binder.get("chain_ids") or ([binder["chain_id"]] if binder.get("chain_id") else None)
        length = binder.get("length")
        if not chain_ids or not length:
            raise RequestShapeError(
                "policy.boltz.binder needs entities, or chain_ids plus a length range "
                "such as '60..100'"
            )
        raw_binder_entities = [{"chain_ids": chain_ids, "value": length}]
    body = {
        "num_proteins": int(num),
        "target": target,
        "binder_specification": {
            "type": "no_template",
            "modality": api_modality(binder.get("modality") or boltz.get("modality")),
            "entities": _entities(
                raw_binder_entities, field="binder.entities", entity_type="designed_protein"
            ),
        },
    }
    validate_legacy_request(body)
    return body


# ---- ranked tables (what curation reads back) ------------------------------------

# Ranked metrics written by the design engine's filtering step.
RANKED_STAGE = Path("final_ranked_designs")
RANKED_METRICS = "all_designs_metrics.csv"
REFOLD_CIF = Path("intermediate_designs_inverse_folded") / "refold_cif"

# Legacy BindCraft2 layout (still accepted when curating old campaigns).
LEGACY_RANK_STAGE = Path("3_Ranked")
LEGACY_RANKED_NAME = "!_Ranked.csv"

# Map bindi / legacy rank_on names to ranked-table columns.
METRIC_ALIASES: dict[str, str] = {
    "i_pTM": "design_to_target_iptm",
    "ipTM": "design_to_target_iptm",
    "iptm": "design_to_target_iptm",
    "pTM": "design_ptm",
    "i_pAE": "min_design_to_target_pae",
    "ipAE": "min_design_to_target_pae",
    "pAE": "min_design_to_target_pae",
    "i_pLDDT": "design_plddt",
    "pLDDT": "design_plddt",
}


def locate_ranked_table(project_folder: Path) -> Path | None:
    boltz = project_folder / RANKED_STAGE / RANKED_METRICS
    if boltz.exists():
        return boltz
    legacy = project_folder / LEGACY_RANK_STAGE / LEGACY_RANKED_NAME
    if legacy.exists():
        return legacy
    flat = project_folder / "ranked.csv"
    if flat.exists():
        return flat
    return None


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """Unify ranked rows (hosted Boltz and legacy BindCraft) for bindi curation."""
    out = dict(row)
    if not (out.get("design") or out.get("Design")):
        ident = out.get("id") or out.get("sample_id") or ""
        if ident:
            out["design"] = ident
    if not out.get("Binder_Sequence") and out.get("sequence"):
        out["Binder_Sequence"] = out["sequence"]
    if not out.get("rank") and out.get("final_rank"):
        out["rank"] = str(out["final_rank"])
    for alias, source in METRIC_ALIASES.items():
        if not (out.get(alias) or "").strip() and (out.get(source) or "").strip():
            out[alias] = out[source]
    return out
