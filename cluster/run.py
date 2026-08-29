#!/usr/bin/env python3
"""Resolve addresses into entities.

    .venv/bin/python cluster/run.py --store store

Stages run in a fixed order. CoinJoin detection must precede clustering: those
transactions violate the ownership assumption CIOH depends on, and including
them merges unrelated people into one entity with no visible failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))

from strata_cluster import classify_coinjoin, cluster, identify_change


def load_transactions(store: Path):
    db = duckdb.connect()
    path = (store / "tx_l1.parquet").resolve()
    rows = db.execute(
        f"""
        SELECT txid, ts_micro, input_addresses, output_addresses,
               input_amounts, output_amounts, fee, script_type,
               n_inputs, n_outputs
        FROM read_parquet('{path}')
        ORDER BY ts_micro, txid
        """
    ).fetchall()
    cols = [
        "txid", "ts_micro", "input_addresses", "output_addresses",
        "input_amounts", "output_amounts", "fee", "script_type",
        "n_inputs", "n_outputs",
    ]
    return [dict(zip(cols, r)) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="STRATA entity resolution")
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--no-coinjoin-filter", action="store_true",
                    help="disable the CoinJoin filter (for demonstrating why it exists)")
    ap.add_argument("--no-change", action="store_true", help="skip change-address merging")
    args = ap.parse_args()

    txs = load_transactions(args.store)

    # Stage 1 - CoinJoin detection, before anything is merged.
    n_cj = 0
    for tx in txs:
        verdict = classify_coinjoin(tx["input_addresses"], tx["output_amounts"])
        tx["is_coinjoin"] = verdict.is_coinjoin and not args.no_coinjoin_filter
        tx["coinjoin_reason"] = verdict.reason
        tx["coinjoin_denomination"] = verdict.denomination
        n_cj += bool(verdict.is_coinjoin)

    # Stage 2 - change identification, walking forward in time so "previously
    # seen" means what it says.
    seen: set[str] = set()
    n_change = 0
    conf_total = 0.0
    for tx in txs:
        guess = identify_change(
            tx["output_addresses"], tx["output_amounts"], tx["script_type"], seen
        )
        tx["change_address"] = (
            tx["output_addresses"][guess.index] if guess.index is not None else None
        )
        tx["change_confidence"] = guess.confidence
        if guess.index is not None:
            n_change += 1
            conf_total += guess.confidence
        seen.update(tx["input_addresses"])
        seen.update(tx["output_addresses"])

    # Stage 3 - CIOH.
    uf = cluster(txs, use_change=not args.no_change)
    groups = uf.groups()

    out = args.store / "clusters.parquet"
    db = duckdb.connect()
    db.execute("CREATE TABLE clusters (address VARCHAR, cluster_id VARCHAR, cluster_size INT)")
    db.executemany(
        "INSERT INTO clusters VALUES (?, ?, ?)",
        [
            (addr, f"C-{root[:12]}", len(members))
            for root, members in groups.items()
            for addr in members
        ],
    )
    db.execute(f"COPY clusters TO '{out.resolve()}' (FORMAT PARQUET)")

    flags = args.store / "tx_flags.parquet"
    db.execute(
        "CREATE TABLE flags (txid VARCHAR, is_coinjoin BOOLEAN, coinjoin_reason VARCHAR, "
        "change_address VARCHAR, change_confidence DOUBLE)"
    )
    db.executemany(
        "INSERT INTO flags VALUES (?, ?, ?, ?, ?)",
        [
            (t["txid"], t["is_coinjoin"], t["coinjoin_reason"],
             t["change_address"], t["change_confidence"])
            for t in txs
        ],
    )
    db.execute(f"COPY flags TO '{flags.resolve()}' (FORMAT PARQUET)")

    sizes = sorted((len(m) for m in groups.values()), reverse=True)
    multi = [s for s in sizes if s > 1]
    print(f"  transactions        {len(txs):>7,}")
    print(f"  coinjoins detected  {n_cj:>7,}")
    print(f"  change resolved     {n_change:>7,}  (mean confidence "
          f"{conf_total / max(n_change, 1):.2f})")
    print(f"  addresses           {len(uf.parent):>7,}")
    print(f"  clusters            {len(groups):>7,}")
    print(f"  multi-address       {len(multi):>7,}  (largest {sizes[0] if sizes else 0})")
    print(f"\n  written to {out} and {flags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
