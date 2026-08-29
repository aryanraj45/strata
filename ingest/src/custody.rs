//! Chain of custody: hash every source file so an ingest can be shown to
//! correspond to exactly the evidence that was handed over.

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::Path;

#[derive(Debug)]
pub struct SourceFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

pub fn hash_file(path: &Path) -> Result<SourceFile> {
    let mut file = std::fs::File::open(path)
        .with_context(|| format!("hashing {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut total = 0u64;
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        total += n as u64;
        hasher.update(&buf[..n]);
    }
    Ok(SourceFile {
        path: path.display().to_string(),
        bytes: total,
        sha256: hex::encode(hasher.finalize()),
    })
}
