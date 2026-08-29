//! The one internal shape every input format is normalised into.
//!
//! Amounts are integer satoshis. Bitcoin values are exact multiples of 1e-8 and
//! floats cannot represent them faithfully — a rounding artefact here would look
//! like a signal to the change-detection heuristics downstream.

use anyhow::{bail, Context, Result};

/// One packet observed by a listening node, carrying the transaction it announced.
#[derive(Debug, Clone)]
pub struct Observation {
    pub timestamp: i64, // microseconds since epoch
    pub src_ip: String,
    pub src_port: u16,
    pub dst_ip: String,
    pub dst_port: u16,
    pub txid: String,
    pub input_addresses: Vec<String>,
    pub output_addresses: Vec<String>,
    pub input_amounts: Vec<i64>,
    pub output_amounts: Vec<i64>,
    pub fee: i64,
    pub script_type: String,
    pub geo_country: String,
    pub asn: u32,
    /// Filled by enrichment, not by the parsers.
    pub asn_org: String,
    /// Which path produced the country: `"db"` or `"record"`.
    pub country_source: String,
    /// Which path produced the ASN: `"db"` or `"record"`.
    pub asn_source: String,
}

impl Observation {
    /// Stable identity for deduplication: the same packet seen twice is one row.
    pub fn dedup_key(&self) -> (String, i64, String) {
        (self.txid.clone(), self.timestamp, self.src_ip.clone())
    }
}

/// Parse a decimal BTC string into satoshis without going through f64.
///
/// Accepts `0.42`, `0.42000000`, `12`, `-0.5`. Rejects anything with more than
/// eight fractional digits rather than silently truncating evidence.
pub fn btc_to_sats(text: &str) -> Result<i64> {
    let text = text.trim();
    if text.is_empty() {
        bail!("empty amount");
    }
    let (neg, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };

    let (whole, frac) = match digits.split_once('.') {
        Some((w, f)) => (w, f),
        None => (digits, ""),
    };
    if frac.len() > 8 {
        bail!("more than 8 decimal places: {text}");
    }
    if whole.is_empty() && frac.is_empty() {
        bail!("not a number: {text}");
    }

    let whole_val: i64 = if whole.is_empty() {
        0
    } else {
        whole.parse().with_context(|| format!("bad integer part: {text}"))?
    };
    let mut frac_val: i64 = 0;
    for ch in frac.chars() {
        let d = ch.to_digit(10).with_context(|| format!("bad fraction: {text}"))?;
        frac_val = frac_val * 10 + d as i64;
    }
    frac_val *= 10_i64.pow(8 - frac.len() as u32);

    let sats = whole_val
        .checked_mul(100_000_000)
        .and_then(|v| v.checked_add(frac_val))
        .with_context(|| format!("amount overflows i64: {text}"))?;

    Ok(if neg { -sats } else { sats })
}

/// Parse an RFC 3339 timestamp to microseconds since the Unix epoch.
///
/// Hand-rolled rather than pulling in a date library: the generator emits a
/// single well-defined shape, and a narrow parser is easier to fuzz.
pub fn parse_timestamp(text: &str) -> Result<i64> {
    let text = text.trim().trim_end_matches('Z');
    let (date, rest) = text.split_once('T').context("timestamp missing 'T'")?;
    let mut date_parts = date.split('-');
    let year: i64 = date_parts.next().context("no year")?.parse()?;
    let month: i64 = date_parts.next().context("no month")?.parse()?;
    let day: i64 = date_parts.next().context("no day")?.parse()?;

    // Strip any offset; the generator emits UTC.
    let rest = rest.split(['+']).next().unwrap_or(rest);
    let (clock, micros) = match rest.split_once('.') {
        Some((c, frac)) => {
            let frac: String = frac.chars().take(6).collect();
            let scaled: i64 = frac.parse().unwrap_or(0);
            (c, scaled * 10_i64.pow(6 - frac.len() as u32))
        }
        None => (rest, 0),
    };
    let mut clock_parts = clock.split(':');
    let hour: i64 = clock_parts.next().context("no hour")?.parse()?;
    let minute: i64 = clock_parts.next().context("no minute")?.parse()?;
    let second: i64 = clock_parts.next().unwrap_or("0").parse()?;

    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        bail!("date out of range: {text}");
    }

    let days = days_from_civil(year, month, day);
    Ok(((days * 86_400 + hour * 3_600 + minute * 60 + second) * 1_000_000) + micros)
}

/// Howard Hinnant's civil-date algorithm: days since 1970-01-01.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn amounts_are_exact() {
        assert_eq!(btc_to_sats("0.42000000").unwrap(), 42_000_000);
        assert_eq!(btc_to_sats("1").unwrap(), 100_000_000);
        assert_eq!(btc_to_sats("0.00000001").unwrap(), 1);
        assert_eq!(btc_to_sats(".5").unwrap(), 50_000_000);
        assert_eq!(btc_to_sats("-0.5").unwrap(), -50_000_000);
        // 0.1 + 0.2 in satoshis is exact; in f64 it is not.
        assert_eq!(
            btc_to_sats("0.1").unwrap() + btc_to_sats("0.2").unwrap(),
            btc_to_sats("0.3").unwrap()
        );
    }

    #[test]
    fn bad_amounts_are_errors_not_panics() {
        assert!(btc_to_sats("").is_err());
        assert!(btc_to_sats("abc").is_err());
        assert!(btc_to_sats("0.123456789").is_err());
        assert!(btc_to_sats("99999999999999999999").is_err());
    }

    #[test]
    fn timestamps_round_trip() {
        assert_eq!(parse_timestamp("1970-01-01T00:00:00Z").unwrap(), 0);
        assert_eq!(parse_timestamp("2026-08-01T00:00:00Z").unwrap(), 1_785_542_400_000_000);
        let a = parse_timestamp("2026-08-01T00:00:00.000481Z").unwrap();
        let b = parse_timestamp("2026-08-01T00:00:00.000000Z").unwrap();
        assert_eq!(a - b, 481);
    }

    #[test]
    fn bad_timestamps_are_errors_not_panics() {
        assert!(parse_timestamp("").is_err());
        assert!(parse_timestamp("not-a-date").is_err());
        assert!(parse_timestamp("2026-13-01T00:00:00Z").is_err());
    }
}
