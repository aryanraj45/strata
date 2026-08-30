"""Dual-layer correlation: linking a wallet entity to the host that broadcast it.

The ledger records what moved. It never records who. But before a transaction
reaches the ledger it crosses the peer-to-peer network, and in that moment it
carries a source address. Our sensors see it, and the host nearest the origin is
usually seen first.

Usually is the whole problem. Any single transaction can arrive first from any
peer by chance, so one observation is worth nothing. The signal is repetition:
when an entity's transactions are first-relayed from the same source far more
often than that source appears generally, chance stops being a plausible
explanation.

So this module produces a probability, not a verdict. Every lead carries the
count it rests on and the likelihood of seeing that count by accident.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

# How improbable a concentration has to be before it is worth an analyst's time.
SIGNIFICANCE = 0.01

# Below this, the sample is too small for the test to mean anything, whatever the
# arithmetic says. Two transactions from the same source is a coincidence.
MIN_TRANSACTIONS = 5


@dataclass
class Lead:
    entity_id: str
    ip: str
    asn: int
    country: str
    hits: int  # transactions where this IP was seen first
    transactions: int  # transactions by this entity in total
    weight: float  # sum of 1/rank across every observation
    baseline: float  # how often this IP leads generally
    p_value: float
    adjusted_p: float  # corrected for the number of pairs tested
    confidence: float
    factors: dict[str, float] = field(default_factory=dict)

    @property
    def significant(self) -> bool:
        return self.transactions >= MIN_TRANSACTIONS and self.adjusted_p <= SIGNIFICANCE


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p).

    Computed exactly rather than by normal approximation: n is small here, and
    the approximation is poor in the tail, which is the only part we care about.
    """
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0 if k > 0 else 1.0
    if p >= 1.0:
        return 1.0

    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return min(1.0, total)


def attribute(
    observations: list[tuple],
    tx_entity: dict[str, str],
    min_transactions: int = MIN_TRANSACTIONS,
) -> list[Lead]:
    """Score every (entity, source IP) pair.

    `observations` are `(txid, src_ip, arrival_rank, asn, country)` rows, already
    ranked by arrival time during ingest. `tx_entity` maps a transaction to the
    entity that spent it.
    """
    # --- accumulate per entity ------------------------------------------
    weights: dict[tuple[str, str], float] = defaultdict(float)
    hits: dict[tuple[str, str], int] = defaultdict(int)
    entity_txs: dict[str, set[str]] = defaultdict(set)
    ip_meta: dict[str, tuple[int, str]] = {}

    # How often each IP leads a transaction overall. This is the null model: an
    # IP that is first for everybody is not evidence about anybody.
    global_first: dict[str, int] = defaultdict(int)
    total_txs = 0

    for txid, src_ip, rank, asn, country in observations:
        ip_meta.setdefault(src_ip, (asn, country))
        if rank == 1:
            global_first[src_ip] += 1
            total_txs += 1

        entity = tx_entity.get(txid)
        if entity is None:
            continue
        entity_txs[entity].add(txid)
        weights[(entity, src_ip)] += 1.0 / rank
        if rank == 1:
            hits[(entity, src_ip)] += 1

    if total_txs == 0:
        return []

    # --- test each pair --------------------------------------------------
    leads: list[Lead] = []
    for (entity, ip), weight in weights.items():
        n = len(entity_txs[entity])
        if n < min_transactions:
            continue

        k = hits[(entity, ip)]
        if k == 0:
            continue

        baseline = global_first[ip] / total_txs
        p_value = binomial_tail(k, n, baseline)

        asn, country = ip_meta.get(ip, (0, ""))
        leads.append(
            Lead(
                entity_id=entity,
                ip=ip,
                asn=asn,
                country=country,
                hits=k,
                transactions=n,
                weight=weight,
                baseline=baseline,
                p_value=p_value,
                adjusted_p=p_value,  # corrected below, once the count is known
                confidence=1.0 - p_value,
                factors={
                    "first_relay_share": k / n,
                    "network_baseline": baseline,
                    "rank_weight": weight,
                    "observations": float(n),
                },
            )
        )

    # Correct for multiple comparisons.
    #
    # Every (entity, IP) pair is a separate hypothesis, and there are hundreds of
    # them. At an uncorrected threshold of 0.01, one in a hundred passes by
    # construction -- so a run over pure noise still yields a pile of "leads",
    # which is exactly what a shuffled-entity control demonstrates. Bonferroni is
    # the conservative choice and costs nothing here: genuine attributions sit
    # many orders of magnitude below the corrected threshold.
    tested = len(leads)
    for lead in leads:
        lead.adjusted_p = min(1.0, lead.p_value * tested)
        lead.confidence = 1.0 - lead.adjusted_p
        lead.factors["tests_performed"] = float(tested)

    # Strongest evidence first: least likely to have happened by chance, then
    # the larger sample when two are equally improbable.
    leads.sort(key=lambda l: (l.adjusted_p, -l.transactions))
    return leads


def best_per_entity(leads: list[Lead]) -> dict[str, Lead]:
    """The single strongest candidate origin for each entity."""
    best: dict[str, Lead] = {}
    for lead in leads:
        current = best.get(lead.entity_id)
        if current is None or lead.adjusted_p < current.adjusted_p:
            best[lead.entity_id] = lead
    return best


def geo_concentration(observations: list[tuple], tx_entity: dict[str, str]) -> dict[str, dict]:
    """Per-entity spread across countries and ASNs.

    A cluster of traffic inside one autonomous system corroborates an
    attribution. Traffic scattered across many countries in a short window is
    the opposite: it suggests the origin is being deliberately rotated, which is
    itself worth reporting rather than discarding.
    """
    countries: dict[str, defaultdict] = defaultdict(lambda: defaultdict(int))
    asns: dict[str, defaultdict] = defaultdict(lambda: defaultdict(int))

    for txid, _src_ip, rank, asn, country in observations:
        if rank != 1:
            continue
        entity = tx_entity.get(txid)
        if entity is None:
            continue
        countries[entity][country] += 1
        asns[entity][asn] += 1

    out: dict[str, dict] = {}
    for entity, counts in countries.items():
        total = sum(counts.values())
        asn_counts = asns[entity]
        asn_total = sum(asn_counts.values())
        out[entity] = {
            "n_countries": len(counts),
            "n_asns": len(asn_counts),
            "top_country_share": max(counts.values()) / total if total else 0.0,
            "top_asn_share": max(asn_counts.values()) / asn_total if asn_total else 0.0,
        }
    return out
