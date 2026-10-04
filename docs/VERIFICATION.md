# Verification — 2026-10-04 review upgrade

## Completed locally

- **92 Python tests passed**, including existing authentication, API, model, backup, worker and deployment-contract tests; new point-in-time/parity/entity-split tests; and a real local SQLite MLflow integration test. There were 16 upstream deprecation warnings, not test failures.
- **7 JavaScript tests passed**; Ruff and Git whitespace checks passed.
- Pinned **runtime** dependency audit: **no known vulnerabilities found** by pip-audit 2.10.1. The earlier scan found advisories in Starlette 0.48.0 and cryptography 48.0.1. Runtime pins now use FastAPI 0.142.2, Starlette 1.7.0 and cryptography 50.0.2 with required transitive dependencies. Numerical/model dependencies were preserved.
- Full seed-42 synthetic run: **10,610 rows**, exact batch/stream feature equality, dataset digest and cohort metrics in [the report](../artifacts/behavioral/report.json). This is synthetic research, not a new production fraud benchmark.
- Logged the full experiment report, parameters and metrics to a local MLflow SQLite store. The test independently reads back run status, a metric and artifact listing.
- `terraform fmt -check` passed. Provider initialization failed with a Windows access-denied error while reading the downloaded provider; **Terraform schema validation and plan have not passed locally**. A separate CI validation job is included. No infrastructure was applied.

## External evidence and publishing status

The existing main-branch [validate run](https://github.com/naveenvarma999/fraudguard/actions/runs/36854683519) passed for commit `7cd0c4d`. The corresponding [dependency audit](https://github.com/naveenvarma999/fraudguard/actions/runs/36854683601) failed. These are **pre-upgrade runs**, not evidence that this branch passed GitHub Actions. README badges track main truthfully; they may remain red until the fixes are pushed, reviewed and merged.

The public HTTPS readiness and fixed-sample demo endpoints responded on 2026-10-04. The serving model reported `20260928T033102Z-76274b69`. This does not prove that every local UI change is deployed. The new research track has not been deployed.

This session could not access saved GitHub authentication; a noninteractive branch push failed asking for a username. New commits remain local until the owner pushes `codex/behavioral-features`. No PR was created and no GitHub Actions result is claimed for these changes.

Docker Desktop's engine was unavailable, so **a fresh container build/start was not verified locally**. The existing CI container smoke test remains required before deployment. AWS, real external alert delivery and off-host S3 recovery were not modified or retested.

## Review and release checklist

1. Push `codex/behavioral-features` and open a PR to `main`; inspect both validation and dependency-audit results.
2. Review the synthetic generator assumptions, cohort sizes, cold-start metric and five decision records. Do not describe this as real-bank training data or distributed real-time serving.
3. Review the dependency changes with the CI container smoke result before upgrading AWS. Preserve secrets, accounts and volumes; follow [operations](OPERATIONS.md).
4. Treat Terraform as a separate reviewed infrastructure reference. Do not apply it to the existing instance without a resource-import plan.
