import json, os, subprocess, sys, tempfile, time, unittest, urllib.request, uuid
from services.atlas.server import Store
class Storage(unittest.TestCase):
    def test_transaction_and_duplicate_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder+'/notes.db'); identifier=str(uuid.uuid4())
            store.save('Original note',identifier); store.save('Original note',identifier)
            self.assertEqual(len(store.list()),1)
            with self.assertRaises(ValueError): store.save('Overwrite',identifier)
            self.assertEqual(store.list()[0]['body'],'Original note')
    def test_killed_service_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            def start():
                p=subprocess.Popen([sys.executable,'-m','services.atlas.server','--port','0','--db',folder+'/notes.db'],stdout=subprocess.PIPE,text=True)
                url=p.stdout.readline().strip().split()[-1]; return p,url
            p,url=start()
            try:
                self.assertEqual(json.load(urllib.request.urlopen(url+'/api/health'))['adapter']['connected'],False)
                data=json.dumps({'id':str(uuid.uuid4()),'body':'Survives abrupt process termination'}).encode()
                urllib.request.urlopen(urllib.request.Request(url+'/api/notes',data=data,headers={'Content-Type':'application/json'})).close()
            finally: p.kill(); p.wait(); p.stdout.close()
            p,url=start()
            try: self.assertEqual(json.load(urllib.request.urlopen(url+'/api/notes'))[0]['body'],'Survives abrupt process termination')
            finally: p.kill(); p.wait(); p.stdout.close()
