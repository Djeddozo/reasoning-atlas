"""Work stopped by a usage limit (hq.resume): found in Claude Code / Codex session logs, one Today item, resolved on resume."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import db, resume


def rec(kind, **kw):
    return json.dumps({"type": kind, "timestamp": kw.pop("ts", "2026-10-18T15:00:00Z"), "cwd": "/home/d/hq-ui", **kw})


def user(text, ts="2026-10-18T15:00:00Z"):
    return rec("user", ts=ts, message={"role": "user", "content": text})


def said(text, ts="2026-10-18T15:01:00Z", tools=()):
    return rec("assistant", ts=ts, message={"role": "assistant", "content": [{"type": "text", "text": text}, *tools]})


LIMIT = rec("assistant", ts="2026-10-18T15:05:00Z", isApiErrorMessage=True, error="rate_limit", apiErrorStatus=429,
            message={"role": "assistant", "content": [{"type": "text", "text": "Claude AI usage limit reached|1792349760"}]})


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp)
        self.claude, self.codex = self.tmp / "claude", self.tmp / "codex"
        (self.claude / "-home-d").mkdir(parents=True); (self.claude / "-home-d-hq-system--devloop-wt-9").mkdir(); (self.codex / "2026").mkdir(parents=True)
        for p in (mock.patch.object(resume, "CLAUDE_DIR", self.claude), mock.patch.object(resume, "CODEX_DIR", self.codex)):
            p.start(); self.addCleanup(p.stop)
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con); self.addCleanup(self.con.close)
        self.session = self.claude / "-home-d" / "abc-123.jsonl"

    def write(self, path, lines):
        path.write_text("\n".join(lines) + "\n")
        st = path.stat(); _os.utime(path, (st.st_atime, st.st_mtime + 1))  # a changed file is read again

    def stopped_session(self):
        edit = {"type": "tool_use", "name": "Edit", "input": {"file_path": "/home/d/hq-ui/hq/resume.py"}}
        commit = {"type": "tool_use", "name": "Bash", "input": {"command": "git add -A && git commit -q -m \"Add the scanner\""}}
        return [user("<command-name>/plugin</command-name>"), user("Build usage-limit recovery for Atlas please"),
                said("Writing the scanner now.", tools=(edit, commit)), said("Next: the Today card and tests."), LIMIT]

    def test_a_stopped_claude_session_becomes_one_item(self):
        self.write(self.session, self.stopped_session())
        self.assertEqual(resume.scan(self.con)["opened"], 1)
        [r] = resume.items(self.con)
        self.assertEqual((r["tool"], r["session_id"], r["project"]), ("claude", "abc-123", "hq-ui"))
        self.assertEqual(r["objective"], "Build usage-limit recovery for Atlas please")  # slash-command noise is skipped
        self.assertEqual((r["files"], r["commits"], r["last_said"]), (["/home/d/hq-ui/hq/resume.py"], ["Add the scanner"], "Next: the Today card and tests."))
        self.assertTrue(r["resets"])
        self.assertTrue(r["resume"].startswith("cd /home/d/hq-ui && claude -r abc-123 'You were stopped by a usage limit"))
        self.assertIn("Files it changed: /home/d/hq-ui/hq/resume.py", r["context"])
        self.assertEqual(resume.scan(self.con)["checked"], 0)  # unchanged log: not read again
        self.write(self.session, self.stopped_session())
        resume.scan(self.con)
        self.assertEqual(len(resume.items(self.con)), 1)  # never twice for the same session

    def test_resuming_resolves_and_a_new_limit_reopens(self):
        self.write(self.session, self.stopped_session()); resume.scan(self.con)
        self.write(self.session, self.stopped_session() + [said("Continuing: the card is done.", ts="2026-10-18T20:00:00Z")])
        self.assertEqual(resume.scan(self.con)["resolved"], 1)
        self.assertEqual(resume.items(self.con), [])
        row = self.con.execute("SELECT status, resolution FROM interruptions").fetchone()
        self.assertEqual(row["status"], "resolved"); self.assertIn("resumed in the same session", row["resolution"])
        again = rec("assistant", ts="2026-10-18T21:00:00Z", isApiErrorMessage=True, error="rate_limit",
                    message={"role": "assistant", "content": [{"type": "text", "text": "You've hit your limit · resets 11pm"}]})
        self.write(self.session, self.stopped_session() + [said("Continuing.", ts="2026-10-18T20:00:00Z"), again])
        self.assertEqual(resume.scan(self.con)["opened"], 1)
        [r] = resume.items(self.con)
        self.assertEqual((r["resets"], r["last_said"]), ("11pm", "Continuing."))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM interruptions").fetchone()[0], 1)

    def test_dismissed_stays_dismissed_and_other_errors_are_ignored(self):
        self.write(self.session, self.stopped_session()); resume.scan(self.con)
        resume.finish(self.con, resume.items(self.con)[0]["id"], "dismissed")
        self.write(self.session, self.stopped_session()); resume.scan(self.con)
        self.assertEqual(resume.items(self.con), [])
        other = self.claude / "-home-d" / "auth.jsonl"
        self.write(other, [user("Do something useful"), rec("assistant", isApiErrorMessage=True, error="oauth_org_not_allowed", apiErrorStatus=403,
                                                           message={"content": [{"type": "text", "text": "Your organization has disabled access"}]})])
        devloop = self.claude / "-home-d-hq-system--devloop-wt-9" / "run.jsonl"
        self.write(devloop, self.stopped_session())  # dev loop runs are reported by the dev loop itself
        self.assertEqual(resume.scan(self.con)["opened"], 0)

    def test_codex_session(self):
        log = self.codex / "2026" / "rollout-2026-10-18T14-35-54-01a1.jsonl"
        lines = [json.dumps({"type": "session_meta", "payload": {"id": "01a1", "cwd": "/home/d"}}),
                 json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}}),
                 json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix the OAuth flow in Atlas"}]}}),
                 json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call", "input": "*** Begin Patch\n*** Update File: engine/claudeauth.py\n"}}),
                 json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Testing the reconnect flow."}]}}),
                 json.dumps({"timestamp": "2026-10-18T14:12:40Z", "type": "event_msg", "payload": {"type": "task_complete", "error": {
                     "message": "You've hit your usage limit. Try again at 7:36 PM.", "codex_error_info": "usage_limit_exceeded"}}})]
        self.write(log, lines)
        resume.scan(self.con)
        [r] = resume.items(self.con)
        self.assertEqual((r["tool"], r["objective"], r["files"], r["resets"]), ("codex", "Fix the OAuth flow in Atlas", ["engine/claudeauth.py"], "7:36 PM"))
        self.assertEqual(r["resume"], "cd /home/d && codex resume 01a1")

    def test_devloop_run_item_resolves_when_the_task_delivers(self):
        task = {"id": 50, "title": "Show the last re-rank"}
        a = resume.record_devloop(self.con, 41, task, "Claude AI usage limit reached|1792349760", "checkpoint: card done")
        self.assertEqual(resume.record_devloop(self.con, 41, task, "again"), a)  # one item per interrupted run
        [r] = resume.items(self.con)
        self.assertEqual((r["tool"], r["task_id"], r["resume"], r["last_said"]), ("devloop", 50, "", "checkpoint: card done"))
        resume.resolve_task(self.con, 50); self.con.commit()
        self.assertEqual(resume.items(self.con), [])

    def test_devloop_does_not_repair_after_a_usage_limit(self):
        from engine import devloop
        with mock.patch.object(devloop.usage, "killed", return_value=False):
            self.assertFalse(devloop.may_repair(self.con, 7, "Claude AI usage limit reached|1792349760"))
            self.assertTrue(devloop.may_repair(self.con, 7, "Tests failed"))

    def test_limit_texts(self):
        for t in ("Claude AI usage limit reached|1792349760", "You've hit your limit · resets 7pm", "5-hour limit reached",
                  "Credit balance is too low", "usage_limit_exceeded"):
            self.assertTrue(resume.is_limit(t), t)
        for t in ("Tests pass.", "API Error: 500 Internal server error", "Your organization has disabled access"):
            self.assertFalse(resume.is_limit(t), t)
        self.assertEqual(resume.resets("You've hit your usage limit. Try again at 7:36 PM."), "7:36 PM")


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to execute the browser renderer")
class InterruptionsRuntimeTest(unittest.TestCase):
    def test_card_escapes_and_offers_resume(self):
        page = (Path(__file__).resolve().parents[1] / "engine/dashboard.html").read_text()
        source = page[page.index("const TOOL = {"):page.index("async function copyText(")]
        harness = """
