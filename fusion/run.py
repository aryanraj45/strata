#!/usr/bin/env python3
"""Correlate network-layer observations with blockchain-layer entities.

    .venv/bin/python fusion/run.py --store store

Produces a ranked list of candidate origins, each with the count it rests on and
the probability of seeing that count by chance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))

from strata_fusion import attribute, best_per_entity, geo_concentration


def main() -> int:
    ap = argparse.ArgumentParser(description="STRATA dual-layer fusion")
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--min-transactions", type=int, default=5,
                    help="smallest sample the significance test is applied to")
    args = ap.parse_args()

    db = duckdb.connect()
    for name, file in [("tx", "tx_l1.parquet"), ("l2", "net_l2.parquet"),
                       ("entities", "entities.parquet")]:
        db.read_parquet(str((args.store / file).resolve())).create_view(name)

    tx_entity = dict(
        db.execute(
            "SELECT t.txid, e.entity_id FROM tx t "
            "JOIN entities e ON e.address = t.input_addresses[1]"
        ).fetchall()
    )
    observations = db.execute(
        "SELECT txid, src_ip, arrival_rank, asn, geo_country FROM l2"
    ).fetchall()

    leads = attribute(observations, tx_entity, min_transactions=args.min_transactions)
    best = best_per_entity(leads)
    geo = geo_concentration(observations, tx_entity)

    rows = []
    for entity, lead in best.items():
        g = geo.get(entity, {})
        rows.append((
            lead.entity_id, lead.ip, lead.asn, lead.country,
            lead.hits, lead.transactions, lead.weight, lead.baseline,
            lead.p_value, lead.adjusted_p, lead.confidence, lead.significant,
            g.get("n_countries", 0), g.get("n_asns", 0),
            g.get("top_asn_share", 0.0),
        ))

    out = args.store / "leads.parquet"
    db.execute(
        "CREATE OR REPLACE TABLE leads ("
        "entity_id VARCHAR, ip VARCHAR, asn UINTEGER, country VARCHAR, "
        "hits INT, transactions INT, rank_weight DOUBLE, baseline DOUBLE, "
        "p_value DOUBLE, adjusted_p DOUBLE, confidence DOUBLE, significant BOOLEAN, "
        "n_countries INT, n_asns INT, top_asn_share DOUBLE)"
    )
    if rows:
        db.executemany(f"INSERT INTO leads VALUES ({', '.join('?' * 15)})", rows)
    db.execute(f"COPY leads TO '{out.resolve()}' (FORMAT PARQUET)")

    significant = [l for l in best.values() if l.significant]
    print(f"  entities scored     {len(best):>7,}")
    print(f"  candidate pairs     {len(leads):>7,}")
    print(f"  significant leads   {len(significant):>7,}   Bonferroni-adjusted p <= 0.01")
    if significant:
        top = sorted(significant, key=lambda l: l.adjusted_p)[:5]
        print(f"\n  {'entity':<16} {'candidate origin':<18} {'hits':>10}  {'p':>9}")
        for l in top:
            print(f"  {l.entity_id:<16} {l.ip:<18} {l.hits:>4}/{l.transactions:<5} "
                  f"{l.adjusted_p:>9.2e}")
    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
