#!/usr/bin/env python3
"""Acceptance checks for the graph stage.

Two distinct claims are scored separately, because conflating them is the easy
mistake here:

  - that a run of hops belongs to *one entity*, which is what unblocks
    attribution downstream, and
  - that the run is *laundering*, which is an accusation and needs more than
    shape to support.

    .venv/bin/python graph/verify.py --store store --data data
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
from strata_graph import subgraph_for  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def pairwise(pred: dict[str, str], true: dict[str, str]) -> tuple[float, float]:
    shared = [a for a in pred if a in true]
    groups: dict[str, list[str]] = defaultdict(list)
    for addr in shared:
        groups[pred[addr]].append(addr)
    pairs = lambda n: n * (n - 1) // 2  # noqa: E731
    tp = sum(pairs(c) for m in groups.values() for c in Counter(true[a] for a in m).values())
    pp = sum(pairs(len(m)) for m in groups.values())
    tt = sum(pairs(c) for c in Counter(true[a] for a in shared).values())
    return (tp / pp if pp else 1.0), (tp / tt if tt else 1.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    args = ap.parse_args()

    truth = json.loads((args.data / "ground_truth.json").read_text())
    db = duckdb.connect()
    for name, file in [
        ("tx", "tx_l1.parquet"), ("l2", "net_l2.parquet"),
        ("entities", "entities.parquet"), ("chains", "chains.parquet"),
        ("graph_nodes", "graph_nodes.parquet"), ("graph_edges", "graph_edges.parquet"),
    ]:
        db.read_parquet(str((args.store / file).resolve())).create_view(name)

    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    true_map = {a: c["actor_id"] for c in truth["clusters"] for a in c["addresses"]}
    kind = {c["actor_id"]: c["kind"] for c in truth["clusters"]}

    entities = dict(db.execute("SELECT address, entity_id FROM entities").fetchall())
    clusters = dict(db.execute("SELECT address, cluster_id FROM entities").fetchall())

    # 1 - chain evidence must not cost precision
    c_prec, c_rec = pairwise(clusters, true_map)
    e_prec, e_rec = pairwise(entities, true_map)
    check(
        "entity precision holds after chain merging",
        e_prec >= 0.95,
        f"{e_prec:.1%} (clustering alone was {c_prec:.1%})",
    )
    check(
        "chain evidence increases recall",
        e_rec > c_rec,
        f"{e_rec:.1%}, up from {c_rec:.1%} on clustering alone",
    )

    # 2 - the point of this stage: entities large enough to attribute
    db.execute(
        "CREATE VIEW txe AS SELECT t.txid, e.entity_id "
        "FROM tx t JOIN entities e ON e.address = t.input_addresses[1]"
    )
    total = one("SELECT count(*) FROM txe")
    covered = one(
        "SELECT count(*) FROM txe WHERE entity_id IN "
        "(SELECT entity_id FROM txe GROUP BY 1 HAVING count(*) >= 10)"
    )
    big = one("SELECT count(*) FROM (SELECT entity_id FROM txe GROUP BY 1 HAVING count(*) >= 10)")
    share = covered / total
    check(
        "entities are large enough for a statistical test",
        share >= 0.15,
        f"{big} entities have >=10 transactions, covering {covered:,}/{total:,} = {share:.0%}",
    )

    # 3 - and specifically for the actors that matter
    rows = db.execute(
        "SELECT t.input_addresses[1], e.entity_id "
        "FROM tx t JOIN entities e ON e.address = t.input_addresses[1]"
    ).fetchall()
    per = defaultdict(Counter)
    for addr, eid in rows:
        if addr in true_map:
            per[true_map[addr]][eid] += 1
    illicit = {"ransomware", "darknet_market", "extortion", "launderer"}
    largest = {
        a: c.most_common(1)[0][1] for a, c in per.items() if kind.get(a) in illicit and c
    }
    workable = sum(1 for n in largest.values() if n >= 10)
    check(
        "illicit actors have a workable entity",
        workable >= len(largest) * 0.5,
        f"{workable}/{len(largest)} illicit actors have an entity with >=10 transactions "
        f"(largest {max(largest.values(), default=0)})",
    )

    # 4 - planted chains recovered
    true_hops = {t for ch in truth["peeling_chains"] for t in ch["txids"]}
    found_hops = {r[0] for r in db.execute("SELECT DISTINCT txid FROM chains").fetchall()}
    recall = len(true_hops & found_hops) / len(true_hops) if true_hops else 0.0
    check(
        "planted peel hops recovered",
        recall >= 0.70,
        f"{len(true_hops & found_hops)}/{len(true_hops)} = {recall:.1%}",
    )

    # 5 - the accusation, scored separately from the linking
    laundering = {
        r[0] for r in db.execute(
            "SELECT DISTINCT txid FROM chains WHERE laundering_like"
        ).fetchall()
    }
    l_prec = len(laundering & true_hops) / len(laundering) if laundering else 1.0
    l_rec = len(laundering & true_hops) / len(true_hops) if true_hops else 0.0
    check(
        "laundering classification is not just chain shape",
        l_prec >= 0.80,
        f"precision {l_prec:.1%}, recall {l_rec:.1%} "
        f"({len(laundering)} hops flagged of {len(found_hops)} chained)",
    )

    # 6 - graph integrity: every edge must land on a node that exists
    dangling = one(
        "SELECT count(*) FROM graph_edges e "
        "WHERE NOT EXISTS (SELECT 1 FROM graph_nodes n WHERE n.id = e.source) "
        "   OR NOT EXISTS (SELECT 1 FROM graph_nodes n WHERE n.id = e.target)"
    )
    n_nodes = one("SELECT count(*) FROM graph_nodes")
    n_edges = one("SELECT count(*) FROM graph_edges")
    check(
        "graph has no dangling edges",
        dangling == 0,
        f"{n_nodes:,} nodes, {n_edges:,} edges, {dangling} dangling",
    )

    # 7 - the PS asks for a graph linking IPs, wallets and transactions
    labels = dict(db.execute("SELECT label, count(*) FROM graph_nodes GROUP BY 1").fetchall())
    edge_labels = dict(db.execute("SELECT label, count(*) FROM graph_edges GROUP BY 1").fetchall())
    check(
        "graph links entities, IPs and ASNs",
        {"ENTITY", "IP", "ASN"} <= set(labels) and {"SENT_TO", "SEEN_FROM", "IN_ASN"} <= set(edge_labels),
        ", ".join(f"{k}={v:,}" for k, v in sorted(labels.items()))
        + " | " + ", ".join(f"{k}={v:,}" for k, v in sorted(edge_labels.items())),
    )

    # 8 - subgraph export must match what the dashboard component consumes
    biggest = one("SELECT id FROM graph_nodes WHERE label='ENTITY' ORDER BY weight DESC LIMIT 1")
    sub = subgraph_for(db, biggest, hops=1)
    shaped = (
        isinstance(sub, dict)
        and {"nodes", "edges"} <= set(sub)
        and all("data" in n and {"id", "label"} <= set(n["data"]) for n in sub["nodes"])
        and all(
            "data" in e and {"id", "label", "source", "target"} <= set(e["data"])
            for e in sub["edges"]
        )
    )
    ids = {n["data"]["id"] for n in sub["nodes"]}
    closed = all(e["data"]["source"] in ids and e["data"]["target"] in ids for e in sub["edges"])
    check(
        "subgraph export is valid st-link-analysis input",
        shaped and closed and len(sub["nodes"]) > 1,
        f"{biggest}: {len(sub['nodes'])} nodes, {len(sub['edges'])} edges, "
        f"all fields wrapped in data{{}}, no edge references a missing node",
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
