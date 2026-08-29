"""Peeling-chain detection by graph structure.

A peeling chain is a run of transactions where a balance repeatedly sheds a
small payment and carries the remainder forward to a fresh address. Each hop has
one input and two outputs, and the remainder becomes the input of the next hop.

Clustering cannot recover these. Common-input-ownership links addresses that are
spent together, and a chain never spends two addresses together -- every hop has
exactly one input. The only bridge available to the clustering stage is the
change heuristic, and a single missed hop severs the chain.

Structure does better. If an output of a one-in/two-out transaction is spent by
another one-in/two-out transaction, that is a link, and it holds whether or not
the change heuristic agreed. Recovering the chain also identifies the change
output as a side effect: the continuation *is* the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Two hops prove nothing -- an ordinary payment followed by another ordinary
# payment has the same shape. A chain has to be long enough that the repetition
# is the evidence.
MIN_CHAIN_LENGTH = 4

# What makes a hop a peel rather than an ordinary payment: the balance carried
# onward dominates what was shed. An ordinary spend splits arbitrarily -- pay 0.5
# from 0.6 and the payment is the larger output -- so following the bigger output
# there walks into the recipient's wallet instead of staying with the sender.
# Requiring dominance keeps the walk inside one entity.
MIN_CARRY_SHARE = 0.75

# A run of one-in/two-out hops with a dominant carry proves the addresses belong
# to one entity. It does not prove laundering: an ordinary person spending
# repeatedly out of their change produces the same shape. Calling that a peeling
# chain would flag normal users as launderers.
#
# What distinguishes the laundering pattern is discipline. The operator sheds a
# small, consistent slice each time and keeps the rest moving, over many hops.
# Someone just spending money peels erratically and stops.
LAUNDERING_MAX_MEDIAN_PEEL = 0.15
LAUNDERING_MAX_PEEL_SPREAD = 0.20
LAUNDERING_MIN_HOPS = 8


@dataclass
class Chain:
    """One recovered peeling chain."""

    chain_id: str
    txids: list[str]
    addresses: list[str] = field(default_factory=list)  # the continuation path
    peeled_total: int = 0  # satoshis shed along the way
    carried_start: int = 0
    carried_end: int = 0
    peel_ratios: list[float] = field(default_factory=list)

    @property
    def hops(self) -> int:
        return len(self.txids)

    @property
    def median_peel(self) -> float:
        if not self.peel_ratios:
            return 0.0
        ordered = sorted(self.peel_ratios)
        return ordered[len(ordered) // 2]

    @property
    def peel_spread(self) -> float:
        """How uneven the slices are. Disciplined peeling is consistent."""
        if len(self.peel_ratios) < 2:
            return 1.0
        return max(self.peel_ratios) - min(self.peel_ratios)

    @property
    def laundering_like(self) -> bool:
        return (
            self.hops >= LAUNDERING_MIN_HOPS
            and self.median_peel <= LAUNDERING_MAX_MEDIAN_PEEL
            and self.peel_spread <= LAUNDERING_MAX_PEEL_SPREAD
        )


def _continuation(tx, spender: dict[str, str], by_txid: dict[str, dict]) -> tuple[str, str] | None:
    """Which output carries the balance onward, and the transaction spending it.

    Returns `(address, next_txid)`, or None when the chain stops here.
    """
    candidates = []
    for addr, amount in zip(tx["output_addresses"], tx["output_amounts"]):
        next_txid = spender.get(addr)
        if next_txid is None:
            continue
        nxt = by_txid.get(next_txid)
        if nxt is None or nxt["n_inputs"] != 1 or nxt["n_outputs"] != 2:
            continue
        candidates.append((amount, addr, next_txid))

    if not candidates:
        return None

    total_out = sum(tx["output_amounts"])
    if total_out <= 0:
        return None

    amount, addr, next_txid = max(candidates)
    # An even-ish split is an ordinary payment: we cannot tell which output is
    # change, and guessing would merge the recipient into the sender.
    if amount / total_out < MIN_CARRY_SHARE:
        return None
    return addr, next_txid


def detect(transactions: list[dict], min_length: int = MIN_CHAIN_LENGTH) -> list[Chain]:
    """Find every maximal peeling chain in the transaction set."""
    by_txid = {tx["txid"]: tx for tx in transactions}

    # address -> the transaction that spends it
    spender: dict[str, str] = {}
    for tx in transactions:
        for addr in tx["input_addresses"]:
            spender.setdefault(addr, tx["txid"])

    peel_shaped = {
        tx["txid"] for tx in transactions if tx["n_inputs"] == 1 and tx["n_outputs"] == 2
    }

    # Walk forward from every peel-shaped transaction, then keep only the
    # maximal runs -- a chain found from its middle is a suffix of the same one.
    successor: dict[str, tuple[str, str]] = {}
    for txid in peel_shaped:
        found = _continuation(by_txid[txid], spender, by_txid)
        if found is not None:
            successor[txid] = found

    has_predecessor = {next_txid for _, next_txid in successor.values()}
    starts = [t for t in peel_shaped if t not in has_predecessor]

    chains: list[Chain] = []
    for start in sorted(starts):
        txids, addresses = [start], []
        seen = {start}
        current = start
        while current in successor:
            addr, nxt = successor[current]
            if nxt in seen:  # a cycle cannot be a chain
                break
            addresses.append(addr)
            txids.append(nxt)
            seen.add(nxt)
            current = nxt

        if len(txids) < min_length:
            continue

        first, last = by_txid[txids[0]], by_txid[txids[-1]]
        peeled = 0
        ratios = []
        for t in txids:
            amounts = by_txid[t]["output_amounts"]
            if not amounts:
                continue
            total = sum(amounts)
            peel = min(amounts)
            peeled += peel
            if total > 0:
                ratios.append(peel / total)
        chains.append(
            Chain(
                chain_id=f"CH-{txids[0][:10]}",
                txids=txids,
                addresses=addresses,
                peeled_total=peeled,
                carried_start=max(first["output_amounts"], default=0),
                carried_end=max(last["output_amounts"], default=0),
                peel_ratios=ratios,
            )
        )

    chains.sort(key=lambda c: c.hops, reverse=True)
    return chains
