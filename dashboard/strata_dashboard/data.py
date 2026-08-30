"""Reading the store for the dashboard.

Every query here is read-only and runs against the Parquet files the pipeline
already wrote. The dashboard computes nothing itself -- if a number appears on
screen, a pipeline stage produced it and its own verify.py checked it.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

TABLES = {
    "tx": "tx_l1.parquet",
    "l2": "net_l2.parquet",
    "entities": "entities.parquet",
    "clusters": "clusters.parquet",
    "chains": "chains.parquet",
    "leads": "leads.parquet",
    "scores": "scores.parquet",
    "flags": "tx_flags.parquet",
    "graph_nodes": "graph_nodes.parquet",
    "graph_edges": "graph_edges.parquet",
}


def connect(store: Path) -> duckdb.DuckDBPyConnection:
    """Open the store. Missing tables are tolerated so a partially-run pipeline
    still shows what it has, rather than failing with a stack trace."""
    db = duckdb.connect()
    for name, file in TABLES.items():
        path = store / file
        if path.exists():
            db.read_parquet(str(path.resolve())).create_view(name)
    return db


def available(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in db.execute("SHOW TABLES").fetchall()}


def alerts(db: duckdb.DuckDBPyConnection) -> list[dict]:
    """The ranked alert list.

    Ordered by risk, then by attribution confidence. An entity with a strong
    origin attribution is more actionable than one without, even at equal risk.
    """
    rows = db.execute(
        """
        SELECT
            s.entity_id,
            s.risk_score,
            s.is_anomaly,
            s.factors,
            l.ip,
            l.country,
            l.asn,
            l.hits,
            l.transactions,
            l.confidence      AS attribution_confidence,
            l.significant     AS attribution_significant,
            l.n_countries,
            coalesce(c.hops, 0)              AS chain_hops,
            coalesce(c.laundering_like, false) AS laundering_like,
            e.n_addresses
        FROM scores s
        LEFT JOIN leads l ON l.entity_id = s.entity_id
        LEFT JOIN (
            SELECT entity_id, max(hops) AS hops,
                   max(laundering_like) AS laundering_like
            FROM chains WHERE entity_id IS NOT NULL GROUP BY 1
        ) c ON c.entity_id = s.entity_id
        LEFT JOIN (
            SELECT entity_id, count(*) AS n_addresses FROM entities GROUP BY 1
        ) e ON e.entity_id = s.entity_id
        ORDER BY s.risk_score DESC, l.confidence DESC NULLS LAST
        """
    ).fetchall()
    columns = [d[0] for d in db.description]
    out = []
    for row in rows:
        record = dict(zip(columns, row))
        record["factors"] = json.loads(record["factors"]) if record["factors"] else []
        out.append(record)
    return out


def entity_detail(db: duckdb.DuckDBPyConnection, entity_id: str) -> dict:
    """Everything known about one entity."""
    addresses = [
        r[0] for r in db.execute(
            "SELECT address FROM entities WHERE entity_id = ? ORDER BY address", [entity_id]
        ).fetchall()
    ]
    transactions = db.execute(
        """
        SELECT t.txid, t.ts_micro, t.total_out, t.fee, t.n_inputs, t.n_outputs, t.script_type
        FROM tx t JOIN entities e ON e.address = t.input_addresses[1]
        WHERE e.entity_id = ?
        ORDER BY t.ts_micro
        """,
        [entity_id],
    ).fetchall()
    chains = db.execute(
        """
        SELECT chain_id, max(hops) AS hops, max(peeled_sats) AS peeled,
               max(median_peel) AS median_peel, max(laundering_like) AS laundering_like
        FROM chains WHERE entity_id = ? GROUP BY 1 ORDER BY hops DESC
        """,
        [entity_id],
    ).fetchall()
    candidates = db.execute(
        """
        SELECT ip, country, asn, hits, transactions, confidence, adjusted_p, significant
        FROM leads WHERE entity_id = ? ORDER BY adjusted_p
        """,
        [entity_id],
    ).fetchall()
    return {
        "addresses": addresses,
        "transactions": transactions,
        "chains": chains,
        "candidates": candidates,
    }


def observations(db: duckdb.DuckDBPyConnection, entity_id: str, limit: int = 400) -> list[tuple]:
    """Network sightings for this entity's transactions, earliest arrival first.

    This is the raw evidence behind an attribution: which host was seen relaying
    each transaction, and in what order.
    """
    return db.execute(
        """
        SELECT l2.txid, l2.src_ip, l2.arrival_rank, l2.ts_micro, l2.geo_country, l2.asn
        FROM l2
        JOIN tx t ON t.txid = l2.txid
        JOIN entities e ON e.address = t.input_addresses[1]
        WHERE e.entity_id = ?
        ORDER BY l2.ts_micro
        LIMIT ?
        """,
        [entity_id, limit],
    ).fetchall()


def subgraph(db: duckdb.DuckDBPyConnection, entity_id: str, hops: int = 1) -> dict:
    """Neighbourhood of one entity in st-link-analysis element format.

    Fields are wrapped in `data`, and `label` is the node *type* -- that is what
    NodeStyle and EdgeStyle key off for colour and icon.
    """
    reach = {entity_id}
    for _ in range(max(1, hops)):
        rows = db.execute(
            "SELECT source, target FROM graph_edges WHERE source IN ? OR target IN ?",
            [list(reach), list(reach)],
        ).fetchall()
        for src, dst in rows:
            reach.update((src, dst))
        # A hub entity can pull in the whole graph; cap it so the view stays
        # readable rather than becoming a hairball.
        if len(reach) > 60:
            break

    reach = list(reach)[:60]
    nodes = db.execute(
        "SELECT id, label, name, weight FROM graph_nodes WHERE id IN ?", [reach]
    ).fetchall()
    edges = db.execute(
        "SELECT id, label, source, target, amount, observations FROM graph_edges "
        "WHERE source IN ? AND target IN ?",
        [reach, reach],
    ).fetchall()

    return {
        "nodes": [
            {
                "data": {
                    "id": nid,
                    "label": label,
                    "name": name,
                    "weight": int(weight or 0),
                    "focus": nid == entity_id,
                }
            }
            for nid, label, name, weight in nodes
        ],
        "edges": [
            {
                "data": {
                    "id": eid,
                    "label": label,
                    "source": src,
                    "target": dst,
                    "btc": f"{(amount or 0) / 1e8:.4f}",
                    "seen": int(obs or 0),
                }
            }
            for eid, label, src, dst, amount, obs in edges
        ],
    }


def summary(db: duckdb.DuckDBPyConnection) -> dict:
    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "transactions": one("SELECT count(*) FROM tx"),
        "observations": one("SELECT count(*) FROM l2"),
        "addresses": one("SELECT count(*) FROM entities"),
        "entities": one("SELECT count(DISTINCT entity_id) FROM entities"),
        "scored": one("SELECT count(*) FROM scores"),
        "high_risk": one("SELECT count(*) FROM scores WHERE risk_score >= 0.5"),
        "attributed": one("SELECT count(*) FROM leads WHERE significant"),
        "chains": one("SELECT count(DISTINCT chain_id) FROM chains"),
        "laundering_chains": one(
            "SELECT count(DISTINCT chain_id) FROM chains WHERE laundering_like"
        ),
        "countries": one("SELECT count(DISTINCT geo_country) FROM l2"),
    }


def geo_rollup(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Where attributed traffic originates, by country and network."""
    return db.execute(
        """
        SELECT l.country, l.asn, count(*) AS entities, avg(s.risk_score) AS mean_risk
        FROM leads l JOIN scores s ON s.entity_id = l.entity_id
        WHERE l.significant
        GROUP BY 1, 2
        ORDER BY entities DESC
        """
    ).fetchall()
