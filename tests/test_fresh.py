import unittest
from pathlib import Path
from datetime import datetime, timedelta

from engine import db, fresh


class Fresh(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(Path(":memory:"))
        db.init(self.con)
        self.now = datetime(2026, 10, 25, 12, 0, 0)

    def tearDown(self):
        self.con.close()

    def at(self, minutes_ago):
        return (self.now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")

    def test_one_rule_default_hour(self):
        since = fresh.cutoff(self.con, self.now)
        self.assertTrue(fresh.is_new(self.at(59), since))
        self.assertFalse(fresh.is_new(self.at(61), since))
        self.assertFalse(fresh.is_new(None, since))  # unknown date stays unknown, never new
        self.assertFalse(fresh.is_new("not a date", since))

    def test_window_is_configurable_and_bounded(self):
        self.assertIn("2 h", fresh.set_window(self.con, 120))
        self.assertEqual(fresh.window(self.con), 120)
        self.assertTrue(fresh.is_new(self.at(100), fresh.cutoff(self.con, self.now)))
        for bad in (0, 4, 1441, "x", None):
            with self.assertRaises(ValueError):
                fresh.set_window(self.con, bad)
        db.set_state(self.con, "fresh_minutes", "99999")  # a stored value out of range is clamped
        self.assertEqual(fresh.window(self.con), 1440)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log WHERE action = 'fresh_window'").fetchone()[0], 1)

    def test_recent_lists_open_items_newest_first(self):
        db.insert(self.con, "tasks", title="Fresh task", created=self.at(5))
        db.insert(self.con, "tasks", title="Old task", created=self.at(500))
        db.insert(self.con, "tasks", title="Fresh but done", status="done", created=self.at(3))
        db.insert(self.con, "ideas", title="Fresh idea", created=self.at(2))
        db.insert(self.con, "ideas", title="Promoted idea", gate="promoted", created=self.at(1))
        db.insert(self.con, "goals", title="Fresh goal", created=self.at(10))
        r = fresh.recent(self.con, self.now)
        self.assertEqual([(x["kind"], x["title"]) for x in r["items"]],
                         [("idea", "Fresh idea"), ("task", "Fresh task"), ("goal", "Fresh goal")])
        self.assertTrue(all(x["new"] for x in r["items"]))
        self.assertEqual(r["minutes"], 60)


if __name__ == "__main__":
    unittest.main()
