"""Landing page — the static site rendered inside the Streamlit app.

Loads site/index.html and its JS assets, inlines them into a single HTML blob,
rewrites the localhost:8502 links to point at the Streamlit app's /overview page,
and renders the whole thing via st.components.v1.html().

The result: one URL, judges see the landing page first, click "Open Explorer",
and land in the dashboard — no second deployment, no second link.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

SITE = Path(__file__).resolve().parents[2] / "site"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_string_literal(code: str) -> str:
    """Wrap arbitrary JS source in a JS template literal, safely escaped."""
    escaped = code.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return f"`{escaped}`"


def _build_page() -> str:
    html = _load_text(SITE / "index.html")
    world_js = _load_text(SITE / "world.js")
    three_js = _load_text(SITE / "vendor" / "three.module.js")

    # world.js imports Three.js from a relative path — rewrite to blob URL
    # so the ES module import works inside the Streamlit iframe.
    world_js = world_js.replace(
        "from './vendor/three.module.js'",
        "from '__THREE_BLOB_URL__'"
    )

    # Build inline script that loads Three.js + world.js as ES module blobs
    inline_script = f"""
<script type="module">
const threeCode = {_js_string_literal(three_js)};
const threeBlob = new Blob([threeCode], {{ type: 'application/javascript' }});
const threeBlobUrl = URL.createObjectURL(threeBlob);

const worldCode = {_js_string_literal(world_js)}.replace('__THREE_BLOB_URL__', threeBlobUrl);
const worldBlob = new Blob([worldCode], {{ type: 'application/javascript' }});
const worldBlobUrl = URL.createObjectURL(worldBlob);
import(worldBlobUrl);
</script>"""

    # Replace external script tag with inlined version
    html = html.replace(
        '<script type="module" src="world.js"></script>',
        inline_script,
    )

    # Rewrite "Open Explorer" links to navigate the parent frame to /overview
    html = html.replace(
        'href="http://localhost:8502"',
        'href="/overview" target="_top"',
    )

    return html


def render(_db, _totals: dict, _alerts) -> None:
    # Hide Streamlit chrome for a clean, full-bleed landing page
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] { display: none; }
            .stMainBlockContainer { padding: 0 !important; max-width: 100% !important; }
            header[data-testid="stHeader"] { display: none; }
            .stAppDeployButton { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    html = _build_page()
    components.html(html, height=4000, scrolling=True)
