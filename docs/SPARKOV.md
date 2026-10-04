# External Sparkov experiment

Source: [Sparkov Data Generation](https://github.com/namebrandon/Sparkov_Data_Generation), MIT-licensed revision `b5eb45c89d36f2aa4ef16044a42945bed8b96d93`. This uses that external project's original behavioral profiles, not FraudGuard's internal compromise generator. Sparkov remains synthetic and rule-driven; it does not solve the absence of real-bank validation.

The completed run generated **42,925 transactions for 100 accounts**, January–July 2024, with a **2.227% fraud rate**. We ran sequentially with seed 42, Faker 13.12.0 and NumPy 2.3.3, froze the customer date to 2024-01-01, normalized transaction wall-clock timestamps to UTC and simulated a fixed two-day label delay. No labels or account IDs become model features. Personal-looking generated columns are discarded; account and merchant identifiers become deterministic tokens. This normalizer is for synthetic Sparkov exports, not a production de-identification service.

[Provenance](../artifacts/sparkov/provenance.json) · [Full evaluation](../artifacts/sparkov/report.json) · [Serving manifest](../artifacts/behavioral_service/manifest.json)

| Cohort | Rows | Fraud | AP | Recall | Precision |
|---|---:|---:|---:|---:|---:|
| Chronological, seen accounts | 8,213 | 133 | 0.5930 | 0.7669 | 0.2906 |
| Chronological, held-out accounts | 2,819 | 10 | 0.6384 | 0.8000 | 0.0952 |
| Held-out amount-only baseline | 2,819 | 10 | 0.1092 | 0.7000 | 0.0636 |
| Random-row diagnostic | 8,585 | 191 | 0.7435 | 0.8325 | 0.4025 |

Selection AP was 0.7284 for boosting and 0.3471 for logistic regression. A threshold chosen for an approximate 5% selection review rate was then frozen. Every raw event passed exact independent batch/stream feature parity. The 21 first-event cold-start rows contain no fraud; AP/precision/recall are therefore null, with `insufficient_data` status.

The held-out result has only **10** positives. The random split uses different cohorts and has access to future training rows, so its higher score is a diagnostic, not a controlled estimate of leakage. No claim is made that Sparkov's 2.2% prevalence matches real card traffic, or that one generation seed establishes uncertainty. The 11-seed summary elsewhere applies to the internal simulator, not this external dataset.

## Reproduce locally

In a separate data-generation environment, review and clone the source, then check out the exact revision above. Install `Faker==13.12.0` alongside the project's pinned numerical dependencies. From the FraudGuard root:

```bash
python scripts/generate_sparkov.py --source /path/to/Sparkov_Data_Generation --output data/sparkov-v1
python -m pip install '.[tracking]' -c requirements.lock
fraudguard behavioral-experiment --data data/sparkov-v1/normalized.csv --output artifacts/my-sparkov-run --tracking-uri sqlite:///mlflow.db --export-model
python -m fraudguard.reproduce --expected artifacts/sparkov/report.json --actual artifacts/my-sparkov-run/report.json
```

The output directory must be new. The generator writes personal-looking **synthetic** source files there; keep them local. Only compact reports and the trained model are committed. The original upstream NumPy pin targets older Python; this run uses the explicitly recorded NumPy version instead. Validate hashes on your platform before comparing metrics.

With `--tracking-uri`, the parent MLflow run starts before data preparation; candidate child runs surround fitting, record hyperparameters and selection AP, and save each fitted joblib model plus feature schema. Errors mark both failed candidate and parent runs failed. `--export-model` writes the selected bundle to a separate `model/` directory without activating or overwriting the original ULB model.
