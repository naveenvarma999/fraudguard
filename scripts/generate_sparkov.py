"""Generate external Sparkov data sequentially, then retain only modeling columns.

Install Faker==13.12.0 separately. Supply a reviewed local checkout of
namebrandon/Sparkov_Data_Generation at the pinned revision below. No downloads.
"""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

REVISION = "b5eb45c89d36f2aa4ef16044a42945bed8b96d93"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--accounts", type=int, default=100)
    args = parser.parse_args()
    source, output = Path(args.source).resolve(), Path(args.output).resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != REVISION:
        raise ValueError("Review and use the pinned Sparkov revision")
    output.mkdir(parents=True, exist_ok=False)
    os.chdir(source)
    sys.path.insert(0, str(source))
    random.seed(42)
    np.random.seed(42)
    Faker.seed(42)
    import datagen_customer
    import datagen_transaction

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2024, 1, 1)

    datagen_customer.date = FixedDate
    customers = output / "customers.csv"
    config = source / "profiles/main_config.json"
    datagen_customer.main(args.accounts, 42, config, customers)
    frames = []
    for profile in json.loads(config.read_text()):
        path = output / profile.replace(".json", ".csv")
        datagen_transaction.main(
            customers,
            source / "profiles" / profile,
            datetime(2024, 1, 1),
            datetime(2024, 7, 1),
            path,
        )
        # Upstream epoch conversion uses host timezone; parse wall-clock fields
        # explicitly as UTC instead. Personal-looking synthetic fields are dropped.
        frame = pd.read_csv(path, sep="|", dtype={"cc_num": str})
        if len(frame):
            frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    from fraudguard.sparkov import normalize

    normalized = normalize(raw)
    text = normalized.to_csv(index=False, lineterminator="\n")
    (output / "normalized.csv").write_text(text, encoding="utf-8")
    provenance = {
        "source": "https://github.com/namebrandon/Sparkov_Data_Generation",
        "revision": revision,
        "seed": 42,
        "customers": args.accounts,
        "period": ["2024-01-01", "2024-07-01"],
        "faker": "13.12.0",
        "numpy": np.__version__,
        "generation": "Sequential original profiles; frozen customer date; UTC timestamp normalization",
        "dataset_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "rows": len(normalized),
        "fraud_rate": float(normalized.label.mean()),
        "limitations": "External synthetic generator, not real bank data. A fixed 2-day label delay is simulated, not supplied by Sparkov.",
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
