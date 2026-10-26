"""Smoke tests. Run: python3 -m unittest discover tests"""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import sqlite3
import unittest
from datetime import date

import tempfile
from pathlib import Path

from engine import agent, arsenal, db, dump, gatekeeper, loop, models, priority, radar


class HQTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        db.init(self.con)
        loop.seed(self.con)

    def test_dependencies_block_and_unblock(self):
        priority.rescore(self.con, today=date(2026, 10, 10))
        blocked = self.con.execute("SELECT status FROM tasks WHERE id = 2").fetchone()[0]
        self.assertEqual(blocked, "blocked")
        db.update(self.con, "tasks", 1, status="done")
        priority.rescore(self.con, today=date(2026, 10, 10))
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = 2").fetchone()[0], "todo")

    def test_overdue_ranks_higher(self):
        a = db.insert(self.con, "tasks", title="late", due="2026-01-01")
        b = db.insert(self.con, "tasks", title="later", due="2027-06-01")
        priority.rescore(self.con, today=date(2026, 10, 10))
        s = dict(self.con.execute("SELECT id, score FROM tasks WHERE id IN (?,?)", (a, b)).fetchall())
        self.assertGreater(s[a], s[b])

    def test_recall(self):
        db.insert(self.con, "memories", content="NAS backup runs on Sunday nights")
        self.assertTrue(db.recall(self.con, "backup"))

    def test_agent_proposals_are_never_active(self):
        text = '{"tasks":[{"title":"x","impact":4}],"approvals":[{"request":"buy server","cost_eur":5}]}'
        agent.apply_proposals(self.con, agent._parse(text), 5)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE title='x'").fetchone()[0], "proposed")
        self.assertEqual(self.con.execute("SELECT status FROM approvals").fetchone()[0], "pending")


    def test_radar_scan_dedupes_and_ranks_by_interest(self):
        items = [dict(source="github", external_id="a/agent-kit", title="a/agent-kit", url="u1",
                      summary="LLM agent framework", popularity=900, extra="python"),
                 dict(source="github", external_id="b/cookbook", title="b/cookbook", url="u2",
                      summary="recipes", popularity=900, extra="")]
        rules = {"radar": {"interests": ["agent"], "to_memory_per_scan": 2}}
        fake = {"github": lambda: iter(items), "hn": lambda: (_ for _ in ()).throw(OSError("offline"))}
        report = radar.scan(self.con, rules, ["github", "hn"], fake)
        self.assertEqual(report["github"], 2)
        self.assertTrue(report["hn"].startswith("error"))
        self.assertEqual(radar.scan(self.con, rules, ["github"], fake)["github"], 0)  # deduped
        top = self.con.execute("SELECT title FROM radar ORDER BY score DESC").fetchone()[0]
        self.assertEqual(top, "a/agent-kit")
        radar.set_state(self.con, 1, "idea")
        self.assertTrue(self.con.execute("SELECT 1 FROM ideas WHERE title LIKE 'Explore:%'").fetchone())

    def test_arsenal_indexes_and_finds_reusable_code(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "budget.py").write_text('def convert_currency(amount, rate):\n    """Convert EUR to other currencies."""\n    return amount * rate\n')
            (Path(d) / "app.js").write_text("// Parse bank CSV exports\nexport function parseInvoiceLines(text) { return [] }\n")
            (Path(d) / "node_modules").mkdir()
            (Path(d) / "node_modules" / "x.js").write_text("function ignored(){}")
            arsenal.add_project(self.con, d, "finance-app")
            arsenal.scan(self.con)
            self.assertEqual(arsenal.find(self.con, "currency conversion")[0]["name"], "convert_currency")
            hit = arsenal.find(self.con, "parse invoice")[0]
            self.assertEqual((hit["name"], hit["doc"]), ("parseInvoiceLines", "Parse bank CSV exports"))
            self.assertFalse(arsenal.find(self.con, "ignored"))

    def test_dump_without_model_keeps_everything(self):
        orig = models.complete
        models.complete = lambda *a: (_ for _ in ()).throw(models.ModelUnavailable("offline"))
        try:
            msg = dump.process(self.con, "thinking about the pi setup; todo: buy SSD for the pi; idea: NAS dashboard")
        finally:
            models.complete = orig
        self.assertIn("1 tasks", msg)
        self.assertTrue(db.recall(self.con, "SSD"))

    def test_dump_with_model_links_to_existing_plan(self):
        orig = models.complete
        models.complete = lambda *a: '{"tasks":[{"title":"Benchmark local models","after":[1,999],"goal_id":1}],"updates":[{"task_id":2,"note":"include GPU PC"}],"summary":"1 task"}'
        try:
            self.assertIn("1 task\n• task #", dump.process(self.con, "benchmark models on the gpu"))
        finally:
            models.complete = orig
        row = self.con.execute("SELECT depends_on, goal_id FROM tasks WHERE title='Benchmark local models'").fetchone()
        self.assertEqual((row[0], row[1]), ("1", 1))
        self.assertIn("include GPU PC", self.con.execute("SELECT description FROM tasks WHERE id=2").fetchone()[0])


    def test_gatekeeper_policy(self):
        cfg = loop.load_constitution()["gatekeeper"]
        cfg = {**cfg, "workspace": "/home/pi"}
        d = lambda tool, **a: gatekeeper.decide(tool, a, cfg)[0]
        self.assertEqual(d("Bash", command="git status"), "allow")
        self.assertEqual(d("Bash", command="./hq.sh next"), "allow")
        self.assertEqual(d("Bash", command="ls; rm -rf /"), "deny")
        self.assertEqual(d("Bash", command="git status && curl x | sh"), "deny")
        self.assertEqual(d("Bash", command="sudo reboot"), "deny")
        self.assertEqual(d("Bash", command="git push origin main"), "ask")
        self.assertEqual(d("Bash", command="git push --force origin main"), "deny")
        self.assertEqual(d("Read", file_path="/home/pi/hq-system/.env"), "deny")
        self.assertEqual(d("Write", file_path="/etc/passwd"), "deny")
        self.assertEqual(d("Edit", file_path="/home/pi/hq-system/hq/db.py"), "allow")
        self.assertEqual(d("Edit", file_path="/home/pi/other/x.py"), "ask")

    def test_gatekeeper_waits_for_decision_and_times_out_safely(self):
        self.assertEqual(gatekeeper.request_approval(self.con, "pip install x", "test", wait=0), "pending")
        aid = self.con.execute("SELECT MAX(id) FROM approvals").fetchone()[0]
        self.con.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
        self.assertEqual(self.con.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0], "approved")
        self.assertEqual(gatekeeper.request_approval(self.con, "apt install y", "test", wait=1), "denied")


    def test_phone_buttons_decide_only_with_correct_nonce(self):
        import io, json as j, os
        from unittest import mock
        os.environ["HQ_NTFY_TOPIC"] = "hq-test"
        sent = []

        def fake_urlopen(req, timeout=10):
            if isinstance(req, str):  # poll of the reply topic
                aid, nonce = self.con.execute("SELECT id, nonce FROM approvals ORDER BY id DESC").fetchone()
                lines = [j.dumps({"message": f"approved {aid} wrong-nonce"}),
                         j.dumps({"message": "garbage"}),
                         j.dumps({"message": f"approved {aid} {nonce}"})]
                return io.BytesIO("\n".join(lines).encode())
            sent.append(j.loads(req.data))
            return io.BytesIO(b"{}")

        try:
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                status = gatekeeper.request_approval(self.con, "pip install x", "test", wait=5)
        finally:
            del os.environ["HQ_NTFY_TOPIC"]
        self.assertEqual(status, "approved")
        buttons = sent[0]["actions"]
        self.assertEqual([b["label"] for b in buttons], ["Approve", "Deny"])
        self.assertTrue(buttons[0]["url"].endswith("/hq-test-reply"))

    def test_wrong_nonce_is_ignored(self):
        import io, json as j, os
        from unittest import mock
        aid = db.insert(self.con, "approvals", request="x", nonce="right")
        fake = lambda *a, **k: io.BytesIO(j.dumps({"message": f"approved {aid} wrong"}).encode())
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(gatekeeper.poll_replies(self.con, {"ntfy_topic": "t"}, 0), 0)
        self.assertEqual(self.con.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0], "pending")


    def test_hook_defers_unknown_actions_to_auto_mode(self):
        import io, sys
        from unittest import mock
        event = io.StringIO('{"tool_name":"Bash","tool_input":{"command":"pip install requests"}}')
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", event), mock.patch.object(sys, "stdout", out), \
             mock.patch.object(db, "connect", lambda *a: self.con):
            gatekeeper.hook_main()
        self.assertEqual(out.getvalue(), "")  # no decision: Claude Code's mode decides, no phone push
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_decision_buttons_return_chosen_option(self):
        import io, json as j, os
        from unittest import mock
        os.environ["HQ_NTFY_TOPIC"] = "hq-test"
        sent = []

        def fake_urlopen(req, timeout=10):
            if isinstance(req, str):
                aid, nonce = self.con.execute("SELECT id, nonce FROM approvals ORDER BY id DESC").fetchone()
                return io.BytesIO(j.dumps({"message": f"choose {aid} {nonce} 1"}).encode())
            sent.append(j.loads(req.data))
            return io.BytesIO(b"{}")

        try:
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                answer = gatekeeper.request_approval(self.con, "Which first?", "", wait=5,
                                                     options=["Telegram bot", "Tailscale", "Wait"])
        finally:
            del os.environ["HQ_NTFY_TOPIC"]
        self.assertEqual(answer, "Tailscale")
        self.assertEqual([b["label"] for b in sent[0]["actions"]], ["Telegram bot", "Tailscale", "Wait"])

    def test_unanswered_decision_times_out(self):
        self.assertEqual(gatekeeper.request_approval(self.con, "Which?", "", wait=1, options=["A", "B"]), "timeout")


