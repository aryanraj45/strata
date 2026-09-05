"""Overview — what the pipeline found, at a glance."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from strata_dashboard import data as store
from strata_dashboard import ui


def render(db, totals: dict, alerts: pd.DataFrame) -> None:
    st.markdown("### Case overview")
    st.caption(
        "Every figure on this page was produced by a pipeline stage and checked by that "
        "stage's own test suite. The dashboard reports; it does not calculate."
    )

    row = st.columns(4)
    with row[0]:
        ui.hero("Entities under review", f"{totals['scored']:,}",
                f"resolved from {totals['addresses']:,} wallet addresses")
    with row[1]:
        ui.hero("High risk", f"{totals['high_risk']:,}",
                f"{totals['high_risk'] / max(totals['scored'], 1):.1%} of the population")
    with row[2]:
        ui.hero("Origin attributed", f"{totals['attributed']:,}",
                "linked to a candidate host, statistically")
    with row[3]:
        ui.hero("Laundering chains", f"{totals['laundering_chains']:,}",
                f"of {totals['chains']:,} peeling chains recovered")

    st.write("")
    left, right = st.columns([0.45, 0.55], gap="large")

    with left:
        bands = pd.DataFrame(store.risk_bands(db), columns=["band", "count"])
        st.altair_chart(ui.band_chart(bands), width="stretch")
        st.caption(
            "Risk is deliberately concentrated: a queue where everything is urgent is a queue "
            "nobody works through."
        )

    with right:
        hours = pd.DataFrame(
            store.hourly_activity(db), columns=["hour", "High risk", "Everyone else"]
        )
        melted = hours.melt("hour", var_name="group", value_name="transactions")
        chart = (
            alt.Chart(melted, title="Transaction volume by hour of day")
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=28, filled=True))
            .encode(
                x=alt.X("hour:Q", title="Hour (UTC)",
                        scale=alt.Scale(domain=[0, 23], nice=False)),
                y=alt.Y("transactions:Q", title=None),
                color=alt.Color(
                    "group:N",
                    scale=alt.Scale(
                        domain=["High risk", "Everyone else"],
                        range=[ui.CRITICAL, ui.INK_FAINT],
                    ),
                    legend=alt.Legend(title=None, orient="top-right"),
                ),
                tooltip=["hour", "group", "transactions"],
            )
            .properties(height=240)
        )
        st.altair_chart(chart, width="stretch")
        st.caption(
            "High-risk entities keep different hours. This is one of the features the model "
            "actually uses — shown here directly rather than described."
        )

    st.divider()

    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("**Highest-risk entities**")
        top = alerts.head(8)[
            ["entity_id", "risk_score", "ip", "attribution_confidence", "chain_hops"]
        ].copy()
        top.columns = ["Entity", "Risk", "Candidate origin", "Confidence", "Chain"]
        top["Risk"] = top["Risk"].map("{:.3f}".format)
        top["Candidate origin"] = top["Candidate origin"].fillna("—")
        top["Confidence"] = top["Confidence"].map(
            lambda v: f"{v:.1%}" if pd.notna(v) else "—"
        )
        top["Chain"] = top["Chain"].fillna(0).astype(int).replace(0, "—")
        st.dataframe(top, hide_index=True, width="stretch")

    with right:
        countries = pd.DataFrame(
            store.countries(db), columns=["Country", "Entities", "High risk"]
        )
        if len(countries):
            st.altair_chart(
                ui.bar(countries.head(9), "Entities", "Country",
                       title="Attributed origins by country", height=250),
                width="stretch",
            )
            st.caption(
                "Resolved from the shipped GeoIP database. Our synthetic addresses use "
                "documentation ranges, so these are the generator's own assignments."
            )
