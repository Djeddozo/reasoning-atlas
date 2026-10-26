import json, unittest
from pathlib import Path
from unittest import mock

from engine import chat, db, models


class PerJob(unittest.TestCase):
    def test_chat_can_have_its_own_model_and_thinking(self):
        consti = {"models": {"cloud_model": "claude-sonnet-5-5", "routing": {"chat": ["cloud"]},
                             "jobs": {"chat": {"model": "claude-opus-5-5", "thinking_tokens": 4000, "max_tokens": 8000}}}}
        sent = {}

        def post(url, body, headers, timeout):
            sent.update(body=body, timeout=timeout)
            return {"content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "Opus here."}], "usage": {}}
        with mock.patch.object(models.loop, "load_constitution", return_value=consti), \
                mock.patch.object(models, "_post", side_effect=post), mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch.object(models, "_over_cap", return_value=False), mock.patch.object(models, "_record"):
            self.assertEqual(models.complete("chat", "s", "u"), "Opus here.")
            self.assertEqual((sent["body"]["model"], sent["body"]["thinking"]["budget_tokens"], sent["body"]["max_tokens"], sent["timeout"]),
                             ("claude-opus-5-5", 4000, 8000, 300))
            models.complete("other", "s", "u")  # other jobs keep the shared model and no thinking
            self.assertEqual(sent["body"]["model"], "claude-sonnet-5-5")
            self.assertNotIn("thinking", sent["body"])

    def test_subscription_route_gets_model_and_thinking(self):
        consti = {"models": {"routing": {"chat": ["claude_code"]}, "jobs": {"chat": {"claude_code_model": "opus", "thinking_tokens": 4000}}}}
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, env=kw["env"])
            return mock.Mock(returncode=0, stdout=json.dumps({"result": "ok", "usage": {}}), stderr="")
        with mock.patch.object(models.loop, "load_constitution", return_value=consti), mock.patch.dict("os.environ", {}, clear=False), \
                mock.patch("engine.devloop.claude_path", return_value="/usr/bin/claude"), mock.patch("subprocess.run", side_effect=run):
            import os
            os.environ.pop("HQ_NO_CLAUDE_CODE", None)
            self.assertEqual(models.complete("chat", "s", "u"), "ok")
        self.assertIn("opus", seen["cmd"])
        self.assertEqual(seen["env"]["MAX_THINKING_TOKENS"], "4000")

    def test_cortex_sees_matching_tasks_beyond_the_top(self):
        con = db.connect(Path(":memory:"))
        db.init(con)
        for i in range(45):
            db.insert(con, "tasks", title=f"Filler {i}", status="todo", owner="me", score=100 - i)
        low = db.insert(con, "tasks", title="Renew the mortgage paperwork", status="todo", owner="me", score=1)
        chat.add(con, "me", "what about the mortgage?")
        ctx = json.loads(chat._context(con))
        self.assertEqual(ctx["open_task_count"], 46)
        self.assertNotIn(low, [t["id"] for t in ctx["open_tasks"]])
        self.assertEqual([t["id"] for t in ctx["matching_tasks"]], [low])


if __name__ == "__main__":
    unittest.main()
