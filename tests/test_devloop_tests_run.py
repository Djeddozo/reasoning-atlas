import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import devloop


class ParallelTestRun(unittest.TestCase):
    def tree(self, sizes):
        d = Path(tempfile.mkdtemp())
        (d / "tests").mkdir()
        (d / "memory").mkdir()
        for name, n in sizes.items():
            (d / "tests" / f"{name}.py").write_text("x" * n)
        return d

    def test_shards_cover_every_module_once_and_balance(self):
        t = self.tree({"test_a": 900, "test_b": 500, "test_c": 400, "test_d": 100})
        groups = devloop._test_shards(t, 2)
        flat = sorted(m for g in groups for m in g)
        self.assertEqual(flat, ["tests.test_a", "tests.test_b", "tests.test_c", "tests.test_d"])
        self.assertEqual(groups[0][0], "tests.test_a")

    def test_passing_shards_pass_without_serial_rerun(self):
        t = self.tree({"test_a": 10, "test_b": 10})
        for n in ("test_a", "test_b"):
            (t / "tests" / f"{n}.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n")
        (t / "tests" / "__init__.py").write_text("")
        self.assertEqual(devloop.run_tests(t, 2), (True, ""))

    def test_failing_shard_triggers_authoritative_serial_run(self):
        t = self.tree({"test_a": 10, "test_b": 10})
        (t / "tests" / "__init__.py").write_text("")
        (t / "tests" / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_bad(self): self.fail('x')\n")
        (t / "tests" / "test_b.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n")
        ok, out = devloop.run_tests(t, 2)
        self.assertFalse(ok)
        self.assertIn("FAILED", out)


if __name__ == "__main__":
    unittest.main()
