import unittest
from pathlib import Path
from unittest import mock

from engine import agents, db


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


class Agents(unittest.TestCase):
    def setUp(self):
        self.con = database()

    def test_status_order_and_next(self):
        installed = {"claude": "/usr/bin/claude", "codex": "/usr/bin/codex"}
        with mock.patch("shutil.which", side_effect=lambda c: installed.get(c)):
            s = agents.status(self.con)
        self.assertEqual(s["order"], ["claude", "codex"])
        self.assertEqual(s["next"], "claude")
        by = {a["id"]: a for a in s["agents"]}
        self.assertEqual((by["codex"]["state"], by["gemini"]["state"]), ("ready", "not installed"))
        self.assertIn("Codex CLI → Claude Code", agents.set_order(self.con, ["codex", "claude", "nonsense", "codex"]))
        with mock.patch("shutil.which", side_effect=lambda c: installed.get(c)):
            self.assertEqual(agents.status(self.con)["next"], "codex")
        with self.assertRaises(ValueError):
            agents.set_order(self.con, ["nobody"])

    def test_claude_out_of_usage_falls_to_the_next(self):
        with mock.patch("shutil.which", return_value="/usr/bin/x"), \
                mock.patch("engine.atlasplan.reset_status", return_value={"waiting": True, "retry_at": "2026-10-26T03:00"}):
            s = agents.status(self.con)
        self.assertEqual(s["next"], "codex")
        self.assertTrue([a for a in s["agents"] if a["id"] == "claude"][0]["state"].startswith("out:"))

    def test_resources_and_handover(self):
        with mock.patch("shutil.which", side_effect=lambda c: "/usr/bin/codex" if c == "codex" else None):
            self.assertEqual(agents.sync_resources(self.con), 1)
            agents.sync_resources(self.con)  # refreshed, not duplicated
        rows = self.con.execute("SELECT name, kind, status FROM resources WHERE kind = 'agent'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Codex CLI", "agent", "active")])
        tid = db.insert(self.con, "tasks", title="Fix the backup", description="retry when busy", status="todo", owner="ai")
        prompt = agents.handover(self.con, tid)
        self.assertIn("AGENTS.md", prompt)
        self.assertIn(f"closes task #{tid}", prompt)
        with self.assertRaises(ValueError):
            agents.handover(self.con, 999)


if __name__ == "__main__":
    unittest.main()
