//! Columnar output: two Parquet files, one per layer.
//!
//! `tx_l1` is the ledger — one row per transaction. `net_l2` is the network — one
//! row per observation, several per transaction. They are written separately
//! because they have different cardinalities and are queried differently; the
//! fusion join puts them back together on `txid`.
//!
//! Parquet rather than a database server: an embedded query engine reads these
//! files directly, so the whole store is a directory that can be copied onto an
//! air-gapped machine with no service to install.

use crate::record::Observation;
use anyhow::Result;
use arrow::array::{
    ArrayRef, Int64Builder, ListBuilder, StringBuilder, UInt16Builder, UInt32Builder,
};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use parquet::basic::Compression;
use parquet::file::properties::WriterProperties;
use std::collections::HashMap;
use std::fs::File;
use std::path::Path;
use std::sync::Arc;

fn list_field(name: &str, inner: DataType) -> Field {
    Field::new(name, DataType::List(Arc::new(Field::new("item", inner, true))), false)
}

fn props() -> WriterProperties {
    WriterProperties::builder()
        .set_compression(Compression::SNAPPY)
        .build()
}

fn write(path: &Path, batch: RecordBatch) -> Result<()> {
    let file = File::create(path)?;
    let mut writer = ArrowWriter::try_new(file, batch.schema(), Some(props()))?;
    writer.write(&batch)?;
    writer.close()?;
    Ok(())
}

/// One row per distinct transaction, taking the earliest observation of each.
pub fn write_tx_l1(path: &Path, rows: &[Observation]) -> Result<usize> {
    let mut first: HashMap<&str, &Observation> = HashMap::new();
    for obs in rows {
        first
            .entry(obs.txid.as_str())
            .and_modify(|cur| {
                if obs.timestamp < cur.timestamp {
                    *cur = obs;
                }
            })
            .or_insert(obs);
    }
    let mut txs: Vec<&Observation> = first.into_values().collect();
    txs.sort_by(|a, b| a.txid.cmp(&b.txid));

    let mut txid = StringBuilder::new();
    let mut ts = Int64Builder::new();
    let mut in_addr = ListBuilder::new(StringBuilder::new());
    let mut out_addr = ListBuilder::new(StringBuilder::new());
    let mut in_amt = ListBuilder::new(Int64Builder::new());
    let mut out_amt = ListBuilder::new(Int64Builder::new());
    let mut fee = Int64Builder::new();
    let mut script = StringBuilder::new();
    let mut n_in = UInt32Builder::new();
    let mut n_out = UInt32Builder::new();
    let mut total_in = Int64Builder::new();
    let mut total_out = Int64Builder::new();

    for obs in &txs {
        txid.append_value(&obs.txid);
        ts.append_value(obs.timestamp);
        for a in &obs.input_addresses {
            in_addr.values().append_value(a);
        }
        in_addr.append(true);
        for a in &obs.output_addresses {
            out_addr.values().append_value(a);
        }
        out_addr.append(true);
        for v in &obs.input_amounts {
            in_amt.values().append_value(*v);
        }
        in_amt.append(true);
        for v in &obs.output_amounts {
            out_amt.values().append_value(*v);
        }
        out_amt.append(true);
        fee.append_value(obs.fee);
        script.append_value(&obs.script_type);
        n_in.append_value(obs.input_addresses.len() as u32);
        n_out.append_value(obs.output_addresses.len() as u32);
        total_in.append_value(obs.input_amounts.iter().sum());
        total_out.append_value(obs.output_amounts.iter().sum());
    }

    let schema = Arc::new(Schema::new(vec![
        Field::new("txid", DataType::Utf8, false),
        Field::new("ts_micro", DataType::Int64, false),
        list_field("input_addresses", DataType::Utf8),
        list_field("output_addresses", DataType::Utf8),
        list_field("input_amounts", DataType::Int64),
        list_field("output_amounts", DataType::Int64),
        Field::new("fee", DataType::Int64, false),
        Field::new("script_type", DataType::Utf8, false),
        Field::new("n_inputs", DataType::UInt32, false),
        Field::new("n_outputs", DataType::UInt32, false),
        Field::new("total_in", DataType::Int64, false),
        Field::new("total_out", DataType::Int64, false),
    ]));

    let columns: Vec<ArrayRef> = vec![
        Arc::new(txid.finish()),
        Arc::new(ts.finish()),
        Arc::new(in_addr.finish()),
        Arc::new(out_addr.finish()),
        Arc::new(in_amt.finish()),
        Arc::new(out_amt.finish()),
        Arc::new(fee.finish()),
        Arc::new(script.finish()),
        Arc::new(n_in.finish()),
        Arc::new(n_out.finish()),
        Arc::new(total_in.finish()),
        Arc::new(total_out.finish()),
    ];

    let count = txs.len();
    write(path, RecordBatch::try_new(schema, columns)?)?;
    Ok(count)
}

