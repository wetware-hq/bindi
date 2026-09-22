# Bindi

**Computational design and prioritization of de novo protein binders against a specified therapeutic target.**

## Background

Many clinical strategies—immune checkpoint blockade, targeted biologics, and emerging cell therapies—depend on molecules that bind a disease-relevant protein with high affinity and specificity. Identifying such binders by screening alone is slow and expensive. Structure-guided *de novo* design can propose candidate binders in silico before any wet-lab synthesis, but raw design pipelines produce large, uneven lists that are difficult to interpret at the bedside.

## Objective

Bindi is a deployable computational resource that accepts a **campaign**—a concise specification of the protein target, binder modality (for example miniprotein, peptide, or VHH), and how many candidates to retain—and returns a **gallery**: a small, ranked set of designed binders with supporting scores and structural models suitable for downstream experimental triage.

## Methods

Bindi orchestrates [BindCraft2](https://github.com/PacesaLab/BindCraft2), a published binder-design engine, in two stages.

**Generation.** Given a campaign policy, Bindi invokes BindCraft2 to propose binders against the named target (for example human PD-L1 in the bundled illustration campaign). Design parameters—target identity, modality, and the number of final designs—are filled at runtime, including from natural-language intent translated by an assisting agent.

**Curation.** Bindi reads BindCraft2’s ranked design table, applies campaign-specific ranking rules (by default emphasizing interface predicted binding energy, *i_pDAE*, among other structural metrics), and copies the top candidates into a standardized gallery folder. Each entry retains computed scores so medicinal chemistry and translational teams can compare candidates without opening the full design archive.

Campaign policies conform to `bindi.policy.v1` (a thin wrapper around BindCraft2 settings plus curation metadata). An example campaign for PD-L1 is provided at `campaigns/pdl1.json`.

## Deliverables

The **gallery** (`bindi.gallery.v1`) is a directory intended for handoff to experimental groups:

- **index.json** — manifest describing the campaign, ranking criteria, and listed candidates  
- **ranked.csv** — tabular shortlist with key metrics per design  
- **structures/** — three-dimensional models (mmCIF), when available from the design run  

Bindi does not replace validation. All outputs are *in silico* hypotheses; binding, specificity, developability, and safety must be established experimentally before any clinical use.

## Deployment

Bindi is packaged as a container built from this repository (`containers/Dockerfile`) so the same workflow can run on GPU-equipped workers in the cloud. Generation requires BindCraft2 (and suitable hardware); curation and dry-run smoke tests can run without a GPU for pipeline checks.

## For computational collaborators

Frontier agents and pipeline operators should use [AGENT.md](AGENT.md) for recruitment (`bindi recruit`), policy authoring, command reference, and the Python API. Bindi is released under the MIT license; BindCraft2 remains a separate dependency with its own license terms.
