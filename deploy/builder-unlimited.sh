#!/usr/bin/env bash
# Let Builder settings decide the builder's daily runs and review batch (#178).
# An older enable-devloop.sh wrote hard ceilings into config/local.toml (max_runs_per_day = 3, max_open_prs = 5),
# so "No limit" still stopped at 3 builds a day and "Unlimited" at 5 open PRs. This comments those two lines out
# in [devloop]; everything else stays. Run it yourself on the Pi:  bash deploy/builder-unlimited.sh
# Undo: remove the leading "# (#178) " from the two lines in config/local.toml.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - config/local.toml <<'PY'
import re, sys
p = sys.argv[1]
try:
    s = open(p).read()
except FileNotFoundError:
    sys.exit("No config/local.toml: there are no ceilings to remove.")
m = re.search(r"(?ms)^\[devloop\]\s*$(.*?)(?=^\[|\Z)", s)
if not m:
    sys.exit("No [devloop] section in config/local.toml: there are no ceilings to remove.")
body, n = re.subn(r"(?m)^(\s*)((?:max_runs_per_day|max_open_prs)\s*=.*)$", r"\1# (#178) \2", m.group(1))
if not n:
    print("Nothing to change: Builder settings already decide daily runs and the review batch.")
    sys.exit(0)
open(p, "w").write(s[:m.start(1)] + body + s[m.end(1):])
print(f"Commented out {n} ceiling line(s) in {p}. Builder settings now decide: 'No limit' and 'Unlimited' hold.")
PY
