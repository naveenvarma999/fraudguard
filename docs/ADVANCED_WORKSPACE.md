# Advanced workspace (v2.0)

FraudGuard v2 adds a persistent, single-replica operations workspace at `/workspace` to the existing benchmark demo at `/`. It is designed to fit the current small EC2 deployment without requiring another paid cloud service.

## 1. Accounts and review history

- Individual `admin` and `analyst` accounts; scrypt password hashes with individual salts.
- Opaque, hashed-at-rest bearer sessions expire after eight hours. Logout, password change/reset and account disable revoke sessions. Browser tokens remain in memory only.
- Login attempts are limited per account and direct client address. Behind the current Caddy setup, clients may share the proxy-address bucket. Do not trust arbitrary forwarded headers to bypass throttling.
- Analysts score and review their own submissions. Administrators can inspect all records, manage users and control releases. The existing API key remains available to service clients, but it cannot approve releases or manage users.
- Each prediction gets a separate `prediction_id`. Delayed labels and review updates attach to that ID, so repeated transaction IDs across batches cannot accidentally overwrite prior predictions.
- Review dispositions and verified outcomes are separate. A review recommendation never silently becomes training ground truth.
- Audit events include actor, timestamp, action, resource and relevant changes. Updates and deletes are blocked by database triggers. This is application-level append-only history; it is not external, tamper-proof archival against a host administrator.

## 2. Drift and delayed-label monitoring

The watchdog checks the API every minute. It runs independently of the API process and writes alert transitions to the persistent database and its structured stdout log.

- Feature drift: per-model, per-hour histograms of the 29 input features; rolling seven-day PSI against the training reference. At least 1,000 predictions are required. PSI >= 0.2 prompts investigation; this is a heuristic, not proof of deteriorating accuracy.
- Delayed outcomes: labels are joined by prediction ID and evaluated within each model's prediction-time cohort. Metrics require at least 50 labels, five frauds and five non-frauds. The UI shows coverage, precision, recall, AP, ROC AUC, confusion counts and label delay where supported by the endpoint.
- Only the latest corrected label enters current metrics; the correction and previous value remain in the audit trail. Selective labels and incomplete maturation can bias every reported metric.
- Service health: prediction p95, 5xx rate, process resident memory, process CPU time, Linux container memory pressure and workspace disk space. Prediction latency includes request validation, authentication, inference and persistence.
- Alerts: API unavailable; 5xx >2% or p95 >500 ms with >=20 requests in five minutes; container memory >85%; disk free <10%; PSI >=0.2; labeled-cohort recall <60%; monitoring cohort >100,000 rows.
- Conditions must hold for two consecutive checks. Resolved conditions produce a resolution event. Alert entries expose their last-check time; stale monitoring is displayed separately. In-memory HTTP statistics reset when the API restarts.

Alerts are visible in the workspace and `docker compose logs monitor`. No email, Slack or other external notification destination is configured. A host outage also stops its watchdog: out-of-host monitoring is still needed for an availability SLA.

The endpoint `/ops/monitoring?days=7` supports 1–30 days and caps quality evaluation at 100,000 predictions. The watchdog uses seven days. Only administrators and the service API key can query global monitoring.

## 3. Model registry, automation and rollback

Model bundles are copied into the persistent volume and checksummed across the model, manifest and evaluation report. Runtime schema/version checks and a real prediction smoke test run before staging. Automated numerical gates require >=1,000 test rows, >=30 frauds, AP >=0.60, recall >=0.65, precision >=0.30 and review rate <=1%, with matching dataset, champion and threshold metadata.

These are explicit project demonstration thresholds, not business-approved banking requirements. Evaluation metadata is trusted output from the training pipeline, bound to the copied bundle by its digest. The staging command does not recompute a whole test dataset. Do not repeatedly tune against the same final test using promotion results; use a new locked temporal evaluation cohort for real model development.

Lifecycle: **train → evaluate → stage → independent approval → activate → observe → rollback if needed**.

