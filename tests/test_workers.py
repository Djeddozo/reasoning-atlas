import json, os, tempfile, threading, unittest, urllib.error, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from engine import db, devloop, parallel, worker, workers
from services.atlas import hub
from tests.test_hub import TOKEN

SECRET = "w" * 32  # a test value for the worker's key
ENV = {"ATLAS_WORKER_" + "TOKEN_ZENUNIT": SECRET}


def database(path=":memory:"):
    con = db.connect(Path(path))
    db.init(con)
    return con


class Base(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        work = Path(self.dir.name)
        for p in (mock.patch.object(devloop, "WORK", work), mock.patch.object(devloop, "LOCK", work / "lock"),
                  mock.patch.dict(os.environ, ENV),
                  mock.patch.object(devloop, "cfg", return_value=devloop.DEFAULTS | {"enabled": True}),
                  mock.patch.object(devloop, "limit_reached", return_value=""),
                  mock.patch.object(devloop, "batch_full", return_value=False),
                  mock.patch.object(devloop, "build_prompt", return_value="PROMPT"),
                  mock.patch("engine.routing.choose", return_value={"model": "sonnet", "reason": "test", "level": "standard"})):
            p.start()
            self.addCleanup(p.stop)
        self.task = db.insert(self.con, "tasks", title="Radar: follow more feeds", owner="ai", status="todo")
        self.con.commit()

    def approve(self, name="zenunit", slots=1):
        workers.register(self.con, name, "ZenUnit PC (WSL2)", slots)
        aid = workers.get(self.con, name)["approval_id"]
        self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (aid,))

    def claim(self, task=None):
        task = task or self.task
        with mock.patch.object(devloop, "pick_task", return_value=self.con.execute("SELECT * FROM tasks WHERE id = ?", (task,)).fetchone()):
            return workers.claim(self.con, "zenunit", SECRET)


class Register(Base):
    def test_approval_first_then_active_and_revoked(self):
        msg = workers.register(self.con, "zenunit", "ZenUnit PC (WSL2)", 9)
        self.assertIn("approve #", msg)
        a = self.con.execute("SELECT * FROM approvals").fetchone()
        self.assertEqual((a["category"], a["status"]), ("access", "pending"))
        self.assertNotIn(SECRET, a["reason"])
        with self.assertRaises(PermissionError):
            workers.claim(self.con, "zenunit", SECRET)  # not before Djeddi approves
        self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (a["id"],))
        self.assertEqual(workers.get(self.con, "zenunit")["status"], "active")
        self.assertEqual(workers.slots(self.con, "zenunit"), workers.ceiling())  # 9 asked, capped (config/local.toml may lower it)
        with self.assertRaises(PermissionError):
            workers.claim(self.con, "zenunit", "wrong" * 8)
        workers.revoke(self.con, "zenunit")
        with self.assertRaises(PermissionError):
            workers.claim(self.con, "zenunit", SECRET)
        for bad in ("pi", "X", "has space"):
            with self.assertRaises(ValueError):
                workers.register(self.con, bad)

    def test_slots_setting_and_ceiling(self):
        self.assertEqual(workers.slots(self.con), 1)  # default: nothing changes until he raises it
        self.assertIn("Side by side", workers.set_slots(self.con, 2))
        self.assertEqual(workers.slots(self.con), 2)
        with mock.patch("engine.loop.load_constitution", return_value={"devloop": {"max_slots_per_worker": 1}}):
            self.assertEqual(workers.slots(self.con), 1)  # the constitution's ceiling wins
            with self.assertRaises(ValueError):
                workers.set_slots(self.con, 2)
        with self.assertRaises(ValueError):
            workers.set_slots(self.con, workers.SLOT_CEILING + 1)


