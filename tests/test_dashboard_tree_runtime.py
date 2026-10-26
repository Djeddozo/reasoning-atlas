"""Execute the real SVG renderer with branches unknown to the installed version."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute the browser renderer')
class TreeRuntimeTest(unittest.TestCase):
    def test_unknown_branches_render_in_both_views_on_phone_and_desktop(self):
        page = (Path(__file__).resolve().parents[1] / 'engine/dashboard.html').read_text()
        source = page[page.index('function visibleTree('):page.index('function selectNode(')]
        harness = '''
const assert = require('node:assert/strict');
let SHOW_BUILT = false, SEL = null;
const STATE_TEXT = {}, window = {innerWidth:1200};
const svg = {parentNode:{clientWidth:700}, setAttribute(){}, innerHTML:''};
const $ = () => svg, esc = s => String(s).replaceAll('<','&lt;'), wrapLabel = s => [s];
''' + source + '''
const node = (id,branch,state,requires=[]) => ({id,branch,state,requires,title:'Node '+id,tasks:[],ideas:[],total:0});
const T = {branches:[{key:'core',title:'Foundation'}],plan:{},nodes:[
 node(1,'core','unlocked'),node(2,'self','available',[1]),
 node(3,'self','locked',[2]),node(4,'<new>','available'),node(5,null,'available')
]};
for (const full of [false,true]) for (const width of [390,1200]) {
 SHOW_BUILT=full; window.innerWidth=width;
 const ns=visibleTree(T), layout=layoutTree(T,ns);
 for (const n of ns) assert.ok(layout.pos[n.id], 'missing position for '+n.id);
 renderTree(T);
 for (const n of ns) assert.ok(svg.innerHTML.includes('Node '+n.id));
 assert.ok(svg.innerHTML.includes('&lt;new>'));
 assert.ok(svg.innerHTML.includes('Other capabilities'));
 assert.ok(!svg.innerHTML.includes('NaN'));
}
assert.equal(T.branches.length,1, 'rendering must not modify the API data');
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
