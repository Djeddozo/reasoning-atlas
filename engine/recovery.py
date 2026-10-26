"""Independent watchdog: deduplicate incidents, build fixes, test and install small repairs.

Only authenticated, structured crash locations are recorded; exception messages,
request bodies, tokens and arbitrary browser text are never passed to the agent.
"""
import hashlib
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

from . import claudeauth, db, devloop, updater, usage

PROTECTED = {'engine/recovery.py', 'engine/db.py', 'engine/devloop.py', 'engine/updater.py',
             'engine/gatekeeper.py', 'engine/claudeauth.py', 'engine/envfile.py', 'engine/models.py', 'engine/quickbuild.py'}


def record(con, source, error_type, frames=''):
    source = source if source in {'server', 'browser', 'service'} else 'server'
    error_type = re.sub(r'[^A-Za-z0-9_]', '', error_type)[:80] or 'UnexpectedError'
    # Callers supply locations only, never error messages or source lines.
    frames = '\n'.join(re.findall(r'(?:engine/[A-Za-z0-9_/.-]+|dashboard\.html):\d+', frames))[:1200]
    fingerprint = hashlib.sha256(f'{source}:{error_type}:{frames}'.encode()).hexdigest()[:24]
    con.execute("""INSERT INTO incidents (fingerprint,source,error_type,frames,created,last_seen)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
                   occurrences=incidents.occurrences+1,last_seen=excluded.last_seen,
                   task_id=CASE WHEN incidents.status='resolved' THEN NULL ELSE incidents.task_id END,
                   attempts=CASE WHEN incidents.status='resolved' THEN 0 ELSE incidents.attempts END,
                   detail=CASE WHEN incidents.status='resolved' THEN 'Error returned after repair' ELSE incidents.detail END,
                   status=CASE WHEN incidents.status='resolved' THEN 'queued' ELSE incidents.status END""",
                (fingerprint,source,error_type,frames,db.now(),db.now()))
    db.log(con,'hq','incident',f'{source}: {error_type} ({fingerprint})');con.commit()
    return fingerprint


def capture(exc):
    try:
        frames=[]
        for frame in traceback.extract_tb(exc.__traceback__):
            path=Path(frame.filename)
            try: relative=path.relative_to(db.ROOT).as_posix()
            except ValueError: continue
            if relative.startswith('engine/'): frames.append(f'{relative}:{frame.lineno}')
        con=db.connect();db.init(con)
        try: record(con,'server',type(exc).__name__,'\n'.join(frames))
        finally: con.close()
    except Exception:
        pass  # reporting must never cause another crash


def overview(con):
    return {'enabled': db.get_state(con,'recovery_enabled','0') == '1',
            'status': db.get_state(con,'recovery_status','Waiting for watchdog'),
            'checked': db.get_state(con,'recovery_checked',''),
            'incidents': [dict(r) for r in con.execute('SELECT * FROM incidents ORDER BY last_seen DESC LIMIT 12')]}


def command(*args):
    r=subprocess.run(list(args),cwd=db.ROOT,capture_output=True,text=True,timeout=180)
    if r.returncode: raise RuntimeError('Recovery command failed: '+args[0])
    return r.stdout


def service_health(con):
    result=command('systemctl','--user','show','hq.service','-p','NRestarts','-p','ActiveState')
    fields=dict(line.split('=',1) for line in result.splitlines() if '=' in line)
    count=int(fields.get('NRestarts','0'));last=db.get_state(con,'recovery_restarts','')
    if last and count>int(last): record(con,'service','UnexpectedServiceRestart')
    if fields.get('ActiveState') == 'failed':
        record(con,'service','ServiceFailed')
        command('systemctl','--user','restart','hq.service')
    db.set_state(con,'recovery_restarts',str(count));con.commit()


def safe_changes(files, numstat):
    if not files or len(files)>8 or any(f in PROTECTED or not (f.startswith('engine/') or f.startswith('tests/')) or '..' in Path(f).parts for f in files):
        return False
    if not any(f.startswith('tests/test_') and f.endswith('.py') for f in files): return False
    if not any(f.startswith('engine/') for f in files): return False
    total=0
    for line in numstat.splitlines():
        a,b,*_=line.split('\t')
        if not a.isdigit() or not b.isdigit(): return False
        total+=int(a)+int(b)
    return 0<total<=400


