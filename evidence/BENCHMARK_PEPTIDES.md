# Benchmark peptide pairs vs case neoantigen (peptide drift resolution)

## Finding

The committed sequence-design repeat-noise evidence (`evidence/sequence_design_repeat_noise.v1.json` and `.v2.json`) was generated from **`evidence/immunology_case_signed_benchmark.v1.json`** (`SYNTH-BENCH-001`), scoring only the **`benchmark_pairs`** entries `PAIR-HARD` and `PAIR-EASY` (peptides `YLGVFPGDW`/`YLGMFPGDW` and `YLGEFPGDW`/`YLGKFPGDW`). That file’s top-level `neoantigen` matches the PAIR-EASY bench peptides, not a distinct clinical case pair.

Issue #10’s `YLGMFPGDW`/`YLGKFPGDW` pair conflates PAIR-HARD wild-type (`YLGMFPGDW`) with PAIR-EASY wild-type (`YLGKFPGDW`); the v2 evidence file uses the correct per-pair sequences above.

The signed immunology record **WET-IMM-001** (`evidence/immunology_case_WET_IMM_001.v1.json`, `case_id` `SYNTH-WET-IMM-001`) carries the case neoantigen **`YLMSYNGVV` / `YLDSYNGVV`**, which was **not** included in the sequence-design noise runs. That is **drift off the signed case record**, not a deliberate substitute for WET-IMM-001: the bench set is deliberate and named (`benchmark_set_id`: `BENCH-PAIR-SET-001`), but margin evidence for the immunology case must also be run on the case peptides.

## What to run

| Pair role | Peptides (mutant / wild-type) | Purpose |
| --- | --- | --- |
| Case neoantigen (`CASE-NEOANTIGEN`) | `YLMSYNGVV` / `YLDSYNGVV` | Signed WET-IMM-001 margin target |
| `PAIR-EASY` (bench) | `YLGEFPGDW` / `YLGKFPGDW` | Positive control for threshold establishment |
| `PAIR-HARD` (bench) | `YLGVFPGDW` / `YLGMFPGDW` | Hard bench; spread only |

Structural margin repeat-noise has no runner in this repo: the self-hosted BindCraft-on-Modal scorer and its `bindi structural-margin-repeat-noise` command were removed on 2026-10-21, so no structural margin evidence can be generated here. Sequence scorers (`class_bucket_v1`, `blosum_position_v2`) remain selectable for reproduction but **do not** establish the margin threshold.
