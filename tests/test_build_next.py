import tempfile, threading, unittest
from pathlib import Path
from unittest import mock

from engine import brain, db, devloop, edit, priority, skilltree
from services.atlas import hub
from tests.test_hub import TOKEN, get, post


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


class Lane(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.t = [db.insert(self.con, "tasks", title=f"Task {n}", status="todo", owner="ai", impact=5 - n % 5, effort_h=1) for n in range(8)]
        self.mine = db.insert(self.con, "tasks", title="Call the bank", status="todo", owner="me")
        self.con.commit()
        priority.rescore(self.con)

    def test_place_reorder_cap_and_undo(self):
        a, b, c = self.t[5], self.t[6], self.t[7]
        self.assertIn("number 1", skilltree.set_lane(self.con, a, 1))
        self.assertIn("after #", skilltree.set_lane(self.con, b, 9))  # beyond the end: joins the end
        skilltree.set_lane(self.con, c, 1)
        self.assertEqual(skilltree.lane(self.con), [c, a, b])
        with self.assertRaises(ValueError):
            skilltree.set_lane(self.con, self.mine, 1)  # his own task is not the builder's
        with mock.patch.object(skilltree, "lane_cap", return_value=3):
            msg = skilltree.set_lane(self.con, self.t[0], 2)
        self.assertIn(f"#{b} moved back", msg)
        self.assertEqual(skilltree.lane(self.con), [c, self.t[0], a])
        self.assertIn("left Build next", skilltree.set_lane(self.con, c, None))
        self.assertEqual(skilltree.lane(self.con), [self.t[0], a])
        self.assertIn("number 2", edit.move(self.con, self.t[0], "down"))  # edit.move works inside the lane
        self.assertEqual(skilltree.lane(self.con), [a, self.t[0]])
        skilltree.set_lane_order(self.con, [self.t[0], a])
        last = [r[0] for r in self.con.execute("SELECT id FROM brain_changes ORDER BY id DESC LIMIT 2")]
        brain.undo_many(self.con, last) if hasattr(brain, "undo_many") else [brain.undo(self.con, i) for i in last]
        self.assertEqual(skilltree.lane(self.con), [a, self.t[0]])  # the reorder was undone

    def test_lane_ranks_first_and_does_not_drift(self):
        low = self.t[7]
        skilltree.set_lane(self.con, low, 1)
        priority.rescore(self.con)
        self.assertEqual(priority.next_tasks(self.con, 1, owner="ai")[0]["id"], low)
        self.con.execute("UPDATE tasks SET impact = 5, confidence = 1, effort_h = 0.25 WHERE id = ?", (self.t[1],))  # another task rises
        priority.rescore(self.con)
        self.assertEqual(priority.next_tasks(self.con, 1, owner="ai")[0]["id"], low)

    def test_builder_takes_the_lane_first_and_built_tasks_leave(self):
        a, b = self.t[6], self.t[7]
        skilltree.set_lane(self.con, a, 1)
        skilltree.set_lane(self.con, b, 1)
        skilltree.queue(self.con, self.t[0], mine=True)  # his queue comes after the lane
        with mock.patch.object(devloop.overlap, "hold", return_value=""), mock.patch.object(devloop.mockup, "hold", return_value=""):
            self.assertEqual(devloop.pick_task(self.con)["id"], b)
        plan = skilltree.build_plan(self.con)
        self.assertEqual([(i["id"], i["why"], i["place"]) for i in plan["lane"]], [(b, "you put this here", 1), (a, "you put this here", 2)])
        self.assertNotIn(b, [i["id"] for i in plan["proposed"] + plan["mine"]])
        db.insert(self.con, "dev_runs", task_id=b, status="pr_open", started=db.now())  # built: its pull request is open
        self.con.execute("UPDATE tasks SET status = 'dropped' WHERE id = ?", (a,))
        self.con.commit()
        self.assertEqual(skilltree.lane_cleanup(self.con), 2)
        self.assertEqual(skilltree.lane(self.con), [])


class ThroughHub(unittest.TestCase):
    def test_action_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hq.db"
            con = db.connect(path)
            db.init(con)
            tid = db.insert(con, "tasks", title="Lane task", status="todo", owner="ai")
            con.commit(); con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/v1/actions/"
            try:
                r = post(url + "engine.build.build_next", {"task": tid, "position": 1})
                self.assertIn("number 1", r["message"])
                self.assertTrue(r["changes"])
                lane = get(url.replace("/actions/", "/functions/") + "engine.build.run_status")["result"]["plan"]["lane"]
                self.assertEqual(lane[0]["id"], tid)
                post(url + "engine.brain.undo_changes", {"changes": r["changes"]})
                self.assertEqual(get(url.replace("/actions/", "/functions/") + "engine.build.run_status")["result"]["plan"]["lane"], [])
                self.assertIn("left", post(url + "engine.build.build_next", {"task": tid, "position": 1}) and
                              post(url + "engine.build.build_next", {"task": tid, "position": 0})["message"])
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
