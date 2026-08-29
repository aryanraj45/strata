//! JSON: either a top-level array of objects, or one object per line (JSONL).

use crate::record::{btc_to_sats, parse_timestamp, Observation};
use anyhow::{Context, Result};
use serde_json::Value;
use std::path::Path;

fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn sats(value: Option<&Value>) -> Result<Vec<i64>> {
    let Some(arr) = value.and_then(|v| v.as_array()) else {
        return Ok(Vec::new());
    };
    arr.iter()
        .map(|x| match x {
            Value::String(s) => btc_to_sats(s),
            Value::Number(n) => btc_to_sats(&n.to_string()),
            other => anyhow::bail!("amount is not a string or number: {other}"),
        })
        .collect()
}

fn from_value(v: &Value) -> Result<Observation> {
    let obj = v.as_object().context("record is not an object")?;
    let text = |k: &str| obj.get(k).and_then(|x| x.as_str()).unwrap_or_default().to_string();
    let port = |k: &str| {
        obj.get(k)
            .and_then(|x| x.as_u64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(0) as u16
    };
    let fee = match obj.get("fee") {
        Some(Value::String(s)) => btc_to_sats(s)?,
        Some(Value::Number(n)) => btc_to_sats(&n.to_string())?,
        _ => 0,
    };

    Ok(Observation {
        timestamp: parse_timestamp(&text("timestamp"))?,
        src_ip: text("src_ip"),
        src_port: port("src_port"),
        dst_ip: text("dst_ip"),
        dst_port: port("dst_port"),
        txid: text("txid"),
        input_addresses: strings(obj.get("input_addresses")),
        output_addresses: strings(obj.get("output_addresses")),
        input_amounts: sats(obj.get("input_amounts"))?,
        output_amounts: sats(obj.get("output_amounts"))?,
        fee,
        script_type: text("script_type"),
        geo_country: text("geo_country"),
        asn: obj
            .get("asn")
            .and_then(|x| x.as_u64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(0) as u32,
        asn_org: String::new(),
        geo_source: String::new(),
    })
}

pub fn parse(path: &Path) -> Result<super::Parsed> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading {}", path.display()))?;

    let values: Vec<Value> = match serde_json::from_str::<Value>(&text) {
        Ok(Value::Array(items)) => items,
        Ok(other) => vec![other],
        Err(_) => text
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect(),
    };

    // A non-empty file that yields no values is a failed parse, not an empty
    // file. Reporting it as zero-of-zero would hide a truncated evidence file.
    if values.is_empty() && !text.trim().is_empty() {
        return Ok(super::Parsed { rows: Vec::new(), rejected: 1 });
    }

    let mut rows = Vec::new();
    let mut rejected = 0usize;
    for value in &values {
        match from_value(value) {
            Ok(obs) if !obs.txid.is_empty() => rows.push(obs),
            _ => rejected += 1,
        }
    }

    Ok(super::Parsed { rows, rejected })
}
