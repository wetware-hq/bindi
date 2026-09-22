# Bindi

Bindi designs and prioritizes de novo protein binders against a specified therapeutic target. Frontier agents recruit it with a machine-readable descriptor (`bindi recruit`, schema `wetware.resource.v1`); translational teams receive a ranked **gallery** for experimental triage. Command reference, policy fields, and recruitment checklists are in [AGENT.md](AGENT.md).

## Purpose

Checkpoint inhibitors, targeted biologics, and related modalities need molecules that bind a disease-relevant protein with useful affinity and specificity. Screening alone is slow and costly, and raw design pipelines produce more candidates than a team can interpret. Bindi standardizes a **campaign** policy as input and a curated **gallery** as output so automation and human review share the same artifacts.

## Contract

Bindi exposes two capabilities, **generate** and **curate**, which `bindi run` runs in sequence unless you must repeat one stage alone (for example, re-curation after manual QC).

| Slot | Direction | Type | Schema |
| --- | --- | --- | --- |
| **campaign** | input | policy | `bindi.policy.v1` |
| **gallery** | output | directory | `bindi.gallery.v1` |

The **campaign** specifies the target, binder modality, number of designs, BindCraft2 project paths, and ranking rules. It is usually filled from natural-language intent and should be reviewed before any GPU job. The **gallery** is a ranked, scored shortlist with a manifest and structure files for laboratory follow-up.

The descriptor emitted by `bindi recruit` is defined in [src/bindi/resource.py](src/bindi/resource.py). The repository includes `containers/Dockerfile` to build a worker image whose entrypoint is `bindi` and whose base provides BindCraft2 as `bindcraft:local`.

## Workflow

Bindi orchestrates [BindCraft2](https://github.com/PacesaLab/BindCraft2). **Generate** (`bindi generate`) calls `bindcraft design` using the policy’s `bindcraft` block and requires `bindcraft` on `PATH` (or `BINDCRAFT_CMD`) and a GPU for production runs. **Curate** (`bindi curate`) reads output under `project_folder`, applies `curation.rank_on` and `curation.top_n` (default ranking uses interface predicted binding energy, *i_pDAE*), and writes the gallery path named in the policy.

Campaigns conform to `bindi.policy.v1`, a thin wrapper around BindCraft2 JSON plus curation metadata; bare BindCraft2 campaign files are normalized automatically. The shipped example `campaigns/pdl1.json` targets human PD-L1. `bindi stub` can draft a policy from intent, but that draft must be edited before production. `bindi run <policy> --dry-run` smoke-tests curation on fixtures without a GPU and does not replace binding or structural validation.

## Gallery

Each gallery follows `bindi.gallery.v1` and contains `index.json` (manifest, ranking criteria, and candidate list), `ranked.csv` (tabular shortlist), and `structures/` (mmCIF models when the design run produced them). All results remain in silico hypotheses until affinity, specificity, developability, and safety are confirmed experimentally and, for therapeutics, through the appropriate regulatory path.

## Deployment

Install from source with `pip install -e .` or `uv pip install -e .`, then run `bindi run campaigns/pdl1.json`, or build and run the container:

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

## License

Bindi is released under the MIT license. [BindCraft2](https://github.com/PacesaLab/BindCraft2) is a separate dependency with its own license terms.
