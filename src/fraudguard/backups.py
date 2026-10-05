"""Verified full-workspace backups and restore into a new, empty directory."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from fraudguard.releases import bundle_digest


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


MAX_BYTES = 2 * 1024**3
SAFE_FILE = re.compile(
    r"^(workspace\.sqlite3|releases/[A-Za-z0-9_-]{1,100}/(manifest\.json|model\.joblib|evaluation\.json))$"
)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def remove_staging(path, parent):
    path = Path(path).resolve()
    if path.parent != Path(parent).resolve() or not path.name.startswith(".building-"):
        raise ValueError("Unexpected staging directory")
    shutil.rmtree(path)


def validate_snapshot(folder):
    folder = Path(folder)
    with database(folder / "workspace.sqlite3") as db:
        db.row_factory = sqlite3.Row
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Database integrity failed")
        for row in db.execute("SELECT id,digest FROM releases"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", row["id"]):
                raise ValueError("Invalid registry identifier")
            if bundle_digest(folder / "releases" / row["id"]) != row["digest"]:
                raise ValueError("Backup registry checksum mismatch")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='behavioral_models'").fetchone():
            for row in db.execute("SELECT manifest,artifact FROM behavioral_models"):
                if (
                    hashlib.sha256(row["artifact"]).hexdigest()
                    != json.loads(row["manifest"])["model_sha256"]
                ):
                    raise ValueError("Backup behavioral model checksum mismatch")
            behavioral_active = db.execute(
                "SELECT value FROM settings WHERE key='behavioral_active'"
            ).fetchone()
            if (
                behavioral_active
                and not db.execute(
                    "SELECT 1 FROM behavioral_candidates WHERE version=? AND status='approved'",
                    (behavioral_active[0],),
                ).fetchone()
            ):
                raise ValueError("Active behavioral model is absent or unapproved")
        active = db.execute("SELECT value FROM settings WHERE key='active_release'").fetchone()
        if (
            active
            and not db.execute(
                "SELECT id FROM releases WHERE id=? AND status='approved'", (active[0],)
            ).fetchone()
        ):
            raise ValueError("Active model is absent or unapproved")


def unpack(archive, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if (
            len(infos) > 5000
            or len(names) != len(set(names))
            or sum(i.file_size for i in infos) > MAX_BYTES
        ):
            raise ValueError("Backup size or entry limit exceeded")
        if any(n != "backup.json" and not SAFE_FILE.fullmatch(n) for n in names):
            raise ValueError("Unsafe backup entry")
        if z.getinfo("backup.json").file_size > 2 * 1024 * 1024:
            raise ValueError("Backup inventory is oversized")
        metadata = json.loads(z.read("backup.json"))
        if (
            metadata.get("format") != 1
            or set(metadata["files"]) != set(names) - {"backup.json"}
            or "workspace.sqlite3" not in metadata["files"]
        ):
            raise ValueError("Backup inventory mismatch")
        for name, expected in metadata["files"].items():
            info = z.getinfo(name)
            if info.is_dir() or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Backup links are not allowed")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if digest(target) != expected:
                raise ValueError("Backup checksum failed")
    validate_snapshot(destination)
    return metadata


def verify(archive, scratch):
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".building-verify-", dir=scratch))
    try:
        return unpack(archive, staging)
    finally:
        remove_staging(staging, scratch)


def create_backup(store, directory, keep=7):
    if keep < 1:
        raise ValueError("Keep at least one backup")
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
    identifier = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    temporary = root / (".building-" + identifier + ".zip")
    final = root / ("backup-" + identifier + ".zip")
    try:
        store.backup(staging / "workspace.sqlite3")
        with database(staging / "workspace.sqlite3") as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM sessions")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrollments'"
            ).fetchone():
                db.execute("DELETE FROM enrollments")
            db.commit()
            db.execute("VACUUM")
            releases = [dict(r) for r in db.execute("SELECT * FROM releases")]
        for release in releases:
            version = release["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", version):
                raise ValueError("Invalid release ID")
            source = Path(release["path"]).resolve()
            if source != (store.directory / "releases" / version).resolve():
                raise ValueError("Release outside managed registry")
            target = staging / "releases" / version
            target.mkdir(parents=True)
            for name in ("manifest.json", "model.joblib", "evaluation.json"):
                if (source / name).exists():
                    shutil.copyfile(source / name, target / name)
        validate_snapshot(staging)
        files = {
            p.relative_to(staging).as_posix(): digest(p) for p in staging.rglob("*") if p.is_file()
        }
        metadata = {
            "format": 1,
            "created": time.time(),
            "files": files,
            "sessions": "revoked in snapshot",
        }
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("backup.json", json.dumps(metadata))
            for name in files:
                z.write(staging / name, name)
        verify(temporary, root)
        os.replace(temporary, final)
        final.chmod(0o600)
        result = {
            "file": final.name,
            "created": metadata["created"],
            "bytes": final.stat().st_size,
            "sha256": digest(final),
            "verified": True,
        }
        with store.connect() as db:
            db.execute(
                "INSERT INTO settings VALUES('backup_latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(result),),
            )
            store.audit(db, "backup", "backup.verified", final.name, {"bytes": result["bytes"]})
        ordered = [final] + sorted(
            (p for p in root.glob("backup-*.zip") if p != final),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        for old in ordered[keep:]:
            if old.is_file() and not old.is_symlink() and old.resolve().parent == root:
                old.unlink()
        return result
    finally:
        remove_staging(staging, root)
        if temporary.exists():
            temporary.unlink()


def restore(archive, destination):
    destination = Path(destination).resolve()
    # Never overwrite an existing database or model registry.
    if destination.exists():
        raise ValueError("Restore target must be a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".building-restore-", dir=destination.parent))
    try:
        metadata = unpack(archive, staging)
        with database(staging / "workspace.sqlite3") as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM sessions")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrollments'"
            ).fetchone():
                db.execute("DELETE FROM enrollments")
            for (version,) in db.execute("SELECT id FROM releases").fetchall():
                db.execute(
                    "UPDATE releases SET path=? WHERE id=?",
                    (str(destination / "releases" / version), version),
                )
            db.execute("DELETE FROM settings WHERE key LIKE 'backup_%'")
        os.replace(staging, destination)
        return {"restored": str(destination), "created": metadata["created"], "sessions": "revoked"}
    finally:
        if staging.exists():
            remove_staging(staging, destination.parent)


def verify_latest(store, directory):
    rows = store.query("SELECT value FROM settings WHERE key='backup_latest'")
    if not rows:
        raise ValueError("No verified backup available")
    latest = json.loads(rows[0]["value"])
    if (
        not re.fullmatch(r"backup-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.zip", latest["file"])
        or time.time() - latest["created"] > 26 * 3600
    ):
        raise ValueError("Latest backup is invalid or stale")
    archive = Path(directory) / latest["file"]
    if digest(archive) != latest["sha256"]:
        raise ValueError("Latest backup changed since verification")
    verify(archive, directory)
    return latest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--directory", default=os.getenv("BACKUP_DIR", "/backups"))
    create.add_argument("--state-dir", default=os.getenv("STATE_DIR", "/data"))
    check = commands.add_parser("verify")
    check.add_argument("archive")
    check.add_argument("--scratch", default="/backups")
    recover = commands.add_parser("restore")
    recover.add_argument("archive")
    recover.add_argument("--target", required=True)
    latest = commands.add_parser("check-latest")
    latest.add_argument("--directory", default=os.getenv("BACKUP_DIR", "/backups"))
    latest.add_argument("--state-dir", default=os.getenv("STATE_DIR", "/data"))
    args = parser.parse_args()
    if args.command == "create":
        from fraudguard.store import Store

        result = create_backup(Store(args.state_dir), args.directory)
    elif args.command == "check-latest":
        from fraudguard.store import Store

        result = verify_latest(Store(args.state_dir), args.directory)
    elif args.command == "verify":
        result = verify(args.archive, args.scratch)
    else:
        result = restore(args.archive, args.target)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
