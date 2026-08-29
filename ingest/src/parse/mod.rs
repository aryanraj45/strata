//! Format-specific readers. Each returns the same `Observation` shape.
//!
//! Every parser reports per-record errors rather than aborting the run: evidence
//! files arrive from collection systems and may be truncated or partially
//! malformed. One bad row must never cost the whole ingest.

pub mod csv_fmt;
pub mod json_fmt;
pub mod xml_fmt;

use crate::record::Observation;
use anyhow::{bail, Result};
use std::path::Path;

/// Records that parsed, plus the count that did not.
pub struct Parsed {
    pub rows: Vec<Observation>,
    pub rejected: usize,
}

pub fn parse_file(path: &Path) -> Result<Parsed> {
    match path.extension().and_then(|e| e.to_str()) {
        Some("csv") => csv_fmt::parse(path),
        Some("json") | Some("jsonl") => json_fmt::parse(path),
        Some("xml") => xml_fmt::parse(path),
        other => bail!("unsupported extension {other:?} for {}", path.display()),
    }
}
