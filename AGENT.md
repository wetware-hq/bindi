---
schema: wetware.agent.v1
resource: bindi
resource_schema: wetware.resource.v1
version: 0.1.0
capabilities:
  - generate
  - curate
inputs:
  campaign:
    type: policy
    schema: bindi.policy.v1
outputs:
  gallery:
    type: directory
    schema: bindi.gallery.v1
container:
  dockerfile: containers/Dockerfile
  image: bindi:local
  entrypoint: bindi
recruit_command: bindi recruit
---

# Bindi — agent operating guide

You are recruiting **bindi**, a worker-deployable resource that designs de novo protein binders with [BindCraft2](https://github.com/PacesaLab/BindCraft2) and returns a **gallery** of ranked, scored candidates for experimental follow-up.

Use bindi when the user needs **binder generation and selection** for a protein target (miniprotein, VHH, peptide, or other modality supported by BindCraft2), not when they only need structure prediction, docking without design, or unrelated molecular biology tasks.

Human-facing overview: [README.md](README.md). Machine-readable contract: `bindi recruit` → `wetware.resource.v1` JSON (see [src/bindi/resource.py](src/bindi/resource.py)).

## Contract

| Slot | Schema | You provide |
| --- | --- | --- |
| **campaign** (input) | `bindi.policy.v1` | Target, modality, design count, output paths, curation metrics — derived from the user’s natural-language intent |
| **gallery** (output) | `bindi.gallery.v1` | Directory with `index.json`, `ranked.csv`, and `structures/` |

Capabilities:

- **generate** — run `bindcraft design` for the campaign.
- **curate** — rank designs and materialize the gallery from an existing BindCraft2 `project_folder`.

Prefer **`bindi run`** unless generation or curation must run separately (e.g. re-curate after manual QC).

## Recruitment

Before scheduling work on a GPU worker, confirm the descriptor matches expectations:

```bash
bindi recruit
```

Recruit bindi when:

1. The task explicitly asks for **de novo binder design** or a **shortlist of binders** against a target.
2. BindCraft2-appropriate inputs exist or can be resolved (target identifier, modality, optional hotspot/epitope constraints in `bindcraft`).
3. The environment provides **`bindcraft` on PATH** (container base `bindcraft:local`) or you will use **`--dry-run`** for pipeline validation only.

Do not recruit bindi for targets or modalities outside BindCraft2’s scope without checking [BindCraft2](https://github.com/PacesaLab/BindCraft2) documentation first.

## Workflow

### 1. Interpret intent → campaign policy

Translate the user query into a **`bindi.policy.v1`** document:

- **`query`** — one-sentence design intent (audit trail for humans).
- **`bindcraft`** — BindCraft2 campaign fields: `target`, `modality`, `number_of_final_designs`, `project_folder`, plus any BC2-specific options (hotspots, chains, filters).
- **`curation`** — `rank_on` (metric column names, default `["i_pDAE"]`), `top_n` (shortlist size).
- **`gallery`** — relative directory name for curated output (default `gallery`).

Starter from natural language (always **review and edit** before a production GPU run):

```bash
bindi stub "VHH binders for human PD-L1" \
  --target hPDL1 --modality VHH --designs 10 > campaign.json
```

Bare BindCraft2 JSON is accepted; bindi wraps it as `bindi.policy.v1` automatically.

Reference campaign: [campaigns/pdl1.json](campaigns/pdl1.json).

**Clinical utility bias:** prefer fewer, higher-confidence designs (`top_n` aligned with `number_of_final_designs`), explicit `rank_on` metrics that reflect interface quality (e.g. `i_pDAE`), and a clear `query` linking the design goal to a therapeutic or mechanistic hypothesis. Avoid inflating design counts without a stated experimental capacity.

### 2. Validate locally (no GPU)

```bash
uv pip install -e .
pytest
bindi run campaign.json --dry-run
```

`--dry-run` writes fixture ranked tables under `project_folder` and still builds a real gallery layout. Use this to verify paths, `top_n`, and manifest shape before spending GPU time.

### 3. Execute

**Full pipeline (typical):**

```bash
bindi run campaign.json --workdir /work
```

**Split steps:**

```bash
bindi generate campaign.json --workdir /work
bindi curate campaign.json --workdir /work
# Optional: bindi curate ... --bindcraft-rank  # delegate ranking to `bindcraft rank`
```

Set `BINDCRAFT_CMD` if the executable is not named `bindcraft`.

**Worker container** (build BindCraft2 image as `bindcraft:local` first):

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

Default container command is `bindi recruit` (descriptor only).

### 4. Deliver the gallery

On success, treat **`gallery/`** (or the path in `policy.gallery`) as the primary artifact:

```text
gallery/
  index.json      # bindi.gallery.v1 manifest: entries, scores, structure paths
  ranked.csv      # tabular shortlist (BindCraft2 columns + rank)
  structures/     # mmCIF copies when found under the campaign folder
```

Read `index.json` for programmatic handoff. Summarize for the user:

- Target, modality, and `query`
- Top designs by `rank` with key scores from `entries[].scores`
- Paths to structures and sequences (`Binder_Sequence` when present)
- Campaign folder (`manifest.campaign`) for traceability

If `generate` failed, do not claim a curated shortlist; surface BindCraft2 stderr/exit code and whether partial outputs exist under `project_folder`.

## Policy fields (quick reference)

```json
{
  "schema": "bindi.policy.v1",
  "query": "<design intent>",
  "bindcraft": {
    "target": "<BindCraft2 target id>",
    "modality": "binder | VHH | ...",
    "number_of_final_designs": 10,
    "project_folder": "results/<campaign>"
  },
  "curation": {
    "rank_on": ["i_pDAE"],
    "top_n": 10
  },
  "gallery": "gallery"
}
```

Paths in `project_folder` and `gallery` are resolved relative to `--workdir`.

## Python API

When embedding bindi in a larger agent loop:

```python
from bindi import run, curate, load_policy, resource_descriptor

assert resource_descriptor()["name"] == "bindi"
policy = load_policy("campaign.json")
outcome = run(policy, dry_run=False)
gallery_path = outcome["curate"]["gallery"]
```

## Engineering constraints

- **Minimum surface area:** do not fork BindCraft2 inside bindi; pass through `bindcraft` CLI and campaign JSON.
- **Idempotent curation:** `curate` can rerun on the same `project_folder` to refresh the gallery after new designs land.
- **No silent success:** non-zero `bindcraft design` exit codes raise; report them to the user.
- **LoC efficiency:** extend behavior via policy JSON and BC2 options before adding Python.

## Verification checklist

Before marking the task done:

- [ ] `campaign` validates as `bindi.policy.v1` (or normalizes from bare BC2 JSON).
- [ ] `bindi run ... --dry-run` succeeds in CI-like environments, or GPU `bindi run` completed on the worker.
- [ ] `gallery/index.json` has `schema: bindi.gallery.v1` and `entries` length ≤ `top_n`.
- [ ] User receives ranked designs with scores and file paths, plus any caveats (dry-run, missing structures, metric choice).

## License note

Bindi is MIT. BindCraft2 is a separate dependency with its own license; comply with both when deploying workers.
