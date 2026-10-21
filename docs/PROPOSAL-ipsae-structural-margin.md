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

**Revision 2026-10-21 (Mathew):** the endpoint has changed. An earlier draft proposed a
threshold on **mutant minus wild-type**. Mathew accepts the argument that this is the wrong
endpoint for a neoantigen binder: the clinical hazard is a binder that engages invariant
HLA framework and therefore binds in **every HLA-A\*02:01 patient regardless of their
tumour**. The threshold proposed here now governs **on-target minus best decoy across the
negative-design panel**, with the wild-type / self peptide a **mandatory member of that
panel**. That is a population-level safety endpoint, not a single-pair one. Still no
threshold number is proposed. §3, §3a and §5 are rewritten accordingly; §6 follows.

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

### 2a. The one thing ipSAE may already do, with no signature at all

`src/bindi/ipsae_veto.py` uses ipSAE as a **veto-only** filter: it may refuse a design and
it may never pass, bless, rank-promote or certify one. The asymmetry is what licenses it
without a threshold. A poor interface-confidence score is a reason to stop spending
attention on a design; a good one is not a reason to keep it, and subtracting candidates
cannot manufacture evidence that any survivor works. Nothing about this clears the margin
stage, and the filter says so in its own record.

It is enforced structurally, not by convention: a decision has no passing state to read
(truth-testing one raises), a decision may carry an ipSAE number **only** when it is a
veto, so a surviving design has no score attached anywhere for anything downstream to rank
on, and the filter returns the surviving design objects unmodified and in their original
order. With no veto floor configured in the campaign policy it is inert — there is no
default floor in code, because a number there would be a threshold in all but name.

### 2b. The caveat travels in the output, not only here

Every record that carries an ipSAE-derived number also carries `ipsae_margin.CAVEAT`
verbatim (margin records get it as `evidence_caveat`; veto records as `caveat`): that this
repository has no orthogonal re-predictor and no experimental anchor for ipSAE, that its
error is therefore peptide-dependent and **does not cancel under subtraction**, and that
any threshold ever signed off this quantity is **provisional until wet-lab confirmation**.
A reader of a trace file should not have to find this document to learn that.

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
   is the control: a comparator whose discrimination is expected to be easy. It must
   separate — mean margin positive and at least the multiple of the spread the rule names —
   or **no threshold is established for anything**. `PAIR-HARD` is run with spread reported
   and no threshold attached. The campaign target is run for traceability only.
5. **The margin is on-target minus BEST DECOY, over the whole panel.** For each design, the
   on-target complex is predicted (the design run does this) and then the same binder is
   re-folded against **every member of the negative-design panel** on the same HLA; the
   margin is the on-target score minus the **maximum** over the panel. The best decoy, not
   the mean decoy: one cross-reactive partner is a safety failure however well the design
   does against the other nine.
6. **The wild-type / self peptide is a mandatory panel member.** It is one decoy among
   others, not the comparator. For campaign one's shared cancer-testis target the self
   peptide presented in healthy tissue is the identical sequence `SLLMWITQC`, which is
   recorded as an explicit absence of a distinct wild-type counterpart in
   `fixtures/immunology_case_NYESO1_A0201.json`; for the benchmark pairs it is a genuine
   distinct peptide and must be in the panel.
7. **Why the endpoint changed.** `CASE.md` puts the peptide at roughly 12–49% of the buried
   surface of a natural TCR–pMHC class I interface, mean about 29%; the rest is invariant
   framework present on every nucleated cell. A design that scores well on the mutant and
   equally well on the wild type has not failed a pair test by much — it has revealed that
   it is reading framework, and a framework binder engages **every patient carrying the
   restricting allele**. The MAGE-A3 affinity-enhanced TCR, which cross-reacted with titin
   and killed two patients by cardiogenic shock, is the reference failure. A threshold on a
   single pair cannot see this; a threshold on the best decoy over a panel can.

Reported artifact: an evidence file in the shape of the old
`bindi.structural_margin_repeat_noise.v1` — per-seed margins, n, mean, min, max, spread,
and the run provenance of every prediction it read (job id, request sha256, pipeline
version). Nothing in it is a pass or a fail.

### 3a. The endpoint question, now decided — and what it costs

An earlier draft left this open: the prior protocol's unit was a **mutant vs wild-type
pair**, while campaign one's specificity endpoint per `CASE.md` is **on-target minus best
decoy** over a ten-member panel on the same HLA. Mathew decided it on 2026-10-21 in favour
of the panel, for the safety reason in item 7 above. Two consequences follow, and neither
is free:

- **The statistic is no longer comparable to the prior rule's.** Anything carried over from
  the 2× multiplier era (§4) is carried over across a change of statistic as well as a
  change of scorer. That strengthens the argument for not carrying it over automatically.
- **The prediction count multiplies by the panel size.** The design side is unchanged, but
  each design now needs up to ten re-folds instead of one. §5 prices both ends of that.

What remains undecided and is not decided here: the ten decoy peptides themselves. `CASE.md`
describes them (nine single or double mutants of `SLLMWITQC` and one quadruple mutant) but
does not enumerate them, and they are not invented anywhere in this repository. They are
needed before any specificity claim, and supplying them is BIO Physician / BIO Scientist
work.

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

| Scope | Design runs | Design units | Design-side cost | Panel re-fold predictions (unpriced) |
|---|---|---|---|---|
| `PAIR-EASY` only (the minimum credible measurement) | 20 seeds × 1 run | 200 | **$10.00** | 200 (top design per seed) to 2,000 (every design) |
| `PAIR-EASY` + `PAIR-HARD` | 40 | 400 | **$20.00** | 400 to 4,000 |
| Full protocol (+ the campaign target) | 60 | 600 | **$30.00** | 600 to 6,000 |

