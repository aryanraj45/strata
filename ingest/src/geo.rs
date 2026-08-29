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

    /// Returns `(country, asn, asn_org, came_from_db)`.
    pub fn resolve(
        &self,
        ip: &str,
        fallback_country: &str,
        fallback_asn: u32,
    ) -> (String, u32, String, bool) {
        let Ok(addr) = ip.parse::<IpAddr>() else {
            return (fallback_country.to_string(), fallback_asn, String::new(), false);
        };

        let country = self.city.as_ref().and_then(|r| {
            r.lookup::<geoip2::City>(addr)
                .ok()
                .and_then(|c| c.country.and_then(|c| c.iso_code.map(str::to_string)))
        });
        let asn = self.asn.as_ref().and_then(|r| {
            r.lookup::<geoip2::Asn>(addr).ok().map(|a| {
                (
                    a.autonomous_system_number.unwrap_or(0),
                    a.autonomous_system_organization.unwrap_or("").to_string(),
                )
            })
        });

        match (country, asn) {
            (Some(c), Some((n, org))) => (c, n, org, true),
            (Some(c), None) => (c, fallback_asn, String::new(), true),
            (None, Some((n, org))) => (fallback_country.to_string(), n, org, true),
            (None, None) => (fallback_country.to_string(), fallback_asn, String::new(), false),
        }
    }
}
