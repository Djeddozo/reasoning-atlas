"""Execute the real lane renderer (#57) and live console: one tab per task in flight, selection kept, log lines as steps."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute the browser renderer')
class LanesRuntimeTest(unittest.TestCase):
    def test_tabs_selection_and_detail(self):
        page = (Path(__file__).resolve().parents[1] / 'engine/dashboard.html').read_text()
        source = page[page.index('let LANE = null;'):page.index('// ---------------- build queue')]
        helpers = page[page.index('const usd = v =>'):page.index('const STATUS =')]
        harness = '''
const assert = require('node:assert/strict');
const els = {};
const $ = id => els[id] || (els[id] = {innerHTML:'', textContent:'', hidden:false, scrollTop:0, scrollHeight:99, clientHeight:50});
let tab = 'today'; const view = {build:'live'}; const H = () => ({});
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const empty = t => '<div class="empty">'+t+'</div>';
''' + helpers + source + '''
const lane = (run, task, state, extra={}) => Object.assign({run, task_id:task, title:'Task '+task, state, kind:'build', branch:'hq-dev/'+task,
  started:'2026-10-14T10:00:00', elapsed:600, progress: state==='running' ? 25 : null, model:'', model_reason:'', pr_url:'', conflict:[],
  turns:0, tokens:0, cost_usd:0, summary:'done <b>', stacked_on:[], in_stack_of:null, doing:'', log:['line of '+task]}, extra);
const A = {max_minutes:40, log:['old'], next:null, why_idle:'idle', lanes:[
  lane(9, 3, 'pr', {stacked_on:[2], pr_url:'https://x/pr/9'}), lane(12, 5, 'running', {doing:'· Writing <tests>', model:'sonnet'}),
  lane(7, 2, 'conflict', {conflict:['engine/db.py'], in_stack_of:3})]};
global.D = {agent:A};
renderLanes(A);
assert.equal(LANE, 12, 'the running lane is selected first');
assert.equal($('agentLanes').hidden, false);
for (const t of [3,5,2]) assert.ok($('agentLanes').innerHTML.includes('#'+t+' Task '+t));
assert.ok($('agentLanes').innerHTML.includes('lane running sel'));
assert.ok($('nowTitle').textContent.includes('1 building · 2 PRs waiting'));
assert.ok($('agentNow').innerHTML.includes('Writing &lt;tests>'), 'what it does now');
assert.ok($('agentNow').innerHTML.includes('model sonnet'));
assert.ok($('agentLog').innerHTML.includes('line of 5'));
assert.ok($('agentNow').innerHTML.includes('class="nowline"'), 'a running lane shows what it does now');
selectLane(7);
assert.ok($('agentNow').innerHTML.includes('Conflicts with main: engine/db.py'));
assert.ok($('agentNow').innerHTML.includes('stack under #3'));
assert.ok($('agentLog').innerHTML.includes('line of 2'));
assert.ok(!$('agentNow').innerHTML.includes('nowline'));
renderLanes(A);  // a refresh keeps the selection
assert.equal(LANE, 7);
A.lanes = A.lanes.filter(l => l.run !== 7);  // its PR was merged: fall back to the running lane
renderLanes(A);
assert.equal(LANE, 12);
A.lanes = [lane(9, 3, 'pr')];
renderLanes(A);
assert.equal($('agentLanes').hidden, true, 'a single lane needs no tabs');
assert.ok($('agentNow').innerHTML.includes('PR waits for review'));
A.lanes = []; A.next = {id:4, title:'Next one'};
renderLanes(A);
assert.equal(LANE, null);
assert.equal($('nowTitle').textContent, 'Next up');
assert.ok($('agentLog').innerHTML.includes('old'));
const html = consoleHTML(['10:01 started', '10:02 · Reading <the> plan', '10:02 → Edit: engine/db.py', '10:03 → Bash: python3 -m unittest', '10:04 ■ finished after 3 turns']);
assert.ok(html.includes('cl say') && html.includes('Reading &lt;the> plan'), 'narration is escaped');
assert.ok(html.includes('tk edit">Edit') && html.includes('tk run">Bash') && html.includes('engine/db.py'));
assert.ok(html.includes('cl done') && html.includes('cl sys'));
assert.equal(nowText(['10:02 · Thinking', '10:03 → Read: CLAUDE.md']), 'Using Read: CLAUDE.md');
assert.ok(!$('agentNow').innerHTML.includes('NaN') && !$('agentNow').innerHTML.includes('undefined'));
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
