#!/usr/bin/env bash
# Install the continuous queue watcher; it obeys existing builder controls.
set -euo pipefail
cd "$(dirname "$0")/.."
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
# the templates name %h/hq-system; install them for the folder this checkout really is in (~/atlas after RA-042)
for unit in atlas-builder-watch.service atlas-builder-watch.timer; do
  sed "s|%h/hq-system|$(pwd -P)|g" "deploy/$unit" > "$UNIT_DIR/$unit"
done
python3 - <<'PY'
from datetime import datetime
import sqlite3
from engine import atlasplan, db, devloop
con = db.connect()
try:
    if devloop.current(con):
        raise SystemExit('A builder run is active; wait for it before changing queue identities.')
    backup = db.ROOT / 'runtime' / ('before-ra067-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '.db')
    backup.parent.mkdir(exist_ok=True)
    con.commit()
    with sqlite3.connect(backup) as target:
        con.backup(target)
    backup.chmod(0o600)
    print(atlasplan.sync(con))
    # Recover the original provider report, using the observation date (not today).
    row = con.execute("SELECT finished,summary FROM dev_runs WHERE summary LIKE '%limit%' ORDER BY id DESC LIMIT 1").fetchone()
    if row and row['finished'] and atlasplan.parsed_reset(row['summary'], datetime.fromisoformat(row['finished']).astimezone()):
        atlasplan.note_limit(con, row['summary'], datetime.fromisoformat(row['finished']).astimezone())
        con.commit()
    print('Existing autonomy:', devloop.autonomy(con))
    print('Daily budget:', devloop.limits_text(con))
finally:
    con.close()
PY
systemctl --user daemon-reload
systemctl --user enable --now atlas-builder-watch.timer
systemctl --user restart atlas-hub.service
