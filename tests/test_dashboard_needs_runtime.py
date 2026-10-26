"""Execute the real Needs your input renderer (#62): buttons per kind, typed input kept across refreshes, 🎙 target."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute the browser renderer')
class NeedsRuntimeTest(unittest.TestCase):
    def test_render_answer_and_mic(self):
        page = (Path(__file__).resolve().parents[1] / 'engine/dashboard.html').read_text()
        source = page[page.index('let NEEDS = [], micTarget = null;'):page.index('const fmtDay = iso =>')]
        spoken = page[page.index('function addSpoken('):page.index('function micLang(')]
        harness = '''
const assert = require('node:assert/strict');
const els = {}, sent = [];
const $ = id => els[id] || (els[id] = {innerHTML:'', textContent:'', hidden:false, value:'', disabled:false, click(){ this.clicked = true; }});
const esc = s => String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const toast = () => {};
async function act(op, id, text){ sent.push([op, id, text]); return 'ok'; }
''' + source + spoken + '''
const list = [
  {key:'D4', kind:'decision', id:4, title:'Voice <local> or cloud?', why:'blocks voice', options:['Local','Cloud'], holds:[]},
  {key:'A7', kind:'approval', id:7, title:'Install tool', why:'€2', options:[], holds:[{id:9, title:'Wire it'}], detail:''},
  {key:'T3', kind:'task', id:3, title:'Make the bot account', why:'the agent waits for this: 1 task paused', options:[], holds:[{id:9, title:'Wire it'}]}];
renderNeeds(list);
const h = $('needs').innerHTML;
assert.equal($('needsCard').hidden, false);
assert.ok(h.includes('Voice &lt;local> or cloud?'), 'item text is escaped');
assert.ok(h.includes('>Local</button>') && h.includes('>Cloud</button>'));
assert.ok(h.includes('Approve</button>') && h.includes('Deny</button>'));
assert.ok(h.includes('Paused until then: #9 Wire it') && h.includes('Holds up: #9 Wire it'));
assert.ok(h.includes('✓ Done'));
assert.ok(!h.includes('undefined'));
$('ni-T3').value = 'half done';
renderNeeds(list);  // the 60 s refresh keeps what he typed
assert.ok($('needs').innerHTML.includes('value="half done"'));
needsMic(2);
assert.equal(micTarget, 'T3'); assert.ok($('mic').clicked);
$('ni-T3').value = 'half done';
addSpoken('then the token');
assert.equal($('ni-T3').value, 'half done then the token');
micTarget = null; addSpoken('to the dump');
assert.equal($('dump').value, 'to the dump');
(async () => {
  await needsAnswer(0, {text:'Cloud'});
  await needsAnswer(1, {verdict:'approved'});
  assert.deepEqual(sent.map(s => JSON.parse(s[2])), [{key:'D4', text:'Cloud', verdict:''}, {key:'A7', text:'', verdict:'approved'}]);
  renderNeeds([]);
  assert.equal($('needsCard').hidden, true);
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
