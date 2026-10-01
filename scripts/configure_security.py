"""Add a distinct monitoring secret without printing or replacing the API key."""

import base64
import secrets
from pathlib import Path


def configure(path):
    path = Path(path)
    content = path.read_text(encoding="utf-8-sig")
    values = {}
    for line in content.splitlines():
        if line.startswith(("API_KEY=", "MONITOR_KEY=", "MFA_ENCRYPTION_KEY=", "INFERENCE_KEY=")):
            name, value = line.split("=", 1)
            if name in values:
                raise ValueError("Duplicate security setting in .env")
            values[name] = value.strip()
    if len(values.get("API_KEY", "")) < 16:
        raise ValueError("An existing API_KEY of at least 16 characters is required")
    if "MONITOR_KEY" not in values:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\nMONITOR_KEY=" + secrets.token_urlsafe(32) + "\n")
    elif len(values["MONITOR_KEY"]) < 16 or values["MONITOR_KEY"] == values["API_KEY"]:
        raise ValueError("MONITOR_KEY must be distinct and at least 16 characters")
    additions = []
    for name in ("MFA_ENCRYPTION_KEY", "INFERENCE_KEY"):
        if name not in values:
            additions.append(
                name
                + "="
                + (
                    base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                    if name == "MFA_ENCRYPTION_KEY"
                    else secrets.token_urlsafe(32)
                )
            )
        elif len(values[name]) < 32:
            raise ValueError(name + " must be at least 32 characters; existing value preserved")
    if "MFA_ENCRYPTION_KEY" in values:
        try:
            if (
                len(base64.b64decode(values["MFA_ENCRYPTION_KEY"], altchars=b"-_", validate=True))
                != 32
            ):
                raise ValueError("wrong length")
        except ValueError:
            raise ValueError(
                "MFA_ENCRYPTION_KEY must encode exactly 32 random bytes; existing value preserved"
            ) from None
    if additions:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(additions) + "\n")
    path.chmod(0o600)


if __name__ == "__main__":
    configure(Path(__file__).resolve().parents[1] / ".env")
    print("Security configuration ready; secrets were not displayed.")
