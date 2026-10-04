# Operations guide

Current application version: **2.3.0**. Behavioral research is an offline addition and does not replace the serving model. This guide consolidates the 2.1–2.3 operating instructions; historical release notes remain in Git history.

## Start locally

With Docker Desktop using Linux containers, from the repository root in Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Deploy-Docker.ps1
docker compose exec api python -m fraudguard.admin create-user admin --role admin
```

The password prompt is private. Open `http://localhost:8000/workspace`. Usernames are 3–40 letters, digits, underscores or hyphens, not email addresses. There is no public signup or default administrator.

## Accounts and authenticator setup

Leave the code blank on first login. In an authenticator app, add the displayed setup key as a time-based account, confirm its six-digit code, and save the eight one-use recovery codes. Sign in with a fresh code afterward. Enrollment expires after ten minutes; five failed confirmations invalidate it. Ordinary sessions expire after 30 minutes idle/eight hours total; sensitive changes need an administrator login within 15 minutes.

Passwords are hashed with salted scrypt. MFA secrets are encrypted using `MFA_ENCRYPTION_KEY`; keep that deployment key when restoring a database. Back up `.env` privately and separately. Recovery codes are hashed and displayed once. Compose requires MFA for named users; service API keys do not use OTP.

An operator can run `python -m fraudguard.admin reset-mfa USERNAME` inside the API container after independently verifying the person's identity. This revokes sessions and requires enrollment again. Password reset does not bypass MFA.

## Deploy or update on EC2

Use a reviewed source archive or Git checkout, preserving `.env`, `.workers-enabled`, `compose.override.yaml`, model artifacts and Docker volumes. Never copy a local demo database over live state. **Windows transfer commands run in PowerShell; Linux deployment commands run only after SSH shows `ubuntu@...`.**

On Ubuntu, from `~/fraudguard`, first create a backup with the command below. Securely preserve the previous source/configuration before installing reviewed updates. Then:

```bash
sudo bash scripts/Deploy-Checked.sh
sudo bash scripts/Compose.sh ps
curl -fsS http://127.0.0.1:8000/health/ready
sudo bash scripts/Compose.sh exec api python -m fraudguard.deployment --expected-version 2.3.0
```

The script configures missing secrets, builds, waits for health, checks application/model/security/backup contracts and smoke-tests enabled workers. On failure it attempts image rollback; this does not restore the database. Inspect its output and rollback override before exposing traffic. Changes in this repository do not automatically update AWS.

Keep SSH restricted to your public IP, expose 80/443 through Caddy, and keep 8000 bound to loopback. The [Terraform reference](../infra/README.md) defines new infrastructure, not an automatic upgrade/import of the existing instance.

## Request limits and optional workers

| Control | Limit |
|---|---|
| Per-account prediction bucket | 60 requests; refill 1/second |
| Per-account transaction bucket | 3,000; refill 50/second |
| Process admission bucket | 120 requests; refill 60/second; health excluded |
| Login failures | 5/username and 30/direct peer per 10 minutes |
| Coordinator concurrency | Four batches |
| Worker concurrency | Two batches each |
| Prediction payload | 100 transactions, 256 KiB |

Account budgets persist in SQLite. The process bucket resets on restart. Quotas can be charged for admitted predictions that later fail. Respect 429/503 `Retry-After` and back off. These limits do not provide network DDoS protection; users behind Caddy can share the direct-peer login limit.

Create `.workers-enabled` then rerun the checked deployment to enable two private scoring workers. Always use `scripts/Compose.sh` afterward so overrides stay applied. Least-busy routing, failure exclusion and read-only retries are explained in [decision 005](DECISIONS.md). Check history after a lost API response before resubmitting.

This remains one API coordinator, SQLite, one host and shared storage. Do not scale the `api` service or put SQLite on network storage. Workers do not add host CPU/RAM; measure capacity on the actual EC2 instance. To disable them, stop `worker-a worker-b`, remove only the `.workers-enabled` marker, and redeploy.

## Backups and recovery

The monitor creates verified full snapshots on first initialization and every 24 hours, retaining seven in the separate `backup_data` volume. Snapshots include SQLite and referenced model bundles, remove sessions/pending enrollment, and check integrity, foreign keys, registry digests and file hashes. Same-host backups do not protect against host loss.

```bash
sudo bash scripts/Compose.sh exec -T monitor python -m fraudguard.backups create
sudo bash scripts/Compose.sh exec -T monitor python -m fraudguard.backups check-latest
sudo bash scripts/Compose.sh exec monitor python -m fraudguard.backups restore /backups/BACKUP_FILENAME.zip --target /data/recovered-YYYYMMDD
```

Restore refuses an existing destination. Inspect the new directory, stop API/monitor/workers, set `FRAUDGUARD_STATE_DIR` in `.env` to that restored directory, preserve `MFA_ENCRYPTION_KEY`, then run the checked deployment and take a new backup. Old snapshots can roll back recovery-code usage; review affected MFA state. **Never use `down -v` during an upgrade or recovery.** Archives contain sensitive hashes/audit data; application checksums are neither encryption nor proof against malicious replacement.

Optional S3 export uses `scripts/Export-Backup-S3.sh` and the `ops/` timer. It needs a private bucket, scoped EC2 role, AWS CLI and private `.backup-env` containing `BACKUP_S3_URI`. Configure versioning/retention separately and perform a real download/restore drill before claiming off-host recovery. No bucket, role or timer is installed automatically.

## Alerts

Configure `ALERT_WEBHOOK_URL` and optional `ALERT_WEBHOOK_TOKEN` in `.env`, then recreate the monitor using the Compose wrapper. Use a trusted HTTPS endpoint accepting generic JSON POST, not an assumed Slack/Teams adapter. The persistent outbox sends firing/resolved status, event ID and details without transaction bodies. It refuses redirects, retries up to eight times with backoff, and shows exhausted deliveries. Receivers should deduplicate the `Idempotency-Key`.

After fixing a receiver, `python -m fraudguard.admin retry-alerts` in the monitor requeues exhausted events. Enabling delivery can send queued historical events. Whole-host failure needs an external monitor.

## Evidence and limits

The UI provides scoped overview counts, transaction timelines, upload validation and an explanatory interactive system flow. It does not display live distributed traffic or model accuracy merely from readiness. Public demo data are fixed samples; private uploads require authentication.

The test and audit badges in the README reflect GitHub runs. Local results and research measurements are in [verification](VERIFICATION.md). Docker runtime, AWS rollout, receiver delivery and S3 recovery need separate environment-specific evidence; passing Python tests does not prove them.