def install_repair(con, incident, run):
    """Only merge an unstacked incident repair at the exact tested head."""
    head=run['head'] or ''
    if not re.fullmatch('[0-9a-f]{40}',head) or run['base_run']:
        return 'Needs review: stacked repair or missing commit identity.'
    match=re.fullmatch(r'https://github.com/[^/]+/[^/]+/pull/(\d+)',run['pr_url'] or '')
    if not match: return 'Needs review: missing pull request.'
    number=match.group(1)
    info=json.loads(command('gh','pr','view',number,'--json','headRefOid,headRefName,baseRefName,state,mergeable,statusCheckRollup'))
    if info.get('state')=='MERGED':
        con.execute("UPDATE incidents SET status='awaiting_update',detail='Repair merged; waiting for installation' WHERE fingerprint=?",(incident['fingerprint'],));con.commit()
        return 'Repair merged; waiting for installation.'
    if info.get('headRefOid')!=head or info.get('headRefName')!=run['branch'] or info.get('baseRefName')!='main':
        return 'Needs review: pull request changed.'
    if info.get('state')!='OPEN' or info.get('mergeable')!='MERGEABLE': return 'Waiting for a mergeable repair.'
    checks=info.get('statusCheckRollup') or []
    if any(c.get('conclusion',c.get('state')) not in {'SUCCESS','NEUTRAL','SKIPPED'} for c in checks):
        return 'Waiting for passing GitHub checks.'
    updater._git('fetch','-q','origin','main',run['branch'])
    # Require the latest main as an ancestor; do not merge untested combinations.
    if subprocess.run(['git','merge-base','--is-ancestor','origin/main',head],cwd=db.ROOT,capture_output=True).returncode:
        return 'Needs review: main changed since this repair was built.'
    files=updater._git('diff','--name-only','origin/main',head).splitlines()
    stats=updater._git('diff','--numstat','origin/main',head)
    if not safe_changes(files,stats): return 'Needs review: repair is broad, protected, or lacks a regression test.'
    ok,_=updater._tests_pass_on(head)
    if not ok: return 'Needs review: repair tests failed; nothing installed.'
    if usage.killed() or devloop.paused(con) or db.get_state(con,'recovery_enabled','0')!='1':
        return 'Automatic repairs paused.'
    command('gh','pr','merge',number,'--squash','--match-head-commit',head)
    db.log(con,'hq','recovery_merge',f"task #{incident['task_id']} commit {head}")
    con.execute("UPDATE incidents SET status='awaiting_update',detail='Repair merged; waiting for installation' WHERE fingerprint=?",(incident['fingerprint'],));con.commit()
    return 'Repair tested and merged; installation follows.'


def tick(con):
    db.set_state(con,'recovery_checked',db.now());con.commit()
    service_health(con)
    if db.get_state(con,'recovery_enabled','0')!='1': return 'Automatic repairs are off.'
    if usage.killed() or devloop.paused(con): return 'Automatic repairs paused by the stop controls.'
    pending=con.execute("SELECT * FROM incidents WHERE status!='resolved' ORDER BY created LIMIT 10").fetchall()
    if not pending:return 'Monitoring: no open incidents.'
    if claudeauth.check()['state']!='connected':return 'Reconnect Claude in System to enable repairs.'
    for incident in pending:
        if incident['status']=='awaiting_update':
            if updater._dirty():return 'Repair ready; local changes must be published before installation.'
            result=updater.apply(con,do_restart=False)
            if result['status'] in {'updated','up_to_date'}:
                con.execute("UPDATE incidents SET status='resolved',detail='Repair installed' WHERE fingerprint=?",(incident['fingerprint'],));con.commit()
                command('systemctl','--user','restart','hq.service')
                return 'Repair installed; Atlas restarted.'
            return result['message']
        tid=incident['task_id']
        if not tid:
            description=(f"Fix the observed Atlas {incident['source']} failure: {incident['error_type']}.\n"
                         f"Observed locations (no user content):\n{incident['frames'] or 'Service exited unexpectedly; inspect startup and process supervision.'}\n"
                         "Reproduce the cause, make the smallest repair and add a regression test. Run the full test suite. "
                         "Do not change authentication, permissions, budgets, update/repair controls or dependencies. "
                         "Do not access live secrets or live memory. If evidence is insufficient, report that; do not invent a fix.")
            tid=db.insert(con,'tasks',title=f"Repair Atlas: {incident['error_type']}",description=description,owner='ai',impact=5,status='todo')
            con.execute('UPDATE incidents SET task_id=? WHERE fingerprint=?',(tid,incident['fingerprint']));con.commit()
        run=con.execute('SELECT * FROM dev_runs WHERE task_id=? ORDER BY id DESC LIMIT 1',(tid,)).fetchone()
        if run and run['status']=='merged':
            con.execute("UPDATE incidents SET status='awaiting_update' WHERE fingerprint=?",(incident['fingerprint'],));con.commit()
            return 'Repair merged; installation follows.'
        if run and run['status']=='pr_open':
            message=install_repair(con,dict(incident)|{'task_id':tid},run)
            con.execute('UPDATE incidents SET detail=? WHERE fingerprint=?',(message,incident['fingerprint']));con.commit()
            return message
        if run and run['status']=='running':return f'Repair task #{tid} is running.'
        if incident['attempts']>=2:return f'Repair task #{tid} needs review after two attempts.'
        reason=devloop.why_not_now(con,tid)
        if reason:return reason
        if not devloop.cfg()['enabled']:return 'Enable the agent to build repairs.'
        if devloop.batch_full(con):return 'Waiting for space in the review batch.'
        reason=devloop.limit_reached(con)
        if reason:return reason
        # Persist before the launch. The dev loop holds its own cross-process run lock.
        con.execute("UPDATE incidents SET attempts=attempts+1,status='repairing',detail='Repair requested' WHERE fingerprint=?",(incident['fingerprint'],));con.commit()
        devloop.start_background(force=False,task_id=tid)
        return f'Repair task #{tid} requested within normal agent limits.'
    return 'Monitoring.'


def main():
    import fcntl
    lock_path=db.ROOT/'memory'/'recovery.lock'
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        con=db.connect();db.init(con)
        try:
            try: message=tick(con)
            except Exception as exc: message=f'Watchdog could not finish ({type(exc).__name__}); it will retry.'
            db.set_state(con,'recovery_status',message);con.commit()
        finally:con.close()


if __name__=='__main__':main()