const assert = require('node:assert/strict');
const els = {}; const $ = id => els[id] || (els[id] = {innerHTML:'', textContent:'', hidden:true});
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
""" + source + """
renderInterruptions([{id:2, tool:'claude', project:'hq-ui', objective:'Build <it>', stopped_at:'2026-10-18T15:05:00Z', resets:'7pm',
  files:['/home/d/hq-ui/hq/resume.py'], commits:['Add scanner'], last_said:'Next: tests', resume:"cd ~ && claude -r abc 'go'", context:'ctx', task_id:null},
 {id:3, tool:'devloop', project:'hq-system', objective:'#50 X', stopped_at:'', resets:'', files:[], commits:[], last_said:'', resume:'', context:'c', task_id:50}]);
const h = $('intrList').innerHTML;
assert.equal($('intrCard').hidden, false);
assert.ok(h.includes('Build &lt;it&gt;') && h.includes('hq/resume.py') && h.includes('limit resets 7pm'));
assert.ok(h.includes("copyText(INTR[0].resume") && !h.includes('runTask('), 'a dev loop item has no resume button (#158)');
assert.ok(h.includes('First in line: the builder continues this task'));
assert.ok(h.includes('claude -r abc &#39;go&#39;'), 'the command is shown escaped');
renderInterruptions([]); assert.equal($('intrCard').hidden, true);
"""
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
