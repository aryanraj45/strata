//! GeoIP enrichment from a local MaxMind-format database.
//!
//! The lookup is a file read, never a network call — that is the whole point of
//! shipping the database rather than querying a service.
//!
//! When no database is present, or an address is absent from it, we fall back to
//! the country/ASN already carried on the record. The synthetic dataset uses
//! RFC 5737 documentation ranges, which no real GeoIP database contains, so the
//! fallback is the normal path in development and the .mmdb path is what runs on
//! real capture data. Which path served each record is reported, so an analyst is
//! never guessing where an attribution came from.

use anyhow::{Context, Result};
use maxminddb::{geoip2, Reader};
use std::net::IpAddr;
use std::path::Path;

pub struct GeoResolver {
    country: Option<CountryDb>,
    asn: Option<Reader<Vec<u8>>>,
}

/// A country database, plus which record shape it holds.
///
/// MaxMind's City databases and DB-IP's Country-Lite both answer "which country
/// is this address in", but the record types differ, so the reader has to know
/// which one it opened.
struct CountryDb {
    reader: Reader<Vec<u8>>,
    city_shaped: bool,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct GeoCounts {
    pub from_db: usize,
    pub from_record: usize,
}

/// Where one field's value came from. Tracked per field rather than per row:
/// country and ASN are separate databases, and either can miss independently.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Database,
    Record,
}

impl Source {
    pub fn as_str(self) -> &'static str {
        match self {
            Source::Database => "db",
            Source::Record => "record",
        }
    }
}

/// A resolved location, with each field carrying its own provenance.
#[derive(Debug, Clone)]
pub struct Resolved {
    pub country: String,
    pub country_source: Source,
    pub asn: u32,
    pub asn_org: String,
    pub asn_source: Source,
}

impl GeoResolver {
    /// Loads whatever databases are present in `dir`, identified by their own
    /// metadata rather than by filename.
    ///
    /// Vendors disagree on naming -- MaxMind ships GeoLite2-City.mmdb, DB-IP
    /// ships dbip-country-lite-YYYY-MM.mmdb -- and hardcoding either means the
    /// other silently does nothing. Every .mmdb in the directory is opened and
    /// classified by its database_type. Missing files are not an error.
    pub fn load(dir: Option<&Path>) -> Result<Self> {
        let Some(dir) = dir else {
            return Ok(Self { country: None, asn: None });
        };

        let mut country = None;
        let mut asn = None;

        let entries = std::fs::read_dir(dir)
            .with_context(|| format!("reading {}", dir.display()))?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("mmdb") {
                continue;
            }
            let reader = Reader::open_readfile(&path)
                .with_context(|| format!("opening {}", path.display()))?;
            let kind = reader.metadata.database_type.clone();

            if kind.contains("ASN") {
                asn = Some(reader);
            } else if kind.contains("City") || kind.contains("Country") {
                country = Some(CountryDb {
                    reader,
                    city_shaped: kind.contains("City"),
                });
            }
        }

        Ok(Self { country, asn })
    }

    pub fn available(&self) -> bool {
        self.country.is_some() || self.asn.is_some()
    }

    /// Resolve one address, falling back to the record's own fields per field.
    pub fn resolve(&self, ip: &str, fallback_country: &str, fallback_asn: u32) -> Resolved {
        let fallback = |country_src, asn_src| Resolved {
            country: fallback_country.to_string(),
            country_source: country_src,
            asn: fallback_asn,
            asn_org: String::new(),
            asn_source: asn_src,
        };

        let Ok(addr) = ip.parse::<IpAddr>() else {
            return fallback(Source::Record, Source::Record);
        };

        let country = self.country.as_ref().and_then(|db| {
            if db.city_shaped {
                db.reader
                    .lookup::<geoip2::City>(addr)
                    .ok()
                    .and_then(|c| c.country.and_then(|c| c.iso_code.map(str::to_string)))
            } else {
                db.reader
                    .lookup::<geoip2::Country>(addr)
                    .ok()
                    .and_then(|c| c.country.and_then(|c| c.iso_code.map(str::to_string)))
            }
        });

        // A record with no autonomous system number is a miss, not AS0 -- that
        // number is reserved, and mapping "absent" onto it would be
        // indistinguishable from a real value downstream.
        let asn = self.asn.as_ref().and_then(|r| {
            r.lookup::<geoip2::Asn>(addr).ok().and_then(|a| {
                a.autonomous_system_number.map(|n| {
                    (n, a.autonomous_system_organization.unwrap_or("").to_string())
                })
            })
        });

        let mut out = fallback(Source::Record, Source::Record);
        if let Some(c) = country {
            out.country = c;
            out.country_source = Source::Database;
        }
        if let Some((n, org)) = asn {
            out.asn = n;
            out.asn_org = org;
            out.asn_source = Source::Database;
        }
        out
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    fn databases() -> Option<GeoResolver> {
        let dir = Path::new("../data/geoip");
        if !dir.exists() {
            return None;   // databases are optional; the fallback path is tested elsewhere
        }
        GeoResolver::load(Some(dir)).ok().filter(|r| r.available())
    }

    #[test]
    fn resolves_real_addresses_from_the_database() {
        let Some(resolver) = databases() else { return };

        // Deliberately wrong fallbacks: anything correct below came from the
        // database, not from the record.
        for (ip, country, asn) in [
            ("8.8.8.8", "US", 15169u32),
            ("1.1.1.1", "AU", 13335),
            ("9.9.9.9", "US", 19281),
        ] {
            let r = resolver.resolve(ip, "ZZ", 0);
            assert_eq!(r.country, country, "country for {ip}");
            assert_eq!(r.asn, asn, "asn for {ip}");
            assert_eq!(r.country_source, Source::Database, "country provenance for {ip}");
            assert_eq!(r.asn_source, Source::Database, "asn provenance for {ip}");
            assert!(!r.asn_org.is_empty(), "org for {ip}");
        }
    }

    #[test]
    fn falls_back_for_addresses_no_database_contains() {
        let Some(resolver) = databases() else { return };

        // RFC 5737 documentation ranges and RFC 1918 private space are absent
        // from every GeoIP database. Falling back is correct; claiming the
        // database answered would not be.
        for ip in ["203.0.113.44", "198.51.100.7", "192.168.1.1"] {
            let r = resolver.resolve(ip, "IN", 64512);
            assert_eq!(r.country, "IN", "fallback country for {ip}");
            assert_eq!(r.asn, 64512, "fallback asn for {ip}");
            assert_eq!(r.country_source, Source::Record, "provenance for {ip}");
        }
    }

    #[test]
    fn malformed_addresses_fall_back_without_panicking() {
        let resolver = GeoResolver::load(None).unwrap();
        let r = resolver.resolve("not-an-ip", "IN", 64512);
        assert_eq!(r.country, "IN");
        assert_eq!(r.country_source, Source::Record);
    }
}
