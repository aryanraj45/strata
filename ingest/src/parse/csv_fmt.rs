//! CSV: array columns are separator-delimited inside a single cell.

use crate::record::{btc_to_sats, parse_port, parse_timestamp, Observation};
use anyhow::{Context, Result};
use std::path::Path;

const SEP: char = ';';

fn split_list(cell: &str) -> Vec<&str> {
    if cell.trim().is_empty() {
        Vec::new()
    } else {
        cell.split(SEP).collect()
    }
}

fn amounts(cell: &str) -> Result<Vec<i64>> {
    split_list(cell).into_iter().map(btc_to_sats).collect()
}

pub fn parse(path: &Path) -> Result<super::Parsed> {
    let mut reader = csv::Reader::from_path(path)
        .with_context(|| format!("opening {}", path.display()))?;
    let headers = reader.headers()?.clone();
    let idx = |name: &str| headers.iter().position(|h| h == name);

    let (i_ts, i_sip, i_sp, i_dip, i_dp, i_txid) = (
        idx("timestamp").context("missing column: timestamp")?,
        idx("src_ip").context("missing column: src_ip")?,
        idx("src_port").context("missing column: src_port")?,
        idx("dst_ip").context("missing column: dst_ip")?,
        idx("dst_port").context("missing column: dst_port")?,
        idx("txid").context("missing column: txid")?,
    );
    let (i_ia, i_oa, i_iam, i_oam) = (
        idx("input_addresses").context("missing column: input_addresses")?,
        idx("output_addresses").context("missing column: output_addresses")?,
        idx("input_amounts").context("missing column: input_amounts")?,
        idx("output_amounts").context("missing column: output_amounts")?,
    );
    let i_fee = idx("fee");
    let i_script = idx("script_type");
    let i_country = idx("geo_country");
    let i_asn = idx("asn");

    let mut rows = Vec::new();
    let mut rejected = 0usize;

    for result in reader.records() {
        let rec = match result {
            Ok(r) => r,
            Err(_) => {
                rejected += 1;
                continue;
            }
        };
        let get = |i: usize| rec.get(i).unwrap_or_default();
        let built = (|| -> Result<Observation> {
            Ok(Observation {
                timestamp: parse_timestamp(get(i_ts))?,
                src_ip: get(i_sip).to_string(),
                src_port: parse_port(get(i_sp))?,
                dst_ip: get(i_dip).to_string(),
                dst_port: parse_port(get(i_dp))?,
                txid: get(i_txid).to_string(),
                input_addresses: split_list(get(i_ia)).iter().map(|s| s.to_string()).collect(),
                output_addresses: split_list(get(i_oa)).iter().map(|s| s.to_string()).collect(),
                input_amounts: amounts(get(i_iam))?,
                output_amounts: amounts(get(i_oam))?,
                fee: i_fee.map(|i| btc_to_sats(get(i))).transpose()?.unwrap_or(0),
                script_type: i_script.map(|i| get(i).to_string()).unwrap_or_default(),
                geo_country: i_country.map(|i| get(i).to_string()).unwrap_or_default(),
                asn: i_asn.and_then(|i| get(i).parse().ok()).unwrap_or(0),
                asn_org: String::new(),
                country_source: String::new(),
                asn_source: String::new(),
            })
        })();

        match built {
            Ok(obs) if !obs.txid.is_empty() => rows.push(obs),
            _ => rejected += 1,
        }
    }

    Ok(super::Parsed { rows, rejected })
}
