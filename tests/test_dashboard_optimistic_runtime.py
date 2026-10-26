"""Execute the real optimistic-UI helpers and the Updates card: an item leaves at once, comes back when the server
refuses, and Undo reverses exactly the change-log entries the action made."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute the browser renderer')
class OptimisticRuntimeTest(unittest.TestCase):
    def test_leave_restore_undo_and_updates_card(self):
        page = (Path(__file__).resolve().parents[1] / 'engine/dashboard.html').read_text()
        helpers = page[page.index('// Optimistic UI: the item fades out'):page.index('let claudeAuthSession')]
        card = page[page.index('// Updates: PR ready'):page.index('// Needs your input: decisions')]
        harness = '''
const assert = require('node:assert/strict');
const els = {}, sent = [], toasts = [];
const mk = () => ({innerHTML:'', textContent:'', hidden:false, onclick:null,
  classList: {s:new Set(), add(c){this.s.add(c)}, remove(c){this.s.delete(c)}, contains(c){return this.s.has(c)}}});
const $ = id => els[id] || (els[id] = mk());
const esc = s => String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
function toast(t){ toasts.push(t); } toast.h = 0;
let reply = {result:'ok', changes:[11, 12]};
async function act(op, id, text){ sent.push([op, id, text]); if (reply.error) { toast(reply.error); return null; } act.changes = reply.changes; return reply.result; }
const realSetTimeout = setTimeout;
''' + helpers + card + '''
(async () => {
  // success: leaves at once, Undo reverses its change-log entries newest first (server sorts), toast says Undone
  const row = mk(); row.closest = () => row;
  const p = optimistic(row, () => act('delete', 5), 'Task deleted');
  assert.ok(row.classList.contains('leaving'), 'fades out before the server answers');
  await p;
  assert.ok($('undo').classList.contains('on')); assert.equal($('undoMsg').textContent, 'Task deleted');
  assert.equal($('undoBtn').hidden, false);
  await $('undoBtn').onclick();
  assert.deepEqual(sent.at(-1), ['brain_undo_many', null, '11,12']);
  // refused: the item comes back
  reply = {error:'Task #5 is the agent\\'s to build'};
  const row2 = mk(); row2.closest = () => row2;
  const r = await optimistic(row2, () => act('done', 5), 'Done');
  assert.equal(r, null); assert.equal(row2.hidden, false); assert.ok(!row2.classList.contains('leaving'));
  // no change-log entries and no undo given: message without an Undo button
  reply = {result:'ok', changes:[]};
  const row3 = mk(); row3.closest = () => row3;
  await optimistic(row3, () => act('cmd_done', 3), 'Marked as run');
  assert.equal($('undoBtn').hidden, true);
  // Updates card: ready to merge links to GitHub, merged waits for install, installed shows; suggestions get 3 answers
  renderDelivery({review:[{number:65, title:'Sync <x>', url:'https://github.com/x/y/pull/65', tasks:[]}],
    install:[{number:63, title:'Revalidation', url:'u63', tasks:[88]}],
    installed:[{number:62, title:'Forge', url:'u62', tasks:[], installed_at:'2026-10-18T22:10:00'}],
    suggestions:[{id:4, task_id:80, title:'Four tabs', evidence:'PR #62 mentions task #80', url:'u62'}]});
  const h = $('prList').innerHTML;
  assert.equal($('deliveryCard').hidden, false);
  assert.ok(h.includes('Review on GitHub') && h.includes('href="https://github.com/x/y/pull/65"'));
  assert.ok(h.includes('Sync &lt;x>'), 'escaped');
  assert.ok(h.includes('installs within 15 minutes') && h.includes('task #88'));
  assert.ok(h.includes('Installed') && h.includes('10-18 22:10'));
  assert.ok(h.includes("reconcile(4,'done'") && h.includes("reconcile(4,'partial'") && h.includes("reconcile(4,'not_related'"));
  assert.ok(!h.includes('undefined'));
  renderDelivery({review:[], install:[], installed:[], suggestions:[]});
  assert.equal($('deliveryCard').hidden, true);
  renderDelivery(undefined);
  assert.equal($('deliveryCard').hidden, true);
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
