# Bindi

Bindi helps teams **design and shortlist protein binders** against a target—for example, molecules that might block PD-L1, stick to a tumor antigen, or modulate a pathway of interest.

You describe the biological goal in plain language; a computational workflow runs [BindCraft2](https://github.com/PacesaLab/BindCraft2) to propose candidate binders and returns a **gallery**: a ranked set of designs with scores and structures where available, ready for lab review or downstream planning.

## What you get

- **Ranked candidates** — designs ordered by interface and quality metrics (for example predicted interface confidence).
- **Sequences and structures** — amino-acid sequences and structural files when the run produces them.
- **Traceability** — each gallery links back to the campaign folder and scoring table used to build it.

## Example use case

**Goal:** De novo miniprotein binders against human PD-L1 to interfere with the PD-1 interaction.

A collaborator or agent configures a **campaign** (target, binder type, how many designs to keep). Bindi runs design, then curation, and writes a **gallery** directory with the top designs.

## Gallery layout

```text
gallery/
  index.json      summary of ranked designs and scores
  ranked.csv      spreadsheet-friendly shortlist
  structures/     3D structure files (mmCIF), when available
```

## For computational workflows

Installation, command reference, containers, and the Python API live in this repository for engineers and **frontier AI** that recruit bindi as a worker resource.

**Frontier agents:** use [AGENT.md](AGENT.md) — recruitment contract, policy authoring, and gallery handoff. Clinicians and program leads can stay on this page.

## License

MIT. BindCraft2 is a separate tool with its own license; see the [BindCraft2 repository](https://github.com/PacesaLab/BindCraft2).