The re-fold column is where the endpoint change lands. Under the old pair endpoint each
design needed **one** counterpart prediction; under the panel endpoint it needs **one per
panel member**, and `CASE.md`'s reproduction panel has ten. Scoring only the top design per
seed keeps the column at 10 × 20 = 200 predictions per pair; scoring every design in every
run takes it to 10 × 200 = 2,000 per pair. Neither number is priced (see below), and the
choice between them is the same scientific trade-off as the "top margin" statistic itself.

Three things to read off that table:

- **The control pair alone costs $10.00 on the design side**, which is two thirds of the
  standing APPR cap of $15.00, before the wild-type predictions are priced at all.
- **The full prior protocol is $30.00 design-side, twice the standing cap.** It cannot be
  run under the current approval. It needs either a raised cap or staging across separate
  per-body approvals, one pair at a time, with the spread from the control pair read before
  the next is funded.
- **The panel re-fold predictions are not priced.** The 2026-10-20 estimate covered
  `POST /compute/v1/protein/design`; the structure-and-binding prediction endpoint was never
  priced for this complex. We can price it exactly with a read-only
  `POST /compute/v1/protein/design/estimate-cost`-style estimate, which creates no run, and
  we have deliberately not done so without instruction. **Treat every total above as a lower
  bound**, and under the panel endpoint the unpriced column is now the dominant term: at
  200–2,000 predictions per pair it will plausibly exceed the design-side cost, so it must
  be priced read-only **before** any approval is drafted, not after.

Cheaper variants, with what they cost scientifically: fewer seeds (10 instead of 20) halves
the design-side cost and halves the data behind an already small-sample standard deviation;
scoring only the top design per seed by on-target ipSAE, rather than every design, cuts the
re-fold column tenfold but changes the statistic from "top margin" to "margin of the
top-scoring design", which is not what the prior rule measured; and a reduced panel (say
three decoys instead of ten) cuts it proportionally but weakens exactly the thing the
endpoint was changed to capture, since the statistic is the **best** decoy and a smaller
panel is a smaller chance of finding it.

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
   top margin per seed, margin taken as on-target minus **best** decoy over the full panel
   with the wild-type / self peptide in it, and spread computed and reported before any
   threshold was named — and that the evidence file records what it actually returned.
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
   in the stack — and therefore that **any threshold signed off this quantity is provisional
   until wet-lab confirmation**, which is the caveat now carried verbatim in every output
   record that contains an ipSAE-derived number.
7. That the panel the threshold was measured against is **the panel that will be used**:
   a threshold established on a ten-member panel says nothing about a design screened
   against three.

What a signature would **not** attest to: that any design binds anything; that ipSAE
predicts affinity; that a cleared margin stage is evidence of specificity; or that the
downstream in-vitro hand-off in `CASE.md` can be shortened by it.

## 7. Open question, NOT an engineering decision: cropping the target construct

Raised and explicitly **not approved** by Mathew on 2026-10-21, recorded here so it is not
quietly implemented. The construct is unchanged and nothing below has been done.

The idea was to drop the alpha-3 domain and beta-2-microglobulin from the target construct
to get under the 512-residue pricing tier. Mathew's view: it may be worth it, but
**beta-2-microglobulin is structurally required for a stable class I fold**, so this is a
question for the Scientist, not an engineering change.

The arithmetic, from this repository's own spec (`campaigns/nyeso1_a0201_pmhc.yaml`), so the
Scientist is answering a costed question:

| Construct | Chain A | Chain B (B2M) | Chain C | Target total | With a 60–100 aa binder |
|---|---|---|---|---|---|
| As shipped | 275 | 99 | 9 | 383 | **443–483** |
| Crop alpha-3 only (mature 183–274, 92 residues) | 183 | 99 | 9 | 291 | **351–391** |
| Crop alpha-3 and B2M | 183 | — | 9 | 192 | **252–292** |

**The specific question asked — does cropping alpha-3 alone get the construct under 512
total residues? — has an answer that makes the premise worth re-examining: the construct is
already under 512 as shipped.** At 443–483 residues campaign one sits in the $0.0500 tier
without any cropping, so cropping buys **no pricing-tier saving at the current binder
length**. It buys headroom: the gap to 512 is only 29 residues at a 100 aa binder, so a
longer binder, a fourth chain, or a larger panel construct would cross the tier and double
the per-unit price. Whether that headroom is worth removing a domain is the scientific
question, and it is not ours.

What engineering can add, and nothing more: alpha-3 and B2M are both membrane-proximal and
neither is part of a TCR or TCR-mimic footprint (`fleets/nonbinding-panel-2026-10-20.md`),
which is why both are already marked in full in the negative-design panel — they are
steered away from rather than removed. Whether a construct that is *marked* non-binding can
also be *deleted* without changing the fold the groove presents, given B2M's structural
role, is the question for the Scientist. Until it is answered the construct stays as it is.

## 8. Recommendation

Fund §3 for `PAIR-EASY` only, as one approved request body under the existing $15.00 cap,
after the panel re-fold leg has been priced read-only — which under the new endpoint may
well decide whether the measurement fits under the cap at all. Read the spread. Decide the
multiplier against it. Then, and only then, consider the signature in §6. Until that
signature exists, the margin stage should keep refusing exactly as it does today — that
refusal is the correct output, and a fabricated or placeholder margin number is the worst
possible outcome.
