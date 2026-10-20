"""Interface PAE score (ipSAE) from Boltz ``pae.npz`` + ``_predicted.cif``.

Computes Dunbrack-lab ipSAE locally (CPU, numpy). PAE is per-token; chain assignment and
pLDDT come only from the ModelCIF ``_atom_site`` loop (not from the npz).

The score follows ``ipsae_d0res`` in dunbracklab/IPSAE. bindi uses ``min(A→B, B→A)`` for
``min_ipsae`` (reference uses max) for conservative binder curation.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gemmi
import numpy as np

_PAE_NPZ_KEYS = ("pae", "predicted_aligned_error", "predicted_aligned_errors")

_ATOM_SITE_COLUMNS = (
    "group_PDB",
    "id",
    "type_symbol",
    "label_atom_id",
    "label_alt_id",
    "label_comp_id",
    "label_seq_id",
    "auth_seq_id",
    "pdbx_PDB_ins_code",
    "label_asym_id",
    "Cartn_x",
    "Cartn_y",
    "Cartn_z",
    "occupancy",
    "label_entity_id",
    "auth_asym_id",
    "auth_comp_id",
    "B_iso_or_equiv",
    "pdbx_PDB_model_num",
)


class TokenAlignmentError(ValueError):
    """PAE token count or composition cannot be aligned to the predicted CIF."""


@dataclass(frozen=True)
class ChainSpec:
    """One polymer chain segment in PAE token order (one token per residue)."""

    chain_id: str
    length: int


@dataclass(frozen=True)
class CifResidueRecord:
    chain_id: str
    seq_id: int
    comp_id: str
    plddt: float


@dataclass(frozen=True)
class ParsedPredictedCif:
    """Polymer residue table and flags from a Boltz ``_predicted.cif``."""

    residues: tuple[CifResidueRecord, ...]
    chain_order: tuple[str, ...]
    has_non_polymer: bool
    residue_counts: dict[str, int]


def _calc_d0_per_residue(n_eff: np.ndarray) -> np.ndarray:
    l_clamped = np.maximum(n_eff.astype(float), 26.0)
    d0 = 1.24 * (l_clamped - 15.0) ** (1.0 / 3.0) - 1.8
    return np.maximum(1.0, d0)


def _ptm_values(pae_row: np.ndarray, d0: float) -> np.ndarray:
    return 1.0 / (1.0 + (pae_row / d0) ** 2.0)


def _ipsae_direction(
    pae_block: np.ndarray,
    pae_cutoff: float,
) -> tuple[float, np.ndarray]:
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


def _validate_pae_shape(pae: np.ndarray, n_tokens: int) -> None:
    if pae.ndim != 2 or pae.shape[0] != pae.shape[1]:
        raise ValueError(f"PAE must be a square 2D array, got shape {pae.shape}")
    if pae.shape[0] != n_tokens:
        raise ValueError(
            f"PAE dimension {pae.shape[0]} does not match token count ({n_tokens})"
        )


def load_pae_npz(path: str | Path) -> np.ndarray:
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
        return np.asarray(data[key], dtype=float)


def parse_chains_arg(spec: str) -> list[ChainSpec]:
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


def _atom_site_table(block: gemmi.cif.Block) -> gemmi.cif.Table:
    table = block.find_mmcif_category("_atom_site")
    if table is None:
        raise ValueError("CIF has no _atom_site loop")
    return table


def _atom_site_loop_column_indices(table: gemmi.cif.Table) -> dict[str, int]:
    """Map ``_atom_site.*`` data names to column index (header-driven, not positional)."""
    names = [tag.split(".", 1)[1] for tag in table.tags]
    return {name: idx for idx, name in enumerate(names)}


def _parse_atom_site_residues(path: str | Path) -> ParsedPredictedCif:
    """Read chain/residue pLDDT from ``_atom_site`` using named columns (Boltz layout)."""
    doc = gemmi.cif.read(str(path))
    block = doc.sole_block()
    table = _atom_site_table(block)
    col = _atom_site_loop_column_indices(table)
    required = ("label_asym_id", "label_seq_id", "label_comp_id", "B_iso_or_equiv", "label_atom_id")
    missing = [k for k in required if k not in col]
    if missing:
        raise ValueError(f"CIF _atom_site missing columns: {missing}")

    has_non_polymer = False
    try:
        st = gemmi.make_structure_from_block(block)
        has_non_polymer = any(
            ent.entity_type == gemmi.EntityType.NonPolymer for ent in st.entities
        )
    except (RuntimeError, ValueError):
        st = None

    chain_first_seen: list[str] = []
    seen_chains: set[str] = set()

    # One pLDDT per residue: prefer CA, else mean over atoms in the residue.
    accum: dict[tuple[str, int], list[float]] = {}
    comp: dict[tuple[str, int], str] = {}
    ca_plddt: dict[tuple[str, int], float] = {}

    for row in table:
        group = row.str(col["group_PDB"])
        if group == "HETATM":
            has_non_polymer = True
        chain_id = row.str(col["label_asym_id"])
        if chain_id not in seen_chains:
            seen_chains.add(chain_id)
            chain_first_seen.append(chain_id)
        seq_raw = row.str(col["label_seq_id"]).strip()
        if not seq_raw or seq_raw in (".", "?"):
            continue
        seq_id = int(seq_raw)
        atom_id = row.str(col["label_atom_id"])
        plddt = float(row.str(col["B_iso_or_equiv"]))
        key = (chain_id, seq_id)
        comp[key] = row.str(col["label_comp_id"])
        accum.setdefault(key, []).append(plddt)
        if atom_id == "CA":
            ca_plddt[key] = plddt

    polymer_chain_order = [cid for cid in chain_first_seen if any(k[0] == cid for k in accum)]
    residues: list[CifResidueRecord] = []
    counts: dict[str, int] = {}
    for chain_id in polymer_chain_order:
        keys = sorted(k for k in accum if k[0] == chain_id)
        counts[chain_id] = len(keys)
        for chain_id_k, seq_id in keys:
            assert chain_id_k == chain_id
            plddt = ca_plddt.get((chain_id, seq_id), float(np.mean(accum[(chain_id, seq_id)])))
            residues.append(
                CifResidueRecord(
                    chain_id=chain_id,
                    seq_id=seq_id,
                    comp_id=comp[(chain_id, seq_id)],
                    plddt=plddt,
                )
            )

    return ParsedPredictedCif(
        residues=tuple(residues),
        chain_order=tuple(polymer_chain_order),
        has_non_polymer=has_non_polymer,
        residue_counts=counts,
    )


def parse_predicted_cif(path: str | Path) -> ParsedPredictedCif:
    return _parse_atom_site_residues(path)


def chain_specs_from_cif(parsed: ParsedPredictedCif) -> list[ChainSpec]:
    return [ChainSpec(cid, parsed.residue_counts[cid]) for cid in parsed.chain_order]


def _resolve_chain_specs(
    parsed: ParsedPredictedCif,
    chain_override: list[ChainSpec] | None,
) -> list[ChainSpec]:
    if chain_override is None:
        specs = chain_specs_from_cif(parsed)
    else:
        specs = chain_override
    for spec in specs:
        cif_len = parsed.residue_counts.get(spec.chain_id)
        if cif_len is None:
            raise TokenAlignmentError(
                f"Chain '{spec.chain_id}' from --chains is not a polymer chain in the CIF "
                f"(polymer chains: {list(parsed.chain_order)})"
            )
        if cif_len != spec.length:
            raise TokenAlignmentError(
                f"Chain '{spec.chain_id}' length {spec.length} from --chains does not match "
                f"CIF residue count {cif_len}"
            )
    if len(specs) < 2:
        raise ValueError("At least two polymer chains are required for ipSAE")
    return specs


def validate_token_alignment(
    n_pae_tokens: int,
    parsed: ParsedPredictedCif,
    chains: list[ChainSpec],
) -> None:
    n_polymer_residues = sum(parsed.residue_counts[cid] for cid in parsed.chain_order)
    n_chain_tokens = sum(c.length for c in chains)
    if n_pae_tokens != n_polymer_residues:
        raise TokenAlignmentError(
            f"PAE has {n_pae_tokens} tokens but CIF has {n_polymer_residues} polymer residues "
            f"(token count exceeds residue count when ligands or per-atom tokens are present)."
        )
    if n_pae_tokens != n_chain_tokens:
        raise TokenAlignmentError(
            f"PAE has {n_pae_tokens} tokens but chain spec accounts for {n_chain_tokens}."
        )


def _plddt_degenerate(values: np.ndarray) -> bool:
    if values.size == 0:
        return True
    return float(np.std(values)) < 1e-6


def _plddt_metrics(
    pae: np.ndarray,
    chains: list[ChainSpec],
    residue_plddt: np.ndarray,
    pae_cutoff: float,
) -> dict[str, Any]:
    if _plddt_degenerate(residue_plddt):
        return {
            "present": False,
            "reason": "B_iso_or_equiv is constant (likely Boltz placeholder, not model pLDDT)",
            "complex_plddt": None,
            "interface_plddt": None,
        }
    slices = _chain_slices(chains)
    chain_ids = [c.chain_id for c in chains]
    n = pae.shape[0]
    token_chain = np.empty(n, dtype=object)
    for cid in chain_ids:
        token_chain[slices[cid]] = cid

    interface_mask = np.zeros(n, dtype=bool)
    for i in range(n):
        for j in range(n):
            if token_chain[i] == token_chain[j]:
                continue
            if pae[i, j] < pae_cutoff:
                interface_mask[i] = True
                interface_mask[j] = True
                break

    out: dict[str, Any] = {
        "present": True,
        "complex_plddt": float(residue_plddt.mean()),
        "interface_plddt": None,
    }
    if interface_mask.any():
        out["interface_plddt"] = float(residue_plddt[interface_mask].mean())
    return out


def compute_ipsae(
    pae: np.ndarray,
    chains: list[ChainSpec],
    pae_cutoff: float = 15.0,
) -> dict[str, Any]:
    """Compute ipSAE from a PAE matrix and contiguous per-chain token layout."""
    if pae_cutoff <= 0:
        raise ValueError("pae_cutoff must be positive")
    n_tokens = sum(c.length for c in chains)
    _validate_pae_shape(pae, n_tokens)
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


def score_predicted_complex(
    pae: np.ndarray,
    parsed: ParsedPredictedCif,
    *,
    chains: list[ChainSpec] | None = None,
    pae_cutoff: float = 15.0,
) -> dict[str, Any]:
    """Align Boltz PAE tokens to the CIF, then compute ipSAE and optional pLDDT metrics."""
    if parsed.has_non_polymer:
        raise TokenAlignmentError(
            "CIF contains a non-polymer entity (e.g. ligand); Boltz per-atom tokenization "
            "cannot be aligned to one-token-per-residue ipSAE without an explicit token map. "
            "Refusing to truncate or pad the PAE."
        )
    specs = _resolve_chain_specs(parsed, chains)
    validate_token_alignment(pae.shape[0], parsed, specs)

    by_chain: dict[str, list[float]] = {cid: [] for cid in parsed.chain_order}
    for rec in parsed.residues:
        by_chain[rec.chain_id].append(rec.plddt)
    residue_plddt = np.concatenate(
        [np.asarray(by_chain[s.chain_id], dtype=float) for s in specs]
    )

    result = compute_ipsae(pae, specs, pae_cutoff=pae_cutoff)
    result["chains"] = [{"chain_id": s.chain_id, "length": s.length} for s in specs]
    result["n_tokens"] = int(pae.shape[0])
    plddt_block = _plddt_metrics(pae, specs, residue_plddt, pae_cutoff)
    result["plddt"] = plddt_block
    if not plddt_block["present"]:
        warnings.warn(plddt_block["reason"], stacklevel=2)
    return result


def score_from_paths(
    pae_npz: str | Path,
    predicted_cif: str | Path,
    *,
    chains: list[ChainSpec] | None = None,
    pae_cutoff: float = 15.0,
) -> dict[str, Any]:
    pae = load_pae_npz(pae_npz)
    parsed = parse_predicted_cif(predicted_cif)
    return score_predicted_complex(pae, parsed, chains=chains, pae_cutoff=pae_cutoff)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute ipSAE from Boltz pae.npz and _predicted.cif"
    )
    parser.add_argument("pae_npz", help="Path to PAE npz (e.g. Boltz pae.npz)")
    parser.add_argument("predicted_cif", help="Path to Boltz *_predicted.cif")
    parser.add_argument(
        "--chains",
        default=None,
        help="Optional CHAIN:LENGTH override in PAE token order (default: polymer chains from CIF)",
    )
    parser.add_argument(
        "--pae-cutoff",
        type=float,
        default=15.0,
        help="PAE cutoff in angstroms (default 15)",
    )
    args = parser.parse_args(argv)
    chain_override = parse_chains_arg(args.chains) if args.chains else None
    result = score_from_paths(
        args.pae_npz,
        args.predicted_cif,
        chains=chain_override,
        pae_cutoff=args.pae_cutoff,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
