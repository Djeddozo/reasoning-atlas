import base64, json, os, subprocess, sys, tempfile, threading, unittest, urllib.request
from pathlib import Path
from unittest import mock

from engine import chat, cortex, db, models
from services.atlas import hub
from tests.test_hub import TOKEN, post

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
log = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["atlas"]["env"]
open(log["CORTEX_TURN"], "a").write(json.dumps({"type": "proposal", "label": "Mark #1 done", "action": "engine.build.task_status", "body": {"task": 1, "status": "done"}}) + "\n")
open(log["CORTEX_TURN"], "a").write(json.dumps({"type": "proposal", "label": "Log in", "action": "engine.build.claude_login", "body": {}}) + "\n")
open(log["CORTEX_TURN"], "a").write(json.dumps({"type": "mockup", "title": "Weekly panel", "brief": "bars"}) + "\n")
resumed = "--resume" in args
side = open(args[args.index("--mcp-config") + 1] + ".seen", "w"); side.write(json.dumps({"args": args, "prompt": prompt})); side.close()
out = lambda o: print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "session_id": "sess-1"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__atlas__find", "input": {"query": "bank"}}]}})
for piece in (["Again: " if resumed else "", "Start with ", "#1."]):
    if piece:
        out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})
out({"type": "result", "subtype": "success", "session_id": "sess-1", "result": "Start with #1.", "usage": {"input_tokens": 10, "output_tokens": 5}})
'''


def database(folder):
    path = Path(folder) / "hq.db"
    con = db.connect(path)
    db.init(con)
    return con, path


class Cortex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con, self.path = database(self.tmp.name)
        db.insert(self.con, "tasks", title="Call the bank", status="todo", owner="me", score=3)
        self.con.commit()
        fake = Path(self.tmp.name) / "claude"
        fake.write_text(FAKE.replace("#!/usr/bin/env python3", "#!" + sys.executable))
        fake.chmod(0o755)
        self.patches = [mock.patch.object(cortex.devloop, "claude_path", return_value=str(fake)),
                        mock.patch.object(cortex, "FILES", Path(self.tmp.name) / "files"),
                        mock.patch.dict(os.environ, {"HQ_NO_CLAUDE_CODE": ""}),
                        mock.patch.object(cortex, "_settings", return_value={"claude_code_model": "opus", "thinking_tokens": 3000})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.con.close()
        self.tmp.cleanup()

    def test_a_turn_streams_resumes_and_proposes(self):
        events = []
        catalog = hub.action_catalog()
        seen = {}
        real_unlink = Path.unlink

        def keep_seen(p, missing_ok=False):  # read what the fake Claude received before the temp files go
            side = Path(str(p) + ".seen")
            if side.exists():
                seen.update(json.loads(side.read_text()))
                real_unlink(side)
            real_unlink(p, missing_ok=missing_ok)
        with mock.patch.object(Path, "unlink", keep_seen):
            out = cortex.run(self.con, "What first?", events.append, ["Today"], catalog)
        self.assertEqual(out["reply"], "Start with #1.")
        self.assertEqual([e["d"] for e in events if e["t"] == "text"], ["Start with ", "#1."])
        self.assertIn("Looking in your plan: bank", [e["d"] for e in events if e["t"] == "tool"])
        self.assertEqual([p["label"] for p in out["proposals"]], ["Mark #1 done"])  # the login is not proposable
        self.assertEqual(out["mockup"]["title"], "Weekly panel")
        args = seen["args"]
        self.assertIn("opus", args)
        self.assertNotIn("--resume", args)
        self.assertIn("Bash,Edit,Write,NotebookEdit,Task", args)
        self.assertIn("Call the bank", args[args.index("--system-prompt") + 1])
        self.assertEqual(db.get_state(self.con, "cortex_session_0", ""), "sess-1")
        self.assertEqual([r["role"] for r in chat.history(self.con)], ["me", "claude"])
        with mock.patch.object(Path, "unlink", keep_seen):
            again = cortex.run(self.con, "And then?", None, ["Today"], catalog)
        self.assertIn("--resume", seen["args"])
        self.assertEqual(seen["prompt"], "And then?")  # real turns: the session carries the conversation
        self.assertTrue(again["reply"].startswith("Again:"))

    def test_files_and_new_conversation_opening(self):
        name = cortex.attach(self.con, "my plan.pdf", b"%PDF-1.4 x")
        self.assertEqual(cortex.attach(self.con, "my plan.pdf", b"%PDF-1.4 y"), "my plan-1.pdf")
        self.assertTrue((cortex.folder(0) / name).exists())
        with self.assertRaises(ValueError):
            cortex.attach(self.con, "big", b"x" * (cortex.MAX_FILE + 1))
        chat.add(self.con, "me", "earlier question")
        chat.add(self.con, "claude", "earlier answer")
        chat.add(self.con, "me", "new one")
        self.assertIn("earlier answer", cortex._opening(self.con, 0, "new one"))


class Tools(unittest.TestCase):
    def test_mcp_server_reads_and_only_proposes(self):
        with tempfile.TemporaryDirectory() as folder:
            con, path = database(folder)
            tid = db.insert(con, "tasks", title="Renew the mortgage", description="call the bank", status="todo", owner="me")
            chat.add(con, "me", "we talked about the mortgage before")
            con.commit()
            turn, cat = Path(folder) / "turn.jsonl", Path(folder) / "cat.json"
            cat.write_text(json.dumps({"engine.build.task_status": '{"task": id, "status": "done"}'}))
            env = {**os.environ, "HQ_DB": str(path), "CORTEX_TURN": str(turn), "CORTEX_CATALOG": str(cat), "CORTEX_CONV": "5"}
            msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "find", "arguments": {"query": "mortgage"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read", "arguments": {"kind": "task", "id": tid}}},
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "past_conversations", "arguments": {"query": "mortgage"}}},
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "propose_action", "arguments": {"label": "Done", "action": "engine.build.task_status", "body": {"task": tid, "status": "done"}}}},
                    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "read", "arguments": {"kind": "secret", "id": 1}}}]
            r = subprocess.run([sys.executable, str(ROOT / "engine" / "cortex_mcp.py")], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                               capture_output=True, text=True, env=env, timeout=30)
            replies = {x["id"]: x for x in map(json.loads, r.stdout.splitlines())}
            self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "atlas")
            names = [t["name"] for t in replies[2]["result"]["tools"]]
            self.assertIn("propose_action", names)
            self.assertIn("engine.build.task_status", [t for t in replies[2]["result"]["tools"] if t["name"] == "propose_action"][0]["description"])
            self.assertIn("Renew the mortgage", replies[3]["result"]["content"][0]["text"])
            self.assertIn("call the bank", replies[4]["result"]["content"][0]["text"])
            self.assertIn("we talked about the mortgage", replies[5]["result"]["content"][0]["text"])
            self.assertTrue(replies[7]["result"]["isError"])
            self.assertEqual(json.loads(turn.read_text())["label"], "Done")
            self.assertEqual(con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "todo")  # nothing ran


class EngineTools(unittest.TestCase):
    """#180: the Engine's read functions as Cortex tools, on a read-only connection."""
    def call(self, path, name, args):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}]
        r = subprocess.run([sys.executable, str(ROOT / "engine" / "cortex_mcp.py")], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, env={**os.environ, "HQ_DB": str(path), "CORTEX_TURN": os.devnull}, timeout=60)
        out = [json.loads(x) for x in r.stdout.splitlines()][-1]["result"]
        return out, out["content"][0]["text"]

    def test_arsenal_overlap_priority_delivery(self):
        from engine import arsenal
        with tempfile.TemporaryDirectory() as folder:
            con, path = database(folder)
            dep = db.insert(con, "tasks", title="Set up the voice model", status="todo", owner="ai", impact=3, effort_h=2)
            tid = db.insert(con, "tasks", title="Weekly progress panel on Today", description="bars per goal", status="todo",
                            owner="ai", impact=4, effort_h=4, depends_on=str(dep))
            db.insert(con, "tasks", title="Weekly progress panel on Today", status="done", owner="ai")
            db.insert(con, "prs", number=31, title="Weekly panel", url="https://x/31", stage="install", task_ids=str(tid), link="closes")
            proj = Path(folder) / "proj"
            proj.mkdir()
            (proj / "panel.py").write_text("def weekly_progress_panel(goals):\n    \"\"\"Bars per goal.\"\"\"\n    return goals\n")
            arsenal.add_project(con, str(proj), "proj")
            arsenal.scan(con)
            con.commit()
            changes = con.total_changes
            out, text = self.call(path, "arsenal_find", {"query": "weekly progress panel"})
            self.assertIn("weekly_progress_panel", text)
            out, text = self.call(path, "overlap", {"kind": "task", "id": tid})
            self.assertEqual(json.loads(text)["verdict"], "exists")  # a done task with the same title
            out, text = self.call(path, "priority", {"task": tid})
            p = json.loads(text)
            self.assertEqual(p["blocked_reason"], f"waits for #{dep}")
            self.assertEqual(p["parts"]["impact"], 4)
            self.assertIn("problem", p["briefing"])
            out, text = self.call(path, "delivery", {"task": tid})
            self.assertEqual(json.loads(text)[0]["number"], 31)
            out, _ = self.call(path, "overlap", {"kind": "task", "id": 999})
            self.assertTrue(out["isError"])
            self.assertEqual(con.total_changes, changes)
            self.assertIsNone(con.execute("SELECT 1 FROM overlap_checks").fetchone())  # nothing stored by a look-up


class Stream(unittest.TestCase):
    def test_hub_streams_events_then_done(self):
        with tempfile.TemporaryDirectory() as folder:
            con, path = database(folder)
            con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}"

            def fake_run(con, text, emit, views, catalog, actions=None):
                emit({"t": "text", "d": "Hel"})
                emit({"t": "text", "d": "lo"})
                return {"reply": "Hello", "proposals": [], "choices": [], "mockup": None, "session": "s"}
            try:
                with mock.patch.object(cortex, "available", return_value=True), mock.patch.object(cortex, "run", side_effect=fake_run), \
                        mock.patch.object(cortex, "FILES", Path(folder) / "files"):
                    req = urllib.request.Request(base + "/v1/stream/engine.brain.chat_stream", data=json.dumps({"text": "hi"}).encode(),
                                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
                    events = [json.loads(line) for line in urllib.request.urlopen(req, timeout=10)]
                    f = post(base + "/v1/actions/engine.brain.chat_file", {"name": "a.txt", "data": base64.b64encode(b"hello").decode()})
                self.assertEqual([e["t"] for e in events], ["text", "text", "done"])
                self.assertEqual(events[-1]["d"]["message"]["reply"], "Hello")
                self.assertEqual(f["message"]["name"], "a.txt")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
