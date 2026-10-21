"""APPR: the human spend gate that stands in front of every paid Boltz submission.

Submitting a design job to the hosted Boltz API costs real money, so bindi treats
submission the way it treats a real design run: refused by default, allowed only when a
human approval record says so. The gate is fail-closed in every direction — no approval
record, a malformed one, an expired one, one that approves a *different* request body, or a
cap below the estimated cost all mean closed, and closed raises rather than warns.

The approval is `bindi.appr_approval.v1`, a JSON file whose path is given by
``BINDI_APPR_APPROVAL``. It is pinned to one request body by sha256, mirroring the
per-SHA sign-off rule: approving a campaign does not approve the next edit of it.

    {
      "schema": "bindi.appr_approval.v1",
      "gate": "APPR",
      "approved": true,
      "approved_by": "Mathew Chu",
      "cap_usd": 15.0,
      "request_sha256": "<sha256 of the exact submitted request body>",
      "expires_at": "2026-10-22T00:00:00Z",
      "on_deny": "HALT"
    }

Nothing in this module can be set from inside bindi: there is no "approve" function, no
environment flag that opens the gate on its own, and no default that assumes approval.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GATE_ID = "APPR"
SCHEMA = "bindi.appr_approval.v1"

#: Path to the human approval record. Unset means the gate is closed.
APPROVAL_ENV = "BINDI_APPR_APPROVAL"

ON_DENY = "HALT"

CLOSED_REASON = (
    "APPR spend gate is closed: submitting a design job to the hosted Boltz API spends real "
    "money, so it requires a human approval record (bindi.appr_approval.v1) naming the "
    f"approver, a cap in USD, and the sha256 of the exact request body, supplied via "
    f"{APPROVAL_ENV}. No approval was found, so nothing was submitted and nothing was spent."
)


class SpendGateClosed(Exception):
    """A paid submission was attempted while the APPR gate was closed. on_deny: HALT."""


def approval_path(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    from_env = os.environ.get(APPROVAL_ENV)
    return Path(from_env) if from_env else None


def load_approval(explicit: str | Path | None = None) -> dict[str, Any] | None:
    """Read the approval record, or None when none is configured."""
    path = approval_path(explicit)
    if path is None:
        return None
    if not path.exists():
        raise SpendGateClosed(
            f"{APPROVAL_ENV} points at {path}, which does not exist. The gate stays closed; "
            "nothing was submitted."
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpendGateClosed(f"approval record {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SpendGateClosed(f"approval record {path} must be a JSON object")
    document["_path"] = str(path)
    return document


def approval_problems(
    approval: dict[str, Any] | None,
    *,
    request_sha256: str,
    estimated_cost_usd: float | None,
    now: datetime | None = None,
) -> list[str]:
    """Everything wrong with this approval for this request. Empty means the gate is open."""
    if approval is None:
        return [CLOSED_REASON]

    problems: list[str] = []
    if approval.get("schema") != SCHEMA:
        problems.append(f"approval.schema must be {SCHEMA!r}, found {approval.get('schema')!r}")
    if approval.get("gate") != GATE_ID:
        problems.append(f"approval.gate must be {GATE_ID!r}, found {approval.get('gate')!r}")
    if approval.get("approved") is not True:
        problems.append("approval.approved is not true: the human has not approved this spend")
    if not str(approval.get("approved_by") or "").strip():
        problems.append("approval.approved_by is empty: an approval must name its approver")
    if approval.get("on_deny") != ON_DENY:
        problems.append(f"approval.on_deny must be {ON_DENY!r} (a denied gate halts, it does not continue)")

    cap = approval.get("cap_usd")
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
        problems.append("approval.cap_usd must be a positive number of US dollars")
    elif estimated_cost_usd is None:
        problems.append(
            "no estimated cost was supplied, so it cannot be checked against the cap; an "
            "unpriced job is never submitted"
        )
    elif float(estimated_cost_usd) > float(cap):
        problems.append(
            f"estimated cost ${float(estimated_cost_usd):.4f} exceeds the approved cap "
            f"${float(cap):.4f}"
        )

    approved_sha = str(approval.get("request_sha256") or "").strip().lower()
    if not approved_sha:
        problems.append("approval.request_sha256 is missing: an approval is pinned to one request body")
    elif approved_sha != request_sha256.lower():
        problems.append(
            "approval.request_sha256 does not match the request being submitted "
            f"(approved {approved_sha[:12]}..., request {request_sha256[:12]}...): editing a "
            "request after approval requires a new approval"
        )

    expires = str(approval.get("expires_at") or "").strip()
    if not expires:
        problems.append("approval.expires_at is missing: an approval does not stay open forever")
    else:
        try:
            deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except ValueError:
            problems.append(f"approval.expires_at is not an ISO 8601 timestamp: {expires!r}")
        else:
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if (now or datetime.now(timezone.utc)) >= deadline:
                problems.append(f"approval expired at {expires}")
    return problems


def gate_status(
    *,
    request_sha256: str,
    estimated_cost_usd: float | None = None,
    approval: dict[str, Any] | None = None,
    approval_file: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Report the gate without acting on it. Safe to call anywhere; makes no network call."""
    record = approval if approval is not None else load_approval(approval_file)
    problems = approval_problems(
        record,
        request_sha256=request_sha256,
        estimated_cost_usd=estimated_cost_usd,
        now=now,
    )
    return {
        "gate": GATE_ID,
        "open": not problems,
        "on_deny": ON_DENY,
        "request_sha256": request_sha256,
        "estimated_cost_usd": estimated_cost_usd,
        "approved_by": (record or {}).get("approved_by"),
        "cap_usd": (record or {}).get("cap_usd"),
        "approval_file": (record or {}).get("_path"),
        "problems": problems,
    }


def assert_open(
    *,
    request_sha256: str,
    estimated_cost_usd: float | None = None,
    approval: dict[str, Any] | None = None,
    approval_file: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the gate record when the gate is open; raise `SpendGateClosed` otherwise."""
    status = gate_status(
        request_sha256=request_sha256,
        estimated_cost_usd=estimated_cost_usd,
        approval=approval,
        approval_file=approval_file,
        now=now,
    )
    if not status["open"]:
        raise SpendGateClosed(
            f"{GATE_ID} gate closed (on_deny: {ON_DENY}); nothing submitted, nothing spent: "
            + "; ".join(status["problems"])
        )
    return status
