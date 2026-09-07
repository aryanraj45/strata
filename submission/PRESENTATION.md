# Presentation

**Project:** STRATA — AI-Powered Monitoring and Analysis of Bitcoin Transaction Traffic
**PS ID:** 26146 · Smart India Hackathon 2026
**Organisation:** National Technical Research Organisation (NTRO)

## Final presentation

<!-- Upload the PPTX to this folder and replace the line below with a link to it,
     e.g. [Open the final presentation](./STRATA_SIH2026_Presentation.pptx) -->

`<ADD THE PPTX HERE, OR PASTE A GOOGLE DRIVE / ONEDRIVE VIEWER LINK>`

If the file is too large for GitHub, upload it to Drive/OneDrive, set sharing to
*anyone with the link can view*, and paste the link above. Check it in a
logged-out browser before submitting — a link that asks the reviewer to request
access counts as inaccessible.

## What the deck should cover

The repository already documents the system in depth; the deck exists to carry
the argument in ten minutes.

1. **The problem.** The blockchain records what moved and never who moved it.
   Ledger-only forensics can follow money but cannot name a machine.
2. **The insight.** A transaction crosses the peer-to-peer network moments
   before it reaches the ledger, and there it carries an IP address. Correlating
   the two layers is what turns a wallet into a host.
3. **Why one sighting proves nothing.** Any transaction can arrive first from
   any peer by chance. The evidence is repetition, tested against how often that
   host leads traffic generally.
4. **The pipeline.** Six stages, generator through detection, each with its own
   test suite scored against ground truth.
5. **The negative control.** Shuffling the entity labels destroys every real
   association. A sound method must then find nothing. Ours found 41 leads until
   the multiple-comparisons correction was added — this is the most useful
   result in the project and worth a slide of its own.
6. **Offline operation.** No outbound request at any stage. The GeoIP database
   ships inside the repository, because for an intelligence service the query
   itself is the secret.
7. **Live demo.** The deployed link, and the pipeline rebuilding itself on
   screen.

## Notes for the demo

The Explorer runs the pipeline live from its Pipeline page. On the hosted
deployment all six stages execute; the ingest stage uses the Linux binary built
by CI and committed under `ingest/prebuilt/`.
