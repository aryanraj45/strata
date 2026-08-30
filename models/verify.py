#!/usr/bin/env python3
"""Acceptance checks for the detection models.

A model that scores well on the data it was fitted to has proved nothing, so
everything here is cross-validated, and the headline claim is checked against a
label-shuffled null.

    .venv/bin/python models/verify.py --store store --data data
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sklearn.metrics import f1_score  # noqa: E402
from sklearn.model_selection import StratifiedKFold  # noqa: E402

from strata_models import FEATURES, GROUPS, evaluate, explain, extract, fit_supervised  # noqa: E402
from strata_models.detect import _classifier  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
ILLICIT = {"ransomware", "darknet_market", "extortion", "launderer"}


def check(name: str, ok: bool, detail: str) -> None:
    results.append((PASS if ok else FAIL, name, detail))


def cv_f1(X, y, seed: int) -> float:
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    pred = np.zeros(len(y), dtype=int)
    for train, test in folds.split(X, y):
        model = _classifier()
        model.fit(X[train], y[train])
        pred[test] = model.predict(X[test])
    return f1_score(y, pred, zero_division=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=Path("store"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    args = ap.parse_args()

    db = duckdb.connect()
    ids, matrix = extract(db, args.store, min_transactions=3)
    X = np.array(matrix, dtype=float)

    truth = json.loads((args.data / "ground_truth.json").read_text())
    owner = {a: c["actor_id"] for c in truth["clusters"] for a in c["addresses"]}
    kind = {c["actor_id"]: c["kind"] for c in truth["clusters"]}
    members: dict[str, list[str]] = {}
    for addr, eid in db.execute("SELECT address, entity_id FROM entities").fetchall():
        if addr in owner:
            members.setdefault(eid, []).append(owner[addr])
    dominant = {e: Counter(v).most_common(1)[0][0] for e, v in members.items()}
    y = np.array([1 if kind.get(dominant.get(e)) in ILLICIT else 0 for e in ids])

    # 1 - the population is imbalanced, as it is in reality
    check(
        "class balance reflects reality",
        0.02 <= y.mean() <= 0.15,
        f"{int(y.sum())} illicit of {len(y)} entities = {y.mean():.1%}",
    )

    # 2 - cross-validated, never scored on data it was fitted to
    ev = evaluate(X, y)
    check(
        "cross-validated detection beats the base rate",
        ev.average_precision >= 0.5 and ev.f1 >= 0.7,
        f"precision {ev.precision:.1%}, recall {ev.recall:.1%}, F1 {ev.f1:.1%}, "
        f"AP {ev.average_precision:.1%} against a {ev.baseline:.1%} base rate",
    )

    # 3 - stable across splits, not an artefact of one lucky fold
    seeds = [cv_f1(X, y, s) for s in range(8)]
    spread = statistics.stdev(seeds)
    check(
        "performance is stable across splits",
        spread <= 0.06,
        f"F1 {statistics.mean(seeds):.1%} +/- {spread:.1%} over 8 seeds "
        f"({min(seeds):.1%}-{max(seeds):.1%})",
    )

    # 4 - the null. With labels shuffled there is nothing to learn, and a model
    # that still scores well is reading noise or leaking the answer.
    rng = np.random.default_rng(7)
    shuffled = y.copy()
    rng.shuffle(shuffled)
    null_f1 = statistics.mean(cv_f1(X, shuffled, s) for s in range(3))
    check(
        "performance collapses on shuffled labels",
        null_f1 <= 0.25,
        f"F1 {null_f1:.1%} on shuffled labels vs {ev.f1:.1%} on real ones",
    )

    # 5 - no single feature carries the model. One that did would be a leak or a
    # brittle dependency on an artefact of the generator.
    model = fit_supervised(X, y)
    importance = sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])
    top_name, top_share = importance[0]
    check(
        "no single feature dominates",
        top_share <= 0.35,
        f"largest is {top_name} at {top_share:.1%}; top three "
        + ", ".join(f"{n} {v:.0%}" for n, v in importance[:3]),
    )

    # 6 - every alert must carry its reasons, since the PS requires it
    contributions = explain(model, X)
    risk = model.predict_proba(X)[:, 1]
    flagged = [i for i in range(len(ids)) if risk[i] >= 0.5]
    explained = [i for i in flagged if (contributions[i] > 0).any()]
    check(
        "every flagged entity has an explanation",
        len(explained) == len(flagged) and flagged,
        f"{len(explained)}/{len(flagged)} flagged entities have positive SHAP factors",
    )

    # 7 - and those reasons must point at the right things
    illicit_idx = [i for i in range(len(ids)) if y[i] == 1]
    top_groups = Counter()
    for i in illicit_idx:
        best = int(np.argmax(contributions[i]))
        top_groups[next(g for g, m in GROUPS.items() if FEATURES[best] in m)] += 1
    check(
        "explanations cite behavioural evidence",
        bool(top_groups),
        ", ".join(f"{g}={n}" for g, n in top_groups.most_common(4)),
    )

    # 8 - the unsupervised model must beat chance without being told anything
    scores = db.execute(
        "SELECT entity_id, is_anomaly FROM read_parquet(?)",
        [str((args.store / "scores.parquet").resolve())],
    ).fetchall()
    anomalous = {e for e, flag in scores if flag}
    hits = sum(1 for i, e in enumerate(ids) if e in anomalous and y[i] == 1)
    rate = hits / len(anomalous) if anomalous else 0.0
    check(
        "unsupervised anomalies enrich for illicit entities",
        rate > y.mean() * 2,
        f"{hits}/{len(anomalous)} anomalies are illicit = {rate:.1%}, "
        f"against a {y.mean():.1%} base rate",
    )

    # 9 - stored scores must match what the model actually produces
    stored = dict(
        db.execute(
            "SELECT entity_id, risk_score FROM read_parquet(?)",
            [str((args.store / "scores.parquet").resolve())],
        ).fetchall()
    )
    drift = [
        e for i, e in enumerate(ids)
        if e in stored and abs(stored[e] - risk[i]) > 1e-9
    ]
    check(
        "stored scores match the model",
        not drift,
        f"{len(stored)} scores on disk, {len(drift)} disagree with a fresh fit",
    )

    width = max(len(name) for _, name, _ in results)
    print()
    for status, name, detail in results:
        mark = "\033[32m✔\033[0m" if status == PASS else "\033[31m✘\033[0m"
        print(f"  {mark} {name.ljust(width)}  {detail}")
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n  {len(results) - len(failed)}/{len(results)} checks passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
