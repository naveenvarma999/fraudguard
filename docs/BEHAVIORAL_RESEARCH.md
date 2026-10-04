# Behavioral fraud experiment

This document describes the internal synthetic timing experiment. v2.4 also includes [external Sparkov training](SPARKOV.md) and a separate [behavioral API](BEHAVIORAL_API.md). The original `/v1/predict` route continues to use ULB features.

## Reproduce

With the development environment from the README:

```bash
fraudguard behavioral-experiment --output artifacts/my-behavioral-run --seed 42
```

By default the command creates a synthetic CSV and `report.json`. Optional `--export-model` writes an isolated serving bundle; it does not activate it or write to the application database. Defaults: 100 opaque accounts, 60 days, seed 42. The committed [reference report](../artifacts/behavioral/report.json) records the dataset digest, runtime versions, model selection, split sizes and results. Regenerate with the pinned dependencies; different numerical library/platform versions may introduce small score differences.

## What the experiment proves

The generator creates account IDs, merchant IDs, UTC timestamps, amounts and locations. Latent compromise episodes generate noisy fraud labels; legitimate travel and purchase bursts overlap those signals. It is an original **synthetic simulation**, not PaySim, Sparkov, IEEE-CIS or real customer data. It deliberately makes behavior informative. Strong scores validate the exercise, not banking usefulness.

Nine features describe amount, 10-minute/hour transaction counts, prior 30-day mean and history count, amount relative to that mean, merchant novelty, distance from the previous transaction and cold start. Window semantics are `[t-window, t)`: current and equal-time transactions are excluded. Events use `(timestamp,event_id)` ordering. No account or merchant identifier is supplied as a model feature.

`offline_features` independently selects historical rows with dataframe timestamp masks. `OnlineFeatures` incrementally maintains per-account history. The experiment asserts **exact equality for every feature on every transaction**, not just similar predictions. Tests cover window boundaries, tied timestamps, future-data isolation, retries, conflicting IDs, late events and recovery by replay.

The streaming processor is a single-owner in-memory research reference. The v2.5 HTTP path reconstructs prior history from SQLite, then atomically persists the new event and prediction. HTTP parity, concurrency, rollback and recovery tests cover that stored path. The batch oracle remains independent and quadratic within each account. See [the API contract](BEHAVIORAL_API.md) for ordering, retention and capacity limits.

## Evaluation contract

1. A stable account hash holds out about 20% of accounts from all model fitting and selection.
2. Chronological 60%/20%/20% windows separate fitting, selection and testing. Labels arrive 1–3 days later; fitting and selection each exclude labels unavailable at their cutoff.
3. Logistic regression and histogram boosting compete on **selection average precision**. Test results cannot choose the model. The selection 95th-percentile score defines an approximate 5% review target; ties or distribution changes can change the realized rate.
4. Test metrics separate previously seen accounts, accounts excluded from development but with available transaction history, and the first event for held-out accounts with history reset. The last cohort is genuinely cold and small; inspect its fraud count before drawing conclusions.
5. An amount-only baseline uses the same chronological/entity masks. A random-row diagnostic uses the chosen model family, with separate random fit/selection/test rows. It violates chronology and is **not** an unbiased estimate or an apples-to-apples causal estimate of leakage; training size and cohorts differ. Its result need not be higher.

Scores are uncalibrated research scores. Eventual test labels are used for retrospective evaluation. Behavioral context strings describe observed inputs; they are not SHAP values or proof that those inputs caused a prediction. No automated shadow rollout or retraining is claimed.

## Optional MLflow evidence

Tracking is separate from the production image:

```bash
python -m pip install '.[tracking]' -c requirements.lock
python -m fraudguard.experiment_tracking --report artifacts/my-behavioral-run/report.json --tracking-uri sqlite:///mlflow.db
```

This logs parameters, dataset digest, cohort metrics and the report artifact. It marks failed logging runs as failed. The integration test reads the resulting run and artifact from a real local SQLite MLflow store. The optional client also accepts an operator-configured remote tracking URI; only use a trusted server because the report is uploaded there. Local database/artifact files are ignored by Git. Model registry activation remains in the existing reviewed release workflow.

To inspect with a web UI, use a separately managed MLflow server connected to this backend; the skinny client alone is not a complete UI installation.

## Reproduction gate and uncertainty

CI now compares a regenerated seed-42 report with the committed reference through `python -m fraudguard.reproduce`. Dataset hash, split sizes, feature list, model selection, threshold, statuses and metrics are checked. Floats allow relative tolerance `1e-7` and absolute tolerance `1e-9` for cross-platform numerical noise; runtime patch-version strings are not required to match. A failing comparison requires investigation and an explicitly reviewed reference update, never silent regeneration of the expected result.

Run `python -m fraudguard.multiseed --output artifacts/all-seeds` for the predeclared 11 seeds `[42,0,1,2,3,4,5,6,7,8,9]`. All individual reports are committed alongside the summary. Held-out AP is **0.7798 mean, 0.0919 standard deviation, 0.6598–0.9240 range**. Boosting wins selection in 9/11 runs; its mean selection advantage is 0.0160 AP, ranging from -0.0080 to 0.0423. This is variability across generated datasets/training seeds, not a bank-performance confidence interval.

Only 6/11 first-event cohorts contain both classes. The summary states the valid count; other runs use null metrics and `insufficient_data`. The internal generator's high fraud prevalence and informative rules make it a timing-correctness exercise. Split gaps can be absent or reverse, and the 5% review target limits attainable recall when fraud prevalence is around 10%.

For actual fit-time MLflow tracking, pass `--tracking-uri sqlite:///mlflow.db` to `fraudguard behavioral-experiment`. Parent/candidate runs now start before fitting and record trained candidate artifacts and failures. The older `experiment_tracking --report` command remains only an explicit report-import utility.
