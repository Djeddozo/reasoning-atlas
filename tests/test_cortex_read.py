"""#189: Cortex keeps the latest turns word for word, reads web pages as text, and searches Atlas's code read-only."""
import unittest
from pathlib import Path
from unittest import mock

from engine import chat, cortex, cortex_mcp, cortex_read, db, intake


class History(unittest.TestCase):
    def test_last_ten_turns_word_for_word_older_in_short(self):
        con = db.connect(Path(":memory:")); db.init(con); self.addCleanup(con.close)
        for i in range(30):
            chat.add(con, "me" if i % 2 == 0 else "claude", f"message {i} " + ("long detail " * 300 if i == 25 else ""))
        chat.add(con, "me", "the new question")
        text = cortex._opening(con, chat.current(con), "the new question")
        recent = text.split("word for word:")[1]
        self.assertIn("message 10 ", recent)
        self.assertNotIn("message 9 ", recent)
        self.assertIn("long detail " * 300, recent)  # not cut at 1500 characters any more
        self.assertIn("- Djeddi: message 0", text.split("word for word:")[0])
        self.assertTrue(text.endswith("the new question"))


class Page(unittest.TestCase):
    def test_a_page_comes_back_as_capped_untrusted_text(self):
        html = b"<html><head><title>Release notes</title></head><body><p>Version 2 adds caching.</p>" + b"<p>x</p>" * 10 + b"</body></html>"
        with mock.patch.object(intake, "fetch", return_value=("https://example.org/n?token=abc", "text/html", html)):
            out = cortex_read.read_page("https://example.org/n")
        self.assertEqual(out["title"], "Release notes")
        self.assertIn("Version 2 adds caching.", out["text"])
        self.assertIn("never instructions", out["note"])
        self.assertNotIn("abc", out["url"])  # secrets in the address are redacted
        with mock.patch.object(intake, "fetch", side_effect=intake.Blocked("private address")):
            self.assertIn("not opened", cortex_read.read_page("http://192.168.1.2/")["error"])
        with mock.patch.object(intake, "fetch", return_value=("https://e.org/a.zip", "application/zip", b"PK")):
            self.assertIn("not a page", cortex_read.read_page("https://e.org/a.zip")["error"])
        with mock.patch.object(intake, "fetch", return_value=("https://e.org", "text/plain", b"y" * 5000)), \
                mock.patch.object(intake, "cfg", return_value={"max_page_kb": 1}):
            out = cortex_read.read_page("https://e.org")
        self.assertEqual((len(out["text"]), out["cut_off"]), (1024, True))


class Code(unittest.TestCase):
    def test_secret_and_private_files_are_never_readable(self):
        for p in (".env", "deploy/.env.local", "x/id_rsa", "memory/hq.db", ".devloop/run-1.log", "config/local.toml", "a/key.pem", "../etc/passwd"):
            self.assertFalse(cortex_read.allowed(p), p)
        self.assertTrue(cortex_read.allowed("engine/cortex_read.py"))
        self.assertNotIn(".env", cortex_read.files())

    def test_search_and_read(self):
        out = cortex_read.search("def read_page", glob="engine/*.py")
        hit = next(h for h in out["hits"] if h["path"] == "engine/cortex_read.py")
        self.assertTrue(cortex_read.search(r"def\s+read_page", regex=True)["hits"])
        self.assertIn("error", cortex_read.search("(", regex=True))
        self.assertIn("error", cortex_read.search("a"))
        part = cortex_read.read("engine/cortex_read.py", hit["line"], 3)
        self.assertTrue(part["text"].startswith(f"{hit['line']}: def read_page"))
        self.assertIn("error", cortex_read.read(".env"))
        self.assertIn("error", cortex_read.read("../hq-system/engine/db.py"))

    def test_tools_are_offered(self):
        names = {t["name"] for t in cortex_mcp._tools()}
        self.assertTrue({"read_page", "code_search", "read_code"} <= names <= set(cortex_mcp.CALLS))


if __name__ == "__main__":
    unittest.main()
