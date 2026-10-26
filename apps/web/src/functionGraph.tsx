import React, {useEffect, useMemo, useRef, useState} from 'react';

export type Program = {id: string; name: string; kind: string; parent: string | null; level: string | null; summary: string};
export type Area = {id: string; program: string; name: string; summary: string};
export type Fn = {id: string; label: string; summary: string; program: string; programName: string; domain: string;
  status: string; effect: string; intent: string | null; requires: string[]; evidence: string | null; focuses: string[];
  use: string[]; atlasSection: string | null; reachable: boolean; note: string | null};
export type Focus = {id: string; name: string; summary: string; current: boolean; concepts: string | null; members: string[]};
export type GraphData = {programs: Program[]; domains: Area[]; functions: Fn[]; focuses: Focus[]};
export type Picked = {kind: 'program'; item: Program} | {kind: 'area'; item: Area} | {kind: 'function'; item: Fn; outside: boolean};

type Node = {key: string; kind: 'program' | 'area' | 'function' | 'tools'; label: string; depth: number; x: number; y: number; stagger?: boolean;
  status?: string; outside?: boolean; ref?: Program | Area | Fn; children: Node[]; leaves: number};

const RING = 150;
const TOOLS = '__tools__';

/** Which functions to draw: a focus's members plus what they require (marked outside), or everything. */
export function visibleFunctions(data: GraphData, focusId: string) {
  const byId = new Map(data.functions.map(f => [f.id, f]));
  if (focusId === 'all') return {shown: data.functions, outside: new Set<string>()};
  const focus = data.focuses.find(f => f.id === focusId);
  const members = new Set(focus ? focus.members : []);
  const outside = new Set<string>();
  const queue = [...members];
  while (queue.length) {
    const fn = byId.get(queue.pop()!);
    for (const r of fn?.requires || []) if (!members.has(r) && !outside.has(r)) { outside.add(r); queue.push(r); }
  }
  return {shown: data.functions.filter(f => members.has(f.id) || outside.has(f.id)), outside};
}

function buildTree(data: GraphData, shown: Fn[], outside: Set<string>, everything: boolean): Node | null {
  const shownIds = new Set(shown.map(f => f.id));
  const areaNode = (a: Area): Node | null => {
    const fns = shown.filter(f => f.domain === a.id);
    if (!fns.length && !everything) return null;
    return {key: a.id, kind: 'area', label: a.name, depth: 0, x: 0, y: 0, ref: a, leaves: 0,
      children: fns.map(f => ({key: f.id, kind: 'function', label: f.label, depth: 0, x: 0, y: 0, status: f.status,
        outside: outside.has(f.id), ref: f, children: [], leaves: 1}))};
  };
  const programNode = (p: Program): Node | null => {
    const subs = data.programs.filter(c => c.parent === p.id && c.kind !== 'tool').map(programNode).filter(Boolean) as Node[];
    const areas = data.domains.filter(a => a.program === p.id).map(areaNode).filter(Boolean) as Node[];
    const children = p.kind === 'tool' ? areas.flatMap(a => a.children) : [...subs, ...areas];
    if (!children.length && !everything) return null;
    return {key: p.id, kind: 'program', label: p.name, depth: 0, x: 0, y: 0, ref: p, children, leaves: 0};
  };
  // A focus that lives inside one program is drawn around that program; otherwise around the root program.
  const owners = new Set(shown.filter(f => !outside.has(f.id)).map(f => f.program));
  const single = !everything && owners.size === 1 ? data.programs.find(p => p.id === [...owners][0] && p.kind !== 'tool') : undefined;
  const root = single || data.programs.find(p => !p.parent && p.kind !== 'tool');
  if (!root) return null;
  const tree = programNode(root) || {key: root.id, kind: 'program', label: root.name, depth: 0, x: 0, y: 0, ref: root, children: [], leaves: 0};
  const tools = data.programs.filter(p => p.kind === 'tool').map(programNode).filter(Boolean) as Node[];
  if (tools.length && tools.some(t => t.children.some(c => shownIds.has(c.key)))) {
    tree.children.push({key: TOOLS, kind: 'tools', label: 'Apps you can switch to', depth: 0, x: 0, y: 0, children: tools, leaves: 0});
  }
  return tree;
}

