# bindi 🐨

**bindi 🐨** designs and prioritizes de novo protein binders against a specified therapeutic target. Frontier agents recruit **bindi 🐨** with a machine-readable descriptor (`bindi recruit`, schema `wetware.resource.v1`); translational teams receive a ranked **gallery** for experimental triage. Command reference, policy fields, and recruitment checklists are in [AGENT.md](AGENT.md).

## Purpose

Checkpoint inhibitors, targeted biologics, and related modalities need molecules that bind a disease-relevant protein with useful affinity and specificity. Screening alone is slow and costly, and raw design pipelines produce more candidates than a team can interpret. **bindi 🐨** standardizes a **campaign** policy as input and a curated **gallery** as output so automation and human review share the same artifacts.

## Contract

**bindi 🐨** exposes two capabilities, **generate** and **curate**, which `bindi run` runs in sequence unless you must repeat one stage alone (for example, re-curation after manual QC).

| Slot | Direction | Type | Schema |
| --- | --- | --- | --- |
| **campaign** | input | policy | `bindi.policy.v1` |
| **gallery** | output | directory | `bindi.gallery.v1` |

The **campaign** specifies the target, binder modality, BoltzGen design spec, output paths, and ranking rules. It is usually filled from natural-language intent and should be reviewed before any GPU job. The **gallery** is a ranked, scored shortlist with a manifest and structure files for laboratory follow-up.