- `scripts/release_pipeline.py` automates local/offline training, evaluation and staging; failed gates stop the pipeline.
- `.github/workflows/model-candidate.yml` automates candidate training and quality checks on manual workflow dispatch and retains the resulting bundle as an artifact. It does not automatically publish to AWS. This workflow is supplied but has not been run in your GitHub account.
- A named active administrator submits a trusted bundle. A different administrator must approve it. Files are rechecked at approval and activation. The previously deployed model is registered as the initial rollback baseline.
- Activation swaps the model, policy, version and public demo together under the inference lock. The active version is persisted and restored on restart. Each batch and stored prediction retains its scoring version.
- A corrupt active artifact disables readiness; it does not silently fall back. An administrator can restore the previously active intact release using rollback.
- Joblib/pickle artifacts execute code when loaded. Only stage artifacts created by your trusted training pipeline; there is no arbitrary browser model-file upload.

To stage a trusted candidate already copied into the container's writable volume:

```bash
sudo docker compose exec api python -m fraudguard.admin stage --bundle /data/incoming/candidate --submitted-by naveen
```

Then a **different administrator** signs in at `/workspace`, opens **Model releases**, approves it, and selects **Activate release**. Use **Roll back to previous** to restore the prior version. These operations also have authenticated `/ops/releases/...` and `/ops/rollback` API routes.

For training on a separate development/staging environment:

```bash
python scripts/release_pipeline.py --data data/creditcard.csv --output artifacts/new-candidate --state-dir state --submitted-by training-admin
```

Create the submitting account in that environment first. Use a new output directory each run. Avoid training inside the small live API container while it is serving traffic.

## 4. Load tests and resource evidence

Run the benchmark against an isolated staging workspace, because requests generate real saved predictions and affect drift counts:

```bash
python scripts/benchmark_api.py --url http://127.0.0.1:8000 --requests 200 --concurrency 4 --output artifacts/operations/load_test.json
```

Set `API_KEY` in the environment first. The script measures p50/p95/p99, status counts, throughput, errors, process CPU delta and memory snapshots. It fails if p95 exceeds 500 ms or errors exceed 1% (both configurable). CI runs a persistent-container load gate after unit tests and image smoke checks.

The supplied measurement was made on Windows loopback with the real benchmark model and SQLite writes: **200 requests × 8 transactions, four concurrent clients, zero errors, p95 174.94 ms, 37.10 requests/second**. API resident memory after the run was approximately 163 MiB; CPU time increased by 3.08 seconds. These are local observations, not EC2 measurements or an SLA. The repeated sample batch is intentionally artificial load and should not be interpreted as genuine transaction drift.

## Persistence, backups and limits

The Compose `workspace_data` volume stores the SQLite WAL database, copied releases and watchdog heartbeat at `/data`. Both processes use UID 10001. The API has a 1 GiB limit; the monitor has 256 MiB. Existing Caddy settings and certificate volumes are left alone. Run one API worker and one replica; this design is not a distributed model registry or database cluster.

Scores, IDs, amounts, ownership and aggregate feature histograms are retained for 90 days by the watchdog's daily retention pass. Raw V1–V28 vectors are not retained. Review/label audit details persist in the append-only audit table. Set an organizational archival policy and protect the volume/backups appropriately. Backups contain sensitive review metadata, password hashes and session hashes.

Make a transaction-consistent database backup:

```bash
sudo docker compose exec api python -m fraudguard.admin backup --output /data/backups/workspace-20260930.sqlite3
```

Use a new filename each time. Copy the database backup **and `/data/releases`** to protected backup storage. Restore into the same `/data` layout while API and monitor are stopped, with UID 10001 ownership. The registry uses paths rooted there. Backing up a live SQLite file with ordinary file copy is not a replacement for the backup command. `docker compose down` retains the volume; **do not use `down -v` unless you intend to erase workspace data**.

Authentication has no MFA, SSO or self-service recovery. An operator can reset a password with `python -m fraudguard.admin reset-password USER` inside the API container. For a larger deployment, move state to a managed database, use an external identity provider, add independent monitoring/notifications and store immutable releases/audit archives off-host.

Implementation reference: [Python SQLite transactions and backup API](https://docs.python.org/3/library/sqlite3.html). Existing `/metrics` remains available for a future Prometheus integration; [Prometheus alerting documentation](https://prometheus.io/docs/alerting/latest/overview/) describes routing external notifications.
