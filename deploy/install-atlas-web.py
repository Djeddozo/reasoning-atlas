"""Install the existing screen on this Pi after npm run build.

Run from the repository with PYTHONPATH=. python deploy/install-atlas-web.py.
Add the private HTTPS route separately as documented in docs/MOBILE_PI.md.
"""
import json,os,subprocess,shutil
from pathlib import Path
from engine import envfile
from services.atlas import hubclient
root=Path(__file__).resolve().parents[1]
os.chdir(root)
s=json.loads(subprocess.check_output(['tailscale','status','--json']))
login=s['User'][str(s['Self']['UserID'])]['LoginName']
name=s['Self']['DNSName'].rstrip('.')
origin=f'https://{name}:4310'
runtime=root/'runtime'; runtime.mkdir(exist_ok=True)
p=runtime/'web.env'; p.write_text(f'ATLAS_WEB_ORIGIN={origin}\nATLAS_WEB_LOGIN={login}\n'); p.chmod(0o600)
url=f"http://{envfile.read_value('ATLAS_HUB_HOST')}:4320"
config={'url':url,'token':envfile.read_value('ATLAS_HUB_TOKEN')}
assert hubclient.check(config)['reachable'], 'Existing Engine API must answer first'
if hubclient.CONFIG.exists(): shutil.copy2(hubclient.CONFIG,runtime/'hub.pre-mobile.json')
hubclient._save(config)
unit=Path.home()/'.config/systemd/user/atlas-web.service'
if unit.exists(): shutil.copy2(unit,runtime/'atlas-web.service.before')
unit.write_text((root/'deploy/atlas-web.service').read_text().replace('%h/hq-system',str(root)))  # the real folder (RA-042)
# HTTPS Serve already owns the tailnet port 8770. The backend belongs on loopback.
if not (runtime/'env.pre-mobile').exists():
    shutil.copy2(root/'.env',runtime/'env.pre-mobile')
    (runtime/'env.pre-mobile').chmod(0o600)
envfile.set_value('HQ_HOST','127.0.0.1')
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','--now','atlas-web.service'],check=True)
subprocess.run(['systemctl','--user','restart','hq.service'],check=True)
print('Atlas web service installed:',origin)
