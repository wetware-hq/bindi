# bindi — the bar

What a green means in **bindi 🐨**, and what it does not. A green here is not the same assurance as a green in a sibling repo; read this before quoting one.

**For testing only, not for use with real patients.** A gallery is an in-silico hypothesis, not a validated result.

## The bar

```bash
pip install -e .     # or: uv pip install -e .
pytest               # 101 passed (11 skipped without mmseqs2 on PATH)
bindi run campaigns/pdl1.json --dry-run   # curation smoke-test on fixtures, no GPU
```

`pytest` green is bindi's bar. The dry-run exercises the curation path and gallery layout without a GPU; it does not replace binding or structural validation.

## What a green covers

- **Manifest disclosures** (`test_five_disclosures_release_and_claim`) — the five fixed disclosure lines, `release: false`, and the `not_a_release` reason are present; a test fails if any of the five is missing.
- **Relative-only claim** (`test_claim_field_present_and_relative`, `test_no_key_looks_like_absolute_affinity`, `test_absolute_affinity_detector_*`) — the `claim` field is present and relative, and no manifest or score key reads like an absolute affinity or kcal/mol value.
- **Rank is a pass signal** (`test_rank_labelled_as_pass_signal_not_potency`) — `rank_meaning` records `rank` as a pass-signal ordering, never potency or affinity.
- **Reproducible given a seed, not "deterministic"** (`test_reproducibility_not_deterministic`) — `reproducibility.model` is `reproducible-given-seed`, with `seed` and `trajectories` where the campaign provides them; the word "deterministic" is never used for the design step.
- **Benchmark honesty** (`test_benchmark_record_present_and_unchecked`) — the `benchmark` record is present and cited, and its status is `unchecked`; no correlation number is fabricated.
- **Licence tiers** (`test_deployable_path_rejects_evaluation_only_tools`, `test_deployable_path_accepts_deployable_tools`, `test_tool_tiers_are_cited`, `test_curate_manifest_tooling_is_deployable`) — `evaluation_only` tools (FEP+, PyRosetta, PBEE) are rejected from any lab-facing path; deployable tools pass; every tier is cited.
- **Provenance** (`test_provenance_present`) — every design is marked an in-silico hypothesis until confirmed experimentally.
- **Fixture-path disclosure** (`test_dry_run_pipeline_builds_gallery`, `test_fixture_path_statement_required_when_fixture_path_used`) — dry-run / fixture-path galleries carry a non-empty `fixture_path_statement` in plain language; a test fails if it is missing or empty when the fixture path was used.
- **Structures recorded** (`test_structures_not_run_recorded_when_none_copied`, `test_structures_copied_status_when_mm_cif_present`) — `structures.copied` is explicit; when zero, `status` is `not_run` with a `reason`, not silence.
- **Ensemble off by default** (`test_default_run_emits_no_ensemble_field_anywhere`, `test_default_run_does_not_reorder_by_ensemble`, `test_enabled_path_emits_full_ensemble`, `test_enabled_via_policy_flag`, `test_enabled_path_reorders_by_consensus`) — by default no `ensemble` field is written and no reorder happens; enabling it explicitly restores the full block, and both paths are tested.
- **Ensemble internals** (`tests/test_ensemble.py`) — ranking, disagreement summary, triage, and not-comparable handling (fewer than two universes, missing/blank/non-numeric metric).
- **Clinical case seam** (`tests/test_case_seam.py`) — a case record validates against the documented fields (required fields, enums, `SYNTH-` ids, peptide and HLA patterns, mutant ≠ wild-type, same length, restriction allele present in `class_i`); the target is extracted from the record; the fixture fallback is intact; the hold conditions (signed record, human-reference `pass`, stated margin, **established structural margin threshold**) are enforced in code, including a `DesignHold` from `generate(dry_run=False)` before BoltzGen is called; and one `case-run` writes one JSONL trace whose ten stages carry their honest statuses.
- **Structural margin (BindCraft-on-Modal, stubbed)** (`tests/test_structural_margin.py`) — Modal token missing fails closed; stub repeat-noise records case neoantigen `YLMSYNGVV`/`YLDSYNGVV` plus bench PAIR-EASY/HARD with seed/trajectory/image/GPU/driver provenance; sequence scorers never clear the margin threshold.
- **Policy & pipeline** (`test_bare_bindcraft_json_normalizes`, `test_legacy_bindcraft_key_migrates_to_boltz`, `test_resolve_paths`, `test_dry_run_pipeline_builds_gallery`, `test_curate_existing_campaign`) — legacy campaign JSON normalizes to `boltz`, paths resolve, and the dry-run and re-curate paths build a `bindi.gallery.v1` gallery.

## What a green does NOT cover

- **The ensemble heuristic is unvalidated.** It ships **off by default** and is a ranking heuristic, never a safety claim; its benchmark status is `unchecked`. A green does not say it ranks correctly.
- **Binding, structural, or experimental validity.** `rank` is a pass signal from Boltz-2 confidence metrics, not measured affinity or potency. A gallery is a hypothesis until confirmed in the lab.
- **No hazard screen** has been applied to any gallery; `release` is always `false` and `not_a_release` names the unmet condition.
- **Identity and sign-off gates are specified but unbuilt.** The disclosure lines say so; a green does not mean they exist.
- **The end-to-end immunology case.** `case-run` is fixture-backed: `design` is `fixture_backed`, `margin_wild_type` is `not_computed`, `human_reference_gate` is `unbuilt`, and `ward_handoff`/`letter` are `not_invoked`/`not_produced`. A green says those statuses were reported honestly, not that any of those steps happened.
- **The human-reference gate.** When a reference FASTA is supplied the gate is exact k-mer containment only: no alignment, no substitution matrix, no E-value, no immunogenicity assessment. A `pass` does not mean a design is not cross-reactive.
- **Not a release.** The gallery manifest is marked `not_a_release`; a green is a code check on invented fixtures, not evidence of clinical safety.

## Name guard

Not feasible as a hard test here, and not faked. Unlike the sibling harness repos, bindi has no registry of named check ids (no `WG-`/`PM-` scheme); its checks are the pytest functions listed above, named by the test runner itself. There is no separate "check name" a run could use that this file could diverge from, so a name-guard test would assert nothing. Keep this list in step with `tests/` by review instead.