The descriptor emitted by `bindi recruit` is defined in [src/bindi/resource.py](src/bindi/resource.py). The repository includes `containers/Dockerfile` to build a worker image whose entrypoint is `bindi` and that installs [BoltzGen](https://github.com/HannesStark/boltzgen) (`boltzgen` on `PATH`).

## Workflow

**bindi 🐨** orchestrates [BoltzGen](https://github.com/HannesStark/boltzgen) (Boltz-1 diffusion design, Boltz-2 refolding and confidence). **Generate** (`bindi generate`) calls `boltzgen run` using the policy’s `boltz` block (design-spec YAML, protocol, `num_designs`, `budget`) and requires `boltzgen` on `PATH` (or `BOLTZGEN_CMD`) and a GPU for production runs. **Curate** (`bindi curate`) reads metrics under `project_folder` (typically `final_ranked_designs/all_designs_metrics.csv`), applies `curation.rank_on` and `curation.top_n` (default ranking uses Boltz-2 interface confidence `design_to_target_iptm`; bindi reports a relative ranking, not an energy), and writes the gallery path named in the policy.

Before a production PD-L1 run, fetch campaign structures: `campaigns/fetch_targets.sh`.

Campaigns conform to `bindi.policy.v1`, a thin wrapper around BoltzGen parameters plus curation metadata; legacy `bindcraft` keys and bare BindCraft-style JSON are migrated automatically. The shipped example `campaigns/pdl1.json` targets human PD-L1 via `campaigns/pdl1_miniprotein.yaml`. `bindi stub` can draft a policy from intent, but that draft must be edited before production. `bindi run <policy> --dry-run` smoke-tests curation on fixtures without a GPU and does not replace binding or structural validation.

## Gallery

Each gallery follows `bindi.gallery.v1` and contains `index.json` (manifest, ranking criteria, and candidate list), `ranked.csv` (tabular shortlist), and `structures/` (mmCIF models when the design run produced them).

### Relative ranking only

bindi emits a **relative ranking among the designs in one campaign** and never a predicted absolute affinity or kcal/mol value. This follows the protein-FEP benchmark of Sampson et al. (*J Mol Biol* 436, 168640, 2024; [data](https://github.com/schrodinger/protein-fep-benchmark)): relative binding free energies between close mutants are predictable where single-structure absolute affinity is not. The manifest carries this as an explicit `claim` field, and a test fails if any manifest or score key reads like an absolute affinity. **Ranking is never a safety claim.**

The `rank` column is a **pass signal, not potency.** Boltz-2 confidence metrics (for example `design_to_target_iptm`) reflect predicted complex quality, not measured binding affinity, so the ordering says which designs are more likely to form the intended complex, not which binds hardest. The manifest records this in `rank_meaning`.

The design step is **reproducible given a seed, not deterministic.** The manifest `reproducibility` field records the `seed` and design count where the campaign provides them.

### Manifest fields

The `index.json` manifest adds, beyond the ranked `entries`:

- **`claim`** — the relative-only statement above.
- **`fixture_path_statement`** — when the run used bundled fixtures with no design compute (`bindi run --dry-run`), a plain-language statement that the gallery exercises record-keeping only, not binding chemistry, and that no design has been shown to bind. Omitted when a real design step ran.
- **`structures`** — how mmCIF copies into `structures/` were handled: `copied` count, `status` (`copied` or `not_run`), and when nothing was copied a `reason` (never left implicit).
- **`disclosures`** — five fixed lines that must appear on every gallery: for testing only (not real patients); candidate order is from the published trial, not computed here; the prediction step is present but unverified; identity and sign-off gates are specified but unbuilt; no hazard screen has been applied. A test fails if any of the five is missing.
- **`release` / `not_a_release`** — `release` is always `false`; `not_a_release` states that no hazard screen has been applied, so the release condition has not been met.
- **`rank_meaning`** — records that `rank` is a pass-signal ordering, not affinity or potency.
- **`reproducibility`** — `model` is `reproducible-given-seed` (never "deterministic"), with `seed` and `trajectories` where the campaign provides them.
- **`provenance`** — `status` (`in_silico_hypothesis`) and a plain statement that these are hypotheses until confirmed experimentally (affinity, specificity, developability, safety; for therapeutics, the regulatory path).
- **`benchmark`** — a cited, data-file-driven record (`src/bindi/data/benchmark.toml`) of how the ranking has been checked against *measured* relative affinities. The honest current `status` is **`unchecked`**; no correlation number is ever fabricated.
- **`tooling`** — the deployable-path tools used, each verified `deployable` against the licence tiers.

**Ensemble is off by default.** The multiverse scorer-ensemble ships as code (`src/bindi/ensemble.py`) but is unvalidated (benchmark status `unchecked`), so by default `curate()` writes **no** `ensemble` field — neither a top-level block nor any per-entry key — and does not reorder by it. Enable it explicitly with `curation.ensemble = true` in the policy or `curate(..., use_ensemble=True)`; only then does the `ensemble` block (and its per-design `mean_rank`/`worst_rank`/`worst_universe`/`spread`/`disagreement`/`by_universe`, plus `provenance.ensemble_basis`) appear.

### Licence tiers

`src/bindi/data/tool_tiers.toml` tags each tool `deployable` or `evaluation_only` with a licence name and citation URL. Commercial or licence-restricted tools (FEP+, PyRosetta, PBEE) are `evaluation_only` and must never be a dependency in a path handed to a lab; curation asserts this. New tools are added by editing the data file, not core code.

## Clinical case seam (end-to-end immunology case)

The design target used to be a literal string in the campaign policy (`bindcraft.target`, e.g. `hPDL1`). `src/bindi/case.py` adds one source of truth above that literal: a **synthetic clinical-immunology case record** written by the clinical half, carrying the HLA type, the mutant peptide and its wild-type counterpart. The policy literal stays as the fixture fallback, so every existing campaign behaves exactly as before.

```bash
bindi case-run fixtures/immunology_case_PLACEHOLDER.json --workdir /tmp/case1
```

One command, one JSONL trace, one case: `case record -> target extraction -> binder step -> wild-type margin hook -> human-reference gate -> ward hand-off -> summary`. The record schema is `schemas/immunology_case.v1.schema.json`; the case is chosen by `--case`/`case-run`'s positional argument, `policy.case`, or `BINDI_CASE_FILE`, in that order.

**What is real:** the record is read and validated (required fields, enums, `SYNTH-` ids, peptide and HLA patterns), the target is extracted from it, the fixture-backed binder step and gallery curation run, and every stage is traced with a status and an exit code.

**What is a placeholder, and says so in its own trace line:**

| Stage | Status today | Why |
|---|---|---|
| `design` | `fixture_backed` | No BindCraft, no Modal, no GPU. The ranked table is `fixtures/sample_ranked.csv`; the sequences are invented and unrelated to the case peptide. |
| `margin_wild_type` | `not_computed` | Sequence-level scorers (`--sequence-design`, `class_bucket_v1`, `blosum_position_v2`) may state a margin for testing but **do not establish** the immunology threshold. Structural evaluation uses **BindCraft on Modal** (`bindi structural-margin-repeat-noise`, scorer `bindcraft_modal_v1`, `MODAL_TOKEN` required for live GPU). |
| `human_reference_gate` | `unbuilt` | bindi ships no human reference proteome. Supplied one (`--human-reference-fasta`), it runs an **exact k-mer containment screen** — not an alignment, not a validated homology search. It never returns `pass` without looking. |
| `ward_handoff` | `not_invoked` | The hand-off record is written to `ward_handoff.json`, but ward runs fifteen fixed scenarios and exposes no entry point for an arbitrary case, so **the ward gates and the letter did not run**. |
| `letter` | `not_produced` | Letters come from ward's letters endware, which this run does not reach. |

**The design hold is enforced in code.** A real design run needs a signed case record, a `pass` from the human-reference gate, a stated wild-type margin, and an **established structural margin threshold** (PAIR-EASY positive control on `bindcraft_modal_v1`). `case.design_hold_reasons` names each missing condition. `bindi case-run --design` on an unsigned record refuses and exits `2` without calling BindCraft; `generate(..., dry_run=False)` on a policy naming a case record raises `DesignHold`. `fixtures/immunology_case_PLACEHOLDER.json` is an engineering placeholder written to exercise the seam — it is **not** a signed patient, and a test fails if any case fixture in this repo claims to be signed.

Exit codes: `0` fixture-backed run completed, `1` case record invalid, `2` design refused by the hold, `3` a gate returned `fail`.

## Bar

`pip install -e .` then `pytest` (101 passed; 11 skipped without `mmseqs2` on PATH) is bindi's bar, with `bindi run campaigns/pdl1.json --dry-run` as a no-GPU curation smoke-test. What a green covers and what it does not — so a green here is not read as the same assurance as a green in a sibling repo — is [BAR.md](BAR.md).

## Deployment

Install from source with `pip install -e .` or `uv pip install -e .`, install BoltzGen (`pip install boltzgen`) on a GPU host, fetch campaign targets (`campaigns/fetch_targets.sh`), then run `bindi run campaigns/pdl1.json`, or build and run the container:

```bash
docker build -f containers/Dockerfile -t bindi:local .
docker run --gpus all -v "$PWD:/work" -w /work bindi:local run campaigns/pdl1.json
```

BoltzGen downloads model weights on first use (~6 GB for Boltz checkpoints; additional cache for inverse folding / ESMFold2 depending on protocol). Set `BOLTZ_CACHE` or BoltzGen’s `--cache` to a persistent volume in production.

## License

**bindi 🐨** is released under the MIT license. [BoltzGen](https://github.com/HannesStark/boltzgen) is a separate dependency with its own license terms.

## References

Each entry is tied to the one claim in this README that rests on it; nothing is listed for adjacency. Peer-reviewed primary literature first, then sources at a different weight (preprints and tool repositories), then the claims that have no peer-reviewed support yet. An entry with no DOI, PMID or arXiv id is not a citation and does not belong here.

### Peer-reviewed

- **Sampson, J. M. et al.** Robust prediction of relative binding energies for protein–protein complex mutations using free energy perturbation calculations. *J. Mol. Biol.* 436, 168640 (2024). DOI: 10.1016/j.jmb.2024.168640 (PMID: 38844044). — The relative-ranking-only claim: relative binding free energies between close mutants are predictable where single-structure absolute affinity is not, so bindi emits an ordering and never a kcal/mol value.
- **Liu, H. et al.** PPB-Affinity: Protein–Protein Binding Affinity dataset for AI-based protein drug discovery. *Scientific Data* (2024). DOI: 10.1038/s41597-024-03997-4. — The `benchmark` field: the *measured* relative affinities that `src/bindi/data/benchmark.toml` is to be checked against come from a dataset of this kind; the record's honest `status` is `unchecked` until that check runs.
- **Keskin, D. B. et al.** Neoantigen vaccine generates intratumoral T cell responses in phase Ib glioblastoma trial. *Nature* 565, 234–239 (2019). DOI: 10.1038/s41586-018-0792-9 (PMID: 30568305). — The disclosure that a gallery's candidate order can be "from the published trial, not computed here": the phase Ib glioblastoma neoantigen trial is that published source.

### Preprints and tool repositories (not peer-reviewed)

- **Stärk, H. et al.** BoltzGen: Toward Universal Binder Design. *bioRxiv* (2025). DOI: 10.1101/2025.11.20.689494. — The design step: **bindi 🐨** orchestrates [BoltzGen](https://github.com/HannesStark/boltzgen) to generate de novo binders; ranking uses Boltz-2 confidence metrics such as `design_to_target_iptm`, not absolute affinity.
- **AbBiBench** (Antibody Binding Benchmarking). arXiv:2506.04235 (2025). — Cited for **benchmark design only**: treating the antibody–antigen complex as the scored unit informs how the multiverse scorer-ensemble would be evaluated. It is not evidence that the ensemble works, which is why the ensemble ships off by default.

### Unsupported claims (no peer-reviewed support)

- The multiverse scorer-ensemble correlation against a held-out, training-set-filtered measured-affinity set is **unrun**; the `benchmark` record reads **`unchecked`** and no correlation number is ever reported.
