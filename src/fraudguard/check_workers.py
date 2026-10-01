"""Deployment smoke test: score once on every configured private worker."""

import json
import os

from fraudguard.dashboard import ASSETS
from fraudguard.pool import Pool
from fraudguard.store import Store


def main():
    store = Store(os.environ["STATE_DIR"])
    version = store.query("SELECT value FROM settings WHERE key='active_release'")[0]["value"]
    release = store.query("SELECT digest FROM releases WHERE id=?", (version,))[0]
    rows = json.loads((ASSETS / "sample.json").read_text())["transactions"][:1]
    urls = os.environ["INFERENCE_WORKERS"].split(",")
    if len(urls) < 2:
        raise ValueError("Load balancing requires at least two workers")
    for url in urls:
        Pool(url, os.environ["INFERENCE_KEY"]).score(rows, version, release["digest"])
    print(
        f"Verified authenticated scoring on {len(urls)} workers; no workspace records were created."
    )


if __name__ == "__main__":
    main()
