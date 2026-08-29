"""Graph construction and subgraph export.

Two outputs. First, an entity map that folds the clustering result together with
chain evidence, so a peeling chain stops being fifty separate entities. Second, a
node/edge graph linking entities, IP addresses and ASNs, in the shape the
dashboard's link-analysis component consumes directly.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .chains import Chain


class _Union:
    """Minimal union-find. The clustering stage has its own; this one folds that
    result together with chain evidence without importing across stages."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        return True


def _resolve_entities(clusters: dict[str, str], chains: list[Chain]) -> tuple[dict[str, str], int]:
    """Fold cluster membership and chain continuity into one entity map.

    Returns the map and how many addresses chain evidence newly joined.
    """
    uf = _Union()

    # Start from the clustering result: addresses sharing a cluster stay together.
    by_cluster: dict[str, list[str]] = defaultdict(list)
    for addr, cid in clusters.items():
        by_cluster[cid].append(addr)
    for members in by_cluster.values():
        for other in members[1:]:
            uf.union(members[0], other)

    # Then add chain evidence. Every address along a chain's continuation path is
    # the same entity carrying its balance forward -- that is what a peel is.
    merged = 0
    for chain in chains:
        path = [a for a in chain.addresses if a in clusters]
        for other in path[1:]:
            if uf.union(path[0], other):
                merged += 1

    entities = {addr: f"E-{uf.find(addr)[:12]}" for addr in clusters}
    return entities, merged


def _write(db, name: str, columns: list[str], rows: list[tuple], out: Path) -> None:
    if not rows:
        rows = []
    db.execute(f"CREATE OR REPLACE TABLE {name} ({', '.join(columns)})")
    if rows:
        placeholders = ", ".join("?" * len(columns))
        db.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
    db.execute(f"COPY {name} TO '{out.resolve()}' (FORMAT PARQUET)")


def build_graph(db, txs, clusters, chains: list[Chain], store: Path) -> tuple[int, int]:
    """Write the entity map, chain records, and the node/edge graph."""
    entities, merged = _resolve_entities(clusters, chains)

    _write(
        db, "entities",
        ["address VARCHAR", "entity_id VARCHAR", "cluster_id VARCHAR"],
        [(a, entities[a], clusters[a]) for a in sorted(entities)],
        store / "entities.parquet",
    )

    _write(
        db, "chains",
        ["chain_id VARCHAR", "hop INT", "txid VARCHAR", "carried_address VARCHAR",
         "entity_id VARCHAR", "hops INT", "peeled_sats BIGINT",
         "median_peel DOUBLE", "peel_spread DOUBLE", "laundering_like BOOLEAN"],
        [
            (c.chain_id, i, txid,
             c.addresses[i] if i < len(c.addresses) else None,
             entities.get(c.addresses[0]) if c.addresses else None,
             c.hops, c.peeled_total,
             c.median_peel, c.peel_spread, c.laundering_like)
            for c in chains
            for i, txid in enumerate(c.txids)
        ],
        store / "chains.parquet",
    )

    # --- node/edge graph -------------------------------------------------
    entity_size = defaultdict(int)
    for eid in entities.values():
        entity_size[eid] += 1

    # money flow between entities
    flow: dict[tuple[str, str], list[int]] = defaultdict(list)
    for tx in txs:
        src = entities.get(tx["input_addresses"][0]) if tx["input_addresses"] else None
        if src is None:
            continue
        for addr, amount in zip(tx["output_addresses"], tx["output_amounts"]):
            dst = entities.get(addr)
            if dst is not None and dst != src:
                flow[(src, dst)].append(amount)

    # where each entity's traffic was first observed from
    seen = db.execute(
        """
        SELECT e.entity_id, l2.src_ip, any_value(l2.asn), any_value(l2.geo_country),
               count(*) AS n
        FROM l2
        JOIN tx  ON tx.txid = l2.txid
        JOIN entities e ON e.address = tx.input_addresses[1]
        WHERE l2.arrival_rank = 1
        GROUP BY 1, 2
        ORDER BY n DESC
        """
    ).fetchall()

    nodes, edges = [], []
    for eid, size in entity_size.items():
        nodes.append((eid, "ENTITY", eid, size))
    ips = {}
    for eid, ip, asn, country, n in seen:
        ips.setdefault(ip, (asn, country))
        nodes.append((ip, "IP", ip, n)) if ip not in {x[0] for x in nodes} else None
    for ip, (asn, country) in ips.items():
        nodes.append((f"AS{asn}", "ASN", f"AS{asn} ({country})", 0))
    # de-duplicate, keeping the first occurrence
    seen_ids, unique_nodes = set(), []
    for node in nodes:
        if node[0] not in seen_ids:
            seen_ids.add(node[0])
            unique_nodes.append(node)

    for (src, dst), amounts in flow.items():
        edges.append((f"e{len(edges)}", "SENT_TO", src, dst, sum(amounts), len(amounts)))
    for eid, ip, asn, country, n in seen:
        edges.append((f"e{len(edges)}", "SEEN_FROM", eid, ip, 0, n))
    for ip, (asn, country) in ips.items():
        edges.append((f"e{len(edges)}", "IN_ASN", ip, f"AS{asn}", 0, 0))

    _write(
        db, "graph_nodes",
        ["id VARCHAR", "label VARCHAR", "name VARCHAR", "weight BIGINT"],
        unique_nodes, store / "graph_nodes.parquet",
    )
    _write(
        db, "graph_edges",
        ["id VARCHAR", "label VARCHAR", "source VARCHAR", "target VARCHAR",
         "amount BIGINT", "observations BIGINT"],
        edges, store / "graph_edges.parquet",
    )

    return len(entity_size), merged


def subgraph_for(db, entity_id: str, hops: int = 1) -> dict:
    """Neighbourhood of one entity, in st-link-analysis element format.

    Nodes and edges wrap their fields in `data`, and `label` is the node *type* --
    that is what NodeStyle and EdgeStyle key off for colour and icon.
    """
    reach = {entity_id}
    for _ in range(hops):
        rows = db.execute(
            "SELECT source, target FROM graph_edges "
            "WHERE source IN ? OR target IN ?",
            [list(reach), list(reach)],
        ).fetchall()
        for src, dst in rows:
            reach.update((src, dst))

    node_rows = db.execute(
        "SELECT id, label, name, weight FROM graph_nodes WHERE id IN ?", [list(reach)]
    ).fetchall()
    edge_rows = db.execute(
        "SELECT id, label, source, target, amount, observations FROM graph_edges "
        "WHERE source IN ? AND target IN ?",
        [list(reach), list(reach)],
    ).fetchall()

    return {
        "nodes": [
            {"data": {"id": i, "label": lbl, "name": name, "weight": w}}
            for i, lbl, name, w in node_rows
        ],
        "edges": [
            {
                "data": {
                    "id": i, "label": lbl, "source": s, "target": t,
                    "amount": f"{amt / 1e8:.8f}" if amt else None,
                    "observations": obs,
                }
            }
            for i, lbl, s, t, amt, obs in edge_rows
        ],
    }
