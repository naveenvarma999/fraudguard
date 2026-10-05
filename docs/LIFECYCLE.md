# Shared history and model lifecycle

## Ingestion

This is a **single-organisation application**. Event IDs and account history are shared across authorised service and named-user submissions. The submitter remains on prediction ownership and audit records; analysts still review their own submissions. An exact cross-user retry returns the original saved response. Conflicting payloads under the same ID fail.

One SQLite transaction reads history, calculates features, scores and saves the event, prediction, feature snapshot, drift counts and audit entry. Equal-time events are excluded from features. A late event receives HTTP **202** with `status: stored_late`: it is retained for subsequent history but has no prediction. This is not a pass decision; an upstream payment workflow must handle that unscored event. Earlier predictions and watermarks are not rewritten.

Migration retains v2.5 predictions and audit history, merges identical duplicate events across owners, and refuses ambiguous legacy IDs with different payloads. Resolve such conflicts against the source system before restarting. Existing review details remain accessible. Events follow received-time retention, default 90 days and minimum 30; ordering markers and delayed-label audit history remain. Idempotency is bounded by event retention.

## Retrain on arrived labels

Each scored event saves an enriched feature snapshot, even while the original champion remains active. Retraining uses those immutable inputs and only verified outcomes available at the snapshot cutoff. It requires 500 labels, with five positives and five negatives in each chronological fitting, calibration, policy and evaluation partition. Labels arriving after a partition boundary are excluded. Missing evidence fails explicitly; no outcomes are fabricated.

Run inside a trusted operator environment with a state snapshot or the protected live database:

```bash
python -m fraudguard.retrain --state-dir /data --output /new/candidate-run
python -m fraudguard.behavioral_lifecycle submit --state-dir /data --model /new/candidate-run/model --actor TRAINER_ADMIN
python -m fraudguard.behavioral_lifecycle shadow --state-dir /data --version CANDIDATE_VERSION --actor TRAINER_ADMIN
python -m fraudguard.behavioral_lifecycle evidence --state-dir /data --version CANDIDATE_VERSION
```

These CLI commands require trusted host access. The actor is an audit identity, not CLI authentication. HTTP promotion and shadow controls use authenticated, recent administrator sessions in **Model releases**. No endpoint accepts arbitrary uploaded joblib files.

The supplied research candidate is at `/app/artifacts/behavioral_candidate` in the Docker image. Registering it is optional; the default champion does not change on deployment.

## Shadow, approval and rollback

A shadow model scores the same arriving event and point-in-time history alongside the live model. Its score never changes the user-visible live prediction. Shadow failures are recorded and do not discard a successful live prediction. The implementation runs both models synchronously under the SQLite writer lock; enabling shadow increases latency.

Promotion requires at least 50 paired verified outcomes, including five fraud and five non-fraud cases; no shadow errors; candidate AP no more than 0.02 below live AP; and candidate Brier no more than 0.01 above live Brier. A different named administrator must approve. These explicit tolerances are operational gates, not statistical proof of equivalence. Selective labels can still bias both sides. Candidate evidence is bounded at 100,000 paired records; larger cohorts require a reviewed evaluation workflow.

```bash
python -m fraudguard.behavioral_lifecycle promote --state-dir /data --version CANDIDATE_VERSION --actor APPROVER_ADMIN
python -m fraudguard.behavioral_lifecycle rollback --state-dir /data --actor APPROVER_ADMIN
```

Activation and previous-version pointers survive restart. Backups contain registered model bytes and verify their hashes and active approval status. The original ULB registry remains independent.

The integration tests exercise retraining, shadow scoring, delayed labels, separate approval, restart and rollback using synthetic fixtures. **No shadow experiment on AWS production traffic is claimed yet.** Deploy the branch, register a candidate and collect real paired outcomes before reporting that evidence.

## Capacity and scaling

Measured locally on Windows/Python 3.12.14 with the bundled champion, 100 transactions per writer:

| Concurrent writers | Transactions | Transactions/second | p50 ms | p95 ms |
|---|---:|---:|---:|---:|
| 1 | 100 | 33.83 | 28.96 | 32.94 |
| 2 | 200 | 33.21 | 58.81 | 89.61 |
| 4 | 400 | 41.26 | 63.70 | 257.88 |
| 8 | 800 | 55.19 | 63.98 | 297.53 |

All 1,500 writes succeeded and were retained. These short runs show contention and a measured peak, not a sustained maximum or cloud capacity guarantee. Raw measurements: [load report](../artifacts/study_v26/load-report.json).

Run `python scripts/benchmark_behavioral.py --output /new/load-result` for a local transaction saturation curve. It exercises the actual SQLite ingestion path, with equal per-writer history workloads at concurrency 1, 2, 4 and 8. It does not include HTTP, TLS, auth quotas or shadow inference; the measured peak is not a universal capacity limit. Public API quotas remain in force. Published measurements are local Windows results, not an EC2 service-level claim.

Beyond one writer, move event/label storage to Postgres, serialize by organisation/account using advisory locks or ordered partitions, use a unique organisation/event key for deduplication, and record prediction plus outbox atomically. Cross-account merchant-label aggregates need a versioned availability-time view so partitioning does not introduce future labels. Compare replay and online features under reordered delivery before cutting over. Do not share SQLite over network storage or merely add API replicas.
