> Historical full project guide. For current setup use the repository README. v2.2 was verified on EC2 from deployment logs; v2.3 AWS deployment is tracked separately.

# FraudGuard

**An end-to-end, production-oriented machine learning reference project for transaction fraud screening.** It includes an actual trained model and measured evaluation, not just a proposed architecture.

Start with [the visual evaluation report](../artifacts/benchmark/report.html), then follow [the project walkthrough](WALKTHROUGH.md). The code is deliberately small enough to understand and substantial enough to extend.


## Current release: v2.3

[Overview dashboard, transaction details, upload feedback, interactive system flow, onboarding and AWS update instructions](WORKSPACE_V23.md). Existing accounts, authenticator setup and scoring workers are retained.

## v2.2 access and scaling

[Authenticator login, account request limits, optional scoring load balancing and AWS update instructions](ACCESS_SCALING_V22.md). Compose now requires an authenticator encryption key; the checked deployment script generates it. The two-worker pool is optional and keeps the workspace coordinator as one process.

## v2.1 hardening

[Security, automatic backups, external alerts, deployment checks and AWS update instructions](HARDENING_V21.md). The current Compose file requires a distinct monitoring key; use the checked-deployment script for upgrades.

## v2 operations workspace

Open `/workspace` for named user accounts, a persistent review queue, delayed outcomes, drift/performance monitoring, operational alerts, model approval/activation/rollback and audit history. The public sample dashboard remains at `/`.

- [Update the existing AWS deployment](UPGRADE_V2_AWS.md)
- [Architecture, monitoring, release workflow and backups](ADVANCED_WORKSPACE.md)
- [Measured persistent-API load test](../artifacts/operations/load_test.json)

For Docker, the supplied Compose configuration enables durable workspace state and the watchdog. For a local Python run, set `STATE_DIR` to a writable folder before starting the API and create an account with `python -m fraudguard.admin --state-dir state create-user YOUR_NAME --role admin`. Without `STATE_DIR`, the original stateless API/demo still works and workspace endpoints report that persistence is disabled. There are no preinstalled accounts or default production passwords.

## What it does

Given the benchmark's 28 anonymized transaction components and an amount, FraudGuard estimates fraud probability and recommends **pass** or **manual review**. It does not automatically decline payments. `Time` controls evaluation windows and is not a model feature.

The included run processed **284,807 public transactions**, removed 1,081 exact duplicate rows, compared a class-weighted logistic model and four gradient boosting configurations, calibrated the selected model, and tested it on later transactions.

| Final temporal test result | Measured value |
|---|---:|
| Test transactions / frauds | 42,438 / 52 |
| Average precision | 0.6621 |
| ROC AUC | 0.9497 |
| Fraud recall | 76.92% (40 of 52) |
| Review precision | 43.96% (40 of 91) |
| Review rate | 0.2144% |
| False alerts / missed frauds | 51 / 12 |

The logistic model won on the earlier model-selection window. Complexity was not a selection criterion. The final test was not used to choose a model or threshold. These results are a limited benchmark, not evidence of readiness for live banking.

## Included capabilities

- Data provenance, pinned download checksum, strict validation, duplicate handling, and chronological splitting with boundary gaps.
- Five separate windows: fitting, model selection, calibration, policy selection, and final evaluation.
- Reusable feature pipeline, reproducible candidate search, probability calibration, cost-aware threshold selection, bootstrap uncertainty, amount slices, and global permutation importance.
- Versioned model bundle with integrity and library compatibility checks.
- FastAPI batch prediction, API-key authentication, input and body limits, bounded model concurrency, readiness, and Prometheus metrics.
- Batch scoring plus live version-specific PSI monitoring, delayed-label joins and operational alerts.
- Named accounts, expiring/revocable sessions, analyst ownership, review history and an append-only audit trail.
- Persistent model registry, automated quality gates, independent approval, activation and rollback.
- Docker image, restricted local Compose service, CI configuration, automated tests, HTTP load-test script, and deployment/rollback runbook.

## Quick start — Python 3.12

