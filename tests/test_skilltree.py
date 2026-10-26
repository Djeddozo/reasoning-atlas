"""Skill tree: capability states, the logical build order, approval for big steps, editing, and Claude growing the tree."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")  # tests never call the real Claude Code
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")  # tests never see this Pi's config/local.toml
import json
import sqlite3
import unittest
from unittest import mock

from engine import brain, dashboard, db, devloop, edit, loop, models, skilltree


class SkillTreeTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        db.init(self.con); loop.seed(self.con)
        skilltree.seed(self.con)
        self.cap = {r["title"]: r["id"] for r in self.con.execute("SELECT id, title FROM capabilities")}
        self.gate_patch = mock.patch.object(devloop, "cfg", return_value=devloop.DEFAULTS | {"enabled": True})
        self.gate_patch.start(); self.addCleanup(self.gate_patch.stop)
        self._lock_dir()

    def _lock_dir(self):
        """Builder slot locks in a temporary folder: a build holding this checkout's .devloop/lock never counts."""
        import tempfile; from pathlib import Path
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        for p in (mock.patch.object(devloop, "WORK", Path(d.name)), mock.patch.object(devloop, "LOCK", Path(d.name) / "lock")):
            p.start(); self.addCleanup(p.stop)

    def task(self, title, cap=None, owner="ai", **kw):
        tid = db.insert(self.con, "tasks", title=title, owner=owner, capability_id=self.cap.get(cap), **kw)
        self.con.commit()
        return tid

    def state(self, title):
        return skilltree.nodes(self.con)[self.cap[title]]["state"]

    def test_seed_once_and_states_follow_the_tasks(self):
        self.assertEqual(skilltree.seed(self.con), 0)  # already planted
        sandbox = self.con.execute("SELECT capability_id FROM tasks WHERE title LIKE 'Sandbox (Docker%'").fetchone()[0]
        self.assertEqual(sandbox, self.cap["Sandbox for outside code"])  # roadmap tasks are linked when planted
        self.assertEqual(self.state("Memory"), "unlocked")
        self.assertEqual(self.state("Prototype builder"), "available")   # needs Self-building + Arsenal: both built
        self.assertEqual(self.state("Product packages"), "locked")       # needs Prototype builder and Sandbox
        a = self.task("Prototype: scaffold a small web app", "Prototype builder")
        b = self.task("Prototype: deploy preview on the Pi", "Prototype builder")
        db.update(self.con, "tasks", a, status="done"); self.con.commit()
        self.assertEqual(self.state("Prototype builder"), "building")
        db.update(self.con, "tasks", b, status="done"); self.con.commit()
        self.assertEqual(self.state("Prototype builder"), "unlocked")
        self.assertEqual(self.state("First digital product"), "available")  # the path unlocks

    def test_self_improving_branch_grows_into_an_existing_tree_once(self):
        # a tree planted before the 'self' branch existed: remove that branch, keep the rest
        self.con.execute("DELETE FROM capabilities WHERE branch = 'self'"); self.con.commit()
        ckpt = self.task("Checkpoint task progress before hitting usage/context limits so work can resume", "Self-building")
        par = self.task("Evaluate building several tasks in parallel on separate branches, with batch approval")
        research = self.task("Skill tree node → auto research of how to implement it")
        other = self.task("Something unrelated", "Self-building")
        self.assertEqual(skilltree.seed(self.con), 6)
        self.assertEqual(skilltree.seed(self.con), 0)  # once
        caps = {r["title"]: dict(r) for r in self.con.execute("SELECT * FROM capabilities WHERE branch = 'self'")}
        self.assertEqual(len(caps), 6)
        self.assertEqual(caps["Better memory"]["requires"], f"{self.cap['Memory']},{self.cap['Brain']}")
        self.assertEqual(caps["Parallel builds on branches"]["requires"],
                         f"{caps['Noticing parallel work']['id']},{caps['Checkpoint & resume']['id']}")
        cap_of = lambda tid: self.con.execute("SELECT capability_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
        self.assertEqual(cap_of(ckpt), caps["Checkpoint & resume"]["id"])
        self.assertEqual(cap_of(par), caps["Parallel builds on branches"]["id"])
        self.assertEqual(cap_of(research), caps["Auto research per node"]["id"])
        self.assertEqual(cap_of(other), self.cap["Self-building"])
        st = skilltree.nodes(self.con)
        self.assertEqual(st[caps["Checkpoint & resume"]["id"]]["state"], "available")   # Self-building + Usage: built
        self.assertEqual(st[caps["Parallel builds on branches"]["id"]]["state"], "locked")
        # the moves are undoable brain changes
        self.assertTrue(self.con.execute("SELECT 1 FROM brain_changes WHERE table_name = 'tasks' AND row_id = ?", (ckpt,)).fetchone())
        # a branch Djeddi dropped stays dropped
        self.con.execute("UPDATE capabilities SET status = 'dropped' WHERE branch = 'self'"); self.con.commit()
        self.assertEqual(skilltree.seed(self.con), 0)

    def test_build_order_is_logical_and_follows_the_focus(self):
        locked = self.task("Package format for products", "Product packages", impact=5)
        frontier = self.task("PDF text extraction", "Reading PDFs & scans", impact=2)
        goal = db.insert(self.con, "goals", title="Pass exams")
        planned = self.task("Exam timetable reminders", goal_id=goal, impact=2)
        from_idea = self.task("Flashcard generator", description="(from idea #3)", impact=2)
        order = skilltree.build_order(self.con)
        self.assertNotIn(locked, [e["task"]["id"] for e in order["ready"]])
        self.assertEqual(order["waiting"][0]["task"]["id"], locked)
        self.assertIn("Prototype builder", order["waiting"][0]["why"])
        self.assertEqual(order["ready"][0]["task"]["id"], frontier)       # frontier bonus
        skilltree.set_focus(self.con, "plan")
        self.assertEqual(skilltree.build_order(self.con)["ready"][0]["task"]["id"], planned)
        skilltree.set_focus(self.con, "ideas")
        self.assertEqual(skilltree.build_order(self.con)["ready"][0]["task"]["id"], from_idea)
        with self.assertRaises(ValueError):
            skilltree.set_focus(self.con, "everything")

    def test_big_steps_wait_for_approval_and_the_agent_moves_on(self):
        big = self.task("Rewrite the planner", impact=5, effort_h=12)
        small = self.task("Fix a typo in the brief", impact=1, effort_h=0.5)
        external = self.task("Publish the help page", impact=4)
        self.assertEqual(devloop.pick_task(self.con)["id"], small)       # dashboard view: asks nothing
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(devloop.pick_task(self.con, ask=True)["id"], small)
        asked = {r["task_id"]: r for r in self.con.execute("SELECT * FROM approvals")}
        self.assertEqual(set(asked), {big, external})                    # one approval each, on the phone
        self.assertIn("12 hours", asked[big]["reason"])
        devloop.pick_task(self.con, ask=True)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 2)  # not asked twice
        db.update(self.con, "tasks", small, status="done"); self.con.commit()
        with mock.patch.object(devloop, "claude_path", return_value="/usr/bin/claude"):
            self.assertIn("2 task(s) wait for your OK", devloop.why_not_now(self.con))
        skilltree.decide(self.con, big, True)
        skilltree.decide(self.con, external, False)
        self.assertEqual(devloop.pick_task(self.con, ask=True)["id"], big)
        nxt = {e["id"]: e for e in skilltree.tree(self.con)["next"]}
        self.assertEqual((nxt[big]["approval"], nxt[external]["approval"]), ("approved", "denied"))

    def test_review_fixes(self):
        # an idea promoted to a task keeps its capability, so a locked foundation still holds it back
        idea = edit.create(self.con, "ideas", {"title": "Product bundle", "capability_id": self.cap["Product packages"]})
        tid = brain.promote_idea(self.con, idea, "ai", "promoted by re-evaluation", batch="re-1")  # Claude's proposal
        self.assertEqual(self.con.execute("SELECT capability_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0],
                         self.cap["Product packages"])
        self.assertIn(tid, [e["task"]["id"] for e in skilltree.build_order(self.con)["waiting"]])
        # the only task of a new capability still opens it: approval first
        only = self.task("Local Whisper endpoint", "Local models (GPU PC)")
        e = next(e for e in skilltree.build_order(self.con)["ready"] if e["task"]["id"] == only)
        self.assertIn("opens a new capability", e["gate"])
        # no frontier bonus for work on capabilities that are already built
        built = self.task("Memory maintenance", "Memory", impact=3)
        plain = self.task("Plain task", impact=3)
        w = {e["task"]["id"]: e["weight"] for e in skilltree.build_order(self.con)["ready"]}
        self.assertEqual(w[built], w[plain])

    def test_my_queue_first_and_always_runnable(self):
        """Tasks Djeddi queues himself go first, in his order, need no approval, and Run builds them even when
        they wait for another task; Atlas's proposals follow; the reason is given when nothing can start."""
        proposal = self.task("Proposal from the plan", impact=5)                        # owner ai, not queued by him
        first = self.task("Help page", owner="me")
        second = self.task("Rewrite the planner", owner="me", effort_h=12)              # big: no OK needed when he queues it
        dashboard.act(self.con, "task_owner", first, "ai")
        edit.edit(self.con, "tasks", second, {"owner": "ai"})
        plan = skilltree.build_plan(self.con)
        self.assertEqual([i["id"] for i in plan["mine"]], [first, second])
        self.assertEqual(plan["mine"][1]["state"], "ready")
        self.assertIn(proposal, [i["id"] for i in plan["proposed"]])
        self.assertEqual(devloop.pick_task(self.con)["id"], first)
        dashboard.act(self.con, "queue_top", second)
        self.assertEqual(devloop.pick_task(self.con)["id"], second)
        # his queued task waits for an unfinished task: automatic runs skip it, Run builds it anyway
        dep = self.task("Prerequisite", owner="me")
        waiting = self.task("Needs the prerequisite", owner="me", depends_on=str(dep))
        for t in (first, second, proposal):
            db.update(self.con, "tasks", t, status="done")
        self.con.commit()
        dashboard.act(self.con, "task_owner", waiting, "ai")
        self.assertIsNone(devloop.pick_task(self.con))
        with mock.patch.object(devloop, "claude_path", return_value="/usr/bin/claude"):
            self.assertEqual(devloop.why_not_now(self.con), "")                         # Run now is allowed
            self.assertEqual(devloop.why_not_now(self.con, waiting), "")
        self.assertEqual(devloop.pick_task(self.con, fallback=True)["id"], waiting)
        self.assertIn(f"after #{dep}", skilltree.build_plan(self.con)["mine"][0]["note"])
        # taken back: out of his queue
        dashboard.act(self.con, "task_owner", waiting, "me")
        self.assertEqual(skilltree.build_plan(self.con)["mine"], [])
        self.assertIn("build queue is empty", skilltree.why_nothing(skilltree.build_plan(self.con)))
        # the agent's own follow-up tasks are proposals, not his queue
        import os, subprocess, sys
        env = dict(os.environ, HQ_ACTOR="agent")
        self.assertEqual(env["HQ_ACTOR"], devloop.agent_env(_pl.Path("/tmp/x.db"))["HQ_ACTOR"])

    def test_earlier_manual_queueing_is_adopted_once(self):
        tid = self.task("Queued before the queue existed", owner="me")
        brain.set_field(self.con, "tasks", tid, "owner", "ai", "queued for the AI by Djeddi"); self.con.commit()
        self.assertEqual([i["id"] for i in skilltree.build_plan(self.con)["mine"]], [tid])
        other = self.task("Set to ai by Claude", owner="me")
        brain.set_field(self.con, "tasks", other, "owner", "ai", "queued for the AI by Djeddi"); self.con.commit()
        self.assertNotIn(other, [i["id"] for i in skilltree.build_plan(self.con)["mine"]])  # only once

    def test_new_product_or_capability_needs_approval(self):
        a = self.task("First product: landing page", "First digital product")
        node = skilltree.nodes(self.con)[self.cap["First digital product"]]
        self.assertIn("product", skilltree.gate(dict(self.con.execute("SELECT * FROM tasks WHERE id = ?", (a,)).fetchone()),
                                                node, devloop.DEFAULTS))
        self.task("Sandbox: container", "Sandbox for outside code")
        t2 = self.task("Sandbox: no network", "Sandbox for outside code")
        node = skilltree.nodes(self.con)[self.cap["Sandbox for outside code"]]
        row = dict(self.con.execute("SELECT * FROM tasks WHERE id = ?", (t2,)).fetchone())
        self.assertIn("opens a new capability", skilltree.gate(row, node, devloop.DEFAULTS))

    def test_edit_create_validate_and_undo(self):
        tid = self.task("Write tests", owner="me")
        self.assertIn("Saved", edit.edit(self.con, "tasks", tid, {"title": "Write more tests", "impact": "5",
                                                                     "capability_id": str(self.cap["Prototype builder"])}))
        row = self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual((row["title"], row["impact"], row["capability_id"]), ("Write more tests", 5, self.cap["Prototype builder"]))
        for bad in ({"impact": 9}, {"title": " "}, {"owner": "boss"}, {"depends_on": str(tid)}, {"score": 99},
                    {"capability_id": 999}, {"due": "tomorrow"}):
            with self.assertRaises(ValueError, msg=bad):
                edit.edit(self.con, "tasks", tid, bad)
        last = brain.changes(self.con, 1)[0]
        brain.undo(self.con, last["id"])  # Djeddi's edits are undoable like Claude's
        self.assertIsNone(self.con.execute("SELECT capability_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0])
        # capabilities: no loops in the tree
        with self.assertRaises(ValueError):
            edit.edit(self.con, "capabilities", self.cap["Memory"], {"requires": str(self.cap["Prototype builder"])})
        cid = edit.create(self.con, "capabilities", {"title": "Video editing", "branch": "make",
                                                     "requires": f"{self.cap['Prototype builder']}"})
        self.assertEqual(skilltree.nodes(self.con)[cid]["state"], "locked")
        iid = edit.create(self.con, "ideas", {"title": "YouTube shorts from my notes", "capability_id": cid, "impact": "4"})
        self.assertEqual([i["id"] for i in skilltree.nodes(self.con)[cid]["ideas"]], [iid])
        gid = edit.create(self.con, "goals", {"title": "Ship one product", "horizon": "year"})
        edit.edit(self.con, "goals", gid, {"status": "achieved"})
        create = next(c for c in brain.changes(self.con, 10) if c["table_name"] == "capabilities")
        brain.undo(self.con, create["id"])
        self.assertNotIn(cid, skilltree.nodes(self.con))

    def test_reevaluate_grows_the_tree_from_ideas(self):
        idea = db.insert(self.con, "ideas", title="Sell 3D prints at school"); self.con.commit()
        tid = self.task("PDF parser", owner="ai")
        reply = {"changes": [{"table": "tasks", "id": tid, "field": "capability_id", "value": self.cap["Reading PDFs & scans"],
                              "reason": "this task builds PDF reading"},
                             {"table": "ideas", "id": idea, "field": "capability_id", "value": self.cap["Physical products"],
                              "reason": "needs the physical products line"}],
                 "capabilities": [{"title": "3D print shop", "branch": "product", "requires": [self.cap["Physical products"], 9999],
                                   "idea_id": idea, "reason": "from idea"},
                                  {"title": "Bad", "branch": "nowhere", "requires": []}],
                 "focus": "f"}
        with mock.patch.object(models, "complete", return_value=json.dumps(reply)):
            r = brain.reevaluate(self.con)
        self.assertEqual((r["applied"], r["skipped"]), (3, 1))
        shop = self.con.execute("SELECT * FROM capabilities WHERE title = '3D print shop'").fetchone()
        self.assertEqual((shop["branch"], shop["requires"], shop["idea_id"]), ("product", str(self.cap["Physical products"]), idea))
        self.assertEqual(self.con.execute("SELECT capability_id FROM ideas WHERE id = ?", (idea,)).fetchone()[0], shop["id"])
        self.assertEqual(self.con.execute("SELECT capability_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0],
                         self.cap["Reading PDFs & scans"])

    def test_queue_this_drafts_a_task_to_edit_before_it_is_queued(self):
        """Queue this on a node: a draft linked to the node that the agent ignores; Save & queue puts it first."""
        cid = self.cap["Prototype builder"]
        tid = int(dashboard.act(self.con, "node_draft", cid))
        row = self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual((row["status"], row["owner"], row["capability_id"]), ("proposed", "ai", cid))
        self.assertEqual(row["title"], "Build Prototype builder")
        self.assertEqual(int(dashboard.act(self.con, "node_draft", cid)), tid)          # one open draft per node
        node = skilltree.nodes(self.con)[cid]
        self.assertEqual((node["state"], node["total"]), ("available", 0))            # a draft is not work yet
        self.assertIn(tid, [t["id"] for t in node["tasks"]])
        self.assertNotIn(tid, [i["id"] for i in skilltree.build_plan(self.con)["mine"]])
        self.assertNotEqual((devloop.pick_task(self.con) or {}).get("id"), tid)
        msg = dashboard.act(self.con, "queue_draft", tid, json.dumps({"title": "Prototype: scaffold a Flask app",
                                                                     "effort_h": "2", "status": "doing"}))
        self.assertIn("Queued", msg)
        row = self.con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual((row["title"], row["effort_h"], row["status"], row["queued_by"]),
                         ("Prototype: scaffold a Flask app", 2, "todo", "me"))
        self.assertEqual(skilltree.build_plan(self.con)["mine"][0]["id"], tid)
        self.assertEqual(devloop.pick_task(self.con)["id"], tid)
        self.assertEqual(skilltree.nodes(self.con)[cid]["total"], 1)
        # a locked node: the draft says what is missing; a second step on a started node is named so
        locked = skilltree.draft_task(self.con, self.cap["Product packages"])
        self.assertIn("still needs", self.con.execute("SELECT description FROM tasks WHERE id = ?", (locked,)).fetchone()[0])
        self.assertTrue(skilltree.draft_task(self.con, cid) != tid)
        self.assertTrue(self.con.execute("SELECT title FROM tasks WHERE status = 'proposed' AND capability_id = ?",
                                         (cid,)).fetchone()[0].startswith("Next step for"))
        # undoable like every other change, and errors are clear
        undo = self.con.execute("SELECT id FROM brain_changes WHERE table_name = 'tasks' AND row_id = ? AND field = 'status'",
                                (tid,)).fetchone()[0]
        brain.undo(self.con, undo)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "proposed")
        with self.assertRaises(ValueError):
            skilltree.draft_task(self.con, 9999)
        db.update(self.con, "tasks", locked, status="dropped"); self.con.commit()
        with self.assertRaises(ValueError):
            skilltree.queue_draft(self.con, locked)

    def test_dashboard_and_telegram(self):
        from engine.interfaces import telegram
        tid = self.task("Rewrite everything", effort_h=20)
        data = dashboard.build_data(self.con)
        self.assertEqual(data["tree"]["next"][0]["gate"][:3], "big")
        self.assertEqual(len(data["tree"]["branches"]), len(skilltree.BRANCHES))
        self.assertIn("Approved", dashboard.act(self.con, "build_ok", tid))
        self.assertEqual(json.loads(dashboard.act(self.con, "get", tid, "tasks"))["title"], "Rewrite everything")
        self.assertIn("Saved", dashboard.act(self.con, "edit", tid, json.dumps({"table": "tasks", "fields": {"effort_h": "3"}})))
        self.assertIn("Added idea", dashboard.act(self.con, "create", None, json.dumps({"table": "ideas", "fields": {"title": "Podcast"}})))
        self.assertIn("Ideas first", dashboard.act(self.con, "build_focus", None, "ideas"))
        with self.assertRaises(ValueError):
            dashboard.act(self.con, "get", tid, "log")
        bot = telegram.Bot.__new__(telegram.Bot); bot.con = self.con
        self.assertIn("Skill tree:", bot.cmd_tree(""))
        self.assertIn("Plan first", bot.cmd_tree("focus plan"))
        self.assertIn("Not now", bot.cmd_tree(f"no {tid}"))


if __name__ == "__main__":
    unittest.main()


class ProofTest(unittest.TestCase):
    """Proof levels: confirmed / partial / suggested / none, from done work and merged PRs only."""
    def setUp(self):
        import sqlite3
        from engine import db
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)
        ins = lambda t, **f: db.insert(self.con, "capabilities", title=t, **f)
        self.done = ins("Weekly review"); self.half = ins("Voice input"); self.like = ins("Model routing per task")
        self.empty = ins("Sourdough baking"); self.manual = ins("Telegram bot", status="unlocked")
        task = lambda title, cap, st: db.insert(self.con, "tasks", title=title, owner="ai", status=st, capability_id=cap)
        task("Weekly review page", self.done, "done")
        t = task("Record voice", self.half, "done"); task("Stream voice", self.half, "todo")
        self.con.execute("INSERT INTO prs(number, title, body, state, stage, task_ids) VALUES (9, 'Record voice', '', 'MERGED', 'live', ?)", (str(t),))
        self.con.execute("INSERT INTO prs(number, title, body, state, stage, task_ids) VALUES (10, 'Model routing: choose a model per task', '', 'MERGED', 'live', '')")
        self.con.commit()

    def nodes(self):
        caps = skilltree.nodes(self.con)
        skilltree.proof(self.con, caps)
        return caps

    def test_levels(self):
        n = self.nodes()
        self.assertEqual(n[self.done]["proof"], "confirmed")
        self.assertEqual(n[self.manual]["proof"], "confirmed")
        self.assertEqual(n[self.half]["proof"], "partial")
        self.assertIn("PR #9 (installed)", n[self.half]["evidence"][0]["prs"])
        self.assertEqual(n[self.like]["proof"], "suggested")
        self.assertEqual(n[self.like]["suggested"][0]["ref"], "PR #10")
        self.assertEqual(n[self.empty]["proof"], "none")

    def test_confirm_is_undoable_and_dismiss_hides_suggestion(self):
        from engine import brain
        skilltree.dismiss(self.con, self.like, "PR #10")
        self.assertEqual(self.nodes()[self.like]["proof"], "none")
        skilltree.confirm(self.con, self.empty, "")
        self.assertEqual(self.nodes()[self.empty]["proof"], "confirmed")
        cid = self.con.execute("SELECT MAX(id) FROM brain_changes").fetchone()[0]
        brain.undo(self.con, cid)
        self.assertEqual(self.nodes()[self.empty]["proof"], "none")
