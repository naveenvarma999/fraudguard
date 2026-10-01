> For the current v2.1 source and archive, use [the v2.1 upgrade instructions](HARDENING_V21.md). The commands below describe the historical v2.0 package.

# Upgrade your existing AWS instance to FraudGuard v2

This updates your current EC2 application. You do not need another instance or a new domain.

## Step 1 — Windows PowerShell

Upload the update, then connect to AWS:

```powershell
scp -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" "C:\path\to\downloaded-updates\FraudGuard-Advanced-Update.tar.gz" ubuntu@YOUR_EC2_PUBLIC_IP:~/
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" ubuntu@YOUR_EC2_PUBLIC_IP
```

Wait until your prompt begins with **`ubuntu@`**. The remaining commands run on Ubuntu, not in a `PS C:\...` prompt.

## Step 2 — Ubuntu SSH terminal

Back up the existing application files, apply the update, and rebuild:

```bash
cd ~/fraudguard
tar -czf ~/fraudguard-before-v2-$(date +%Y%m%d-%H%M%S).tar.gz src pyproject.toml Dockerfile compose.yaml
tar -xzf ~/FraudGuard-Advanced-Update.tar.gz -C ~/fraudguard
sudo docker compose build api
sudo docker compose up -d --no-build --wait --wait-timeout 180
sudo docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
```

The expected readiness result is `{"status":"ready"}`. The API and the new monitor should become healthy; your existing Caddy HTTPS service remains configured as before.

This v2 update changes the main `compose.yaml` to add a persistent volume and monitoring container. It **does not include or replace `.env`, `compose.override.yaml`, the Caddyfile, existing model artifacts or certificate volumes**. First startup copies your current model into the persistent registry as the baseline. Existing v1 predictions were not persisted, so the new history starts empty.

## Step 3 — Create your first administrator

Still in the Ubuntu SSH terminal:

```bash
sudo docker compose exec api python -m fraudguard.admin create-user naveen --role admin
```

Enter a new password of at least 12 characters, then confirm it. **Nothing appears while you type the password; that is normal.** Do not use your API key as your account password.

Open **https://YOUR_EC2_PUBLIC_IP/workspace** and sign in as `naveen`. Your original public demo remains at **https://YOUR_EC2_PUBLIC_IP/**.

You can now:

1. Create analyst accounts from **Users**.
2. Upload a correctly formatted CSV/JSON batch from **Review queue**.
3. Open a prediction to save a review or verified delayed outcome.
4. Inspect **Monitoring** and **Audit trail**.
5. Create a second administrator account for independent model-release approval.

Drift and quality cards initially say there is not enough evidence. They need real saved predictions and verified labels; they do not display fabricated metrics. The public demo does not populate operational monitoring.

## If something fails

```bash
sudo docker compose logs --tail=80 api monitor
```

Do not publish credentials or `.env` contents when sharing logs. A missing workspace directory should not be solved by running Ubuntu commands in Windows; reconnect over SSH first.

If login is rate-limited, wait ten minutes. If the initial username already exists, use your existing password or reset it:

```bash
sudo docker compose exec api python -m fraudguard.admin reset-password naveen
```

Use the registry's **Roll back to previous** control for model releases. To revert this application upgrade, stop the monitor, restore the source/Compose backup created above, rebuild the API and bring the prior configuration up again. Keep the `workspace_data` volume; do not run `docker compose down -v`.

## Validation status

The v2 source passed 51 Python tests, seven JavaScript data tests and browser workflow checks. Local persistent-API load testing passed with zero errors. The package wheel includes the dashboard/workspace assets, and Docker Compose configuration validation passed.

The Docker engine was inaccessible from this assistant session, so the new Linux image has not been run locally. GitHub CI includes an image build, container smoke checks and a persistent-API load gate, but that workflow has not been executed in your account. The commands above are needed to apply and verify the update on AWS.

See `docs/ADVANCED_WORKSPACE.md` for release staging, monitoring thresholds, retention, backups and limitations.
