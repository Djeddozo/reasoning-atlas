"""Budgets: automatic jobs stop using paid models at their daily budget; what Djeddi starts is never capped."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import budget, db, models


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "b.db"
        def connect(*a, **k):
            c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row; return c
        con = connect(); db.init(con); con.close()
        for p in (mock.patch.object(db, "connect", side_effect=connect),
                  mock.patch.object(budget, "limits", return_value={"overlap": 2, "summarise": 0}),
                  mock.patch.object(models, "on_usage", None)):
            p.start(); self.addCleanup(p.stop)
        self.calls = []
        def tier(name, fail=False):
            def f(*a):
                self.calls.append(name)
                if fail:
                    raise OSError("unreachable")
                return name
            return f
        p = mock.patch.dict(models.TIERS, {"cloud": tier("cloud"), "claude_code": tier("claude_code"), "local": tier("local", True)})
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(_loop, "load_constitution", return_value={"models": {"routing": {
            "overlap": ["cloud", "claude_code", "local"], "chat": ["cloud", "local"], "summarise": ["local", "cloud"]}}})
        p.start(); self.addCleanup(p.stop)

    def spend(self, job, n, tier="claude_code"):
        con = db.connect()
        for _ in range(n):
            con.execute("INSERT INTO model_usage(ts, job, tier, model, tokens_in, tokens_out, cost_usd) VALUES (?,?,?,?,0,0,0)",
                        (db.now(), job, tier, "m"))
        con.commit(); con.close()

    def test_under_budget_uses_paid_tier(self):
        self.spend("overlap", 1)
        self.assertEqual(models.complete("overlap", "s", "u"), "cloud")

    def test_over_budget_skips_paid_tiers_and_is_shown(self):
        self.spend("overlap", 2)
        with self.assertRaises(models.ModelUnavailable) as e:
            models.complete("overlap", "s", "u")
        self.assertIn("daily budget", str(e.exception))
        self.assertEqual(self.calls, ["local"])          # only the free tier was tried
        st = {b["job"]: b for b in budget.status(db.connect())}
        self.assertEqual((st["overlap"]["used"], st["overlap"]["deferred"]), (2, 1))
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM log WHERE action = 'budget_deferred'").fetchone()[0], 1)
        with self.assertRaises(models.ModelUnavailable):
            models.complete("overlap", "s", "u")         # logged once per job per day, counted every time
        self.assertEqual(con.execute("SELECT COUNT(*) FROM log WHERE action = 'budget_deferred'").fetchone()[0], 1)

    def test_local_calls_do_not_count(self):
        self.spend("overlap", 5, tier="local")
        self.assertEqual(models.complete("overlap", "s", "u"), "cloud")

    def test_zero_budget_never_paid_and_manual_jobs_never_capped(self):
        with self.assertRaises(models.ModelUnavailable):
            models.complete("summarise", "s", "u")
        self.assertNotIn("cloud", self.calls)
        self.spend("chat", 50, tier="cloud")
        self.assertEqual(models.complete("chat", "s", "u"), "cloud")


if __name__ == "__main__":
    unittest.main()
