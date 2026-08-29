"""CoinJoin detection.

This runs *before* clustering, and the order is not a style preference.

A CoinJoin is many unrelated people signing one transaction together. The
common-input-ownership heuristic assumes the opposite — that everyone signing a
transaction is the same entity. Feed a CoinJoin to CIOH and it merges strangers
into the suspect's cluster, and the error is silent: nothing crashes, the cluster
just quietly becomes wrong, and every downstream attribution inherits it.

So we identify them first and exclude them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# A CoinJoin needs enough participants to hide in. Below this, equal outputs are
# more likely to be a batch payment or a round-number coincidence.
MIN_INPUTS = 3
MIN_OUTPUTS = 3
MIN_EQUAL_OUTPUTS = 3


@dataclass(frozen=True)
class CoinJoinVerdict:
    is_coinjoin: bool
    equal_count: int  # size of the largest equal-value output group
    denomination: int | None  # that group's value, in satoshis
    reason: str


def classify(input_addresses, output_amounts) -> CoinJoinVerdict:
    """Structural test on one transaction.

    The signature is a repeated output denomination: participants must receive
    indistinguishable amounts or the mix achieves nothing.
    """
    n_in = len(input_addresses)
    n_out = len(output_amounts)

    if n_in < MIN_INPUTS or n_out < MIN_OUTPUTS:
        return CoinJoinVerdict(False, 0, None, f"{n_in} inputs, {n_out} outputs: too small")

    counts = Counter(output_amounts)
    denom, equal = counts.most_common(1)[0]

    if equal < MIN_EQUAL_OUTPUTS:
        return CoinJoinVerdict(
            False, equal, None, f"largest equal-value group is {equal}, need {MIN_EQUAL_OUTPUTS}"
        )

    return CoinJoinVerdict(
        True,
        equal,
        int(denom),
        f"{equal} outputs of {denom / 1e8:.8f} BTC across {n_in} inputs",
    )
