# Behavioral ML evidence — v2.6

The larger experiment generated **1,200 customer profiles, 1,197 active accounts and 523,605 transactions** from the pinned external Sparkov generator. The final unseen-account cohort contains **282 fraud cases in 20,732 events**, compared with ten in the older experiment. This is synthetic evidence, not bank validation.

## Protocol

Account hashes reserve 20% of identities from fitting, selection, calibration, policy selection and merchant-label encoding. Chronological boundaries are 60%, 70%, 80% and 85% of the observation interval. Each training-side partition uses only labels available before its closing boundary. Test outcomes are used only for reporting.

Feature history includes earlier unlabeled transactions from unseen accounts, matching an established but previously unseen customer. Independent batch/replay parity checks cover the complete histories of ten preselected accounts; the remaining feature rows use replay. Persisted endpoint tests separately verify cross-user history, equal-time exclusion and late arrivals.

The new features are cyclic UTC time of day, a fixed merchant-category vocabulary, home-to-merchant distance and a missing-home indicator. Merchant fraud rate uses a fixed Beta prior with mean 1% and strength 20: `(arrived fraud labels + 0.2) / (arrived labels + 20)`. Only labels arriving **strictly before** the event are eligible. All held-out account labels are excluded from the research encoding. Corrections in the application replace the old label only from the correction's availability time onward.

Home coordinates and category are upstream inputs, not independently verified customer identity. All application users belong to one trusted organisation. Unknown context requires manual review when the enriched model is active.

## Ablation result

Every row uses the same GBM configuration and data partitions. These are threshold-independent AP values; no feature group was hidden because it performed poorly. Exact values are in [the report](../artifacts/study_v26/report.json).

| Feature group | Selection AP | Seen-account test AP | Unseen-account test AP |
|---|---:|---:|---:|
| Original nine features | 0.71 | 0.68 | 0.66 |
| Add UTC time | 0.84 | 0.83 | 0.80 |
| Add category | 0.34 | 0.32 | 0.32 |
| Replace previous-merchant distance with home distance | 0.34 | 0.32 | 0.32 |
| Add arrived-label merchant statistics | 0.86 | 0.59 | 0.55 |

**More features did not consistently improve generalisation.** Time of day was useful. The full model won its candidate selection comparison but deteriorated on later data; the test result does not justify automatic promotion. The exported full-feature model is a **shadow candidate**, while the existing bundled model remains the default champion. Selecting a new winner after seeing these tests would require a fresh evaluation period; the time-only result is a direction for follow-up, not a retrospectively unbiased production choice.

## Calibration and uncertainty

Sigmoid calibration uses an independent chronological partition after fitting and selection. A later policy partition sets the review threshold. On unseen accounts, full-model AP is **0.5501**, recall **80.85%**, review rate **5.31%**, and Brier score improves from **0.03372 to 0.01019** after calibration. Calibration improves probability error here; it does not repair the ranking/generalisation gap.

The 95% account-cluster bootstrap interval for unseen AP is **0.4263–0.6527**, and recall is **0.7477–0.8757**. All 200 predeclared resamples were valid. Resampling accounts preserves within-account dependence; intervals are conditional on this fitted model and omit retraining and generator uncertainty.

Unseen fraud prevalence is 1.36%, so AP/base-rate lift is **40.44**. Lift is a useful comparison but does not remove all prevalence effects. The separately reported threshold targeting 5% policy false positives gives **86.52% test recall at 5.78% actual test FPR**; the target is not misrepresented as the achieved FPR. Cold-start manual review is a separate operational override, and these research metrics evaluate the model threshold only.

## Reproduce

Use the pinned Sparkov checkout described in [provenance](../artifacts/study_v26/provenance.json), Python 3.12, the project dependencies and Faker 13.12.0:

```bash
python scripts/generate_sparkov.py --source /path/to/Sparkov_Data_Generation --output /new/sparkov-1200 --accounts 1200 --enriched
python -m fraudguard.ml_study --data /new/sparkov-1200/normalized.csv --output /new/study
```

Outputs must be new directories. The dataset digest normalises text line endings to LF, so Windows and Linux exports compare consistently. Model weights, manifest, report and provenance are committed; generated raw customer fields and full local datasets are not published. The original simulator reproduction gate remains an independent CI regression test.
