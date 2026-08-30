"""Network — where attributed traffic originates."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from strata_dashboard import data as store
from strata_dashboard import ui


def render(db, totals: dict, alerts: pd.DataFrame) -> None:
    st.markdown("### Network origins")
    st.caption(
        "The dual-layer result. Each attribution links a wallet entity to the host that was "
        "repeatedly first to relay its transactions — with the count and the probability behind it."
    )

    attributed = alerts[alerts["attribution_significant"].fillna(False)]

    row = st.columns(4)
    with row[0]:
        ui.hero("Attributed entities", f"{len(attributed):,}",
                f"of {totals['scored']:,} scored")
    with row[1]:
        ui.hero("Distinct origin hosts", f"{attributed['ip'].nunique():,}",
                "candidate source addresses")
    with row[2]:
        ui.hero("Countries", f"{totals['countries']:,}", "seen in network telemetry")
    with row[3]:
        rotating = int((attributed["n_countries"].fillna(0) > 1).sum())
        ui.hero("Possibly rotating", f"{rotating:,}",
                "traffic spanning several countries")

    st.divider()
    left, right = st.columns(2, gap="large")

    with left:
        countries = pd.DataFrame(
            store.countries(db), columns=["Country", "Entities", "High risk"]
        )
        if len(countries):
            st.altair_chart(
                ui.bar(countries, "Entities", "Country",
                       title="Attributed entities by country", height=280),
                width="stretch",
            )

    with right:
        nets = pd.DataFrame(
            store.networks(db),
            columns=["ASN", "Country", "Entities", "Mean risk", "Sightings"],
        )
        if len(nets):
            nets["Network"] = "AS" + nets["ASN"].astype(str) + " · " + nets["Country"]
            chart = (
                alt.Chart(nets, title="Origin networks, by mean risk of their entities")
                .mark_circle(opacity=0.85, stroke=ui.BG, strokeWidth=2)
                .encode(
                    x=alt.X("Entities:Q", title="Entities attributed to this network"),
                    y=alt.Y("Mean risk:Q", title="Mean risk", scale=alt.Scale(domain=[0, 1])),
                    size=alt.Size("Sightings:Q", legend=None, scale=alt.Scale(range=[60, 700])),
                    color=alt.Color(
                        "Mean risk:Q",
                        scale=alt.Scale(range=ui.SEQUENTIAL),
                        legend=None,
                    ),
                    tooltip=["Network", "Entities", "Sightings",
                             alt.Tooltip("Mean risk:Q", format=".3f")],
                )
                .properties(height=280)
            )
            st.altair_chart(chart, width="stretch")
            st.caption(
                "Size is total first-relay sightings. A network high and to the right hosts "
                "several high-risk entities — worth an analyst's attention before one that "
                "hosts many low-risk ones."
            )

    st.divider()
    st.markdown("**Attributions, strongest first**")
    if attributed.empty:
        st.info("No significant attributions in this store.")
        return

    table = pd.DataFrame({
        "Entity": attributed["entity_id"],
        "Origin host": attributed["ip"],
        "Country": attributed["country"],
        "ASN": attributed["asn"].map(lambda v: f"AS{int(v)}" if pd.notna(v) else "—"),
        "First seen": [
            f"{int(h)}/{int(t)}" for h, t in zip(attributed["hits"], attributed["transactions"])
        ],
        "Confidence": attributed["attribution_confidence"].map("{:.2%}".format),
        "Risk": attributed["risk_score"].map("{:.3f}".format),
        "Countries seen": attributed["n_countries"].fillna(0).astype(int),
    })
    st.dataframe(table, hide_index=True, width="stretch", height=380)
    st.caption(
        "\"First seen\" is how many of the entity's transactions this host relayed before anyone "
        "else. One would be a coincidence; the ratio is the evidence."
    )
