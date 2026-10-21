# Proposal: ipSAE as the structural margin scorer

**To:** the Chief Scientist
**From:** bindi engineering
**Date:** 2026-10-21 (Australia/Sydney)
**Status:** PROPOSAL. Nothing here is in force. No threshold is named in code, no margin
number has been computed, nothing has been submitted to the hosted Boltz API and nothing
has been spent.
**Scorer id:** `ipsae_boltz_cpu_v1` · **Evidence level:**
`interface_pae_from_boltz_prediction_not_a_binding_measurement`
**Decision requested:** whether to fund the spread measurement described in §3, and on what
terms a threshold derived from it would be signed.

---

## 0. What has already been done, and what has not

Done, and merged as plumbing only: `src/bindi/ipsae_margin.py` registers ipSAE with
`src/bindi/margin.py` as a **candidate** structural scorer. The adapter exists, the evidence
level exists, and a margin *could* be computed from it if a threshold were signed.

Not done, and deliberately not doable by code: clearing the margin stage.
`margin.SIGNED_STRUCTURAL_SCORER_IDS` is an empty `frozenset`, `margin._threshold_fields`
has no branch that returns `established`, and registering this candidate did not change
either. The immunology margin stage still refuses by construction, with its wording
unchanged, and a test pins that. Admitting a scorer is a signature, not a commit.

## 1. What ipSAE measures

ipSAE is the Dunbrack-lab interface score over a model's **predicted aligned error** matrix.
`src/bindi/ipsae.py` implements `ipsae_d0res` on CPU with numpy, reading the Boltz run's own
`pae.npz` and `*_predicted.cif`; chain assignment and pLDDT come from the ModelCIF
`_atom_site` loop. For each ordered chain pair it computes a pTM-like quantity restricted to
inter-chain residue pairs under a PAE cutoff (15 Å by default), and bindi reports
`min(A→B, B→A)` where the reference implementation reports the maximum — the conservative
direction, chosen for binder curation.

Three properties make it the natural candidate now that BindCraft is out of the design path:

