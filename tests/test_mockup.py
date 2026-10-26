import base64, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

from engine import db, devloop, models, mockup
from services.atlas import hub
from tests.test_hub import TOKEN, get, post

PAGE = ("SECTION: Today\nNOTES: A card on Today with the new list.\n<!doctype html><html><head><style>body{background:#050a1b}"
        "</style></head><body><div class='new' onclick=\"steal()\">new</div><script>alert(1)</script>"
        "<img src='https://evil.example/x.png'><a href='#top'>top</a><svg/onload=alert(2)></svg>"
        + "<p>Atlas content</p>" * 20 + "</body></html>")


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def database():
    con = db.connect(Path(":memory:"))
    db.init(con)
    return con


class Rules(unittest.TestCase):
    def test_visual_side_and_section(self):
        self.assertTrue(mockup.is_visual("Show mockups in the Brain", ""))
        self.assertTrue(mockup.is_visual("Redesign the Today card", ""))
        self.assertFalse(mockup.is_visual("Fix the backup cron job", "retry when the disk is busy"))
        self.assertEqual(mockup.guess_section("Show it in the Brain"), "Cortex")
        self.assertEqual(mockup.guess_section("A new panel on Today"), "Today")
        self.assertEqual(mockup.guess_section("Drag tasks in the skill tree"), "Map › Skill tree")
        self.assertEqual(mockup.guess_section("something unrelated"), "")

    def test_sanitise_removes_everything_active(self):
        _, notes, page = mockup.parse_reply(PAGE)
        clean = mockup.sanitise(page)
        for bad in ("<script", "onclick", "onload", "evil.example", "alert(1)"):
            self.assertNotIn(bad, clean)
        self.assertIn("href='#top'", clean)
        self.assertIn("Content-Security-Policy", clean)
        self.assertIn("default-src 'none'", clean)
        self.assertEqual(notes, "A card on Today with the new list.")
        css = mockup.sanitise("<html><head><style>@import 'http://x/y.css';a{background:url(http://x/t.png)}</style></head></html>")
        self.assertNotIn("http://x", css)
        self.assertIn("Content-Security-Policy", mockup.sanitise("<p>bare</p>"))
        self.assertNotIn("javascript:", mockup.sanitise("<a href='javascript:alert(1)'>x</a><div style='x:javascript:1'>"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.con = database()
        self.ui = db.insert(self.con, "tasks", title="Show build lanes as cards on Today", description="a new panel", owner="ai", status="todo", score=5)
        self.plain = db.insert(self.con, "tasks", title="Retry the backup", description="cron", owner="ai", status="todo", score=4)
        self.con.commit()

    def test_choice_is_offered_only_for_visual_tasks(self):
        tasks = [c["task"] for c in mockup.candidates(self.con)]
        self.assertEqual(tasks, [self.ui])
        self.assertEqual(mockup.candidates(self.con)[0]["section"], "Today")
        self.assertIn("without a mockup", mockup.choose(self.con, self.ui, False))
        self.assertEqual(mockup.candidates(self.con), [])
        self.assertEqual(mockup.hold(self.con, self.ui), "")  # skipped: build as usual

    def test_made_outside_rounds_approval_and_build_hold(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(mockup, "IMAGES", Path(folder)), \
                mock.patch.object(models, "complete") as call:
            msg = mockup.choose(self.con, self.ui, True)
            self.assertIn("Claude or ChatGPT", msg)
            call.assert_not_called()  # Atlas does not draw pictures itself
            m = mockup.for_task(self.con, self.ui)
            self.assertEqual((m["status"], m["section"]), ("open", "Today"))
            self.assertTrue(mockup.hold(self.con, self.ui))
            with self.assertRaises(ValueError):
                mockup.approve(self.con, m["id"])  # nothing to approve yet
            prompt = mockup.view(self.con)["mockups"][0]["prompt"]
            self.assertIn("Show build lanes as cards on Today", prompt)
            self.assertIn("#050a1b", prompt)
            out = mockup.add_round(self.con, m["id"], PNG, by="claude")
            self.assertEqual((out["round"], out["image"]), (1, f"mockup-{m['id']}-round-1.png"))
            self.assertEqual(mockup.image(self.con, m["id"], 1), (PNG, "image/png"))
            self.assertIn("Less text, bigger cards", mockup.outside_prompt(self.con, m["id"], "Less text, bigger cards"))
            out = mockup.add_round(self.con, m["id"], PAGE.encode(), feedback="Less text, bigger cards", by="chatgpt")
            self.assertEqual((out["round"], out["image"]), (2, ""))
            with self.assertRaises(ValueError):
                mockup.add_round(self.con, m["id"], b"just words")
            with self.assertRaises(ValueError):
                mockup.image(self.con, m["id"], 2)  # round 2 is a page
            v = mockup.view(self.con)
            self.assertEqual([r["round"] for r in v["mockups"][0]["rounds"]], [1, 2])
            self.assertNotIn("<script", v["mockups"][0]["rounds"][1]["html"])
            self.assertIn("Less text, bigger cards", v["mockups"][0]["prompt"])
            self.assertIn("approved", mockup.approve(self.con, m["id"]))
            self.assertEqual(mockup.hold(self.con, self.ui), "")
            block = mockup.prompt_block(self.con, self.ui)
            self.assertIn("Approved mockup", block)
            self.assertIn("Less text, bigger cards", block)
            with self.assertRaises(ValueError):
                mockup.add_round(self.con, m["id"], PNG)  # approved: reopen first
            mockup.reopen(self.con, m["id"])
            self.assertTrue(mockup.hold(self.con, self.ui))
            mockup.add_round(self.con, m["id"], PNG, by="claude")
            mockup.approve(self.con, m["id"])
            self.assertIn("Read tool", mockup.prompt_block(self.con, self.ui))
            mockup.reopen(self.con, m["id"])
            self.assertIn("set aside", mockup.set_aside(self.con, m["id"]))
            self.assertEqual(mockup.hold(self.con, self.ui), "")
            call.assert_not_called()

    def test_wireframe_when_no_model(self):
        with mock.patch.object(models, "complete", side_effect=models.ModelUnavailable("cloud: no key")):
            out = mockup.request(self.con, "Mockup gallery", "a grid of mockups in the Brain")
        self.assertEqual((out["by"], out["section"]), ("rule", "Cortex"))
        self.assertIn("Mockup gallery", out["html"])
        self.assertIn("not reachable", out["notes"])
        with mock.patch.object(models, "complete", return_value="no html here"):
            out = mockup.draw(self.con, out["id"], "how would it look on Today?")
        self.assertEqual((out["by"], out["section"]), ("rule", "Today"))
        with self.assertRaises(ValueError):
            mockup.request(self.con, "  ")
        with self.assertRaises(ValueError):
            mockup.request(self.con, "x", section="Nowhere")

    def test_dev_loop_skips_a_task_waiting_for_its_mockup(self):
        mockup.choose(self.con, self.ui, True)
        plan = {"mine": [{"id": self.ui, "state": "ready", "owner": "ai", "title": "x"},
                         {"id": self.plain, "state": "ready", "owner": "ai", "title": "y"}], "proposed": []}
        with mock.patch("engine.skilltree.build_plan", return_value=plan), mock.patch.object(devloop.overlap, "hold", return_value=""):
            self.assertEqual(devloop.pick_task(self.con)["id"], self.plain)


SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 200' onload=\"fetch('https://evil.example')\">"
       "<title>Weekly progress card</title><script>alert(1)</script>"
       "<image href='https://evil.example/t.png'/><a href='javascript:alert(2)'><rect width='400' height='200' fill='#0b1124'/></a>"
       "<foreignObject><iframe src='https://evil.example'></iframe></foreignObject>"
       "<style>@import url(https://evil.example/x.css);rect{fill:url(https://evil.example/p)}</style>"
       "<text x='20' y='40' fill='#f2edff'>Progress</text></svg>")


class Drawings(unittest.TestCase):
    """#183: svg and html blocks from Cortex, drawn in the chat and kept as mockups."""

    def setUp(self):
        self.con = database()
        self.task = db.insert(self.con, "tasks", title="A progress card on Today", owner="ai", status="todo", score=3)
        self.idea = db.insert(self.con, "ideas", title="A weekly review screen")
        self.con.commit()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patch = mock.patch.object(mockup, "IMAGES", Path(self.dir.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_svg_is_sanitised_and_framed(self):
        page = mockup.drawing(SVG, "svg")
        for bad in ("<script", "onload", "evil.example", "javascript:", "alert(", "<iframe", "@import"):
            self.assertNotIn(bad, page)
        self.assertIn("<svg", page)
        self.assertIn(">Progress</text>", page)
        self.assertEqual(page.count("Content-Security-Policy"), 1)
        self.assertIn("default-src 'none'", page)
        self.assertTrue(page.startswith("<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\""))

    def test_html_kinds_and_limits(self):
        page = mockup.drawing("<div style='color:#a76bff' onclick='x()'>Card</div><link rel=stylesheet href='https://x/y.css'>", "HTML")
        self.assertIn("Card</div>", page)
        for bad in ("onclick", "<link", "https://x"):
            self.assertNotIn(bad, page)
        with self.assertRaises(ValueError):
            mockup.drawing("<svg></svg>", "js")
        with self.assertRaises(ValueError):
            mockup.drawing("  ", "svg")
        with self.assertRaises(ValueError):
            mockup.drawing("<div>no svg here</div>", "svg")
        with self.assertRaises(ValueError):  # too large is refused, never cut in half
            mockup.drawing("<svg>" + "<rect/>" * mockup.MAX_DRAWING + "</svg>", "svg")
        # a policy planted in the body is removed with every other meta tag; the real one opens the head, once
        planted = mockup.drawing("<html><head></head><body>" + mockup._CSP + "</body></html>", "html")
        self.assertTrue(planted.startswith("<html><head>" + mockup._CSP))
        self.assertEqual(planted.count("Content-Security-Policy"), 1)
        self.assertEqual(mockup.sanitise(planted), planted)  # sanitising again changes nothing

    def test_save_for_a_task_then_next_round(self):
        out = mockup.save_drawing(self.con, SVG, "svg", task_id=self.task)
        self.assertEqual((out["round"], out["task"], out["title"]), (1, self.task, "Weekly progress card"))
        m = mockup.for_task(self.con, self.task)
        self.assertEqual((m["id"], m["status"]), (out["id"], "open"))
        self.assertTrue(mockup.hold(self.con, self.task))  # the builder waits for approval, as with any mockup
        saved = (Path(self.dir.name) / out["file"]).read_text()
        self.assertNotIn("<script", saved)
        again = mockup.save_drawing(self.con, "<svg><rect/></svg>", "svg", task_id=self.task, feedback="less text")
        self.assertEqual((again["id"], again["round"]), (out["id"], 2))  # one mockup per task, a new round
        v = mockup.view(self.con)["mockups"][0]
        self.assertEqual([r["by"] for r in v["rounds"]], ["cortex", "cortex"])
        self.assertEqual(v["rounds"][1]["feedback"], "less text")
        self.assertIn("Made in Cortex", v["rounds"][0]["notes"])
        mockup.approve(self.con, out["id"])
        self.assertIn("<svg", mockup.prompt_block(self.con, self.task))
        with self.assertRaises(ValueError):
            mockup.save_drawing(self.con, SVG, "svg", task_id=self.task)

    def test_save_for_an_idea_or_nothing(self):
        out = mockup.save_drawing(self.con, "<div>Review</div>", "html", idea_id=self.idea)
        v = next(m for m in mockup.view(self.con)["mockups"] if m["id"] == out["id"])
        self.assertEqual((v["idea_id"], v["idea_title"], v["task_id"]), (self.idea, "A weekly review screen", None))
        self.assertEqual(v["title"], "A weekly review screen")
        self.assertEqual(mockup.save_drawing(self.con, "<svg/>", "svg", idea_id=self.idea)["id"], out["id"])
        free = mockup.save_drawing(self.con, "<svg/>", "svg", title="  A   sketch ")
        self.assertEqual((free["title"], free["task"], free["idea"]), ("A sketch", None, None))
        for bad in ({"task_id": 999}, {"idea_id": 999}, {"task_id": self.task, "idea_id": self.idea}):
            with self.assertRaises(ValueError):
                mockup.save_drawing(self.con, "<svg/>", "svg", **bad)


class ThroughHub(unittest.TestCase):
    def test_read_and_act(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hq.db"
            con = db.connect(path)
            db.init(con)
            tid = db.insert(con, "tasks", title="Redesign the Research page", description="", owner="ai", status="todo", score=3)
            con.commit()
            con.close()
            server = hub.make_server("127.0.0.1", 0, TOKEN, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                view = get(url + "/v1/functions/engine.brain.mockups")["result"]
                self.assertEqual(view["choose"][0]["task"], tid)
                r = post(url + "/v1/actions/engine.brain.mockup", {"choose": tid, "visual": True})
                self.assertIn("open in Cortex", r["message"])
                mid = get(url + "/v1/functions/engine.brain.mockups")["result"]["mockups"][0]["id"]
                with self.assertRaises(Exception):
                    post(url + "/v1/actions/engine.brain.mockup", {"mockup": mid, "feedback": "More contrast"})
                with mock.patch.object(mockup, "IMAGES", Path(folder) / "pics"):
                    data = base64.b64encode(PNG).decode()
                    self.assertIn("Round 1", post(url + "/v1/actions/engine.brain.mockup", {"mockup": mid, "data": data, "by": "chatgpt"})["message"])
                    pic = get(url + f"/v1/functions/engine.brain.mockups?image={mid}&round=1")["result"]
                    self.assertEqual((pic["type"], base64.b64decode(pic["data"])), ("image/png", PNG))
                self.assertIn("approved", post(url + "/v1/actions/engine.brain.mockup", {"mockup": mid, "do": "approve"})["message"])
                page = post(url + "/v1/actions/engine.brain.mockup", {"preview": {"code": SVG, "kind": "svg"}})["message"]
                self.assertIn("<svg", page)
                self.assertNotIn("<script", page)
                with mock.patch.object(mockup, "IMAGES", Path(folder) / "pics"):
                    r = post(url + "/v1/actions/engine.brain.mockup", {"drawing": {"code": SVG, "kind": "svg", "task": "", "idea": ""}})
                self.assertIn("Saved as mockup", r["message"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
