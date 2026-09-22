---
schema: wetware.agent.v1
resource: bindi
resource_schema: wetware.resource.v1
version: 0.1.0
audience: frontier_intelligence
human_context: README.md
descriptor: bindi recruit
---

# bindi — recruitment contract

This document is for **frontier AI** that selects, configures, and invokes bindi at runtime. Clinicians and collaborators read [README.md](README.md).

Canonical machine contract: stdout of `bindi recruit` (`wetware.resource.v1`). This file is the prose mirror of that descriptor plus operational rules not encoded in JSON.

## Identity

| Field | Value |
| --- | --- |
| `name` | `bindi` |
| `tool` | BindCraft2 CLI (`bindcraft`) — https://github.com/PacesaLab/BindCraft2 |
| `capabilities` | `generate`, `curate` |
| `worker_shape` | container `containers/Dockerfile`, `ENTRYPOINT bindi`, base image must provide `bindcraft` |

## Slots

### Input: `campaign` (`bindi.policy.v1`)

You **author** this object from the user’s natural-language request before invoking bindi.

Required semantics:

| Path | Type | Rule |
| --- | --- | --- |
| `schema` | string | `bindi.policy.v1` (auto-added if missing) |
| `query` | string | Verbatim design intent from user; audit string |
| `bindcraft` | object | BindCraft2 campaign payload; must include resolvable `target`, `modality`, `project_folder` |
| `bindcraft.number_of_final_designs` | int | Design budget; default align `curation.top_n` to this unless user specifies otherwise |
| `curation.rank_on` | string[] | CSV column names for ordering; default `["i_pDAE"]` |
| `curation.top_n` | int | Gallery shortlist size |
| `gallery` | string | Relative directory name for output; default `gallery` |

**Normalization:** If the payload is bare BindCraft2 JSON (no `schema`), bindi wraps it:

```json
{ "schema": "bindi.policy.v1", "bindcraft": <payload>, "curation": {}, "gallery": "gallery" }
```

**Path resolution:** `bindcraft.project_folder` and `gallery` are resolved under `--workdir` when relative.

**Stub helper** (seed only; you must validate BC2 fields before GPU):

```bash
bindi stub "<user intent>" --target <id> --modality <modality> --designs <n>
```

Reference instance: `campaigns/pdl1.json`.

### Output: `gallery` (`bindi.gallery.v1`)

Directory produced by `curate` / `run`:

| Path | Schema | Role |
| --- | --- | --- |
| `index.json` | `bindi.gallery.v1` | Ranked `entries[]` with `design`, `rank`, `scores`, `sequence`, `structure` |
| `ranked.csv` | tabular | Top `top_n` rows from BindCraft2 ranked table |
| `structures/*.cif` | mmCIF | Copied when matched under `project_folder` |

Consume `index.json` for programmatic downstream steps; do not infer ranks from filenames alone.

## Recruitment decision

Recruit bindi **iff** all hold:

1. **Task class** ∈ {de novo binder design, binder shortlisting, re-curation of existing BC2 campaign folder}.
2. **Exclusions:** task is not satisfied by structure prediction only, rigid docking only, or non-design molecular tasks.
3. **Runtime:** `bindcraft` on `PATH` (or `BINDCRAFT_CMD`) for real `generate`, **or** explicit `dry_run=true` for contract testing only.
4. **Policy:** you can populate `bindcraft.target` and `bindcraft.modality` per BindCraft2 docs for the organism/modality requested.

If any fail → do not recruit; choose another resource or ask the user for missing BC2 inputs (target id, epitope/hotspots, modality).

## Capability routing

| Goal | Command | Notes |
| --- | --- | --- |
| Design + gallery | `bindi run <policy> [--workdir W] [--dry-run]` | Default path |
| Design only | `bindi generate <policy> [--workdir W] [--dry-run]` | Leaves ranked table under `project_folder` |
| Gallery only | `bindi curate <policy> [--project P] [--workdir W] [--bindcraft-rank]` | Requires `3_Ranked/!_Ranked.csv` or legacy `ranked.csv` |
| Descriptor | `bindi recruit` | No inputs; prints `wetware.resource.v1` JSON |

