"""Explicit proxy trust boundary for the container's single API process."""

import ipaddress
import os

import uvicorn


def trusted_proxies(value):
    addresses = [part.strip() for part in value.split(",") if part.strip()]
    for address in addresses:
        # Never trust all clients or a broad Docker/private subnet.
        ipaddress.ip_address(address)
    return ",".join(addresses)


def main():
    uvicorn.run(
        "fraudguard.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        workers=1,
        limit_concurrency=32,
        timeout_keep_alive=5,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=trusted_proxies(os.getenv("TRUSTED_PROXY_IPS", "")),
    )


if __name__ == "__main__":
    main()
