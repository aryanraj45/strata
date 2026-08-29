//! XML: streaming pull parse. Array fields are `<field><item>..</item></field>`.
//!
//! Streaming rather than DOM so a 2 GB evidence file does not have to fit in
//! memory, and so a truncated file yields the records before the truncation
//! instead of nothing.

use crate::record::{btc_to_sats, parse_port, parse_timestamp, Observation};
use anyhow::{Context, Result};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::path::Path;

#[derive(Default)]
struct Fields {
    timestamp: String,
    src_ip: String,
    src_port: String,
    dst_ip: String,
    dst_port: String,
    txid: String,
    input_addresses: Vec<String>,
    output_addresses: Vec<String>,
    input_amounts: Vec<String>,
    output_amounts: Vec<String>,
    fee: String,
    script_type: String,
    geo_country: String,
    asn: String,
}

impl Fields {
    fn build(self) -> Result<Observation> {
        let to_sats = |list: Vec<String>| -> Result<Vec<i64>> {
            list.iter().map(|s| btc_to_sats(s)).collect()
        };
        Ok(Observation {
            timestamp: parse_timestamp(&self.timestamp)?,
            src_ip: self.src_ip,
            src_port: parse_port(&self.src_port)?,
            dst_ip: self.dst_ip,
            dst_port: parse_port(&self.dst_port)?,
            txid: self.txid,
            input_addresses: self.input_addresses,
            output_addresses: self.output_addresses,
            input_amounts: to_sats(self.input_amounts)?,
            output_amounts: to_sats(self.output_amounts)?,
            fee: if self.fee.is_empty() { 0 } else { btc_to_sats(&self.fee)? },
            script_type: self.script_type,
            geo_country: self.geo_country,
            asn: self.asn.parse().unwrap_or(0),
            asn_org: String::new(),
            country_source: String::new(),
                asn_source: String::new(),
        })
    }
}

pub fn parse(path: &Path) -> Result<super::Parsed> {
    let mut reader = Reader::from_file(path)
        .with_context(|| format!("opening {}", path.display()))?;
    reader.config_mut().trim_text(true);

    let mut buf = Vec::new();
    let mut rows = Vec::new();
    let mut rejected = 0usize;

    let mut current: Option<Fields> = None;
    let mut field = String::new(); // the array wrapper we are inside, if any
    let mut scalar = String::new(); // the scalar element we are inside, if any

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                match name.as_str() {
                    "observation" => current = Some(Fields::default()),
                    "input_addresses" | "output_addresses" | "input_amounts"
                    | "output_amounts" => field = name,
                    "item" => {}
                    other => scalar = other.to_string(),
                }
            }
            Ok(Event::Text(e)) => {
                let Some(fields) = current.as_mut() else { continue };
                let text = e.unescape().unwrap_or_default().to_string();
                if text.is_empty() {
                    continue;
                }
                if !field.is_empty() {
                    match field.as_str() {
                        "input_addresses" => fields.input_addresses.push(text),
                        "output_addresses" => fields.output_addresses.push(text),
                        "input_amounts" => fields.input_amounts.push(text),
                        "output_amounts" => fields.output_amounts.push(text),
                        _ => {}
                    }
                } else {
                    match scalar.as_str() {
                        "timestamp" => fields.timestamp = text,
                        "src_ip" => fields.src_ip = text,
                        "src_port" => fields.src_port = text,
                        "dst_ip" => fields.dst_ip = text,
                        "dst_port" => fields.dst_port = text,
                        "txid" => fields.txid = text,
                        "fee" => fields.fee = text,
                        "script_type" => fields.script_type = text,
                        "geo_country" => fields.geo_country = text,
                        "asn" => fields.asn = text,
                        _ => {}
                    }
                }
            }
            Ok(Event::End(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                match name.as_str() {
                    "observation" => {
                        if let Some(fields) = current.take() {
                            match fields.build() {
                                Ok(obs) if !obs.txid.is_empty() => rows.push(obs),
                                _ => rejected += 1,
                            }
                        }
                    }
                    "input_addresses" | "output_addresses" | "input_amounts"
                    | "output_amounts" => field.clear(),
                    _ => scalar.clear(),
                }
            }
            Ok(Event::Eof) => break,
            // A malformed tail is expected on truncated evidence: keep what parsed.
            Err(_) => {
                if current.take().is_some() {
                    rejected += 1;
                }
                break;
            }
            _ => {}
        }
        buf.clear();
    }

    Ok(super::Parsed { rows, rejected })
}
