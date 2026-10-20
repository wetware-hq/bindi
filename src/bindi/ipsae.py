"""Interface PAE score (ipSAE) from predicted aligned error matrices.

Computes Dunbrack-lab ipSAE locally from Boltz/AlphaFold PAE outputs (CPU, numpy only).
The score follows the ``ipsae_d0res`` variant in dunbracklab/IPSAE ``ipsae.py``: per aligned
residue *i*, *d0* uses the count of partner residues *j* with PAE < cutoff (``n0res``), then
ipSAE for chain A→B is max over *i* in A of the mean pTM transform over qualifying *j* in B.

Reference uses ``max(A→B, B→A)`` for the symmetric pair summary; bindi exposes
``min_ipSAE = min(A→B, B→A)`` for conservative binder curation (see ``curate.design_ipsae_min``).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

import numpy as np

_PAE_NPZ_KEYS = ("pae", "predicted_aligned_error", "predicted_aligned_errors")


@dataclass(frozen=True)
class ChainSpec:
    """One chain in the same order as rows/columns of the complex PAE matrix."""

    chain_id: str
    length: int


def _calc_d0_per_residue(n_eff: np.ndarray) -> np.ndarray:
    """Tm-score *d0* per residue from partner counts (reference ``calc_d0_array``, protein)."""
    # Reference clamps L to at least 26 before the Yang–Skolnick formula; minimum d0 is 1.0.
    l_clamped = np.maximum(n_eff.astype(float), 26.0)
    d0 = 1.24 * (l_clamped - 15.0) ** (1.0 / 3.0) - 1.8
    return np.maximum(1.0, d0)


def _ptm_values(pae_row: np.ndarray, d0: float) -> np.ndarray:
    return 1.0 / (1.0 + (pae_row / d0) ** 2.0)


def _ipsae_direction(
    pae_block: np.ndarray,
    pae_cutoff: float,
) -> tuple[float, np.ndarray]:
    """ipSAE for one ordered pair: rows = aligned chain, cols = partner chain."""
    valid = pae_block < pae_cutoff
    n_rows = pae_block.shape[0]
    byres = np.zeros(n_rows, dtype=float)
    for i in range(n_rows):
        mask = valid[i]
        if not np.any(mask):
            byres[i] = 0.0
            continue
        n_eff = int(mask.sum())
        d0 = float(_calc_d0_per_residue(np.array([n_eff]))[0])
        ptm_row = _ptm_values(pae_block[i], d0)
        byres[i] = float(ptm_row[mask].mean())
    return float(byres.max()), byres


def _chain_slices(chains: list[ChainSpec]) -> dict[str, slice]:
    start = 0
    out: dict[str, slice] = {}
    for ch in chains:
        end = start + ch.length
        out[ch.chain_id] = slice(start, end)
        start = end
    return out


def _validate_pae(pae: np.ndarray, chains: list[ChainSpec]) -> None:
    if pae.ndim != 2 or pae.shape[0] != pae.shape[1]:
        raise ValueError(f"PAE must be a square 2D array, got shape {pae.shape}")
    n = sum(c.length for c in chains)
    if pae.shape[0] != n:
        raise ValueError(
            f"PAE dimension {pae.shape[0]} does not match sum of chain lengths ({n})"
        )


def load_pae_npz(path: str) -> np.ndarray:
    """Load a PAE matrix from a ``.npz`` file (Boltz / AF-style keys)."""
    with np.load(path) as data:
        key = None
        for candidate in _PAE_NPZ_KEYS:
            if candidate in data:
                key = candidate
                break
        if key is None:
            names = list(data.files)
            raise KeyError(
                f"No PAE array in {path}; tried {_PAE_NPZ_KEYS}, found keys {names}"
            )
        pae = np.asarray(data[key], dtype=float)
    return pae


def parse_chains_arg(spec: str) -> list[ChainSpec]:
    """Parse ``A:275,B:99`` into chain specs."""
    chains: list[ChainSpec] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Invalid chain segment '{part}'; expected CHAIN:LENGTH")
        chain_id, length_s = part.split(":", 1)
        chain_id = chain_id.strip()
        if not chain_id:
            raise ValueError(f"Empty chain id in '{part}'")
        length = int(length_s.strip())
        if length <= 0:
            raise ValueError(f"Chain length must be positive, got {length} in '{part}'")
        chains.append(ChainSpec(chain_id=chain_id, length=length))
    if len(chains) < 2:
        raise ValueError("At least two chains are required for ipSAE")
    return chains


def compute_ipsae(
    pae: np.ndarray,
    chains: list[ChainSpec],
    pae_cutoff: float = 15.0,
) -> dict[str, Any]:
    """
    Compute ipSAE for all unordered chain pairs.

    Returns ``ordered`` keyed by ``"A->B"`` strings and ``pairs`` keyed by sorted
    ``"A|B"`` with ``min_ipsae``, ``ipsae_a_to_b``, and ``ipsae_b_to_a``.
    """
    if pae_cutoff <= 0:
        raise ValueError("pae_cutoff must be positive")
    _validate_pae(pae, chains)
    slices = _chain_slices(chains)
    chain_ids = [c.chain_id for c in chains]

    ordered: dict[str, float] = {}
    pairs: dict[str, dict[str, float]] = {}

    for i, id_a in enumerate(chain_ids):
        for id_b in chain_ids[i + 1 :]:
            block_ab = pae[slices[id_a], slices[id_b]]
            block_ba = pae[slices[id_b], slices[id_a]]
            score_ab, _ = _ipsae_direction(block_ab, pae_cutoff)
            score_ba, _ = _ipsae_direction(block_ba, pae_cutoff)
            ordered[f"{id_a}->{id_b}"] = score_ab
            ordered[f"{id_b}->{id_a}"] = score_ba
            key = "|".join(sorted((id_a, id_b)))
            pairs[key] = {
                "min_ipsae": min(score_ab, score_ba),
                f"ipsae_{id_a}_to_{id_b}": score_ab,
                f"ipsae_{id_b}_to_{id_a}": score_ba,
            }

    return {
        "pae_cutoff": pae_cutoff,
        "ordered": ordered,
        "pairs": pairs,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute ipSAE from a PAE .npz file")
    parser.add_argument("pae_npz", help="Path to PAE npz (e.g. Boltz pae.npz)")
    parser.add_argument(
        "--chains",
        required=True,
        help="Comma-separated CHAIN:LENGTH in PAE order, e.g. A:275,B:99,C:9",
    )
    parser.add_argument(
        "--pae-cutoff",
        type=float,
        default=15.0,
        help="PAE cutoff in angstroms (default 15)",
    )
    args = parser.parse_args(argv)
    pae = load_pae_npz(args.pae_npz)
    chains = parse_chains_arg(args.chains)
    result = compute_ipsae(pae, chains, pae_cutoff=args.pae_cutoff)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