class Leases(Base):
    def test_claim_heartbeat_report(self):
        self.approve()
        job = self.claim()
        self.assertEqual((job["task"], job["prompt"], job["base"], job["model"]), (self.task, "PROMPT", "origin/main", "sonnet"))
        run = self.con.execute("SELECT * FROM dev_runs WHERE id = ?", (job["run"],)).fetchone()
        self.assertEqual((run["worker"], run["status"]), ("zenunit", "running"))
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "doing")
        self.assertIn("busy", self.claim()["why"])  # one slot
        out = workers.heartbeat(self.con, "zenunit", SECRET, job["lease"], ["12:00 → Bash: tests"])
        self.assertEqual(out, {"stop": False})
        self.assertIn("→ Bash: tests", devloop.log_path(job["run"]).read_text())
        with self.assertRaises(ValueError):
            workers.report(self.con, "zenunit", SECRET, job["lease"], {"status": "pr_open", "pr_url": "https://evil.example/x"})
        msg = workers.report(self.con, "zenunit", SECRET, job["lease"],
                             {"status": "pr_open", "summary": "done", "pr_url": "https://github.com/Djeddozo/x/pull/9", "tokens_in": 5})
        self.assertIn("pr open", msg)
        run = self.con.execute("SELECT * FROM dev_runs WHERE id = ?", (job["run"],)).fetchone()
        self.assertEqual((run["status"], run["pr_url"], run["tokens_in"]), ("pr_open", "https://github.com/Djeddozo/x/pull/9", 5))
        self.assertEqual(self.con.execute("SELECT status FROM build_leases").fetchone()[0], "released")
        with self.assertRaises(ValueError):
            workers.heartbeat(self.con, "zenunit", SECRET, job["lease"])  # released: stop

    def test_expiry_returns_the_task(self):
        self.approve()
        job = self.claim()
        past = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
        self.con.execute("UPDATE build_leases SET expires = ?", (past,))
        self.assertEqual(workers.expire(self.con), [self.task])
        run = self.con.execute("SELECT * FROM dev_runs WHERE id = ?", (job["run"],)).fetchone()
        self.assertEqual(run["status"], "stopped")
        self.assertIn("Lease expired", run["summary"])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.task,)).fetchone()[0], "todo")
        with self.assertRaises(ValueError):
            workers.report(self.con, "zenunit", SECRET, job["lease"], {"status": "pr_open"})

    def test_stop_reaches_the_worker(self):
        self.approve()
        job = self.claim()
        self.assertIn(f"task #{self.task}", devloop.stop(self.con))
        self.assertTrue(workers.heartbeat(self.con, "zenunit", SECRET, job["lease"])["stop"])

    def test_further_builds_only_from_an_accepted_batch(self):
        self.approve(slots=2)
        other = db.insert(self.con, "tasks", title="Backup: keep seven days", owner="ai", status="todo")
        same = db.insert(self.con, "tasks", title="Radar: explain why it matters", owner="ai", status="todo")
        self.claim()
        with mock.patch.object(parallel, "ready", return_value=[same, other]), \
                mock.patch.object(parallel.overlap, "modules", return_value=[]):
            self.assertIn("Side by side", workers.claim(self.con, "zenunit", SECRET)["why"])  # no batch accepted
            self.con.execute("INSERT INTO parallel_batches (tasks, status) VALUES (?, 'accepted')", (f"{same},{other}",))
            job = workers.claim(self.con, "zenunit", SECRET)
            self.assertEqual(job["task"], other)  # the radar task shares engine/radar.py with the build running
            self.assertEqual(self.con.execute("SELECT slot FROM build_leases WHERE id = ?", (job["lease"],)).fetchone()[0], 2)


class PiSlots(Base):
    def test_slot_locks_and_interrupted_runs(self):
        workers.set_slots(self.con, 2)
        a, k1 = devloop.take_slot(self.con)
        b, k2 = devloop.take_slot(self.con)
        none, k3 = devloop.take_slot(self.con)
        self.assertEqual((k1, k2, none, k3), (1, 2, None, 0))
        self.assertEqual(devloop.busy_slots(self.con), 2)
        self.assertEqual(devloop.why_not_now(self.con), "All 2 builder slots on the Pi are busy.")
        rid = devloop.claim_run(self.con, devloop.cfg(), self.con.execute("SELECT * FROM tasks").fetchone(), slot=2)
        self.assertEqual(devloop.close_interrupted(self.con), 0)  # slot 2 is held: the run is real
        self.assertEqual(devloop.current(self.con)["id"], rid)
        b.close()  # the process on slot 2 died
        self.assertIsNone(devloop.current(self.con))
        self.assertEqual(self.con.execute("SELECT status FROM dev_runs WHERE id = ?", (rid,)).fetchone()[0], "failed")
        a.close()

    def test_lanes_name_the_worker(self):
        self.approve()
        self.claim()
        with mock.patch.object(devloop, "_held", return_value=True):
            lane = devloop.lanes(self.con)[0]
        self.assertEqual((lane["worker"], lane["slot"]), ("zenunit", 1))


