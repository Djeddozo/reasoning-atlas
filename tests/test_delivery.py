"""Delivery: PRs from ready to merge through installed, tasks closed only on evidence, suggestions otherwise."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import sqlite3
import unittest
from unittest import mock

from engine import brain, db, delivery, devloop


def pr(n, state="OPEN", body="", title=None, branch="feature", draft=False, merge=""):
    return {"number": n, "url": f"https://github.com/x/y/pull/{n}", "title": title or f"PR {n}", "headRefName": branch,
            "body": body, "state": state, "isDraft": draft, "headRefOid": f"h{n}",
            "mergeCommit": {"oid": merge} if merge else None, "mergedAt": "2026-10-18T10:00:00Z" if merge else None}


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        self.t1 = db.insert(self.con, "tasks", title="Weekly report", owner="ai", status="todo")
        self.t2 = db.insert(self.con, "tasks", title="Voice shortcut", owner="ai", status="todo")
        self.con.commit()
        self.installed = set()   # merge commits in the "running code"
        self.github = []
        for p in (mock.patch.object(delivery, "fetch_prs", side_effect=lambda: [dict(x) for x in self.github]),
                  mock.patch.object(delivery, "_in_running_code", side_effect=lambda sha: sha in self.installed),
                  mock.patch.object(delivery, "_git_ok", return_value=True),
                  mock.patch.object(devloop, "sync_merged", return_value=[])):
            p.start(); self.addCleanup(p.stop)
        delivery.sync(self.con)  # first sync: imports (empty) history

    def stage(self, n):
        return self.con.execute("SELECT stage FROM prs WHERE number = ?", (n,)).fetchone()["stage"]

    def status(self, tid):
        return self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"]

    def test_lifecycle_ready_merged_installed(self):
        self.github = [pr(70, draft=True)]
        delivery.sync(self.con); self.assertEqual(self.stage(70), "draft")
        self.github = [pr(70)]
        delivery.sync(self.con); self.assertEqual(self.stage(70), "review")
        self.assertEqual([p["number"] for p in delivery.today(self.con)["review"]], [70])
        self.github = [pr(70, "MERGED", merge="m70")]
        delivery.sync(self.con); self.assertEqual(self.stage(70), "install")
        self.assertEqual([p["number"] for p in delivery.today(self.con)["install"]], [70])
        self.installed.add("m70")
        delivery.sync(self.con, remote=False)   # after an update install: local check only, no GitHub call
        self.assertEqual(self.stage(70), "live")
        t = delivery.today(self.con)
        self.assertFalse(t["install"]); self.assertEqual([p["number"] for p in t["installed"]], [70])

    def test_installed_build_is_never_offered_again_and_repeats_change_nothing(self):
        self.github = [pr(71, "MERGED", merge="m71")]; self.installed.add("m71")
        delivery.sync(self.con); delivery.sync(self.con); delivery.sync(self.con)
        self.assertEqual(self.stage(71), "live")
        self.assertFalse(delivery.today(self.con)["install"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM prs").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log WHERE action = 'delivery'").fetchone()[0], 1)

    def test_closing_keyword_marks_done_undoably(self):
        self.github = [pr(72, body=f"Closes task #{self.t1}")]
        delivery.sync(self.con)
        self.assertEqual(self.status(self.t1), "todo")   # not merged yet: nothing happens
        self.github = [pr(72, "MERGED", body=f"Closes task #{self.t1}", merge="m72")]
        delivery.sync(self.con)
        self.assertEqual(self.status(self.t1), "done")
        change = self.con.execute("SELECT id FROM brain_changes WHERE row_id = ? AND field = 'status'", (self.t1,)).fetchone()
        brain.undo(self.con, change["id"])
        self.assertEqual(self.status(self.t1), "todo")

    def test_mention_only_suggests_and_waits_for_djeddi(self):
        self.github = [pr(73, "MERGED", body=f"Built the first part of task #{self.t2}", merge="m73")]
        delivery.sync(self.con)
        self.assertEqual(self.status(self.t2), "todo")
        sug = delivery.today(self.con)["suggestions"]
        self.assertEqual([s["task_id"] for s in sug], [self.t2])
        delivery.sync(self.con)   # duplicate event: still one suggestion
        self.assertEqual(len(delivery.today(self.con)["suggestions"]), 1)
        delivery.decide(self.con, sug[0]["id"], "partial")
        self.assertEqual(self.status(self.t2), "todo")
        self.assertIn("Partly delivered by PR #73", self.con.execute("SELECT description FROM tasks WHERE id = ?", (self.t2,)).fetchone()[0])
        self.assertFalse(delivery.today(self.con)["suggestions"])

    def test_similar_title_without_reference_never_closes_a_task(self):
        self.github = [pr(74, "MERGED", title="Weekly report", merge="m74")]
        delivery.sync(self.con)
        self.assertEqual(self.status(self.t1), "todo")
        self.assertFalse(delivery.today(self.con)["suggestions"])
        self.assertIn("PR #74", [f["ref"] for f in delivery.built_features(self.con)])  # left to the overlap check

    def test_dev_branch_links_task_and_hands_merge_to_devloop(self):
        self.github = [pr(75, "MERGED", branch=f"hq-dev/{self.t1}-weekly-report-1018", merge="m75")]
        delivery.sync(self.con)
        row = self.con.execute("SELECT task_ids, link FROM prs WHERE number = 75").fetchone()
        self.assertEqual((row["task_ids"], row["link"]), (str(self.t1), "run"))
        devloop.sync_merged.assert_called()

    def test_github_unreachable_changes_nothing(self):
        self.github = [pr(76)]
        delivery.sync(self.con)
        with mock.patch.object(delivery, "fetch_prs", return_value=None):
            out = delivery.sync(self.con)
        self.assertFalse(out["ok"]); self.assertEqual(self.stage(76), "review")

    def test_first_sync_imports_history_without_touching_tasks(self):
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row; db.init(con); self.addCleanup(con.close)
        tid = db.insert(con, "tasks", title="Old", owner="ai", status="todo"); con.commit()
        self.github = [pr(10, "MERGED", body=f"Closes task #{tid}", merge="m10")]
        delivery.sync(con)
        self.assertEqual(con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "todo")
        self.assertEqual(con.execute("SELECT stage FROM prs WHERE number = 10").fetchone()[0], "install")


if __name__ == "__main__":
    unittest.main()
