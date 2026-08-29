"""Writers for the three required formats, plus the ground-truth sidecar.

One row per network observation, with the transaction payload denormalised onto
it — that is what the problem statement's field list describes, and it is the
shape the fusion engine needs: the same TXID appearing several times with
different source addresses and arrival times.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import Observation, Transaction
from .scenario import World

FIELDS = [
    "timestamp",
    "src_ip",
    "src_port",
    "dst_ip",
    "dst_port",
    "txid",
    "input_addresses",
    "output_addresses",
    "input_amounts",
    "output_amounts",
    "fee",
    "script_type",
    "geo_country",
    "asn",
]

SEP = ";"  # array separator inside CSV cells


def _iso(ts) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _amt(value: Decimal) -> str:
    return f"{value:.8f}"


def build_records(world: World) -> list[dict]:
    """Join every observation to its transaction."""
    by_txid: dict[str, Transaction] = {t.txid: t for t in world.chain.transactions}
    records = []
    for obs in world.observe_all():
        tx = by_txid[obs.txid]
        geo = world.ips.geo(obs.src_ip)
        records.append(
            {
                "timestamp": _iso(obs.ts),
                "src_ip": obs.src_ip,
                "src_port": obs.src_port,
                "dst_ip": obs.dst_ip,
                "dst_port": obs.dst_port,
                "txid": tx.txid,
                "input_addresses": tx.input_addresses,
                "output_addresses": tx.output_addresses,
                "input_amounts": [_amt(a) for a in tx.input_amounts],
                "output_amounts": [_amt(a) for a in tx.output_amounts],
                "fee": _amt(tx.fee),
                "script_type": tx.script_type.value,
                "geo_country": geo.country,
                "asn": geo.asn,
            }
        )
    return records


def write_json(records: list[dict], path: Path) -> None:
    path.write_text(json.dumps(records, indent=None, separators=(",", ":")) + "\n")


def write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            for key in ("input_addresses", "output_addresses", "input_amounts", "output_amounts"):
                row[key] = SEP.join(rec[key])
            writer.writerow(row)


def write_xml(records: list[dict], path: Path) -> None:
    root = ET.Element("observations")
    for rec in records:
        node = ET.SubElement(root, "observation")
        for key in FIELDS:
            value = rec[key]
            if isinstance(value, list):
                wrapper = ET.SubElement(node, key)
                for item in value:
                    ET.SubElement(wrapper, "item").text = item
            else:
                ET.SubElement(node, key).text = str(value)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_truth(world: World) -> dict:
    """Everything the system under test must never read."""
    clusters = []
    for actor in world.actors.values():
        if not actor.addresses:
            continue
        clusters.append(
            {
                "actor_id": actor.actor_id,
                "kind": actor.kind.value,
                "illicit": actor.kind.illicit,
                "obfuscated": actor.obfuscated,
                "origin_ips": actor.origin_ips,
                "country": actor.country,
                "asn": actor.asn,
                "script_pref": actor.script_pref.value,
                "fee_rate": actor.fee_rate,
                "n_addresses": len(actor.addresses),
                "addresses": actor.addresses,
            }
        )

    tx_origin = {t.txid: t.origin for t in world.chain.transactions}
    change = {
        t.txid: t.output_addresses[t.change_index]
        for t in world.chain.transactions
        if t.change_index is not None
    }

    return {
        "sensors": world.sensor_ips,
        "coverage": world.net.coverage,
        "n_transactions": len(world.chain.transactions),
        "clusters": clusters,
        "tx_origin_actor": tx_origin,
        "change_addresses": change,
        "peeling_chains": world.truth["peeling_chains"],
        "coinjoins": world.truth["coinjoins"],
        "cashouts": world.truth["cashouts"],
        "ip_geo": world.ips.geo_table(),
    }


def write_all(world: World, outdir: Path, stem: str = "strata") -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    records = build_records(world)

    write_json(records, outdir / f"{stem}.json")
    write_csv(records, outdir / f"{stem}.csv")
    write_xml(records, outdir / f"{stem}.xml")

    truth = build_truth(world)
    (outdir / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n")

    return {"records": len(records), "transactions": truth["n_transactions"]}