/** Radial tree: angle span proportional to the number of leaves, radius by depth. Deterministic: nothing moves while reading. */
function layout(root: Node) {
  const count = (n: Node, depth: number): number => {
    n.depth = depth;
    const sum = n.children.reduce((s, c) => s + count(c, depth + 1), 0);
    // programs and the tools group always get room, so an empty or small program is not squeezed between areas
    n.leaves = Math.max(sum, n.kind === 'program' && depth > 0 ? 4 : n.kind === 'tools' ? 4 : n.kind === 'area' ? 1.5 : 1);
    return n.leaves;
  };
  count(root, 0);
  const place = (n: Node, a0: number, a1: number) => {
    const a = (a0 + a1) / 2;
    let r = n.depth * RING + (n.kind === 'program' && n.depth > 0 ? 40 : 0);
    if (n.kind === 'function' && n.stagger) r += 46;  // crowded areas alternate between two rings
    n.x = Math.cos(a) * r; n.y = Math.sin(a) * r;
    let start = a0;
    if (n.kind === 'area' && n.children.length > 6) n.children.forEach((c, i) => { c.stagger = i % 2 === 1; });
    for (const c of n.children) { const span = (a1 - a0) * c.leaves / n.leaves; place(c, start, start + span); start += span; }
  };
  place(root, -Math.PI / 2, Math.PI * 1.5);
  const all: Node[] = [];
  const walk = (n: Node, parent: Node | null, edges: [Node, Node][]) => { all.push(n); if (parent) edges.push([parent, n]); n.children.forEach(c => walk(c, n, edges)); return edges; };
  const tree = walk(root, null, []);
  return {nodes: all, tree};
}

const STATUS_FILL: Record<string, string> = {live: '#ff8a32', tested: '#c38cff', built: '#9c60e7', planned: '#2a2140'};

