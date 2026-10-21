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

You are recruiting **bindi**, a worker-deployable resource that designs de novo protein binders through the **hosted Boltz design API** and returns a **gallery** of ranked, scored candidates for experimental follow-up. bindi self-hosts no published design model: there is no GPU path and no weight download.

Use bindi when the user needs **binder generation and selection** for a protein target, not when they only need structure prediction, docking without design, or unrelated molecular biology tasks.

Human-facing overview: [README.md](README.md). Machine-readable contract: `bindi recruit` → `wetware.resource.v1` JSON (see [src/bindi/resource.py](src/bindi/resource.py)).

## Contract

| Slot | Schema | You provide |
| --- | --- | --- |
| **campaign** (input) | `bindi.policy.v1` | Target, modality, design count, output paths, curation metrics — derived from the user’s natural-language intent |
| **gallery** (output) | `bindi.gallery.v1` | Directory with `index.json`, `ranked.csv`, and `structures/` |

Capabilities:

- **generate** — build the design request, price it against the hosted API, and submit it **only** when handed an explicit human approval record (`--approve`). Without that it estimates and stops; running the command is not consent to spend.
- **curate** — rank designs and materialize the gallery from an existing campaign output directory (`project_folder`).
- **job** — `bindi job <job_id>` for run status, `--results` for generated designs. Free and read-only.

Prefer **`bindi run`** unless generation or curation must run separately (e.g. re-curate after manual QC).

## Recruitment

Before scheduling work on a worker, confirm the descriptor matches expectations:

```bash
bindi recruit
```

Recruit bindi when:

1. The task explicitly asks for **de novo binder design** or a **shortlist of binders** against a target.
2. The target **sequences** exist or can be resolved. The hosted API takes sequences, not a target name and not a design-spec YAML; bindi refuses to guess one.
3. `BOLTZ_API_KEY` is available to the worker, or you will use **`--dry-run`** for pipeline validation only.
4. A human has approved the spend, or you accept that `generate` will stop at the estimate.

## Workflow

### 1. Interpret intent → campaign policy

Translate the user query into a **`bindi.policy.v1`** document:

- **`query`** — one-sentence design intent (audit trail for humans).
- **`boltz`** — design-request fields: `target_entities` (list of `{chain_ids, value}` with literal sequences), optional `epitope_residues` (`{chain: [0-based index]}`), `binder` (`modality`, `chain_ids`, `length` such as `"60..100"`), `num_proteins` (aliases: `num_designs`, `number_of_final_designs`, `budget`), `project_folder`. Legacy `bindcraft` keys are migrated automatically; `design_spec` and `protocol` are gone, since both named inputs to the removed local runner.
- **`curation`** — `rank_on` (metric column names, default `["design_to_target_iptm"]`), `top_n` (shortlist size). The multiverse ensemble is **off by default**; enable it with `ensemble: true` (and optional `universes` scorer columns, defaulting to the higher-better metrics present when at least two exist).
- **`gallery`** — relative directory name for curated output (default `gallery`).

Starter from natural language (always **review and edit** before a production run — the stub ships empty `target_entities` on purpose):

```bash
bindi stub "VHH binders for human PD-L1" --modality VHH --designs 10 > campaign.json
# then fill boltz.target_entities with the target sequence(s) before running
```

Bare legacy campaign JSON is accepted; bindi wraps it as `bindi.policy.v1` with a `boltz` block automatically.

Reference campaign: [campaigns/pdl1.json](campaigns/pdl1.json).

**Clinical utility bias:** prefer fewer, higher-confidence designs (`top_n` aligned with `budget`), explicit `rank_on` metrics that reflect interface quality (e.g. `design_to_target_iptm`, `min_design_to_target_pae`), and a clear `query` linking the design goal to a therapeutic or mechanistic hypothesis. Avoid inflating `num_designs` without a stated experimental capacity.

Resolve target sequences from their authority (for example UniProt) and record where they came from; `campaigns/pdl1.json` carries its provenance in `_target_provenance`.

### 2. Validate locally (no API call, no spend)

```bash
uv pip install -e .
pytest
bindi run campaign.json --dry-run
```

`--dry-run` writes fixture ranked tables under `project_folder` and still builds a real gallery layout, touching no endpoint. Use this to verify paths, `top_n`, and manifest shape before spending anything.

### 3. Execute

**Full pipeline (typical):**

```bash
bindi run campaign.json --workdir /work
```

**Split steps:**

```bash
bindi generate campaign.json --workdir /work            # estimates, submits nothing
bindi generate campaign.json --workdir /work \
  --approve "<who approved this spend, and when>"       # THIS SPENDS MONEY
bindi job <job_id>                                      # status
bindi job <job_id> --results                            # generated designs
bindi curate campaign.json --workdir /work
```

Set `BOLTZ_API_KEY` in the environment. It is sent as the `x-api-key` header and is never logged, echoed, or written to an artifact. Design runs are asynchronous: `bindi run` returns with curation `not_run` until results exist. Pull artifacts promptly — their URLs are short-lived and run data is deleted after the workspace retention window.

