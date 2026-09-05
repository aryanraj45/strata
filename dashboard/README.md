# Dashboard

    .venv/bin/streamlit run dashboard/app.py

Reads `store/` and presents it as an investigator would use it: a ranked queue,
then the evidence behind whichever alert is selected.

## What it shows

| Panel | Source | Answers |
|---|---|---|
| Alert queue | `scores`, `leads` | Which entities to look at first |
| Why flagged | SHAP factors from `models` | Why the model scored it that way |
| Network evidence | `leads`, `net_l2` | Which host broadcast it, and how sure we are |
| Money flow | `graph_nodes`, `graph_edges` | Who it paid, and where it was seen from |
| Transactions | `tx_l1`, `entities` | The underlying ledger records |

## A note on responsibility

The dashboard computes nothing. Every figure on screen was produced by a
pipeline stage and checked by that stage's `verify.py`. A dashboard that quietly
does its own arithmetic is a second, unverified implementation of the system --
and the two drift apart exactly when it matters.

`dashboard/verify.py` asserts this directly: the headline metrics are compared
against fresh DuckDB queries over the same store.

## Offline

No outbound requests. `st-link-analysis` accepts node icons by name or by URL;
we use names only, since a URL is a network call. `verify.py` fails if a URL
icon or remote host appears in `app.py`.

`streamlit-aggrid` is pinned to `>=1.2.0`: earlier releases enabled AG Grid
*Enterprise* modules by default, which require a paid licence.

## Known gap

Streamlit's `AppTest` harness runs the real script and catches exceptions on
every code path, but it does not render custom components. So AgGrid row
selection and the link-analysis graph are exercised only in a browser, not by
`verify.py`. The data those components receive *is* checked -- element shape,
closure, and provenance -- but the click-through itself needs a human.
