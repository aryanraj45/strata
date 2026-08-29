"""Synthetic IP allocation with consistent geo/ASN attribution.

Addresses come from the RFC 5737 documentation ranges and ASNs from the private
range (64512-65534). Nothing here maps to a real network operator — assigning
criminal behaviour to a real ISP's address space in a demo dataset would be both
wrong and useless.

Each /24 is partitioned into blocks with a fixed country and ASN, so an IP always
resolves to the same location. That gives the fusion engine a real geo/ASN signal
to work with without pretending to be a GeoIP database.
"""

from __future__ import annotations

from dataclasses import dataclass

# (base, first_octet4, last_octet4, country, asn, org)
_BLOCKS = [
    ("203.0.113", 1, 60, "IN", 64512, "SYNTH-AS-Bharat-Net"),
    ("203.0.113", 61, 120, "IN", 64513, "SYNTH-AS-Deccan-Broadband"),
    ("203.0.113", 121, 180, "SG", 64520, "SYNTH-AS-Straits-Transit"),
    ("203.0.113", 181, 254, "AE", 64521, "SYNTH-AS-Gulf-Peering"),
    ("198.51.100", 1, 80, "DE", 64530, "SYNTH-AS-Rhein-IX"),
    ("198.51.100", 81, 160, "NL", 64531, "SYNTH-AS-Randstad-Hosting"),
    ("198.51.100", 161, 254, "US", 64532, "SYNTH-AS-Cascade-Backbone"),
    ("192.0.2", 1, 90, "RU", 64540, "SYNTH-AS-Volga-Telecom"),
    ("192.0.2", 91, 170, "PA", 64541, "SYNTH-AS-Isthmus-VPN"),
    ("192.0.2", 171, 254, "SC", 64542, "SYNTH-AS-Offshore-Relay"),
]

# Blocks that read as anonymising infrastructure — used for obfuscated actors.
VPN_BLOCKS = {64541, 64542}


@dataclass(frozen=True)
class GeoInfo:
    country: str
    asn: int
    asn_org: str


class IpPool:
    """Hands out addresses without repeats and remembers where each one lives."""

    def __init__(self, rng):
        self.rng = rng
        self._issued: set[str] = set()
        self._geo: dict[str, GeoInfo] = {}

    def geo(self, ip: str) -> GeoInfo:
        return self._geo[ip]

    def _take(self, block) -> str:
        base, lo, hi, country, asn, org = block
        for _ in range(200):
            ip = f"{base}.{self.rng.randint(lo, hi)}"
            if ip not in self._issued:
                self._issued.add(ip)
                self._geo[ip] = GeoInfo(country, asn, org)
                return ip
        raise RuntimeError(f"block {base}.{lo}-{hi} exhausted")

    def allocate(self, n: int = 1, vpn: bool = False) -> list[str]:
        pool = [b for b in _BLOCKS if (b[4] in VPN_BLOCKS) == vpn]
        return [self._take(self.rng.choice(pool)) for _ in range(n)]

    def geo_table(self) -> dict[str, dict]:
        return {
            ip: {"country": g.country, "asn": g.asn, "asn_org": g.asn_org}
            for ip, g in self._geo.items()
        }