**Worker container** (no GPU, no model weights):

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run -e BOLTZ_API_KEY -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

A rebuild no longer reproduces the pinned `7e8762ae` digest: that image predates the removal of the self-hosted layer, so a rebuilt image is a new content address and must be recorded as one.

Default container command is `bindi recruit` (descriptor only).

### 4. Deliver the gallery

On success, treat **`gallery/`** (or the path in `policy.gallery`) as the primary artifact:

```text
gallery/
  index.json      # bindi.gallery.v1 manifest: entries, scores, structure paths,
                  #   plus claim, provenance, ensemble, benchmark, tooling (see below)
  ranked.csv      # tabular shortlist (Boltz metrics + rank)
  structures/     # mmCIF copies when found under the campaign folder
```

**Manifest fields to surface:** the output is a **relative ranking within this campaign only** (`claim`) — never an absolute affinity or kcal/mol number, and never a safety claim. `rank` is a **pass signal, not potency** (`rank_meaning`): Boltz-2 confidence metrics reflect predicted complex quality, not measured affinity. The design step is **reproducible given a seed, not deterministic** (`reproducibility`: `seed`, `trajectories`). Every gallery carries five fixed `disclosures` (testing only / order from the published trial, not computed here / prediction present but unverified / identity and sign-off gates specified but unbuilt / no hazard screen applied), and is marked `release: false` with a `not_a_release` reason. `provenance` marks every design an in-silico hypothesis until confirmed experimentally. `benchmark` is a cited record whose honest status is `unchecked` — no correlation is fabricated. `tooling` lists the deployable-path tools, checked against `src/bindi/data/tool_tiers.toml`; `evaluation_only` tools (FEP+, PyRosetta, PBEE) must never be a dependency in a lab-facing path.

**The multiverse ensemble is OFF by default** and unvalidated: by default nothing about it is written to the manifest and it does not reorder designs. Enable it only deliberately via `curation.ensemble: true` or `use_ensemble=True`; it then adds the `ensemble` block (`universes`, `disagreement_summary`, `triage`, per-design stats) and `provenance.ensemble_basis`.

Read `index.json` for programmatic handoff. Summarize for the user:

- Target, modality, and `query`
- Top designs by `rank` with key scores from `entries[].scores`
- Paths to structures and sequences (`Binder_Sequence` when present)
- Campaign folder (`manifest.campaign`) for traceability

If `generate` failed, do not claim a curated shortlist; surface the API's own error text (status, `code`, `message`) verbatim and whether partial outputs exist under `project_folder`. A validation error is never retried and never silently worked around.

## Policy fields (quick reference)

```json
{
  "schema": "bindi.policy.v1",
  "query": "<design intent>",
  "boltz": {
    "target_entities": [{ "chain_ids": ["A"], "value": "<target sequence>" }],
    "epitope_residues": { "A": [0, 1, 2] },
    "binder": { "modality": "custom_protein", "chain_ids": ["D"], "length": "60..100" },
    "num_proteins": 10,
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

- **Minimum surface area:** do not reimplement the design model; send a request to the hosted API. The client is standard-library only (`urllib`), with no HTTP dependency added for it.
- **The legacy request shape is required:** the type-discriminated `{"type": "binder", …}` body is rejected live with `HTTP 400 VALIDATION_ERROR` (verified 2026-10-21 on both the design and estimate-cost endpoints), whatever the upstream docs say. `non_binding_residues` must never appear in a request body.
- **Spend is gated by a human, not by code convenience:** `submit()` is unreachable without an explicit approval argument, and estimating or polling never submits as a side effect.
- **Idempotent curation:** `curate` can rerun on the same `project_folder` to refresh the gallery after new designs land.
- **No silent success:** any non-2xx raises with the API's own error text; a 4xx is never retried and there is no local fallback to fall back to.
- **LoC efficiency:** extend behavior via policy JSON and request fields before adding Python.

## Verification checklist

Before marking the task done ([BAR.md](BAR.md) records what a green covers and what it does not):

- [ ] `campaign` validates as `bindi.policy.v1` (or normalizes from legacy campaign JSON).
- [ ] `bindi run ... --dry-run` succeeds in CI-like environments, or an approved hosted run completed and its results were pulled before the retention window closed.
- [ ] `gallery/index.json` has `schema: bindi.gallery.v1` and `entries` length ≤ `top_n`.
- [ ] User receives ranked designs with scores and file paths, plus any caveats (dry-run, missing structures, metric choice).

## License note

Bindi is MIT. The hosted Boltz API is a separate paid service with its own terms (https://boltz.com/terms); bindi redistributes no model weights. Tool licence tiers live in `src/bindi/data/tool_tiers.toml`: tools marked `evaluation_only` (e.g. FEP+, PyRosetta, PBEE) are commercial or licence-restricted and may be used for evaluation only, never as a dependency in a deployable path. Add new tools by editing that data file.
