# Behavioral API — v2.6

`POST /v1/behavioral/predict` accepts one `transaction`, authenticated with a named-user session or service key. `STATE_DIR` is required. `history` and supplied fraud labels are rejected. Use opaque tokens, not payment-card numbers or personal identifiers.

Required fields are `event_id`, `account_id`, `merchant_id`, UTC Unix-second `timestamp`, `amount`, `latitude` and `longitude`. Optional `category`, `home_latitude` and `home_longitude` supply richer context for the shadow candidate and retraining snapshots. The default champion remains the existing nine-feature model. The enriched model sends incomplete context to manual review if promoted.

History is shared by account across this single organisation. Submitter ownership still controls review access. Exact event retries return saved responses; conflicting payloads under the same ID fail with 422. New out-of-order events return **202** with `status: stored_late` and `prediction_id: null`; they are stored for future history without rewriting prior scores. A 202 result is not approval to authorize a payment.

Features use `[t-30 days, t)`, excluding all equal-time peers, with a 10,000-prior-event limit. Accepted score/event/audit/drift writes are atomic. Event-ID retries are bounded by retention. Current label corrections enter merchant statistics only after their actual availability timestamp. Application history assumes a complete, authentic upstream feed; storing it does not independently prove authenticity.

Responses identify model version, score kind, threshold, decision reason, saved features and prediction ID. No history forces manual review. Workspace records support human review and separate verified labels, monitoring and shadow comparison. Successful-response counters include retries; database prediction counts do not.

`GET /v1/behavioral/model` returns the active contract, including a promoted candidate after restart. `/ops/behavioral/releases` and its authenticated administrator controls expose shadow evidence, start, promotion and rollback. Candidate submission remains a trusted operator operation because joblib deserialization requires trusted artifacts.

See [Lifecycle](LIFECYCLE.md) for migration, retraining, evidence gates, limits and commands, and [Operations](OPERATIONS.md) for backup and deployment. Version 2.6.0 is prepared in this branch; the last verified live deployment is 2.5.0.
