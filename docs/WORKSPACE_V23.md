# FraudGuard 2.3: review workspace update

## What changed

- **Overview:** actual saved transaction volume and flagged counts over seven UTC calendar days, open flagged backlog across retained history, daily counts, recent submissions and model availability. Analysts see their submissions; administrators see all accounts. Availability is explicitly separate from measured accuracy.
- **Transaction details:** addressable `#transaction/PREDICTION_ID` pages show probability, threshold, amount, model version, ownership, reviews and verified outcomes. Paginated history preserves review and label corrections. Direct links require login and ownership permission.
- **Upload feedback:** validation, indeterminate server processing, saved-result counts and a link to details. Lost responses tell users to check history before retrying to avoid duplicate submissions. Processing percentages are not invented.
- **Interactive system flow:** six selectable stages explain upload, authentication and limits, worker routing, models, human review and monitoring. Configured worker count is shown. This is an explanatory diagram, not live traffic telemetry.
- **Onboarding:** clearer username instructions prevent email-address login errors. Authenticator setup has numbered steps, setup-key copy, cancellation and recovery-code instructions. Validation messages identify affected fields.
- **Accessibility:** keyboard controls, visible focus, live status messages, numerical chart alternatives, mobile layouts and reduced-motion support.

The existing model, authenticator accounts, quotas, worker pool and backup features are retained. This release does not add automatic scaling or retrain the model.

## Update the existing AWS deployment

Version 2.2 was confirmed deployed in the user-supplied AWS output. Version 2.3 remains local until the following update is run.

In **Windows PowerShell**:

```powershell
scp -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" "C:\path\to\downloaded-updates\FraudGuard-Workspace-Update.tar.gz" ubuntu@YOUR_EC2_PUBLIC_IP:~/
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\Downloads\fraudguard-key.pem.pem" ubuntu@YOUR_EC2_PUBLIC_IP
```

After the prompt starts with **`ubuntu@...`**, run:

```bash
cd ~/fraudguard
sudo bash scripts/Compose.sh exec -T monitor python -m fraudguard.backups create
umask 077
tar -czf ~/fraudguard-before-v23-$(date +%Y%m%d-%H%M%S).tar.gz src scripts pyproject.toml requirements.lock Dockerfile compose.yaml compose.workers.yaml .env
tar -xzf ~/FraudGuard-Workspace-Update.tar.gz -C ~/fraudguard
sudo bash scripts/Deploy-Checked.sh
sudo bash scripts/Compose.sh ps
```

The archive excludes `.env`, `.workers-enabled`, the HTTPS override, model artifacts and live databases. Keep your existing authenticator encryption key. The deployment script retains the enabled worker pool and checks each worker, application version, security and backup freshness. The expected application version is **2.3.0**. It attempts image rollback if verification fails; this is not a database restore.

Refresh the existing `/workspace` page. Use your existing username, password and authenticator. Do not recreate the account or enroll again. The initial view is now **Overview**.

## Verification

- 81 Python tests passed, including overview scope/window/backlog, detail ownership and history pagination.
- Seven JavaScript data tests passed.
- Desktop and mobile browser checks passed for username guidance, MFA enrollment, empty overview, invalid upload, successful scoring, review and verified-outcome history, real overview counts, interactive stages and transaction links after login.
- Ruff, Compose configuration with both workers, and Bash deployment syntax passed.

Preview screenshots contain local sample transactions, not customer data. This UI release has not yet been deployed to AWS.
