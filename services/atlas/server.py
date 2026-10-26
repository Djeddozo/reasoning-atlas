"""Loopback-only Atlas service with transactional SQLite notes."""
import argparse, contextlib, json, os, sqlite3, sys, threading, uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from adapters.hq import status, snapshot as hq_snapshot
from services.atlas import actions, capabilities, enginesync as hq_connect, graph, handoff, hubclient, intake, localvoice, progress, reminders, selfupdate, sync, versioning
from packages.contracts import modules
ROOT=Path(__file__).resolve().parents[2]
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
class Store:
    def __init__(self,path):
        self.path=path; Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT NOT NULL, createdAt TEXT NOT NULL)')
            if db.execute('PRAGMA user_version').fetchone()[0]<1: db.execute('PRAGMA user_version=1')
            reminders.migrate(db)
            handoff.migrate(db)
            hq_connect.migrate(db)
            intake.migrate(db)
            actions.migrate(db)
            sync.migrate_client(db)
        self.sources=intake.Intake(self.connect,Path(path).parent/'sources')
    @contextlib.contextmanager
    def connect(self):
        """One transaction per use; the connection is always closed so Windows releases the file."""
        db=sqlite3.connect(self.path,timeout=10)
        try:
            with db: yield db
        finally: db.close()
    def list(self):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT notes.*, s.sourceId, src.contentHash FROM notes LEFT JOIN note_sources s ON s.noteId=notes.id LEFT JOIN sources src ON src.id=s.sourceId ORDER BY createdAt DESC')]
    def save(self,body,identifier):
        if not isinstance(body,str) or not body.strip() or len(body)>20000: raise ValueError('Note must contain 1–20,000 characters')
        uuid.UUID(identifier)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO notes VALUES (?,?,?)',(identifier,body,now()))
            row=db.execute('SELECT body FROM notes WHERE id=?',(identifier,)).fetchone()
            if row[0]!=body: raise ValueError('Note identity already exists with different content')
            self.sources.note(db,identifier,body)
        return identifier
    def reminders(self):
        with self.connect() as db: return reminders.list_all(db)
    def add_reminder(self,identifier,title,detail=''):
        with self.connect() as db: return reminders.add(db,identifier,title,detail)
    def set_reminder_done(self,identifier,done):
        with self.connect() as db: reminders.set_done(db,identifier,bool(done))
    def actions(self):
        with self.connect() as db: items=actions.list_all(db)
        return [{**i,'prompt':actions.as_text(i,prompt=True)} for i in items]
    def update_action(self,identifier,what,data):
        with self.connect() as db:
            if what=='step':
                if not isinstance(data.get('done'),bool): raise ValueError('done must be true or false')
                actions.set_step(db,identifier,data['step'],data['done'])
            elif what=='input': actions.set_input(db,identifier,data['input'])
            elif what=='done':
                if not isinstance(data.get('done'),bool): raise ValueError('done must be true or false')
                actions.set_done(db,identifier,data['done'])
            else: raise KeyError('Unknown endpoint')
    def sync_now(self):
        config=hubclient.load()
        if not config: return {**sync.status(self.connect),'configured':False}
        try: return {**sync.flush(self.connect,sync.HubTransport(config)),'configured':True}
        except sync.SyncError as exc: return {**sync.status(self.connect),'configured':True,'lastError':str(exc)}
    def sync_view(self):
        with self.connect() as db: items,open_conflicts=sync.notes(db),sync.conflicts(db)
        return {**sync.status(self.connect),'configured':bool(hubclient.load()),'notes':items,'conflictDetails':open_conflicts}
    def handoff(self,task_id,tool):
        result=handoff.packet(task_id,tool)
        with self.connect() as db: result['id']=handoff.log(db,task_id,tool,result['intent'])
        return result
    def handoffs(self):
        with self.connect() as db: return handoff.summary(db)
RESTART_EXIT_CODE=3  # Start Atlas.cmd rebuilds and restarts the service on this code
def release_source():
    try: return json.loads((ROOT/'apps/web/dist/current.json').read_text(encoding='utf-8')).get('source')
    except (OSError,ValueError): return None
