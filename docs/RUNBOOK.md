# Deployment and operation runbook

## Verified scope

This delivery trains on the actual public benchmark and provides local automated checks and HTTP measurements. Check `artifacts/benchmark/verification.json` for the completed verification record. Docker execution and hosted CI were not verified locally because no Docker engine was available. No cloud service was provisioned or published.

## Local startup and readiness

1. Install the pinned environment and editable package as described in README.
2. Use a cryptographically random API key, at least 16 characters; supply it through environment or a secret manager. Never commit it.
3. Set MODEL_DIR to a trusted release, then start one Uvicorn worker.
4. `/health/live` answers whether the service is alive. `/health/ready` returns 503 when the model is missing, invalid, or incompatible. Route traffic only after readiness succeeds.
5. Send `example_request.json` to `/v1/predict` with `X-API-Key`. Compare its model version with the intended release.

## Production rollout sequence

1. Build a real point-in-time feature pipeline. The public PCA vectors cannot be replaced with arbitrary raw transactions. Version feature definitions and validate training/serving equivalence.
2. Obtain representative data covering multiple months, mature labels, and relevant entities. Enforce event-time and label-availability cutoffs. Use rolling temporal backtests and entity-aware evaluation where available.
3. Predeclare acceptable recall, precision, review capacity, cost, segment behavior, and service latency/error targets with the business owner. The benchmark's policy assumptions are not approved business targets.
4. Create a fresh immutable model release; inspect its data hash, evaluation, schema, dependencies, and provenance. Store the previous release alongside it.
5. Run tests and build the container on the actual deployment platform. Scan dependencies and image, verify trusted artifact provenance, and test under expected traffic and failure conditions.
6. Place the service behind TLS termination and an authenticated ingress. Add network policy, request timeouts, rate limits, secret rotation, and central logging with sensitive fields excluded. The supplied localhost binding is intentional.
7. Run in shadow mode. Compare feature validity and probabilities to the existing decision process without affecting customers.
8. Deploy a small canary only after shadow results and mature outcomes satisfy the predeclared gates. Gradually expand traffic while watching quality and service health.
9. Preserve a tested fallback policy and analyst workflow. An API failure must lead to an explicit upstream fallback, never silently equate failure with low fraud risk.

## Monitoring and example alert logic

Scrape `/metrics` with the key via your metrics system. Do not expose it publicly. The following are examples to adapt; no monitoring server is included or running automatically.

```promql
# Fraction of failed prediction requests (5xx) over five minutes
sum(rate(fraudguard_requests_total{status=~"5.."}[5m]))
  / clamp_min(sum(rate(fraudguard_requests_total[5m])), 0.000001)

# 95th percentile model-computation latency, seconds
histogram_quantile(0.95, sum by (le) (rate(fraudguard_inference_seconds_bucket[5m])))

# Review fraction (transactions, not requests)
sum(rate(fraudguard_predictions_total{decision="review"}[15m]))
  / clamp_min(sum(rate(fraudguard_predictions_total[15m])), 0.000001)
```

Model-computation latency excludes HTTP queuing and network overhead; measure end-to-end latency at ingress as well. Use minimum sample sizes and sustained windows when alerting. Track invalid-input rates, 503 capacity responses, review queue depth, labeled recall/precision, and outcome delay. In a real system, add durable version-tagged prediction/outcome records with retention controls.

Run `fraudguard monitor` on a minimum 1,000-row window. Investigate PSI shifts with source-data health and delayed labels. A drift alert alone is not authorization to retrain or redeploy. A fixed threshold may exceed the validation review budget under drift; queue capacity is the caller's responsibility.

## Incident response

| Symptom | First checks | Response |
|---|---|---|
| Readiness 503 | Model path, schema, SHA, sklearn version | Restore trusted compatible release; keep traffic off instance |
| Authentication 401 | Secret mismatch or rotation | Restore matching secret; do not disable authentication |
| Input 422 | Schema, missing fields, finite numbers, batch size | Fix upstream contract; do not silently impute invalid inputs |
| Body 413 | Actual request bytes >256 KiB | Reduce batch/request size |
| Capacity 503 | Concurrency, CPU, latency | Backoff/retry; scale tested replicas or apply upstream fallback |
| Review-volume spike | Feature changes, score drift, traffic mix | Inspect queue and outcome quality; use approved fallback policy |
| Model-quality drop | Mature labels, prevalence, segment and data changes | Pause promotion; evaluate a new release on fresh holdouts |

## Rollback

Keep the previous image digest, MODEL_DIR, configuration, and schema contract. Route the canary away, deploy the prior trusted image/release, wait for readiness, and verify its version with `/v1/model`. Confirm input schema compatibility before routing traffic back. Retain incident records and both releases; do not overwrite model files in place.

## Reproducibility and validation gaps

The pinned lockfile records tested versions but is not a hash-verified supply-chain lock. The Docker base and GitHub actions use tags; a live release should pin reviewed immutable digests/commits. Hosted CI requires putting this folder at the root of a repository. Dependency security scanning, cloud identity, managed secrets, HA behavior, gateway limits, queue integration, fairness assessment, label ingestion, and long-running load tests remain target-environment work.
