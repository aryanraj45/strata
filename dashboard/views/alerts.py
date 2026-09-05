"""Alerts — the ranked queue, and the evidence behind whichever row is selected."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder, GridUpdateMode
from st_link_analysis import EdgeStyle, NodeStyle, st_link_analysis

from strata_dashboard import data as store
from strata_dashboard import ui


def _queue(alerts: pd.DataFrame) -> str | None:
    st.markdown("### Alert queue")

    controls = st.columns([0.26, 0.24, 0.25, 0.25])
    min_risk = controls[0].slider("Minimum risk", 0.0, 1.0, 0.0, 0.05)
    band = controls[1].selectbox("Band", ["Any", "Critical", "High", "Elevated", "Low"])
    attributed = controls[2].toggle("Attributed only", value=False,
                                    help="Has a statistically significant candidate origin")
    chains_only = controls[3].toggle("Laundering chains only", value=False)

    view = alerts[alerts["risk_score"] >= min_risk]
    if band != "Any":
        view = view[view["risk_score"].map(ui.risk_band) == band]
    if attributed:
        view = view[view["attribution_significant"].fillna(False)]
    if chains_only:
        view = view[view["laundering_like"].fillna(False)]

    if view.empty:
        st.info("No entities match these filters.")
        return None

    table = pd.DataFrame({
        "Entity": view["entity_id"],
        "Risk": view["risk_score"].round(3),
        "Band": view["risk_score"].map(ui.risk_band),
        "Origin": view["ip"].fillna("—"),
        "Country": view["country"].fillna("—"),
        "Confidence": view["attribution_confidence"],
        "Evidence": [
            f"{int(h)}/{int(t)}" if pd.notna(h) else "—"
            for h, t in zip(view["hits"], view["transactions"])
        ],
        "Chain": view["chain_hops"].fillna(0).astype(int),
        "Wallets": view["n_addresses"].fillna(0).astype(int),
    })

    options = GridOptionsBuilder.from_dataframe(table)
    options.configure_selection("single", use_checkbox=False, pre_selected_rows=[0])
    options.configure_default_column(resizable=True, sortable=True, filter=True)
    options.configure_column("Entity", width=165, pinned="left")
    options.configure_column("Risk", type=["numericColumn"], width=85)
    options.configure_column(
        "Band", width=100,
        cellStyle={"function": (
            "params.value === 'Critical' ? {'color':'#f2686f','fontWeight':600} :"
            "params.value === 'High'     ? {'color':'#f0883c','fontWeight':600} :"
            "params.value === 'Elevated' ? {'color':'#f0c73c'} : {'color':'#8b98a9'}"
        )},
    )
    options.configure_column(
        "Confidence", type=["numericColumn"], width=110,
        valueFormatter="value == null ? '—' : (value * 100).toFixed(1) + '%'",
    )
    options.configure_column(
        "Evidence", width=105,
        headerTooltip="First-relay sightings out of the entity's total transactions",
    )
    options.configure_column("Chain", width=80, headerTooltip="Longest peeling chain, in hops")
    options.configure_grid_options(rowHeight=33, headerHeight=36)

    grid = AgGrid(
        table,
        gridOptions=options.build(),
        height=330,
        theme="balham",
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
        allow_unsafe_jscode=True,
        key="queue",
    )

    st.caption(f"{len(view):,} of {len(alerts):,} entities · select a row to open its evidence")

    selected = grid.get("selected_rows")
    if selected is None or (hasattr(selected, "empty") and selected.empty) or len(selected) == 0:
        return view["entity_id"].iloc[0]
    row = selected.iloc[0] if hasattr(selected, "iloc") else selected[0]
    return row["Entity"]


def _why(db, alert) -> None:
    factors = alert["factors"] or []
    if not factors:
        st.info("This entity scored below the flagging threshold, so there is nothing to explain.")
    else:
        st.markdown(
            "**The model's reasoning.** Each bar is how much that feature pushed the risk score "
            "up, measured with SHAP — not a guess at what mattered."
        )
        largest = max(f["contribution"] for f in factors)
        for factor in factors:
            ui.factor_bar(factor["name"], factor["group"], factor["contribution"], largest)

    detail = store.entity_detail(db, alert["entity_id"])
    if detail["chains"]:
        st.markdown("**Laundering structure**")
        for chain_id, hops, peeled, median_peel, laundering in detail["chains"][:4]:
            label = ("consistent small peels — laundering pattern" if laundering
                     else "chain shape only, not classified as laundering")
            ui.evidence(
                f"<code>{chain_id}</code> · {hops} hops · {(peeled or 0) / 1e8:.4f} BTC shed · "
                f"median slice {(median_peel or 0):.1%}<br><span class='muted'>{label}</span>"
            )

    if alert["is_anomaly"]:
        ui.evidence(
            "Also flagged independently by the unsupervised model, which was given no labels "
            "at all — it simply does not resemble the rest of the population."
        )


def _network(db, alert) -> None:
    detail = store.entity_detail(db, alert["entity_id"])
    candidates = detail["candidates"]
    if not candidates:
        st.info(
            "No candidate origin. Either too few transactions for the significance test, or "
            "the sightings are spread evenly across many hosts."
        )
        return

    st.markdown(
        "**Candidate origin hosts.** A host is a lead only if it was seen first far more often "
        "than it leads traffic generally. Repetition is the evidence, never a single packet."
    )
    frame = pd.DataFrame(
        candidates,
        columns=["IP", "Country", "ASN", "First seen", "Transactions", "Confidence",
                 "p (adjusted)", "Significant"],
    )
    frame["Confidence"] = frame["Confidence"].map("{:.1%}".format)
    frame["p (adjusted)"] = frame["p (adjusted)"].map("{:.2e}".format)
    st.dataframe(frame, hide_index=True, width="stretch")

    best = candidates[0]
    ui.evidence(
        f"Strongest candidate <code>{best[0]}</code> was first to relay "
        f"<b>{best[3]} of {best[4]}</b> transactions from this entity ({best[1]}, AS{best[2]}). "
        f"Adjusted p = {best[6]:.2e} — corrected for the number of host/entity pairs tested, "
        f"so this is not one lucky coincidence pulled out of many."
    )

    if alert["n_countries"] and alert["n_countries"] > 1:
        ui.caution(
            f"Traffic seen from {int(alert['n_countries'])} countries. This can indicate a "
            "rotating VPN or proxy. Reported rather than discarded — the pattern is itself "
            "informative."
        )

    sightings = store.observations(db, alert["entity_id"], limit=250)
    if sightings:
        with st.expander(f"Raw network sightings ({len(sightings)} shown)"):
            st.dataframe(
                pd.DataFrame(
                    sightings,
                    columns=["TXID", "Source", "Arrival rank", "Timestamp (µs)", "Country", "ASN"],
                ),
                hide_index=True,
                width="stretch",
            )


def _graph(db, entity_id: str) -> None:
    elements = store.subgraph(db, entity_id, hops=1)
    if len(elements["nodes"]) <= 1:
        st.info("This entity has no recorded counterparties in the graph.")
        return

    st.markdown(
        "**Money flow and network sightings.** Green is an entity, amber an origin host, "
        "violet its network operator. Double-click a node to expand it."
    )
    st_link_analysis(
        elements,
        layout="cose",
        # Icons by name only. A URL would be an outbound request, which would
        # break offline operation.
        node_styles=[
            NodeStyle("ENTITY", ui.ENTITY_HUE, "name", "account_balance"),
            NodeStyle("IP", ui.IP_HUE, "name", "router"),
            NodeStyle("ASN", ui.ASN_HUE, "name", "public"),
        ],
        edge_styles=[
            EdgeStyle("SENT_TO", color="#5eb3e4", caption="btc", directed=True),
            EdgeStyle("SEEN_FROM", color=ui.IP_HUE, caption="seen", directed=True),
            EdgeStyle("IN_ASN", color="#4a5568", directed=False),
        ],
        height=430,
        node_actions=["expand"],
        key=f"graph_{entity_id}",
    )


def _transactions(db, entity_id: str) -> None:
    detail = store.entity_detail(db, entity_id)
    st.markdown(
        f"**{len(detail['transactions'])} transactions** across "
        f"**{len(detail['addresses'])} wallet addresses** resolved into this entity."
    )
    if detail["transactions"]:
        frame = pd.DataFrame(
            detail["transactions"],
            columns=["TXID", "Timestamp", "Out (sats)", "Fee (sats)", "Inputs", "Outputs", "Script"],
        )
        frame["Out (BTC)"] = (frame["Out (sats)"] / 1e8).map("{:.8f}".format)
        st.dataframe(
            frame[["TXID", "Out (BTC)", "Fee (sats)", "Inputs", "Outputs", "Script"]],
            hide_index=True,
            width="stretch",
        )
    with st.expander(f"Wallet addresses ({len(detail['addresses'])})"):
        st.code("\n".join(detail["addresses"]), language=None)


def render(db, totals: dict, alerts: pd.DataFrame) -> None:
    entity_id = _queue(alerts)
    if entity_id is None:
        return

    alert = alerts[alerts["entity_id"] == entity_id].iloc[0]
    st.divider()

    head = st.columns([0.34, 0.22, 0.22, 0.22])
    with head[0]:
        st.markdown(f"### `{entity_id}`")
        st.markdown(
            ui.pill(ui.risk_band(alert["risk_score"]), ui.risk_colour(alert["risk_score"])),
            unsafe_allow_html=True,
        )
    head[1].metric("Risk score", f"{alert['risk_score']:.3f}")
    head[2].metric("Candidate origin", str(alert["ip"]) if pd.notna(alert["ip"]) else "none")
    head[3].metric(
        "Attribution confidence",
        f"{alert['attribution_confidence']:.1%}"
        if pd.notna(alert["attribution_confidence"]) else "—",
    )

    # Deliberately not st.tabs. Streamlit renders every tab panel eagerly, and
    # the inactive ones are laid out at zero width -- so the link-analysis
    # iframe measured itself at 0x0 on mount and never recovered when its tab
    # was selected. Rendering only the chosen panel means the graph mounts
    # while it is actually visible.
    PANELS = ["Why flagged", "Network evidence", "Money flow", "Transactions"]
    panel = st.radio(
        "Evidence", PANELS,
        horizontal=True,
        label_visibility="collapsed",
        key=f"panel_{entity_id}",
    )

    if panel == "Why flagged":
        _why(db, alert)
    elif panel == "Network evidence":
        _network(db, alert)
    elif panel == "Money flow":
        _graph(db, entity_id)
    else:
        _transactions(db, entity_id)
