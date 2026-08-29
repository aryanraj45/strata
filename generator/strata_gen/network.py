"""P2P propagation simulation.

This is the part that decides whether the fusion engine has anything to find.

A transaction is broadcast by its origin host. Some fraction of the time one of
our listening nodes is a direct peer of that host, and sees it first — after a
sub-millisecond hop. The rest of the time we only see it after it has bounced
through relays, and the source address we record belongs to a relay, not the
originator.

Coverage below 1.0 and overlapping delay distributions are deliberate. If the
origin were always first, attribution would be a lookup rather than a statistical
inference, and any evaluation against this data would be meaningless.
"""

from __future__ import annotations

from datetime import timedelta

from .model import Actor, Observation, Transaction

P2P_PORT = 8333

# `lognormvariate(0, sigma)` has median 1 and mean exp(sigma^2 / 2), so scaling
# it by a constant sets the *median* delay, not the mean. Named accordingly: the
# realised means are about 11% and 28% higher than these figures respectively.
# Median is the more useful handle for latency anyway, since the distribution is
# skewed and the median is what "a typical hop" means.
#
# Direct hop from the originating host to one of our sensors.
DIRECT_MEDIAN_MS = 0.9
DIRECT_SIGMA = 0.45

# Arrival via one or more intermediate relays.
RELAY_MEDIAN_MS = 14.0
RELAY_SIGMA = 0.7


class Network:
    def __init__(self, rng, sensor_ips: list[str], relay_ips: list[str], coverage: float = 0.70):
        self.rng = rng
        self.sensors = sensor_ips
        self.relays = relay_ips
        self.coverage = coverage

    def _ephemeral_port(self) -> int:
        return self.rng.randint(32768, 60999)

    def observe(self, tx: Transaction, actor: Actor) -> list[Observation]:
        """Return every packet our sensors recorded for this transaction."""
        obs: list[Observation] = []
        origin_ip = actor.pick_ip(self.rng)

        direct = self.rng.random() < self.coverage
        if direct:
            delay_ms = self.rng.lognormvariate(0, DIRECT_SIGMA) * DIRECT_MEDIAN_MS
            obs.append(
                Observation(
                    txid=tx.txid,
                    ts=tx.ts + timedelta(milliseconds=delay_ms),
                    src_ip=origin_ip,
                    src_port=self._ephemeral_port(),
                    dst_ip=self.rng.choice(self.sensors),
                    dst_port=P2P_PORT,
                    is_origin=True,
                )
            )

        # Indirect arrivals always happen — the transaction reaches the whole
        # network regardless of whether we were a direct peer.
        for _ in range(self.rng.randint(2, 5)):
            delay_ms = self.rng.lognormvariate(0, RELAY_SIGMA) * RELAY_MEDIAN_MS
            obs.append(
                Observation(
                    txid=tx.txid,
                    ts=tx.ts + timedelta(milliseconds=delay_ms),
                    src_ip=self.rng.choice(self.relays),
                    src_port=self._ephemeral_port(),
                    dst_ip=self.rng.choice(self.sensors),
                    dst_port=P2P_PORT,
                    is_origin=False,
                )
            )

        obs.sort(key=lambda o: o.ts)
        return obs
