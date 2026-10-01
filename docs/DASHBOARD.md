# Historical v1 dashboard guide

For the current release, follow [the v2 AWS upgrade guide](UPGRADE_V2_AWS.md). The account workspace and persistence change the behavior described below.

# FraudGuard dashboard update (v1.1)

The home page now provides a responsive transaction-review application. `/docs` remains the API reference.

## Included workflow

- Public demo: eight curated real benchmark transactions, scored by the loaded model at startup.
- Private scoring: CSV upload or pasted JSON, up to 100 transactions / 256 KiB.
- Connection with the existing API key; authenticated requests go to the existing same-origin API.
- Batch totals, probability scores, review/pass filtering, ID search, risk sorting, input inspection and CSV export.
- Historical model performance and a guide to the required features.

The browser retains the key only in page memory. Refresh or disconnect clears it. The application does not save uploaded files, predictions or review history. This is a single-workspace benchmark application, not a multi-user payment-processing system. The public demo never grants arbitrary prediction access. Real uploads require the same transformed features as the benchmark; ordinary card numbers or statements cannot be scored.

## Update the existing AWS installation

In a **new Windows PowerShell window**, upload the archive:

```powershell
scp -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" "C:\path\to\downloaded-updates\FraudGuard-Dashboard-Update.tar.gz" ubuntu@YOUR_EC2_PUBLIC_IP:~/
```

In your **Ubuntu SSH window**, apply it:

```bash
cd ~/fraudguard
mkdir -p ~/fraudguard-backups
tar -czf ~/fraudguard-backups/source-before-dashboard-$(date +%Y%m%d-%H%M%S).tar.gz src pyproject.toml
tar -xzf ~/FraudGuard-Dashboard-Update.tar.gz -C ~/fraudguard
sudo docker compose up --build -d --wait --wait-timeout 180
curl -fsS http://127.0.0.1:8000/health/ready
```

The update includes source, tests, documentation and packaging metadata. It excludes `.env`, model artifacts, Docker Compose files, Caddy configuration and certificate volumes. Your existing API key, model and HTTPS setup remain in place.

Open **https://YOUR_EC2_PUBLIC_IP/**. Use a hard refresh if necessary. Click **Explore the live demo** first. For uploads, click **Connect workspace**, enter the API key from your server's `~/fraudguard/.env`, and use **Download sample CSV** as the format template. Do not share your API key in screenshots, chats or repositories.

If you are disconnected from SSH, reconnect using:

```powershell
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" ubuntu@YOUR_EC2_PUBLIC_IP
```

## Verification

- 36 Python tests passed, including original API/model tests, demo parity, authorization and dashboard routes.
- 7 JavaScript tests passed: CSV parsing, schema limits, rejected inputs, label removal and export.
- Browser checks passed in Edge: public demo, review filtering, row inspection, authenticated CSV upload, export and report navigation; no JavaScript or CSP errors.
- Desktop and 390px mobile layouts inspected; no page-wide horizontal overflow.
- Wheel build confirmed all seven frontend assets are included.

AWS deployment is not performed by creating this archive. Run the update commands above to publish it. Browser tests use a local test key, never your production key.
