#!/usr/bin/env python3
"""Build the entity/transaction graph and recover peeling chains.

    .venv/bin/python graph/run.py --store store

The problem statement asks for a graph "linking IPs, wallets, and transactions".
That is what this stage produces, and it also does something clustering could
not: recovering peeling chains as paths, which folds a fragmented set of
single-input hops back into one entity.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))

from strata_graph import build_graph, detect_chains

TX_COLS = [
    "txid", "ts_micro", "input_addresses", "output_addresses",
    "input_amounts", "output_amounts", "fee", "script_type",
    "n_inputs", "n_outputs",
]


def load(store: Path):
    db = duckdb.connect()
    db.read_parquet(str((store / "tx_l1.parquet").resolve())).create_view("tx")
    db.read_parquet(str((store / "net_l2.parquet").resolve())).create_view("l2")
    db.read_parquet(str((store / "clusters.parquet").resolve())).create_view("cl")
    rows = db.execute(
        f"SELECT {', '.join(TX_COLS)} FROM tx ORDER BY ts_micro, txid"
    ).fetchall()
    txs = [dict(zip(TX_COLS, r)) for r in rows]
    clusters = dict(db.execute("SELECT address, cluster_id FROM cl").fetchall())
    return db, txs, clusters


def main() -> int:
    ap = argparse.ArgumentParser(description="STRATA graph stage")
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--min-chain", type=int, default=4, help="shortest run counted as a chain")
    args = ap.parse_args()

    db, txs, clusters = load(args.store)

    chains = detect_chains(txs, min_length=args.min_chain)
    entities, merged = build_graph(db, txs, clusters, chains, args.store)

    hops = sorted((c.hops for c in chains), reverse=True)
    laundering = [c for c in chains if c.laundering_like]
    print(f"  transactions      {len(txs):>7,}")
    print(f"  chains recovered  {len(chains):>7,}"
          + (f"   longest {hops[0]} hops" if hops else ""))
    print(f"  laundering-like   {len(laundering):>7,}   consistent small peels over many hops")
    print(f"  addresses merged  {merged:>7,}   by chain evidence")
    print(f"  entities          {entities:>7,}   (was {len(set(clusters.values())):,} clusters)")
    print(f"\n  written to {args.store}/entities.parquet, chains.parquet, "
          f"graph_nodes.parquet, graph_edges.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
