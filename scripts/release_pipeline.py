"""Train, evaluate and stage a trusted candidate. Approval never happens automatically."""

import argparse
import json
import os

from fraudguard.releases import stage
from fraudguard.store import Store
from fraudguard.training import train

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--submitted-by", required=True)
parser.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "./state"))
args = parser.parse_args()
train(args.data, args.output)
result = stage(Store(args.state_dir), args.output, args.submitted_by)
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
