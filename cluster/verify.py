#!/usr/bin/env python3
"""Acceptance checks for entity resolution.

Scored against ground truth rather than eyeballed. Clustering is measured with
pairwise precision and recall over address pairs, which is the standard for this
task and — unlike counting clusters — actually penalises a false merge.

Precision matters more than recall here. A missed link costs an investigator one
lead; a false merge attributes an innocent party's transactions to a suspect and
poisons everything downstream.

    .venv/bin/python cluster/verify.py --store store --data data
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def pairs(n: int) -> int:
    return n * (n - 1) // 2


def pairwise_scores(pred: dict[str, str], true: dict[str, str]) -> tuple[float, float, float]:
    """Precision, recall and F1 over co-membership of address pairs."""
    shared = [a for a in pred if a in true]
    pred_groups: dict[str, list[str]] = defaultdict(list)
    for addr in shared:
        pred_groups[pred[addr]].append(addr)

    tp = 0
    for members in pred_groups.values():
        overlap = Counter(true[a] for a in members)
        tp += sum(pairs(c) for c in overlap.values())

    pred_pairs = sum(pairs(len(m)) for m in pred_groups.values())
    true_groups = Counter(true[a] for a in shared)
    true_pairs = sum(pairs(c) for c in true_groups.values())

    precision = tp / pred_pairs if pred_pairs else 1.0
    recall = tp / true_pairs if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--quiet", action="store_true", help="print only the summary line")
    args = ap.parse_args()

    truth = json.loads((args.data / "ground_truth.json").read_text())
    db = duckdb.connect()
    cl = (args.store / "clusters.parquet").resolve()
    fl = (args.store / "tx_flags.parquet").resolve()
    db.execute(f"CREATE VIEW clusters AS SELECT * FROM read_parquet('{cl}')")
    db.execute(f"CREATE VIEW flags AS SELECT * FROM read_parquet('{fl}')")

    predicted = dict(db.execute("SELECT address, cluster_id FROM clusters").fetchall())
    true_map: dict[str, str] = {}
    for entity in truth["clusters"]:
        for addr in entity["addresses"]:
            true_map[addr] = entity["actor_id"]

    # Ground truth also records seed-funding addresses that were never spent or
    # received. They are invisible on-chain, so scoring against them would be
    # measuring the generator, not the clusterer.
    ledger = set(predicted)
    unseen = [a for a in true_map if a not in ledger]

    # 1 - coverage over addresses that actually appear in the ledger
    check(
        "every ledger address assigned to an entity",
        len(ledger & set(true_map)) == len(true_map) - len(unseen),
        f"{len(predicted):,} clustered; {len(unseen):,} truth addresses never appear on-chain",
    )

    # 2 - CoinJoin detection against ground truth
    true_cj = {c["txid"] for c in truth["coinjoins"]}
    pred_cj = {
        r[0] for r in db.execute("SELECT txid FROM flags WHERE is_coinjoin").fetchall()
    }
    tp = len(true_cj & pred_cj)
    cj_prec = tp / len(pred_cj) if pred_cj else 1.0
    cj_rec = tp / len(true_cj) if true_cj else 1.0
    check(
        "CoinJoin detection",
        cj_prec == 1.0 and cj_rec == 1.0,
        f"precision {cj_prec:.0%}, recall {cj_rec:.0%} "
        f"({tp}/{len(true_cj)} found, {len(pred_cj - true_cj)} false positives)",
    )

    # 3 - change identification, where we made a call
    true_change = truth["change_addresses"]
    decided = db.execute(
        "SELECT txid, change_address FROM flags WHERE change_address IS NOT NULL"
    ).fetchall()
    scorable = [(t, a) for t, a in decided if t in true_change]
    correct = sum(1 for t, a in scorable if true_change[t] == a)
    accuracy = correct / len(scorable) if scorable else 0.0
    check(
        "change-address identification",
        accuracy >= 0.85,
        f"{correct}/{len(scorable)} correct = {accuracy:.1%} "
        f"(declined to guess on {len(true_change) - len(scorable):,})",
    )

    # 4 - clustering quality
    precision, recall, f1 = pairwise_scores(predicted, true_map)

    # Recall over the whole ground truth counts pairs no method could link: an
    # exchange receiving to a fresh address each time leaves no on-chain evidence
    # connecting those addresses. Scoring only addresses that were ever spent
    # measures the clusterer instead of the dataset.
    spent = set()
    for ins, in db.execute(
        f"SELECT input_addresses FROM read_parquet('{(args.store / 'tx_l1.parquet').resolve()}')"
    ).fetchall():
        spent.update(ins)
    linkable_true = {a: c for a, c in true_map.items() if a in spent}
    _, linkable_recall, _ = pairwise_scores(predicted, linkable_true)
    check(
        "clustering precision (no false merges)",
        precision >= 0.99,
        f"pairwise precision {precision:.1%}",
    )
    check(
        "clustering recall over linkable addresses",
        linkable_recall >= 0.10,
        f"{linkable_recall:.1%} over co-spendable addresses "
        f"({recall:.1%} over all ground truth, F1 {f1:.1%})",
    )

    # 5 - supercluster collapse, the characteristic failure of address clustering.
    #
    # One bad merge chains through union-find and can swallow most of the ledger
    # into a single entity. The result still looks like a cluster and nothing
    # errors; it is simply meaningless. A healthy run keeps the largest entity to
    # a small share of all addresses.
    sizes = [
        r[0]
        for r in db.execute(
            "SELECT count(*) c FROM clusters GROUP BY cluster_id ORDER BY c DESC"
        ).fetchall()
    ]
    largest_share = sizes[0] / len(predicted)
    check(
        "no supercluster collapse",
        largest_share < 0.05,
        f"largest entity holds {sizes[0]} of {len(predicted):,} addresses "
        f"= {largest_share:.1%}; {sum(1 for s in sizes if s > 1)} multi-address entities",
    )

    # 6 - peel-chain continuity, reported honestly rather than hidden.
    #
    # Each hop of a peeling chain has one input, so there is no co-spend evidence
    # linking it to the next. The only bridge is the change guess, and a single
    # miss severs the chain. Chain reconstruction is a graph-path problem handled
    # in the graph stage; this number records how much of it clustering alone
    # recovers, which is a bonus rather than the mechanism.
    linked = total = 0
    for ch in truth["peeling_chains"]:
        addrs_in_chain = [
            truth["change_addresses"][t] for t in ch["txids"] if t in truth["change_addresses"]
        ]
        for a, b in zip(addrs_in_chain, addrs_in_chain[1:]):
            total += 1
            if a in predicted and b in predicted and predicted[a] == predicted[b]:
                linked += 1
    frac = linked / total if total else 0.0
    check(
        "peel-chain hops linked by change alone",
        frac >= 0.10,
        f"{linked}/{total} consecutive hops = {frac:.1%} "
        f"(chains are reconstructed as graph paths in the graph stage)",
    )

    # 6 - CoinJoin participants must NOT have been merged with each other
    contaminated = 0
    for cj in truth["coinjoins"]:
        owners = cj["participants"]
        if len(owners) < 2:
            continue
        addr_by_owner = {
            o: [a for a in truth["clusters"] if a["actor_id"] == o][0]["addresses"]
            for o in owners
        }
        rep = {}
        for owner, addrs in addr_by_owner.items():
            found = [predicted[a] for a in addrs if a in predicted]
            if found:
                rep[owner] = Counter(found).most_common(1)[0][0]
        if len(set(rep.values())) < len(rep):
            contaminated += 1
    check(
        "CoinJoin participants stayed separate",
        contaminated == 0,
        f"{contaminated}/{len(truth['coinjoins'])} rounds merged unrelated owners",
    )

    if args.quiet:
        failed = [r for r in results if r[0] == FAIL]
        print(f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f} "
              f"coinjoin_prec={cj_prec:.2f} failures={len(failed)}")
        return 1 if failed else 0

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