Run these commands from this project folder. Create a clean environment before installing the tested dependency set.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pip install --no-deps -e .
pytest -q
```

On Linux/macOS, use `python3.12 -m venv .venv` and `source .venv/bin/activate`, then the same installation commands. The local completed run was validated on Windows/Python 3.12; Linux container execution still needs verification.

Serve the included trained model:

```powershell
$env:API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:MODEL_DIR = "artifacts/benchmark"
uvicorn fraudguard.api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Keep the API key in that shell. In a second terminal, activate the environment and set the same `API_KEY` securely. Then:

```powershell
$headers = @{"X-API-Key" = $env:API_KEY}
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/predict -Method Post -Headers $headers -ContentType "application/json" -InFile artifacts/benchmark/example_request.json
```

Open [local interactive API documentation](http://127.0.0.1:8000/docs). Supply the `x-api-key` header when trying protected endpoints. The service requires a secret of at least 16 characters and refuses startup without it.

## Reproduce training

```powershell
fraudguard download --output data/creditcard.csv
fraudguard train --data data/creditcard.csv --output artifacts/my-run --seed 42
```

Each output directory must be new. The default policy allows at most 1% review rate **on the policy-validation window**, assumes cost 2 per review and 100 per missed fraud, and assumes reviewed frauds are caught. Override with `--max-review-rate`, `--review-cost`, and `--missed-cost`. These are illustrative cost units, not measured currency savings. Future review volume can exceed the validation limit.

The raw dataset is downloaded separately and is not included in the delivery. See [data provenance and limitations](DATA_AND_MODEL_CARD.md). Random seeds and pinned packages improve reproducibility; floating-point differences across platforms can remain.

## Score and monitor a new window

```powershell
fraudguard score --data data/new-window.csv --model-dir artifacts/benchmark --output artifacts/scored.csv
fraudguard monitor --data data/new-window.csv --model-dir artifacts/benchmark --output artifacts/drift.json
fraudguard evaluate --data data/labeled-window.csv --model-dir artifacts/benchmark --output artifacts/performance.json
python scripts/benchmark_api.py
```

`score` needs V1–V28 and Amount. `monitor` requires at least 1,000 rows. `evaluate` additionally needs mature `Class` labels with both classes present. Labels must be correctly joined by the upstream system; the CLI does not perform that join. Batch scoring returns row positions so input order is preserved.

## Run with Docker

On Windows, open Docker Desktop and wait for the Linux container engine to run. Then use the launch script from the project folder:

```powershell
.\scripts\Deploy-Docker.ps1
```

The script creates a private API key in `.env` on first use, preserves it on later runs, builds the image, waits for container health, and verifies a real prediction. It prints the local API documentation URL only after those checks succeed. Do not share `.env` or include it in project archives. If PowerShell blocks the script, use `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Deploy-Docker.ps1` to permit this invocation without changing your saved execution policy.

With the Docker engine running and `API_KEY` set:

```powershell
docker compose up --build -d
docker compose ps
```

The service binds to localhost, runs as a non-root user with a read-only filesystem, and limits resources. Container files and CI are included, but Docker execution could not be verified in the delivery environment because its engine was unavailable. Do not treat that path as tested.

## Project map

```text
src/fraudguard/       Data, training, policy, artifact, API, and monitoring modules
tests/               Contract, temporal, policy, artifact, API, and pipeline tests
scripts/             HTTP load testing
notebooks/           Optional guided exploration of the completed experiment
artifacts/benchmark/  Trained model, manifest, charts, measured results, sample request
docs/                Walkthrough, model card, architecture, deployment runbook
.github/workflows/    CI tests and container smoke test
```

The notebook is optional and requires Jupyter/IPython with the project environment selected as its kernel. The tested training and serving workflows use the CLI and do not depend on Jupyter.

## Production boundary

This is a working engineering reference, not a certified production fraud system. The anonymized V1–V28 inputs are already transformed; their original mapping is unavailable. A real deployment needs its own validated feature contract and model, mature fraud labels, longer out-of-time backtesting, identity-aware evaluation, business-approved review costs, queue integration, and security/load validation on the target infrastructure. See [the runbook](RUNBOOK.md) for concrete rollout steps and unresolved checks.

## Browser dashboard (v1.1)

Open the service root URL for the transaction-review application. It includes a public sample demo, authenticated CSV/JSON scoring, result inspection and export, and a benchmark report. See [dashboard guide and AWS update](DASHBOARD.md).