Policy may be a file path, or `-` / stdin JSON.

**Preference:** `run` unless you need split phases (e.g. regenerate without re-curating, or curate after external QC).

## Execution graph

```text
NL query
  → author bindi.policy.v1 (stub optional)
  → [optional] bindi run --dry-run   # validates layout; fixture ranked data
  → bindi run                        # bindcraft design → curate → gallery
  → read gallery/index.json
  → hand off gallery path + manifest to parent workflow / user-facing summary
```

### CLI result JSON (`run`)

```json
{
  "generate": {
    "status": "completed" | "dry_run",
    "campaign_file": "<path>",
    "project_folder": "<path>",
    "gallery": "<path>"
  },
  "curate": {
    "schema": "bindi.gallery.v1",
    "gallery": "<path>",
    "count": <int>,
    "structures_copied": <int>,
    "manifest": "<path>/index.json"
  }
}
```

Non-zero `bindcraft design` → `generate` raises; do not assert gallery completeness.

### Environment

| Variable | Effect |
| --- | --- |
| `BINDCRAFT_CMD` | Executable name for `bindcraft` (default `bindcraft`) |

### Worker container

```bash
docker build -f containers/Dockerfile -t bindi:local .   # requires bindcraft:local base
docker run --gpus all -v <mount>:/work -w /work bindi:local run <policy.json>
```

Image default `CMD`: `recruit` (descriptor probe, not a design job).

## Policy authoring from NL

Map user utterances to `bindcraft` fields explicitly; do not leave implicit defaults for production GPU runs.

| NL signal | Policy field |
| --- | --- |
| protein / target name | `bindcraft.target` (BC2 identifier) |
| VHH, nanobody, miniprotein, peptide | `bindcraft.modality` |
| “top N”, “shortlist”, “final designs” | `number_of_final_designs`, `curation.top_n` |
| epitope, hotspot, interface residue | BC2 hotspot keys inside `bindcraft` (per BindCraft2 spec) |
| ranking metric, “best interface” | `curation.rank_on` (e.g. `i_pDAE`, `i_pTM`) |
| output location | `bindcraft.project_folder`, `gallery` |

**Optimization objective (when user does not specify):** minimize `number_of_final_designs` consistent with stated experimental throughput; rank on interface-quality metrics; keep `query` aligned with mechanism (e.g. blockade, degradation, detection).

## Python surface

```python
from bindi import load_policy, run, curate, resource_descriptor

resource_descriptor()  # wetware.resource.v1
policy = load_policy("campaign.json")
outcome = run(policy, workdir=workdir, dry_run=False)
manifest_path = outcome["curate"]["manifest"]
```

## Failure modes

| Condition | Agent action |
| --- | --- |
| `FileNotFoundError: no ranked table` | Run `generate` first or fix `project_folder` |
| `bindcraft design failed` exit code | Surface code; inspect `project_folder`; do not fabricate gallery |
| Empty `entries` | Report zero designs; check BC2 filters and campaign JSON |
| Missing `structures/` | Non-fatal; sequences may still be in `ranked.csv` / `entries[].sequence` |
| `--dry-run` | Label all downstream claims as pipeline test, not biological results |

## Done predicate

Recruitment task is complete when:

- `campaign` on disk matches `bindi.policy.v1` (normalized).
- `gallery/index.json` exists with `"schema": "bindi.gallery.v1"`.
- `len(entries) <= curation.top_n`.
- Parent workflow holds `curate.gallery` path and parsed manifest (or explicit failure record).

Local verification without GPU: `python3 -m pytest` in repo; `bindi run <policy> --dry-run`.

## Boundaries

- Do not reimplement BindCraft2 inside bindi; extend via `bindcraft` CLI and policy JSON.
- `curate` is safe to rerun on the same `project_folder` after new designs appear.
- License: bindi MIT; BindCraft2 separate — worker images must comply with both.
