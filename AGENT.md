---
schema: wetware.agent.v1
resource: bindi
descriptor: bindi recruit
audience: frontier_intelligence
human_context: README.md
---

# bindi

De novo **protein binder design + ranked shortlist** via BindCraft2 (`bindcraft`). Clinicians: [README.md](README.md). Machine descriptor: `bindi recruit` → `wetware.resource.v1`.

| | |
| --- | --- |
| **Capabilities** | `generate` (BC2 design), `curate` (rank → gallery) |
| **Input** | `campaign` — `bindi.policy.v1` |
| **Output** | `gallery` — `bindi.gallery.v1` directory |
| **Worker** | `containers/Dockerfile`; base image must expose `bindcraft` |

## Recruit when

All true:

1. Task = binder **design** or **shortlist**, or re-curate an existing BC2 `project_folder`.
2. Not: structure-only, dock-only, or unrelated mol-bio.
3. `bindcraft` available (`BINDCRAFT_CMD` optional), **or** `--dry-run` for pipeline test only.
4. You can set `bindcraft.target` + `bindcraft.modality` per [BindCraft2](https://github.com/PacesaLab/BindCraft2).

Else: other resource, or ask for target id / modality / hotspots.

## Policy (`bindi.policy.v1`)

Author from user NL before invoke. Example: `campaigns/pdl1.json`.

| Field | Rule |
| --- | --- |
| `query` | User intent (audit) |
| `bindcraft` | BC2 campaign: `target`, `modality`, `project_folder`, `number_of_final_designs`, + BC2 options |
| `curation.rank_on` | Default `["i_pDAE"]` |
| `curation.top_n` | Default = `number_of_final_designs` |
| `gallery` | Default `"gallery"` |

Bare BC2 JSON → wrapped as `{schema, bindcraft: payload, curation: {}, gallery: "gallery"}`. Relative paths resolve under `--workdir`.

Seed (edit before GPU): `bindi stub "<intent>" --target T --modality M --designs N`

**NL → fields:** target name → `target`; binder type → `modality`; count/shortlist → `number_of_final_designs` + `top_n`; epitope → BC2 hotspot keys in `bindcraft`; metric → `rank_on`. If unspecified: smallest N consistent with user throughput; prefer interface metrics (`i_pDAE`).

## Commands

| Need | Command |
| --- | --- |
| Default | `bindi run <policy.json> [--workdir W] [--dry-run]` |
| Design only | `bindi generate …` |
| Gallery only | `bindi curate … [--project P] [--bindcraft-rank]` |
| Contract JSON | `bindi recruit` |

Policy: file path or stdin (`-`). Prefer `run` unless split steps required.

**Flow:** NL → policy → optional `run --dry-run` → `run` → read `gallery/index.json` → emit `curate.gallery` + manifest.

**`run` stdout (success):** `generate.{status,project_folder}` + `curate.{gallery,manifest,count}`. BC2 nonzero exit → error; no fake gallery.

**Gallery:** `index.json` (`entries[]`: `design`, `rank`, `scores`, `sequence`, `structure`), `ranked.csv`, `structures/*.cif` if found. Use `index.json` for ranks.

**Worker:** `docker run --gpus all -v M:/work -w /work bindi:local run policy.json` (image CMD default = `recruit`).

## Errors

| Signal | Do |
| --- | --- |
| No ranked CSV | `generate` first or fix `project_folder` |
| BC2 failed | Report exit code; no shortlist claims |
| Empty entries | Report; check BC2 campaign/filters |
| No structures | OK if sequences in CSV/manifest |
| `--dry-run` | Mark results as pipeline test |

## Done

- Normalized `bindi.policy.v1` saved.
- `gallery/index.json` has `schema: bindi.gallery.v1`, `len(entries) ≤ top_n`.
- Downstream has gallery path + parsed manifest, or logged failure.

**API:** `from bindi import load_policy, run, resource_descriptor`

**Limits:** BC2 via CLI only; `curate` rerunnable on same folder; bindi MIT + BC2 license on workers.
