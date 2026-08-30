#!/usr/bin/env python3
"""STRATA — forensic explorer.

    .venv/bin/streamlit run dashboard/app.py

Reads the store the pipeline produced and presents it as an investigator would
use it: a ranked queue, then the evidence behind whichever alert is selected.

Nothing is computed here. Every figure on screen was produced by a pipeline
stage and checked by that stage's verify.py -- a dashboard that quietly does its
own arithmetic is a second, unverified implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder, GridUpdateMode
from st_link_analysis import EdgeStyle, NodeStyle, st_link_analysis

from strata_dashboard import data as store

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"

st.set_page_config(
    page_title="STRATA — Forensic Explorer",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; padding-bottom: 3rem;}
      [data-testid="stMetricValue"] {font-size: 1.5rem;}
      .factor-row {display:flex; align-items:center; gap:10px; margin:5px 0;}
      .factor-name {min-width:210px; font-size:0.86rem;}
      .factor-bar {height:9px; border-radius:5px; background:#2ee6a8;}
      .factor-val {font-size:0.8rem; color:#8b98a9; font-variant-numeric:tabular-nums;}
      .evidence {border-left:3px solid #2ee6a8; padding:2px 0 2px 14px; margin:10px 0;}
      .muted {color:#8b98a9; font-size:0.86rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- loading

@st.cache_resource(show_spinner=False)
def _db(store_path: str, _stamp: float):
    """One connection per store. `_stamp` busts the cache when the store changes."""
    return store.connect(Path(store_path))


def _stamp_for(path: Path) -> float:
    files = list(path.glob("*.parquet"))
    return max((f.stat().st_mtime for f in files), default=0.0)


@st.cache_data(show_spinner=False)
def _alerts(store_path: str, _stamp: float) -> pd.DataFrame:
    db = _db(store_path, _stamp)
    return pd.DataFrame(store.alerts(db))


@st.cache_data(show_spinner=False)
def _summary(store_path: str, _stamp: float) -> dict:
    return store.summary(_db(store_path, _stamp))


if not STORE.exists() or not list(STORE.glob("*.parquet")):
    st.title("🛰️ STRATA")
    st.error("No store found. Run the pipeline first:")
    st.code(
        "python generator/generate.py --out data\n"
        "./ingest/target/release/strata-ingest data/strata.*  --geoip data/geoip --out store\n"
        "python cluster/run.py --store store\n"
        "python graph/run.py   --store store\n"
        "python fusion/run.py  --store store\n"
        "python models/run.py  --store store --data data",
        language="bash",
    )
    st.stop()

stamp = _stamp_for(STORE)
db = _db(str(STORE), stamp)
present = store.available(db)

missing = {"scores", "leads", "entities"} - present
if missing:
    st.title("🛰️ STRATA")
    st.warning(
        f"The store is incomplete — missing {', '.join(sorted(missing))}. "
        "Run the remaining pipeline stages and reload."
    )
    st.stop()

alerts = _alerts(str(STORE), stamp)
totals = _summary(str(STORE), stamp)


# ---------------------------------------------------------------- header

st.title("🛰️ STRATA — Forensic Explorer")
st.caption(
    "Dual-layer Bitcoin transaction analysis · every figure below was produced by a "
    "pipeline stage and checked by its own test suite"
)

cols = st.columns(6)
cols[0].metric("Transactions", f"{totals['transactions']:,}")
cols[1].metric("Entities", f"{totals['entities']:,}", help="Wallet addresses resolved into actors")
cols[2].metric("Network sightings", f"{totals['observations']:,}")
cols[3].metric("High risk", f"{totals['high_risk']:,}", help="Risk score at or above 0.50")
cols[4].metric(
    "Origin attributed", f"{totals['attributed']:,}",
    help="Entities linked to a candidate source host with statistical significance",
)
cols[5].metric(
    "Laundering chains", f"{totals['laundering_chains']:,}",
    help="Peeling chains with consistent small slices over many hops",
)

st.divider()


# ---------------------------------------------------------------- queue

left, right = st.columns([0.52, 0.48], gap="large")

with left:
    st.subheader("Alert queue")

    filters = st.columns([0.3, 0.35, 0.35])
    min_risk = filters[0].slider("Min risk", 0.0, 1.0, 0.0, 0.05)
    only_attributed = filters[1].toggle("Attributed only", value=False,
                                        help="Entities with a significant candidate origin")
    only_chains = filters[2].toggle("Laundering chains only", value=False)

    view = alerts[alerts["risk_score"] >= min_risk]
    if only_attributed:
        view = view[view["attribution_significant"].fillna(False)]
    if only_chains:
        view = view[view["laundering_like"].fillna(False)]

    table = pd.DataFrame({
        "Entity": view["entity_id"],
        "Risk": view["risk_score"].round(3),
        "Origin": view["ip"].fillna("—"),
        "Confidence": view["attribution_confidence"],
        "Evidence": [
            f"{int(h)}/{int(t)}" if pd.notna(h) else "—"
            for h, t in zip(view["hits"], view["transactions"])
        ],
        "Chain": view["chain_hops"].fillna(0).astype(int),
        "Wallets": view["n_addresses"].fillna(0).astype(int),
        "Anomaly": view["is_anomaly"].fillna(False),
    })

    options = GridOptionsBuilder.from_dataframe(table)
    options.configure_selection("single", use_checkbox=False)
    options.configure_default_column(resizable=True, sortable=True, filter=True)
    options.configure_column("Risk", type=["numericColumn"], width=90)
    options.configure_column(
        "Confidence", type=["numericColumn"], width=110,
        valueFormatter="value == null ? '—' : (value * 100).toFixed(1) + '%'",
    )
    options.configure_column("Entity", width=170)
    options.configure_column("Evidence", width=100,
                             headerTooltip="First-relay sightings out of total transactions")
    options.configure_column("Chain", width=80, headerTooltip="Longest peeling chain, in hops")
    options.configure_grid_options(rowHeight=32, headerHeight=34)

    grid = AgGrid(
        table,
        gridOptions=options.build(),
        height=470,
        theme="balham",
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
        allow_unsafe_jscode=True,
        key="alert_queue",
    )

    st.caption(
        f"{len(view):,} of {len(alerts):,} entities shown. "
        "Select a row to see the evidence behind it."
    )


# ---------------------------------------------------------------- detail

selected = grid.get("selected_rows")
if selected is None or (hasattr(selected, "empty") and selected.empty) or len(selected) == 0:
    entity_id = view["entity_id"].iloc[0] if len(view) else None
else:
    row = selected.iloc[0] if hasattr(selected, "iloc") else selected[0]
    entity_id = row["Entity"]

with right:
    if entity_id is None:
        st.info("No entities match the current filters.")
        st.stop()

    alert = alerts[alerts["entity_id"] == entity_id].iloc[0]

    st.subheader(f"Evidence · `{entity_id}`")

    top = st.columns(3)
    top[0].metric("Risk score", f"{alert['risk_score']:.3f}")
    if pd.notna(alert["ip"]):
        top[1].metric("Candidate origin", str(alert["ip"]))
        top[2].metric(
            "Attribution confidence",
            f"{alert['attribution_confidence']:.1%}" if pd.notna(alert["attribution_confidence"]) else "—",
        )
    else:
        top[1].metric("Candidate origin", "none")
        top[2].metric("Attribution confidence", "—")

    tabs = st.tabs(["Why flagged", "Network evidence", "Money flow", "Transactions"])

    # --- why flagged -------------------------------------------------
    with tabs[0]:
        factors = alert["factors"] or []
        if not factors:
            st.info("This entity scored below the flagging threshold, so there is nothing to explain.")
        else:
            st.markdown(
                "**The model's reasoning.** Each bar is how much that feature pushed the risk "
                "score up, measured with SHAP — not a guess at what mattered."
            )
            largest = max(f["contribution"] for f in factors)
            for factor in factors:
                width = int(100 * factor["contribution"] / largest) if largest else 0
                st.markdown(
                    f"<div class='factor-row'>"
                    f"<span class='factor-name'>{factor['name']}"
                    f" <span class='muted'>· {factor['group']}</span></span>"
                    f"<span class='factor-bar' style='width:{max(width, 3)}%'></span>"
                    f"<span class='factor-val'>+{factor['contribution']:.3f}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        detail = store.entity_detail(db, entity_id)
        if detail["chains"]:
            st.markdown("**Laundering structure**")
            for chain_id, hops, peeled, median_peel, laundering in detail["chains"][:4]:
                label = "consistent small peels — laundering pattern" if laundering else "chain shape"
                st.markdown(
                    f"<div class='evidence'><code>{chain_id}</code> · {hops} hops · "
                    f"{(peeled or 0) / 1e8:.4f} BTC shed · median slice "
                    f"{(median_peel or 0):.1%} · <span class='muted'>{label}</span></div>",
                    unsafe_allow_html=True,
                )

        if alert["is_anomaly"]:
            st.markdown(
                "<div class='evidence'>Also flagged independently by the unsupervised model, "
                "which was given no labels at all — it simply does not resemble the rest of the "
                "population.</div>",
                unsafe_allow_html=True,
            )

    # --- network evidence --------------------------------------------
    with tabs[1]:
        detail = store.entity_detail(db, entity_id)
        candidates = detail["candidates"]
        if not candidates:
            st.info(
                "No candidate origin. This entity has too few transactions for the significance "
                "test, or its sightings are spread evenly across many hosts."
            )
        else:
            st.markdown(
                "**Candidate origin hosts.** A host is only a lead if it was seen first far more "
                "often than it leads traffic generally — repetition is the evidence, never a "
                "single packet."
            )
            frame = pd.DataFrame(
                candidates,
                columns=["IP", "Country", "ASN", "First seen", "Transactions",
                         "Confidence", "p (adjusted)", "Significant"],
            )
            frame["Confidence"] = frame["Confidence"].map(lambda v: f"{v:.1%}")
            frame["p (adjusted)"] = frame["p (adjusted)"].map(lambda v: f"{v:.2e}")
            st.dataframe(frame, hide_index=True, width="stretch")

            best = candidates[0]
            st.markdown(
                f"<div class='evidence'>Strongest: <code>{best[0]}</code> was first to relay "
                f"<b>{best[3]} of {best[4]}</b> transactions from this entity "
                f"({best[1]}, AS{best[2]}). Adjusted p = {best[6]:.2e} — corrected for the number "
                f"of host/entity pairs tested, so this is not one lucky coincidence out of many.</div>",
                unsafe_allow_html=True,
            )

            if alert["n_countries"] and alert["n_countries"] > 1:
                st.warning(
                    f"Traffic seen from {int(alert['n_countries'])} countries. This can indicate "
                    "a rotating VPN or proxy — reported rather than discarded, since the pattern "
                    "is itself informative."
                )

        sightings = store.observations(db, entity_id, limit=250)
        if sightings:
            with st.expander(f"Raw sightings ({len(sightings)} shown)"):
                st.dataframe(
                    pd.DataFrame(
                        sightings,
                        columns=["TXID", "Source", "Arrival rank", "Timestamp (µs)",
                                 "Country", "ASN"],
                    ),
                    hide_index=True,
                    width="stretch",
                )

    # --- money flow ----------------------------------------------------
    with tabs[2]:
        if "graph_nodes" not in present:
            st.info("Graph stage has not been run for this store.")
        else:
            elements = store.subgraph(db, entity_id, hops=1)
            if len(elements["nodes"]) <= 1:
                st.info("This entity has no recorded counterparties in the graph.")
            else:
                node_styles = [
                    # Icons by name only — a URL would be an outbound request and
                    # would break offline operation.
                    NodeStyle("ENTITY", "#2ee6a8", "name", "account_balance"),
                    NodeStyle("IP", "#f0a83c", "name", "router"),
                    NodeStyle("ASN", "#9d8bff", "name", "public"),
                ]
                edge_styles = [
                    EdgeStyle("SENT_TO", color="#5eb3e4", caption="btc", directed=True),
                    EdgeStyle("SEEN_FROM", color="#f0a83c", caption="seen", directed=True),
                    EdgeStyle("IN_ASN", color="#6b7280", directed=False),
                ]
                st.markdown(
                    "**Money flow and network sightings.** Green is an entity, amber an origin "
                    "host, violet its network operator. Double-click a node to expand it."
                )
                st_link_analysis(
                    elements,
                    layout="cose",
                    node_styles=node_styles,
                    edge_styles=edge_styles,
                    height=460,
                    node_actions=["expand"],
                    key=f"graph_{entity_id}",
                )

    # --- transactions ---------------------------------------------------
    with tabs[3]:
        detail = store.entity_detail(db, entity_id)
        st.markdown(
            f"**{len(detail['transactions'])} transactions** across "
            f"**{len(detail['addresses'])} wallet addresses** resolved into this entity."
        )
        if detail["transactions"]:
            frame = pd.DataFrame(
                detail["transactions"],
                columns=["TXID", "Timestamp (µs)", "Out (sats)", "Fee (sats)",
                         "Inputs", "Outputs", "Script"],
            )
            frame["Out (BTC)"] = (frame["Out (sats)"] / 1e8).map(lambda v: f"{v:.8f}")
            st.dataframe(
                frame[["TXID", "Out (BTC)", "Fee (sats)", "Inputs", "Outputs", "Script"]],
                hide_index=True,
                width="stretch",
            )
        with st.expander(f"Wallet addresses ({len(detail['addresses'])})"):
            st.code("\n".join(detail["addresses"]), language=None)


# ---------------------------------------------------------------- footer

st.divider()
foot = st.columns([0.7, 0.3])
foot[0].caption(
    "STRATA · SIH 2026 Problem Statement 26146 · NTRO — "
    "runs entirely offline, reading only local files."
)
foot[1].caption(f"Store: `{STORE.name}/` · {totals['scored']:,} entities scored")