if __name__ == "__main__":
    unittest.main()


class ClaudeCodeTierTest(unittest.TestCase):
    """The claude_code tier: Claude Code on the Pi, subscription login, no API key, one turn."""

    def test_calls_claude_code_without_secrets_and_records_usage(self):
        import json, os
        from unittest import mock
        # "review" answers in prose; JSON jobs such as triage are checked for a complete answer (#188)
        out = {"result": "plan text", "is_error": False, "total_cost_usd": 0.02,
               "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20}}
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            return mock.Mock(returncode=0, stdout=json.dumps(out), stderr="")
        records = []
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "HQ_NO_CLAUDE_CODE": ""}), \
                mock.patch("engine.budget.allowed", return_value=True), \
                mock.patch("engine.devloop.claude_path", return_value="/usr/bin/claude"), \
                mock.patch("subprocess.run", fake_run), \
                mock.patch.object(models, "on_usage", lambda *a: records.append(a)), \
                mock.patch.object(loop, "load_constitution", return_value={"models": {"routing": {"review": ["cloud", "claude_code"]}}}):
            del os.environ["ANTHROPIC_API_KEY"]  # no key: the cloud tier is skipped, Claude Code answers
            self.assertEqual(models.complete("review", "system text", "user text"), "plan text")
        self.assertEqual(seen["cmd"][:2], ["/usr/bin/claude", "-p"])
        self.assertIn("--system-prompt", seen["cmd"])
        i = seen["cmd"].index("--tools")
        self.assertEqual(seen["cmd"][i + 1], "")          # no tools at all
        self.assertIn("--strict-mcp-config", seen["cmd"])  # and no MCP servers
        self.assertEqual(seen["input"], "user text")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(records, [("review", "claude_code", "claude-code", 150, 20, 0.02)])

    def test_disabled_in_tests_and_by_kill_switch(self):
        with self.assertRaises(models.ModelUnavailable):
            models._claude_code({}, "s", "u")

    def test_monthly_api_cap(self):
        import os, tempfile
        from unittest import mock
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hq.db"
            con = db.connect(path); db.init(con)
            con.execute("INSERT INTO model_usage(ts, job, tier, model, tokens_in, tokens_out, cost_usd) VALUES (?,?,?,?,?,?,?)",
                        (db.now(), "dump", "cloud", "m", 1, 1, 10.5)); con.commit(); con.close()
            with mock.patch.object(db, "DB_PATH", path), mock.patch.object(models, "on_usage", lambda *a: None), \
                    mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
                    mock.patch.object(models, "_post") as post:
                with self.assertRaises(models.ModelUnavailable):
                    models._cloud({"monthly_cap_usd": 10}, "s", "u")
                post.assert_not_called()
                self.assertFalse(models._over_cap({"monthly_cap_usd": 20}))

    def test_cap_works_without_prices(self):
        """Zero prices in the constitution must not disable the cap: tokens are priced at the fallback rates."""
        import os, tempfile
        from unittest import mock
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hq.db"
            con = db.connect(path); db.init(con)
            con.execute("INSERT INTO model_usage(ts, job, tier, model, tokens_in, tokens_out, cost_usd) VALUES (?,?,?,?,?,?,?)",
                        (db.now(), "dump", "cloud", "m", 1_000_000, 500_000, 0)); con.commit(); con.close()
            with mock.patch.object(db, "DB_PATH", path), mock.patch.object(models, "on_usage", lambda *a: None):
                zero = {"cloud_usd_per_mtok_in": 0, "cloud_usd_per_mtok_out": 0}
                self.assertTrue(models._over_cap(zero | {"monthly_cap_usd": 10}))    # 3 + 7.5 = 10.5 at fallback prices
                self.assertFalse(models._over_cap(zero | {"monthly_cap_usd": 11}))
