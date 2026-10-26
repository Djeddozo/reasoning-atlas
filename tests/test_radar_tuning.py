import sqlite3
import unittest

from engine import db, radar


class RadarTuningTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con); self.addCleanup(self.con.close)
        self.rules = {"radar": {"interests": ["agent", "voice"]}}

    def _add(self, n, title, state):
        self.con.execute("INSERT INTO radar(source, external_id, title, url, summary, popularity, matched, score, found, state) "
                         "VALUES ('hn', ?, ?, '', '', 1, ?, 1, ?, ?)", (str(n), title, "agent" if "agent" in title else "voice", db.now(), state))

    def test_review_flags_noise_and_useful(self):
        for i in range(3):
            self._add(i, "agent hype", "dismissed")
        for i in range(3, 5):
            self._add(i, "voice tool", "starred")
        got = {x["word"]: x["verdict"] for x in radar.review(self.con, self.rules)["interests"]}
        self.assertEqual(got["agent"], "noisy")
        self.assertEqual(got["voice"], "useful")

    def test_tuning_changes_interests_and_scoring(self):
        radar.save_tuning(self.con, mute=["agent"], add=["Robotics"], boost=["voice", "agent"])
        t = radar.tuning(self.con)
        self.assertEqual(t["boost"], ["voice"])  # muted word cannot stay boosted
        keys = radar.interests(self.con, self.rules)
        self.assertNotIn("agent", keys)
        self.assertIn("robotics", keys)
        item = {"title": "voice robotics", "summary": "", "source": "hn", "popularity": 0}
        plain = radar.score_item(item, keys)[0]
        self.assertGreater(radar.score_item(item, keys, ["voice"])[0], plain)
        self.assertEqual(radar.review(self.con, self.rules)["interests"][-1]["verdict"], "muted")

    def test_invalid_words_dropped(self):
        radar.save_tuning(self.con, add=["ok", "bad word!", "x"])
        self.assertEqual(radar.tuning(self.con)["add"], ["ok"])


if __name__ == "__main__":
    unittest.main()
