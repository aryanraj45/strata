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

# The landing page's tokens, verbatim. The explorer is the same product, so it
# is the same palette: black, one amber, cream ink. No second accent hue.
BG = "#000000"
SURFACE = "#0b0a09"
LINE = "#1c1a17"
LINE_2 = "#2a2723"

# Ink
INK = "#f2efe9"
INK_DIM = "#a9a29a"
INK_FAINT = "#6b655e"

# Accent -- identity, not state
ACCENT = "#e0a24a"
AMBER_DIM = "#8a6428"

# Reserved status colours. Never reused as categorical series.
CRITICAL = "#c9564f"
SERIOUS = "#d98b3f"
WARNING = "#c9a227"
GOOD = "#7f9c6b"
NEUTRAL = "#6b655e"

# Node identity in the link graph
ENTITY_HUE = "#e0a24a"
IP_HUE = "#c9564f"
ASN_HUE = "#8a7f6d"

SERIF = 'ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif'
MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace'

SEQUENTIAL = ["#3a2a12", "#6b4a1c", "#a06f28", "#d09a3e", "#f0cd8a"]


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
          /* ---- the page itself, on the landing page's terms ---- */
          .stApp, [data-testid="stAppViewContainer"] {{background:{BG};}}
          [data-testid="stHeader"] {{background:transparent;}}
          .block-container {{padding-top: 2.2rem; padding-bottom: 5rem; max-width: 1500px;}}

          /* Headings in the serif, same as the landing page. Streamlit ships a
             sans for everything, which is what made this read as a template. */
          h1, h2, h3 {{
            font-family:{SERIF} !important; font-weight:400 !important;
            letter-spacing:-.015em; color:{INK};
          }}
          h1 {{font-size:2.5rem !important;}}
          h2 {{font-size:1.9rem !important;}}
          h3 {{font-size:1.45rem !important;}}

          [data-testid="stSidebar"] {{background:#050505; border-right:1px solid {LINE};}}
          [data-testid="stSidebarNav"] {{padding-top:.5rem;}}

          /* ---- everything arrives, rather than appearing ---- */
          @keyframes rise {{from {{opacity:0; transform:translateY(14px);}}
                            to   {{opacity:1; transform:none;}}}}
          .block-container [data-testid="stVerticalBlock"] > div {{
            animation: rise .55s cubic-bezier(.2,.7,.2,1) both;
          }}
          @media (prefers-reduced-motion: reduce) {{
            .block-container [data-testid="stVerticalBlock"] > div {{animation:none;}}
          }}

          /* ---- stat cards ---- */
          .hero {{
            display:flex; flex-direction:column; gap:5px;
            padding: 20px 22px; border-radius: 14px;
            background: linear-gradient(180deg, rgba(255,255,255,.022), transparent);
            border: 1px solid {LINE_2}; border-left: 2px solid {ACCENT};
            transition: border-color .3s, transform .3s;
          }}
          .hero:hover {{border-color:{AMBER_DIM}; transform:translateY(-2px);}}
          .hero .k {{font-family:{MONO}; font-size:.68rem; letter-spacing:.13em;
                     text-transform:uppercase; color:{INK_FAINT};}}
          .hero .v {{font-family:{SERIF}; font-size:2.4rem; font-weight:400;
                     line-height:1.05; color:{INK}; font-variant-numeric: tabular-nums;}}
          .hero .s {{font-size:.82rem; color:{INK_DIM};}}

          .pill {{display:inline-block; padding:2px 10px; border-radius:100px;
                  font-family:{MONO}; font-size:.68rem; letter-spacing:.06em;}}

          .factor-row {{display:flex; align-items:center; gap:12px; margin:7px 0;}}
          .factor-name {{min-width:210px; font-size:.85rem; color:{INK_DIM};}}
          .factor-track {{flex:1; height:7px; border-radius:5px; background:#181614;
                          overflow:hidden;}}
          .factor-fill {{height:100%; border-radius:5px;
                         background:linear-gradient(90deg,{AMBER_DIM},{ACCENT});}}
          .factor-val {{font-family:{MONO}; font-size:.76rem; color:{INK}; min-width:58px;
                        text-align:right;}}

          .evidence {{border-left:2px solid {ACCENT}; background:rgba(224,162,74,.05);
                      padding:13px 17px; border-radius:0 8px 8px 0; margin:10px 0;
                      font-size:.9rem;}}
          .caution {{border-left:2px solid {WARNING}; background:rgba(201,162,39,.05);
                     padding:13px 17px; border-radius:0 8px 8px 0; margin:10px 0;
                     font-size:.9rem;}}
          .muted {{color:{INK_DIM}; font-size:.85rem;}}

          .stDataFrame, [data-testid="stDataFrame"] {{
            border:1px solid {LINE}; border-radius:10px;
          }}
          [data-testid="stMetricValue"] {{
            font-family:{SERIF} !important; font-weight:400 !important; color:{INK};
          }}
          [data-testid="stMetricLabel"] {{
            font-family:{MONO} !important; font-size:.68rem !important;
            letter-spacing:.13em; text-transform:uppercase; color:{INK_FAINT} !important;
          }}
          div[role="radiogroup"] {{gap:6px;}}
          div[role="radiogroup"] label {{
            border:1px solid {LINE_2}; border-radius:100px; padding:5px 15px;
            transition:border-color .25s, background .25s;
          }}
          div[role="radiogroup"] label:hover {{border-color:{AMBER_DIM};
                                               background:rgba(224,162,74,.05);}}

          .stButton > button {{
            border-radius:100px; border:1px solid {LINE_2};
            background:{INK}; color:#000; font-weight:600;
            padding:.55rem 1.5rem; transition:transform .2s, box-shadow .3s;
          }}
          .stButton > button:hover {{
            transform:translateY(-1px); border-color:{ACCENT};
            box-shadow:0 8px 26px rgba(224,162,74,.22);
          }}

          /* ---- pipeline run: one row per stage ---- */
          .runrow {{display:flex; align-items:center; gap:14px; padding:11px 2px;
                    border-bottom:1px solid #131211; font-size:.9rem;}}
          .runrow:last-child {{border-bottom:0;}}
          .runrow .idx {{font-family:{MONO}; font-size:.7rem; color:{INK_FAINT};
                         min-width:26px;}}
          .runrow .nm {{color:{INK_FAINT}; min-width:200px;}}
          .runrow .bar {{flex:1; height:4px; border-radius:3px; background:#181614;
                         overflow:hidden;}}
          .runrow .bar i {{display:block; height:100%; width:0; border-radius:3px;
                           background:linear-gradient(90deg,{AMBER_DIM},{ACCENT});}}
          .runrow .st {{font-family:{MONO}; font-size:.7rem; letter-spacing:.1em;
                        text-transform:uppercase; color:{INK_FAINT}; min-width:96px;
                        text-align:right;}}

          .runrow.done .nm {{color:{INK};}}
          .runrow.done .bar i {{width:100%;}}
          .runrow.done .st {{color:{ACCENT};}}

          /* the running stage sweeps, because a stage's real progress is not
             observable from outside its process */
          .runrow.live .nm, .runrow.live .st {{color:{ACCENT};}}
          .runrow.live .bar i {{width:38%; animation: sweep 1.15s ease-in-out infinite;}}
          @keyframes sweep {{
            0%   {{transform:translateX(-110%);}}
            100% {{transform:translateX(300%);}}
          }}
          .runrow.fail .nm, .runrow.fail .st {{color:{CRITICAL};}}
          .runrow.fail .bar i {{width:100%; background:{CRITICAL};}}
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
