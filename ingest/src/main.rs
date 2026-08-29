//! STRATA bulk ingest.
//!
//! Reads CSV/JSON/XML evidence files, normalises them into one record shape,
//! enriches source addresses with country and ASN from a local GeoIP database,
//! deduplicates, and writes a two-file columnar store.
//!
//! Runs entirely offline. The only file access is the inputs, the GeoIP database
//! and the output directory.

mod custody;
mod geo;
mod parse;
mod record;
mod sink;

use anyhow::{bail, Context, Result};
use clap::Parser;
use rayon::prelude::*;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Parser, Debug)]
#[command(name = "strata-ingest", about = "Bulk ingest for STRATA")]
struct Args {
    /// Evidence files to ingest (.csv, .json, .jsonl, .xml)
    #[arg(required = true)]
    inputs: Vec<PathBuf>,

    /// Output directory for the columnar store
    #[arg(short, long, default_value = "store")]
    out: PathBuf,

    /// Directory containing GeoLite2-City.mmdb and/or GeoLite2-ASN.mmdb
    #[arg(long)]
    geoip: Option<PathBuf>,

    /// Write the chain-of-custody manifest here
    #[arg(long, default_value = "store/manifest.json")]
    manifest: PathBuf,
}

fn main() -> Result<()> {
    let args = Args::parse();
    let started = Instant::now();

    for path in &args.inputs {
        if !path.exists() {
            bail!("no such file: {}", path.display());
        }
    }

    let resolver = geo::GeoResolver::load(args.geoip.as_deref())?;

    // Parse every file in parallel; a file that fails is reported, not fatal.
    let parsed: Vec<(PathBuf, Result<parse::Parsed>)> = args
        .inputs
        .par_iter()
        .map(|path| (path.clone(), parse::parse_file(path)))
        .collect();

    let mut rows = Vec::new();
    let mut rejected = 0usize;
    println!("  {:<28} {:>9}  {:>8}", "source", "records", "rejected");
    for (path, result) in parsed {
        match result {
            Ok(p) => {
                println!(
                    "  {:<28} {:>9}  {:>8}",
                    truncate(&path),
                    p.rows.len(),
                    p.rejected
                );
                rejected += p.rejected;
                rows.extend(p.rows);
            }
            Err(err) => {
                eprintln!("  {:<28} {err:#}", truncate(&path));
            }
        }
    }
    if rows.is_empty() {
        bail!("no records parsed from any input");
    }

    // Deduplicate: the same packet may appear in more than one supplied format.
    let before = rows.len();
    let mut seen = HashSet::with_capacity(before);
    rows.retain(|obs| seen.insert(obs.dedup_key()));
    let duplicates = before - rows.len();

    // Enrich. Parallel because each lookup is independent and read-only.
    let counts = rows
        .par_iter_mut()
        .map(|obs| {
            let (country, asn, org, from_db) =
                resolver.resolve(&obs.src_ip, &obs.geo_country, obs.asn);
            obs.geo_country = country;
            obs.asn = asn;
            obs.asn_org = org;
            obs.geo_source = if from_db { "db" } else { "record" }.to_string();
            from_db
        })
        .fold(
            geo::GeoCounts::default,
            |mut acc, from_db| {
                if from_db {
                    acc.from_db += 1
                } else {
                    acc.from_record += 1
                }
                acc
            },
        )
        .reduce(geo::GeoCounts::default, |a, b| geo::GeoCounts {
            from_db: a.from_db + b.from_db,
            from_record: a.from_record + b.from_record,
        });

    std::fs::create_dir_all(&args.out)
        .with_context(|| format!("creating {}", args.out.display()))?;
    let n_tx = sink::write_tx_l1(&args.out.join("tx_l1.parquet"), &rows)?;
    let n_obs = sink::write_net_l2(&args.out.join("net_l2.parquet"), &mut rows)?;

    // Chain of custody.
    let sources: Vec<custody::SourceFile> = args
        .inputs
        .iter()
        .map(|p| custody::hash_file(p))
        .collect::<Result<_>>()?;
    if let Some(dir) = args.manifest.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let manifest = serde_json::json!({
        "ingested_at_unix": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?.as_secs(),
        "transactions": n_tx,
        "observations": n_obs,
        "rejected_records": rejected,
        "duplicate_records": duplicates,
        "geoip_database_present": resolver.available(),
        "geo_from_database": counts.from_db,
        "geo_from_record": counts.from_record,
        "sources": sources.iter().map(|s| serde_json::json!({
            "path": s.path, "bytes": s.bytes, "sha256": s.sha256
        })).collect::<Vec<_>>(),
    });
    std::fs::write(&args.manifest, serde_json::to_string_pretty(&manifest)? + "\n")?;

    let elapsed = started.elapsed();
    let rate = n_obs as f64 / elapsed.as_secs_f64().max(1e-9);
    println!();
    println!("  transactions   {n_tx:>9}");
    println!("  observations   {n_obs:>9}");
    println!("  duplicates     {duplicates:>9}");
    println!("  rejected       {rejected:>9}");
    println!(
        "  geo            {:>9}  ({} from database, {} from record)",
        counts.from_db + counts.from_record,
        counts.from_db,
        counts.from_record
    );
    if !resolver.available() {
        println!("                           no GeoIP database supplied; using record fields");
    }
    println!();
    println!(
        "  {:.2?}  ({:.0} records/sec)  ->  {}",
        elapsed,
        rate,
        args.out.display()
    );

    Ok(())
}

fn truncate(path: &Path) -> String {
    let text = path.display().to_string();
    if text.len() <= 28 {
        text
    } else {
        format!("...{}", &text[text.len() - 25..])
    }
}