class Devices(Base):
    """#193: per device what it builds or why it is idle, named as on its Resources › Connected card."""

    def machines(self):
        for host, role in (("DjedsberryPi", "pi"), ("ZenUnit", "pc")):
            db.insert(self.con, "resources", kind="hardware", name=f"Machine ({host})", source=f"detected:{host}",
                      status="active", detected=json.dumps({"role": role}))

    def test_display_names_follow_the_connected_cards(self):
        self.machines()
        self.approve()
        self.assertEqual(workers.display_names(self.con), {"pi": "DjedsberryPi", "zenunit": "ZenUnit"})
        cards = {d["name"]: d["resource_id"] for d in workers.roles(self.con)}
        ids = {r["source"]: r["id"] for r in self.con.execute("SELECT id, source FROM resources")}
        self.assertEqual(cards, {"pi": ids["detected:DjedsberryPi"], "zenunit": ids["detected:ZenUnit"]})

    def test_names_without_a_card(self):
        workers.register(self.con, "zenunit", "ZenUnit PC (WSL2)", 1)
        self.assertEqual(workers.display_names(self.con), {"pi": "Pi", "zenunit": "ZenUnit PC"})

    def test_why_each_device_is_idle(self):
        self.machines()
        workers.register(self.con, "zenunit", "ZenUnit PC (WSL2)", 1)
        with mock.patch.object(devloop, "paused", return_value=False):
            d = {x["name"]: x for x in workers.devices(self.con)}
            self.assertEqual(d["pi"]["role"], "hub")
            self.assertIn("approval #", d["zenunit"]["idle"])
            self.assertEqual(d["zenunit"]["status"], "pending")
            aid = d["zenunit"]["approval_id"]
            self.con.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (aid,))
            self.assertIn("ffline", {x["name"]: x for x in workers.devices(self.con)}["zenunit"]["idle"])
            workers.claim(self.con, "zenunit", SECRET, idle="Only 2.0 GB free here (at least 5 GB needed): not claiming work.")
            z = {x["name"]: x for x in workers.devices(self.con)}["zenunit"]
            self.assertTrue(z["online"])
            self.assertIn("GB free", z["idle"])
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM build_leases").fetchone()[0], 0)  # idle: no claim
            self.claim()  # ZenUnit builds; the Pi is idle next to it without an accepted batch
            d = {x["name"]: x for x in workers.devices(self.con)}
            self.assertEqual((d["zenunit"]["busy"], d["zenunit"]["idle"], d["zenunit"]["building"][0]["task_id"]), (1, "", self.task))
            self.assertIn("Side by side", d["pi"]["idle"])
        with mock.patch.object(devloop, "paused", return_value=True):
            self.assertEqual({x["name"]: x for x in workers.devices(self.con)}["pi"]["idle"], "The builder is paused")

    def test_runs_and_lanes_carry_the_device(self):
        self.machines()
        self.approve()
        job = self.claim()
        with mock.patch.object(devloop, "_held", return_value=True):
            lane = devloop.lanes(self.con)[0]
            o = devloop.overview(self.con)
        self.assertEqual((lane["worker"], lane["device"], lane["slot"]), ("zenunit", "ZenUnit", 1))
        run = next(r for r in o["runs"] if r["id"] == job["run"])
        self.assertEqual((run["worker"], run["device"], run["slot"]), ("zenunit", "ZenUnit", 1))
        self.assertEqual([d["display"] for d in o["devices"]], ["DjedsberryPi", "ZenUnit"])
        self.assertEqual(o["alongside"], [])
        with mock.patch.object(devloop, "_held", return_value=True), mock.patch.object(hub, "_on_copy", lambda con, fn: fn(con)):
            r = next(r for r in hub._run_status(self.con, {})["runs"] if r["id"] == job["run"])
        self.assertEqual((r["worker"], r["device"], r["slot"]), ("zenunit", "ZenUnit", 1))
        res = hub._resources(self.con, {})
        roles = {c["source"]: c["builder"] for c in res["connected"]}
        self.assertEqual(roles["detected:ZenUnit"]["role"], "worker")
        self.assertEqual(roles["detected:ZenUnit"]["building"][0]["task_id"], self.task)
        self.assertEqual(roles["detected:DjedsberryPi"]["role"], "hub")

    def test_slots_per_device_through_the_hub(self):
        self.approve()
        self.assertIn("zenunit: 2 slots", hub._dev_controls(self.con, {"slots": 2, "worker": "zenunit"}))
        self.assertEqual(workers.slots(self.con, "zenunit"), 2)
        self.assertEqual(workers.slots(self.con), 1)

    def test_cortex_builder_names_the_device(self):
        from engine import cortex_mcp
        path = Path(self.dir.name) / "hq.db"
        con = database(path)
        self.addCleanup(con.close)
        db.insert(con, "resources", kind="hardware", name="PC (ZenUnit)", source="detected:ZenUnit", status="active", detected="{}")
        db.insert(con, "workers", name="zenunit", device="ZenUnit PC (WSL2)", slots=2, status="active")
        t = db.insert(con, "tasks", title="Build", owner="ai", status="doing")
        rid = db.insert(con, "dev_runs", task_id=t, branch="b", status="running", started=db.now(), worker="zenunit")
        workers.lease(con, t, rid, "zenunit", 2, remote=True)
        con.commit()
        with mock.patch.dict(os.environ, {"HQ_DB": str(path)}):
            r = cortex_mcp.builder()["running"][0]
        self.assertEqual((r["worker"], r["device"], r["slot"]), ("zenunit", "ZenUnit", 2))


