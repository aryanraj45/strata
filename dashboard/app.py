#!/usr/bin/env python3
"""STRATA — forensic explorer.

    .venv/bin/streamlit run dashboard/app.py

Reads the store the pipeline produced and presents it as an investigator would
use it. Nothing is computed here: every figure on screen was produced by a
pipeline stage and checked by that stage's verify.py. A dashboard that quietly
does its own arithmetic is a second, unverified implementation of the system,
and the two drift apart exactly when it matters.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from strata_dashboard import data as store  # noqa: E402
from strata_dashboard import ui  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "store"

# Where the landing page is served from. Override when it is not the local
# static server -- the two are separate processes and neither can discover the
# other, so the address has to be stated somewhere.
LANDING_URL = os.environ.get("STRATA_LANDING_URL", "http://localhost:8899/")
store.STORE_PATH = STORE

st.set_page_config(
    page_title="STRATA — Forensic Explorer",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.inject_css()


# ---------------------------------------------------------------- loading

@st.cache_resource(show_spinner=False)
def _db(path: str, _stamp: float):
    """One connection per store. `_stamp` invalidates it when the store changes."""
    return store.connect(Path(path))


def _stamp(path: Path) -> float:
    return max((f.stat().st_mtime for f in path.glob("*.parquet")), default=0.0)


@st.cache_data(show_spinner=False)
def _alerts(path: str, _s: float) -> pd.DataFrame:
    return pd.DataFrame(store.alerts(_db(path, _s)))


@st.cache_data(show_spinner=False)
def _totals(path: str, _s: float) -> dict:
    return store.summary(_db(path, _s))


def _no_store() -> None:
    st.title("🛰️ STRATA")
    st.error("No store found. Run the pipeline first:")
    st.code(
        "python generator/generate.py --out data\n"
        "./ingest/target/release/strata-ingest data/strata.* --geoip data/geoip --out store\n"
        "python cluster/run.py --store store\n"
        "python graph/run.py   --store store\n"
        "python fusion/run.py  --store store\n"
        "python models/run.py  --store store --data data",
        language="bash",
    )
    st.stop()


if not STORE.exists() or not list(STORE.glob("*.parquet")):
    _no_store()

stamp = _stamp(STORE)
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
totals = _totals(str(STORE), stamp)


# ---------------------------------------------------------------- chrome

with st.sidebar:
    # Back to the landing page. st.navigation only knows about pages inside
    # this app, so the way out has to be an explicit external link.
    st.page_link(LANDING_URL, label="Home", icon=":material/home:")
    st.divider()

    st.markdown("## 🛰️ STRATA")
    st.caption("Dual-layer Bitcoin transaction forensics")
    st.markdown(
        f"<div style='font-size:.8rem;color:{ui.INK_FAINT};line-height:1.7'>"
        f"SIH 2026 · PS 26146<br>National Technical Research Organisation"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(
        f"<div style='font-size:.8rem;color:{ui.INK_DIM};line-height:1.8'>"
        f"<b style='color:{ui.INK}'>{totals['scored']:,}</b> entities scored<br>"
        f"<b style='color:{ui.CRITICAL}'>{totals['high_risk']:,}</b> high risk<br>"
        f"<b style='color:{ui.ACCENT}'>{totals['attributed']:,}</b> origins attributed"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(
        f"<div style='font-size:.75rem;color:{ui.INK_FAINT};line-height:1.6'>"
        f"Runs entirely offline. No outbound requests — the GeoIP database and every "
        f"component asset are local files."
        f"</div>",
        unsafe_allow_html=True,
    )


from views import alerts as alerts_view  # noqa: E402
from views import chains as chains_view  # noqa: E402
from views import network as network_view  # noqa: E402
from views import overview as overview_view  # noqa: E402
from views import pipeline as pipeline_view  # noqa: E402


def _page(view, title: str, icon: str, path: str, default: bool = False) -> st.Page:
    """Bind a view to the loaded store.

    Every page closure would otherwise be named `run`, and Streamlit derives a
    page's URL from the callable's name — so the path is given explicitly.
    """
    def render():
        view.render(db, totals, alerts)

    return st.Page(render, title=title, icon=icon, url_path=path, default=default)


navigation = st.navigation([
    _page(overview_view, "Overview", ":material/dashboard:", "overview", default=True),
    _page(alerts_view, "Alerts", ":material/warning:", "alerts"),
    _page(network_view, "Network origins", ":material/router:", "network"),
    _page(chains_view, "Laundering", ":material/link:", "laundering"),
    _page(pipeline_view, "Pipeline", ":material/account_tree:", "pipeline"),
])
navigation.run()
