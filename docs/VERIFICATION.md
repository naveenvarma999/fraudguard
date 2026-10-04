# Verification — v2.4 review fixes, 2026-10-04

## Completed locally

- **111 Python tests passed**, with no skips; 16 upstream deprecation warnings remain. Coverage includes proxy/client isolation, targeted login attacks, spoofed forwarding headers, short backoff with a controlled clock, feature/score parity, invalid behavioral history, corrupted bundles, busy-capacity quota preservation, null metrics, reproduction mismatch rejection, and MLflow candidate/failed-run recording.
- **7 JavaScript tests passed**, Ruff and Git whitespace checks passed.
- A fresh seed-42 experiment passed the **committed report reproduction assertion**. It checks dataset hash, feature contract, splits, selection, threshold, statuses and metrics; floating-point tolerance is declared in `reproduce.py`.
- **11 predeclared seeds** completed, with all reports and a mean/range/standard-deviation summary committed. Held-out AP is 0.7798 mean, 0.6598–0.9240 range. No seed was dropped because of its score.
- **42,925 external Sparkov transactions** were generated using the pinned upstream revision, normalized and used for actual training. Both candidate models were fitted, and every event passed independent batch/stream feature parity. The selected model, report and provenance are committed. See [external-data results](SPARKOV.md).
- MLflow created a parent training run and two completed candidate child runs with model artifacts and feature schemas. [Tracking evidence](../artifacts/sparkov/tracking-evidence.json) records their IDs/statuses/metrics. Tests additionally verify failed training marks parent and child failed.
- A local API smoke call loaded the committed Sparkov bundle and returned HTTP 200 from `/v1/behavioral/predict`, application **2.4.0**, model `behavioral-6d94fa3d42458af3`. This used the in-process ASGI client, not Docker or AWS.
- Runtime dependency audit with pip-audit 2.10.1: **no known vulnerabilities found**. Data-generation and optional tracking environments are separate from that runtime audit.
- Compose configuration validated with both proxy and worker overlays. Deployment/Compose/bootstrap Bash syntax and Terraform formatting passed.

The first full suite run exposed a wall-clock-dependent assertion in an old login test after introducing a one-second backoff. The test now controls the clock, and the complete suite was rerun successfully. Production delays were not extended merely to make that test pass.

## Live deployment check

On 2026-10-04, HTTPS requests to the public readiness and OpenAPI endpoints succeeded with certificate verification enabled using standard certifi roots. The live application reported **2.3.0**, and readiness returned `ready`. No certificate checks were bypassed. This verifies reachability from this environment, not every reviewer's network.

**The v2.4 changes are local, not deployed to AWS.** The existing server was not modified. Before rollout, review the trusted-proxy migration in [operations](OPERATIONS.md); the login fix requires correct deployment configuration as well as updated code.

## Infrastructure and publishing limits

The EC2 configuration now includes Elastic IP, an S3 backend with locking and a pinned-commit bootstrap. Terraform provider initialization still fails under this machine's Windows access controls. **No local provider-schema validation, Terraform plan/apply, remote-state migration or cloud-init execution is claimed.** CI performs provider validation without applying resources. AWS deployment requires reviewed inputs, existing state-bucket permissions, a plan and explicit operator execution; do not create a duplicate server unintentionally.

Docker Desktop's engine is unavailable, so a new container build/start was not run locally. CI retains its container smoke gate and now calls the behavioral endpoint. GitHub CLI configuration access is denied in this session, so these commits require an owner push of `codex/review-hardening`; no new CI run or PR is claimed yet.

## Remaining model limitations

Both data sources are synthetic. The internal generator deliberately links behavior and fraud, and its high prevalence limits review-budget recall. Sparkov uses independent profiles but remains rule-generated; only 10 held-out test events are fraud. The 11-seed uncertainty summary applies to the internal simulator, not multiple Sparkov generations. Real-world calibration, dataset shift, durable event ingestion/history completeness, full cloud recovery and multi-host availability remain unproven.
