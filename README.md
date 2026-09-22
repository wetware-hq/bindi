# Bindi

**Resource:** de novo protein binder design and ranked shortlisting for a specified therapeutic target.  
**Contract:** `wetware.resource.v1` (emit with `bindi recruit`). **Operator guide:** [AGENT.md](AGENT.md).

---

## Background

Checkpoint inhibitors, targeted biologics, and related modalities often hinge on molecules that bind a disease-relevant protein with useful affinity and specificity. Empirical screening alone is slow and costly. Structure-guided *de novo* design can propose binders *in silico* before synthesis, but uncollated pipeline output is hard to interpret in translational or clinical planning. Bindi closes that gap by standardizing what goes in (a campaign policy) and what comes out (a curated gallery).

## Resource specification

Bindi is a worker-recruitable resource with two capabilities—**generate** and **curate**—composed by **`bindi run`** unless generation or curation must run separately (for example re-curation after manual QC).

| Slot | Direction | Type | Schema | Semantics |
| --- | --- | --- | --- | --- |
| **campaign** | input | policy | `bindi.policy.v1` | Target, modality, design count, BindCraft2 project paths, and curation rules; populated at runtime from natural-language intent (and reviewed before GPU work). |
| **gallery** | output | directory | `bindi.gallery.v1` | Ranked, scored shortlist plus manifest and structures for experimental handoff. |

**Recruitment.** Frontier agents discover the machine-readable descriptor via `bindi recruit` (see [src/bindi/resource.py](src/bindi/resource.py)). **Packaging.** `containers/Dockerfile` builds a GPU-worker image (`entrypoint`: `bindi`; base expects BindCraft2 as `bindcraft:local`).

## Objective

Given a **campaign**, bindi returns a **gallery**: a bounded set of designed binders ordered by campaign-defined metrics (default ranking emphasizes interface predicted binding energy, *i_pDAE*), each with computed scores and, when available, three-dimensional models. The intent is experimental triage—not clinical deployment without validation.

## Methods

Bindi orchestrates [BindCraft2](https://github.com/PacesaLab/BindCraft2) in two stages aligned with its capabilities.

**Generate (`bindi generate`).** Invokes `bindcraft design` for the `bindcraft` block inside the campaign policy—target identity, modality (miniprotein, VHH, peptide, or other BindCraft2-supported class), and `number_of_final_designs`, plus any BC2-specific options (hotspots, chains, filters). Requires `bindcraft` on `PATH` or `BINDCRAFT_CMD`, and GPU-backed workers for production runs.

**Curate (`bindi curate`).** Reads BindCraft2 output under `project_folder`, applies `curation.rank_on` and `curation.top_n`, and materializes the gallery directory named in the policy (default `gallery`).

Policies use schema **`bindi.policy.v1`**: a wrapper around BindCraft2 JSON plus curation metadata. Bare BindCraft2 campaign JSON is accepted and normalized automatically. Illustrative campaign: `campaigns/pdl1.json` (human PD-L1). Agents may seed policy from intent with `bindi stub` (output must be reviewed before production).

**Validation without GPU.** `bindi run <policy> --dry-run` exercises curation on fixtures; it does not substitute for structural or binding assays.

## Deliverables

The **gallery** conforms to **`bindi.gallery.v1`**:

| Artifact | Role |
| --- | --- |
| `index.json` | Manifest: campaign reference, ranking criteria, candidate list |
| `ranked.csv` | Tabular shortlist with key metrics per design |
| `structures/` | mmCIF models when present in the design run |

**Clinical caveat.** All outputs are *in silico* hypotheses. Affinity, specificity, developability, immunogenicity, and safety require experimental and, where applicable, regulatory evidence before therapeutic use.

## Deployment

Install from source (`pip install -e .` or `uv pip install -e .`). Run the full pipeline with `bindi run campaigns/pdl1.json`, or build and run the container:

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

Command-level workflow, policy field reference, Python API, and recruitment checklists are specified in [AGENT.md](AGENT.md).

## License

MIT for bindi. [BindCraft2](https://github.com/PacesaLab/BindCraft2) is a separate dependency with its own license terms.
