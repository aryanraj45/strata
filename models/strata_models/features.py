"""Per-entity feature extraction.

Everything here is computable from the store alone. Ground truth is used only to
label and to score, never to build a feature -- a feature derived from the answer
would make the model look excellent and be worthless.

The features fall into four groups, and keeping them separate matters for the
explanation the problem statement asks for: an analyst reading "flagged because
of chain structure" acts differently from one reading "flagged because of where
it broadcast from".
"""

from __future__ import annotations

FEATURE_SQL = """
WITH tx_entity AS (
    SELECT t.txid, e.entity_id, t.ts_micro, t.fee, t.script_type,
           t.n_inputs, t.n_outputs, t.total_in, t.total_out,
           t.output_addresses, t.output_amounts
    FROM tx t
    JOIN entities e ON e.address = t.input_addresses[1]
),

-- Ledger behaviour: how this entity moves money.
spend AS (
    SELECT entity_id,
           count(*)                                        AS n_transactions,
           count(DISTINCT date_trunc('day', to_timestamp(ts_micro / 1000000))) AS active_days,
           sum(total_out)                                   AS total_out,
           avg(total_out)                                   AS mean_value,
           median(total_out)                                AS median_value,
           stddev_pop(total_out)                            AS value_spread,
           avg(fee)                                         AS mean_fee,
           stddev_pop(fee)                                  AS fee_spread,
           avg(n_inputs)                                    AS mean_inputs,
           avg(n_outputs)                                   AS mean_outputs,
           max(n_inputs)                                    AS max_inputs,
           count(DISTINCT script_type)                      AS n_script_types
    FROM tx_entity GROUP BY 1
),

-- Timing: illicit operators keep different hours, and act in bursts.
timing AS (
    SELECT entity_id,
           avg(hour)                                        AS mean_hour,
           sum(CASE WHEN hour < 6 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS nocturnal_share,
           count(DISTINCT hour)::DOUBLE / 24                AS hour_coverage
    FROM (SELECT entity_id, extract(hour FROM to_timestamp(ts_micro / 1000000)) AS hour
          FROM tx_entity)
    GROUP BY 1
),

-- Counterparties: fan-out is what a payer looks like, fan-in a collector.
counterparties AS (
    SELECT te.entity_id,
           count(DISTINCT e2.entity_id)                     AS fan_out
    FROM tx_entity te
    CROSS JOIN UNNEST(te.output_addresses) AS o(addr)
    JOIN entities e2 ON e2.address = o.addr
    WHERE e2.entity_id <> te.entity_id
    GROUP BY 1
),
received AS (
    SELECT e2.entity_id,
           count(*)                                         AS n_received,
           count(DISTINCT te.entity_id)                     AS fan_in
    FROM tx_entity te
    CROSS JOIN UNNEST(te.output_addresses) AS o(addr)
    JOIN entities e2 ON e2.address = o.addr
    WHERE e2.entity_id <> te.entity_id
    GROUP BY 1
),

-- Size of the entity itself.
size AS (
    SELECT entity_id, count(*) AS n_addresses FROM entities GROUP BY 1
),

-- Structure recovered by the graph stage.
chain AS (
    SELECT entity_id,
           max(hops)                                        AS chain_hops,
           min(median_peel)                                 AS chain_median_peel,
           max(CASE WHEN laundering_like THEN 1 ELSE 0 END) AS chain_laundering_like
    FROM chains WHERE entity_id IS NOT NULL GROUP BY 1
),

-- Obfuscation: participation in CoinJoin rounds.
mixing AS (
    SELECT te.entity_id,
           sum(CASE WHEN f.is_coinjoin THEN 1 ELSE 0 END)::DOUBLE / count(*) AS coinjoin_share
    FROM tx_entity te JOIN flags f ON f.txid = te.txid
    GROUP BY 1
),

-- Network layer: the dual-layer contribution, as model input.
network AS (
    SELECT entity_id,
           max(CASE WHEN significant THEN 1 ELSE 0 END)     AS has_attribution,
           max(hits::DOUBLE / nullif(transactions, 0))      AS first_relay_share,
           min(adjusted_p)                                  AS attribution_p,
           max(n_countries)                                 AS n_countries,
           max(n_asns)                                      AS n_asns,
           max(top_asn_share)                               AS top_asn_share
    FROM leads GROUP BY 1
)

SELECT
    s.entity_id,
    s.n_transactions,
    coalesce(sz.n_addresses, 1)                             AS n_addresses,
    s.n_transactions::DOUBLE / nullif(s.active_days, 0)     AS tx_per_active_day,
    s.total_out, s.mean_value, s.median_value,
    coalesce(s.value_spread, 0)                             AS value_spread,
    s.mean_fee, coalesce(s.fee_spread, 0)                   AS fee_spread,
    s.mean_inputs, s.mean_outputs, s.max_inputs, s.n_script_types,
    t.mean_hour, t.nocturnal_share, t.hour_coverage,
    coalesce(c.fan_out, 0)                                  AS fan_out,
    coalesce(r.fan_in, 0)                                   AS fan_in,
    coalesce(r.n_received, 0)                               AS n_received,
    coalesce(ch.chain_hops, 0)                              AS chain_hops,
    coalesce(ch.chain_median_peel, 0)                       AS chain_median_peel,
    coalesce(ch.chain_laundering_like, 0)                   AS chain_laundering_like,
    coalesce(m.coinjoin_share, 0)                           AS coinjoin_share,
    coalesce(n.has_attribution, 0)                          AS has_attribution,
    coalesce(n.first_relay_share, 0)                        AS first_relay_share,
    coalesce(n.n_countries, 0)                              AS n_countries,
    coalesce(n.n_asns, 0)                                   AS n_asns,
    coalesce(n.top_asn_share, 0)                            AS top_asn_share
FROM spend s
LEFT JOIN timing t         ON t.entity_id  = s.entity_id
LEFT JOIN counterparties c ON c.entity_id  = s.entity_id
LEFT JOIN received r       ON r.entity_id  = s.entity_id
LEFT JOIN size sz          ON sz.entity_id = s.entity_id
LEFT JOIN chain ch         ON ch.entity_id = s.entity_id
LEFT JOIN mixing m         ON m.entity_id  = s.entity_id
LEFT JOIN network n        ON n.entity_id  = s.entity_id
WHERE s.n_transactions >= ?
ORDER BY s.entity_id
"""