export function FunctionGraph({data, focusId, picked, onPick}: {data: GraphData; focusId: string; picked: string | null; onPick: (p: Picked) => void}) {
  const everything = focusId === 'all';
  const {shown, outside} = useMemo(() => visibleFunctions(data, focusId), [data, focusId]);
  const {nodes, tree} = useMemo(() => {
    const root = buildTree(data, shown, outside, everything);
    return root ? layout(root) : {nodes: [], tree: []};
  }, [data, shown, outside, everything]);
  const pos = useMemo(() => new Map(nodes.map(n => [n.key, n])), [nodes]);
  const requires = useMemo(() => shown.flatMap(f => f.requires.filter(r => pos.has(r) && pos.has(f.id)).map(r => [f.id, r] as [string, string])), [shown, pos]);
  const intents = useMemo(() => {
    const groups = new Map<string, Fn[]>();
    shown.forEach(f => f.intent && pos.has(f.id) && groups.set(f.intent, [...(groups.get(f.intent) || []), f]));
    const pairs: [string, string][] = [];
    groups.forEach(list => list.forEach((a, i) => list.slice(i + 1).forEach(b => a.program !== b.program && pairs.push([a.id, b.id]))));
    return pairs;
  }, [shown, pos]);
  const svg = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({k: 1, x: 467, y: 301});
  const drag = useRef<{x: number; y: number; vx: number; vy: number} | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  const fit = () => {
    if (!nodes.length) return;
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const [x0, x1, y0, y1] = [Math.min(...xs) - 90, Math.max(...xs) + 90, Math.min(...ys) - 60, Math.max(...ys) + 60];
    const k = Math.min(2.2, Math.min(934 / (x1 - x0), 560 / (y1 - y0)));
    setView({k, x: 467 - k * (x0 + x1) / 2, y: 320 - k * (y0 + y1) / 2});
  };
  useEffect(fit, [nodes]);  // refit when the focus changes

  const toSvg = (e: {clientX: number; clientY: number}) => {
    const m = svg.current!.getScreenCTM()!.inverse();
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m);
    return {x: p.x, y: p.y};
  };
  const zoomAt = (factor: number, cx = 467, cy = 301) => setView(v => {
    const k = Math.min(5, Math.max(0.25, v.k * factor));
    return {k, x: cx - (cx - v.x) * k / v.k, y: cy - (cy - v.y) * k / v.k};
  });
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => { e.preventDefault(); const p = toSvg(e); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, p.x, p.y); };
    el.addEventListener('wheel', wheel, {passive: false});
    return () => el.removeEventListener('wheel', wheel);
  }, []);

  // Zoom buttons and keys zoom towards the selected node, so it stays in view.
  const anchor = (): [number, number] => {
    const n = picked ? pos.get(picked) : undefined;
    return n ? [view.x + n.x * view.k, view.y + n.y * view.k] : [467, 301];
  };
  const neighbours = useMemo(() => {
    const focusKey = hover || picked;
    if (!focusKey) return null;
    const set = new Set([focusKey]);
    tree.forEach(([a, b]) => { if (a.key === focusKey) set.add(b.key); if (b.key === focusKey) set.add(a.key); });
    requires.concat(intents).forEach(([a, b]) => { if (a === focusKey) set.add(b); if (b === focusKey) set.add(a); });
    return set;
  }, [hover, picked, tree, requires, intents]);
  const dim = (key: string) => neighbours && !neighbours.has(key) ? ' faded' : '';
  const quiet = !neighbours && requires.length > 25 ? ' quiet' : '';  // many dependency lines: show them on hover or selection
  const showFnLabels = view.k >= 0.95;
  const pick = (n: Node) => {
    if (n.kind === 'function') onPick({kind: 'function', item: n.ref as Fn, outside: !!n.outside});
    else if (n.kind === 'area') onPick({kind: 'area', item: n.ref as Area});
    else if (n.kind === 'program') onPick({kind: 'program', item: n.ref as Program});
  };
  const curve = (a: Node, b: Node, bend = 0.12) => {
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y;
    return `M${a.x} ${a.y} Q${mx - dy * bend} ${my + dx * bend} ${b.x} ${b.y}`;
  };

  if (!nodes.length) return <div className="empty"><h2>Nothing in this focus yet</h2><p>Its functions appear here once the program is dissected and registered.</p></div>;
  return <div className="fn-graph">
    <div className="map-tools" role="toolbar" aria-label="Map zoom">
      <button onClick={() => zoomAt(1.25, ...anchor())} aria-label="Zoom in">+</button>
      <button onClick={() => zoomAt(0.8, ...anchor())} aria-label="Zoom out">−</button>
      <button onClick={fit} aria-label="Fit to view">⤢</button>
    </div>
    <svg ref={svg} className="graph" viewBox="0 0 934 602" role="group" aria-label={everything ? 'All programs, areas and functions' : 'Functions in this focus'}
      onPointerDown={e => { if ((e.target as Element).closest('.fg-node')) return; (e.currentTarget as Element).setPointerCapture(e.pointerId); const p = toSvg(e); drag.current = {x: p.x, y: p.y, vx: view.x, vy: view.y}; }}
      onPointerMove={e => { if (!drag.current) return; const p = toSvg(e); const d = drag.current; setView(v => ({...v, x: d.vx + p.x - d.x, y: d.vy + p.y - d.y})); }}
      onPointerUp={() => { drag.current = null; }}
      onKeyDown={e => { if (e.key === '+' || e.key === '=') zoomAt(1.2, ...anchor()); if (e.key === '-') zoomAt(1 / 1.2, ...anchor()); if (e.key === '0') fit(); }}>
      <defs>
        <pattern id="fg-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#666396" strokeOpacity=".15" strokeWidth=".6"/></pattern>
        <filter id="fg-glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="4"/></filter>
        <marker id="fg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#ff9b4a"/></marker>
        <radialGradient id="fg-core"><stop stopColor="#433161"/><stop offset=".8" stopColor="#20143d"/><stop offset="1" stopColor="#9c473c"/></radialGradient>
      </defs>
      <rect width="934" height="602" fill="url(#fg-grid)"/>
      <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
        {[1, 2, 3, 4].map(d => <circle key={d} r={d * RING} className="orbit"/>)}
        {tree.map(([a, b]) => <path key={a.key + b.key} d={curve(a, b, 0.06)} className={'fg-tree' + (b.outside ? ' outside' : '') + (b.kind === 'tools' || a.kind === 'tools' ? ' tools' : '') + dim(b.key)}/>)}
        {intents.map(([a, b]) => <path key={'i' + a + b} d={curve(pos.get(a)!, pos.get(b)!, 0.25)} className={'fg-intent' + (neighbours && !(neighbours.has(a) && neighbours.has(b)) ? ' faded' : '')}><title>Same kind of work in two programs</title></path>)}
        {requires.map(([a, b]) => <path key={'r' + a + b} d={curve(pos.get(a)!, pos.get(b)!, -0.18)} className={'fg-requires' + quiet + (neighbours && !(neighbours.has(a) && neighbours.has(b)) ? ' faded' : '')} markerEnd="url(#fg-arrow)"><title>{`${a} needs ${b}`}</title></path>)}
        {nodes.map(n => {
          const r = n.kind === 'program' ? (n.depth === 0 ? 46 : 30) : n.kind === 'tools' ? 22 : n.kind === 'area' ? 14 : 8;
          const fn = n.kind === 'function' ? n.ref as Fn : null;
          const selected = picked === n.key;
          const labelBelow = n.kind !== 'function';
          const away = Math.hypot(n.x, n.y) || 1;
          const lx = n.kind === 'function' ? n.x + (n.x / away) * 14 : n.x;
          const ly = n.kind === 'function' ? n.y + (n.y / away) * 14 + 4 : n.y + r + 15;
          const anchor = n.kind === 'function' ? (Math.abs(n.x) < 20 ? 'middle' : n.x > 0 ? 'start' : 'end') : 'middle';
          const showLabel = n.kind !== 'function' || showFnLabels || selected || hover === n.key;
          return <g key={n.key} className={`fg-node ${n.kind} ${fn ? 'st-' + fn.status : ''}${fn && !fn.reachable ? ' unreachable' : ''}${n.outside ? ' outside' : ''}${selected ? ' selected' : ''}${dim(n.key)}`}
            tabIndex={n.kind === 'tools' ? -1 : 0} role="button" aria-label={`${n.label}${fn ? `, ${fn.status}, ${fn.programName}` : ''}`} aria-pressed={selected}
            onClick={() => pick(n)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(n); } }}
            onPointerEnter={() => setHover(n.key)} onPointerLeave={() => setHover(null)}>
            {fn && !fn.reachable && fn.status === 'live' && <circle cx={n.x} cy={n.y} r={r + 4} className="fg-elsewhere"/>}
            {(n.kind === 'program' || (fn && fn.status === 'live' && fn.reachable) || selected) && <circle cx={n.x} cy={n.y} r={r + 3} className="fg-halo" filter="url(#fg-glow)"/>}
            <circle cx={n.x} cy={n.y} r={r} className="fg-dot" fill={n.kind === 'program' ? (n.depth === 0 ? 'url(#fg-core)' : '#2a1850') : n.kind === 'area' ? '#1b1436' : n.kind === 'tools' ? '#101a2c' : STATUS_FILL[fn!.status]}/>
            {showLabel && <text x={lx} y={labelBelow ? ly : ly} textAnchor={anchor} className={`fg-label ${n.kind}`}>{n.label}</text>}
            <title>{fn ? `${fn.label} — ${fn.summary} (${fn.status}${fn.reachable ? '' : ', not yet on Atlas\'s screen'}${n.outside ? ', needed from outside this focus' : ''})` : n.label}</title>
          </g>;
        })}
      </g>
    </svg>
    <div className="fg-legend" aria-hidden="true">
      <span><i className="dot live"/>Live</span><span><i className="dot elsewhere"/>Live in the Engine, not yet on Atlas's screen</span><span><i className="dot built"/>Built or tested</span><span><i className="dot planned"/>Planned</span>
      <span><i className="line req"/>Needs{requires.length > 25 ? ' (hover a node)' : ''}</span><span><i className="line intent"/>Same kind of work</span>{!everything && <span><i className="dot outside"/>From outside this focus</span>}
      <span className="hint">Scroll to zoom · drag to move · ⤢ fit</span>
    </div>
  </div>;
}

export function FunctionList({data, focusId, onPick}: {data: GraphData; focusId: string; onPick: (p: Picked) => void}) {
  const {shown, outside} = visibleFunctions(data, focusId);
  const programs = data.programs.filter(p => shown.some(f => f.program === p.id));
  if (!shown.length) return <div className="empty"><h2>Nothing in this focus yet</h2></div>;
  return <div className="node-list">{programs.map(p => <section key={p.id}><h4 className="fl-program">{p.name}</h4>
    {shown.filter(f => f.program === p.id).map(f => <button key={f.id} onClick={() => onPick({kind: 'function', item: f, outside: outside.has(f.id)})}>
      <strong>{f.label}</strong><span>{f.summary}</span><small>{data.domains.find(a => a.id === f.domain)?.name} · {f.status}{outside.has(f.id) ? ' · needed from outside this focus' : ''}{f.requires.length ? ` · needs ${f.requires.length}` : ''}</small></button>)}</section>)}</div>;
}
