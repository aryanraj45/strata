# GeoIP databases

Committed rather than downloaded at run time. The problem statement requires a
complete offline solution, and a clone that cannot resolve an address is not one.

| File | Type | Source |
|---|---|---|
| `dbip-country-lite.mmdb` | `DBIP-Country-Lite` | [DB-IP](https://db-ip.com/db/download/ip-to-country-lite) |
| `dbip-asn-lite.mmdb` | `DBIP-ASN-Lite` (GeoLite2-ASN compatible) | [DB-IP](https://db-ip.com/db/download/ip-to-asn-lite) |

## Attribution

These databases are made available by [DB-IP](https://db-ip.com) under the
[Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).

DB-IP Lite was chosen over MaxMind GeoLite2 because it is directly downloadable
without an account. GeoLite2 works identically — the loader identifies databases
by their own metadata rather than by filename, so either vendor drops in.

## Usage

```bash
strata-ingest data/*.json --geoip data/geoip --out store
```

Without `--geoip`, the ingest falls back to the country and ASN carried on each
record. Which path served each row is recorded per field in `country_source` and
`asn_source`, so an attribution is never silently assumed.

## Note on the synthetic dataset

Our generated data uses RFC 5737 documentation ranges, which no GeoIP database
contains — assigning criminal behaviour to a real network operator's address
space would be both wrong and useless. So on synthetic data every row falls back
by design. The database path is exercised by the integration tests in
`ingest/src/geo.rs`, which resolve real routable addresses.
