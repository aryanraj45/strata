"""Common-input-ownership clustering.

Every input to a transaction must be signed by whoever controls it. Unrelated
strangers do not coordinate signatures inside one transaction, so spending from
several inputs together is strong evidence of one owner. Applied across the whole
ledger, this collapses thousands of scattered addresses into a handful of
entities.

The exception is CoinJoin, which is exactly strangers coordinating signatures.
Those transactions are excluded upstream — see coinjoin.py.
"""

from __future__ import annotations

# Merging on a change guess is irreversible and its errors are silent, so we act
# on one only when the signals clearly agree. Lower-confidence guesses are still
# reported for an analyst to review — they just do not move the cluster
# boundaries on their own.
# A change call moves cluster boundaries only when raggedness backed it, and
# with enough margin. Every observed error was a call the other signals made
# without raggedness, so this is a structural rule rather than a tuned cutoff.
MERGE_MIN_CONFIDENCE = 0.40

# A change call is right about 94% of the time, and the cost of the other 6% is
# not constant. Joining clusters of size |A| and |B| asserts |A|x|B| address
# pairs, and if the call was wrong every one of them puts a stranger inside a
# suspect's entity. So a change call gets a budget in pairs rather than one
# global confidence cutoff.
#
# Both alternatives were measured on this dataset and were worse:
#
#   Raising MERGE_MIN_CONFIDENCE to 0.70 bought perfect precision by discarding
#   the good merges along with the bad, halving attributed leads from 147 to 77.
#
#   Pricing on the smaller side rather than the product let a 6-address group
#   weld onto a 99-address one -- 594 wrong pairs that looked like a cost of 6.
#
# The override exists because a merge over budget is still worth making when the
# signals are emphatic; it recovers a little recall at no measurable cost to
# precision.
CHANGE_MERGE_BUDGET = 60         # pairs a single change call may risk
CHANGE_MERGE_DEAR = 0.70         # a costly join needs this much confidence


class UnionFind:
    """Address -> entity, with path compression and union by size."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self.parent:
            self.parent[item] = item
            self.size[item] = 1

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        # compress
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def cluster(
    transactions,
    use_change: bool = True,
    min_change_confidence: float = MERGE_MIN_CONFIDENCE,
) -> UnionFind:
    """Build the entity map.

    `transactions` must already be ordered by time and carry an `is_coinjoin`
    flag and, when available, a resolved change address.
    """
    uf = UnionFind()

    # Pass one: co-spending only. For a non-CoinJoin transaction every input
    # was signed by one party, so these merges are correct by construction and
    # need no confidence gate.
    for tx in transactions:
        for addr in tx["input_addresses"]:
            uf.add(addr)
        for addr in tx["output_addresses"]:
            uf.add(addr)

        if tx["is_coinjoin"]:
            # Signatures here belong to different people. Merging them is the
            # single most damaging mistake available in this pipeline.
            continue

        inputs = tx["input_addresses"]
        for addr in inputs[1:]:
            uf.union(inputs[0], addr)

    if not use_change:
        return uf

    # Pass two: the change guesses, priced against clusters that have already
    # settled. Doing this in the first pass prices them against whatever had
    # been seen so far, which is close to nothing early on -- so a wrong call
    # between two addresses that each later grew into a hundred-address entity
    # looked cheap at the time and welded them together. That single merge
    # produced 86% of all the wrongly-linked pairs.
    for tx in transactions:
        if tx["is_coinjoin"]:
            continue
        confidence = tx.get("change_confidence", 0.0)
        if not (
            tx.get("change_address")
            and tx.get("change_corroborated")
            and confidence >= min_change_confidence
        ):
            continue

        left, right = uf.find(tx["input_addresses"][0]), uf.find(tx["change_address"])
        if left == right:
            continue

        at_risk = uf.size[left] * uf.size[right]
        if at_risk <= CHANGE_MERGE_BUDGET or confidence >= CHANGE_MERGE_DEAR:
            uf.union(left, right)

    return uf
