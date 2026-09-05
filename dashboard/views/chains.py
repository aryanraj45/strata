"""Laundering — peeling chains and mixer participation."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from strata_dashboard import data as store
from strata_dashboard import ui


def render(db, totals: dict, alerts: pd.DataFrame) -> None:
    st.markdown("### Laundering structures")
    st.caption(
        "Two techniques, detected separately. Peeling chains move a balance through many small "
        "hops; CoinJoins deliberately mix strangers together to break the ownership assumption "
        "that clustering depends on."
    )

    chains = pd.DataFrame(
        store.chain_list(db),
        columns=["chain_id", "entity_id", "hops", "peeled", "median_peel",
                 "peel_spread", "laundering_like", "risk"],
    )
    joins = pd.DataFrame(
        store.coinjoins(db),
        columns=["txid", "reason", "n_inputs", "n_outputs", "total_out"],
    )

    row = st.columns(4)
    with row[0]:
        ui.hero("Chains recovered", f"{len(chains):,}", "runs of one-in / two-out hops")
    with row[1]:
        laundering = int(chains["laundering_like"].sum()) if len(chains) else 0
        ui.hero("Classified as laundering", f"{laundering:,}",
                "consistent small peels over many hops")
    with row[2]:
        ui.hero("Longest chain", f"{int(chains['hops'].max()) if len(chains) else 0} hops",
                "before the trail ends")
    with row[3]:
        ui.hero("CoinJoins filtered", f"{len(joins):,}",
                "excluded before clustering")

    ui.caution(
        "<b>Chain shape alone is not laundering.</b> A person spending repeatedly out of their "
        "own change produces exactly the same structure. The laundering label additionally "
        "requires small, consistent slices over many hops — so the two claims are scored "
        "separately rather than conflated."
    )

    st.divider()
    left, right = st.columns([0.5, 0.5], gap="large")

    with left:
        st.markdown("**Recovered chains**")
        if chains.empty:
            st.info("No chains recovered in this store.")
            return
        table = pd.DataFrame({
            "Chain": chains["chain_id"],
            "Hops": chains["hops"],
            "Peeled (BTC)": (chains["peeled"] / 1e8).map("{:.4f}".format),
            "Median slice": chains["median_peel"].map("{:.1%}".format),
            "Laundering": chains["laundering_like"].map({True: "yes", False: "—"}),
            "Entity risk": chains["risk"].map(
                lambda v: f"{v:.2f}" if pd.notna(v) else "—"
            ),
        })
        event = st.dataframe(
            table, hide_index=True, width="stretch", height=420,
            on_select="rerun", selection_mode="single-row", key="chain_table",
        )
        picked = event.selection.rows if event and event.selection else []
        chain_id = chains["chain_id"].iloc[picked[0]] if picked else chains["chain_id"].iloc[0]

    with right:
        st.markdown(f"**Chain `{chain_id}` — hop by hop**")
        hops = store.chain_hops(db, chain_id)
        if not hops:
            st.info("No hops recorded for this chain.")
        else:
            frame = pd.DataFrame(
                hops, columns=["Hop", "TXID", "Carried address", "Out (sats)", "Fee", "Timestamp"]
            )
            frame["Carried (BTC)"] = (frame["Out (sats)"] / 1e8).map("{:.6f}".format)
            frame["TXID"] = frame["TXID"].str.slice(0, 16) + "…"
            frame["Carried address"] = frame["Carried address"].fillna("—")
            st.dataframe(
                frame[["Hop", "TXID", "Carried (BTC)", "Carried address"]],
                hide_index=True, width="stretch", height=340,
            )
            record = chains[chains["chain_id"] == chain_id].iloc[0]
            ui.evidence(
                f"{int(record['hops'])} hops, shedding {record['peeled'] / 1e8:.4f} BTC in total. "
                f"Median slice {record['median_peel']:.1%}, spread {record['peel_spread']:.1%}. "
                + ("Consistent enough to classify as laundering."
                   if record["laundering_like"] else
                   "Too irregular to call laundering — reported as chain structure only.")
            )

    if not joins.empty:
        st.divider()
        st.markdown("**CoinJoin transactions**")
        st.caption(
            "Detected and excluded before clustering. Applying common-input-ownership to these "
            "would merge unrelated people into a suspect's entity — silently, with nothing "
            "erroring."
        )
        table = pd.DataFrame({
            "TXID": joins["txid"].str.slice(0, 20) + "…",
            "Inputs": joins["n_inputs"],
            "Outputs": joins["n_outputs"],
            "Value (BTC)": (joins["total_out"] / 1e8).map("{:.4f}".format),
            "Why flagged": joins["reason"],
        })
        st.dataframe(table, hide_index=True, width="stretch")
