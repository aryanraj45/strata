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
    city: Option<Reader<Vec<u8>>>,
    asn: Option<Reader<Vec<u8>>>,
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
    /// Loads whichever of the two databases exist. Missing files are not an error.
    pub fn load(dir: Option<&Path>) -> Result<Self> {
        let Some(dir) = dir else {
            return Ok(Self { city: None, asn: None });
        };
        let open = |name: &str| -> Result<Option<Reader<Vec<u8>>>> {
            let path = dir.join(name);
            if !path.exists() {
                return Ok(None);
            }
            let reader = Reader::open_readfile(&path)
                .with_context(|| format!("opening {}", path.display()))?;
            Ok(Some(reader))
        };
        Ok(Self {
            city: open("GeoLite2-City.mmdb")?,
            asn: open("GeoLite2-ASN.mmdb")?,
        })
    }

    pub fn available(&self) -> bool {
        self.city.is_some() || self.asn.is_some()
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

        let country = self.city.as_ref().and_then(|r| {
            r.lookup::<geoip2::City>(addr)
                .ok()
                .and_then(|c| c.country.and_then(|c| c.iso_code.map(str::to_string)))
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
