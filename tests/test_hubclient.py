import json, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock
from services.atlas import hub, hubclient, server
from tests.test_hub import TOKEN, engine_database


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=15))


class HubClient(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = Path(self.folder.name) / "hub.json"
        self.patch = mock.patch.object(hubclient, "CONFIG", self.config)
        self.patch.start()
        hubclient._cache.update(at=0.0, value=None)
        self.hub = hub.make_server("127.0.0.1", 0, TOKEN, engine_database(self.folder.name))
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()
        self.hub_url = f"http://127.0.0.1:{self.hub.server_port}"

    def tearDown(self):
        self.hub.shutdown(); self.hub.server_close()
        self.patch.stop()
        self.folder.cleanup()

    def test_not_configured(self):
        self.assertEqual(hubclient.status(), {"configured": False, "reachable": False})
        self.assertEqual(hubclient.call("engine.attention.needs")[0], 409)

    def test_rejects_bad_input(self):
        for url, token in (("ftp://pi:4320", TOKEN), ("not a url", TOKEN), (self.hub_url, "short")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                hubclient.configure(url, token)

    def test_connect_call_and_forget(self):
        result = hubclient.configure(self.hub_url + "/some/path", TOKEN)
        self.assertTrue(result["reachable"])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(json.loads(self.config.read_text())["url"], self.hub_url)
        code, body = hubclient.call("engine.attention.terminal_steps")
        self.assertEqual((code, body["result"][0]["command"]), (200, "./hq.sh bot"))
        self.assertEqual(hubclient.call("engine.research.find_options")[0], 404)
        hubclient.forget()
        self.assertFalse(self.config.exists())

    def test_wrong_token_is_not_saved(self):
        result = hubclient.configure(self.hub_url, "wrong-token-0123456789abcdef")
        self.assertEqual((result["reachable"], result["error"]), (False, "The hub refused the token"))
        self.assertFalse(self.config.exists())

    def test_act(self):
        self.assertEqual(hubclient.act("engine.attention.finish_terminal_step", {"id": 1})[0], 409)
        hubclient.configure(self.hub_url, TOKEN)
        code, body = hubclient.act("engine.attention.finish_terminal_step", {"id": 1, "status": "done"})
        self.assertEqual(code, 200)
        self.assertEqual(hubclient.call("engine.attention.terminal_steps")[1]["result"], [])
        self.assertEqual(hubclient.act("engine.attention.finish_terminal_step", {"id": 1, "status": "done"})[0], 400)
        self.assertEqual(hubclient.act("engine.attention.needs", {"x": 1})[0], 404)

    def test_hub_down(self):
        hubclient.configure(self.hub_url, TOKEN)
        self.hub.shutdown(); self.hub.server_close()
        self.assertFalse(hubclient.status(max_age=0)["reachable"])
        self.assertEqual(hubclient.call("engine.attention.needs")[0], 504)
        self.assertEqual(hubclient.act("engine.attention.finish_terminal_step", {"id": 1, "status": "done"})[0], 504)
        self.hub = hub.make_server("127.0.0.1", 0, TOKEN, Path(self.folder.name) / "hq.db")  # a running one for tearDown
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()

    def test_through_atlas_service(self):
        atlas = server.make_server(0, self.folder.name + "/atlas.db")
        threading.Thread(target=atlas.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{atlas.server_port}"
        try:
            self.assertFalse(json.load(urllib.request.urlopen(url + "/api/hub/status"))["configured"])
            status = post(url + "/api/hub/config", {"url": self.hub_url, "token": TOKEN})
            self.assertTrue(status["reachable"])
            self.assertNotIn(TOKEN, json.dumps(json.load(urllib.request.urlopen(url + "/api/hub/status"))))
            needs = json.load(urllib.request.urlopen(url + "/api/engine/engine.attention.needs"))
            self.assertEqual(needs["result"][0]["title"], "Spend 5 euro on a domain")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(url + "/api/hub/config", {"url": "ftp://x", "token": TOKEN})
            self.assertEqual(ctx.exception.code, 400)
            answered = post(url + "/api/engine/engine.attention.answer_needs", {"key": "A1", "verdict": "denied", "text": "not now"})
            self.assertIn("#1", answered["message"])
            self.assertEqual(json.load(urllib.request.urlopen(url + "/api/engine/engine.attention.needs"))["result"], [])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post(url + "/api/engine/engine.attention.answer_needs", [1, 2])
            self.assertEqual(ctx.exception.code, 400)
            self.assertFalse(post(url + "/api/hub/forget", {})["configured"])
        finally:
            atlas.shutdown(); atlas.server_close()