def current_release():
    """Return (release id, folder) for the live client build."""
    dist=ROOT/'apps/web/dist'
    try:
        release=json.loads((dist/'current.json').read_text(encoding='utf-8'))['release']
        folder=dist/'releases'/release
        if folder.is_dir(): return release,folder
    except (OSError,ValueError,KeyError): pass
    return None,dist
def make_server(port,path,supervised=False,public_origin=None,tailnet_login=None,capability_adapters=None):
    # Remote mode is exclusively behind loopback Tailscale Serve. Its identity
    # headers are stripped/replaced by Serve; never bind this server to the LAN.
    public_origin=public_origin or os.environ.get('ATLAS_WEB_ORIGIN')
    tailnet_login=tailnet_login or os.environ.get('ATLAS_WEB_LOGIN')
    if public_origin:
        parsed=urlsplit(public_origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or not tailnet_login:
            raise ValueError('Remote Atlas requires an HTTPS origin and a Tailscale login')
    store=Store(path)
    store.sources.resume()  # finish intake that a crash or failure interrupted
    started=dict(at=now(),service=versioning.service_fingerprint(ROOT),restartRequested=False)
    def gateway():  # the registry is read fresh, so a manifest change applies without a restart (RA-022)
        return capabilities.Gateway(modules.load_registry(ROOT/'modules',ROOT),store.connect,capability_adapters)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def authorized(self):
            local_hosts=(f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
            host=self.headers.get('Host')
            if public_origin:
                if host!=urlsplit(public_origin).netloc or self.headers.get('Tailscale-User-Login')!=tailnet_login:
                    self.send(403,{'error':'Connect with the authorized Tailscale account.'})
                    return False
            elif host not in local_hosts:
                self.send(403,{'error':'Host not allowed'})
                return False
            return True
        def send(self,code,value):
            payload=json.dumps(value).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def do_GET(self):
            if not self.authorized(): return
            route=urlsplit(self.path).path
            if route=='/api/health': return self.send(200,dict(version='0.1.0',schemaVersion=1,adapter=status(store.path)))
            if route=='/api/notes': return self.send(200,store.list())
            if route=='/api/reminders': return self.send(200,store.reminders())
            if route=='/api/actions': return self.send(200,store.actions())
            if route=='/api/sources': return self.send(200,store.sources.list())
            if route.startswith('/api/sources/'):
                parts=route[len('/api/sources/'):].split('/')
                try:
                    if len(parts)==1: return self.send(200,store.sources.get(parts[0]))
                    if len(parts)==2 and parts[1]=='original':
                        body,media=store.sources.original(parts[0])
                        self.send_response(200); self.send_header('Content-Type',media if media.startswith('image/') else 'application/octet-stream'); self.send_header('Content-Disposition','attachment'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                        return
                except KeyError: return self.send(404,{'error':'Unknown source'})
                except OSError: return self.send(410,{'error':'The kept original is missing'})
            if route=='/api/progress':
                return self.send(200,json.loads((ROOT/'STATE/tasks.json').read_text(encoding='utf-8')))
            if route=='/api/hq/status':
                with store.connect() as db: return self.send(200,hq_snapshot.health(db))
            if route=='/api/hq/connection':
                with store.connect() as db: return self.send(200,hq_connect.connection(db))
            if route=='/api/hq/entities':
                with store.connect() as db: return self.send(200,{'entities':hq_snapshot.read_model(db)})
            if route=='/api/continue':
                return self.send(200,{'tasks':handoff.candidates(),'route':modules.resolve(modules.load_registry(ROOT/'modules',ROOT),'code.edit'),'handoffs':store.handoffs()})
            if route=='/api/hub/status': return self.send(200,hubclient.status())
            if route=='/api/sync': return self.send(200,store.sync_view())  # synced notes, outbox and conflicts (RA-013)
            if route=='/api/voice/local': return self.send(200,localvoice.status())  # Whisper on this computer's own hardware
            if route=='/api/hardware': return self.send(200,hardware_report())
            if route=='/api/update/status':
                pi=None
                hs=hubclient.status()
                if hs.get('reachable'):
                    code,body=hubclient.call('engine.system.update_status')
                    pi=body.get('result') if code==200 else {'error':body.get('error','The Pi cannot report its version yet (update it once by hand: ./hq.sh update).')}
                    pi={**pi,'hubCommit':hs.get('runningCommit') or hs.get('commit'),'diskCommit':hs.get('commit'),'restartNeeded':hs.get('restartNeeded',False)}
                return self.send(200,{'pc':{**selfupdate.status(),'canRestart':supervised},'pi':pi})
            if route.startswith('/api/engine/'):
                code,body=hubclient.call(route[len('/api/engine/'):],urlsplit(self.path).query)
                return self.send(code,body)
            if route=='/api/graph':
                try: return self.send(200,graph.build(ROOT))
                except ValueError as exc: return self.send(500,{'error':str(exc)})
            if route=='/api/modules':
                registry=modules.load_registry(ROOT/'modules',ROOT)
                return self.send(200,{'modules':list(registry['modules'].values())})
            if route.startswith('/api/capabilities/call/'):  # read capabilities, with provenance
                from urllib.parse import parse_qsl
                code,body=gateway().call(route[len('/api/capabilities/call/'):],dict(parse_qsl(urlsplit(self.path).query)))
                return self.send(code,body)
            if route=='/api/capabilities/calls': return self.send(200,{'calls':gateway().calls()})
            if route=='/api/capabilities/audit':
                with store.connect() as db: return self.send(200,capabilities.audit(db))
            if route=='/api/route':
                from urllib.parse import parse_qs
                try: return self.send(200,modules.resolve(modules.load_registry(ROOT/'modules',ROOT),parse_qs(urlsplit(self.path).query).get('intent',[''])[0]))
                except ValueError as exc: return self.send(400,{'error':str(exc)})
            if route=='/api/build-status':
                restart=versioning.restart_status(started['service'],release_source(),ROOT)
                return self.send(200,{**progress.read(hide_finished=True),'release':current_release()[0],'serverStartedAt':started['at'],
                    'restart':{**restart,'canRestart':supervised}})
            if route.startswith('/api/'): return self.send(404,{'error':'Unknown endpoint'})
            base=current_release()[1].resolve(); path=(base/route.lstrip('/')).resolve()
            if not path.is_relative_to(base): return self.send(403,{'error':'Invalid path'})
            if not path.is_file(): path=base/'index.html'
            if not path.exists(): return self.send(503,{'error':'Build the web app first with npm run build'})
            import mimetypes
            body=path.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(path)[0] or 'application/octet-stream'); self.send_header('Cache-Control','no-cache' if path.suffix=='.html' else 'public, max-age=31536000, immutable'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            if not self.authorized(): return
            origin=self.headers.get('Origin')
            allowed_origins=(public_origin,) if public_origin else (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
            if (public_origin and origin!=public_origin) or (origin and origin not in allowed_origins):
                return self.send(403,{'error':'Origin not allowed'})
            route=urlsplit(self.path).path
            if route=='/api/restart':
                if not supervised: return self.send(409,{'error':'Restart is only available when Atlas is started with Start Atlas.cmd.'})
                started['restartRequested']=True
                self.send(202,{'restarting':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if route.startswith('/api/voice/local/') or route=='/api/hardware/sync':
                try:
                    length=int(self.headers.get('Content-Length','0') or 0)
                    if length>(36_000_000 if route.endswith('/transcribe') else 10000): raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length)) if length else {}
                    if route=='/api/voice/local/transcribe':
                        import base64
                        audio=base64.b64decode(str(data.get('data','')),validate=True)
                        return self.send(200,{'text':localvoice.transcribe(audio,str(data.get('type','')),data.get('lang'))})
                    if route=='/api/voice/local/settings': return self.send(200,localvoice.save_settings(data))
                    if route=='/api/voice/local/install': return self.send(202,localvoice.install(None if data.get('gpu') is None else bool(data.get('gpu'))))
                    if route=='/api/voice/local/remove-gpu': return self.send(202,localvoice.remove_gpu_libraries())
                    if route=='/api/hardware/sync': return self.send(200,sync_hardware())
                    return self.send(404,{'error':'Unknown endpoint'})
                except PermissionError as exc: return self.send(403,{'error':str(exc)})
                except RuntimeError as exc: return self.send(503,{'error':str(exc)})
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route in ('/api/update/check','/api/update/apply'):
                out={'messages':[]}
                hub_ok=hubclient.status().get('reachable')
                if route=='/api/update/check':
                    pc=selfupdate.check()
                    if hub_ok:
                        code,body=hubclient.act('engine.system.updates',{'check':True})
                        out['messages'].append('Pi: '+(body.get('message') or ('needs one update by hand first (see above).' if code==404 else body.get('error',''))))
                    out['messages'].insert(0,'This computer: '+(pc['error'] or (f"{pc['behind']} new change(s)." if pc['behind'] else 'up to date.'))+(' '+pc['blocker'] if pc['blocker'] else ''))
                    return self.send(200,out)
                if hub_ok:  # the Pi first: its tests take minutes and run on their own
                    code,body=hubclient.act('engine.system.updates',{})
                    out['messages'].append('Pi: '+(body.get('message') or body.get('error','')))
                else:
                    out['messages'].append('Pi: not reachable; it updates itself within 15 minutes.')
                try: installed,msg=selfupdate.apply()
                except (RuntimeError,OSError) as exc: installed,msg=False,f'This computer: update failed: {exc}'
                out['messages'].append(msg)
                out['restarting']=bool(installed and supervised)
                if installed and not supervised: out['messages'].append('Close Atlas and open Start Atlas.cmd again to use the new version.')
                self.send(200,out)
                if out['restarting']:
                    started['restartRequested']=True
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if route.startswith('/api/stream/'):  # the Cortex chat, word by word (RA-083)
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<2 or length>20000: raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    if not isinstance(data,dict): raise ValueError('Send a JSON object')
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.send_header('Cache-Control','no-store');self.end_headers()
                try:
                    for line in hubclient.stream(route[len('/api/stream/'):],data):
                        self.wfile.write(line);self.wfile.flush()
                except OSError: pass  # the page closed: the Engine still finishes and keeps the answer
                self.close_connection=True
                return
            if route.startswith('/api/engine/'):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<2 or length>(15_000_000 if route.endswith(('engine.brain.ingest','engine.brain.voice_input','engine.brain.mockup','engine.brain.chat_file')) else 20000): raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    if not isinstance(data,dict): raise ValueError('Send a JSON object')
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
                code,body=hubclient.act(route[len('/api/engine/'):],data)
                return self.send(code,body)
            if route in ('/api/capabilities/preview','/api/capabilities/confirm'):  # propose: preview, then confirm once
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<2 or length>capabilities.MAX_INPUT+1000: raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    if not isinstance(data,dict): raise ValueError('Send a JSON object')
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
                if route=='/api/capabilities/preview': code,body=gateway().preview(data.get('id'),data.get('inputs',{}))
                else: code,body=gateway().confirm(data.get('callId'),data.get('repeat') is True)
                return self.send(code,body)
            if route=='/api/hq/sync':  # read-only: one GET of the Engine's snapshot export, imported into Atlas's cache
                with store.connect() as db: return self.send(200,hq_connect.sync(db))
            if route in ('/api/hub/config','/api/hub/forget'):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<1 or length>10000: raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    if route=='/api/hub/forget': hubclient.forget(); return self.send(200,{'configured':False})
                    return self.send(200,hubclient.configure(data.get('url'),data.get('token')))
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route=='/api/sources/resume': return self.send(200,{'processed':store.sources.resume()})
            if route=='/api/sources':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<2 or length>14_000_000: raise ValueError('Invalid request size')
                    result=store.sources.add_base64(json.loads(self.rfile.read(length)))
                    return self.send(200 if result['duplicate'] else 201,result)
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route.startswith('/api/sources/') and route.endswith('/retry'):
                try: return self.send(200,{'status':store.sources.retry(route[len('/api/sources/'):-len('/retry')])})
                except KeyError: return self.send(404,{'error':'Unknown source'})
            if route.startswith('/api/sync/'):  # /api/sync/run | notes | notes/<id>/delete | conflicts/<command>/<mine|theirs>
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length>sync.MAX_BODY+1000: raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length)) if length else {}
                    parts=route[len('/api/sync/'):].split('/')
                    if parts==['run']: return self.send(200,store.sync_now())
                    with store.connect() as db:
                        if parts==['notes']: sync.enqueue(db,data.get('id') or str(uuid.uuid4()),'upsert',data.get('body'))
                        elif len(parts)==3 and parts[0]=='notes' and parts[2]=='delete': sync.enqueue(db,parts[1],'delete')
                        elif len(parts)==3 and parts[0]=='conflicts': sync.resolve(db,parts[1],parts[2])
                        else: return self.send(404,{'error':'Unknown endpoint'})
                    return self.send(200,store.sync_view())
                except KeyError as exc: return self.send(404,{'error':str(exc).strip("'")})
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route=='/api/handoff':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<1 or length>10000: raise ValueError('Invalid request size')
                    data=json.loads(self.rfile.read(length))
                    return self.send(201,store.handoff(data['taskId'],data['tool']))
                except (ValueError,KeyError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route.startswith('/api/actions/') and route.count('/')==4:  # /api/actions/<id>/step|input|done
                identifier,what=route.split('/')[3:5]
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<1 or length>100000: raise ValueError('Invalid request size')
                    store.update_action(identifier,what,json.loads(self.rfile.read(length)))
                    return self.send(200,{'id':identifier})
                except KeyError as exc: return self.send(404,{'error':str(exc).strip("'")})
                except (ValueError,TypeError) as exc: return self.send(400,{'error':str(exc)})
            if route!='/api/notes' and route!='/api/reminders' and not (route.startswith('/api/reminders/') and route.endswith('/done')):
                return self.send(404,{'error':'Unknown endpoint'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<1 or length>100000: raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(length))
                if route=='/api/notes': return self.send(201,{'id':store.save(data['body'],data['id'])})
                if route=='/api/reminders': return self.send(201,{'id':store.add_reminder(data['id'],data['title'],data.get('detail',''))})
                identifier=route[len('/api/reminders/'):-len('/done')]; done=data['done']
                if not isinstance(done,bool): raise ValueError('done must be true or false')
                try: store.set_reminder_done(identifier,done)
                except KeyError: return self.send(404,{'error':'Unknown reminder'})
                self.send(200,{'id':identifier,'done':done})
            except (ValueError,KeyError,TypeError) as exc: self.send(400,{'error':str(exc)})
    def hardware_report():
        from engine import hardware
        return hardware.detect()
    def sync_hardware():
        """This computer as a live resource in the Engine (through the hub); the Pi reports itself."""
        report=hardware_report()
        if report.get('role')=='pi': return {'report':report,'message':'This is the Pi: it reports itself every hour.'}
        code,body=hubclient.act('engine.assets.report_hardware',{'report':report})
        return {'report':report,'message':body.get('message') if code==200 else (body.get('error') or 'The hub is not reachable; try again later.')}
    httpd_sync=sync_hardware
    class Server(ThreadingHTTPServer):
        def handle_error(self,request,client_address):
            if isinstance(sys.exc_info()[1],(BrokenPipeError,ConnectionResetError)): return  # the page went away mid-request (reload, restart)
            super().handle_error(request,client_address)
    httpd=Server(('127.0.0.1',port),Handler)
    httpd.atlas_started=started
    httpd.sync_hardware=httpd_sync
    return httpd
if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=4310); parser.add_argument('--db',default=str(ROOT/'runtime/atlas.db')); args=parser.parse_args()
    server=make_server(args.port,args.db,supervised=os.environ.get('ATLAS_SUPERVISED')=='1')
    selfupdate.watch()  # checks GitHub every 10 minutes, so the screen can show that an update is waiting
    def report_daily():  # this computer's hardware as a live resource: at start, then once a day
        import time
        while True:
            try: server.sync_hardware()
            except Exception: pass
            time.sleep(86400)
    threading.Thread(target=report_daily,daemon=True,name='atlas-hardware-report').start()
    print(f'Reasoning Atlas: http://127.0.0.1:{server.server_port}',flush=True)
    server.serve_forever(); server.server_close()
    if server.atlas_started['restartRequested']:
        print('Restarting Atlas…',flush=True); sys.exit(RESTART_EXIT_CODE)
