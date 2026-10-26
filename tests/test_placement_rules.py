"""Placement (ADR-025): every function has one home among Atlas's sections, and no two do the same thing."""
import json
import unittest
from pathlib import Path

from packages.contracts.modules import check_placement, load_registry

ROOT = Path(__file__).resolve().parents[1]


class Placement(unittest.TestCase):
    def test_every_function_has_one_known_home(self):
        registry = load_registry(ROOT / "modules", ROOT)  # raises on a misplaced or duplicated function
        sections = json.loads((ROOT / "STATE" / "sections.json").read_text(encoding="utf-8"))["sections"]
        homes = {c["atlasSection"] for c in registry["capabilities"].values() if c["moduleKind"] != "tool"}
        self.assertLessEqual(homes, set(sections))

    def test_misplaced_and_duplicated_functions_are_refused(self):
        registry = load_registry(ROOT / "modules")
        registry["capabilities"]["engine.brain.write_thought"] = {**registry["capabilities"]["engine.brain.write_thought"], "atlasSection": "Waiting on you"}
        with self.assertRaisesRegex(ValueError, "not one of Atlas's sections"):
            check_placement(registry, ["Notes"])
        registry = load_registry(ROOT / "modules")
        registry["capabilities"]["atlas.notes.copy"] = {**registry["capabilities"]["atlas.notes.save"], "id": "atlas.notes.copy"}
        sections = json.loads((ROOT / "STATE" / "sections.json").read_text(encoding="utf-8"))["sections"]
        with self.assertRaisesRegex(ValueError, "lives in more than one function"):
            check_placement(registry, sections)


class UndoLivesWithTheChangeLog(unittest.TestCase):
    def test_undo_is_in_memory_next_to_the_change_log_not_in_notes(self):
        caps = load_registry(ROOT / "modules")["capabilities"]
        self.assertEqual(caps["engine.brain.undo_changes"]["atlasSection"], "Memory")
        self.assertEqual(caps["engine.memory.change_log"]["atlasSection"], "Memory")


class OverlapKnowsAtlasFunctions(unittest.TestCase):
    def test_registry_functions_are_compared_before_a_build(self):
        import sqlite3
        from engine import db, overlap
        funcs = overlap.functions()
        hit = next(f for f in funcs if f["ref"] == "engine.brain.write_thought")
        self.assertEqual((hit["kind"], hit["section"]), ("function", "Notes"))
        con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        db.init(con)
        cands = overlap.candidates(con, {"kind": "task", "id": 1, "title": "Write a thought to the brain from the screen",
                                         "description": "the engine sorts it into the plan"})
        self.assertIn("engine.brain.write_thought", [c["ref"] for c in cands[:3]])
