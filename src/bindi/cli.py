"""Command line interface for bindi."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import VERSION
from .case import CaseError, DesignHold
from .curate import curate
from .e2e import EXIT_CASE_ERROR, EXIT_HOLD, case_run
from .generate import generate
from .policy import load_policy, policy_from_nl_stub
from .resource import resource_descriptor
from .run import run


def _load(path: str | None) -> dict:
    if path is None or path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        raise SystemExit("empty policy input")
    if text.strip()[0] in "{[":
        return load_policy(None, payload=json.loads(text))
    return load_policy(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bindi",
        description="Generate and curate protein binders from a campaign policy.",
    )
    parser.add_argument("--version", action="version", version=f"bindi {VERSION}")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Run BindCraft2 design for a campaign policy")
    gen.add_argument("policy", nargs="?", help="Campaign policy JSON (or '-' for stdin)")
    gen.add_argument("--workdir", type=Path, default=Path.cwd())
    gen.add_argument("--dry-run", action="store_true", help="Write fixture ranked output (no GPU)")

    cur = sub.add_parser("curate", help="Build a gallery from campaign results")
    cur.add_argument("policy", nargs="?", help="Campaign policy JSON")
    cur.add_argument("--project", type=Path, help="BindCraft2 project_folder override")
    cur.add_argument("--workdir", type=Path, default=Path.cwd())
    cur.add_argument(
        "--bindcraft-rank",
        action="store_true",
        help="Call bindcraft rank before building the gallery",
    )

    full = sub.add_parser("run", help="Generate then curate")
    full.add_argument("policy", nargs="?", help="Campaign policy JSON")
    full.add_argument("--workdir", type=Path, default=Path.cwd())
    full.add_argument("--dry-run", action="store_true")
    full.add_argument("--bindcraft-rank", action="store_true")

    e2e = sub.add_parser(
        "case-run",
        help="Run one synthetic immunology case end to end and write a single JSONL trace",
    )
    e2e.add_argument("case", help="Patient case record JSON (bindi.immunology_case.v1)")
    e2e.add_argument("--policy", help="Campaign policy JSON (optional; target comes from the case)")
    e2e.add_argument("--workdir", type=Path, default=Path.cwd())
    e2e.add_argument("--trace", type=Path, help="Trace path (default <workdir>/case_trace.jsonl)")
    e2e.add_argument(
        "--design",
        action="store_true",
        help="Request a real design run. Refused while the hold is in force (exit 2).",
    )
    e2e.add_argument("--human-reference-fasta", help="Reference FASTA for the human-reference gate")
    e2e.add_argument("--human-reference-source", help="Version/source label for the human reference")
    e2e.add_argument("--hazard-screen-fasta", help="Reference FASTA for the hazard screen gate")
    e2e.add_argument("--hazard-screen-source", help="Version/source label for the hazard screen")
    e2e.add_argument("--margin-scorer", help="module:function scorer for the wild-type margin")

    sub.add_parser("recruit", help="Print resource descriptor JSON for agent recruitment")

    stub = sub.add_parser("stub", help="Build a starter policy from a one-line query")
    stub.add_argument("query", help="Natural-language design intent")
    stub.add_argument("--target", default="hPDL1")
    stub.add_argument("--modality", default="binder")
    stub.add_argument("--designs", type=int, default=5, dest="number_of_final_designs")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    command = args.command

    if command == "recruit":
        json.dump(resource_descriptor(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if command == "stub":
        document = policy_from_nl_stub(
            args.query,
            target=args.target,
            modality=args.modality,
            number_of_final_designs=args.number_of_final_designs,
        )
        json.dump(document, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if command == "case-run":
        policy = _load(args.policy) if args.policy else None
        try:
            result = case_run(
                args.case,
                policy=policy,
                workdir=args.workdir,
                trace_path=args.trace,
                allow_design=args.design,
                reference_fasta=args.human_reference_fasta,
                reference_source=args.human_reference_source,
                hazard_fasta=args.hazard_screen_fasta,
                hazard_source=args.hazard_screen_source,
                margin_scorer_spec=args.margin_scorer,
            )
        except DesignHold as exc:
            json.dump({"error": "design_hold", "detail": str(exc)}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return EXIT_HOLD
        except CaseError as exc:
            json.dump({"error": "case_error", "detail": str(exc)}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return EXIT_CASE_ERROR
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return int(result["exit_code"])

    policy_path = getattr(args, "policy", None)
    if policy_path is None and command != "recruit":
        parser.error(f"{command} requires a policy path")
    policy = _load(policy_path)

    if command == "generate":
        result = generate(policy, workdir=args.workdir, dry_run=args.dry_run)
    elif command == "curate":
        result = curate(
            policy,
            project_folder=args.project,
            workdir=args.workdir,
            use_bindcraft_rank=args.bindcraft_rank,
        )
    else:
        result = run(
            policy,
            workdir=args.workdir,
            dry_run=args.dry_run,
            use_bindcraft_rank=args.bindcraft_rank,
        )

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
