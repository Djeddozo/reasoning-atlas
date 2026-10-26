"""#179: the builder's log lines as plain sentences; raw lines only behind the Output button."""
import unittest

from engine import buildsteps


class PlainStepTest(unittest.TestCase):
    def text(self, line):
        s = buildsteps.plain(line)
        return s and s["text"]

    def test_every_builder_line_is_a_full_sentence(self):
        lines = ["10:00 started", "10:00 model: sonnet (standard): ordinary task", "10:00 claimed by worker zenunit",
                 "10:01 → Read: /x/engine/devloop.py", "10:01 → Edit: engine/db.py", "10:02 → Grep: lanes",
                 "10:02 → Bash: python3 -m unittest discover tests", "10:03 → Bash: git commit -m x",
                 "10:03 → Bash: ls engine", "10:03 → Bash: ./hq.sh next --owner ai", "10:03 → Bash: make",
                 "10:04 → TodoWrite: ", "10:04 → Agent: x", "10:04 → WebFetch: https://example.org/a",
                 "10:04 → NotebookEdit: x", "10:05 ■ finished after 12 turns", "10:05 ■ finished after 3 turns (error)",
                 "10:05 checking the work: running the tests", "10:06 check: ok (66 s)", "10:06 check: tests failed (70 s)",
                 "10:06 check: weird", "10:06 self-repair: tests_failed, starting one repair run",
                 "10:07 nearly at the turn limit: saving a checkpoint", "10:08 stopped: the time limit (40 min)",
                 "10:08 pushing the branch and opening the PR", "10:09 PR ready (12 s)"]
        for line in lines:
            t = self.text(line)
            self.assertTrue(t, line)
            self.assertTrue(t[0].isupper() and t.endswith("."), (line, t))
            self.assertNotIn("→", t)
        self.assertEqual(self.text("10:01 → Read: /x/engine/devloop.py"), "Reading devloop.py to understand the code.")
        self.assertIn("tests", self.text("10:02 → Bash: python3 -m unittest discover tests"))
        self.assertIn("sonnet", self.text("10:00 model: sonnet (standard): ordinary task"))
        self.assertIn("66 s", self.text("10:06 check: ok (66 s)"))
        self.assertIn("tests failed", self.text("10:06 self-repair: tests_failed, starting one repair run"))

    def test_raw_output_is_not_a_step(self):
        self.assertIsNone(buildsteps.plain("  Traceback (most recent call last):"))
        self.assertIsNone(buildsteps.plain(""))
        self.assertIsNone(buildsteps.plain("10:00 something unknown"))

    def test_agent_words_are_said_not_steps(self):
        out = buildsteps.steps(["10:00 started", "10:01 · I will add a test.", "10:01 → Read: a.py"])
        self.assertEqual(out["said"], "I will add a test.")
        self.assertEqual([s["text"] for s in out["steps"]],
                         ["Claude Code has started and is reading the task.", "Reading a.py to understand the code."])
        self.assertEqual(out["now"], "Reading a.py to understand the code.")

    def test_repeats_are_grouped(self):
        out = buildsteps.steps(["→ Read: a.py", "→ Read: b.py", "→ Read: a.py", "→ Read: c.py",
                                "→ Bash: pytest", "→ Bash: pytest", "→ Edit: a.py"])
        texts = [s["text"] for s in out["steps"]]
        self.assertEqual(texts, ["Reading 3 files: a.py, b.py, c.py.", "Running the tests to see whether everything still works.",
                                 "Changing a.py."])
        self.assertEqual(out["steps"][0]["n"], 2)
        self.assertEqual(out["steps"][1]["n"], 2)
        self.assertNotIn("files", out["steps"][0])

    def test_last_steps_only(self):
        out = buildsteps.steps([f"→ Edit: f{i}.py" if i % 2 else "→ Bash: make" for i in range(20)], last=7)
        self.assertEqual(len(out["steps"]), 7)
        self.assertEqual(out["total"], 20)

    def test_now_skips_raw_and_words(self):
        self.assertEqual(buildsteps.now(["10:00 → Bash: pytest", "  ok", "10:01 · done"]),
                         "Running the tests to see whether everything still works.")
        self.assertEqual(buildsteps.now([]), "")


if __name__ == "__main__":
    unittest.main()
