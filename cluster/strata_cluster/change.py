"""Change-address identification.

When a wallet spends, the surplus returns to an address the sender still
controls. Telling that output apart from the real payment is the central problem
of chain-following: get it wrong and you follow the shopkeeper instead of the
suspect.

No single signal is reliable — privacy-conscious wallets defeat each one
individually. We score three weak signals and combine them, and we surface the
score rather than the verdict, so an analyst can see how confident the call was.
"""

from __future__ import annotations

from dataclasses import dataclass

# Weights are deliberately unequal, and raggedness leads.
#
# Change is the arithmetic remainder of a spend, so it is almost never a round
# number, whereas a person choosing a payment amount very often picks one. A
# fresh address is weaker than it first appears: change is always fresh, but so
# is a payment to anyone who has not published a reusable deposit address, so the
# signal frequently fails to discriminate. Measured on the evaluation set, the
# decisions that went wrong were overwhelmingly the ones freshness decided alone.
W_RAGGED = 0.45
W_FRESH = 0.35
W_SCRIPT = 0.20

# Confidence is the *margin* between the two candidates, not either one's score.
# Both outputs scoring 0.80 tells you nothing; 0.80 against 0.20 tells you a lot.
# Below this margin we decline rather than guess, because a wrong change call
# merges a recipient into the sender's cluster and that error is silent.
MIN_MARGIN = 0.30


@dataclass(frozen=True)
class ChangeGuess:
    index: int | None
    confidence: float
    signals: dict[str, float]


def _script_of(address: str) -> str:
    if address.startswith("bc1q"):
        return "v0_p2wpkh"
    if address.startswith("3"):
        return "p2sh"
    if address.startswith("1"):
        return "p2pkh"
    return "unknown"


def _is_round(sats: int) -> bool:
    """A payment a human chose tends to be round; change is whatever is left."""
    for unit in (100_000_000, 10_000_000, 1_000_000, 100_000):
        if sats % unit == 0:
            return True
    return False


def identify(
    output_addresses: list[str],
    output_amounts: list[int],
    input_script: str,
    seen_addresses: set[str],
) -> ChangeGuess:
    """Score each output as a change candidate and return the best.

    `seen_addresses` is every address observed before this transaction. Change
    goes to a fresh address; a payment often goes somewhere already known.
    """
    if len(output_addresses) != 2:
        # Exactly one change output only makes sense in the two-output case. For
        # anything else we decline rather than guess.
        return ChangeGuess(None, 0.0, {})

    scores = []
    for addr, sats in zip(output_addresses, output_amounts):
        signals = {
            "fresh_address": W_FRESH if addr not in seen_addresses else 0.0,
            "ragged_amount": W_RAGGED if not _is_round(sats) else 0.0,
            "script_matches_input": W_SCRIPT if _script_of(addr) == input_script else 0.0,
        }
        scores.append((sum(signals.values()), signals))

    best = 0 if scores[0][0] >= scores[1][0] else 1
    margin = abs(scores[0][0] - scores[1][0])

    if margin < MIN_MARGIN:
        return ChangeGuess(None, margin, scores[best][1])

    return ChangeGuess(best, margin, scores[best][1])
