# Landing page

Static, single file, no build step.

```bash
python3 -m http.server 8899 --directory site
```

Deploy: point Vercel at this directory. `vercel.json` disables caching so a
redeploy is visible immediately.

## Design

Pure black, an editorial serif for display type, and a single warm accent. The
brief was explicitly *not* the purple/navy gradient look every AI product ships
with, so the page leans monochrome and lets typography carry it.

The hero visual is a live canvas animation of the thing the project actually
does: a transaction leaves an origin host, reaches listening sensors, and the
nearest one hears it first. The order deliberately varies between cycles —
attribution is a statistical inference, not a lookup, and an animation that
always resolved the same way would misrepresent the method.

## Linking to the Explorer

The CTAs point at `http://localhost:8502`, the local Streamlit app. Change those
to the deployed dashboard URL before shipping the site anywhere public.
