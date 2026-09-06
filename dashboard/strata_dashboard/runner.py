"""Run the pipeline from the dashboard, stage by stage, and show it happening.

Each stage is a real subprocess against the real repo -- nothing here replays a
recording. That is the point: a judge can press the button and watch the store
be rebuilt from the generator up.

Progress inside a stage is not observable from outside its process, so a
running stage sweeps rather than filling to a percentage. Claiming "62%" when
nothing reports 62% would be a lie told in CSS.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


@dataclass(frozen=True)
class Stage:
    name: str
    note: str
    argv: list[str]


def stages(store: Path, data: Path) -> list[Stage]:
    ingest = ROOT / "ingest" / "target" / "release" / "strata-ingest"
    return [
        # Volume and seed are pinned, not left to the generator's defaults.
        # Running with the defaults rebuilds a much smaller dataset, which
        # quietly moves every headline figure the write-up quotes.
        Stage("Generator", "Synthesises the dataset and its ground truth",
              [PY, "generator/generate.py", "--out", str(data),
               "--volume", "5950", "--days", "14", "--seed", "42"]),
        Stage("Ingest", "Rust. CSV, JSON and XML into one shape, GeoIP resolved",
              [str(ingest), *sorted(str(p) for p in data.glob("strata.*")),
               "--geoip", str(data / "geoip"), "--out", str(store)]),
        Stage("Clustering", "CoinJoins filtered, then addresses spent together",
              [PY, "cluster/run.py", "--store", str(store)]),
        Stage("Graph", "Peeling chains recovered as paths",
              [PY, "graph/run.py", "--store", str(store)]),
        Stage("Fusion", "Network sightings correlated with ledger entities",
              [PY, "fusion/run.py", "--store", str(store)]),
        Stage("Detection", "Supervised and unsupervised models, SHAP explanations",
              [PY, "models/run.py", "--store", str(store), "--data", str(data)]),
    ]


def can_ingest() -> bool:
    """Whether the compiled ingest stage can run in this environment.

    `ingest/` is Rust and `ingest/target/` is a build artefact, so it is not
    committed and a hosted deploy has the source with nothing to execute.
    """
    binary = ROOT / "ingest" / "target" / "release" / "strata-ingest"
    return binary.exists() and os.access(binary, os.X_OK)


# Rebuilding the dataset means running the generator and then the Rust ingest.
# Where that binary is absent the first two stages cannot run -- but the other
# four are pure Python over a store that already exists, so they can, and they
# are the stages that do the actual analysis. Running four real stages beats
# disabling the button and showing nobody anything.
NEEDS_INGEST = frozenset({"Generator", "Ingest"})


def partition(all_stages: list[Stage]) -> tuple[list[Stage], list[Stage]]:
    """Split into (stages that can run here, stages that cannot)."""
    if can_ingest():
        return all_stages, []
    runnable = [s for s in all_stages if s.name not in NEEDS_INGEST]
    return runnable, [s for s in all_stages if s.name in NEEDS_INGEST]


def row(i: int, stage: Stage, state: str, detail: str = "") -> str:
    """One stage as a row. `state` is idle | live | done | fail."""
    label = {"idle": "waiting", "live": "running", "done": detail or "done",
             "fail": "failed", "skip": "not here"}[state]
    return (
        f"<div class='runrow {state}'>"
        f"<span class='idx'>{i + 1:02d}</span>"
        f"<span class='nm'>{stage.name}</span>"
        f"<span class='bar'><i></i></span>"
        f"<span class='st'>{label}</span>"
        f"</div>"
    )


def run(stage: Stage, timeout: int = 900) -> tuple[bool, str, float]:
    """Run one stage. Returns (ok, output tail, seconds)."""
    began = time.monotonic()
    try:
        proc = subprocess.run(
            stage.argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as err:
        return False, f"not found: {err}", time.monotonic() - began
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s", time.monotonic() - began

    took = time.monotonic() - began
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()[-4000:], took
