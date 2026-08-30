"""Pipeline — provenance, and what each stage actually produced."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from strata_dashboard import data as store
from strata_dashboard import ui

STAGE_NOTES = {
    "Ingest": "Rust. Reads CSV, JSON and XML into one record shape, resolves country and ASN "
              "from a local GeoIP database, and hashes every source file.",
    "Clustering": "Detects CoinJoins first, then groups addresses that were spent together. "
                  "The order matters: clustering a CoinJoin merges unrelated people.",
    "Graph": "Recovers peeling chains as paths. Clustering cannot see them — every hop has a "
             "single input, so there is nothing spent together to link.",
    "Fusion": "Correlates network sightings with ledger entities. Tests each candidate host "
              "against how often it leads traffic generally, corrected for multiple comparisons.",
    "Models": "A supervised model for known patterns and an unsupervised one for anything new, "
              "with every score decomposed by SHAP.",
}


def render(db, totals: dict, alerts: pd.DataFrame) -> None:
    st.markdown("### Pipeline and provenance")
    st.caption(
        "What each stage produced, read back from its own output — not from a log. If a figure "
        "elsewhere in this dashboard looks wrong, this page is where you check what produced it."
    )

    stats = store.pipeline_stats(db)
    for name, tables, produced in stats:
        cols = st.columns([0.16, 0.34, 0.5])
        cols[0].markdown(f"**{name}**")
        cols[1].markdown(f"<span class='muted'>{produced}</span>", unsafe_allow_html=True)
        cols[2].markdown(f"<span class='muted'>{STAGE_NOTES.get(name, '')}</span>",
                         unsafe_allow_html=True)
        st.markdown(f"<hr style='border:0;border-top:1px solid {ui.LINE};margin:8px 0'>",
                    unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([0.55, 0.45], gap="large")

    with left:
        st.markdown("**Chain of custody**")
        manifest = store.custody(store.STORE_PATH) if hasattr(store, "STORE_PATH") else None
        if not manifest:
            st.info("No ingest manifest found in this store.")
        else:
            st.caption(
                f"Ingested {manifest.get('observations', 0):,} observations from "
                f"{len(manifest.get('sources', []))} source files. Each file was hashed at "
                f"ingest so a run can be tied to exactly the evidence it was given."
            )
            sources = pd.DataFrame(manifest.get("sources", []))
            if not sources.empty:
                sources["size"] = (sources["bytes"] / 1e6).map("{:.1f} MB".format)
                sources["sha256"] = sources["sha256"].str.slice(0, 24) + "…"
                st.dataframe(
                    sources[["path", "size", "sha256"]].rename(
                        columns={"path": "Source file", "size": "Size", "sha256": "SHA-256"}
                    ),
                    hide_index=True, width="stretch",
                )
            rejected = manifest.get("rejected_records", 0)
            duplicates = manifest.get("duplicate_records", 0)
            ui.evidence(
                f"<b>{duplicates:,}</b> duplicate records dropped — the same evidence supplied in "
                f"three formats parses to identical records, which is how we know the parsers "
                f"agree. <b>{rejected:,}</b> records were rejected as malformed."
            )

    with right:
        st.markdown("**Where the numbers come from**")
        provenance = pd.DataFrame(
            [
                ("Risk score", "models", "RandomForest, cross-validated"),
                ("Why flagged", "models", "SHAP contributions per feature"),
                ("Anomaly flag", "models", "IsolationForest, no labels"),
                ("Candidate origin", "fusion", "Binomial test vs network baseline"),
                ("Confidence", "fusion", "1 − adjusted p-value"),
                ("Entity grouping", "cluster + graph", "CIOH, then chain paths"),
                ("Chain hops", "graph", "One-in/two-out path walk"),
                ("Country / ASN", "ingest", "Local GeoIP database"),
            ],
            columns=["Shown as", "Produced by", "Method"],
        )
        st.dataframe(provenance, hide_index=True, width="stretch")
        st.caption(
            "The dashboard calculates nothing. A dashboard that quietly does its own arithmetic "
            "is a second, unverified implementation — and the two drift apart exactly when it "
            "matters."
        )
