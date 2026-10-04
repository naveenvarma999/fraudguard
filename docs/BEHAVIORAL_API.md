# Behavioral API — v2.4

`POST /v1/behavioral/predict` accepts a transaction plus prior history for the same account. Use your service API key or named-user bearer session, exactly as for `/v1/predict`. The Docker image includes the Sparkov-trained bundle under `artifacts/behavioral_service`; an operator can configure another trusted bundle with `BEHAVIORAL_MODEL_DIR`.

```bash
curl -H "X-API-Key: YOUR_PRIVATE_KEY" -H "Content-Type: application/json" \
  --data @docs/behavioral-request.json http://localhost:8000/v1/behavioral/predict
```

The [sample request](behavioral-request.json) contains synthetic tokens and three events. Timestamps are UTC Unix seconds. Supply opaque account tokens, merchant tokens, amount and merchant latitude/longitude. No names, card numbers or labels are accepted.

The response contains `risk_score`, `score_kind: uncalibrated`, threshold, review recommendation, model version, calculated features, cold-start flag and factual behavioral context. `GET /v1/behavioral/model` exposes the authenticated model contract and dataset digest. Context is not SHAP attribution or proof of causality.

History must use unique IDs, the same account, and timestamps strictly before the current event. Equal-time history is rejected rather than arbitrarily giving some simultaneous events more knowledge. Up to 1,000 history events fit within the shared 256 KiB body limit. The engine uses only the preceding 30 days. An empty history is a cold start; callers must supply complete relevant history for valid behavioral interpretation.

The route shares authentication, account quotas and the coordinator's four inference slots. A busy local slot returns 503 before quota debit. Invalid history returns 422; attempts admitted before validation may consume quota. Results are not appended to the original V1–V28 review workspace: that database contract is still specific to the benchmark model. Repeating an identical request is stateless and deterministic for a fixed bundle.

Tests compare API features and scores with independent offline features, reject future/foreign-account/duplicate/label inputs, and ensure saturated-capacity rejection does not spend quota. Bundles must have a matching schema, feature order, class order, finite threshold and file digest. Joblib files execute trusted operator-supplied code; checksums do not authorize an untrusted uploaded model.

No v2.4 AWS rollout has occurred. Check `/openapi.json` for version `2.4.0` after deployment; source changes alone do not update the server.
