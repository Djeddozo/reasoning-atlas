"""Run in your terminal (hq.commands): steps left for Djeddi's shell, copyable as a shell line or a Claude prompt."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from engine import commands, dashboard, db


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:"); self.con.row_factory = sqlite3.Row; db.init(self.con)
        self.addCleanup(self.con.close)

    def test_add_list_and_both_copy_forms(self):
        cid = commands.add(self.con, "Open the pull request", "gh pr create --fill --head fix",
                           why="Claude committed the crash-loop fix; only Djeddi publishes branches.", cwd="~/hq-ui")
        [c] = commands.items(self.con)
        self.assertEqual(c["id"], cid)
        self.assertEqual(c["shell"], "cd ~/hq-ui && gh pr create --fill --head fix  # Atlas: Open the pull request")
        self.assertIn("Context: Claude committed the crash-loop fix", c["claude"])
        self.assertIn("in ~/hq-ui", c["claude"]); self.assertTrue(c["claude"].endswith("gh pr create --fill --head fix"))
        self.assertIn("#1 Open the pull request", commands.text(self.con))

    def test_same_open_command_is_not_added_twice(self):
        a = commands.add(self.con, "Old title", "make deploy")
        b = commands.add(self.con, "New title", "make deploy", why="newer context")
        self.assertEqual(a, b)
        self.assertEqual([(c["title"], c["why"]) for c in commands.items(self.con)], [("New title", "newer context")])

    def test_rejects_multi_line_and_secrets(self):
        with self.assertRaises(ValueError):
            commands.add(self.con, "Two steps", "cd x\nls y")
        with self.assertRaises(ValueError):
            commands.add(self.con, "Login", "export API_KEY=abcdef123456")
        with self.assertRaises(ValueError):
            commands.add(self.con, "", "ls")
        self.assertEqual(commands.items(self.con), [])

    def test_done_and_dismiss_leave_the_list(self):
        a = commands.add(self.con, "One", "ls"); b = commands.add(self.con, "Two", "pwd")
        self.assertIn("done", commands.finish(self.con, a))
        self.assertIn("dismissed", dashboard.act(self.con, "cmd_dismiss", b, ""))
        self.assertEqual(commands.items(self.con), [])
        with self.assertRaises(ValueError):
            commands.finish(self.con, a)

    def test_title_hash_cannot_break_the_comment(self):
        commands.add(self.con, "Merge PR #58", "gh pr view 58")
        self.assertTrue(commands.items(self.con)[0]["shell"].endswith("# Atlas: Merge PR 58"))


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to execute the browser renderer")
class CommandsRuntimeTest(unittest.TestCase):
    def test_render_escapes_and_keeps_text_out_of_onclick(self):
        page = (Path(__file__).resolve().parents[1] / "engine/dashboard.html").read_text()
        source = page[page.index("let CMDS = [];"):page.index("async function copyCmd(")]
        harness = """
const assert = require('node:assert/strict');
const els = {}; const $ = id => els[id] || (els[id] = {innerHTML:'', textContent:'', hidden:true});
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
""" + source + """
renderCommands([{id:3, title:'Open <b>', why:'it\\'s "needed"', shell:'gh pr view 3  # Atlas: Open b', claude:'x', created:'2026-10-18T17:30:00', source:'claude-code', cwd:'~/hq-ui'}]);
const h = $('cmdList').innerHTML;
assert.equal($('cmdCard').hidden, false);
assert.ok(h.includes('Open &lt;b&gt;') && h.includes('it&#39;s &quot;needed&quot;'));
assert.ok(h.includes('<span class="c">  # Atlas: Open b</span>'), 'the comment is dimmed');
assert.ok(h.includes("copyCmd(0,'shell')") && h.includes("act('cmd_done',CMDS[0].id)"));
assert.ok(!h.includes('onclick="gh'), 'command text never goes into an onclick attribute');
renderCommands([]); assert.equal($('cmdCard').hidden, true);
"""
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
