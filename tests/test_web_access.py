"""Remote screens require Serve identity, exact host, and same-origin writes."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from services.atlas.server import make_server


class WebAccessTests(unittest.TestCase):
    def test_remote_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            server=make_server(0,folder+'/test.db',public_origin='https://atlas.example:4310',tailnet_login='owner@example.com')
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            def request(headers,body=None):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/notes',data=body,headers=headers)
                try:
                    with urllib.request.urlopen(req) as response: return response.status
                except urllib.error.HTTPError as exc: return exc.code
            try:
                valid={'Host':'atlas.example:4310','Tailscale-User-Login':'owner@example.com'}
                self.assertEqual(request({}),403)
                self.assertEqual(request({'Host':'atlas.example:4310'}),403)
                self.assertEqual(request({**valid,'Tailscale-User-Login':'other@example.com'}),403)
                self.assertEqual(request({**valid,'Host':'evil.example'}),403)
                self.assertEqual(request(valid),200)
                body=json.dumps({'id':'b3bf8763-ff04-4d60-a045-b60cd349c991','body':'Synthetic mobile note'}).encode()
                self.assertEqual(request(valid,body),403)
                self.assertEqual(request({**valid,'Origin':'https://evil.example'},body),403)
                self.assertEqual(request({**valid,'Origin':'https://atlas.example:4310'},body),201)
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_remote_requires_login(self):
        with self.assertRaises(ValueError):
            make_server(0,'unused.db',public_origin='https://atlas.example')
