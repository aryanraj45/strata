"""Builds the world and drives activity through it.

Everything planted here is recorded in ground truth so detection can be scored
rather than asserted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .chain import Chain
from .geo import IpPool
from .model import Actor, ActorKind, ScriptType, Transaction
from .network import Network

# How many of each archetype, and how they are funded.
POPULATION = [
    (ActorKind.EXCHANGE, 4, Decimal("400")),
    (ActorKind.MINING_POOL, 2, Decimal("250")),
    (ActorKind.MERCHANT, 12, Decimal("18")),
    (ActorKind.NORMAL, 45, Decimal("6")),
    (ActorKind.RANSOMWARE, 3, Decimal("2")),
    (ActorKind.DARKNET_MARKET, 2, Decimal("30")),
    (ActorKind.EXTORTION, 2, Decimal("2")),
    (ActorKind.LAUNDERER, 3, Decimal("60")),
    (ActorKind.MIXER, 1, Decimal("40")),
]

# Fee-estimation behaviour differs per wallet client — a fingerprinting signal.
FEE_PROFILES = {
    ScriptType.P2WPKH: (8, 14),
    ScriptType.P2SH: (18, 26),
    ScriptType.P2PKH: (30, 44),
}


class World:
    def __init__(self, rng, start: datetime | None = None, coverage: float = 0.70):
        self.rng = rng
        self.ips = IpPool(rng)
        self.chain = Chain(rng)
        self.start = start or datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.actors: dict[str, Actor] = {}
        self.by_kind: dict[ActorKind, list[Actor]] = {k: [] for k in ActorKind}
        self.truth = {
            "peeling_chains": [],
            "coinjoins": [],
            "cashouts": [],
        }

        sensors = self.ips.allocate(8)
        relays = self.ips.allocate(60)
        self.sensor_ips = sensors
        self.net = Network(rng, sensors, relays, coverage=coverage)

        self._populate()

    # -- setup -------------------------------------------------------------

    def _populate(self) -> None:
        counter = 0
        for kind, count, funding in POPULATION:
            for _ in range(count):
                counter += 1
                actor = self._make_actor(f"E-{counter:04d}", kind)
                self.actors[actor.actor_id] = actor
                self.by_kind[kind].append(actor)
                # split funding across a few UTXOs so coin selection has choices
                for _ in range(self.rng.randint(2, 5)):
                    self.chain.fund(actor, funding / 3, self.start)

    def _make_actor(self, actor_id: str, kind: ActorKind) -> Actor:
        # Older script types persist mainly among long-lived services.
        if kind in (ActorKind.EXCHANGE, ActorKind.MINING_POOL):
            script = self.rng.choice([ScriptType.P2SH, ScriptType.P2WPKH])
        elif kind is ActorKind.NORMAL:
            script = self.rng.choices(
                [ScriptType.P2WPKH, ScriptType.P2PKH, ScriptType.P2SH], [6, 2, 2]
            )[0]
        else:
            script = ScriptType.P2WPKH

        reuse = {
            ActorKind.EXCHANGE: 0.55,
            ActorKind.MERCHANT: 0.45,
            ActorKind.MINING_POOL: 0.30,
            ActorKind.DARKNET_MARKET: 0.25,
        }.get(kind, 0.03)

        obfuscated = kind.illicit and self.rng.random() < 0.35
        ips = self.ips.allocate(self.rng.randint(1, 3), vpn=obfuscated)
        geo = self.ips.geo(ips[0])
        lo, hi = FEE_PROFILES[script]

        return Actor(
            actor_id=actor_id,
            kind=kind,
            origin_ips=ips,
            country=geo.country,
            asn=geo.asn,
            asn_org=geo.asn_org,
            script_pref=script,
            fee_rate=self.rng.randint(lo, hi),
            obfuscated=obfuscated,
            reuse_prob=reuse,
        )

    # -- activity ----------------------------------------------------------

    def _payment(self, lo: float, hi: float) -> Decimal:
        """A payment amount.

        People pay round numbers most of the time. Change is whatever is left
        over, so it is almost never round — which is the whole basis of the
        round-number change heuristic.
        """
        if self.rng.random() < 0.55:
            step = self.rng.choice([Decimal("0.001"), Decimal("0.01"), Decimal("0.05"),
                                    Decimal("0.1"), Decimal("0.25")])
            steps = max(1, int(self.rng.uniform(lo, hi) / float(step)))
            return (step * steps).quantize(Decimal("0.00000001"))
        return Decimal(str(round(self.rng.uniform(lo, hi), 8)))

    def _ts(self, day: float) -> datetime:
        """A timestamp `day` days in, with a plausible hour-of-day bias."""
        hour = self.rng.choices(range(24), weights=[2] * 6 + [5] * 12 + [3] * 6)[0]
        return self.start + timedelta(
            days=day,
            hours=hour,
            minutes=self.rng.randint(0, 59),
            seconds=self.rng.randint(0, 59),
            microseconds=self.rng.randint(0, 999999),
        )

    def _ts_nocturnal(self, day: float) -> datetime:
        """Illicit operators skew to the small hours — a real timing signal."""
        hour = self.rng.choices(range(24), weights=[8] * 5 + [1] * 14 + [4] * 5)[0]
        return self.start + timedelta(
            days=day,
            hours=hour,
            minutes=self.rng.randint(0, 59),
            seconds=self.rng.randint(0, 59),
            microseconds=self.rng.randint(0, 999999),
        )

    def normal_traffic(self, n: int, days: int) -> None:
        payers = self.by_kind[ActorKind.NORMAL] + self.by_kind[ActorKind.MERCHANT]
        payees = payers + self.by_kind[ActorKind.EXCHANGE]
        for _ in range(n):
            sender = self.rng.choice(payers)
            recipient = self.rng.choice([a for a in payees if a is not sender])
            amount = self._payment(0.01, 0.9)
            self.chain.spend(sender, [(recipient, amount)], self._ts(self.rng.uniform(0, days)))

    def consolidations(self, n: int, days: int) -> None:
        """Multi-input spends — the raw material for CIOH clustering."""
        for _ in range(n):
            sender = self.rng.choice(self.by_kind[ActorKind.NORMAL])
            recipient = self.rng.choice(self.by_kind[ActorKind.EXCHANGE])
            amount = self._payment(0.3, 1.5)
            self.chain.spend(
                sender,
                [(recipient, amount)],
                self._ts(self.rng.uniform(0, days)),
                pattern="consolidation",
                n_inputs_hint=self.rng.randint(3, 6),
            )

    def peeling_chain(self, actor: Actor, hops: int, day_start: float) -> None:
        """Repeatedly shed a small amount, carry the remainder to a fresh address."""
        txids: list[str] = []
        t = day_start
        for _ in range(hops):
            exchange = self.rng.choice(self.by_kind[ActorKind.EXCHANGE])
            balance = self.chain.balance(actor.actor_id)
            if balance < Decimal("0.05"):
                break
            peel = balance * Decimal(str(round(self.rng.uniform(0.02, 0.05), 4)))
            # Launderers move round amounts into exchanges; the remainder that
            # carries on down the chain is whatever is left, and is not round.
            if self.rng.random() < 0.6:
                step = self.rng.choice([Decimal("0.001"), Decimal("0.005"), Decimal("0.01")])
                peel = (step * max(1, int(peel / step))).quantize(Decimal("0.00000001"))
            else:
                peel = peel.quantize(Decimal("0.00000001"))
            tx = self.chain.spend(
                actor, [(exchange, peel)], self._ts_nocturnal(t), pattern="peel"
            )
            if tx is None:
                break
            txids.append(tx.txid)
            t += self.rng.uniform(0.02, 0.2)

        if len(txids) >= 5:
            self.truth["peeling_chains"].append(
                {"actor_id": actor.actor_id, "hops": len(txids), "txids": txids}
            )

    def coinjoin_round(self, day: float) -> None:
        pool = self.rng.sample(
            self.by_kind[ActorKind.NORMAL] + self.by_kind[ActorKind.LAUNDERER],
            k=self.rng.randint(5, 9),
        )
        tx = self.chain.coinjoin(pool, Decimal("0.10000000"), self._ts(day))
        if tx is not None:
            self.truth["coinjoins"].append(
                {"txid": tx.txid, "participants": sorted({u.owner for u in tx.inputs})}
            )

    def cash_out(self, actor: Actor, day: float) -> None:
        exchange = self.rng.choice(self.by_kind[ActorKind.EXCHANGE])
        balance = self.chain.balance(actor.actor_id)
        if balance < Decimal("0.2"):
            return
        amount = (balance * Decimal("0.6")).quantize(Decimal("0.00000001"))
        tx = self.chain.spend(actor, [(exchange, amount)], self._ts_nocturnal(day), pattern="cashout")
        if tx is not None:
            self.truth["cashouts"].append({"txid": tx.txid, "actor_id": actor.actor_id})

    def run(self, days: int = 14, volume: int = 900) -> None:
        self.normal_traffic(int(volume * 0.62), days)
        self.consolidations(int(volume * 0.12), days)

        for _ in range(max(2, days // 3)):
            self.coinjoin_round(self.rng.uniform(0, days))

        # Ransomware and extortion collect, then launder out.
        for actor in self.by_kind[ActorKind.RANSOMWARE] + self.by_kind[ActorKind.EXTORTION]:
            for _ in range(self.rng.randint(6, 14)):
                victim = self.rng.choice(self.by_kind[ActorKind.NORMAL])
                self.chain.spend(
                    victim,
                    [(actor, self._payment(0.15, 0.6))],
                    self._ts(self.rng.uniform(0, days * 0.4)),
                    pattern="ransom_payment",
                )
            self.peeling_chain(actor, self.rng.randint(12, 30), days * 0.5)
            self.cash_out(actor, days * 0.95)

        for actor in self.by_kind[ActorKind.LAUNDERER]:
            self.peeling_chain(actor, self.rng.randint(20, 45), days * 0.3)
            self.cash_out(actor, days * 0.9)

        for actor in self.by_kind[ActorKind.DARKNET_MARKET]:
            for _ in range(self.rng.randint(20, 40)):
                buyer = self.rng.choice(self.by_kind[ActorKind.NORMAL])
                self.chain.spend(
                    buyer,
                    [(actor, self._payment(0.005, 0.08))],
                    self._ts_nocturnal(self.rng.uniform(0, days)),
                    pattern="darknet_purchase",
                )
            self.peeling_chain(actor, self.rng.randint(10, 25), days * 0.6)

        self.chain.transactions.sort(key=lambda t: t.ts)

    # -- observation -------------------------------------------------------

    def observe_all(self) -> list:
        obs = []
        for tx in self.chain.transactions:
            obs.extend(self.net.observe(tx, self.actors[tx.origin]))
        obs.sort(key=lambda o: o.ts)
        return obs
