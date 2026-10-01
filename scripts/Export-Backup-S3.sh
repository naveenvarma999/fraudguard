#!/usr/bin/env bash
# Optional off-host copy. Requires AWS CLI, an instance role and a private S3 bucket.
set -Eeuo pipefail
umask 077
cd -- "$(dirname -- "$0")/.."
: "${BACKUP_S3_URI:?Set BACKUP_S3_URI to s3://your-private-bucket/fraudguard}"
[[ "$BACKUP_S3_URI" =~ ^s3://[a-z0-9][a-z0-9.-]+(/[A-Za-z0-9_./-]*)?$ ]] || exit 2
filename=$(docker compose exec -T monitor python - <<'PY'
import json, os, time
from fraudguard.store import Store
row = Store(os.environ["STATE_DIR"]).query("SELECT value FROM settings WHERE key=?", ("backup_latest",))[0]
metadata = json.loads(row["value"])
if time.time()-metadata["created"] > 26*3600:
    raise SystemExit("Latest verified backup is stale")
print(metadata["file"])
PY
)
[[ "$filename" =~ ^backup-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.zip$ ]] || exit 2
mkdir -p backup-export
chmod 700 backup-export
docker compose exec -T monitor python -m fraudguard.backups verify "/backups/$filename" --scratch /backups > /dev/null
docker compose cp "monitor:/backups/$filename" "backup-export/$filename"
chmod 600 "backup-export/$filename"
aws s3 cp "backup-export/$filename" "${BACKUP_S3_URI%/}/$filename" --sse AES256 --only-show-errors
rm -- "backup-export/$filename"
printf '%s\n' 'Verified backup copied to the configured S3 location. Apply bucket lifecycle and access policies separately.'
