# Behavioral API — v2.5

`POST /v1/behavioral/predict` accepts **only one `transaction`**. Client-provided `history` and labels are rejected. Use a named-user bearer session or service API key. `STATE_DIR` is mandatory for this endpoint. The included Sparkov model remains synthetic-data research evidence, not a validated payment authorization system.

```bash
curl -H "X-API-Key: YOUR_PRIVATE_KEY" -H "Content-Type: application/json" \
  --data @docs/behavioral-request.json http://localhost:8000/v1/behavioral/predict
```

The [sample](behavioral-request.json) uses opaque account/merchant tokens, UTC Unix seconds, amount and merchant coordinates. Never submit card numbers or personal details. The workspace has a form for the same operation.

## Persistence and ordering

History is keyed by **authenticated submitter and account token**; separate users do not share history. The service credential has its own namespace. Events for each account must arrive in `(timestamp, event_id)` order. Features use `[t-30 days, t)`, excluding all equal-time peers. An identical retry returns the original saved response and prediction ID, even after restart or a model change; a conflicting ID or new late event returns 422. The per-owner ID is unique across accounts. Retries consume request quota but do not add prediction, audit or drift rows.

One SQLite `BEGIN IMMEDIATE` transaction reads history, scores, and stores the event, prediction, feature bins, audit entry and watermark. Failures roll back all these changes. This intentionally serializes writes; it is not a horizontally distributed ingestion service. Up to 10,000 prior events fit in the supported account window; overflow rejects without committing. Clients must submit complete, authentic events in order: server storage prevents rewriting supplied history, but cannot prove the upstream feed is truthful.

The response includes an uncalibrated risk score, model version, saved features/context, `prediction_id` and `decision_reason`. With no history the decision is always `review`, with reason `cold_start_manual_review`, regardless of score. This operational policy does not change model weights or measured threshold-only research results. Other events use the model threshold.

## Review and monitoring

Predictions appear in the normal workspace queue and detail route, with the same ownership rules as ULB submissions. Review opinions stay separate from verified fraud labels. `/ops/monitoring` adds a `behavioral` object with version-specific delayed-label quality and feature PSI; the included reference bins use only the original Sparkov fit partition. Older bundles without reference bins explicitly report `reference_not_available`. Service counters count successful responses, including retries; persisted prediction counts count unique accepted events.

Raw events follow prediction retention (90 days by default; at least 30 when behavioral events exist). Cascading deletion removes event payloads and cached responses. Account watermarks remain so pruned old events cannot be replayed as new. Backup archives include event state and checksummed model bytes in SQLite; they revoke login sessions. A backup still needs the matching MFA encryption key from operator-managed configuration.

The route shares authentication, quotas and four coordinator inference slots; it does not use the optional ULB scoring workers. Busy slots return 503 before quota debit. Model files are trusted operator-controlled serialized code; checksum verification is not authorization to load arbitrary uploads.

No AWS rollout is claimed. After deployment, verify `/openapi.json` reports **2.5.0**, then submit an event and confirm its review record before calling the rollout complete.
