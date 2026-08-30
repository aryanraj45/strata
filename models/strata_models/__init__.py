"""Detection models for STRATA: supervised risk, unsupervised anomaly, SHAP."""

from .features import FEATURES, GROUPS, extract, group_of
from .detect import (
    Evaluation,
    evaluate,
    explain,
    fit_supervised,
    fit_unsupervised,
    top_factors,
)

__all__ = [
    "FEATURES", "GROUPS", "extract", "group_of",
    "Evaluation", "evaluate", "explain", "fit_supervised", "fit_unsupervised",
    "top_factors",
]
