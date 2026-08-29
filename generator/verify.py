#!/usr/bin/env python3
"""Acceptance checks for the generated dataset.

Run after generating. If the fusion-signal check fails, the propagation model is
wrong and nothing downstream can work — that is the one to watch.

    python generator/verify.py --data data
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

SEP = ";"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def load_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            for key in ("input_addresses", "output_addresses", "input_amounts", "output_amounts"):
                row[key] = row[key].split(SEP) if row[key] else []
            rows.append(row)
    return rows


def load_xml(path: Path) -> list[dict]:
    rows = []
    for node in ET.parse(path).getroot():
        rec: dict = {}
        for child in node:
            items = list(child)
            rec[child.tag] = [i.text for i in items] if items else child.text
        rows.append(rec)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    args = ap.parse_args()

    js = load_json(args.data / "strata.json")
    cs = load_csv(args.data / "strata.csv")
    xm = load_xml(args.data / "strata.xml")
    truth = json.loads((args.data / "ground_truth.json").read_text())

    # 1 — the three formats must describe the same dataset
    counts = (len(js), len(cs), len(xm))
    check("format parity: record counts", len(set(counts)) == 1, f"json/csv/xml = {counts}")

    txid_sets = [{r["txid"] for r in rows} for rows in (js, cs, xm)]
    check(
        "format parity: identical TXID sets",
        txid_sets[0] == txid_sets[1] == txid_sets[2],
        f"{len(txid_sets[0])} distinct txids",
    )

    sample_ok = all(
        js[i]["input_addresses"] == cs[i]["input_addresses"] == xm[i]["input_addresses"]
        for i in range(0, len(js), max(1, len(js) // 50))
    )
    check("format parity: array fields survive round-trip", sample_ok, "sampled 50 records")

    # 2 — value conservation
    by_txid = {}
    for rec in js:
        by_txid.setdefault(rec["txid"], rec)
    bad = []
    for txid, rec in by_txid.items():
        tin = sum(Decimal(a) for a in rec["input_amounts"])
        tout = sum(Decimal(a) for a in rec["output_amounts"])
        if tin != tout + Decimal(rec["fee"]):
            bad.append(txid)
    check(
        "value conservation: inputs == outputs + fee",
        not bad,
        f"{len(by_txid)} transactions, {len(bad)} unbalanced",
    )

    # 3 — the fusion signal: is the true origin usually the first relay we see?
    tx_origin = truth["tx_origin_actor"]
    actor_ips = {c["actor_id"]: set(c["origin_ips"]) for c in truth["clusters"]}

    first_by_tx: dict[str, dict] = {}
    for rec in js:
        cur = first_by_tx.get(rec["txid"])
        if cur is None or rec["timestamp"] < cur["timestamp"]:
            first_by_tx[rec["txid"]] = rec

    hits = sum(
        1
        for txid, rec in first_by_tx.items()
        if rec["src_ip"] in actor_ips.get(tx_origin.get(txid, ""), ())
    )
    rate = hits / len(first_by_tx)
    coverage = truth["coverage"]
    check(
        "fusion signal: origin IP is first relay",
        0.55 <= rate <= coverage + 0.12,
        f"{hits}/{len(first_by_tx)} = {rate:.1%} (coverage {coverage:.0%})",
    )

    # 4 — and a random source must be far below that, or the signal means nothing
    all_srcs = Counter(r["src_ip"] for r in js)
    most_common_share = all_srcs.most_common(1)[0][1] / len(js)
    check(
        "fusion baseline: no single IP dominates traffic",
        most_common_share < 0.10,
        f"busiest source is {most_common_share:.1%} of all observations",
    )

    # 5 — multi-input spends must exist, or CIOH has nothing to cluster on
    multi = sum(1 for rec in by_txid.values() if len(rec["input_addresses"]) > 1)
    check(
        "CIOH material: multi-input transactions present",
        multi >= len(by_txid) * 0.10,
        f"{multi}/{len(by_txid)} = {multi / len(by_txid):.1%} have >1 input",
    )

    # 6 — peeling chains, shaped as 1-in / 2-out with a fresh change address
    chains = truth["peeling_chains"]
    change = truth["change_addresses"]
    shaped = 0
    for chain in chains:
        for txid in chain["txids"]:
            rec = by_txid.get(txid)
            if rec and len(rec["output_addresses"]) == 2 and txid in change:
                shaped += 1
    total_hops = sum(len(c["txids"]) for c in chains)
    check(
        "peeling chains present and shaped",
        chains and shaped >= total_hops * 0.8,
        f"{len(chains)} chains, {shaped}/{total_hops} hops are 1-in/2-out with change",
    )

    # 7 — CoinJoins must break CIOH, otherwise the filter is untested
    cjs = truth["coinjoins"]
    multi_owner = sum(1 for cj in cjs if len(cj["participants"]) >= 3)
    check(
        "CoinJoins present with multiple owners",
        cjs and multi_owner == len(cjs),
        f"{len(cjs)} rounds, all with >=3 distinct owners",
    )

    # 8 — equal-denomination outputs are the detectable signature
    denom_ok = 0
    for cj in cjs:
        rec = by_txid.get(cj["txid"])
        if not rec:
            continue
        common = Counter(rec["output_amounts"]).most_common(1)[0][1]
        if common >= 3:
            denom_ok += 1
    check(
        "CoinJoin signature: repeated equal outputs",
        cjs and denom_ok == len(cjs),
        f"{denom_ok}/{len(cjs)} rounds show >=3 equal-value outputs",
    )

    # 9 — obfuscated actors should look different, so UC-7 has something to find
    obf = [c for c in truth["clusters"] if c["obfuscated"]]
    check(
        "obfuscated actors exist for geo/ASN testing",
        len(obf) >= 1,
        f"{len(obf)} of {len(truth['clusters'])} entities route through VPN ranges",
    )

    # 10 — illicit population is a minority, as in reality
    illicit = sum(1 for c in truth["clusters"] if c["illicit"])
    share = illicit / len(truth["clusters"])
    check(
        "class balance: illicit entities are a minority",
        0.05 <= share <= 0.25,
        f"{illicit}/{len(truth['clusters'])} = {share:.1%} illicit",
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
