"""Address generation and UTXO bookkeeping.

Amounts are Decimal throughout — float would introduce rounding that looks like
a signal to the clustering heuristics.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from .model import Actor, ScriptType, Transaction, Utxo

SAT = Decimal("0.00000001")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def make_address(rng, script: ScriptType) -> str:
    if script is ScriptType.P2WPKH:
        return "bc1q" + "".join(rng.choice(BECH32) for _ in range(38))
    prefix = "1" if script is ScriptType.P2PKH else "3"
    return prefix + "".join(rng.choice(B58) for _ in range(33))


def make_txid(rng) -> str:
    return hashlib.sha256(rng.randbytes(32)).hexdigest()


def quantize(value: Decimal) -> Decimal:
    return value.quantize(SAT)


class Chain:
    """Tracks who owns what, so every transaction actually balances."""

    def __init__(self, rng):
        self.rng = rng
        self.utxos: dict[str, list[Utxo]] = defaultdict(list)
        self.address_seen: set[str] = set()
        self.transactions: list[Transaction] = []

    # -- addresses ---------------------------------------------------------

    def new_address(self, actor: Actor, script: ScriptType | None = None) -> str:
        addr = make_address(self.rng, script or actor.script_pref)
        actor.addresses.append(addr)
        return addr

    def recipient_address(self, actor: Actor) -> str:
        """Where a payment to `actor` lands.

        Merchants and exchanges publish an address and receive to it repeatedly.
        Change never does this, which is what makes address reuse a usable
        signal for telling the two apart.
        """
        if actor.receive_pool and self.rng.random() < actor.reuse_prob:
            return self.rng.choice(actor.receive_pool)
        addr = self.new_address(actor)
        actor.receive_pool.append(addr)
        return addr

    # -- funding -----------------------------------------------------------

    def fund(self, actor: Actor, amount: Decimal, ts: datetime) -> Utxo:
        """Coinbase-style seed funding, outside the observed transaction set."""
        utxo = Utxo(make_txid(self.rng), 0, self.new_address(actor), quantize(amount), actor.actor_id)
        self.utxos[actor.actor_id].append(utxo)
        return utxo

    def balance(self, actor_id: str) -> Decimal:
        return sum((u.amount for u in self.utxos[actor_id]), Decimal(0))

    # -- spending ----------------------------------------------------------

    def select_inputs(self, actor_id: str, target: Decimal) -> list[Utxo] | None:
        """Greedy largest-first coin selection. Returns None if underfunded."""
        pool = sorted(self.utxos[actor_id], key=lambda u: u.amount, reverse=True)
        chosen: list[Utxo] = []
        total = Decimal(0)
        for utxo in pool:
            chosen.append(utxo)
            total += utxo.amount
            if total >= target:
                return chosen
        return None

    def spend(
        self,
        sender: Actor,
        recipients: list[tuple[Actor, Decimal]],
        ts: datetime,
        pattern: str = "normal",
        n_inputs_hint: int | None = None,
    ) -> Transaction | None:
        """Build one transaction. Change returns to a fresh address of `sender`.

        Returns None when the sender cannot cover the spend — callers skip.
        """
        fee = quantize(Decimal(sender.fee_rate) * Decimal(250) * SAT)
        payout = sum((amt for _, amt in recipients), Decimal(0))
        inputs = self.select_inputs(sender.actor_id, payout + fee)
        if inputs is None:
            return None

        if n_inputs_hint and len(inputs) < n_inputs_hint:
            extra = [u for u in self.utxos[sender.actor_id] if u not in inputs]
            inputs = inputs + extra[: n_inputs_hint - len(inputs)]

        total_in = sum((u.amount for u in inputs), Decimal(0))
        change = quantize(total_in - payout - fee)

        out_addrs: list[str] = []
        out_amounts: list[Decimal] = []
        for actor, amt in recipients:
            out_addrs.append(self.recipient_address(actor))
            out_amounts.append(quantize(amt))

        change_index = None
        if change > SAT:
            change_index = len(out_addrs)
            out_addrs.append(self.new_address(sender))
            out_amounts.append(change)

        txid = make_txid(self.rng)
        tx = Transaction(
            txid=txid,
            ts=ts,
            origin=sender.actor_id,
            inputs=inputs,
            output_addresses=out_addrs,
            output_amounts=out_amounts,
            fee=fee,
            script_type=sender.script_pref,
            change_index=change_index,
            pattern=pattern,
        )

        # settle the UTXO set
        for utxo in inputs:
            self.utxos[sender.actor_id].remove(utxo)
        owners = [a for a, _ in recipients] + ([sender] if change_index is not None else [])
        for vout, (addr, amt) in enumerate(zip(out_addrs, out_amounts)):
            self.utxos[owners[vout].actor_id].append(
                Utxo(txid, vout, addr, amt, owners[vout].actor_id)
            )

        self.address_seen.update(tx.input_addresses)
        self.transactions.append(tx)
        return tx

    def coinjoin(self, participants: list[Actor], denom: Decimal, ts: datetime) -> Transaction | None:
        """Many inputs, many equal-value outputs — deliberately breaks CIOH.

        Attributed to a random participant, since only one host broadcasts the
        finished transaction.
        """
        fee = quantize(Decimal(200) * Decimal(250) * SAT)
        share = quantize(denom + fee / len(participants))

        inputs: list[Utxo] = []
        joined: list[Actor] = []
        for actor in participants:
            picked = self.select_inputs(actor.actor_id, share)
            if picked is None:
                continue
            inputs.extend(picked)
            joined.append(actor)
        if len(joined) < 3:
            return None

        out_addrs, out_amounts, owners = [], [], []
        for actor in joined:
            out_addrs.append(self.new_address(actor))
            out_amounts.append(denom)
            owners.append(actor)

        # remainders back to each participant, still equal-denominated where possible
        total_in = sum((u.amount for u in inputs), Decimal(0))
        remainder = quantize(total_in - denom * len(joined) - fee)
        if remainder > SAT:
            out_addrs.append(self.new_address(joined[0]))
            out_amounts.append(remainder)
            owners.append(joined[0])

        txid = make_txid(self.rng)
        broadcaster = self.rng.choice(joined)
        tx = Transaction(
            txid=txid,
            ts=ts,
            origin=broadcaster.actor_id,
            inputs=inputs,
            output_addresses=out_addrs,
            output_amounts=out_amounts,
            fee=fee,
            script_type=ScriptType.P2WPKH,
            change_index=None,
            pattern="coinjoin",
        )

        for utxo in inputs:
            self.utxos[utxo.owner].remove(utxo)
        for vout, (addr, amt) in enumerate(zip(out_addrs, out_amounts)):
            self.utxos[owners[vout].actor_id].append(
                Utxo(txid, vout, addr, amt, owners[vout].actor_id)
            )

        self.address_seen.update(tx.input_addresses)
        self.transactions.append(tx)
        return tx
