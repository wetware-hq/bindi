"""ipSAE scoring from PAE matrices (CPU, numpy-only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from bindi.ipsae import ChainSpec, compute_ipsae, load_pae_npz, parse_chains_arg

ROOT = Path(__file__).resolve().parents[1]


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


def test_cli_round_trip_npz(tmp_path: Path):
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
            "--chains",
            "A:5,B:5",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    cli_out = json.loads(proc.stdout)
    ref = compute_ipsae(pae, [ChainSpec("A", 5), ChainSpec("B", 5)])
    assert cli_out["ordered"] == ref["ordered"]
    assert cli_out["pairs"] == ref["pairs"]
