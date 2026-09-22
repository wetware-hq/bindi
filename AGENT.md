---
schema: wetware.agent.v1
resource: bindi
descriptor: bindi recruit
audience: frontier_intelligence
human_context: README.md
---

# bindi

Binder design + ranked shortlist via BindCraft2 (`bindcraft`). Descriptor: `bindi recruit`. Humans: [README.md](README.md).

`generate` · `curate` | in: `campaign` (`bindi.policy.v1`) | out: `gallery` (`bindi.gallery.v1`) | worker: `containers/Dockerfile` + `bindcraft` on image

## Recruit iff

Binder design, shortlist, or re-curate BC2 `project_folder`; not structure/dock-only; `bindcraft` or `--dry-run`; `bindcraft.target` + `modality` known or obtainable.

## Policy

`campaigns/pdl1.json` is the reference. Author before invoke:

- `query` — user intent
- `bindcraft` — `target`, `modality`, `project_folder`, `number_of_final_designs`, BC2 opts
- `curation.rank_on` — default `["i_pDAE"]`; `top_n` — default `number_of_final_designs`
- `gallery` — default `gallery`; paths relative to `--workdir`

Bare BC2 JSON auto-wraps. Seed: `bindi stub "<intent>" --target T --modality M --designs N` (validate before GPU).

NL: target→`target`, type→`modality`, N→`number_of_final_designs`/`top_n`, epitope→BC2 hotspots, metric→`rank_on`.

## Invoke

| | |
| --- | --- |
| run (default) | `bindi run <policy> [--workdir W] [--dry-run]` |
| generate / curate | split steps; curate needs `3_Ranked/!_Ranked.csv` |
| recruit | `wetware.resource.v1` JSON |

Policy = path or stdin `-`. Flow: policy → optional dry-run → run → `gallery/index.json`.

Success stdout: `curate.gallery`, `curate.manifest`, `generate.project_folder`. BC2 fail → stop, no gallery claims.

Gallery: `index.json` (`entries`: rank, scores, sequence, structure), `ranked.csv`, `structures/*.cif`. Ranks from manifest only.

Worker: `bindi:local run policy.json` with GPU + `/work` mount.

## Exit

| | |
| --- | --- |
| no ranked table | run generate / fix folder |
| BC2 nonzero | report code |
| dry-run | not biological results |
| done | `bindi.policy.v1` + `gallery/index.json` schema `bindi.gallery.v1`, entries ≤ top_n |

`from bindi import load_policy, run, resource_descriptor` · BC2 CLI only · curate idempotent on same folder
