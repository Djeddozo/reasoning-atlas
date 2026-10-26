"""#188 parts 1 and 4: room to answer per job, cut-off answers retried once and reported, cloud timeout per job."""
import json, os, unittest
from pathlib import Path
from unittest import mock

from engine import brain, db, models


def consti(job="reevaluate", tiers=("cloud",), **job_cfg):
    return {"models": {"cloud_model": "claude-sonnet-5-5", "routing": {job: list(tiers)}, "jobs": {job: job_cfg} if job_cfg else {}}}


class CloudRoute(unittest.TestCase):
    def call(self, conf, replies, job="reevaluate"):
        sent = []

        def post(url, body, headers, timeout):
            sent.append({"max_tokens": body["max_tokens"], "timeout": timeout})
            return replies[min(len(sent), len(replies)) - 1]
        with mock.patch.object(models.loop, "load_constitution", return_value=conf), \
                mock.patch.object(models, "_post", side_effect=post), mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch.object(models, "_over_cap", return_value=False), mock.patch.object(models, "_record"), \
                mock.patch("engine.budget.allowed", return_value=True):
            try:
                return models.complete(job, "s", "u"), sent
            except models.ModelUnavailable as e:
                return e, sent

    def test_whole_plan_jobs_get_room_and_others_more_than_before(self):
        ok = [{"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}]
        self.assertEqual(self.call(consti(), ok)[1][0], {"max_tokens": 12000, "timeout": 300})
        self.assertEqual(self.call(consti("triage"), ok, "triage")[1][0]["max_tokens"], 6000)
        self.assertEqual(self.call(consti(max_tokens=3000), ok)[1][0]["max_tokens"], 3000)  # his own setting wins

    def test_cut_off_is_retried_once_with_double_room(self):
        cut = {"content": [{"type": "text", "text": '{"items": ['}], "stop_reason": "max_tokens"}
        ok = {"content": [{"type": "text", "text": '{"items": []}'}], "stop_reason": "end_turn"}
        out, sent = self.call(consti(), [cut, ok])
        self.assertEqual(out, '{"items": []}')
        self.assertEqual([s["max_tokens"] for s in sent], [12000, 24000])

    def test_still_cut_off_is_reported_as_such(self):
        cut = {"content": [{"type": "text", "text": "{"}], "stop_reason": "max_tokens"}
        out, sent = self.call(consti(max_tokens=20000), [cut])
        self.assertIsInstance(out, models.Truncated)
        self.assertEqual([s["max_tokens"] for s in sent], [20000, models.MAX_TOKENS_CEILING])
        self.assertTrue(models.why_failed(out).startswith("answer was cut off"))
        self.assertTrue(models.why_failed(models.ModelUnavailable("cloud: URLError")).startswith("no model reachable"))

    def test_timeout_per_job_with_a_ceiling(self):
        ok = [{"content": [{"type": "text", "text": "{}"}]}]
        self.assertEqual(self.call(consti(timeout=600), ok)[1][0]["timeout"], 600)
        self.assertEqual(self.call(consti(timeout=5000), ok)[1][0]["timeout"], models.TIMEOUT_CEILING)


class SubscriptionRoute(unittest.TestCase):
    def test_incomplete_json_counts_as_cut_off_and_room_is_passed_on(self):
        seen = []
        replies = ['{"items": [1, 2', '{"items": []}']

        def run(cmd, **kw):
            seen.append(kw["env"].get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"))
            return mock.Mock(returncode=0, stdout=json.dumps({"result": replies[len(seen) - 1], "usage": {}}), stderr="")
        with mock.patch.object(models.loop, "load_constitution", return_value=consti(tiers=("claude_code",))), \
                mock.patch.dict("os.environ", {}, clear=False), mock.patch("engine.budget.allowed", return_value=True), \
                mock.patch("engine.devloop.claude_path", return_value="/usr/bin/claude"), mock.patch("subprocess.run", side_effect=run):
            os.environ.pop("HQ_NO_CLAUDE_CODE", None)
            self.assertEqual(models.complete("reevaluate", "s", "u"), '{"items": []}')
        self.assertEqual(seen, ["12000", "24000"])

    def test_prose_jobs_are_not_judged_as_json(self):
        self.assertNotIn("chat", models.JSON_JOBS)
        self.assertTrue(models.is_json('Here: {"a": 1}'))
        self.assertFalse(models.is_json('{"a": [1,'))


class Reevaluate(unittest.TestCase):
    def test_error_line_says_cut_off(self):
        con = db.connect(Path(":memory:")); db.init(con); self.addCleanup(con.close)
        with mock.patch.object(models, "complete", side_effect=models.Truncated("cloud: answer was cut off")):
            self.assertTrue(brain.reevaluate(con)["error"].startswith("answer was cut off"))


class WholePlanContext(unittest.TestCase):
    """#188 part 2: reevaluate and doover see the whole open plan, more memories and thoughts, within a prompt budget."""

    def setUp(self):
        self.con = db.connect(Path(":memory:")); db.init(self.con); self.addCleanup(self.con.close)

    def test_more_tasks_with_their_description(self):
        for i in range(160):
            db.insert(self.con, "tasks", title=f"Task {i}", description="Why: " + "x" * 400, status="todo", owner="ai", score=200 - i)
        for i in range(30):
            db.insert(self.con, "memories", kind="decision", content=f"Task decision {i} " + "y" * 700, created=db.now())
        for i in range(45):
            db.insert(self.con, "thoughts", text=f"thought {i}", created=db.now())
        self.con.commit()
        ctx = json.loads(brain._context(self.con))
        self.assertEqual((len(ctx["open_tasks"]), ctx["open_task_count"]), (150, 160))
        self.assertEqual(len(ctx["open_tasks"][0]["about"]), 200)
        self.assertEqual(len(ctx["relevant_memories"]), 25)
        self.assertEqual(len(ctx["relevant_memories"][0]["content"]), 600)
        self.assertEqual(len(ctx["recent_thoughts"]), 40)
        self.assertNotIn("trimmed", ctx)

    def test_too_big_drops_the_least_relevant_first_and_logs_it(self):
        ctx = {"ideas": [{"t": "i" * 400}] * 30, "relevant_memories": [{"c": "m" * 600}] * 25,
               "recent_thoughts": [{"t": "t" * 400}] * 40, "open_tasks": [{"t": "k" * 300}] * 150}
        dropped = brain.trim(ctx, 20000)
        self.assertLessEqual(brain.tokens(json.dumps(ctx)), 20000)
        self.assertEqual(len(ctx["ideas"]), 10)  # ideas go first, down to their floor
        self.assertEqual(len(ctx["open_tasks"]), 150)  # tasks are kept longest
        self.assertEqual(dropped["ideas"], 20)
        with mock.patch.dict(brain.CONTEXT, max_tokens=200):
            for i in range(40):
                db.insert(self.con, "tasks", title=f"Task {i}", description="d" * 300, status="todo", owner="ai")
            out = json.loads(brain._context(self.con))
        self.assertEqual((out["trimmed"]["open_tasks"], len(out["open_tasks"])), (20, 20))
        self.assertTrue(self.con.execute("SELECT 1 FROM log WHERE action = 'context_trimmed'").fetchone())


if __name__ == "__main__":
    unittest.main()
