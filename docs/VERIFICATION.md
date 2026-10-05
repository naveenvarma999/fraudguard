# Verification — 2026-10-05

## Current branch: v2.6

- **121 Python tests passed**, including shared cross-user history, exact retries, late retention, boundary semantics, conflicting legacy migration, delayed merchant labels/corrections, shadow failure isolation, retraining, separate approval, restart and rollback. Sixteen upstream deprecation warnings remain.
- **7 JavaScript tests passed**, and JavaScript syntax and Ruff checks passed.
- A local headless Edge walkthrough passed login, behavioral submission, detail display, review, monitoring and system flow. Its six real screenshots form the 60-second synthetic walkthrough GIF. The in-app browser automation entry point failed to initialise, so local headless browser testing was used.
- Actual training ran on **523,605 Sparkov transactions / 1,197 active accounts**, generated from 1,200 customer profiles. Five ablations, the full-feature candidate comparison, independent calibration and 200 account-bootstrap resamples per cohort completed. Report and model hashes are committed. [Evidence](ML_EVIDENCE.md).
- The larger model is a shadow candidate. The live/default champion weights are unchanged; improved infrastructure does not imply improved model AP. Retraining/shadow tests use synthetic fixtures, not real customer outcomes.
- [Load results](../artifacts/study_v26/load-report.json) measure the SQLite ingestion path locally. They exclude HTTP/TLS/auth quotas and shadow inference; they are not EC2 throughput measurements.

## GitHub and live AWS evidence

GitHub's public API reported `validate` and `dependency-security` successful on PR #2 merge commit **c604ad0**. That validates the merged v2.5 Docker path and Terraform schema, not this unpublished v2.6 branch.

Readiness and OpenAPI at the public IP were queried with standard certifi certificate verification enabled. Readiness returned `ready`, and the application reported **2.5.0**. No certificate checks were bypassed. The operator's supplied deployment log also showed the API, monitor and two scoring workers healthy and deployment checks passing. This supports the existing app rollout; it does not establish the new EBS Terraform configuration was applied.

## Pending external verification

This environment cannot read the owner's GitHub CLI configuration under its Windows permissions. The branch needs an owner push, fresh CI, review and merge, then an AWS application rollout. No v2.6 AWS update is claimed. No production shadow experiment, off-host restore drill, Terraform apply or automatic failover is claimed. Docker Desktop's engine was unavailable during this work, so the new image build still needs CI verification.

Historical v2.4/v2.5 release notes describe their original local checks. Current deployment evidence in this file supersedes their earlier 'not deployed' statements for v2.5 only.
