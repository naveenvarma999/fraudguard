"""Shared transformations ensure offline and online feature parity."""

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from fraudguard.data import FEATURES


class TransactionFeatures(TransformerMixin, BaseEstimator):
    def fit(self, X, y=None):
        self.n_features_in_ = len(FEATURES)
        return self

    def transform(self, X):
        result = X.loc[:, FEATURES].to_numpy(dtype=float, copy=True)
        result[:, -1] = np.log1p(result[:, -1])
        return result

    def get_feature_names_out(self, input_features=None):
        return np.array(FEATURES[:-1] + ["log1p_Amount"])


def candidates(seed=42):
    yield (
        "logistic_balanced",
        make_pipeline(
            TransactionFeatures(),
            StandardScaler(),
            LogisticRegression(C=0.1, class_weight="balanced", max_iter=600, random_state=seed),
        ),
    )
    for leaves in (15, 31):
        for weight in (None, "balanced"):
            name = f"histboost_leaves{leaves}_{weight or 'natural'}"
            yield (
                name,
                make_pipeline(
                    TransactionFeatures(),
                    HistGradientBoostingClassifier(
                        max_iter=180,
                        learning_rate=0.06,
                        max_leaf_nodes=leaves,
                        min_samples_leaf=30,
                        l2_regularization=3.0,
                        class_weight=weight,
                        early_stopping=False,
                        random_state=seed,
                    ),
                ),
            )
