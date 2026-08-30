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


# ---------------------------------------------------------------- page queries

def risk_bands(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    return db.execute(
        """
        SELECT CASE
                 WHEN risk_score >= 0.80 THEN 'Critical'
                 WHEN risk_score >= 0.50 THEN 'High'
                 WHEN risk_score >= 0.25 THEN 'Elevated'
                 ELSE 'Low' END AS band,
               count(*) AS count
        FROM scores GROUP BY 1
        """
    ).fetchall()


def countries(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Where attributed traffic originates."""
    return db.execute(
        """
        SELECT l.country, count(*) AS entities,
               sum(CASE WHEN s.risk_score >= 0.5 THEN 1 ELSE 0 END) AS high_risk
        FROM leads l JOIN scores s ON s.entity_id = l.entity_id
        WHERE l.significant AND l.country IS NOT NULL
        GROUP BY 1 ORDER BY entities DESC
        """
    ).fetchall()


def networks(db: duckdb.DuckDBPyConnection, limit: int = 15) -> list[tuple]:
    return db.execute(
        """
        SELECT l.asn, any_value(l.country) AS country, count(*) AS entities,
               avg(s.risk_score) AS mean_risk,
               sum(l.hits) AS sightings
        FROM leads l JOIN scores s ON s.entity_id = l.entity_id
        WHERE l.significant
        GROUP BY 1 ORDER BY entities DESC LIMIT ?
        """,
        [limit],
    ).fetchall()


def hourly_activity(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Transaction volume by hour, split by whether the actor is high risk.

    Illicit operators keep different hours; this is the feature the model uses,
    shown directly.
    """
    return db.execute(
        """
        SELECT extract(hour FROM to_timestamp(t.ts_micro / 1000000)) AS hour,
               sum(CASE WHEN s.risk_score >= 0.5 THEN 1 ELSE 0 END) AS high_risk,
               sum(CASE WHEN s.risk_score <  0.5 THEN 1 ELSE 0 END) AS other
        FROM tx t
        JOIN entities e ON e.address = t.input_addresses[1]
        JOIN scores s   ON s.entity_id = e.entity_id
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()


def chain_list(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    return db.execute(
        """
        SELECT c.chain_id, any_value(c.entity_id) AS entity_id,
               max(c.hops) AS hops, max(c.peeled_sats) AS peeled,
               max(c.median_peel) AS median_peel,
               max(c.peel_spread) AS peel_spread,
               max(c.laundering_like) AS laundering_like,
               max(s.risk_score) AS risk
        FROM chains c LEFT JOIN scores s ON s.entity_id = c.entity_id
        GROUP BY 1 ORDER BY laundering_like DESC, hops DESC
        """
    ).fetchall()


def chain_hops(db: duckdb.DuckDBPyConnection, chain_id: str) -> list[tuple]:
    return db.execute(
        """
        SELECT c.hop, c.txid, c.carried_address,
               t.total_out, t.fee, t.ts_micro
        FROM chains c LEFT JOIN tx t ON t.txid = c.txid
        WHERE c.chain_id = ? ORDER BY c.hop
        """,
        [chain_id],
    ).fetchall()


def coinjoins(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    return db.execute(
        """
        SELECT f.txid, f.coinjoin_reason, t.n_inputs, t.n_outputs, t.total_out
        FROM flags f JOIN tx t ON t.txid = f.txid
        WHERE f.is_coinjoin ORDER BY t.n_inputs DESC
        """
    ).fetchall()


def pipeline_stats(db: duckdb.DuckDBPyConnection) -> list[tuple]:
    """What each stage produced, read back from its own output."""
    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return [
        ("Ingest", "tx_l1 + net_l2",
         f"{one('SELECT count(*) FROM tx'):,} transactions, "
         f"{one('SELECT count(*) FROM l2'):,} network sightings"),
        ("Clustering", "clusters + tx_flags",
         f"{one('SELECT count(DISTINCT cluster_id) FROM clusters'):,} clusters, "
         f"{one('SELECT count(*) FROM flags WHERE is_coinjoin'):,} CoinJoins filtered"),
        ("Graph", "entities + chains",
         f"{one('SELECT count(DISTINCT entity_id) FROM entities'):,} entities, "
         f"{one('SELECT count(DISTINCT chain_id) FROM chains'):,} chains recovered"),
        ("Fusion", "leads",
         f"{one('SELECT count(*) FROM leads WHERE significant'):,} significant attributions "
         f"of {one('SELECT count(*) FROM leads'):,} scored"),
        ("Models", "scores",
         f"{one('SELECT count(*) FROM scores'):,} entities scored, "
         f"{one('SELECT count(*) FROM scores WHERE is_anomaly'):,} flagged as anomalous"),
    ]


def custody(store: Path) -> dict | None:
    """The ingest manifest: what evidence went in, and its hashes."""
    path = store / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
