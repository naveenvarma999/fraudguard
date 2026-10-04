# FraudGuard

**Fraud screening and human review, with a new research track for point-in-time account behavior and verified online/offline feature parity.**

[Public sample demo](https://3.9.213.56/) · [API documentation](https://3.9.213.56/docs) · [Behavioral experiment](docs/BEHAVIORAL_RESEARCH.md) · [Operations](docs/OPERATIONS.md)

[![Validate](https://github.com/naveenvarma999/fraudguard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/naveenvarma999/fraudguard/actions/workflows/ci.yml)
[![Dependency security](https://github.com/naveenvarma999/fraudguard/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/naveenvarma999/fraudguard/actions/workflows/security.yml)

The public demo is read-only sample scoring; accounts are operator-created. It was reachable on 2026-10-04. This is a single EC2 host, so availability and the IP can change. The new behavioral research code is **not deployed** there.

## Measured benchmark results

The serving model uses the ULB credit-card benchmark: chronological fit, selection, calibration, policy and test windows, with boundary gaps and duplicate removal. Logistic regression won on selection data; a separate calibration window fits sigmoid calibration.

| Final temporal test | Result |
|---|---:|
| Transactions / fraud cases | 42,438 / 52 |
| Average precision | 0.6621 |
| ROC AUC | 0.9497 |
| Fraud recall | 76.92% |
| Review precision | 43.96% |
| Review rate | 0.2144% |
| Detected / missed fraud cases | 40 / 12 |
| False alerts | 51 |

[Model card](docs/DATA_AND_MODEL_CARD.md) · [Evaluation evidence](artifacts/benchmark/evaluation.json). Two historical days do not establish live banking performance. Inputs are `Amount` and anonymized `V1`–`V28`, not bank statements or card numbers.

## What makes the new ML work different

- Raw synthetic account/merchant/timestamp/amount/location events, with nine behavioral features using only prior transactions.
- Independent batch and streaming implementations, with exact replay parity, boundary/tie tests and explicit late-event/retry behavior.
- Account holdout plus chronological evaluation and delayed-label cutoffs; separate amount-only, random-split and cold-start diagnostics.
- Reproducible evidence, optional MLflow tracking and five short [engineering decisions](docs/DECISIONS.md).

The [reference report](artifacts/behavioral/report.json) is a synthetic experiment, not a real-bank accuracy claim. The streaming implementation is in-memory research code; it is not Kafka/Redis-backed or connected to the production endpoint.

## Run and reproduce

Use Python 3.12 in a virtual environment:

```bash
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pip install --no-deps -e .
pytest -q
node --test tests/dashboard.test.mjs
fraudguard behavioral-experiment --output artifacts/my-behavioral-run --seed 42
```

For the original benchmark:

```bash
fraudguard download --output data/creditcard.csv
fraudguard train --data data/creditcard.csv --output artifacts/my-run --seed 42
```

Data rights remain with their providers. Load only trusted serialized model bundles. For Docker setup, accounts, AWS updates, backups and recovery, use the single [operations guide](docs/OPERATIONS.md). The [Terraform reference](infra/README.md) is not applied to the existing instance.

## Application and operations extras

The application includes authenticated CSV/JSON scoring, analyst reviews and audit history, TOTP login, request/concurrency limits, optional scoring workers, delayed-label monitoring, approved model activation/rollback, backups and configurable alerts. These support the ML workflow; they do not provide multi-host availability, autoscaling or a banking SLA.

![Sample review workspace](docs/screenshots/overview.png)

[Project walkthrough](docs/WALKTHROUGH.md) · [Verification and deployment limits](docs/VERIFICATION.md)

## Repository history

The first public commit imported an already-developed local v2.3 snapshot. Earlier public feature-by-feature commits do not exist; the versioned guides described local iterations. This upgrade is recorded in genuine new commits, without reconstructing or backdating history. The old versioned guide paths now point to the consolidated operations guide.
