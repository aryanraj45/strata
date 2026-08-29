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

        if (
            use_change
            and tx.get("change_address")
            and tx.get("change_corroborated")
            and tx.get("change_confidence", 0.0) >= min_change_confidence
        ):
            uf.union(inputs[0], tx["change_address"])

    return uf