class FakeHub:
    name = "zenunit"

    def __init__(self, stop=False):
        self.calls, self.stop = [], stop

    def post(self, what, body, timeout=60):
        self.calls.append((what, body))
        return 200, ({"stop": self.stop} if what == "heartbeat" else {})


class Worker(Base):
    JOB = {"task": 7, "title": "Radar feeds", "lease": 3, "run": 11, "branch": "hq-dev/7-radar-1026030000", "base": "origin/main",
           "prompt": "PROMPT", "model": "sonnet", "max_turns": 5, "max_minutes": 1, "heartbeat_seconds": 60, "outside_ok": False}

    def run_job(self, outcome="ok"):
        h = FakeHub()
        with mock.patch.object(worker, "LOCAL_DB", Path(self.dir.name) / "worker.db"), \
                mock.patch.object(devloop, "_git", return_value="abc123") as git, \
                mock.patch.object(devloop, "run_agent", return_value=("built it", False, {"turns": 3, "tokens_in": 10})), \
                mock.patch.object(devloop, "checked", return_value=(outcome, "boom" if outcome != "ok" else "")), \
                mock.patch.object(devloop, "open_pr", return_value="https://github.com/Djeddozo/x/pull/5"):
            out = worker.build(h, dict(self.JOB), claude="claude")
        return out, h, git

    def test_builds_from_main_and_reports(self):
        out, h, git = self.run_job()
        self.assertEqual((out["status"], out["pr_url"], out["turns"]), ("pr_open", "https://github.com/Djeddozo/x/pull/5", 3))
        self.assertIn(mock.call("worktree", "add", "-q", "-b", self.JOB["branch"], mock.ANY, "origin/main"), git.call_args_list)
        self.assertIn(mock.call("push", "-q", "-u", "origin", self.JOB["branch"], cwd=mock.ANY, timeout=180), git.call_args_list)
        report = [b for w, b in h.calls if w == "report"][0]
        self.assertEqual((report["lease"], report["result"]["status"]), (3, "pr_open"))

    def test_failed_tests_are_not_pushed(self):
        out, h, git = self.run_job("tests_failed")
        self.assertEqual(out["status"], "tests_failed")
        self.assertFalse(any(c.args[:1] == ("push",) for c in git.call_args_list))


class ThroughHub(unittest.TestCase):
    def test_worker_routes_use_the_worker_key(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, ENV):
            path = Path(folder) / "hq.db"
            con = database(path)
            workers.register(con, "zenunit", "PC")
            con.execute("UPDATE approvals SET status = 'approved'")
            con.commit()
            con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                good = worker.Hub(url, "zenunit", SECRET)
                with mock.patch.object(devloop, "cfg", return_value=devloop.DEFAULTS | {"enabled": False}):
                    code, out = good.post("claim", {})
                self.assertEqual((code, out["task"]), (200, None))
                self.assertIn("off", out["why"])
                self.assertEqual(worker.Hub(url, "zenunit", TOKEN).post("claim", {})[0], 403)  # the hub's key is not a worker key
                self.assertEqual(worker.Hub(url, "zenunit", SECRET).post("report", {"lease": 99, "result": {}})[0], 403)
            finally:
                server.shutdown()
                server.server_close()


class PcWorker(unittest.TestCase):
    """#187: the PC as a worker (WSL2)."""

    def test_low_disk_means_no_claim(self):
        h = FakeHub()
        with mock.patch.dict(os.environ, {"ATLAS_WORKER_MIN_FREE_GB": "5"}), \
                mock.patch("shutil.disk_usage", return_value=mock.Mock(free=2e9)):
            self.assertIn("2.0 GB free", worker.low_disk())
            worker.run(h, once=True)
        self.assertEqual([c[0] for c in h.calls], ["claim"])  # nothing claimed: it only tells the hub why (#193)
        self.assertIn("2.0 GB free", h.calls[0][1]["idle"])
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=50e9)):
            self.assertEqual(worker.low_disk(), "")

    def test_setup_scripts(self):
        import subprocess
        root = Path(__file__).resolve().parent.parent
        for name in ("setup-worker.sh", "add-worker.sh"):
            script = root / "deploy" / name
            self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0, name)
        setup = (root / "deploy" / "setup-worker.sh").read_text()
        for check in ("python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'", "gh auth status", "claude -p",
                      "tailscale", "MIN_FREE_GB", "systemd=true", "atlas-worker.service", "engine worker run"):
            self.assertIn(check, setup)
        self.assertIn("read -rsp", setup)  # the key is typed hidden, never echoed
        self.assertIn("chmod(0o600)", setup)
        add = (root / "deploy" / "add-worker.sh").read_text()
        self.assertIn("envfile.set_value", add)  # the hub's copy of the key goes to .env only
        self.assertIn("worker add", add)  # registering asks Djeddi's approval


if __name__ == "__main__":
    unittest.main()