# Grouped for explanation. An analyst reading "flagged on chain structure" acts
# differently from one reading "flagged on where it broadcast from".
GROUPS = {
    "volume": ["n_transactions", "n_addresses", "tx_per_active_day", "total_out", "n_received"],
    "value": ["mean_value", "median_value", "value_spread", "mean_fee", "fee_spread"],
    "structure": ["mean_inputs", "mean_outputs", "max_inputs", "n_script_types",
                  "fan_out", "fan_in"],
    "timing": ["mean_hour", "nocturnal_share", "hour_coverage"],
    "laundering": ["chain_hops", "chain_median_peel", "chain_laundering_like",
                   "coinjoin_share"],
    "network": ["has_attribution", "first_relay_share", "n_countries", "n_asns",
                "top_asn_share"],
}

FEATURES = [f for group in GROUPS.values() for f in group]


def group_of(feature: str) -> str:
    for name, members in GROUPS.items():
        if feature in members:
            return name
    return "other"


def extract(db, store, min_transactions: int = 3):
    """Load the store and return (entity_ids, feature matrix as list of dicts)."""
    for name, file in [
        ("tx", "tx_l1.parquet"), ("l2", "net_l2.parquet"),
        ("entities", "entities.parquet"), ("chains", "chains.parquet"),
        ("leads", "leads.parquet"), ("flags", "tx_flags.parquet"),
    ]:
        db.read_parquet(str((store / file).resolve())).create_view(name)

    rows = db.execute(FEATURE_SQL, [min_transactions]).fetchall()
    columns = [d[0] for d in db.description]
    records = [dict(zip(columns, r)) for r in rows]
    ids = [r["entity_id"] for r in records]
    matrix = [[float(r[f] if r[f] is not None else 0.0) for f in FEATURES] for r in records]
    return ids, matrix
