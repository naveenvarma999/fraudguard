# Project walkthrough

## 1. Translate the problem into a decision

The product is a screening service used before an analyst reviews suspicious transactions. Its useful output is a risk probability plus a review recommendation. False alerts consume analyst time; missed fraud has a different cost. Accuracy alone is unsuitable because fraud is rare. The project measures average precision, recall, precision, review volume, calibration, and an illustrative decision cost.

## 2. Establish a data contract

The public ULB/Worldline benchmark contains `Time`, `Amount`, `Class`, and V1–V28. The latter are anonymized PCA components. `Class=1` indicates fraud. `data.py` checks required numeric values, finite inputs, non-negative amounts/time, and binary labels. Exact duplicates are removed globally before splitting; identical full transaction features with conflicting labels cause failure.

This deduplication cannot detect repeated customers, cards, merchants, or events with changed timestamps. Those identifiers are absent. The original PCA fit process is outside this project's control, so upstream leakage cannot be ruled out.

## 3. Make future prediction the evaluation target

Sorted transactions are partitioned approximately 50/15/10/10/15% by row position. Identical timestamps never straddle a boundary, and a 60-second gap is removed from the beginning of each later window. It is a simple boundary gap, not a validated fraud-label maturation period.

| Window | Permitted use |
|---|---|
| Train | Fit preprocessing and candidate estimators |
| Tune | Select the highest average-precision candidate |
| Calibrate | Fit a sigmoid calibration map on the frozen winner |
| Policy | Choose a cost-sensitive review threshold; descriptive importance |
| Test | One final performance report, without fitting or selection |

No SMOTE or negative downsampling is used. Class weighting is compared to natural-frequency fitting. This preserves the original prevalence in validation and test. Standard scaling is learned only inside the logistic training pipeline. Amount is transformed with `log1p`; absolute time is excluded to avoid relying on a two-day clock artifact.

## 4. Compare a meaningful baseline with nonlinear alternatives

The search has five candidates: weighted logistic regression and four histogram gradient boosting combinations (15/31 leaves × natural/balanced weights). The seed, parameters, tuning score, and timing are saved in `evaluation.json`.

The selected logistic model achieved tuning AP 0.8033. The best tree candidate scored 0.8024. A narrow difference is not evidence that linear models universally outperform boosted trees. This run simply follows the predeclared selection rule. A much broader search on this small dataset could overfit the tuning window.

## 5. Calibrate probability estimates

Class-weighted models do not automatically output probabilities appropriate to the natural fraud prevalence. A `FrozenEstimator` prevents refitting the winner, while `CalibratedClassifierCV(method='sigmoid')` learns a map on a separate chronological window. Only 30 frauds are available for calibration, so probability estimates remain uncertain.

The report includes a reliability plot, Brier score, and uncalibrated Brier comparison. Brier score reflects discrimination as well as calibration, and sparse reliability bins should not be overinterpreted.

## 6. Convert probabilities into a policy

`select_policy` minimizes `2 × number reviewed + 100 × frauds missed` subject to at most 1% reviewed on the policy window. It evaluates all distinct score thresholds, handles ties exactly, and permits a no-review option. Every reviewed transaction is charged the review cost. The assumptions include perfect recovery of reviewed fraud and flat missed-fraud cost; neither is demonstrated by this dataset.

The service applies that fixed threshold to future transactions. It does not enforce a global review queue or a rolling rate cap. A deployment should manage queue capacity separately and never silently drop reviews when traffic changes.

## 7. Assess final performance and uncertainty

The final window contains 42,438 transactions and 52 frauds. The policy finds 40 frauds, misses 12, and flags 51 legitimate transactions. AP is 0.6621, lower than the tuning score. That change is visible in the report rather than hidden by a random split.

The AP interval uses 200 stratified row bootstrap resamples. It conditions on observed prevalence and assumes independent rows. It is not a confidence interval for future months. Amount-based slices use a training-derived cutoff. The dataset has no demographic attributes, so no demographic fairness claim is possible.

Global permutation importance is computed on the policy window, not used for further model selection. PCA component importance does not provide a meaningful customer-facing reason code or causal explanation.

## 8. Package the same computation for serving

The full feature pipeline and calibrated estimator are saved together. The manifest records data and model hashes, schema, package version, threshold, seed, and reference distributions. The API uses this same object for prediction; a parity test verifies that online and offline probabilities agree.

Model releases are immutable directories. Integrity checks detect accidental file changes; they do not authenticate a maliciously replaced manifest. Joblib models are executable pickle-based artifacts. Load only trusted releases from controlled storage.

## 9. Monitor inputs, service health, and delayed outcomes

The API exposes request status counts, risk score histograms, decision counts, and inference latency. Feature values and request identifiers are not logged. PSI is computed offline against training quantile bins and reports potential shifts. A 0.2 threshold is a heuristic that requires operational calibration.

Distribution drift does not prove accuracy degradation. Collect mature outcomes and evaluate precision/recall, review capacity, and segment performance. Feedback from only reviewed transactions is selection-biased; design an independent outcome collection strategy.

## 10. Release with evidence

Tests cover contracts, split integrity, tied policy scores, offline/online parity, key enforcement, bad inputs, missing/tampered artifacts, drift, and a small complete training run. The local HTTP test measures actual loopback requests. Docker/CI definitions provide a reproducible deployment path but were not executed successfully here because the Docker engine was unavailable.

For a real rollout: validate months of point-in-time data, set business gates before testing, run in shadow mode, canary behind an authenticated gateway, inspect outcomes, and preserve the previous image/model for rollback. Infrastructure-specific integration remains deployment work, not a property of the benchmark score.
