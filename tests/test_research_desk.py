"""Research: light feeds (RSS/Atom/JSON, no model calls), settings and due scans, rule-based relevance, deep research."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import pathlib as _pl; from hq import loop as _loop; _loop.LOCAL = _pl.Path("/nonexistent/hq-local.toml")
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest import mock

from engine import dashboard, db, deepresearch, models, radar

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Introducing a faster coding agent</title><link>https://lab.example/a</link><guid>a1</guid><description>&lt;p&gt;Agents that write code.&lt;/p&gt;</description></item>
<item><title>Company picnic</title><link>https://lab.example/b</link><guid>b1</guid><description>Fun.</description></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>tag:github.com,2008:Release/1</id><title>v2.0: local models via Ollama</title><link rel="alternate" href="https://github.com/o/r/releases/tag/v2.0"/><content>Run models locally.</content></entry>
</feed>"""


class FeedTest(unittest.TestCase):
    def test_rss_and_atom(self):
        with mock.patch.object(radar, "_get", return_value=RSS):
            items = list(radar._feed("labs", "u", 5, "Lab"))
        self.assertEqual([(i["title"], i["external_id"], i["summary"]) for i in items][0], ("Lab: Introducing a faster coding agent", "a1", "Agents that write code."))
        with mock.patch.object(radar, "_get", return_value=ATOM):
            [rel] = list(radar.fetch_releases(3, ["o/r"]))
        self.assertEqual((rel["source"], rel["title"], rel["url"]), ("releases", "o/r: v2.0: local models via Ollama", "https://github.com/o/r/releases/tag/v2.0"))

    def test_one_lab_down_keeps_the_others(self):
        def get(url, headers=None):
            if "openai" in url:
                raise OSError("down")
            return RSS
        with mock.patch.object(radar, "_get", side_effect=get):
            self.assertEqual(len(list(radar.fetch_labs(5))), 2)

    def test_explain_is_rule_based(self):
        e = radar.explain({"title": "Ollama adds tool calling for local agents", "source": "releases"})
        self.assertEqual((e["impact"], e["topics"][:3]), ("high", ["agents", "tools & MCP", "local models"]))
        self.assertTrue(e["why"].endswith("."))
        self.assertEqual(radar.explain({"title": "Company picnic"})["topics"], [])


class SettingsAndScanTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con); self.addCleanup(self.con.close)

    def test_settings_are_clamped_and_due_follows_them(self):
        radar.save_settings(self.con, {"every_hours": 9999, "per_source": 1, "sources": ["labs", "nope"], "repos": ["ok/repo", "bad repo"]})
        s = radar.settings(self.con)
        self.assertEqual((s["every_hours"], s["per_source"], s["sources"], s["repos"]), (168, 3, ["labs"], ["ok/repo"]))
        self.assertTrue(radar.due(self.con))  # never scanned
        db.log(self.con, "hq", "radar_scan", "{}"); self.con.commit()
        self.assertFalse(radar.due(self.con))
        old = (datetime.now() - timedelta(hours=200)).isoformat(timespec="seconds")
        self.con.execute("UPDATE log SET ts = ? WHERE action = 'radar_scan'", (old,)); self.con.commit()
        self.assertTrue(radar.due(self.con))

    def test_scan_uses_settings_and_the_feed_shows_only_what_matters(self):
        radar.save_settings(self.con, {"sources": ["labs"], "per_source": 10})
        with mock.patch.object(radar, "_get", return_value=RSS):
            report = radar.scan(self.con, {"radar": {"interests": []}})
        self.assertEqual(report, {"labs": 2})  # both fake labs return the same two ids: stored once (deduplicated)
        feed = dashboard._research(self.con)["feed"]
        self.assertTrue(feed and all("picnic" not in i["title"].lower() for i in feed))  # irrelevant items stay out
        self.assertEqual(feed[0]["label"], "AI labs")
        self.assertIn("why", feed[0])


class DeepResearchTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con); self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO radar (source, external_id, title, url, summary, found) VALUES ('labs','x','Local models get tool calling','https://lab.example/x','Ollama update', ?)", (db.now(),))
        db.insert(self.con, "goals", title="Run Atlas from my phone by voice")
        self.con.commit()

    def test_answer_is_stored_and_becomes_an_idea(self):
        answer = json.dumps({"summary": "Ollama now supports tools.", "matters": "Atlas could sort dumps locally.", "risks": "Slower on the Pi.",
                             "next_steps": ["Try it on the GPU PC"], "idea": "Local brain dump sorting",
                             "sources": ["https://lab.example/x", "https://made.up/link"]})
        with mock.patch.object(models, "complete", return_value=answer) as call:
            n = deepresearch.ask(self.con, "Could local models with tool calling sort brain dumps?")
        self.assertEqual(call.call_args.args[0], "research")
        sent = json.loads(call.call_args.args[2])
        self.assertEqual(sent["radar_items"][0]["url"], "https://lab.example/x")
        self.assertEqual((n["status"], n["answer"]["sources"]), ("done", ["https://lab.example/x"]))  # only links Atlas fetched
        iid = deepresearch.to_idea(self.con, n["id"])
        idea = self.con.execute("SELECT title, description FROM ideas WHERE id = ?", (iid,)).fetchone()
        self.assertEqual(idea["title"], "Local brain dump sorting"); self.assertIn("Try it on the GPU PC", idea["description"])
        self.assertEqual(deepresearch.note(self.con, n["id"])["idea_id"], iid)

    def test_no_model_is_honest_and_short_questions_are_refused(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("cloud: no credit")):
            n = deepresearch.ask(self.con, "Is tool calling in local models ready?")
        self.assertEqual(n["status"], "failed"); self.assertIn("No model was reachable", n["answer"]["summary"])
        with self.assertRaises(ValueError):
            deepresearch.ask(self.con, "why")
        with mock.patch.object(models, "complete", return_value="{}"):
            self.assertIn("Research #", dashboard.act(self.con, "deep_research", None, "What does tool calling change for Atlas?"))


if __name__ == "__main__":
    unittest.main()
