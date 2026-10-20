"""ipSAE scoring from PAE + predicted CIF (CPU, numpy-only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from bindi.ipsae import (
    ChainSpec,
    TokenAlignmentError,
    compute_ipsae,
    load_pae_npz,
    parse_chains_arg,
    parse_predicted_cif,
    score_from_paths,
    score_predicted_complex,
)

ROOT = Path(__file__).resolve().parents[1]
LIGAND_FIXTURE_CIF = ROOT / "fixtures" / "ipsae_predicted.cif"


def _two_chain_pae(
    n_a: int,
    n_b: int,
    block_ab: np.ndarray,
    fill: float = 30.0,
) -> np.ndarray:
    n = n_a + n_b
    pae = np.full((n, n), fill, dtype=float)
    pae[:n_a, n_a:] = block_ab
    pae[n_a:, :n_a] = block_ab.T
    np.fill_diagonal(pae, 0.0)
    return pae


def _minimal_protein_cif(
    chains: dict[str, list[tuple[int, str, float]]],
) -> str:
    """Build a tiny ModelCIF with Boltz-style _atom_site column order."""
    lines = [
        "data_model",
        "loop_",
        "_atom_site.group_PDB",
        "_atom_site.id",
        "_atom_site.type_symbol",
        "_atom_site.label_atom_id",
        "_atom_site.label_alt_id",
        "_atom_site.label_comp_id",
        "_atom_site.label_seq_id",
        "_atom_site.auth_seq_id",
        "_atom_site.pdbx_PDB_ins_code",
        "_atom_site.label_asym_id",
        "_atom_site.Cartn_x",
        "_atom_site.Cartn_y",
        "_atom_site.Cartn_z",
        "_atom_site.occupancy",
        "_atom_site.label_entity_id",
        "_atom_site.auth_asym_id",
        "_atom_site.auth_comp_id",
        "_atom_site.B_iso_or_equiv",
        "_atom_site.pdbx_PDB_model_num",
    ]
    atom_id = 1
    entity = 1
    for asym_id, residues in chains.items():
        for seq_id, comp_id, plddt in residues:
            lines.append(
                f"ATOM {atom_id} C CA . {comp_id} {seq_id} {seq_id} ? {asym_id} "
                f"0 0 0 1 {entity} {asym_id} {comp_id} {plddt:.3f} 1"
            )
            atom_id += 1
        entity += 1
    return "\n".join(lines) + "\n"


def test_strong_interface_high_ipsae():
    n_a, n_b = 8, 8
    block = np.zeros((n_a, n_b))
    pae = _two_chain_pae(n_a, n_b, block)
    chains = [ChainSpec("A", n_a), ChainSpec("B", n_b)]
    out = compute_ipsae(pae, chains, pae_cutoff=15.0)
    assert out["ordered"]["A->B"] > 0.9
    assert out["ordered"]["B->A"] > 0.9
    assert out["pairs"]["A|B"]["min_ipsae"] > 0.9


def test_weak_interface_near_zero():
    n_a, n_b = 8, 8
    block = np.full((n_a, n_b), 20.0)
    pae = _two_chain_pae(n_a, n_b, block)
    chains = [ChainSpec("A", n_a), ChainSpec("B", n_b)]
    out = compute_ipsae(pae, chains, pae_cutoff=15.0)
    assert out["ordered"]["A->B"] == 0.0
    assert out["ordered"]["B->A"] == 0.0
    assert out["pairs"]["A|B"]["min_ipsae"] == 0.0


def test_asymmetry_min_ipsae_picks_smaller():
    n_a, n_b = 4, 2
    block = np.full((n_a, n_b), 30.0)
    block[0, :] = 0.0
    block[1, :] = 14.0
    pae = _two_chain_pae(n_a, n_b, block)
    chains = [ChainSpec("A", n_a), ChainSpec("B", n_b)]
    out = compute_ipsae(pae, chains, pae_cutoff=15.0)
    ab = out["ordered"]["A->B"]
    ba = out["ordered"]["B->A"]
    assert ab > ba
    assert out["pairs"]["A|B"]["min_ipsae"] == ba


def test_n_eff_edge_no_nan():
    n_a, n_b = 4, 4
    block = np.full((n_a, n_b), 30.0)
    block[0, 0] = 5.0
    block[1, 1] = 6.0
    pae = _two_chain_pae(n_a, n_b, block)
    chains = [ChainSpec("A", n_a), ChainSpec("B", n_b)]
    out = compute_ipsae(pae, chains, pae_cutoff=15.0)
    for v in out["ordered"].values():
        assert np.isfinite(v)
    assert out["pairs"]["A|B"]["min_ipsae"] >= 0.0


def test_three_chain_submatrix_slices():
    lengths = (3, 2, 4)
    total = sum(lengths)
    pae = np.full((total, total), 40.0, dtype=float)
    np.fill_diagonal(pae, 0.0)
    slices = {}
    start = 0
    for name, ln in zip(("A", "B", "C"), lengths, strict=True):
        slices[name] = slice(start, start + ln)
        start += ln

    pae[slices["A"], slices["B"]] = 3.0
    pae[slices["B"], slices["A"]] = 3.0
    pae[slices["B"], slices["C"]] = 25.0
    pae[slices["C"], slices["B"]] = 25.0

    chains = [ChainSpec("A", 3), ChainSpec("B", 2), ChainSpec("C", 4)]
    out = compute_ipsae(pae, chains, pae_cutoff=15.0)

    ab_only = np.full((3, 2), 40.0, dtype=float)
    ab_only[:, :] = 3.0
    pae_ab_only = _two_chain_pae(3, 2, ab_only)
    only_ab = compute_ipsae(
        pae_ab_only,
        [ChainSpec("A", 3), ChainSpec("B", 2)],
        pae_cutoff=15.0,
    )
    assert out["ordered"]["A->B"] == only_ab["ordered"]["A->B"]
    assert out["ordered"]["B->C"] == 0.0
    assert out["pairs"]["B|C"]["min_ipsae"] == 0.0


def test_parse_chains_arg():
    chains = parse_chains_arg("A:275,B:99,C:9")
    assert [c.chain_id for c in chains] == ["A", "B", "C"]
    assert [c.length for c in chains] == [275, 99, 9]


def test_all_protein_cif_derives_chains_and_plddt(tmp_path: Path):
    cif_text = _minimal_protein_cif(
        {
            "A": [(1, "ALA", 80.0), (2, "GLY", 85.0), (3, "SER", 90.0)],
            "B": [(1, "VAL", 70.0), (2, "LEU", 75.0)],
        }
    )
    cif_path = tmp_path / "pred.cif"
    cif_path.write_text(cif_text)
    parsed = parse_predicted_cif(cif_path)
    assert parsed.has_non_polymer is False
    assert parsed.residue_counts == {"A": 3, "B": 2}

    block = np.full((3, 2), 4.0)
    pae = _two_chain_pae(3, 2, block)
    out = score_predicted_complex(pae, parsed, pae_cutoff=15.0)
    assert out["n_tokens"] == 5
    assert out["plddt"]["present"] is True
    assert out["plddt"]["complex_plddt"] == pytest.approx(80.0)
    assert out["plddt"]["interface_plddt"] == pytest.approx(80.0)
    assert out["ordered"]["A->B"] > 0.0


def test_ligand_fixture_raises_on_token_mismatch(tmp_path: Path):
    parsed = parse_predicted_cif(LIGAND_FIXTURE_CIF)
    assert parsed.has_non_polymer is True
    assert parsed.residue_counts["A"] == 169
    pae = np.zeros((256, 256), dtype=np.float32)
    npz_path = tmp_path / "pae.npz"
    np.savez(npz_path, pae=pae)
    with pytest.raises(TokenAlignmentError, match="non-polymer"):
        score_from_paths(npz_path, LIGAND_FIXTURE_CIF)


def test_token_count_mismatch_raises_without_ligand(tmp_path: Path):
    cif_text = _minimal_protein_cif(
        {
            "A": [(1, "ALA", 50.0), (2, "GLY", 60.0)],
            "B": [(1, "VAL", 55.0), (2, "LEU", 65.0)],
        }
    )
    cif_path = tmp_path / "two.cif"
    cif_path.write_text(cif_text)
    parsed = parse_predicted_cif(cif_path)
    pae = np.zeros((5, 5))
    with pytest.raises(TokenAlignmentError, match="4 polymer residues"):
        score_predicted_complex(pae, parsed)


def test_degenerate_plddt_marked_absent(tmp_path: Path):
    cif_text = _minimal_protein_cif(
        {
            "A": [(1, "ALA", 100.0), (2, "GLY", 100.0)],
            "B": [(1, "VAL", 100.0), (2, "LEU", 100.0)],
        }
    )
    cif_path = tmp_path / "flat.cif"
    cif_path.write_text(cif_text)
    parsed = parse_predicted_cif(cif_path)
    pae = _two_chain_pae(2, 2, np.full((2, 2), 5.0))
    out = score_predicted_complex(pae, parsed)
    assert out["plddt"]["present"] is False
    assert out["plddt"]["complex_plddt"] is None
    assert "placeholder" in out["plddt"]["reason"]


def test_fixture_cif_column_layout_matches_boltz():
    parsed = parse_predicted_cif(LIGAND_FIXTURE_CIF)
    assert parsed.chain_order == ("A",)
    assert len(parsed.residues) == 169
    assert all(r.plddt == 100.0 for r in parsed.residues)


def test_cli_round_trip_npz(tmp_path: Path):
    cif_text = _minimal_protein_cif(
        {
            "A": [(i, "ALA", 55.0 + i) for i in range(1, 6)],
            "B": [(i, "GLY", 65.0 + i) for i in range(1, 6)],
        }
    )
    cif_path = tmp_path / "pred.cif"
    cif_path.write_text(cif_text)
    n_a, n_b = 5, 5
    block = np.full((n_a, n_b), 4.0)
    pae = _two_chain_pae(n_a, n_b, block)
    npz_path = tmp_path / "pae.npz"
    np.savez(npz_path, pae=pae)

    loaded = load_pae_npz(str(npz_path))
    assert np.allclose(loaded, pae)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bindi.ipsae",
            str(npz_path),
            str(cif_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    cli_out = json.loads(proc.stdout)
    parsed = parse_predicted_cif(cif_path)
    ref = score_predicted_complex(pae, parsed)
    assert cli_out["ordered"] == ref["ordered"]
    assert cli_out["pairs"] == ref["pairs"]
    assert cli_out["plddt"]["present"] is True


# --- positive control -------------------------------------------------------
# Expected values are NOT taken from this implementation. They are the output of
# the independent reference implementation (Dunbrack ipsae.py) on the same two
# files at PAE cutoff 10 / distance cutoff 10, committed alongside the fixture as
# fixtures/ipsae_positive_twochain_reference_ipsae_10_10.txt:
#     A -> B  ipSAE 0.852971
#     B -> A  ipSAE 0.838137
# The reference prints 6 decimal places, so the tolerance is 5e-7: tight enough
# that any real change in the scoring path fails, loose enough to absorb the
# reference's own printed precision.
POSITIVE_FIXTURE_CIF = ROOT / "fixtures" / "ipsae_positive_twochain.cif"
POSITIVE_FIXTURE_PAE = ROOT / "fixtures" / "ipsae_positive_twochain_pae.npz"
REFERENCE_IPSAE_A_TO_B = 0.852971
REFERENCE_IPSAE_B_TO_A = 0.838137
REFERENCE_TOLERANCE = 5e-7


def test_positive_fixture_matches_reference_implementation():
    """The shipped positive control must agree with the reference ipSAE to 6 dp."""
    result = score_from_paths(str(POSITIVE_FIXTURE_PAE), str(POSITIVE_FIXTURE_CIF))

    assert [c["chain_id"] for c in result["chains"]] == ["A", "B"]
    assert [c["length"] for c in result["chains"]] == [100, 69]
    assert result["n_tokens"] == 169

    assert result["ordered"]["A->B"] == pytest.approx(
        REFERENCE_IPSAE_A_TO_B, abs=REFERENCE_TOLERANCE
    )
    assert result["ordered"]["B->A"] == pytest.approx(
        REFERENCE_IPSAE_B_TO_A, abs=REFERENCE_TOLERANCE
    )

    pair = result["pairs"]["A|B"]
    assert pair["min_ipsae"] == pytest.approx(
        min(REFERENCE_IPSAE_A_TO_B, REFERENCE_IPSAE_B_TO_A), abs=REFERENCE_TOLERANCE
    )
    # min() must pick B->A here, i.e. the conservative direction, not the larger one.
    assert pair["min_ipsae"] == pair["ipsae_B_to_A"]
    assert pair["min_ipsae"] < pair["ipsae_A_to_B"]


def test_positive_fixture_reports_no_plddt():
    """Constant B-factors are a Boltz placeholder, not pLDDT, and must be refused."""
    result = score_from_paths(str(POSITIVE_FIXTURE_PAE), str(POSITIVE_FIXTURE_CIF))
    assert result["plddt"]["present"] is False
    assert result["plddt"]["complex_plddt"] is None
    assert result["plddt"]["interface_plddt"] is None


def test_positive_fixture_cutoff_invariance_is_explained():
    """Score is cutoff-invariant here ONLY because the best row sits below every cutoff.

    This documents a known limit of the fixture: it cannot regression-test
    --pae-cutoff. The assertion on the best row's maximum interface PAE is what
    makes the invariance a fact rather than a coincidence.
    """
    pae = load_pae_npz(str(POSITIVE_FIXTURE_PAE))
    block_ab = pae[0:100, 100:169]

    scores = {}
    for cutoff in (5.0, 10.0, 15.0, 20.0):
        result = score_from_paths(
            str(POSITIVE_FIXTURE_PAE), str(POSITIVE_FIXTURE_CIF), pae_cutoff=cutoff
        )
        scores[cutoff] = result["ordered"]["A->B"]

    assert len(set(scores.values())) == 1, scores

    # Row 80 is the argmax of the by-residue scores, i.e. the row that sets the
    # reported A->B value. Its worst interface PAE is below the lowest cutoff
    # tested, so its valid-pair set is identical at every cutoff above.
    assert float(block_ab[80].max()) < 5.0
