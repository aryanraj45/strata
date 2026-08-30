"""Shared visual language for the dashboard.

One place for the palette, the chart defaults, and the small components every
page reuses, so the pages stay about content rather than styling.

Charts are almost all single-hue. Risk, volume and counts are magnitude
questions, and magnitude reads better as one hue light-to-dark than as a set of
competing colours. Where a chart does encode state, it uses the reserved status
colours below and never the accent -- an analyst should be able to tell "this is
critical" from "this is series three" at a glance.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

# Surfaces
BG = "#0a0d12"
SURFACE = "#12161d"
LINE = "#1e2530"

# Ink
INK = "#e6ebf2"
INK_DIM = "#8b98a9"
INK_FAINT = "#5d6a7b"

# Accent — identity, not state
ACCENT = "#2ee6a8"

# Reserved status colours. Never reused as categorical series.
CRITICAL = "#f2686f"
SERIOUS = "#f0883c"
WARNING = "#f0c73c"
GOOD = "#2ee6a8"
NEUTRAL = "#5eb3e4"

# Node identity in the link graph
ENTITY_HUE = "#2ee6a8"
IP_HUE = "#f0a83c"
ASN_HUE = "#a48bf0"

SEQUENTIAL = ["#0f3a30", "#166b56", "#1e9c7c", "#2ecfa2", "#7cf0cb"]


def risk_colour(score: float) -> str:
    if score >= 0.80:
        return CRITICAL
    if score >= 0.50:
        return SERIOUS
    if score >= 0.25:
        return WARNING
    return NEUTRAL


def risk_band(score: float) -> str:
    if score >= 0.80:
        return "Critical"
    if score >= 0.50:
        return "High"
    if score >= 0.25:
        return "Elevated"
    return "Low"


BAND_ORDER = ["Critical", "High", "Elevated", "Low"]
BAND_COLOURS = [CRITICAL, SERIOUS, WARNING, NEUTRAL]


def chart_theme():
    """Recessive axes and grid, so the marks carry the chart."""
    return {
        "config": {
            "background": "transparent",
            "view": {"stroke": "transparent"},
            "axis": {
                "domainColor": LINE,
                "gridColor": LINE,
                "gridOpacity": 0.5,
                "tickColor": LINE,
                "labelColor": INK_DIM,
                "titleColor": INK_FAINT,
                "labelFontSize": 11,
                "titleFontSize": 11,
                "titleFontWeight": "normal",
            },
            "legend": {
                "labelColor": INK_DIM,
                "titleColor": INK_FAINT,
                "labelFontSize": 11,
                "titleFontSize": 11,
                "symbolType": "circle",
            },
            "title": {"color": INK, "fontSize": 13, "fontWeight": 600, "anchor": "start"},
        }
    }


alt.themes.register("strata", chart_theme)
alt.themes.enable("strata")


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
          .block-container {{padding-top: 2rem; padding-bottom: 4rem; max-width: 1500px;}}
          [data-testid="stSidebarNav"] {{padding-top: 0.5rem;}}

          .hero {{
            display:flex; flex-direction:column; gap:4px;
            padding: 18px 22px; border-radius: 10px;
            background: linear-gradient(135deg, {SURFACE} 0%, #0e1319 100%);
            border: 1px solid {LINE}; border-left: 3px solid {ACCENT};
          }}
          .hero .k {{font-size:.72rem; letter-spacing:.14em; text-transform:uppercase;
                     color:{INK_FAINT};}}
          .hero .v {{font-size:2rem; font-weight:700; line-height:1.1; color:{INK};
                     font-variant-numeric: tabular-nums;}}
          .hero .s {{font-size:.82rem; color:{INK_DIM};}}

          .pill {{display:inline-block; padding:2px 10px; border-radius:100px;
                  font-size:.72rem; font-weight:600; letter-spacing:.03em;}}

          .factor-row {{display:flex; align-items:center; gap:12px; margin:6px 0;}}
          .factor-name {{min-width:200px; font-size:.85rem; color:{INK};}}
          .factor-track {{flex:1; height:8px; border-radius:5px; background:{LINE};
                          overflow:hidden;}}
          .factor-fill {{height:100%; border-radius:5px; background:{ACCENT};}}
          .factor-val {{font-size:.78rem; color:{INK_DIM}; min-width:56px;
                        text-align:right; font-variant-numeric:tabular-nums;}}

          .evidence {{border-left:3px solid {ACCENT}; background:#0c1a16;
                      padding:12px 16px; border-radius:0 6px 6px 0; margin:10px 0;
                      font-size:.9rem;}}
          .caution {{border-left:3px solid {WARNING}; background:#1a1608;
                     padding:12px 16px; border-radius:0 6px 6px 0; margin:10px 0;
                     font-size:.9rem;}}
          .muted {{color:{INK_DIM}; font-size:.85rem;}}

          .stDataFrame {{border:1px solid {LINE}; border-radius:8px;}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(label: str, value: str, sub: str = "") -> None:
    st.markdown(
        f"<div class='hero'><span class='k'>{label}</span>"
        f"<span class='v'>{value}</span>"
        + (f"<span class='s'>{sub}</span>" if sub else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def pill(text: str, colour: str) -> str:
    return (
        f"<span class='pill' style='background:{colour}22;color:{colour};"
        f"border:1px solid {colour}55'>{text}</span>"
    )


def factor_bar(name: str, group: str, value: float, largest: float) -> None:
    width = int(100 * value / largest) if largest else 0
    st.markdown(
        f"<div class='factor-row'>"
        f"<span class='factor-name'>{name} <span class='muted'>· {group}</span></span>"
        f"<span class='factor-track'><span class='factor-fill' "
        f"style='width:{max(width, 4)}%'></span></span>"
        f"<span class='factor-val'>+{value:.3f}</span></div>",
        unsafe_allow_html=True,
    )


def evidence(html: str) -> None:
    st.markdown(f"<div class='evidence'>{html}</div>", unsafe_allow_html=True)


def caution(html: str) -> None:
    st.markdown(f"<div class='caution'>{html}</div>", unsafe_allow_html=True)


def bar(frame, x: str, y: str, *, title: str = "", horizontal: bool = True,
        colour: str = ACCENT, height: int = 260, fmt: str = ""):
    """A magnitude chart. One hue: the length is the encoding, not the colour."""
    base = alt.Chart(frame, title=title)
    axis = alt.Axis(format=fmt) if fmt else alt.Undefined
    enc = {
        "x": alt.X(f"{x}:Q", title=None, axis=axis),
        "y": alt.Y(f"{y}:N", title=None, sort="-x"),
    } if horizontal else {
        "x": alt.X(f"{x}:N", title=None, sort=None),
        "y": alt.Y(f"{y}:Q", title=None),
    }
    return (
        base.mark_bar(cornerRadius=4, color=colour, height=14 if horizontal else None)
        .encode(**enc, tooltip=list(frame.columns))
        .properties(height=height)
    )


def band_chart(frame, height: int = 240):
    """Entity counts by risk band. Status colours, in severity order."""
    return (
        alt.Chart(frame, title="Entities by risk band")
        .mark_bar(cornerRadius=4, height=22)
        .encode(
            x=alt.X("count:Q", title=None),
            y=alt.Y("band:N", title=None, sort=BAND_ORDER),
            color=alt.Color(
                "band:N",
                scale=alt.Scale(domain=BAND_ORDER, range=BAND_COLOURS),
                legend=None,
            ),
            tooltip=["band", "count"],
        )
        .properties(height=height)
    )
