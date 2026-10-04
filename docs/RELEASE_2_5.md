# v2.5 — server-owned behavioral workflow

Behavioral clients now submit only a new transaction. SQLite derives prior account history and atomically saves events, scores, ordering markers, feature bins and audit entries. Exact retries return the saved prediction; late and conflicting events fail. All cold starts require manual review.

The workspace adds a transaction form, saved behavioral evidence, review and verified-label integration. Behavioral drift and delayed-label performance are reported independently, with watchdog alert conditions. Backups preserve event state and checksummed behavioral model artifacts.

New Terraform deployments use a protected encrypted EBS volume for data, backups and configuration. App releases use checked scripts instead of replacing instances. This is prepared configuration, not an applied AWS migration.

Validation: full Python suite 114 passed; final targeted suite 13 passed after the last alert/timestamp changes; JavaScript data tests 7 passed; Ruff, JavaScript syntax, Compose configuration, Bash syntax and Terraform formatting passed. No fresh Docker build or browser-interaction validation was performed. Provider validation and AWS tests remain outstanding.

The trained model weights and research metrics are unchanged. Larger Sparkov training, enriched features, calibration and account-bootstrap intervals remain outstanding. This combined branch includes the previous unmerged v2.4 work.

## Publish from your Windows PowerShell

```powershell
cd "C:\Users\NAVEEN VARMA\Documents\Codex\2026-09-28\do-x20\outputs\FraudGuard-GitHub"
git push -u origin codex/server-owned-history
```

Create a pull request from that branch to main, inspect the CI results and merge after review. Do not assume this updates AWS. Use the existing checked deployment workflow in OPERATIONS.md after publication. Existing servers retain their current volumes unless explicitly migrated; do not enable `.storage-enabled` on an empty directory.
