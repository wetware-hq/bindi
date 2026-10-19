---
schema: wetware.agent.v1
resource: bindi
resource_schema: wetware.resource.v1
version: 0.2.0
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

# Bindi — agent manual

You are recruiting **bindi**, a worker-deployable resource that designs de novo protein binders with [BoltzGen](https://github.com/HannesStark/boltzgen) (Boltz-1/Boltz-2) and returns a **gallery** of ranked, scored candidates for experimental follow-up.

Use bindi when the user needs **binder generation and selection** for a protein target (miniprotein, VHH, peptide, nanobody, or other modality supported by BoltzGen protocols), not when they only need structure prediction, docking without design, or unrelated molecular biology tasks.

Human-facing overview: [README.md](README.md). Machine-readable contract: `bindi recruit` → `wetware.resource.v1` JSON (see [src/bindi/resource.py](src/bindi/resource.py)).

## Contract

| Slot | Schema | You provide |
| --- | --- | --- |
| **campaign** (input) | `bindi.policy.v1` | Target, modality, design count, output paths, curation metrics — derived from the user’s natural-language intent |
| **gallery** (output) | `bindi.gallery.v1` | Directory with `index.json`, `ranked.csv`, and `structures/` |

Capabilities:

- **generate** — run `boltzgen run` for the campaign.
- **curate** — rank designs and materialize the gallery from an existing BoltzGen output directory (`project_folder`).

Prefer **`bindi run`** unless generation or curation must run separately (e.g. re-curate after manual QC).

## Recruitment

Before scheduling work on a GPU worker, confirm the descriptor matches expectations:

```bash
bindi recruit
```

Recruit bindi when:

1. The task explicitly asks for **de novo binder design** or a **shortlist of binders** against a target.
2. BoltzGen-appropriate inputs exist or can be resolved (`design_spec` YAML, protocol, optional binding-site constraints in the spec).
3. The environment provides **`boltzgen` on PATH** (see `containers/Dockerfile`) or you will use **`--dry-run`** for pipeline validation only.

Do not recruit bindi for targets or modalities outside BoltzGen’s protocols without checking [BoltzGen](https://github.com/HannesStark/boltzgen) documentation first.

## Workflow

### 1. Interpret intent → campaign policy

Translate the user query into a **`bindi.policy.v1`** document:

- **`query`** — one-sentence design intent (audit trail for humans).
- **`boltz`** — BoltzGen fields: `design_spec` (YAML path), `protocol`, `num_designs`, `budget` / `number_of_final_designs`, `project_folder`, plus optional `alpha`, `devices`, `seed`. Legacy `bindcraft` keys are migrated automatically.
- **`curation`** — `rank_on` (metric column names, default `["design_to_target_iptm"]`), `top_n` (shortlist size). The multiverse ensemble is **off by default**; enable it with `ensemble: true` (and optional `universes` scorer columns, defaulting to the higher-better metrics present when at least two exist).
- **`gallery`** — relative directory name for curated output (default `gallery`).

Starter from natural language (always **review and edit** before a production GPU run):

```bash
bindi stub "VHH binders for human PD-L1" \
  --target hPDL1 --modality VHH --designs 10 > campaign.json
```

Bare legacy campaign JSON is accepted; bindi wraps it as `bindi.policy.v1` with a `boltz` block automatically.

Reference campaign: [campaigns/pdl1.json](campaigns/pdl1.json).

**Clinical utility bias:** prefer fewer, higher-confidence designs (`top_n` aligned with `budget`), explicit `rank_on` metrics that reflect interface quality (e.g. `design_to_target_iptm`, `min_design_to_target_pae`), and a clear `query` linking the design goal to a therapeutic or mechanistic hypothesis. Avoid inflating `num_designs` without a stated experimental capacity.

Fetch mmCIF targets referenced by the design spec before GPU runs: `campaigns/fetch_targets.sh`.

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
# Optional: bindi curate ... --boltz-rank  # re-run BoltzGen filtering
```

Set `BOLTZGEN_CMD` if the executable is not named `boltzgen`.

**Worker container** (installs BoltzGen via pip; GPU host required):

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

Default container command is `bindi recruit` (descriptor only).

### 4. Deliver the gallery

On success, treat **`gallery/`** (or the path in `policy.gallery`) as the primary artifact:

```text
gallery/
  index.json      # bindi.gallery.v1 manifest: entries, scores, structure paths,
                  #   plus claim, provenance, ensemble, benchmark, tooling (see below)
  ranked.csv      # tabular shortlist (BoltzGen metrics + rank)
  structures/     # mmCIF copies when found under the campaign folder
```

**Manifest fields to surface:** the output is a **relative ranking within this campaign only** (`claim`) — never an absolute affinity or kcal/mol number, and never a safety claim. `rank` is a **pass signal, not potency** (`rank_meaning`): Boltz-2 confidence metrics reflect predicted complex quality, not measured affinity. The design step is **reproducible given a seed, not deterministic** (`reproducibility`: `seed`, `trajectories`). Every gallery carries five fixed `disclosures` (testing only / order from the published trial, not computed here / prediction present but unverified / identity and sign-off gates specified but unbuilt / no hazard screen applied), and is marked `release: false` with a `not_a_release` reason. `provenance` marks every design an in-silico hypothesis until confirmed experimentally. `benchmark` is a cited record whose honest status is `unchecked` — no correlation is fabricated. `tooling` lists the deployable-path tools, checked against `src/bindi/data/tool_tiers.toml`; `evaluation_only` tools (FEP+, PyRosetta, PBEE) must never be a dependency in a lab-facing path.

**The multiverse ensemble is OFF by default** and unvalidated: by default nothing about it is written to the manifest and it does not reorder designs. Enable it only deliberately via `curation.ensemble: true` or `use_ensemble=True`; it then adds the `ensemble` block (`universes`, `disagreement_summary`, `triage`, per-design stats) and `provenance.ensemble_basis`.

Read `index.json` for programmatic handoff. Summarize for the user:

- Target, modality, and `query`
- Top designs by `rank` with key scores from `entries[].scores`
- Paths to structures and sequences (`Binder_Sequence` when present)
- Campaign folder (`manifest.campaign`) for traceability

If `generate` failed, do not claim a curated shortlist; surface BoltzGen stderr/exit code and whether partial outputs exist under `project_folder`.

## Policy fields (quick reference)

```json
{
  "schema": "bindi.policy.v1",
  "query": "<design intent>",
  "boltz": {
    "design_spec": "campaigns/pdl1_miniprotein.yaml",
    "protocol": "protein-anything",
    "num_designs": 500,
    "budget": 10,
    "project_folder": "results/<campaign>"
  },
  "curation": {
    "rank_on": ["design_to_target_iptm"],
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

- **Minimum surface area:** do not fork BoltzGen inside bindi; pass through the `boltzgen` CLI and design-spec YAML.
- **Idempotent curation:** `curate` can rerun on the same `project_folder` to refresh the gallery after new designs land.
- **No silent success:** non-zero `boltzgen run` exit codes raise; report them to the user.
- **LoC efficiency:** extend behavior via policy JSON and BoltzGen options before adding Python.

## Verification checklist

Before marking the task done ([BAR.md](BAR.md) records what a green covers and what it does not):

- [ ] `campaign` validates as `bindi.policy.v1` (or normalizes from legacy campaign JSON).
- [ ] `bindi run ... --dry-run` succeeds in CI-like environments, or GPU `bindi run` completed on the worker.
- [ ] `gallery/index.json` has `schema: bindi.gallery.v1` and `entries` length ≤ `top_n`.
- [ ] User receives ranked designs with scores and file paths, plus any caveats (dry-run, missing structures, metric choice).

## License note

Bindi is MIT. BoltzGen is a separate dependency with its own license; comply with both when deploying workers. Tool licence tiers live in `src/bindi/data/tool_tiers.toml`: tools marked `evaluation_only` (e.g. FEP+, PyRosetta, PBEE) are commercial or licence-restricted and may be used for evaluation only, never as a dependency in a deployable path. Add new tools by editing that data file.