1. **It is structure-derived**, not sequence-level. The room's standing decision (`USE
   structure`) is what disqualifies `class_bucket_v1` and `blosum_position_v2`.
2. **It needs no GPU and runs no model.** It reads artifacts a hosted Boltz design run has
   already produced. Nothing is self-hosted, so it does not touch the Boltz self-hosting
   prohibition.
3. **It is free at the margin.** The predictions it reads are paid for once, by the design
   run; scoring them again costs CPU seconds.

## 2. What ipSAE does not measure

- **Not binding.** It is a model's confidence in its own interface geometry. It is not an
  affinity, a Kd/Ki/IC50, a kcal/mol value, or any measured quantity. The evidence level
  says so in its own name so that no downstream record can quietly read as binding.
- **Not independent.** The quantity is computed from the same model that generated the
  design. There is no orthogonal re-predictor in this stack (`CASE.md`, "three things a
  reader should not be allowed to miss", item 3). A systematic error of the generator is
  invisible to it.
- **Not bias-free under subtraction.** A margin is `ipSAE(mutant complex) −
  ipSAE(wild-type complex)`. A bias shared by both complexes cancels to first order; a
  **peptide-dependent** bias does not cancel at all, and the two complexes differ exactly by
  the peptide. This is the main scientific risk in the proposal.
- **Not calibrated here.** This repository contains no paired (ipSAE, measured relative
  affinity) data. `src/bindi/data/benchmark.toml` records the ranking as `unchecked`, and
  that is unchanged by this proposal.
- **Not defined for every prediction.** ipSAE refuses rather than guesses when PAE tokens
  cannot be aligned to CIF residues, when a non-polymer entity is present, or when the
  interface to score is ambiguous. A refusal is an outcome, not a zero.

## 3. The measurement that would establish a threshold

This follows the protocol the removed `src/bindi/structural_margin.py` used, unchanged in
shape so that the two are comparable. Ported to ipSAE on hosted Boltz:

1. **Independent seeds.** 20 independent generator seeds per pair (the prior protocol used
   `repeats=20`, `seed_start=1000`). Seeds are the unit of repetition; designs within a seed
   are not independent.
2. **Top margin per seed.** For each seed, compute the margin for every design that seed
   produced and keep the **maximum**. The per-seed statistic is a top margin, not a mean
   over designs — the prior protocol's choice, kept because a design campaign is selected
   on its best candidate.
3. **Spread before any threshold is named.** Compute the population standard deviation
   (`statistics.pstdev`) of the 20 per-seed top margins. **The spread is measured first and
   reported before any threshold is proposed.** A threshold named before the spread is known
   is a number chosen to be passed.
4. **A positive control that must separate.** `PAIR-EASY` from the signed benchmark record
   is the control: a pair whose mutant/wild-type discrimination is expected to be easy. It
   must separate — mean margin positive and at least the multiple of the spread the rule
   names — or **no threshold is established for any pair**. `PAIR-HARD` is run with spread
   reported and no threshold attached. The case pair is run for traceability only.
5. **Both complexes per design.** A margin needs the mutant complex *and* the wild-type
   counterpart for the same binder. The design run predicts the former; the latter is a
   separate prediction job (§5).

Reported artifact: an evidence file in the shape of the old
`bindi.structural_margin_repeat_noise.v1` — per-seed margins, n, mean, min, max, spread,
and the run provenance of every prediction it read (job id, request sha256, pipeline
version). Nothing in it is a pass or a fail.

### 3a. A question this proposal cannot answer for you

The prior protocol's unit is a **mutant vs wild-type peptide pair**. Campaign one's
specificity endpoint, per `CASE.md`, is **on-target rank minus best-decoy rank** over a
ten-member decoy panel on the same HLA. These are not the same statistic, and the decoy
framing is the one with the safety argument behind it. Whether the threshold is established
on mutant/wild-type pairs (comparable to the prior rule) or on on-target/best-decoy (what
campaign one actually ships) is a scientific decision, not an engineering one. The cost
figures below are for the mutant/wild-type protocol as written; a best-of-ten-decoys
statistic multiplies the prediction count by the panel size.

## 4. The 2×-spread multiplier, and whether it should carry over

The prior rule was `margin_threshold = 2.0 × spread`, with the multiplier held in
`design_engine.MARGIN_THRESHOLD_SPREAD_MULTIPLIER_PENDING_SIGNOFF` and described in the
removed structural module as "pending chief-scientist sign-off on the multiplier; until
PAIR-EASY separates at 2× spread the margin stage stays not established". It was never
signed. It is still in the code today, still named `_PENDING_SIGNOFF`, and still attached
only to sequence-level scorers that cannot clear anything.

**Engineering's view: it should not carry over automatically, and we do not propose a
replacement number.** Three reasons:

1. The multiplier was chosen against BindCraft margins in arbitrary pass-signal units. ipSAE
   is bounded roughly in 0–1 and has an entirely different noise structure; a multiplier is
   dimensionless but the distribution it multiplies is not the same distribution.
2. Two population standard deviations estimated from 20 seeds is itself a noisy quantity.
   Whether 2× is a sensible separation bar for *this* statistic is exactly what the
   measurement in §3 is for.
3. Naming the multiplier before the spread is measured would reverse the order §3 insists
   on. We would rather report the spread and let you set the bar against it.

What we do propose: keep 2× as the **prior**, state it explicitly in the evidence file as
the number being tested, and treat a PAIR-EASY separation comfortably above it as support
for carrying it over — and anything marginal as a reason to raise it, not to lower the bar
to meet the data.

## 5. Cost through the APPR gate

Prices below are this project's own, from the live **read-only** cost estimate recorded on
2026-10-20 in `/workspace/wetware/binder-design/v2/boltz-api-pmhc-schema-2026-10-20.md`. No
job was submitted then and none has been submitted since. The key in the environment is a
**live workspace key**, so every figure here is real money.

Design-side unit price, confirmed empirically: **$0.0500 per design** while the whole
complex stays under 512 residues, **$0.1000** above it. Campaign one's target is 383
residues (275 + 99 + 9) and the designed binder is 60–100, so 443–483 residues: the
$0.05 tier, with no headroom for a longer binder. `num_proteins` has an API floor of 10, so
10 designs is the smallest run that exists.

| Scope | Design runs | Design units | Design-side cost | Wild-type counterpart predictions |
|---|---|---|---|---|
| `PAIR-EASY` only (the minimum credible measurement) | 20 seeds × 1 run | 200 | **$10.00** | 200, unpriced |
| `PAIR-EASY` + `PAIR-HARD` | 40 | 400 | **$20.00** | 400, unpriced |
| Full prior protocol (+ case pair) | 60 | 600 | **$30.00** | 600, unpriced |

Three things to read off that table:

- **The control pair alone costs $10.00 on the design side**, which is two thirds of the
  standing APPR cap of $15.00, before the wild-type predictions are priced at all.
- **The full prior protocol is $30.00 design-side, twice the standing cap.** It cannot be
  run under the current approval. It needs either a raised cap or staging across separate
  per-body approvals, one pair at a time, with the spread from the control pair read before
  the next is funded.
- **The wild-type counterpart predictions are not priced.** The 2026-10-20 estimate covered
  `POST /compute/v1/protein/design`; the structure-and-binding prediction endpoint was never
  priced for this complex. We can price it exactly with a read-only
  `POST /compute/v1/protein/design/estimate-cost`-style estimate, which creates no run, and
  we have deliberately not done so without instruction. **Treat every total above as a lower
  bound.** A 10-member decoy panel instead of a single wild-type counterpart multiplies that
  unpriced column by ten.

Cheaper variants, with what they cost scientifically: fewer seeds (10 instead of 20) halves
the design-side cost and halves the data behind an already small-sample standard deviation;
scoring only the top design per seed by mutant ipSAE, rather than every design, removes most
of the wild-type predictions but changes the statistic from "top margin" to "margin of the
top-scoring design", which is not what the prior rule measured.

Whatever is chosen, it goes through APPR unchanged: a per-request-body approval naming its
approver, an unexpired `expires_at`, `on_deny: HALT`, and a cap at or above the estimated
cost, pinned to the sha256 of the exact request. The gate is checked before the credential
is read, so a closed gate cannot reach the API.

## 6. What a signature would be attesting to

Signing would attest to all of the following, and to nothing beyond it:

1. That **ipSAE on Boltz-predicted complexes, as implemented in `src/bindi/ipsae.py` at a
   named commit**, is an acceptable basis for the immunology margin stage — as an
   interface-confidence difference, explicitly not a measurement of binding.
2. That the **spread measurement in §3 was carried out as written** — 20 independent seeds,
   top margin per seed, spread computed and reported before any threshold was named — and
   that the evidence file records what it actually returned.
3. That the **positive control separated**: `PAIR-EASY` showed a positive mean margin at or
   above the chosen multiple of the measured spread, and that this is a necessary condition,
   not a formality.
4. That the **multiplier chosen** (2×, or whatever replaces it) is the signer's judgement
   against that measured spread, and that it was not adjusted afterwards to admit a design.
5. That **`margin.SIGNED_STRUCTURAL_SCORER_IDS` may then contain `ipsae_boltz_cpu_v1`**, and
   that doing so is a change to signed content requiring Mathew's per-SHA sign-off as well.
6. That the signer accepts the **stated limits**: one model, no orthogonal confirmation, no
   experimental anchor in this repository, a peptide-dependent bias that does not cancel
   under subtraction, and transferred rather than calibrated provenance for every threshold
   in the stack.

What a signature would **not** attest to: that any design binds anything; that ipSAE
predicts affinity; that a cleared margin stage is evidence of specificity; or that the
downstream in-vitro hand-off in `CASE.md` can be shortened by it.

## 7. Recommendation

Fund §3 for `PAIR-EASY` only, as one approved request body under the existing $15.00 cap,
after the wild-type prediction leg has been priced read-only. Read the spread. Decide the
multiplier against it. Then, and only then, consider the signature in §6. Until that
signature exists, the margin stage should keep refusing exactly as it does today — that
refusal is the correct output, and a fabricated or placeholder margin number is the worst
possible outcome.