/// One row per observation, sorted by `(txid, ts_micro)` so the fusion join is a
/// merge over already-ordered runs rather than a scan.
pub fn write_net_l2(path: &Path, rows: &mut Vec<Observation>) -> Result<usize> {
    rows.sort_by(|a, b| a.txid.cmp(&b.txid).then(a.timestamp.cmp(&b.timestamp)));

    let mut txid = StringBuilder::new();
    let mut ts = Int64Builder::new();
    let mut src_ip = StringBuilder::new();
    let mut src_port = UInt16Builder::new();
    let mut dst_ip = StringBuilder::new();
    let mut dst_port = UInt16Builder::new();
    let mut country = StringBuilder::new();
    let mut asn = UInt32Builder::new();
    let mut asn_org = StringBuilder::new();
    let mut country_src = StringBuilder::new();
    let mut asn_src = StringBuilder::new();
    let mut rank = UInt32Builder::new();

    let mut current = "";
    let mut position = 0u32;
    for obs in rows.iter() {
        if obs.txid != current {
            current = &obs.txid;
            position = 0;
        }
        position += 1;

        txid.append_value(&obs.txid);
        ts.append_value(obs.timestamp);
        src_ip.append_value(&obs.src_ip);
        src_port.append_value(obs.src_port);
        dst_ip.append_value(&obs.dst_ip);
        dst_port.append_value(obs.dst_port);
        country.append_value(&obs.geo_country);
        asn.append_value(obs.asn);
        asn_org.append_value(&obs.asn_org);
        country_src.append_value(&obs.country_source);
        asn_src.append_value(&obs.asn_source);
        rank.append_value(position);
    }

    let schema = Arc::new(Schema::new(vec![
        Field::new("txid", DataType::Utf8, false),
        Field::new("ts_micro", DataType::Int64, false),
        Field::new("src_ip", DataType::Utf8, false),
        Field::new("src_port", DataType::UInt16, false),
        Field::new("dst_ip", DataType::Utf8, false),
        Field::new("dst_port", DataType::UInt16, false),
        Field::new("geo_country", DataType::Utf8, false),
        Field::new("asn", DataType::UInt32, false),
        Field::new("asn_org", DataType::Utf8, false),
        Field::new("country_source", DataType::Utf8, false),
        Field::new("asn_source", DataType::Utf8, false),
        Field::new("arrival_rank", DataType::UInt32, false),
    ]));

    let columns: Vec<ArrayRef> = vec![
        Arc::new(txid.finish()),
        Arc::new(ts.finish()),
        Arc::new(src_ip.finish()),
        Arc::new(src_port.finish()),
        Arc::new(dst_ip.finish()),
        Arc::new(dst_port.finish()),
        Arc::new(country.finish()),
        Arc::new(asn.finish()),
        Arc::new(asn_org.finish()),
        Arc::new(country_src.finish()),
        Arc::new(asn_src.finish()),
        Arc::new(rank.finish()),
    ];

    let count = rows.len();
    write(path, RecordBatch::try_new(schema, columns)?)?;
    Ok(count)
}
