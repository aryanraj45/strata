"""Supervised risk scoring, unsupervised anomaly detection, and SHAP attribution.

Two models, because they answer different questions and one cannot replace the
other.

The RandomForest learns what known illicit behaviour looks like. It is only as
good as its labels, and criminal tradecraft changes faster than labels arrive.
The IsolationForest is told nothing at all -- it flags entities that simply do
not resemble the rest of the population, which is how anything genuinely new
gets noticed.

Every score is decomposed with SHAP. The problem statement requires an
explainable alert list, and a number an analyst cannot interrogate is not
evidence -- it is an assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shap
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import average_precision_score, precision_recall_fscore_support
from sklearn.model_selection import StratifiedKFold

from .features import FEATURES, group_of

RANDOM_STATE = 42

# Illicit entities are a small minority, as they are in reality. A single
# train/test split would put a handful of positives in the test set and the
# resulting numbers would move several points on the roll of the split. Repeated
# cross-validation reports what the model actually does.
N_FOLDS = 5


@dataclass
class Evaluation:
    precision: float
    recall: float
    f1: float
    average_precision: float
    support: int
    baseline: float  # what a model that guesses the majority class would score
    per_fold: list[tuple[float, float]] = field(default_factory=list)


def evaluate(X: np.ndarray, y: np.ndarray) -> Evaluation:
    """Cross-validated performance on the illicit class."""
    folds = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    predictions = np.zeros(len(y), dtype=int)
    scores = np.zeros(len(y), dtype=float)
    per_fold = []

    for train_idx, test_idx in folds.split(X, y):
        model = _classifier()
        model.fit(X[train_idx], y[train_idx])
        predictions[test_idx] = model.predict(X[test_idx])
        scores[test_idx] = model.predict_proba(X[test_idx])[:, 1]
        p, r, _, _ = precision_recall_fscore_support(
            y[test_idx], predictions[test_idx], average="binary", zero_division=0
        )
        per_fold.append((p, r))

    p, r, f1, _ = precision_recall_fscore_support(
        y, predictions, average="binary", zero_division=0
    )
    return Evaluation(
        precision=p,
        recall=r,
        f1=f1,
        average_precision=average_precision_score(y, scores),
        support=int(y.sum()),
        baseline=float(y.mean()),
        per_fold=per_fold,
    )


def _classifier() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=2,
        # Without this the model can score 93% accuracy by calling everything
        # licit, which is worse than useless for finding the 6% that are not.
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


def fit_supervised(X: np.ndarray, y: np.ndarray) -> RandomForestClassifier:
    model = _classifier()
    model.fit(X, y)
    return model


def fit_unsupervised(X: np.ndarray, contamination: float = 0.08) -> IsolationForest:
    """Flag entities that do not resemble the population, with no labels at all."""
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X)
    return model


def explain(model: RandomForestClassifier, X: np.ndarray) -> np.ndarray:
    """Per-entity SHAP contributions towards the illicit class.

    Returns an (n_entities, n_features) array in the model's probability units,
    so a contribution of +0.34 means that feature pushed the score up by 0.34.
    """
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(X, check_additivity=False)

    # Recent SHAP returns (n, features, classes) for classifiers; older versions
    # return a list per class. Take the illicit class either way.
    values = np.asarray(values)
    if values.ndim == 3:
        return values[:, :, 1]
    return values


def top_factors(contributions: np.ndarray, k: int = 5) -> list[dict]:
    """The k features that pushed this entity's score up the most.

    Only positive contributions: an analyst asking why something was flagged is
    not helped by the reasons it nearly was not.
    """
    order = np.argsort(contributions)[::-1]
    out = []
    for idx in order[:k]:
        value = float(contributions[idx])
        if value <= 0:
            break
        out.append({
            "name": FEATURES[idx],
            "group": group_of(FEATURES[idx]),
            "contribution": round(value, 4),
        })
    return out


def ablate(X: np.ndarray, y: np.ndarray, groups: dict[str, list[str]]) -> list[tuple]:
    """How much each layer of the pipeline actually contributes.

    Ledger features alone are what a purely on-chain tool sees. Adding the graph
    stage, then the network layer, shows whether those stages earn their place or
    merely add columns. Reporting this is the difference between a claim and a
    measurement.
    """
    from .features import FEATURES as ALL

    order = ["volume", "value", "structure", "timing", "laundering", "network"]
    cumulative: list[str] = []
    out = []
    for name in order:
        cumulative += groups[name]
        idx = [ALL.index(f) for f in cumulative]
        ev = evaluate(X[:, idx], y)
        out.append((name, len(cumulative), ev))
    return out
