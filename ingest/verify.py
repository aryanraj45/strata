#!/usr/bin/env python3
"""Acceptance checks for the ingested columnar store.

Reads the Parquet output with DuckDB rather than with the writer's own library:
if an independent engine can query it, the store is a real contract that tracks B
and C can build against.

    .venv/bin/python ingest/verify.py --store store --data data
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def btc_to_sats(text: str) -> int:
    """Decimal string to integer satoshis, without touching a float.

    This check exists to prove the ingest never rounds through f64, so it must
    not round through one itself: `float("0.1") * 1e8` is 10000000.000000002.
    """
    whole, _, frac = text.strip().partition(".")
    frac = (frac + "00000000")[:8]
    return int(whole or 0) * 100_000_000 + int(frac or 0)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    args = ap.parse_args()

    tx_path = (args.store / "tx_l1.parquet").resolve()
    l2_path = (args.store / "net_l2.parquet").resolve()
    truth = json.loads((args.data / "ground_truth.json").read_text())
    manifest = json.loads((args.store / "manifest.json").read_text())

    db = duckdb.connect()
    # Registered through the relation API rather than interpolated into SQL: a
    # store path containing a quote would otherwise produce a confusing binder
    # error at best. DuckDB cannot bind parameters inside CREATE VIEW, so this
    # is the parameterised equivalent.
    db.read_parquet(str(tx_path)).create_view("tx")
    db.read_parquet(str(l2_path)).create_view("l2")

    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731

    # 1 — an engine that did not write the files can read them
    n_tx = one("SELECT count(*) FROM tx")
    n_l2 = one("SELECT count(*) FROM l2")
    check(
        "store is readable by an independent engine",
        n_tx > 0 and n_l2 > 0,
        f"DuckDB {duckdb.__version__} read {n_tx:,} tx / {n_l2:,} observations",
    )

    # 2 — no transaction was lost or invented
    check(
        "transaction count matches ground truth",
        n_tx == truth["n_transactions"],
        f"store {n_tx:,} vs truth {truth['n_transactions']:,}",
    )

    # 3 — every transaction still balances, now in integer satoshis
    unbalanced = one("SELECT count(*) FROM tx WHERE total_in <> total_out + fee")
    check(
        "value conservation survives ingest",
        unbalanced == 0,
        f"{n_tx:,} transactions, {unbalanced} unbalanced (integer satoshis)",
    )

    # 4 — array columns kept their pairing with the address arrays
    mismatched = one(
        "SELECT count(*) FROM tx WHERE len(input_addresses) <> len(input_amounts) "
        "OR len(output_addresses) <> len(output_amounts)"
    )
    check(
        "array columns stayed aligned",
        mismatched == 0,
        f"{mismatched} transactions with address/amount length mismatch",
    )

    # 5 — deduplication actually deduplicated
    dupes = one(
        "SELECT count(*) FROM (SELECT txid, ts_micro, src_ip FROM l2 "
        "GROUP BY 1,2,3 HAVING count(*) > 1)"
    )
    check(
        "no duplicate observations",
        dupes == 0,
        f"ingested 3 formats of the same data; {manifest['duplicate_records']:,} duplicates dropped",
    )

    # 6 — arrival_rank must be a dense 1..n ordering by arrival time
    bad_rank = one(
        """
        WITH expected AS (
          SELECT txid, ts_micro, arrival_rank,
                 row_number() OVER (PARTITION BY txid ORDER BY ts_micro) AS want
          FROM l2
        )
        SELECT count(*) FROM expected WHERE arrival_rank <> want
        """
    )
    check(
        "arrival_rank is dense and time-ordered",
        bad_rank == 0,
        f"{n_l2:,} observations, {bad_rank} mis-ranked",
    )

    # 7 — the fusion signal must survive the whole ingest path, not just the generator
    origin = truth["tx_origin_actor"]
    actor_ips = {c["actor_id"]: set(c["origin_ips"]) for c in truth["clusters"]}
    firsts = db.execute(
        "SELECT txid, src_ip FROM l2 WHERE arrival_rank = 1"
    ).fetchall()
    if not firsts:
        check("fusion signal survives ingest", False, "no rank-1 observations in the store")
        firsts, hits, rate = [], 0, 0.0
    else:
        hits = sum(1 for txid, ip in firsts if ip in actor_ips.get(origin.get(txid, ""), ()))
        rate = hits / len(firsts)
    coverage = truth["coverage"]
    if firsts:
        check(
        "fusion signal survives ingest",
        0.55 <= rate <= coverage + 0.12,
        f"{hits}/{len(firsts)} rank-1 sources are the true origin = {rate:.1%} "
        f"(coverage {coverage:.0%})",
        )

    # 8 — values round-trip against the original evidence
    src = json.loads((args.data / "strata.json").read_text())
    by_txid = {}
    for rec in src:
        by_txid.setdefault(rec["txid"], rec)
    sample = db.execute(
        "SELECT txid, total_in, total_out, fee, input_addresses FROM tx USING SAMPLE 60 ROWS"
    ).fetchall()
    drift = []
    for txid, tin, tout, fee, in_addrs in sample:
        rec = by_txid[txid]
        want_in = sum(btc_to_sats(a) for a in rec["input_amounts"])
        want_out = sum(btc_to_sats(a) for a in rec["output_amounts"])
        if tin != want_in or tout != want_out or fee != btc_to_sats(rec["fee"]):
            drift.append(txid)
        if list(in_addrs) != rec["input_addresses"]:
            drift.append(txid)
    check(
        "values round-trip against source evidence",
        not drift,
        f"sampled {len(sample)} transactions, {len(drift)} with drift",
    )

    # 9 — chain of custody
    sources = manifest["sources"]
    # A source that has since moved cannot be verified, which is a reportable
    # state rather than a crash -- the manifest records what was ingested, and
    # evidence files are not expected to stay put forever.
    missing = [s for s in sources if not Path(s["path"]).exists()]
    bad_hash = [
        s for s in sources
        if Path(s["path"]).exists() and sha256(Path(s["path"])) != s["sha256"]
    ]
    check(
        "chain of custody: source hashes verify",
        bool(sources) and not bad_hash and not missing,
        f"{len(sources)} source files, {len(bad_hash)} hash mismatches"
        + (f", {len(missing)} no longer present" if missing else ""),
    )

    # 10 — geo provenance is recorded, never silently assumed
    provenance = db.execute(
        "SELECT country_source, asn_source, count(*) FROM l2 GROUP BY 1,2 ORDER BY 3 DESC"
    ).fetchall()
    labelled = one(
        "SELECT count(*) FROM l2 WHERE country_source IN ('db','record') "
        "AND asn_source IN ('db','record')"
    )
    check(
        "geo provenance labelled per field on every row",
        labelled == n_l2,
        ", ".join(f"country={c}/asn={a}: {n:,}" for c, a, n in provenance),
    )

    # 11 — country and ASN actually populated, so UC-7 has something to work with
    countries = one("SELECT count(DISTINCT geo_country) FROM l2")
    asns = one("SELECT count(DISTINCT asn) FROM l2")
    check(
        "geo/ASN populated with real variety",
        countries >= 3 and asns >= 3,
        f"{countries} distinct countries, {asns} distinct ASNs",
    )

    width = max(len(name) for _, name, _ in results)
    print()
    for status, name, detail in results:
        mark = "\033[32m✔\033[0m" if status == PASS else "\033[31m✘\033[0m"
        print(f"  {mark} {name.ljust(width)}  {detail}")

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n  {len(results) - len(failed)}/{len(results)} checks passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
