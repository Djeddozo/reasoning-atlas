"""One name (RA-053, ADR-024): nothing the owner sees says "HQ".

Covers the Engine's code (messages, prompts, Telegram, CLI output, briefs), the old dashboard page, Atlas's screen,
scripts and the plan texts shown in Atlas. Allowed until stage 3 (RA-055): environment variables such as HQ_DB,
the X-HQ-Token header, and lower-case command and file names such as `hq` and hq.db.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"(?<![-\w])HQ(?![-_\w])")


class OneName(unittest.TestCase):
    def test_no_hq_in_what_the_owner_sees(self):
        files = [*ROOT.joinpath("hq").rglob("*.py"), ROOT / "engine" / "dashboard.html", ROOT / "hq.sh",
                 *ROOT.joinpath("deploy").glob("*.sh"), *ROOT.joinpath("deploy").glob("*.py"),
                 *ROOT.joinpath("services").rglob("*.py"), *ROOT.joinpath("apps", "web", "src").glob("*.tsx"),
                 *ROOT.joinpath("modules").glob("*.json"), ROOT / "STATE" / "tasks.json", ROOT / "STATE" / "focuses.json",
                 ROOT / "STATE" / "owner-reminders.json"]
        found = [f"{f.relative_to(ROOT)}:{n}: {line.strip()[:100]}"
                 for f in files if f.exists()
                 for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                 if WORD.search(line.replace("former HQ", ""))]  # "former HQ" tells Claude what is outdated
        self.assertEqual(found, [], "The name HQ is retired (ADR-024); say Atlas, or the Engine for the always-on part")

    def test_documents_and_agent_guidance_say_atlas_and_engine(self):  # RA-054
        # Dated records (STATE/evidence, docs/reviews) keep the wording of their day; HQ-APP is the archived repository.
        history = re.compile(r"[Ff]ormer(?:ly)? HQ|used to be HQ|HQ name|name HQ|called \*\*HQ\*\*|HQ commit|names like HQ"
                             r'|shows "HQ"')
        files = [f for f in ROOT.rglob("*.md")
                 if not {"node_modules", ".git", "dist", ".devloop"} & set(f.relative_to(ROOT).parts)
                 and not f.relative_to(ROOT).as_posix().startswith(("STATE/evidence/", "docs/reviews/"))]
        found = [f"{f.relative_to(ROOT)}:{n}: {line.strip()[:100]}"
                 for f in files
                 for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                 if WORD.search(history.sub("", line))]
        self.assertEqual(found, [], "Documents and agent guidance say Atlas, or the Engine for the always-on part (RA-054)")

    def test_history_keeps_a_note_of_the_old_name(self):  # RA-054
        decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
        self.assertIn("called **HQ**", decisions.split("## ADR", 1)[0])
        self.assertIn("formerly HQ", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"))


class BuilderTarget(unittest.TestCase):
    def test_builder_builds_into_atlas_not_the_old_dashboard(self):  # RA-061
        from engine import devloop
        self.assertIn("apps/web/src", devloop.PROMPT)
        self.assertIn("engine/dashboard.html is the old dashboard and is\n  frozen", devloop.PROMPT)


if __name__ == "__main__":
    unittest.main()
