"""Top-bar search (tasks 99 and 100): ranking of destinations and the Enter / arrow-down behaviour."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute the browser code')
class SearchRuntimeTest(unittest.TestCase):
    def test_best_match_and_guesses(self):
        page = (Path(__file__).resolve().parents[1] / 'engine/dashboard.html').read_text()
        self.assertIn('id="gSearch"', page)
        source = page[page.index('const gsNorm'):page.index('function gsFilterPage(')]
        harness = '''
const assert = require('node:assert/strict');
const TABS=[["today","Today"],["forge","Forge"]], STAGES=[["plan","Plan"]];
const VIEWS={build:[["queue","Queue"]],system:[["usage","Usage"]]};
const D={goals:[{title:"Usage budget goal"}],ideas:[],recommended:[{id:7,title:"Fix queue bug"}]};
function setTab(){} function openTaskDetail(){}
''' + source + '''
assert.equal(gsMatches("")  .length, 0);
assert.equal(gsMatches("usage")[0].label, "Usage");
assert.equal(gsMatches("usage")[1].kind, "Goal");
assert.equal(gsMatches("queue")[0].label, "Queue");
assert.ok(gsMatches("queue").some(e=>e.kind==="Task"));
assert.equal(gsMatches("zzzz").length, 0);
assert.ok(gsMatches("build").some(e=>e.label==="Queue"), "area name finds its pages");
'''
        r = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
