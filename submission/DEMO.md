# Demo

**Project:** STRATA — AI-Powered Monitoring and Analysis of Bitcoin Transaction Traffic
**PS ID:** 26146 · Smart India Hackathon 2026

## Live links

Both are public and need no login.

| | |
|---|---|
| Landing page | https://strata-alpha-two.vercel.app/ |
| Forensic Explorer | https://strata-forensics.streamlit.app/ |

The landing page's **Open the Explorer** button leads to the Explorer, so a
reviewer can start from either one.

## Demo video

<!-- Optional. Paste a YouTube or Google Drive link and check it while logged
     out — a link that asks the reviewer to request access counts as missing. -->

`<PASTE YOUTUBE OR GOOGLE DRIVE LINK, OR DELETE THIS SECTION>`

## Walkthrough for a reviewer

Roughly five minutes, no setup required.

**1 · Landing page.** The claim in one line: the ledger shows the money, the
network layer shows the person.

**2 · Explorer → Overview.** Entities under review, how many are high risk, and
how many have been attributed to a candidate origin host. Every figure here was
produced by a pipeline stage and checked by that stage's own tests — the
dashboard reports, it does not calculate.

**3 · Explorer → Alerts.** Pick any row. *Why flagged* shows the SHAP
decomposition — which features pushed the score up, and by how much. Never a
bare number.

**4 · Alerts → Money flow.** The entity, the hosts that were repeatedly first to
relay its transactions, and the counterparties it paid. Double-click a node to
expand it.

**5 · Explorer → Network origins.** The dual-layer result: wallet entities tied
to origin hosts, with the sighting counts and the corrected probability behind
each attribution.

**6 · Explorer → Pipeline → Run all six stages.** Rebuilds the store live, in
front of you. Nothing is replayed; each stage is a real process.

## Running it locally instead

See the *Running it after a clone* section of the main [README](../README.md).
Four commands from a clean checkout to a running dashboard.
