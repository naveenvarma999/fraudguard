"""Operator-only administration; run inside the API container."""

import argparse
import getpass
import json
import os

from fraudguard.auth import add_user, password_hash
from fraudguard.releases import stage
from fraudguard.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.getenv("STATE_DIR", "./state"))
    commands = parser.add_subparsers(dest="command", required=True)
    user = commands.add_parser("create-user")
    user.add_argument("username")
    user.add_argument("--role", choices=["admin", "analyst"], default="admin")
    reset = commands.add_parser("reset-password")
    reset.add_argument("username")
    mfa_reset = commands.add_parser(
        "reset-mfa", help="Operator recovery after independently verifying user identity"
    )
    mfa_reset.add_argument("username")
    release = commands.add_parser("stage")
    release.add_argument("--bundle", required=True)
    release.add_argument("--submitted-by", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", required=True)
    prune = commands.add_parser("prune")
    prune.add_argument("--days", type=int, default=90)
    commands.add_parser(
        "retry-alerts", help="Requeue exhausted alert deliveries after fixing the receiver"
    )
    args = parser.parse_args()
    store = Store(args.state_dir)
    if args.command in ("create-user", "reset-password"):
        password = getpass.getpass("Password (12–128 characters): ")
        if password != getpass.getpass("Confirm password: ") or not 12 <= len(password) <= 128:
            parser.error("Passwords must match and contain 12–128 characters")
        if args.command == "create-user":
            add_user(store, args.username, password, args.role)
        else:
            with store.connect() as db:
                if (
                    db.execute(
                        "UPDATE users SET password=? WHERE name=?",
                        (password_hash(password), args.username),
                    ).rowcount
                    != 1
                ):
                    parser.error("Unknown user")
                db.execute("DELETE FROM sessions WHERE username=?", (args.username,))
                db.execute("DELETE FROM enrollments WHERE username=?", (args.username,))
                store.audit(db, "operator", "password.reset", args.username)
        print("Account updated. Password was not logged.")
    elif args.command == "reset-mfa":
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE name=?", (args.username,)).fetchone():
                parser.error("Unknown user")
            for table in ("mfa", "recovery_codes", "enrollments", "sessions"):
                db.execute(f"DELETE FROM {table} WHERE username=?", (args.username,))
            store.audit(db, "operator", "mfa.reset", args.username)
        print("Authenticator reset and sessions revoked. User must enroll again at sign-in.")
    elif args.command == "stage":
        print(json.dumps(stage(store, args.bundle, args.submitted_by), indent=2))
    elif args.command == "backup":
        store.backup(args.output)
        print("Database backup completed. Also preserve the releases directory.")
    elif args.command == "prune":
        print(f"Pruned {store.prune(args.days)} old predictions; audit history retained.")

    else:
        with store.connect() as db:
            count = db.execute(
                "UPDATE deliveries SET status='pending',attempts=0,next_attempt=0 WHERE status='dead'"
            ).rowcount
            store.audit(db, "operator", "notification.requeued", detail={"count": count})
        print(f"Requeued {count} deliveries; original event IDs retained.")


if __name__ == "__main__":
    main()
