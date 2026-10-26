"""Hide duplicate/infrastructure registrations without uninstalling anything.

Run with --apply after reviewing the default preview. Backups retain all
registry metadata. Offline applications are deliberately retained.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

INFRASTRUCTURE = {
    'user:atlas-hub.service', 'user:hq.service',
    'user:atlas-builder-watch.service',
    'user:ticket-watcher-cleanup.service',
    'system:finance-vault-network-guard.service',
    'system:padelslot-cloudflare.service', 'system:padelslot-gatebot.service',
    'system:pm2-user.service', 'system:wifi-reconnect.service',
    'system:padelslot-candidate.service',
}


def hidden_keys(keys):
    keys=set(keys)
    return {key for key in keys if key in INFRASTRUCTURE or (
        key.startswith('systemd:') and 'system:'+key.split(':',1)[1] in keys)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('/var/lib/pi-app-hub/data'))
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    path=args.data/'registry.sqlite3'
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        rows=db.execute('SELECT id,source_key,removed FROM apps').fetchall()
    hide=hidden_keys(row[1] for row in rows)
    print(json.dumps({'hide':sorted(hide),'preserve':'All services, containers, files and application data; offline applications'},indent=2))
    if not args.apply: return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=args.data/f'registry-before-RA-066-{stamp}.sqlite3'
    with sqlite3.connect(path) as db:
        with sqlite3.connect(backup) as dest: db.backup(dest)
        backup.chmod(0o600)
        for identifier,key,removed in rows:
            if key in hide and not removed:
                db.execute('UPDATE apps SET removed=1 WHERE id=?',(identifier,))
                db.execute('INSERT INTO activity(at,actor,action,app_id,outcome,detail) VALUES(unixepoch(),?,?,?,?,?)',
                           ('Codex RA-066','hide-registration',identifier,'success','Duplicate or infrastructure registration only; runtime and data preserved.'))
        # Keep the genuine screen service as Atlas's single application entry.
        row=db.execute("SELECT id,overrides FROM apps WHERE source_key='user:atlas-web.service'").fetchone()
        if not row: raise RuntimeError('Wait for Hub discovery of atlas-web.service before applying')
        overrides=json.loads(row[1])
        overrides.update(name='Reasoning Atlas',web_url=os.environ.get('ATLAS_WEB_ORIGIN','https://<pi-name>.<tailnet>.ts.net:4310').rstrip('/')+'/',icon='globe')
        db.execute('UPDATE apps SET overrides=? WHERE id=?',(json.dumps(overrides),row[0]))
    print('Registry backup:',backup)


if __name__=='__main__': main()
