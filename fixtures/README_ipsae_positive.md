# ipSAE positive-control fixture

Until this fixture was added, `bindi`'s ipSAE worker had **no committed positive
control**. The only shipped structure, `ipsae_predicted.cif`, is a single-chain +
ligand *negative* fixture that the worker is required to reject
(`TokenAlignmentError`), and every passing number in `tests/test_ipsae.py` came
from PAE matrices synthesized in memory by the tests themselves. The suite could
therefore only ever confirm that the implementation agrees with itself.

## Files

| File | What it is |
|---|---|
| `ipsae_positive_twochain.cif` | Two-chain polymer model, chain A 100 residues, chain B 69 residues, 169 tokens |
| `ipsae_positive_twochain_pae.npz` | Matching 169 x 169 PAE matrix, key `pae` |
| `ipsae_positive_twochain_reference_ipsae_10_10.txt` | Output of the independent reference implementation on these same two files |

## Expected values

Run as `python -m bindi.ipsae fixtures/ipsae_positive_twochain_pae.npz fixtures/ipsae_positive_twochain.cif`:

```
ordered A->B   = 0.8529713769693358
ordered B->A   = 0.8381368355923635
min_ipsae      = 0.8381368355923635   (pairs."A|B")
n_tokens       = 169
plddt.present  = false
```

## Why this fixture is worth committing

The numbers above are **not** self-referential. Dunbrack's reference `ipsae.py`,
run on the same two files at PAE cutoff 10 and distance cutoff 10, reports
ipSAE `0.852971` (A to B) and `0.838137` (B to A) — see the committed reference
output file. The two independent implementations agree to six decimal places.
That cross-implementation agreement, not the magnitude of the score, is what
this fixture pins.

## Honest limits

- **Provenance of the structure is not fully established.** The CIF carries no
  `_struct.title`, no entry id and no source metadata — only `data_twochain` and
  an `_atom_site` loop. Its uniform `B_iso_or_equiv` of 100.000 is consistent
  with a Boltz-style placeholder B-factor column, but the file itself does not
  record which run produced it. It is committed for its numerical agreement with
  the reference implementation, not as a structure of known origin.
- **pLDDT is absent by construction.** Because that B-factor column is constant,
  the worker correctly reports `plddt.present = false` with reason
  `B_iso_or_equiv is constant (likely Boltz placeholder, not model pLDDT)`. This
  fixture therefore does not exercise the pLDDT path.
- **This fixture does not exercise PAE-cutoff sensitivity.** The score is
  bit-identical at `--pae-cutoff` 5, 10, 15 and 20. That is expected rather than
  a bug: the best-scoring residue (row index 80 of the A->B block) has a maximum
  interface PAE of 3.13, below every one of those cutoffs, so its valid-pair set
  never changes. A fixture with interface PAE spanning the cutoff range would be
  needed to regression-test that parameter.
- **No pMHC content.** This is a generic two-chain complex. It says nothing
  about whether the thresholds used in campaign NYESO1-A0201 transfer to
  class I pMHC targets; those remain transferred, not calibrated.

---

# Campaign target reference: PDB 9NNF

`nyeso1_a0201_9nnf_reference.cif` is the experimental cryo-EM structure of the
exact complex campaign NYESO1-A0201 targets:

> Cryo-EM structure of a de-novo designed binder NY1-B04 in complex with
> HLA-A\*02:01 and NY-ESO-1-derived peptide SLLMWITQC
> PDB [9NNF](https://www.rcsb.org/structure/9NNF), EMDB 49573, 3.8 Angstrom,
> released 2025-07-23.

Chains as parsed by gemmi:

| Chain | Residues | What |
|---|---|---|
| A | 141 | de novo designed binder NY1-B04 |
| B | 276 | HLA class I heavy chain, A alpha |
| C | 100 | beta-2-microglobulin |
| D | 9 | `SLLMWITQC` (NY-ESO-1 157-165) |

Primary citation: Johansen, K. H. et al. De novo-designed pMHC binders
facilitate T cell-mediated cytotoxicity toward cancer cells. *Science* 389,
380-385 (2025). DOI: 10.1126/science.adv0422 (PMID: 40705893). This is the
peer-reviewed version of bioRxiv 10.1101/2024.11.27.624796, which the campaign
spec still cites as a preprint; Crossref records the preprint relation
explicitly.

## What this fixture is NOT

**It is not an ipSAE positive control.** An experimental structure has no
predicted aligned error, and the ipSAE worker scores predictions. Scoring the
campaign target with this worker requires a *predicted* structure of this
complex with its PAE matrix, which means a Boltz submission, which is gated by
the in-graph approval node. Nothing has been submitted and nothing has been
spent.

It is committed here as ground truth: the structure any future prediction of
this target should be compared against, and the only file in this repository
that contains the actual campaign epitope.
