#!/usr/bin/env python3
"""Train the detection models and score every entity.

    .venv/bin/python models/run.py --store store --data data

Labels come from ground truth, which is legitimate for a supervised model and is
what the Elliptic benchmark does too. No feature is derived from the label: a
feature that leaks the answer makes a model look excellent and be worthless.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from strata_models import (
    FEATURES, GROUPS, evaluate, explain, extract, fit_supervised, fit_unsupervised,
    top_factors,
)
from strata_models.detect import ablate

ILLICIT = {"ransomware", "darknet_market", "extortion", "launderer"}


def labels_for(db, ids, data: Path) -> np.ndarray:
    """Label an entity by the actor most of its addresses belong to.

    Entity resolution is ~97% precise, not perfect, so an entity is labelled by
    its majority owner rather than assumed pure.
    """
    truth = json.loads((data / "ground_truth.json").read_text())
    owner = {a: c["actor_id"] for c in truth["clusters"] for a in c["addresses"]}
    kind = {c["actor_id"]: c["kind"] for c in truth["clusters"]}

    members: dict[str, list[str]] = {}
    for addr, eid in db.execute("SELECT address, entity_id FROM entities").fetchall():
        if addr in owner:
            members.setdefault(eid, []).append(owner[addr])
    dominant = {e: Counter(v).most_common(1)[0][0] for e, v in members.items()}
    return np.array([1 if kind.get(dominant.get(e)) in ILLICIT else 0 for e in ids])


def main() -> int:
    ap = argparse.ArgumentParser(description="STRATA detection models")
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--min-transactions", type=int, default=3)
    ap.add_argument("--ablate", action="store_true",
                    help="measure what each pipeline stage contributes (slow)")
    args = ap.parse_args()

    db = duckdb.connect()
    ids, matrix = extract(db, args.store, min_transactions=args.min_transactions)
    X = np.array(matrix, dtype=float)
    y = labels_for(db, ids, args.data)

    print(f"  entities           {len(ids):>7,}   with >={args.min_transactions} transactions")
    print(f"  features           {len(FEATURES):>7,}")
    print(f"  illicit            {int(y.sum()):>7,}   ({y.mean():.1%} of the population)")

    # --- supervised, cross-validated ------------------------------------
    ev = evaluate(X, y)
    print(f"\n  RandomForest, {len(ev.per_fold)}-fold cross-validated")
    print(f"    precision        {ev.precision:>7.1%}")
    print(f"    recall           {ev.recall:>7.1%}")
    print(f"    F1               {ev.f1:>7.1%}")
    print(f"    average precision{ev.average_precision:>7.1%}   (a random model scores {ev.baseline:.1%})")

    if args.ablate:
        # What each stage of the pipeline is worth, measured rather than claimed.
        print("\n  cumulative feature groups")
        print(f"    {'added':<12} {'n':>3}  {'precision':>10} {'recall':>8} {'F1':>7}")
        for name, n, e in ablate(X, y, GROUPS):
            print(f"    + {name:<10} {n:>3}  {e.precision:>9.1%} {e.recall:>8.1%} {e.f1:>7.1%}")
        print("    network and laundering features are neutral for classification;")
        print("    their value is attribution, which classification cannot do at all")

    model = fit_supervised(X, y)
    risk = model.predict_proba(X)[:, 1]

    # --- unsupervised ----------------------------------------------------
    iso = fit_unsupervised(X)
    anomaly = -iso.score_samples(X)          # higher is stranger
    is_anomaly = iso.predict(X) == -1
    caught = int(((y == 1) & is_anomaly).sum())
    print(f"\n  IsolationForest, no labels")
    print(f"    flagged          {int(is_anomaly.sum()):>7,}   entities as anomalous")
    print(f"    of which illicit {caught:>7,}   ({caught / max(int(is_anomaly.sum()), 1):.1%})")

    # --- explanation ------------------------------------------------------
    contributions = explain(model, X)
    factors = [top_factors(contributions[i]) for i in range(len(ids))]

    rows = [
        (ids[i], float(risk[i]), float(anomaly[i]), bool(is_anomaly[i]),
         json.dumps(factors[i]), int(y[i]))
        for i in range(len(ids))
    ]
    out = args.store / "scores.parquet"
    db.execute(
        "CREATE OR REPLACE TABLE scores (entity_id VARCHAR, risk_score DOUBLE, "
        "anomaly_score DOUBLE, is_anomaly BOOLEAN, factors VARCHAR, label INT)"
    )
    db.executemany("INSERT INTO scores VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.execute(f"COPY scores TO '{out.resolve()}' (FORMAT PARQUET)")

    order = np.argsort(risk)[::-1][:5]
    print(f"\n  highest risk")
    print(f"  {'entity':<18} {'risk':>6}  top factors")
    for i in order:
        names = ", ".join(f["name"] for f in factors[i][:3])
        print(f"  {ids[i]:<18} {risk[i]:>6.2f}  {names}")

    print(f"\n  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
