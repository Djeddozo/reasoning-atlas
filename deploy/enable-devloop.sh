#!/usr/bin/env bash
# Switch Atlas's dev loop on (or off with --off). Run this yourself on the Pi; it asks before changing anything.
#
#   bash deploy/enable-devloop.sh        # on: limits + safety rules in the constitution, schedule every 3 hours
#   bash deploy/enable-devloop.sh --off  # off: enabled = false, schedule removed (the rules stay)
#
# Controls afterwards: dashboard → Agent tab, Telegram /dev /kill /resume /usage, or hq dev / hq kill.
set -euo pipefail
cd "$(dirname "$0")/.."
Atlas="$(pwd -P)"   # the real folder, also when started through the ~/hq-system link (RA-042)
C=config/constitution.toml
L=config/local.toml   # Pi-only settings, git-ignored
MARK="# hq-devloop"

if [ "${1:-}" = "--off" ]; then
  python3 - "$L" <<'PY'
import re, sys
p = sys.argv[1]
try:
    s = open(p).read()
except FileNotFoundError:
    s = "# Settings for this Pi only (git-ignored). Merged over config/constitution.toml.\n"
if "[devloop]" in s:
    s = re.sub(r"(?m)^(\[devloop\][^\[]*?^enabled\s*=\s*)true", r"\1false", s, count=1)
else:  # overrides an older `enabled = true` that may still be in constitution.toml
    s = s.rstrip("\n") + "\n\n[devloop]\nenabled = false\n"
open(p, "w").write(s)
PY
  (crontab -l 2>/dev/null | grep -v "$MARK" || true) | crontab -
  echo "Dev loop is off: enabled = false and the schedule is removed. A run in progress finishes (or use: ./atlas.sh dev stop)."
  exit 0
fi

CLAUDE_BIN="$(command -v claude || true)"
if [ -z "$CLAUDE_BIN" ]; then
  cat <<'EOF'
Claude Code is not installed on this Pi yet. Install it and log in with your Claude account first:
  curl -fsSL https://claude.ai/install.sh | bash      # or: npm install -g @anthropic-ai/claude-code
  claude                                              # log in once, then type /exit
Then run this script again.
EOF
  exit 1
fi

cat <<EOF
This switches on Atlas's dev loop: Claude Code works on the top task owned by the AI, every 3 hours.

It will change:
  1. $L (this Pi only, not in git) → add a [devloop] section (enabled, 40 min and 60 turns per run; daily runs set on Builder settings)
  2. $L → add gatekeeper rules: the agent may not push, merge or rebase; may not control the dev loop
     or the kill switch; may not edit Atlas's live config or memory (it works in its own copy)
  3. crontab → run '$Atlas/atlas.sh dev run' at minute 17 every 3 hours

The agent commits on its own branch; Atlas runs the tests and opens a pull request; you merge.
Stop everything at any time: Kill switch on the dashboard, /kill in Telegram, or ./atlas.sh kill.
EOF
read -r -p "Switch it on? [y/N] " ok
[ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Nothing changed."; exit 0; }

python3 - "$C" "$L" <<'PY'
import re, subprocess, sys, tomllib
c, l = sys.argv[1], sys.argv[2]
RULE_WHY = ("agents commit only", "only Djeddi controls the dev loop", "Atlas's live configuration and memory")
# 1. an older version of this script wrote into constitution.toml: move that out, so updates never collide
head = subprocess.run(["git", "show", f"HEAD:{c}"], capture_output=True, text=True).stdout
cur = open(c).read()
if head and cur != head:
    a, b = tomllib.loads(cur), tomllib.loads(head)
    a.pop("devloop", None)
    a.get("gatekeeper", {})["deny"] = [r for r in a.get("gatekeeper", {}).get("deny", []) if not r.get("why", "").startswith(RULE_WHY)]
    if a == b:
        open(c, "w").write(head)
        print("Moved the dev-loop settings out of constitution.toml (it matches GitHub again).")
    else:
        print("Note: constitution.toml has other hand-made changes; left as they are.")
# 2. Pi-only settings live in config/local.toml (git-ignored), merged over the constitution
try:
    s = open(l).read()
except FileNotFoundError:
    s = "# Settings for this Pi only (git-ignored). Merged over config/constitution.toml.\n"
if "[devloop]" in s:
    s = re.sub(r"(?m)^(\[devloop\][^\[]*?^enabled\s*=\s*)false", r"\1true", s, count=1)
else:
    s = s.rstrip("\n") + """

[devloop]
# Claude Code on the Pi works through AI-owned tasks (engine/devloop.py). Switched on by deploy/enable-devloop.sh.
enabled = true
max_minutes = 40                # a run is stopped after this
max_turns = 60                  # Claude Code turns per run
# Daily runs and the review batch are chosen on Builder settings. Uncomment to set a hard ceiling over that choice:
# max_runs_per_day = 10         # 0 = no scheduled runs at all
# max_open_prs = 5              # unreviewed PRs before the agent stops, whatever Builder settings say
branch_prefix = "hq-dev/"
claude_command = "claude"
"""
if "agents commit only" not in s:
    s = s.rstrip("\n") + """

[[gatekeeper.deny]]
tool = "Bash"
pattern = '(^|[;&|\\s])git\\s+(push|merge|rebase|worktree)\\b|(^|[;&|\\s])gh\\s+pr\\s+merge'
why = "agents commit only; Atlas pushes branches after tests pass and Djeddi merges"
[[gatekeeper.deny]]
tool = "Bash"
pattern = '(hq\\.sh|atlas\\.sh|-m\\s+(hq|engine))\\s+(dev|kill|unkill|update)\\b|memory/KILL'
why = "only Djeddi controls the dev loop, updates and the kill switch"
[[gatekeeper.deny]]
tool = "Edit|Write|NotebookEdit|Bash"
pattern = '/hq-system/(config|memory)/'
why = "Atlas's live configuration and memory are not edited by agents (they work in .devloop/ copies)"
"""
tomllib.loads(s)  # refuse to write a broken file
open(l, "w").write(s)
PY

LINE="17 */3 * * * cd $Atlas && set -a && . ./.env && set +a && PATH=\"$(dirname "$CLAUDE_BIN"):\$PATH\" ./atlas.sh dev run >> memory/devloop.log 2>&1  $MARK"
( crontab -l 2>/dev/null | grep -v "$MARK" || true; echo "$LINE" ) | crontab -

./atlas.sh dev resume >/dev/null
echo
echo "Dev loop is ON. First scheduled run within 3 hours; start one now with: ./atlas.sh dev run"
echo "Watch it: dashboard → Agent ($(./atlas.sh link 2>/dev/null | tail -1))"
echo "Kill switch: dashboard button, /kill in Telegram, or ./atlas.sh kill"
