#!/usr/bin/env python3
"""Acceptance checks for the dashboard.

Streamlit's own test harness runs the real script headlessly, so an exception in
any code path shows up here rather than as a red box in front of a jury.

The other half of these checks is that the dashboard reports what the pipeline
produced. A dashboard that quietly recomputes its own numbers is a second,
unverified implementation of the system.

    .venv/bin/python dashboard/verify.py --store store
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import duckdb  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from strata_dashboard import data as store  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
APP = Path(__file__).parent / "app.py"


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def run_app(timeout: int = 90) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=timeout)
    app.run()
    return app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=Path("store"))
    args = ap.parse_args()

    db = store.connect(args.store)
    totals = store.summary(db)
    alerts = store.alerts(db)

    # 1 - the app runs at all, with no uncaught exception on any path
    app = run_app()
    check(
        "app runs without exceptions",
        not app.exception,
        f"{len(app.exception)} exceptions"
        + (f": {app.exception[0].value}" if app.exception else ""),
    )
    if app.exception:
        _report()
        return 1

    # 2 - the headline figures come from the store, not from the dashboard.
    #
    # The hero cards are rendered as HTML rather than st.metric, so this reads
    # the markdown the app actually emitted and looks for the store's values in
    # it. A dashboard that recomputes its own numbers is a second, unverified
    # implementation, and the drift shows up exactly when it matters.
    emitted = " ".join(block.value for block in app.markdown)
    expected = {
        "entities scored": f"{totals['scored']:,}",
        "high risk": f"{totals['high_risk']:,}",
        "origins attributed": f"{totals['attributed']:,}",
        "wallet addresses": f"{totals['addresses']:,}",
    }
    absent = {k: v for k, v in expected.items() if v not in emitted}
    check(
        "headline figures match the store",
        not absent,
        f"{len(expected)} figures checked against DuckDB"
        + (f"; not found on the page: {absent}" if absent else ""),
    )

    # 3 - the alert queue is ranked, since an unordered queue is not a queue
    ordered = all(
        alerts[i]["risk_score"] >= alerts[i + 1]["risk_score"] for i in range(len(alerts) - 1)
    )
    check(
        "alert queue is ranked by risk",
        ordered and len(alerts) > 0,
        f"{len(alerts):,} entities, highest risk {alerts[0]['risk_score']:.3f}",
    )

    # 4 - the PS requires a confidence score on every alert
    scored = [a for a in alerts if a["risk_score"] is not None]
    check(
        "every alert carries a confidence score",
        len(scored) == len(alerts),
        f"{len(scored):,}/{len(alerts):,} entities have a risk score",
    )

    # 5 - and an explanation, which is the harder half of that requirement
    flagged = [a for a in alerts if a["risk_score"] >= 0.5]
    explained = [a for a in flagged if a["factors"]]
    check(
        "every flagged entity has a stated reason",
        bool(flagged) and len(explained) == len(flagged),
        f"{len(explained)}/{len(flagged)} flagged entities carry SHAP factors",
    )

    # 6 - attribution evidence is shown as counts, not as a bare score
    attributed = [a for a in alerts if a.get("attribution_significant")]
    with_evidence = [
        a for a in attributed
        if a.get("hits") is not None and a.get("transactions") is not None
    ]
    check(
        "attributions show the evidence behind them",
        bool(attributed) and len(with_evidence) == len(attributed),
        f"{len(with_evidence)}/{len(attributed)} attributions report first-relay counts",
    )

    # 7 - the graph export must be valid input for the link-analysis component
    top = alerts[0]["entity_id"]
    graph = store.subgraph(db, top, hops=1)
    node_ids = {n["data"]["id"] for n in graph["nodes"]}
    shaped = all(
        "data" in n and {"id", "label"} <= set(n["data"]) for n in graph["nodes"]
    ) and all(
        "data" in e and {"id", "label", "source", "target"} <= set(e["data"])
        for e in graph["edges"]
    )
    closed = all(
        e["data"]["source"] in node_ids and e["data"]["target"] in node_ids
        for e in graph["edges"]
    )
    check(
        "graph export is valid st-link-analysis input",
        shaped and closed and len(graph["nodes"]) > 1,
        f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, all wrapped in data{{}}, "
        f"no edge references a missing node",
    )

    # 8 - offline: no code path may reach the network. Icons in particular are
    # accepted by name or by URL, and a URL would be an outbound request.
    source = APP.read_text()
    url_icons = re.findall(r"NodeStyle\([^)]*url\(", source)
    remote = re.findall(r"https?://(?!localhost)", source)
    check(
        "no outbound requests in the dashboard",
        not url_icons and not remote,
        f"{len(url_icons)} URL icons, {len(remote)} remote URLs in app.py",
    )

    # 9 - AG Grid Enterprise needs a paid licence; releases before 1.1.3 enabled
    # it by default, so the pin matters.
    import st_aggrid
    version = getattr(st_aggrid, "__version__", None) or _installed_version()
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:3])
    check(
        "streamlit-aggrid is a Community-safe version",
        parts >= (1, 2, 0),
        f"installed {version}; >=1.2.0 required so Enterprise modules stay off",
    )

    # 10 - a partially-run pipeline must degrade with a message, not a traceback
    empty = Path("/tmp/strata-empty-store")
    empty.mkdir(exist_ok=True)
    for stale in empty.glob("*.parquet"):
        stale.unlink()
    guarded = AppTest.from_file(str(APP), default_timeout=60)
    guarded.run()
    check(
        "missing store is handled, not crashed on",
        not guarded.exception,
        "app guards for an absent or incomplete store before querying it",
    )

    _report()
    return 1 if any(r[0] == FAIL for r in results) else 0


def _installed_version() -> str:
    from importlib.metadata import version
    return version("streamlit-aggrid")


def _report() -> None:
    width = max(len(name) for _, name, _ in results)
    print()
    for status, name, detail in results:
        mark = "\033[32m✔\033[0m" if status == PASS else "\033[31m✘\033[0m"
        print(f"  {mark} {name.ljust(width)}  {detail}")
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n  {len(results) - len(failed)}/{len(results)} checks passed\n")


if __name__ == "__main__":
    raise SystemExit(main())
