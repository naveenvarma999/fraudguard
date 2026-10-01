# Data and model card

## Intended use

Educational and engineering reference for highly imbalanced classification and a fraud-review API. Suitable for experimenting with the supplied anonymized benchmark. Not validated for payment blocking, credit decisions, or production financial loss prevention.

## Data provenance

- Dataset: Credit Card Fraud Detection, collected in a Worldline/ULB research collaboration; transactions over two days in September 2013.
- Source description and download example: https://www.tensorflow.org/tutorials/structured_data/imbalanced_data
- Download: https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv
- Observed file SHA-256: `76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89`.
- Original shape: 284,807 rows; 492 fraud labels. After removal of 1,081 exact duplicate rows: 283,726 rows and 473 frauds.
- 486 additional rows are purged near temporal boundaries. See the generated report for the exact windows.
- Raw data is not redistributed with this project. Review the original provider's current terms before downloading or reusing it commercially. This project does not grant rights to the third-party data.

## Feature contract

Offline training requires numeric V1–V28, Amount, Time, Class. Online inference requires V1–V28, Amount, and an opaque transaction_id. The original PCA transform is unavailable; these are **not** raw banking features. API features must match schema `ulb-pca-v1`, not merely have the same names.

API limits: at most 100 transactions; V components in [-10,000, 10,000]; Amount in [0, 1e9]; finite numbers only; no extra fields; unique batch identifiers of 1–64 alphanumeric/underscore/hyphen characters. These generous bounds are defensive input limits, not a learned fraud rule. The API accepts integer JSON numbers as floating-point values but rejects numeric strings.

## Algorithm and selection

Weighted logistic regression (C=0.1) plus standardization is selected by tuning average precision against four histogram gradient boosting alternatives. Amount uses log1p. A disjoint sigmoid calibration window maps model scores to probabilities. Another window sets a cost-sensitive review threshold. The fitted training estimator is not refit on later windows.

All model comparison results, split sizes, hyperparameters, costs, metrics, and feature importances are in `artifacts/benchmark/evaluation.json`. The exact artifact threshold and package version are in `manifest.json`.

## Evaluation and limitations

- Test AP: 0.662129; ROC AUC: 0.949704; Brier: 0.000572.
- Review policy: 40 true positives, 51 false positives, 12 false negatives, 42,335 true negatives.
- Stratified bootstrap AP interval: about 0.536–0.812, conditional on prevalence and row independence.
- Two days of old data cannot establish resilience to seasonality, adversarial adaptation, or long-term drift.
- No entity identifiers: customer/card/merchant leakage and group generalization are untested.
- No protected attributes: demographic fairness cannot be evaluated.
- Labels are assumed known in historical order; real fraud labels often mature later. The 60-second gap does not solve delayed-label leakage.
- Anonymized features limit human explanations; permutation importance is global and non-causal.
- Threshold cost assumptions are illustrative; no realized currency savings are claimed.
- Third-party dependency pins reflect this tested environment. Security review, upgrades, and regression validation are required before an external deployment.

## References

- Scikit-learn on leakage and preprocessing pipelines: https://scikit-learn.org/stable/common_pitfalls.html
- Scikit-learn on disjoint calibration: https://scikit-learn.org/stable/modules/calibration.html
- FastAPI container deployment guidance: https://fastapi.tiangolo.com/deployment/docker/
