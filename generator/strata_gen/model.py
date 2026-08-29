"""Core data types for the synthetic dataset."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ActorKind(str, Enum):
    """What an entity is, in ground-truth terms."""

    NORMAL = "normal"
    EXCHANGE = "exchange"
    MINING_POOL = "mining_pool"
    MERCHANT = "merchant"
    RANSOMWARE = "ransomware"
    DARKNET_MARKET = "darknet_market"
    EXTORTION = "extortion"
    LAUNDERER = "launderer"
    MIXER = "mixer"

    @property
    def illicit(self) -> bool:
        return self in _ILLICIT


_ILLICIT = frozenset(
    {
        ActorKind.RANSOMWARE,
        ActorKind.DARKNET_MARKET,
        ActorKind.EXTORTION,
        ActorKind.LAUNDERER,
    }
)


class ScriptType(str, Enum):
    P2PKH = "p2pkh"  # 1...
    P2SH = "p2sh"  # 3...
    P2WPKH = "v0_p2wpkh"  # bc1q...


@dataclass
class Actor:
    """A real-world entity controlling one or more wallets.

    Ground truth only — the system under test never sees this.
    """

    actor_id: str
    kind: ActorKind
    origin_ips: list[str]
    country: str
    asn: int
    asn_org: str
    script_pref: ScriptType
    fee_rate: int  # sat/vB; a per-wallet-software fingerprint
    obfuscated: bool = False  # routes through VPN/Tor
    # Services that publish a deposit address reuse it; individuals rarely do.
    reuse_prob: float = 0.0
    addresses: list[str] = field(default_factory=list)
    # Addresses handed out to receive payments. Kept apart from change
    # addresses: a wallet never publishes a change address as a deposit
    # address, and conflating the two destroys the signal that tells them apart.
    receive_pool: list[str] = field(default_factory=list)

    def pick_ip(self, rng) -> str:
        return rng.choice(self.origin_ips)


@dataclass
class Utxo:
    txid: str
    vout: int
    address: str
    amount: Decimal
    owner: str  # actor_id


@dataclass
class Transaction:
    txid: str
    ts: datetime
    origin: str  # actor_id that broadcast it
    inputs: list[Utxo]
    output_addresses: list[str]
    output_amounts: list[Decimal]
    fee: Decimal
    script_type: ScriptType
    # ground truth
    change_index: int | None = None
    pattern: str = "normal"

    @property
    def input_addresses(self) -> list[str]:
        return [u.address for u in self.inputs]

    @property
    def input_amounts(self) -> list[Decimal]:
        return [u.amount for u in self.inputs]


@dataclass
class Observation:
    """One packet seen by one of our listening nodes."""

    txid: str
    ts: datetime
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    # ground truth
    is_origin: bool = False
