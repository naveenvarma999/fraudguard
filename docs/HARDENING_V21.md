# FraudGuard 2.1: security, backups, alerts and recovery

## What changed

- Browser sessions now expire after 30 minutes of inactivity or eight hours total. Account-management and model-release changes require an administrator login within the last 15 minutes. Existing v2 sessions must sign in again after the upgrade.
- A separate `MONITOR_KEY` authenticates the watchdog only to the monitoring endpoint. It cannot score transactions, list users or approve releases. The monitor container no longer receives the prediction API key. Both processes still trust the shared database volume; this is API credential separation, not protection against a compromised host/monitor process.
- Workspace responses have frame, content-type, referrer and browser-permission protections. Readiness checks cover registry and database access.
- A dependency-audit GitHub workflow fails on reported runtime vulnerabilities. It is supplied but was not executed in this session; this is not a clean vulnerability-scan certificate. The audit uses [pip-audit](https://pypi.org/project/pip-audit/). Review any dependency upgrade against serialized-model compatibility before deploying it.
- The watchdog automatically creates a full backup on first successful initialization and every 24 hours thereafter, retaining seven completed snapshots in a separate Docker volume. A restart does not reset the due time.
- Backups contain a transaction-consistent SQLite snapshot plus every referenced model bundle. Sessions are removed from the snapshot. Each file has a SHA-256 inventory, database integrity and foreign-key checks run, registry digests are validated, and the archive is verified before atomic publication. Restore rejects unsafe paths, duplicate entries, symbolic links, oversized archives and checksum mismatches.
- External HTTPS alert delivery has a persistent outbox, bounded retries, backoff, event IDs and exhausted-delivery status. Failed transport never logs receiver URLs, tokens or response bodies. Redirects are refused. Delivery remains disabled unless explicitly configured.
- The checked deployment script validates the new version, access controls, matching model/demo versions and a fresh backup. It also reopens the latest backup on disk for integrity verification. If checks fail, it attempts to restart the prior image using an explicit rollback override.

## Update the existing AWS installation

**Windows PowerShell:**

```powershell
scp -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" "C:\path\to\downloaded-updates\FraudGuard-Hardening-Update.tar.gz" ubuntu@YOUR_EC2_PUBLIC_IP:~/
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" ubuntu@YOUR_EC2_PUBLIC_IP
```

Wait for the **`ubuntu@...`** prompt. Then run on **Ubuntu**:

```bash
cd ~/fraudguard
tar -czf ~/fraudguard-before-v21-$(date +%Y%m%d-%H%M%S).tar.gz src scripts pyproject.toml Dockerfile compose.yaml
tar -xzf ~/FraudGuard-Hardening-Update.tar.gz -C ~/fraudguard
sudo bash scripts/Deploy-Checked.sh
```

The script adds a random monitoring key to your existing `.env` without displaying or replacing the API key. `.env` is restricted to its owner on Linux. Existing Caddy configuration, model artifacts and certificate volumes are not included in the update archive. Main Compose changes add a separate backup volume; do not delete persistent volumes when upgrading.

After success, open **https://YOUR_EC2_PUBLIC_IP/workspace** and sign in again. If you have not created an account yet:

```bash
sudo docker compose exec api python -m fraudguard.admin create-user naveen --role admin
```

The **Monitoring** screen now shows backup age and alert-delivery counts. The backup scheduler and alert worker run in the monitor service.

If the checked script reports failure, inspect `sudo docker compose logs --tail=100 api monitor`. When a prior image was available, the script writes `compose.rollback.yaml` and attempts to start that image. Normal Compose commands do not automatically load this override. While investigating a rolled-back deployment, use all relevant files explicitly:

```bash
sudo docker compose -f compose.yaml -f compose.override.yaml -f compose.rollback.yaml ps
```

The application rollback path was tested with simulated Docker commands. Real Docker execution and AWS rollback still need validation in your environment; no automatic data-destructive rollback is performed.

## Enable external alerts when you have a receiver

The supplied adapter sends generic JSON to an HTTPS receiver that accepts HTTP POST. It is not automatically compatible with every Slack/Teams or SNS API endpoint. Keep it disabled until you control the destination and want messages sent there.

Add these entries to the server's `.env` (using your real receiver and optional bearer token):

```dotenv
ALERT_WEBHOOK_URL=https://your-receiver.example/alerts
ALERT_WEBHOOK_TOKEN=your-receiver-token
```

Then recreate the monitor:

```bash
sudo docker compose up -d --no-build monitor
```

Payload fields are `event_id`, `service`, `alert`, `status`, `detail` and `timestamp`; no transaction payloads or account credentials are sent. The `Idempotency-Key` header matches `event_id`. Receivers should deduplicate that value: delivery is at least once, not exactly once. Both firing and resolved events are queued. No external messages were sent while building or testing this update.

Each event receives up to eight attempts with exponential backoff (30 seconds initially, capped at one hour). The worker checks every minute and sends up to five due events per pass. Exhausted deliveries remain visible. Once the receiver is fixed:

```bash
sudo docker compose exec monitor python -m fraudguard.admin retry-alerts
```

This requeues exhausted events with their original IDs. Enabling a receiver can deliver queued historical events, so inspect delivery counts first. Delivery and backup failures generate local alerts. If the API is down, the independent monitor can still persist/send a service alert; a whole-host outage requires external monitoring.

## Backups and restore

Local backups live in the `backup_data` Docker volume at `/backups`, mounted only into the monitor container. They are separate from the workspace volume but still on the same EC2 host. They do **not** protect against loss of that host unless copied elsewhere.

Create or verify a snapshot manually:

```bash
sudo docker compose exec monitor python -m fraudguard.backups create
sudo docker compose exec monitor python -m fraudguard.backups check-latest
```

To copy a named archive to a protected host directory, first create that directory with restrictive permissions, then use `docker compose cp monitor:/backups/BACKUP_FILENAME.zip DESTINATION`. Backups contain password hashes, review metadata and audit records. They are checksummed, not encrypted or cryptographically signed by this application. Protect their storage and provenance; checksums detect corruption, not a hostile party replacing both data and inventory.

Restore to a **new** directory, leaving the current workspace untouched:

```bash
sudo docker compose exec monitor python -m fraudguard.backups restore /backups/BACKUP_FILENAME.zip --target /data/recovered-20260930
```

Replace `BACKUP_FILENAME.zip` with the actual filename. Restore remaps model-registry paths to the new directory and revokes all saved sessions. It refuses an existing destination.

After inspecting the restored result, stop API and monitor:

```bash
sudo docker compose stop api monitor
```

Set `FRAUDGUARD_STATE_DIR=/data/recovered-20260930` in `.env`, preserving the other keys. Then restart and verify:

```bash
sudo docker compose up -d --no-build --wait --wait-timeout 240
sudo docker compose exec api python -m fraudguard.deployment --expected-version 2.1.0
```

Create a fresh backup after recovery. Do not use `docker compose down -v`. Retained sessions are revoked, but pending alert events retain their IDs so receivers can deduplicate any replay.

## Optional daily off-host S3 export

The repository includes `scripts/Export-Backup-S3.sh` and systemd service/timer files under `ops/`. This optional path requires the AWS CLI, a private S3 bucket and an EC2 instance role with permission to upload only to the chosen backup prefix. No bucket, IAM role, timer or upload was created in this session.

The script verifies the latest archive, requires it to be under 26 hours old, copies it to S3 with server-side AES256 encryption, and removes its temporary host copy only after the upload succeeds. It does not delete remote backups. Configure bucket versioning, a retention lifecycle and restore permissions separately.

On Ubuntu, create `/home/ubuntu/fraudguard/.backup-env` with:

```dotenv
BACKUP_S3_URI=s3://YOUR-PRIVATE-BUCKET/fraudguard
```

Restrict it to its owner, test the script with the environment loaded, then install the optional timer:

```bash
sudo cp ops/fraudguard-backup-export.service ops/fraudguard-backup-export.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fraudguard-backup-export.timer
```

It runs daily around 04:00 UTC. Inspect `journalctl -u fraudguard-backup-export.service` for failures. An S3 restore/download exercise is still required after configuring your real bucket. Local tests do not establish an off-host recovery-time or recovery-point guarantee.

## Verification and remaining limits

The local checks cover session expiry, recent administrator login, monitoring-key permissions, full backup/restore parity, invalid archives, backup retention, failure to replace the last good snapshot, stale/changed snapshots, notification retries/exhaustion, secret redaction, deployment contracts and a forcibly terminated SQLite writer. A firing alert remains firing across monitor restarts until a healthy check resolves it. Browser workflows were exercised with no JavaScript errors.

Docker Compose configuration and Bash syntax were checked. The checked-deployment success/failure branches were exercised using a fake Docker executable, not the real daemon. Live image execution, AWS deployment, vulnerability-database scanning, real receiver delivery and S3 export were not performed here.

This remains a single-host benchmark application. It does not provide MFA/SSO, database replication, cryptographically immutable external audit storage, host-level failover or a banking-production SLA.
