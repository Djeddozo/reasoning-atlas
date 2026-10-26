
import React, {createContext,useContext,useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {FunctionGraph,FunctionList,GraphData,Picked} from './functionGraph';
import {HEARING_TEXT,joinText,saveHearing,useDictation,type Hearing} from './dictation';
const store={get:(k:string)=>{try{return localStorage.getItem(k)}catch{return null}},set:(k:string,v:string)=>{try{localStorage.setItem(k,v)}catch{}}};
// Cortex: the name of Atlas's reasoning core, or the owner's nickname for it (owner, 2026-10-25). Function ids keep `brain`.
const CortexCtx=createContext('Cortex');
type SearchSrc={graph:GraphData|null;tasks:Task[];notes:Note[];hub:Hub|null;open:(kind:string,ref:any)=>void};
const SearchCtx=createContext<SearchSrc|null>(null);
const useCortex=()=>useContext(CortexCtx);
const renameCortex=(text:string,name:string)=>name==='Cortex'?text:text.replace(/\bCortex\b/g,name);
type Note={id:string;body:string;createdAt:string};
type Task={id:string;title:string;status:string;program?:string;path?:string};
type Path={id:string;program:string;name:string;summary:string};
const PROGRAM_NAMES:Record<string,string>={atlas:'Atlas',engine:'Engine',finance:'Finance'};
function pathLabel(task:Task|undefined,paths:Path[]){if(!task)return '';const p=paths.find(x=>x.id===task.path);return `${PROGRAM_NAMES[task.program||'atlas']||task.program} › ${p?p.name:task.path||'No path'}`}
const STATUS_ORDER:Record<string,number>={in_progress:0,review:1,ready:2,blocked:3,todo:4,done:5};
const STATUS_TEXT:Record<string,string>={done:'Completed checkpoint',in_progress:'Being built',review:'Awaiting your review',ready:'Ready to start',blocked:'Blocked',todo:'Not built yet'};
function PathList({tasks,paths}:{tasks:Task[];paths:Path[]}){
 const groups=paths.map(p=>({p,items:tasks.filter(t=>t.path===p.id).sort((a,b)=>(STATUS_ORDER[a.status]??9)-(STATUS_ORDER[b.status]??9)||a.id.localeCompare(b.id))})).filter(g=>g.items.length);
 const open=groups.filter(g=>g.items.some(t=>t.status!=='done')).sort((a,b)=>Number(!a.items.some(t=>t.status==='in_progress'))-Number(!b.items.some(t=>t.status==='in_progress'))),finished=groups.filter(g=>g.items.every(t=>t.status==='done'));
 const row=(t:Task)=><article className={'task '+(t.status==='done'?'complete':'pending')} key={t.id}><span>{t.status==='done'?'✓':t.status==='in_progress'?'◌':'○'}</span><div><strong>{t.id} · {t.title}</strong><small>{STATUS_TEXT[t.status]||t.status}</small></div></article>;
 return <div className="paths">{open.map(({p,items})=>{const done=items.filter(t=>t.status==='done');const active=items.filter(t=>t.status!=='done');return <section className="path" key={p.id}><header><div><small>{PROGRAM_NAMES[p.program]||p.program} ›</small><h3>{p.name}</h3><p>{p.summary}</p></div><span className="path-count">{done.length}/{items.length}</span></header><div className="buildbar-track"><i style={{width:`${Math.round(100*done.length/items.length)}%`}}/></div>{active.map(row)}{done.length>0&&<details className="done-list"><summary>Done ({done.length})</summary>{done.map(row)}</details>}</section>})}
  {finished.length>0&&<details className="done-list finished-paths"><summary>Completed paths ({finished.length})</summary>{finished.map(({p,items})=><section className="path" key={p.id}><header><div><small>{PROGRAM_NAMES[p.program]||p.program} ›</small><h3>{p.name}</h3></div><span className="path-count">{items.length}/{items.length}</span></header>{items.map(row)}</section>)}</details>}</div>;
}
type Reminder={id:string;title:string;detail:string;link:string|null;group:string|null;source:'agent'|'owner';createdAt:string|null;doneAt:string|null};
function ReminderItem({r,onToggle}:{r:Reminder;onToggle:(r:Reminder)=>void}){
 return <li className={'reminder '+(r.doneAt?'done':'')}><label><input type="checkbox" checked={!!r.doneAt} onChange={()=>onToggle(r)}/><span>{r.title}</span></label>{r.link&&<small className="chip">{r.link}</small>}{r.source==='owner'&&<small className="chip mine">Yours</small>}{r.detail&&<details><summary>Details</summary><p>{r.detail}</p></details>}</li>;
}
type ActionStep={id:string;text:string;detail:string;command:string;doneAt:string|null};
type OwnerAction={id:string;title:string;context:string;steps:ActionStep[];answered:{q:string;a:string}[];ask:string;link:string|null;createdAt:string|null;input:string;inputAt:string|null;doneAt:string|null;prompt:string};
function ForYou(){
 const [items,setItems]=useState<OwnerAction[]|null>(null),[drafts,setDrafts]=useState<Record<string,string>>({}),[msg,setMsg]=useState('');
 const load=async()=>{try{const r=await fetch('/api/actions');if(r.ok)setItems(await r.json())}catch{}};
 useEffect(()=>{load()},[]);
 async function post(id:string,what:string,body:object){setMsg('');try{const r=await fetch(`/api/actions/${encodeURIComponent(id)}/${what}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error((await r.json()).error||'Not saved');await load();return true}catch(e){setMsg(`Not saved: ${String((e as Error).message||e)}. Your text is kept; retry when the service returns.`);return false}}
 if(items===null)return null;
 const open=items.filter(a=>!a.doneAt),done=items.filter(a=>a.doneAt);
 const card=(a:OwnerAction)=>{const n=a.steps.filter(s=>s.doneAt).length,draft=drafts[a.id]??a.input;return <li key={a.id} className={'engine-item for-you-item'+(a.doneAt?' done':'')}>
  <div className="ei-head"><strong>{a.title}</strong>{a.steps.length>0&&<span className="chip">{n}/{a.steps.length} done</span>}{a.input&&<span className="chip mine">your input saved</span>}<small className="ei-key">{a.link||a.id}</small></div>
  {a.context&&<p className="ei-why fy-context">{a.context}</p>}
  {a.steps.length>0&&<ul className="fy-steps">{a.steps.map(s=><li key={s.id} className={'reminder'+(s.doneAt?' done':'')}><label><input type="checkbox" checked={!!s.doneAt} onChange={()=>post(a.id,'step',{step:s.id,done:!s.doneAt})}/><span>{s.text}</span></label>{(s.detail||s.command)&&<details><summary>How</summary>{s.detail&&<p>{s.detail}</p>}{s.command&&<><pre className="ei-cmd">{s.command}</pre><Copy text={s.command} label="Copy command"/></>}</details>}</li>)}</ul>}
  {a.answered.length>0&&<details className="fy-answered"><summary>Answered questions ({a.answered.length})</summary><dl>{a.answered.map((x,i)=><div key={i}><dt>{x.q}</dt><dd>{x.a}</dd></div>)}</dl></details>}
  {a.ask&&<p className="fy-ask"><b>Asked of you:</b> {a.ask}</p>}
  <textarea className="quick" aria-label={`Your input for ${a.title}`} placeholder="Your input, answers or notes for the next agent session…" value={draft} maxLength={20000} onChange={e=>setDrafts({...drafts,[a.id]:e.target.value})}/>
  <div className="ei-actions"><button disabled={draft===a.input} onClick={async()=>{if(await post(a.id,'input',{input:draft})){const d={...drafts};delete d[a.id];setDrafts(d)}}}>Save input</button><Copy text={a.prompt} label="Copy as prompt"/><button onClick={()=>post(a.id,'done',{done:!a.doneAt})}>{a.doneAt?'Reopen':'Mark done'}</button>{a.inputAt&&<small className="muted">saved {new Date(a.inputAt).toLocaleString('en-GB',{timeZone:'Europe/Paris'})}</small>}</div></li>};
 return <section className="engine-attention for-you"><h4>For you <small>what only you can do, with its context; ticks and your input stay on this computer</small></h4>
  {msg&&<p className="save-status" role="status">{msg}</p>}
  {!open.length&&<p className="build-idle">Nothing is waiting on you.</p>}<ul>{open.map(card)}</ul>
  {done.length>0&&<details className="done-list"><summary>Done ({done.length})</summary><ul>{done.map(card)}</ul></details>}
  <p className="muted fy-cli">In a terminal session: <code>python -m services.atlas.actions list</code>, then <code>show &lt;id&gt;</code> or <code>prompt &lt;id&gt;</code>.</p></section>;
}
function Reminders({items,onToggle,group}:{items:Reminder[];onToggle:(r:Reminder)=>void;group?:string}){
 const list=group?items.filter(r=>r.group===group):items;
 const open=list.filter(r=>!r.doneAt),done=list.filter(r=>r.doneAt);
 const groups=[...new Set(open.map(r=>r.group||(r.source==='owner'?'Your reminders':'Other')))];
 return <div className="reminders">{!open.length&&<p className="build-idle">Nothing is waiting on you.</p>}
  {groups.map(g=><section key={g}>{!group&&<h4>{g}</h4>}<ul>{open.filter(r=>(r.group||(r.source==='owner'?'Your reminders':'Other'))===g).map(r=><ReminderItem key={r.id} r={r} onToggle={onToggle}/>)}</ul></section>)}
  {done.length>0&&<details className="done-list"><summary>Done ({done.length})</summary><ul>{done.map(r=><ReminderItem key={r.id} r={r} onToggle={onToggle}/>)}</ul></details>}</div>;
}
type Build={state:'idle'|'building'|'done'|'failed'|'stalled';taskId:string|null;title:string|null;steps:string[];step:number;progress:number;message:string;updatedAt:string|null;release:string|null;serverStartedAt?:string;restart?:{needed:boolean;reasons:string[];canRestart:boolean}};
function useBuild(){
 const [build,setBuild]=useState<Build|null>(null),[updating,setUpdating]=useState(false),loaded=useRef<string|null|undefined>(undefined);
 useEffect(()=>{let alive=true;async function poll(){try{const r=await fetch('/api/build-status');if(!r.ok)return;const b:Build=await r.json();if(!alive)return;setBuild(b);
  if(loaded.current===undefined)loaded.current=b.release;
  else if(b.release&&b.release!==loaded.current){setUpdating(true);setTimeout(()=>location.reload(),1500)}}catch{}}
  poll();const t=setInterval(poll,2000);return()=>{alive=false;clearInterval(t)}},[]);
 return {build,updating};
}
type Side={hubCommit?:string;diskCommit?:string;restartNeeded?:boolean;current:{sha:string;subject:string}|null;behind:number;commits?:{sha:string;subject:string}[];blocker?:string;error?:string;running?:boolean;last?:{state?:string;message?:string;finishedAt?:number}};
type Upd={pc:Side&{canRestart:boolean};pi:Side|null};
function UpdatesPanel({onRestart}:{onRestart:(wait:()=>Promise<void>)=>void}){
 const [u,setU]=useState<Upd|null>(null),[open,setOpen]=useState(false),[busy,setBusy]=useState(''),[msgs,setMsgs]=useState<string[]>([]);
 const load=async()=>{try{const r=await fetch('/api/update/status');if(r.ok)setU(await r.json())}catch{}};
 useEffect(()=>{load();const t=setInterval(load,open||u?.pi?.running?5000:60000);return()=>clearInterval(t)},[open,u?.pi?.running]);
 const post=async(what:'check'|'apply')=>{setBusy(what);setMsgs([]);try{const r=await fetch('/api/update/'+what,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const b=await r.json();setMsgs(b.messages||[b.error]);if(b.restarting){onRestart(async()=>{});return}}catch{setMsgs(['Atlas could not be reached.'])}finally{setBusy('');load()}};
 const pending=(u?.pc.behind||0)+(u?.pi&&!u.pi.error?u.pi.behind:0);
 const piRunning=!!u?.pi?.running;
 const side=(name:string,x:Side|null,offline:string)=>{
  if(!x)return <div className="upd-side"><b>{name}</b><p>{offline}</p></div>;
  const pi=x as Side&{hubCommit?:string;diskCommit?:string;restartNeeded?:boolean};
  if(pi.restartNeeded)return <div className="upd-side"><b>{name}</b><p className="upd-block">The new version is installed on the Pi ({pi.diskCommit}), but the hub still runs {pi.hubCommit}. Restart the hub on the Pi:</p><pre className="ei-cmd">systemctl --user restart atlas-hub</pre></div>;
  if(x.error&&!x.current)return <div className="upd-side"><b>{name}</b>{/not available through the hub/.test(x.error)?<><p className="upd-block">The Pi's hub (version {pi.hubCommit||'unknown'}) is from before the Update button, so this button cannot reach it yet. Update it once by hand on the Pi:</p><pre className="ei-cmd">cd ~/hq-system && ./hq.sh update</pre><p>After that, this button updates the Pi too.</p></>:<p>{x.error}</p>}</div>;
  return <div className="upd-side"><b>{name}</b><p>Runs <code>{x.current?.sha||'?'}</code> {x.current?.subject}</p>
   {x.running?<p className="upd-run"><span className="spinner">◌</span> {x.last?.message||'Updating…'}</p>:x.behind?<p className="upd-new">{x.behind} new change(s) waiting{x.commits?.length?':':''}</p>:<p className="upd-ok">Up to date</p>}
   {x.commits&&x.commits.length>0&&<ul>{x.commits.slice(0,5).map(c=><li key={c.sha}>{c.subject}</li>)}</ul>}
   {x.blocker&&<p className="upd-block">{x.blocker}</p>}{(x as any).note&&<p className="ei-why">{(x as any).note}</p>}{x.error&&<p className="upd-block">{x.error}</p>}
   {!x.running&&x.last?.message&&x.last.state!=='running'&&(x.last.message.includes('\n')?<details className="upd-last"><summary>Last update: {x.last.message.split('\n')[0]}</summary><pre className="ei-cmd">{x.last.message.split('\n').slice(1).join('\n')}</pre></details>:<p className="upd-last">Last update: {x.last.message}</p>)}</div>;
 };
 return <div className="upd">
  <button className={'upd-btn'+(pending?' has':'')+(piRunning?' running':'')} onClick={()=>setOpen(!open)} aria-expanded={open} title="Updates for this computer and the Pi">{piRunning?<><span className="spinner">◌</span> Pi updating</>:pending?`⬆ ${pending} update${pending>1?'s':''}`:'⬆'}</button>
  {open&&<div className="upd-pop" role="dialog" aria-label="Updates"><h4>Updates</h4>
   {side('This computer',u?.pc||null,'Loading…')}{side('Pi (Engine and hub)',u?.pi||null,'The hub is offline. The Pi still updates itself every 15 minutes.')}
   <p className="upd-note">Phone and other screens load from the Pi, so they follow the Pi.</p>
   {msgs.length>0&&<ul className="upd-msgs">{msgs.map((m,i)=><li key={i}>{m}</li>)}</ul>}
   <div className="ei-actions"><button disabled={!!busy} onClick={()=>post('check')}>{busy==='check'?'Checking…':'Check now'}</button><button className="approve" disabled={!!busy||piRunning||!pending} onClick={()=>post('apply')}>{busy==='apply'?'Updating…':'Update all and restart'}</button></div>
  </div>}
 </div>;
}
function UpdateControls({build}:{build:Build|null}){
 const [phase,setPhase]=useState<'idle'|'restarting'|'failed'>('idle');
 const restart=build?.restart;
 async function restartNow(){
  setPhase('restarting');
  const before=build?.serverStartedAt;
  try{const r=await fetch('/api/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)throw Error()}catch{setPhase('failed');return}
  const deadline=Date.now()+180000;
  while(Date.now()<deadline){await new Promise(res=>setTimeout(res,1500));try{const r=await fetch('/api/build-status');if(r.ok){const b=await r.json();if(b.serverStartedAt&&b.serverStartedAt!==before){location.reload();return}}}catch{}}
  setPhase('failed');
 }
 const tip=restart?.needed?restart.reasons.join(' ')+(restart.canRestart?' Restart applies it.':' Close the Atlas window and open Start Atlas.cmd again.'):'Reload this screen';
 return <div className="update-controls"><UpdatesPanel onRestart={()=>{const before=build?.serverStartedAt;setPhase('restarting');(async()=>{const deadline=Date.now()+300000;while(Date.now()<deadline){await new Promise(res=>setTimeout(res,1500));try{const r=await fetch('/api/build-status');if(r.ok){const b=await r.json();if(b.serverStartedAt&&b.serverStartedAt!==before){location.reload();return}}}catch{}}setPhase('failed')})()}}/>
  {phase==='restarting'&&<div className="restart-overlay" role="alert"><div><span className="spinner">◌</span><h2>Updating Atlas…</h2><p>Rebuilding the screen and restarting the service. This window reloads by itself.</p></div></div>}
  {restart?.needed&&<button className="update-ready" onClick={restart.canRestart?restartNow:()=>location.reload()} title={tip} disabled={phase==='restarting'}>● {phase==='failed'?'Restart failed · retry':restart.canRestart?'Update ready · Restart':'Update ready · reopen the launcher'}</button>}
  <button className={'reload'+(restart?.needed?' marked':'')} onClick={()=>location.reload()} title={tip} aria-label={'Reload screen'+(restart?.needed?' (update available)':'')}>⟳</button>
 </div>;
}
function BuildBar({build,updating,tasks,paths}:{build:Build|null;updating:boolean;tasks:Task[];paths:Path[]}){
 if(updating)return <div className="buildbar ready" role="status"><div className="buildbar-track"><i style={{width:'100%'}}/></div><span>New version ready · updating this window…</span></div>;
 if(!build||build.state==='idle')return null;
 if(build.state==='done'&&build.updatedAt&&Date.now()-Date.parse(build.updatedAt)>60000)return null;
 const label=build.state==='stalled'?'Paused':build.state==='failed'?'Build failed':build.state==='done'?'Built':'Building';
 const total=build.steps.length;
 return <div className={'buildbar '+build.state} role="status" aria-live="polite" title={build.message}><div className="buildbar-track" aria-hidden="true"><i style={{width:build.progress+'%'}}/></div><span><b>{label}</b> {build.taskId} · {build.title}{(()=>{const l=pathLabel(tasks.find(t=>t.id===build.taskId),paths);return l?` · ${l}`:''})()}{total?` · step ${Math.max(build.step,1)} of ${total}`:''} · {build.progress}%</span><span className="buildbar-msg">{build.message}</span></div>;
}
type Candidate={id:string;title:string;status:string;active:boolean;program?:string;pathName?:string};
type Packet={text:string;tool:string;toolName:string;howTo:string};
function ContinueBuilding({build}:{build:Build|null}){
 const [data,setData]=useState<{tasks:Candidate[];route:{here:any;switchTo:{module:string;moduleName:string}[];alsoAvailableIn:{module:string;moduleName:string}[]};handoffs:{counts:{tool:string;count:number}[]}}|null>(null);
 const [packet,setPacket]=useState<Packet|null>(null),[copied,setCopied]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let alive=true;async function load(){try{const r=await fetch('/api/continue');if(r.ok&&alive)setData(await r.json())}catch{}}load();const t=setInterval(load,5000);return()=>{alive=false;clearInterval(t)}},[]);
 async function start(taskId:string,tool:string){setError('');setCopied(false);try{const r=await fetch('/api/handoff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({taskId,tool})});if(!r.ok)throw Error((await r.json()).error);const p:Packet=await r.json();setPacket(p);try{await navigator.clipboard.writeText(p.text);setCopied(true)}catch{}}catch(e){setError(String((e as Error).message||e))}}
 if(!data)return null;
 const tools=[...data.route.switchTo,...data.route.alsoAvailableIn];
 const switched=data.handoffs.counts.reduce((n,c)=>n+c.count,0);
 return <section className="continue"><header><h3>Continue building</h3><small>{data.route.here?'Atlas can build this itself.':'Atlas cannot build by itself yet (VQ-009), so it hands the work to an app that can.'}</small></header>
  {data.tasks.length===0&&<p className="build-idle">No task is ready to continue.</p>}
  <ul>{data.tasks.slice(0,5).map(t=><li key={t.id} className={t.active?'active':''}><div><strong>{t.id} · {t.title}</strong>{t.pathName&&<small className="path-chip">{PROGRAM_NAMES[t.program||'atlas']||t.program} › {t.pathName}</small>}<small>{t.active&&build?`${build.state==='stalled'?'Paused':build.state==='failed'?'Failed':'In progress'} at step ${Math.max(build.step,1)} of ${build.steps.length}`:t.status==='review'?'Waiting for your review':t.status==='in_progress'?'In progress':'Ready to start'}</small></div><span className="continue-actions">{tools.map(tl=><button key={tl.module} onClick={()=>start(t.id,tl.module)}>Continue in {tl.moduleName}</button>)}</span></li>)}</ul>
  {error&&<p className="continue-error" role="alert">{error}</p>}
  {packet&&<div className="packet" role="dialog" aria-label="Handoff"><header><strong>{copied?'Copied. ':''}Continue in {packet.toolName}</strong><button onClick={()=>setPacket(null)} aria-label="Close">×</button></header><p>{packet.howTo} Its progress will appear here as it works.</p><textarea readOnly value={packet.text} onFocus={e=>e.currentTarget.select()}/>{!copied&&<button onClick={async()=>{try{await navigator.clipboard.writeText(packet.text);setCopied(true)}catch{}}}>Copy</button>}</div>}
  {switched>0&&<small className="switch-log">Handed to other apps {switched} time{switched===1?'':'s'}: {data.handoffs.counts.map(c=>`${c.tool} ${c.count}`).join(', ')}. Frequent switches show what Atlas should learn next.</small>}
 </section>;
}
function ActiveBuild({build}:{build:Build|null}){
 if(!build||build.state==='idle')return <p className="build-idle">No builder is active right now.</p>;
 return <article className={'active-build '+build.state}><header><strong>{build.taskId} · {build.title}</strong><span>{build.progress}%</span></header><div className="buildbar-track"><i style={{width:build.progress+'%'}}/></div><p>{build.message}</p><ol>{build.steps.map((s,i)=>{const n=i+1;const st=build.state==='done'||n<build.step?'complete':n===build.step?build.state:'upcoming';return <li key={s} className={st}><span>{st==='complete'?'✓':st==='upcoming'?'○':st==='stalled'?'⏸':st==='failed'?'✕':'◌'}</span>{s}</li>})}</ol>{build.updatedAt&&<small>Last update {new Date(build.updatedAt).toLocaleTimeString('en-GB',{timeZone:'Europe/Paris'})}</small>}</article>;
}
const domains=[
 {name:'LLM Reasoning',x:492,y:309,r:90,icon:'♧',description:'Natural language models enable general-purpose reasoning through language, with broad world knowledge and flexible inference. Serves as the foundation for connecting and integrating alternative reasoning paradigms.',sat:[]},
 {name:'Mathematical Reasoning',x:284,y:176,r:54,icon:'∑',description:'Proof, abstraction and computation provide precise tools for reasoning.',sat:['Proof Generation','Abstract Algebra','Formal Methods','Computational Mathematics']},
 {name:'Symbolic Logic',x:492,y:115,r:48,icon:'♧',description:'Explicit representations connect knowledge, rules and formal verification.',sat:['Knowledge Graphs','Formal Verification','Theorem Proving','Logical Representations']},
 {name:'Physics-based Models',x:693,y:180,r:51,icon:'⚛',description:'Simulation and physical constraints connect models to the world.',sat:['Differential Equations','Causal Modeling','Simulation & World Models','Physical Constraints']},
 {name:'Evolutionary Search',x:714,y:326,r:53,icon:'⚯',description:'Explore alternative solutions through variation, selection and adaptation.',sat:['Genetic Algorithms','Multi-objective Optimization','Automated Program Search','Open-ended Evolution']},
 {name:'Chemical Computing',x:675,y:473,r:51,icon:'♧',description:'Investigate computation through molecular systems and reaction networks.',sat:['Molecular Computation','Reaction Networks','DNA Computing','Bio-inspired Algorithms']},
 {name:'Meta-Reasoner',x:492,y:511,r:53,icon:'◇',description:'Reason about strategy, decomposition and how reasoning can improve.',sat:['Strategy Selection','Self-improvement','Task Decomposition','Reasoning about Reasoning']},
 {name:'Embodied Intelligence',x:295,y:473,r:51,icon:'⬡',description:'Connect reasoning to perception, action and interaction with an environment.',sat:['Robotics','Environment Interaction','Sensorimotor Learning','Spatial Reasoning']},
 {name:'Neuro-symbolic Systems',x:261,y:326,r:53,icon:'♧',description:'Combine neural learning with structured symbolic representations.',sat:['Neural-Symbolic Integration','Program Induction','Structured Memory','Interpretable AI']},
];
const stages=['LLMs','Hybrid Reasoning','Mechanism Transfer','Adaptive Architectures','Reasoning Discovery'];
const captions=['General language reasoning','Combine complementary methods','Adapt insights across domains','Dynamic method selection and integration','New reasoning paradigms and capabilities'];
function lines(name:string){if(name.length<17)return [name];const words=name.split(' ');return [words.slice(0,-1).join(' '),words.at(-1)!]}
function Graph({selected,onSelect,query,list}:{selected:number;onSelect:(i:number)=>void;query:string;list:boolean}){
 const matches=domains.map((d,i)=>({d,i})).filter(({d})=>(d.name+' '+d.sat.join(' ')).toLowerCase().includes(query.toLowerCase()));
 if(!matches.length)return <div className="empty"><h2>No matching concepts</h2><p>Try a different search or clear the search field.</p></div>;
 if(list)return <div className="node-list">{matches.map(({d,i})=><button key={d.name} onClick={()=>onSelect(i)} aria-pressed={selected===i}><strong>{d.name}</strong><span>{d.description}</span><small>Connected to {i===0?'all eight research domains':'LLM Reasoning'} · Synthetic research content</small></button>)}</div>;
 return <svg className="graph" viewBox="0 0 934 602" role="group" aria-label="Knowledge map: nine synthetic research domains"><defs><radialGradient id="node"><stop stopColor="#6135a2"/><stop offset=".8" stopColor="#22104d"/><stop offset="1" stopColor="#6e32ba"/></radialGradient><radialGradient id="core"><stop stopColor="#433161"/><stop offset=".8" stopColor="#20143d"/><stop offset="1" stopColor="#9c473c"/></radialGradient><filter id="glow" x="-70%" y="-70%" width="240%" height="240%"><feGaussianBlur stdDeviation="5"/></filter><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#666396" strokeOpacity=".17" strokeWidth=".6"/></pattern></defs>
 <rect width="934" height="602" fill="url(#grid)"/>
 {[115,145,180,220,270].map(r=><circle key={r} cx="492" cy="309" r={r} className="orbit"/>)}
 {Array.from({length:80},(_,i)=><circle key={i} cx={(i*137+19)%934} cy={(i*83+29)%602} r={i%7===0?1.6:.6} fill={i%5===0?'#ff8a32':'#b991ef'} opacity=".5"/>)}
 {domains.slice(1).map(d=><g key={d.name}><path d={`M492 309 Q${(492+d.x)/2+12} ${(309+d.y)/2-9} ${d.x} ${d.y}`} stroke="#ff9b4a" strokeWidth="5" filter="url(#glow)"/><path d={`M492 309 Q${(492+d.x)/2+12} ${(309+d.y)/2-9} ${d.x} ${d.y}`} stroke="#dba7fc" strokeWidth="1.5" fill="none"/></g>)}
 {domains.slice(1).map((d,idx)=><g key={d.name}>{d.sat.map((label,j)=>{let x:number,y:number,anchor:'start'|'end'|'middle';if(idx===1){const a=(210+j*40)*Math.PI/180;x=d.x+Math.cos(a)*90;y=d.y+Math.sin(a)*90;anchor=j<2?'end':'start'}else if(idx===5){x=d.x+(j<2?-1:1)*(j%2?80:110);y=d.y+30+(j%2)*25;anchor=j<2?'end':'start'}else{const side=d.x<492?-1:1;x=d.x+side*(105+Math.sin(j)*12);y=d.y-75+j*34;anchor=side<0?'end':'start'}return <g key={label}><line x1={d.x} y1={d.y} x2={x} y2={y} stroke="#b080de" strokeWidth="1"/><circle cx={x} cy={y} r="8" fill="#ac71ee" filter="url(#glow)"/><circle cx={x} cy={y} r="7" fill="#9c60e7" stroke="#e4c6ff"/><text x={x+(anchor==='end'?-18:18)} y={y+4} textAnchor={anchor} className="satellite">{lines(label).map((s,k)=><tspan key={s} x={x+(anchor==='end'?-18:18)} dy={k?13:0}>{s}</tspan>)}</text></g>})}</g>)}
 {domains.map((d,i)=><g key={d.name} role="button" tabIndex={0} aria-label={`Select ${d.name}`} aria-pressed={i===selected} onClick={()=>onSelect(i)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(i)}}} className={'graph-node '+(matches.some(m=>m.i===i)?'':'filtered')}><circle cx={d.x} cy={d.y} r={d.r+3} fill="none" stroke={i===selected?'#ff8a32':'#a76bff'} strokeWidth="8" filter="url(#glow)"/><circle cx={d.x} cy={d.y} r={d.r} fill={`url(#${i===0?'core':'node'})`} stroke={i===selected?'#ffc088':'#dbb7ff'} strokeWidth="2"/><text x={d.x} y={d.y-(i===0?44:23)} textAnchor="middle" className={'node-icon '+(i===0?'orange':'')}>{d.icon}</text><text x={d.x} y={d.y-(i===0?16:0)} textAnchor="middle" className={i===0?'core-label':'node-label'}>{lines(d.name).map((s,k)=><tspan key={s} x={d.x} dy={k?(i===0?24:17):0}>{s}</tspan>)}</text>{i===0&&<text x={d.x} y={d.y+30} textAnchor="middle" className="satellite"><tspan x={d.x}>Natural language</tspan><tspan x={d.x} dy="15">reasoning, world knowledge,</tspan><tspan x={d.x} dy="15">flexible inference</tspan></text>}</g>)}
 </svg>;
}
function App(){
 const {build,updating}=useBuild();
 const [hub,setHub]=useState<Hub|null>(null);
 const attention=useEngineAttention(hub);
 const loadHub=async()=>{try{const r=await fetch('/api/hub/status');if(r.ok)setHub(await r.json())}catch{}};
 useEffect(()=>{loadHub();const t=setInterval(loadHub,15000);return()=>clearInterval(t)},[]);
 const [graphData,setGraphData]=useState<GraphData|null>(null),[focusId,setFocusIdRaw]=useState<string>(()=>store.get('atlas-focus')||''),[mapMode,setMapMode]=useState<'concepts'|'functions'>('concepts'),[picked,setPicked]=useState<Picked|null>(null);
 const setFocusId=(id:string)=>{setFocusIdRaw(id);store.set('atlas-focus',id);setPicked(null);setMapMode('concepts')};
 useEffect(()=>{let alive=true;async function load(){try{const r=await fetch('/api/graph');if(r.ok&&alive){const g=await r.json();setGraphData(g)}}catch{}}load();const t=setInterval(load,30000);return()=>{alive=false;clearInterval(t)}},[]);
 const [selected,setSelectedRaw]=useState(0),[query,setQuery]=useState(''),[list,setList]=useState(false),[lens,setLens]=useState<'functions'|'skills'>('functions'),[view,setView]=useState('Today');
 const [forgeStage,setForgeStage]=useState<{stage:'ideas'|'plan'|'build';n:number}|null>(null);
 const dir=useDirection(hub,view==='Forge');
 const builder=useBuilder(hub,true,view==='Forge'?5000:15000);
 const forgeHot=!!builder.s?.lanes.some(l=>l.state==='running')||build?.state==='building';
 const openBuild=()=>{setForgeStage({stage:'build',n:Date.now()});setView('Forge')};
 const [stamp,setStamp]=useState(0);
 useEffect(()=>{const u=new URLSearchParams(location.search);if(u.has('queue')){const h=parseInt(u.get('highlight')||'',10);goTo('Forge › Build › Queue'+(h?` #${h}`:''));u.delete('queue');u.delete('highlight');history.replaceState(null,'',location.pathname+(u.toString()?'?'+u:'')+location.hash)}},[]);  // deep link ?queue&highlight=N (#181)
 useEffect(()=>{const f=(e:Event)=>goTo(String((e as CustomEvent).detail||''));window.addEventListener('atlas-go',f);return()=>window.removeEventListener('atlas-go',f)});
 const openHit=(kind:string,ref:any)=>{if(kind==='function'){setView('Map');setFocusId('all');setTimeout(()=>setPicked({kind:'function',item:ref,outside:false}),0)}else if(kind==='atlas task')openBuild();else if(kind==='note')setView('Notes');else if(kind==='screen')goTo(ref);else{setForgeStage({stage:kind==='idea'?'ideas':'plan',n:Date.now()});setView('Forge')}};
 // Speak to Cortex: go to a section by its label, e.g. 'Forge › Plan' (owner, 2026-10-25)
 const goTo=(label:string)=>{const qm=/^Forge › Build › Queue(?: #(\d+))?$/.exec(label);if(qm){setForgeStage({stage:'build',n:Date.now()});setView('Forge');openQueue(qm[1]?+qm[1]:null);return}
  const m=/^Forge › (Ideas|Plan|Build)$/.exec(label);if(m){setForgeStage({stage:m[1].toLowerCase() as 'ideas'|'plan'|'build',n:Date.now()});setView('Forge')}else if(label)setView(label)};
 const [reminders,setReminders]=useState<Reminder[]>([]),[convo,setConvo]=useState<{role:string;text:string;ts:string}[]|null>(null),[thoughts,setThoughts]=useState<Thought[]|null>(null);
 const [notes,setNotes]=useState<Note[]>([]),[tasks,setTasks]=useState<Task[]>([]),[paths,setPaths]=useState<Path[]>([]),[connection,setConnection]=useState('loading'),[saving,setSaving]=useState(false),[message,setMessage]=useState('');
 async function refresh(){try{const results=await Promise.all(['/api/notes','/api/progress','/api/health','/api/reminders'].map(async url=>{const r=await fetch(url);if(!r.ok)throw Error('Service unavailable');return r.json()}));setNotes(results[0]);setTasks(results[1].tasks);setPaths(results[1].paths||[]);setReminders(results[3]);setConnection('online')}catch{setConnection('offline')}}
 useEffect(()=>{refresh();const timer=setInterval(refresh,5000);return()=>clearInterval(timer)},[]);
 useEffect(()=>{function key(e:KeyboardEvent){if((e.ctrlKey&&e.key==='k')||(e.key==='/'&&!['INPUT','TEXTAREA'].includes((e.target as HTMLElement).tagName))){e.preventDefault();document.getElementById('search')?.focus()}}window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)},[]);
 async function toggle(r:Reminder){const done=!r.doneAt;setReminders(rs=>rs.map(x=>x.id===r.id?{...x,doneAt:done?new Date().toISOString():null}:x));try{const res=await fetch(`/api/reminders/${encodeURIComponent(r.id)}/done`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({done})});if(!res.ok)throw Error()}catch{setMessage('Could not update the reminder.')}await refresh()}
 const loadConvo=async()=>{try{const r=await fetch('/api/engine/engine.brain.conversation');if(r.ok){const c=(await r.json()).result;setConvo(c);return c as {role:string;text:string;ts:string}[]}}catch{}return null};
 useEffect(()=>{if(hubHas(hub,'engine.brain.conversation'))loadConvo()},[hub?.reachable,hub?.functions?.length]);
 const [pcVoice,setPcVoice]=useState<LocalVoice|null>(null),[cortex,setCortex]=useState<{name:string;named:boolean}|null>(null);
 const loadCortex=()=>fetch('/api/engine/engine.brain.cortex').then(r=>r.ok?r.json():null).then(b=>b&&setCortex(b.result)).catch(()=>{});
 useEffect(()=>{if(hub?.reachable)loadCortex()},[hub?.reachable]);
 const cx=cortex?.name||'Cortex';
 const graphShown:GraphData|null=graphData&&cx!=='Cortex'?{...graphData,domains:graphData.domains.map((d:any)=>({...d,name:renameCortex(d.name,cx),summary:renameCortex(d.summary,cx)})),functions:graphData.functions.map((f:any)=>({...f,summary:renameCortex(f.summary,cx),note:f.note&&renameCortex(f.note,cx),atlasSection:f.atlasSection&&renameCortex(f.atlasSection,cx)}))}:graphData;
 useEffect(()=>{const f=()=>fetch('/api/voice/local').then(r=>r.ok?r.json():null).then(setPcVoice).catch(()=>{});f();window.addEventListener('atlas-voice',f);return()=>window.removeEventListener('atlas-voice',f)},[]);
 const [sources,setSources]=useState<Source[]|null>(null);
 const loadSources=async()=>{try{const r=await fetch('/api/sources');if(r.ok)setSources(await r.json())}catch{}};
 useEffect(()=>{loadSources()},[]);
 const loadThoughts=async()=>{try{const r=await fetch('/api/engine/engine.brain.recent_thoughts');if(r.ok)setThoughts((await r.json()).result)}catch{}};
 useEffect(()=>{if(!hubHas(hub,'engine.brain.recent_thoughts')){setThoughts(null);return}loadThoughts();const t=setInterval(loadThoughts,30000);return()=>clearInterval(t)},[hub?.reachable,hub?.functions?.length]);
 async function undoThought(id:number){setMessage('');try{const r=await fetch('/api/engine/engine.brain.undo_changes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thought:id})});const b=await r.json();setMessage(b.message||b.error)}catch{setMessage('The hub cannot be reached; nothing was undone.')}await loadThoughts()}
 const waiting=reminders.filter(r=>!r.doneAt).length+attention.count;
 const setSelected=(i:number)=>{setSelectedRaw(i);setPicked(null)};
 const activeFocus=graphData?.focuses.find(f=>f.id===(focusId||graphData.focuses.find(x=>x.current)?.id))||null;
 const focusKey=focusId==='all'?'all':activeFocus?.id||'all';
 const conceptView=focusKey!=='all'&&!!activeFocus?.concepts&&mapMode==='concepts';
 const d=domains[selected];
 const navMain=[['☀','Today'],['◎','Focus'],['⚒','Forge'],['✺','Cortex'],['▤','Notes']],navLibrary=[['⌘','Map'],['▥','Research'],['▱','Resources'],['◫','Memory'],['◇','Tags'],['▧','Archive']],navSystems=[['⬢','Systems']],navHorizon=[['✧','Research map'],['⌁','Roadmap'],['⚗','Experiments'],['♧','Hypotheses']];
 const built=['Today','Focus','Forge','Cortex','Notes','Map','Research','Resources','Memory','Systems','Research map','Roadmap'];
 const navItem=([icon,name]:string[])=><button key={name} className={'nav-item '+(view===name?'active':'')+(!built.includes(name)?' unbuilt':'')+(name==='Forge'&&forgeHot?' forge-hot':'')} aria-disabled={!built.includes(name)} onClick={()=>setView(name)}><span>{name==='Build progress'&&build?.state==='building'?<span className="spinner" aria-hidden="true">◌</span>:icon}</span>{name==='Cortex'?cx:name}{name==='Today'&&waiting>0&&<b className="nav-count">{waiting}</b>}{name==='Forge'&&forgeHot&&<small>{buildingOn(builder.s)>1?`building on ${buildingOn(builder.s)} devices…`:'building…'}</small>}{!built.includes(name)&&<small>Not built yet</small>}</button>;
 return <CortexCtx.Provider value={cx}><SearchCtx.Provider value={{graph:graphShown,tasks,notes,hub,open:openHit}}><a className="skip" href="#main">Skip to workspace</a><header><div className="brand"><span className="brand-icon">⬡</span><div><h1>Reasoning Atlas</h1><p>Visual notebook for cross-domain intelligence</p></div></div><span className="header-gap"/><HeaderSearch/>{message&&<p className="app-toast" role="status">{message}<button className="linkish" aria-label="Dismiss" onClick={()=>setMessage('')}>×</button></p>}<QuickCortex hub={hub} view={view} go={goTo} pcVoice={pcVoice} onChanged={()=>setStamp(n=>n+1)} openChat={()=>setView('Cortex')}/>{waiting>0&&<button className="waiting" onClick={()=>setView('Today')} title="Open Today">● {waiting} waiting on you</button>}<HubChip hub={hub} onOpen={()=>setView('Systems')}/><span className={'connection '+connection} role="status">● {connection==='online'?'Local':connection==='loading'?'Loading':'Offline'}</span><UpdateControls build={build}/><BuildBar build={build} updating={updating} tasks={tasks} paths={paths}/></header>
 <div className={"workspace"+(view==="Cortex"?" chat-wide":"")}><nav aria-label="Primary navigation"><div className="eyebrow">Reasoning Atlas</div>{navMain.map(navItem)}<div className="eyebrow library">Library</div>{navLibrary.map(navItem)}<div className="eyebrow library">Connections</div>{navSystems.map(navItem)}<div className="horizon"><div className="eyebrow">Horizon <small>long-term vision, outside the build plan</small></div>{navHorizon.map(navItem)}<div className="quote"><div className="constellation">✧ · ✦<br/> · ✧ · ✧</div>Different ways of reasoning illuminate different parts of the truth.<div className="orange">—</div></div></div></nav>
 <main id="main" key={stamp} tabIndex={-1}>{connection==='offline'&&<div className="banner" role="alert">Local service is offline. Your draft stays on this device. <button onClick={refresh}>Retry connection</button></div>}
 {view==='Map'?<section className="map panel"><div className="panel-heading map-heading"><h2>▦ {lens==='skills'?'Skill tree':focusKey==='all'?'Everything':'Function map'}</h2>{lens==='functions'?<div className="focus-chips" role="tablist" aria-label="Focus">{(graphData?.focuses||[]).filter(f=>!(f as any).horizon).map(f=><button key={f.id} role="tab" aria-selected={focusKey===f.id} className={'chip'+(focusKey===f.id?' on':'')} onClick={()=>setFocusId(f.id)} title={f.summary}>{f.name}{f.current&&<b className="orange"> ●</b>}</button>)}<button role="tab" aria-selected={focusKey==='all'} className={'chip all'+(focusKey==='all'?' on':'')} onClick={()=>setFocusId('all')} title="No focus: every program, area and function with all links">Everything</button></div>:<span className="focus-chips"/>}<div className="map-actions"><span className="note-dest inline" role="radiogroup" aria-label="Map lens"><button role="radio" aria-checked={lens==='functions'} className={'chip'+(lens==='functions'?' on':'')} onClick={()=>setLens('functions')} title="What Atlas does: programs, areas and functions">Functions</button><button role="radio" aria-checked={lens==='skills'} className={'chip'+(lens==='skills'?' on':'')} onClick={()=>setLens('skills')} title="What Atlas can learn next: capabilities in build order, with proof">Skill tree</button></span>{lens==='functions'&&<button onClick={()=>setList(!list)}>{list?'Graph view':'List view'}</button>}</div></div>{lens==='skills'?<Safe name="Skill tree"><SkillTree hub={hub}/></Safe>:!graphShown?<p className="build-idle">Loading the function map…</p>:list?<FunctionList data={graphShown} focusId={focusKey} onPick={setPicked}/>:<FunctionGraph data={graphShown} focusId={focusKey} picked={picked?.kind==='function'?picked.item.id:picked?picked.item.id:null} onPick={setPicked}/>}{lens==='functions'&&<span className="fixture-label">{`Functions from the registry${focusKey==='all'?' · everything':` · ${activeFocus?.name}`}`}</span>}</section>:view==='Research map'?<><section className="map panel"><div className="panel-heading map-heading"><h2>✧ Research map</h2><label className="search inline-search"><span>⌕</span><input placeholder="Search research concepts…" aria-label="Search research concepts" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="Clear search" onClick={()=>setQuery('')}>×</button>}</label><div className="map-actions"><button onClick={()=>setList(!list)}>{list?'Graph view':'List view'}</button></div></div><Graph selected={selected} onSelect={setSelected} query={query} list={list}/><span className="fixture-label">Horizon · synthetic research map, not implemented</span></section><Roadmap/></>:view==='Roadmap'?<><div className="view-title"><h2>Research roadmap</h2><p>Horizon: a long-term research direction for a later product, not part of Atlas's build plan and not a claim that these engines exist.</p></div><Roadmap/></>:view==='Today'?<section className="panel page"><h2>Today</h2><p>Your day at a glance, and everything only you can settle.</p><Safe name="Today"><TodayGlance hub={hub} onPlan={()=>{setForgeStage({stage:'plan',n:Date.now()});setView('Forge')}}/></Safe><Safe name="From the Engine"><EngineAttention att={attention}/></Safe>{hub?.reachable&&!hubHas(hub,'engine.attention.finish_terminal_step')&&<Outdated what="Answering the Engine's items from Atlas"/>}{hub&&!hub.reachable&&hub.configured&&<p className="build-idle">The Engine's items appear here when the hub is online.</p>}<h4 className="local-title">Reminders <small>ticks are saved on this computer</small></h4><Reminders items={reminders} onToggle={toggle}/></section>:view==='Cortex'?<Safe name="Cortex"><BrainPage go={goTo} hub={hub} dir={dir} cortex={cortex} onNamed={loadCortex} convo={convo} loadConvo={loadConvo} thoughts={thoughts} loadThoughts={loadThoughts} undoThought={undoThought} pcVoice={pcVoice}/></Safe>:view==='Notes'?<section className="panel page"><h2>Notes</h2><p>Your thoughts: those sent to {cx} and what they became, and notes kept on this computer.</p><Safe name="For you"><ForYou/></Safe>{hub?.reachable&&!hubHas(hub,'engine.brain.recent_thoughts')&&<Outdated what={cx+'\u2019s list of thoughts'}/>}<EngineBrain thoughts={thoughts}/><p className="muted">To undo what a thought changed, open <button className="linkish" onClick={()=>goTo('Memory')}>Memory › What changed</button>.</p>{convo&&convo.length>0&&<section className="engine-attention brain convo"><h4>Conversation with Atlas <small>continue in {cx}</small></h4><ul>{convo.slice(-10).map((m,i)=><li key={i} className={m.role==='me'?'me':'atlas'}><b>{m.role==='me'?'You':'Atlas'}</b><p>{m.text}</p></li>)}</ul></section>}<Safe name="Local sources"><LocalSources items={sources} onChange={loadSources}/></Safe><h2 className="notes-title">Saved notes</h2><p>Stored locally on this computer.</p>{connection==='loading'?<p role="status">Loading notes…</p>:notes.length?notes.map(n=><article className="saved-note" key={n.id}><time>{new Date(n.createdAt).toLocaleString('en-GB',{timeZone:'Europe/Paris'})}</time><p>{n.body}</p></article>):<p className="empty">No saved notes yet. Press ✦ at the top (or Ctrl+J) and choose This computer.</p>}</section>:view==='Build progress'?<Redirect go={openBuild}/>:view==='Systems'?<Safe name="Systems"><SystemsPage hub={hub} onHub={loadHub}/></Safe>:view==='Memory'?<Safe name="Memory"><MemoryPage hub={hub}/></Safe>:view==='Resources'?<Safe name="Resources"><ResourcesPage hub={hub}/></Safe>:view==='Research'?<Safe name="Research"><ResearchPage hub={hub}/></Safe>:view==='Forge'?<Safe name="Forge"><Forge hub={hub} dir={dir} build={build} want={forgeStage} builder={builder} tasks={tasks} paths={paths}/></Safe>:view==='Focus'?<Safe name="Focus"><FocusPage dir={dir} hub={hub}/></Safe>:<section className="panel empty"><span className="placeholder-icon">◇</span><h2>{view}</h2><p>Not built yet</p><p>{view==='Systems'?'All your apps as modules in one place: what each can do, where it lives and how far it is integrated (RA-021).':'This component will become available as its implementation is completed.'}</p>{view==='Systems'&&<div className="systems-todo"><h3>Apps to dissect first</h3><Reminders items={reminders} onToggle={toggle} group="Apps to dissect"/></div>}<button onClick={()=>setView('Build progress')}>Follow build progress</button></section>}
 </main><aside><FocusPanel dir={dir} onOpen={()=>setView('Focus')}/>{view==='Map'&&picked?<Inspector picked={picked} data={graphShown} onPick={setPicked} onFocus={setFocusId}/>:view==='Research map'?<section className="panel inspector"><h2>♧ Selected concept</h2><h3><span className="orange">{d.icon}</span>{d.name}</h3><div className="tags"><span>Research concept</span><span>Horizon</span></div><p>{d.description}</p><h4>Key connections</h4>{(selected===0?[1,8,4,6]:[0]).map(i=><button className="connection-item" key={i} onClick={()=>setSelected(i)}>● <span>{domains[i].name}</span></button>)}<small className="provenance">Illustrative association · not verified evidence</small></section>:null}<MiniBuild build={build} builder={builder} onOpen={openBuild}/></aside></div></SearchCtx.Provider></CortexCtx.Provider>;
}
type SNode={id:number;title:string;branch:string;state:string;done:number;total:number;proof:string;proof_text:string;requires:number[]|string|null;description:string|null;evidence:{ref:string;title:string;prs:string[]}[];suggested:{ref:string;title:string;score:number}[];depth:number;status:string;open:number};
type SOption={title:string;kind:string;why:string;steps:string[];risks:string;effort_h:number|null;source:string};
type SDetail=SNode&{tasks:{id:number;title:string;status:string}[];ideas:{id:number;title:string}[];research:{id:number;summary:string;options:SOption[];recommended:number|null;created:string}|null};
const STATE_TEXT:Record<string,string>={unlocked:'built',building:'building',available:'ready to build',locked:'locked'};
function SkillTree({hub}:{hub:Hub|null}){
 const [t,setT]=useState<{branches:{key:string;title:string}[];nodes:SNode[];counts:any}|null>(null),[pick,setPick]=useState<number|null>(null),[d,setD]=useState<SDetail|null>(null),[busy,setBusy]=useState(''),[msg,setMsg]=useState('');
 const ok=hubHas(hub,'engine.growth.skill_tree');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.growth.skill_tree');if(r.ok)setT((await r.json()).result)}catch{}};
 const open=async(id:number,keep=false)=>{setPick(id);if(!keep)setMsg('');try{const r=await fetch('/api/engine/engine.growth.skill_tree?node='+id);if(r.ok)setD((await r.json()).result.node)}catch{}};
 useEffect(()=>{if(ok)load()},[ok]);
 const act=async(kind:string,action:string,body:object)=>{setBusy(kind);setMsg('');try{setMsg(await post(action,body));await load();if(pick)await open(pick,true)}catch(e){setMsg((e as Error).message)}finally{setBusy('')}};
 if(!hub?.reachable)return <p className="build-idle">The skill tree comes from the Engine; it appears when the hub is online.</p>;
 if(!ok)return <Outdated what="The skill tree"/>;
 if(!t)return <p className="build-idle">Loading the skill tree…</p>;
 const byId=new Map(t.nodes.map(n=>[n.id,n]));
 const reqs=(n:SNode)=>(Array.isArray(n.requires)?n.requires:String(n.requires||'').split(',').map(Number).filter(Boolean)).map(i=>byId.get(i)).filter(Boolean) as SNode[];
 return <div className="skill-tree">
  <p className="st-sum">{t.counts.unlocked} built · {t.counts.building} building · {t.counts.available} ready to build · {t.counts.locked} locked <small>Proof counts only done tasks and merged code.</small></p>
  <div className="st-cols">{t.branches.map(b=>{const ns=t.nodes.filter(n=>n.branch===b.key).sort((x,y)=>x.depth-y.depth||x.id-y.id);return ns.length>0&&<div className="st-col" key={b.key}><h5>{b.title}</h5>{ns.map(n=><button key={n.id} className={'st-node s-'+n.state+' p-'+n.proof+(pick===n.id?' on':'')} onClick={()=>open(n.id)} title={n.proof_text}><b>{n.title}</b><small>{STATE_TEXT[n.state]||n.state}{n.total?` · ${n.done}/${n.total}`:''}{n.proof==='suggested'?' · proof?':''}</small></button>)}</div>})}</div>
  {pick&&d&&d.id===pick&&<section className="st-panel"><div className="ei-head"><h4>{d.title}</h4><span className="chip">{STATE_TEXT[d.state]||d.state}</span><button className="linkish" onClick={()=>{setPick(null);setD(null)}}>Close</button></div>
   {d.description&&<p>{d.description}</p>}
   <p className="muted">{d.proof_text}</p>
   {reqs(d).length>0&&<p><b>Needs first:</b> {reqs(d).map((r,i)=><span key={r.id}>{i>0&&', '}<button className="linkish" onClick={()=>open(r.id)}>{r.title}</button> <small>({STATE_TEXT[r.state]||r.state})</small></span>)}</p>}
   {d.evidence.length>0&&<><h5>Proof</h5><ul>{d.evidence.map(e=><li key={e.ref}>{e.ref} {e.title} {e.prs.length>0&&<small>{e.prs.join(', ')}</small>}</li>)}</ul></>}
   {d.suggested.length>0&&<><h5>Looks like proof <small>inferred from words, not verified</small></h5><ul>{d.suggested.map(e=><li key={e.ref}>{e.ref} {e.title} <span className="ei-actions"><button className="approve" disabled={!!busy} onClick={()=>act('c','engine.growth.confirm_proof',{node:d.id,ref:e.ref})}>Yes, built</button><button className="deny" disabled={!!busy} onClick={()=>act('c','engine.growth.confirm_proof',{node:d.id,ref:e.ref,dismiss:true})}>Not this</button></span></li>)}</ul></>}
   {d.tasks.length>0&&<><h5>Work on it</h5><ul>{d.tasks.map(x=><li key={x.id}>#{x.id} {x.title} <small>{x.status}</small></li>)}</ul></>}
   {d.state!=='unlocked'&&<div className="ei-actions">
    {hubHas(hub,'engine.growth.node_research')&&<button disabled={!!busy} title="Claude compares ways to build it: an outside tool, something you already built, or building it; one research call" onClick={()=>act('r','engine.growth.node_research',{node:d.id})}>{busy==='r'?'Researching…':d.research?'Research again':'Research how'}</button>}
    {hubHas(hub,'engine.growth.draft_node_task')&&<button disabled={!!busy} title="A draft task that builds this node; accept it under Proposals in Forge › Plan" onClick={()=>act('t','engine.growth.draft_node_task',{node:d.id})}>Draft a task</button>}
    {d.proof!=='confirmed'&&<button className="linkish" disabled={!!busy} title="You already have this capability: mark it built (undoable)" onClick={()=>act('c','engine.growth.confirm_proof',{node:d.id})}>I already have this</button>}
   </div>}
   {d.research&&<div className="st-research"><h5>Ways to build it <small>{new Date(d.research.created).toLocaleDateString('en-GB')}</small></h5>{d.research.summary&&<p>{d.research.summary}</p>}<ol>{d.research.options.map((o,i)=><li key={i} className={d.research!.recommended===i?'rec':''}><b>{o.title}</b> <span className="chip small">{o.kind}</span>{d.research!.recommended===i&&<span className="chip small on">recommended</span>}{o.effort_h?<small> ~{o.effort_h} h</small>:null}<p>{o.why}</p>{o.kind==='adopt'&&<p className="muted">Outside code: the task waits for your approval, and runs only in a sandbox.</p>}<button className="small-btn" disabled={!!busy} onClick={()=>act('p','engine.growth.node_research',{pick:d.research!.id,option:i})}>Pick this → draft task</button></li>)}</ol></div>}
   {msg&&<p className="save-status" role="status">{msg}</p>}
  </section>}
 </div>;
}
type RItem={id:number;source:string;label:string;kind:'tool'|'development';title:string;url:string;summary:string;state:string;found:string;topics:string[];why:string;impact:string;matched:string;prereq?:{line:string;ready:boolean}};
type RNote={id:number;question:string;status:string;created:string;idea_id:number|null;answer:{summary?:string;matters?:string;risks?:string;next_steps?:string[];idea?:string;sources?:string[]}};
type RInt={word:string;hits:number;useful:number;noise:number;verdict:string;boosted:boolean;own:boolean};
type RData={feed:RItem[];review?:{interests:RInt[];tuning:{mute:string[];boost:string[];add:string[]};findings:number;ready:boolean;note:string};notes:RNote[];settings:{every_hours:number;sources:string[];per_source:number;repos:string[];keep_days:number};all_sources:Record<string,string>;last_scan:string;due:boolean};
function ResearchPage({hub}:{hub:Hub|null}){
 const [r,setR]=useState<RData|null>(null),[tab,setTab]=useState<'feed'|'saved'|'deep'|'interests'|'sources'>('feed'),[kind,setKind]=useState<'all'|'development'|'tool'>('all'),[busy,setBusy]=useState(''),[msg,setMsg]=useState(''),[q,setQ]=useState(''),[newWord,setNewWord]=useState(''),[conf,setConf]=useState<RData['settings']|null>(null),[repos,setRepos]=useState('');
 const ok=hubHas(hub,'engine.research.research_feed');
 const load=async()=>{try{const x=await fetch('/api/engine/engine.research.research_feed');if(x.ok){const d=(await x.json()).result as RData;setR(d);setConf(c=>c||d.settings);setRepos(v=>v||d.settings.repos.join('\n'))}}catch{}};
 useEffect(()=>{if(ok)load()},[ok]);
 const act=async(k:string,action:string,body:object)=>{setBusy(k);setMsg('');try{setMsg(await post(action,body));await load()}catch(e){setMsg((e as Error).message)}finally{setBusy('')}};
 const ask=async(question:string,item?:number)=>{setTab('deep');await act('ask','engine.research.find_options',item?{question,item}:{question});setQ('')};
 const head=<div className="view-title"><h2>Research</h2><p>What is happening in AI that touches Atlas or your interests, checked by rule every few hours; ask Claude when something deserves a closer look.</p></div>;
 if(!hub?.reachable)return <section className="panel page">{head}<p className="build-idle">Research comes from the Engine; it appears when the hub is online.</p></section>;
 if(!ok)return <section className="panel page">{head}<Outdated what="Research"/></section>;
 if(!r)return <section className="panel page">{head}<p className="build-idle">Loading research…</p></section>;
 const saved=r.feed.filter(i=>i.state==='starred'),feed=r.feed.filter(i=>i.state!=='starred'&&(kind==='all'||i.kind===kind));
 const item=(i:RItem)=><li key={i.id} className={'engine-item r-item imp-'+i.impact}><div className="ei-head"><span className="chip small">{i.label}</span><span className={'chip small '+(i.kind==='tool'?'r-tool':'')}>{i.kind==='tool'?'tool':'development'}</span><strong><a href={i.url} target="_blank" rel="noreferrer noopener">{i.title}</a></strong>{i.impact!=='low'&&<small className="ei-key">{i.impact}</small>}</div>
  {i.summary&&<p className="ei-why">{i.summary.slice(0,220)}</p>}
  {i.why&&<p className="r-why"><b>Why it matters</b> {i.why}{i.topics.length>0&&<small> · {i.topics.join(', ')}</small>}</p>}
  {i.kind==='tool'&&i.prereq&&<p className={'r-pre'+(i.prereq.ready?'':' warn')}><b>Before you start</b> {i.prereq.line}</p>}
  <div className="ei-actions">
   {i.kind==='tool'?(['adopt','adapt','build'] as const).map(a=><button key={a} disabled={!!busy} title={{adopt:'Use the tool as it is: an idea that waits for sandbox and approval',adapt:'Take parts of it into Atlas or a project (licence check first)',build:'Build our own version of the idea, without the outside code'}[a]} onClick={()=>act('i'+i.id,'engine.research.research_to_idea',{item:i.id,approach:a})}>{a[0].toUpperCase()+a.slice(1)}</button>):<button disabled={!!busy} title="An idea in Forge › Ideas to explore" onClick={()=>act('i'+i.id,'engine.research.research_to_idea',{item:i.id})}>→ Idea</button>}
   <button disabled={!!busy} title="One Claude call: what it is and what it could mean for you" onClick={()=>ask('',i.id)}>Ask Claude</button>
   {i.state==='starred'?<button className="linkish" disabled={!!busy} onClick={()=>act('s'+i.id,'engine.research.mark_finding',{item:i.id,state:'new'})}>Unsave</button>:<button className="linkish" disabled={!!busy} onClick={()=>act('s'+i.id,'engine.research.mark_finding',{item:i.id,state:'starred'})}>☆ Save</button>}
   <button className="linkish" disabled={!!busy} onClick={()=>act('h'+i.id,'engine.research.mark_finding',{item:i.id,state:'dismissed'})}>Hide</button></div></li>;
 return <section className="panel page research-page">{head}
  <div className="note-dest" role="tablist" aria-label="Research">{([['feed',`Feed (${r.feed.length-saved.length})`],['saved',`Saved (${saved.length})`],['deep',`Deep research (${r.notes.length})`],['interests','Interests'],['sources','Sources & limits']] as const).map(([k,l])=><button key={k} role="tab" aria-selected={tab===k} className={'chip'+(tab===k?' on':'')} onClick={()=>{setTab(k);setMsg('')}}>{l}</button>)}</div>
  {msg&&<p className="save-status" role="status">{msg}</p>}
  {tab==='feed'&&<><div className="note-dest inline r-kinds">{([['all','All'],['development','Developments'],['tool','Tools']] as const).map(([k,l])=><button key={k} className={'chip small'+(kind===k?' on':'')} onClick={()=>setKind(k)}>{l}</button>)}<small className="muted">Last scan {r.last_scan?new Date(r.last_scan).toLocaleString('en-GB',{dateStyle:'short',timeStyle:'short'}):'never'}</small></div>
   {feed.length?<ul className="engine-list">{feed.map(item)}</ul>:<p className="empty">Nothing new that touches Atlas. {r.last_scan?'':'No scan has run yet: start one under Sources & limits.'}</p>}</>}
  {tab==='saved'&&(saved.length?<ul className="engine-list">{saved.map(item)}</ul>:<p className="empty">Nothing saved. ☆ Save keeps a finding here until you act on it.</p>)}
  {tab==='deep'&&<><div className="r-ask"><textarea value={q} maxLength={500} placeholder="Ask one question, e.g. could a local model sort my notes as well as Claude?" aria-label="Research question" onChange={e=>setQ(e.target.value)}/><button disabled={q.trim().length<8||!!busy} onClick={()=>ask(q)}>{busy==='ask'?'Researching…':'Ask Claude'}</button></div><p className="muted">One Claude call per question, with matching findings, your own code and your goals; the answer is kept here.</p>
   <ul className="engine-list">{r.notes.map(n=><li key={n.id} className="engine-item r-note"><div className="ei-head"><strong>{n.question}</strong><small className="ei-key">#{n.id} · {n.status==='done'?'answered':n.status}</small></div>
    {n.answer.summary&&<p>{n.answer.summary}</p>}{n.answer.matters&&<p><b>Why it matters</b> {n.answer.matters}</p>}{n.answer.risks&&<p><b>Risks</b> {n.answer.risks}</p>}
    {n.answer.next_steps&&n.answer.next_steps.length>0&&<><h6>Next steps</h6><ul>{n.answer.next_steps.map((x,k)=><li key={k}>{x}</li>)}</ul></>}
    {n.answer.sources&&n.answer.sources.length>0&&<p className="r-src">{n.answer.sources.map(u=><a key={u} href={u} target="_blank" rel="noreferrer noopener">{(()=>{try{return new URL(u).hostname}catch{return u}})()}</a>)}</p>}
    <div className="ei-actions">{n.idea_id?<small className="muted">Became idea #{n.idea_id}</small>:<button disabled={!!busy||n.status!=='done'} onClick={()=>act('n'+n.id,'engine.research.research_to_idea',{note:n.id})}>→ Idea{n.answer.idea?`: ${n.answer.idea}`:''}</button>}</div></li>)}</ul></>}
  {tab==='interests'&&r.review&&<div className="r-sources">
   <p className="muted">{r.review.ready?r.review.note:`Not enough to judge yet (${r.review.findings} findings). Save or hide findings in the feed; this review fills in as you do.`}</p>
   <ul className="engine-list">{r.review.interests.map(w=><li key={w.word} className="engine-item"><div className="ei-head"><strong>{w.word}</strong><span className="chip small">{w.verdict}{w.boosted?' · boosted':''}{w.own?' · yours':''}</span><small className="ei-key">{w.hits} hits · {w.useful} useful · {w.noise} noise</small></div>
    <div className="ei-actions">{w.verdict==='muted'?<button disabled={!!busy} onClick={()=>act('t'+w.word,'engine.research.tune_interests',{mute:r.review!.tuning.mute.filter(x=>x!==w.word)})}>Unmute</button>:<button disabled={!!busy} onClick={()=>act('t'+w.word,'engine.research.tune_interests',{mute:[...r.review!.tuning.mute,w.word]})}>Mute</button>}
     {w.verdict!=='muted'&&(w.boosted?<button className="linkish" disabled={!!busy} onClick={()=>act('b'+w.word,'engine.research.tune_interests',{boost:r.review!.tuning.boost.filter(x=>x!==w.word)})}>Unboost</button>:<button className="linkish" disabled={!!busy} onClick={()=>act('b'+w.word,'engine.research.tune_interests',{boost:[...r.review!.tuning.boost,w.word]})}>Boost</button>)}</div></li>)}</ul>
   <div className="r-ask"><input value={newWord} maxLength={30} placeholder="Add an interest word" aria-label="New interest" onChange={e=>setNewWord(e.target.value)}/><button disabled={newWord.trim().length<2||!!busy} onClick={()=>{act('add','engine.research.tune_interests',{add:[...r.review!.tuning.add,newWord.trim()]});setNewWord('')}}>Add</button></div>
   <small className="muted">Changes apply to the next scan; muted words stop counting, boosted words count double.</small>
  </div>}
  {tab==='sources'&&conf&&<div className="r-sources">
   <h5>Sources</h5><div className="r-checks">{Object.entries(r.all_sources).map(([k,l])=><label key={k}><input type="checkbox" checked={conf.sources.includes(k)} onChange={e=>setConf({...conf,sources:e.target.checked?[...conf.sources,k]:conf.sources.filter(x=>x!==k)})}/> {l}</label>)}</div>
   <div className="r-nums"><label>Scan every <input type="number" min={1} max={168} value={conf.every_hours} onChange={e=>setConf({...conf,every_hours:Number(e.target.value)})}/> hours</label><label>Up to <input type="number" min={3} max={50} value={conf.per_source} onChange={e=>setConf({...conf,per_source:Number(e.target.value)})}/> items per source</label><label>Keep <input type="number" min={1} max={365} value={conf.keep_days} onChange={e=>setConf({...conf,keep_days:Number(e.target.value)})}/> days</label></div>
   <h5>Followed repositories <small>releases of these appear in the feed; one owner/name per line</small></h5><textarea value={repos} onChange={e=>setRepos(e.target.value)} aria-label="Followed repositories"/>
   <div className="ei-actions"><button disabled={!!busy} onClick={()=>act('conf','engine.research.research_sources',{settings:{...conf,repos:repos.split(/\s+/).filter(Boolean)}})}>Save</button>{hubHas(hub,'engine.research.radar_scan')&&<button disabled={!!busy} title="Fetch every chosen source now; it also runs by itself" onClick={()=>act('scan','engine.research.radar_scan',{})}>Scan now</button>}<small className="muted">Feeds are read as data and ranked by rule; no model calls while scanning.</small></div>
  </div>}
 </section>;
}
type Res={id:number;kind:string;name:string;details:string;value:number|null;unit:string;status:string;updated:string};
type ResData={kinds:string[];groups:{kind:string;items:Res[]}[];connected?:(Res&{source:string;detected:{role?:string;gpus?:{name:string;memory_gb:number|null;cuda:boolean}[]};builder?:Device|null})[];count:number};
// #193: the machine's builder role on its Connected card (one device, one card)
function builderRole(d:Device){const slots=`${d.busy}/${d.slots} slot${d.slots>1?'s':''} in use`;return [d.role==='hub'?'hub':'worker',d.status==='pending'?`waits for your approval #${d.approval_id}`:d.status!=='active'?'access revoked':d.role==='worker'?(d.online?'approved · online':'approved · offline'):'',d.status==='active'?slots:'',d.building.length?'building '+d.building.map(b=>'#'+b.task_id).join(', '):''].filter(Boolean).join(' · ')}
type Arsenal={projects:{id:number;name:string;description:string;scanned:string|null;assets:number}[];hits:{project:string;name:string;kind:string;signature:string;doc:string;path:string;line:number}[];query:string};
const KIND_TEXT:Record<string,string>={money:'Money',hardware:'Hardware',account:'Accounts',time:'Time',skill:'Skills',contact:'Contacts',service:'Services',other:'Other'};
function ResourceForm({r,kinds,onSave,onCancel,busy}:{r?:Res;kinds:string[];onSave:(f:object)=>void;onCancel:()=>void;busy:boolean}){
 const [f,setF]=useState({kind:r?.kind||'hardware',name:r?.name||'',details:r?.details||'',value:r?.value==null?'':String(r.value),unit:r?.unit||''});
 return <div className="res-form"><select value={f.kind} aria-label="Kind" onChange={e=>setF({...f,kind:e.target.value})}>{kinds.map(k=><option key={k} value={k}>{KIND_TEXT[k]||k}</option>)}</select><input placeholder="Name, e.g. GPU PC" aria-label="Name" value={f.name} maxLength={120} onChange={e=>setF({...f,name:e.target.value})}/><input placeholder="Details" aria-label="Details" value={f.details} maxLength={500} onChange={e=>setF({...f,details:e.target.value})}/><input className="num" placeholder="Amount" aria-label="Amount" value={f.value} onChange={e=>setF({...f,value:e.target.value})}/><input className="num" placeholder="Unit" aria-label="Unit" value={f.unit} maxLength={20} onChange={e=>setF({...f,unit:e.target.value})}/><button disabled={busy||!f.name.trim()} onClick={()=>onSave({...f,value:f.value.trim()===''?null:f.value})}>{r?'Save':'Add'}</button><button className="linkish" onClick={onCancel}>Cancel</button></div>;
}
function ResourcesPage({hub}:{hub:Hub|null}){
 const [d,setD]=useState<ResData|null>(null),[a,setA]=useState<Arsenal|null>(null),[q,setQ]=useState(''),[editing,setEditing]=useState<number|'new'|null>(null);
 const {toast,run:run0,undo}=useUndoable(),[busy,setBusy]=useState(false),[err,setErr]=useState('');
 const run=async(action:string,body:object,after?:()=>void)=>{setBusy(true);setErr('');try{await run0(action,body,after);return true}catch(e){setErr((e as Error).message);return false}finally{setBusy(false)}};
 const ok=hubHas(hub,'engine.assets.resources'),okA=hubHas(hub,'engine.assets.arsenal_find');
 const load=async()=>{try{const x=await fetch('/api/engine/engine.assets.resources');if(x.ok)setD((await x.json()).result)}catch{}};
 const find=async(text:string)=>{try{const x=await fetch('/api/engine/engine.assets.arsenal_find?q='+encodeURIComponent(text));if(x.ok)setA((await x.json()).result)}catch{}};
 useEffect(()=>{if(ok)load();if(okA)find('')},[ok,okA]);
 useEffect(()=>{if(!okA)return;const t=setTimeout(()=>find(q),300);return()=>clearTimeout(t)},[q]);
 const save=async(body:object)=>{if(await run('engine.assets.edit_resource',body,load))setEditing(null)};
 const head=<div className="view-title"><h2>Resources</h2><p>What you have to work with, and everything you have already built. The Engine reads both when it plans, and the builder reuses your code before writing new code.</p></div>;
 if(!hub?.reachable)return <section className="panel page">{head}<p className="build-idle">Resources come from the Engine; they appear when the hub is online.</p></section>;
 if(!ok)return <section className="panel page">{head}<Outdated what="Resources"/></section>;
 return <section className="panel page resources-page">{head}
  {err&&<p className="save-status" role="alert">{err}</p>}
  {toast&&<div className="toast" role="status">{toast.msg.split('\n')[0]}{toast.changes.length>0&&<button onClick={()=>undo(load)}>Undo</button>}</div>}
  <div className="dir-head"><h4 className="local-title">Connected <small>machines that report themselves: the Pi every hour, this PC when Atlas starts and daily</small></h4><button className="small-btn" disabled={busy} title="Read this computer's CPU, memory, GPUs and disk now and update its resource" onClick={async()=>{setBusy(true);setErr('');try{const r=await fetch('/api/hardware/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const b=await r.json();if(!r.ok)throw Error(b.error);setErr('');await load()}catch(e){setErr((e as Error).message)}finally{setBusy(false)}}}>Detect this PC now</button></div>
  {d&&(d.connected||[]).length>0?<div className="res-groups">{(d.connected||[]).map(c=><div className={'res-group connected'+(c.builder?.busy?' building':'')} key={c.id}><h5>{c.builder?.display||(c.detected?.role==='pi'?'The Pi':'PC')} <span className="chip small on">live</span></h5><b>{c.name}</b><small>{c.details}</small><small className="res-role">{c.builder?<>Builder: {builderRole(c.builder)}</>:'Not a builder for Atlas (docs/WORKERS.md adds one)'}</small><small className="muted">updated {c.updated?new Date(c.updated).toLocaleString('en-GB',{dateStyle:'short',timeStyle:'short'}):'—'}</small></div>)}</div>:<p className="empty">No machine has reported yet. The Pi reports at the next hourly cycle; press "Detect this PC now" on your PC.</p>}
  <div className="dir-head"><h4 className="local-title">Written by you <small>context the machines cannot report: money, hardware, accounts, time, skills, contacts, services · never passwords or numbers</small></h4>{editing!=='new'&&<button className="small-btn" onClick={()=>setEditing('new')}>+ Add</button>}</div>
  {editing==='new'&&d&&<ResourceForm kinds={d.kinds} busy={busy} onSave={f=>save({fields:f})} onCancel={()=>setEditing(null)}/>}
  {!d?<p className="build-idle">Loading…</p>:d.count===0?<p className="empty">Nothing listed yet. Add what you can draw on: a GPU PC, hours a week, a budget, an account.</p>:<div className="res-groups">{d.groups.map(g=><div className="res-group" key={g.kind}><h5>{KIND_TEXT[g.kind]||g.kind}</h5><ul>{g.items.map(r=>editing===r.id?<li key={r.id}><ResourceForm r={r} kinds={d.kinds} busy={busy} onSave={f=>save({id:r.id,fields:f})} onCancel={()=>setEditing(null)}/></li>:<li key={r.id} className="res-item"><div><b>{r.name}</b>{r.value!=null&&<span className="res-val">{r.value} {r.unit}</span>}{r.details&&<small>{r.details}</small>}</div><span className="ei-actions"><button className="linkish" onClick={()=>setEditing(r.id)}>Edit</button><button className="linkish" disabled={busy} onClick={()=>run('engine.assets.edit_resource',{retire:r.id},load)}>Retire</button></span></li>)}</ul></div>)}</div>}
  <div className="dir-head"><h4 className="local-title">What you have built <small>the arsenal: functions and modules in your project folders</small></h4>{hubHas(hub,'engine.assets.arsenal_scan')&&<button className="small-btn" disabled={busy} title="Read your project folders again" onClick={()=>run('engine.assets.arsenal_scan',{},async()=>{setTimeout(()=>find(q),20000)})}>Index again</button>}</div>
  {!okA?<Outdated what="The arsenal"/>:!a?<p className="build-idle">Loading…</p>:<>
   <label className="search inline-search"><span>⌕</span><input placeholder="Find code you already wrote, e.g. budget, telegram, parse pdf…" aria-label="Search the arsenal" value={q} onChange={e=>setQ(e.target.value)}/>{q&&<button aria-label="Clear search" onClick={()=>setQ('')}>×</button>}</label>
   {q.trim()?(a.hits.length?<ul className="engine-list">{a.hits.map((h,i)=><li key={i} className="engine-item"><div className="ei-head"><span className="chip small">{h.project}</span><strong><code>{h.signature||h.name}</code></strong><small className="ei-key">{h.path}:{h.line}</small></div>{h.doc&&<p className="ei-why">{h.doc}</p>}</li>)}</ul>:<p className="empty">Nothing in your projects matches “{q}”.</p>)
   :a.projects.length?<ul className="res-projects">{a.projects.map(p=><li key={p.id}><b>{p.name}</b><span className="res-val">{p.assets} parts</span>{p.description&&<small>{p.description}</small>}<small className="muted">{p.scanned?`indexed ${new Date(p.scanned).toLocaleDateString('en-GB')}`:'not indexed yet'}</small></li>)}</ul>:<p className="empty">No project folders yet. The builder registers new ones; on the Pi: <code>./hq.sh arsenal add &lt;folder&gt;</code>.</p>}
  </>}
 </section>;
}
type Mem={id:number;kind:string;content:string;tags:string;source:string;created:string};
type Change={id:number;ts:string;action:string;table:string;row:number;text:string;reason:string;undone:boolean;thought:number|null};
const MEM_TEXT:Record<string,string>={lesson:'Lessons',decision:'Decisions',preference:'Preferences',fact:'Facts',info:'Research notices'};
function MemoryPage({hub}:{hub:Hub|null}){
 const [tab,setTab]=useState<'memory'|'changes'>('memory'),[kind,setKind]=useState(''),[q,setQ]=useState(''),[m,setM]=useState<{kinds:string[];counts:Record<string,number>;items:Mem[]}|null>(null),[log,setLog]=useState<Change[]|null>(null);
 const [nk,setNk]=useState('lesson'),[nt,setNt]=useState(''),[busy,setBusy]=useState(false),[msg,setMsg]=useState('');
 const ok=hubHas(hub,'engine.memory.memories'),okLog=hubHas(hub,'engine.memory.change_log');
 const load=async()=>{try{const x=await fetch('/api/engine/engine.memory.memories?kind='+kind+'&q='+encodeURIComponent(q));if(x.ok)setM((await x.json()).result)}catch{}};
 const loadLog=async()=>{try{const x=await fetch('/api/engine/engine.memory.change_log?n=80');if(x.ok)setLog((await x.json()).result)}catch{}};
 useEffect(()=>{if(!ok)return;const t=setTimeout(load,q?300:0);return()=>clearTimeout(t)},[ok,kind,q]);
 useEffect(()=>{if(okLog&&tab==='changes')loadLog()},[okLog,tab]);
 const act=async(action:string,body:object,after:()=>void)=>{setBusy(true);setMsg('');try{setMsg(await post(action,body));after()}catch(e){setMsg((e as Error).message)}finally{setBusy(false)}};
 const head=<div className="view-title"><h2>Memory</h2><p>What Atlas has learned and decided, and every change it made to your plan, with who made it and why.</p></div>;
 if(!hub?.reachable)return <section className="panel page">{head}<p className="build-idle">Memory comes from the Engine; it appears when the hub is online.</p></section>;
 if(!ok)return <section className="panel page">{head}<Outdated what="Memory"/></section>;
 const total=m?Object.entries(m.counts).filter(([k])=>k!=='info').reduce((a,[,v])=>a+v,0):0;
 return <section className="panel page memory-page">{head}
  <div className="note-dest" role="tablist" aria-label="Memory">{([['memory','What Atlas remembers'],['changes','What changed']] as const).map(([k,l])=><button key={k} role="tab" aria-selected={tab===k} className={'chip'+(tab===k?' on':'')} onClick={()=>{setTab(k);setMsg('')}}>{l}</button>)}</div>
  {msg&&<p className="save-status" role="status">{msg}</p>}
  {tab==='memory'&&<>
   <div className="mem-add"><select value={nk} aria-label="Kind" onChange={e=>setNk(e.target.value)}>{['lesson','decision','preference','fact'].map(k=><option key={k} value={k}>{k[0].toUpperCase()+k.slice(1)}</option>)}</select><input value={nt} maxLength={2000} placeholder="Something Atlas should remember, e.g. always test on the Pi's Python first" aria-label="What to remember" onChange={e=>setNt(e.target.value)}/><button disabled={busy||nt.trim().length<4} onClick={()=>act('engine.memory.record_lesson',{kind:nk,text:nt},()=>{setNt('');load()})}>Remember</button></div>
   <div className="mem-filter"><span className="note-dest inline"><button className={'chip small'+(kind===''?' on':'')} onClick={()=>setKind('')}>All ({total})</button>{(m?.kinds||[]).map(k=><button key={k} className={'chip small'+(kind===k?' on':'')} onClick={()=>setKind(k)}>{MEM_TEXT[k]||k} ({m?.counts[k]||0})</button>)}</span><label className="search inline-search"><span>⌕</span><input placeholder="Search memory…" aria-label="Search memory" value={q} onChange={e=>setQ(e.target.value)}/>{q&&<button aria-label="Clear search" onClick={()=>setQ('')}>×</button>}</label></div>
   {!m?<p className="build-idle">Loading…</p>:m.items.length?<ul className="mem-list">{m.items.map(x=><li key={x.id}><span className={'chip small k-'+x.kind}>{x.kind}</span><p>{x.content}</p><small>{x.created?new Date(x.created).toLocaleDateString('en-GB'):''}{x.source&&x.source!=='me'?` · from ${x.source}`:''}{x.tags?` · ${x.tags}`:''}</small></li>)}</ul>:<p className="empty">{q?`Nothing remembered about “${q}”.`:'Nothing here yet.'}</p>}
  </>}
  {tab==='changes'&&(!okLog?<Outdated what="The change log"/>:!log?<p className="build-idle">Loading…</p>:<ul className="mem-list changes">{log.map((c,i)=><li key={c.id} className={c.undone?'undone':''}><span className={'chip small '+(c.action==='create'?'k-decision':'k-fact')}>{c.action==='create'?'added':'changed'}</span><p>{c.text}</p><small>{new Date(c.ts).toLocaleString('en-GB',{dateStyle:'short',timeStyle:'short'})} · {c.reason}{c.thought?` · from thought #${c.thought}`:''}</small>{c.undone?<small className="muted">undone</small>:<button className="linkish" disabled={busy} title="Reverse this change (a later change to the same field must be undone first)" onClick={()=>act('engine.brain.undo_changes',{changes:[c.id]},loadLog)}>Undo</button>}{!c.undone&&c.thought&&log.findIndex(x=>!x.undone&&x.thought===c.thought)===i&&<button className="linkish" disabled={busy} title="Reverse every open change this thought made (the thought itself is kept)" onClick={()=>act('engine.brain.undo_changes',{thought:c.thought},loadLog)}>Undo all from thought #{c.thought}</button>}</li>)}</ul>)}
 </section>;
}
type Upkeep={label:string;when:string;last:string|null;failed:boolean;detail:string};
type SysData={killed:boolean;budgets:{job:string;used:number;limit:number;deferred:number}[];routing:Record<string,string[]|string>;phase:number|null;repairs:{enabled:boolean;status:string;checked:string;incidents:{id:number;source:string;error_type:string;status:string;count:number;last_seen:string}[]};upkeep:Upkeep[];voice:{tiers:Record<string,{configured:boolean;url?:string;model:string;label:string;key_set?:boolean;install?:string;check?:{ok:boolean;message:string;at:string}|null}>;order:string[];active:boolean};claude:{state:string;message:string};constitution:string};
type UsageWin={runs?:number;calls?:number;tokens_in:number;tokens_out:number;cost_usd:number};
const fmtK=(n:number)=>n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n||0);
const ago=(ts:string|null)=>{if(!ts)return 'never';const h=(Date.now()-new Date(ts).getTime())/36e5;return h<1?`${Math.max(1,Math.round(h*60))} min ago`:h<48?`${Math.round(h)} h ago`:`${Math.round(h/24)} days ago`};
function ClaudeSignIn({hub}:{hub:Hub|null}){
 const [st,setSt]=useState<{state:string;message:string;url:string;session:string}|null>(null),[code,setCode]=useState(''),[err,setErr]=useState(''),[busy,setBusy]=useState(false);
 const step=async(body:object)=>{setBusy(true);setErr('');try{const r=await fetch('/api/engine/engine.build.claude_login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');setSt(b.message)}catch(e){setErr((e as Error).message)}finally{setBusy(false)}};
 useEffect(()=>{if(!st||!['starting','verifying'].includes(st.state))return;const t=setTimeout(()=>step({step:'status'}),1500);return()=>clearTimeout(t)},[st]);
 if(!hubHas(hub,'engine.build.claude_login'))return <Outdated what="Claude sign-in"/>;
 return <div className="sys-card"><h5>Claude <small>the builder's subscription on the Pi</small></h5>
  <p className={'sys-state s-'+(st?.state||'unchecked')}>{st?.message||'Not checked yet.'}</p>
  {st?.state==='waiting'&&st.url&&<div className="claude-step"><a className="button" href={st.url} target="_blank" rel="noreferrer noopener">Open Claude sign-in</a><input value={code} placeholder="Paste the code Claude shows" aria-label="Sign-in code" autoComplete="off" onChange={e=>setCode(e.target.value)}/><button disabled={busy||!code.trim()} onClick={async()=>{await step({step:'submit',session:st.session,code});setCode('')}}>Send code</button></div>}
  <div className="ei-actions"><button disabled={busy} onClick={()=>step({step:'check'})}>Check</button>{st&&['starting','waiting','verifying'].includes(st.state)?<button className="linkish" disabled={busy} onClick={()=>step({step:'cancel'})}>Cancel</button>:<button disabled={busy} title="Starts Claude's own sign-in on the Pi; the code goes straight to it and is never stored" onClick={()=>step({step:'start'})}>Reconnect</button>}</div>
  {err&&<p className="save-status" role="alert">{err}</p>}</div>;
}
type EngineLink={state:string;message:string;readOnly:boolean;dataAgeMinutes:number|null;dataStale:boolean;capturedAt:string|null;snapshotId:string|null;sourceCommit:string|null;counts:Record<string,number>;attempts:{at:string;outcome:string;message:string}[]};
const LINK_LABEL:Record<string,string>={connected:'Up to date',stale:'Stale',offline:'Hub offline',unauthorized:'Token refused',schema_mismatch:'Version mismatch',not_configured:'No hub',never:'Not read yet',error:'Refused'};
function EngineReadModel(){
 const [l,setL]=useState<EngineLink|null>(null),[busy,setBusy]=useState(false);
 const load=async()=>{try{const r=await fetch('/api/hq/connection');if(r.ok)setL(await r.json())}catch{}};
 const sync=async()=>{setBusy(true);try{const r=await fetch('/api/hq/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.ok)setL(await r.json())}catch{}finally{setBusy(false)}};
 useEffect(()=>{load()},[]);
 if(!l)return null;
 const good=l.state==='connected';
 return <div className="sys-card engine-link"><h5>Engine read model <small>a read-only copy of the Engine's plan on this computer; reading never changes the Engine</small></h5>
  <p><span className={'chip small'+(good?' on':'')}>{LINK_LABEL[l.state]||l.state}</span> {l.message}</p>
  {l.snapshotId&&<p className="muted">Read {l.dataAgeMinutes===null?'at an unknown time':l.dataAgeMinutes<1?'just now':`${l.dataAgeMinutes} min ago`}{l.dataStale&&!good?' (stale)':''} · Engine {l.sourceCommit?.slice(0,7)||'unknown'} · {Object.entries(l.counts).map(([k,v])=>`${v} ${k}`).join(', ')}</p>}
  <div className="ei-actions"><button disabled={busy} title="One read of the Engine's snapshot export through the hub" onClick={sync}>{busy?'Reading…':'Read now'}</button></div>
 </div>;
}
type SyncNote={id:string;body:string;revision:number|null;deleted:boolean;state:'synced'|'pending'|'conflict'};
type SyncConflict={commandId:string;noteId:string;reason:string;mine:{op:string;body:string|null};theirs:{body:string;revision:number;deleted:boolean}|null};
type SyncView={configured:boolean;pending:number;sent:number;conflicts:number;acknowledged:number;stale:boolean;minutesSinceSync:number|null;lastError:string|null;notes:SyncNote[];conflictDetails:SyncConflict[]};
function NotesSync(){
 const [v,setV]=useState<SyncView|null>(null),[text,setText]=useState(''),[busy,setBusy]=useState(false),[msg,setMsg]=useState('');
 const call=async(path:string,body:object={})=>{setBusy(true);setMsg('');try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'Refused');setV(b);return true}catch(e){setMsg((e as Error).message);return false}finally{setBusy(false)}};
 useEffect(()=>{fetch('/api/sync').then(r=>r.ok?r.json():null).then(b=>b&&setV(b)).catch(()=>{})},[]);
 if(!v)return null;
 const open=v.pending+v.sent;
 const state=v.conflicts?'Conflict':open?'Waiting to send':v.stale?'Stale':'Up to date';
 return <div className="sys-card notes-sync"><h5>Synced notes <small>the Pi holds the one authoritative copy; offline edits wait in this computer's outbox and never overwrite a newer version</small></h5>
  <p><span className={'chip small'+(state==='Up to date'?' on':'')}>{state}</span> {!v.configured?'No hub connected: edits stay in the outbox until one is.':v.minutesSinceSync===null?'Not synced yet.':`Last sync ${v.minutesSinceSync<1?'just now':`${v.minutesSinceSync} min ago`}.`} {open>0&&`${open} change${open>1?'s':''} not confirmed by the Pi yet.`}</p>
  {v.lastError&&<small className="warn">{v.lastError}</small>}
  {v.conflictDetails.map(c=><div key={c.commandId} className="sync-conflict"><b>Two versions of one note</b> <small className="muted">{c.reason}</small>
   <div className="sync-versions"><div><small>This computer</small><p>{c.mine.op==='delete'?'(deleted here)':c.mine.body}</p></div><div><small>On the Pi{c.theirs?` · revision ${c.theirs.revision}`:''}</small><p>{!c.theirs?'(not on the Pi)':c.theirs.deleted?'(deleted there)':c.theirs.body}</p></div></div>
   <div className="ei-actions"><button disabled={busy} onClick={()=>call(`/api/sync/conflicts/${c.commandId}/mine`)}>Keep this computer's</button><button disabled={busy} onClick={()=>call(`/api/sync/conflicts/${c.commandId}/theirs`)}>Keep the Pi's</button></div></div>)}
  <ul className="sync-notes">{v.notes.slice(0,20).map(n=><li key={n.id}><span>{n.body}</span> <small className={n.state==='conflict'?'warn':'muted'}>{n.state==='synced'?`revision ${n.revision}`:n.state}</small> <button className="linkish" disabled={busy} title="Deleted notes are kept as a tombstone on the Pi" onClick={()=>call(`/api/sync/notes/${n.id}/delete`)}>Delete</button></li>)}</ul>
  <div className="ei-actions"><input value={text} maxLength={20000} placeholder="A note to keep on every device" onChange={e=>setText(e.target.value)}/><button disabled={busy||!text.trim()} onClick={async()=>{if(await call('/api/sync/notes',{body:text}))setText('')}}>Add</button><button disabled={busy} title="Send the outbox to the Pi, then read what changed there" onClick={()=>call('/api/sync/run')}>{busy?'Syncing…':'Sync now'}</button></div>
  {msg&&<p className="save-status" role="status">{msg}</p>}
 </div>;
}
type LocalVoice={available:boolean;why:string;installed:boolean;device:string;model:string;settings:{model:string;language:string;prompt:string};models:string[];install:{state:string;message:string;stage?:string;percent?:number;packages?:number;downloaded_mb?:number;expected_mb?:number;seconds?:number;gpu?:boolean};machine:string;hardware?:{nvidia:string[];gpus:string[]};gpu_recommended?:boolean;gpu_libraries?:boolean;gpu_libraries_unused?:boolean};
function VoiceSettings(){
 const [v,setV]=useState<LocalVoice|null>(null),[pick,setPick]=useState<Hearing|null>(()=>{try{return localStorage.getItem('atlas-hearing') as Hearing|null}catch{return null}}),[gpu,setGpu]=useState<boolean|null>(null),[msg,setMsg]=useState(''),[form,setForm]=useState<{model:string;language:string;prompt:string}|null>(null);
 const load=async()=>{try{const r=await fetch('/api/voice/local');if(r.ok){const x=await r.json();setV(x);setForm(f=>f||x.settings)}}catch{}};
 useEffect(()=>{load()},[]);
 useEffect(()=>{if(v?.install?.state!=='installing')return;const t=setTimeout(load,2000);return()=>clearTimeout(t)},[v]);
 const gpuOn=gpu??!!v?.gpu_recommended;  // from the hardware scan: recommended only with an NVIDIA GPU
 const postLocal=async(path:string,body:object)=>{setMsg('');try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'Refused');setV(x=>({...(x as LocalVoice),...b,install:b.state?b:(b.install||x?.install)}));window.dispatchEvent(new Event('atlas-voice'));await load();return true}catch(e){setMsg((e as Error).message);return false}};
 const choose=(h:Hearing)=>{saveHearing(h);setPick(h)};
 const browser=!!((window as any).SpeechRecognition||(window as any).webkitSpeechRecognition);
 const rows:[Hearing,string,boolean,string][]=[
  ['pc','Whisper on this PC',!!v?.available,v?.available?`Runs on this computer's ${v.device==='cuda'?'GPU':'CPU'} (${v.model}); nothing leaves the PC and recordings are not kept.`:(v?.why||'Only when Atlas runs on your PC.')],
  ['whisper','Whisper server (Engine)',true,'The Whisper server or hosted API below, through the Engine. Used on the phone; the Pi never transcribes on its own CPU.'],
  ['browser','Browser dictation',browser,browser?'Instant and without setup. Chrome and Edge send the audio to their own speech service (Google, Microsoft).':'This browser has no speech recognition; use Chrome or Edge.']];
 return <><div className="sys-card voice-choice"><h5>How Atlas hears you <small>on this device; 🎙 uses the first that is available unless you choose</small></h5>
  <ul className="voice-tiers">{rows.map(([k,label,ok,why])=><li key={k}><label className={ok?'':'muted'}><input type="radio" name="hearing" checked={(pick||'')===k} disabled={!ok} onChange={()=>choose(k)}/> <b>{label}</b></label><small>{why}</small></li>)}</ul>
  <small className="muted">The Claude app's own dictation belongs to the Claude apps and cannot be called by other apps. Browser dictation is the equivalent without a key; Whisper on this PC is the private one.</small></div>
 <div className="sys-card"><h5>Whisper on this PC <small>{v?v.machine:'…'} · faster-whisper, on an NVIDIA GPU when one is usable</small></h5>
  {!v?<p className="build-idle">Checking this computer…</p>:v.why&&!v.why.startsWith('Whisper is not installed')?<p className="sys-state s-disconnected">{v.why}</p>:<>
   <p className={'sys-state s-'+(v.available?'connected':'disconnected')}>{v.available?`Ready: ${v.model} on the ${v.device==='cuda'?'GPU':'CPU'}`:'Not installed on this computer yet.'}</p>
   {!v.installed&&v.install?.state!=='installing'&&<div className="ei-actions"><button className="approve" title="Installs the faster-whisper package from PyPI for your Windows user (pip --user). Nothing else changes." onClick={()=>postLocal('/api/voice/local/install',{gpu:gpuOn})}>Install Whisper on this PC</button><label className="merge-toggle voice-card-toggle" title="cuBLAS and cuDNN, about 1 GB; only useful with an NVIDIA graphics card"><input type="checkbox" checked={gpuOn} onChange={e=>setGpu(e.target.checked)}/> with NVIDIA GPU support <small>{v.gpu_recommended?`recommended: ${v.hardware?.nvidia.join(', ')} found`:`not needed: no NVIDIA GPU found${v.hardware?.gpus.length?' ('+v.hardware.gpus.join(', ')+')':''}`}</small></label></div>}
   {v.install?.state==='installing'&&<div className="install-progress"><div className="tl-track"><i className="tl-done" style={{width:Math.max(3,v.install.percent||0)+'%'}}/></div><small>{v.install.stage||'Working…'} {v.install.percent!=null?`about ${v.install.percent}%`:''}{v.install.expected_mb?` · ${v.install.downloaded_mb||0} of ~${v.install.expected_mb} MB downloaded`:''}{v.install.packages?` · ${v.install.packages} packages`:''}{v.install.seconds!=null?` · ${Math.floor((v.install.seconds||0)/60)}:${String((v.install.seconds||0)%60).padStart(2,'0')}`:''}</small></div>}
   {v.install?.message&&<small className={v.install.state==='failed'||v.install.state==='interrupted'?'warn':'muted'}>{v.install.message}</small>}
   {v.gpu_libraries_unused&&v.install?.state!=='installing'&&<div className="ei-actions"><small className="warn">The NVIDIA libraries are installed, but this PC has no NVIDIA GPU to use them (about 1 GB).</small><button onClick={()=>postLocal('/api/voice/local/remove-gpu',{})}>Remove them</button></div>}
   {v.installed&&form&&<div className="voice-form">
    <label>Model <select value={form.model} onChange={e=>setForm({...form,model:e.target.value})}><option value="">Best for this PC ({v.device==='cuda'?'large-v3-turbo':'small'})</option>{v.models.map(m=><option key={m} value={m}>{m}</option>)}</select></label>
    <label>Language <select value={form.language} onChange={e=>setForm({...form,language:e.target.value})}><option value="">Detect</option><option value="nl">Dutch</option><option value="en">English</option><option value="fr">French</option></select></label>
    <label className="wide">Words to spell right <input value={form.prompt} maxLength={300} onChange={e=>setForm({...form,prompt:e.target.value})}/></label>
    <div className="ei-actions"><button onClick={async()=>{if(await postLocal('/api/voice/local/settings',form))setMsg('Saved; the next recording uses it (a new model downloads once).')}}>Save</button></div></div>}
  </>}
  {msg&&<p className="save-status" role="status">{msg}</p>}</div></>;
}
function SystemsPage({hub,onHub}:{hub:Hub|null;onHub:()=>void}){
 const [d,setD]=useState<SysData|null>(null),[u,setU]=useState<{agent:Record<string,UsageWin>;hq:Record<string,UsageWin>;by_job:{job:string;tier:string;calls:number;tokens_in:number;tokens_out:number;cost_usd:number}[]}|null>(null);
 const [tab,setTab]=useState<'overview'|'settings'|'agents'|'connections'|'voice'|'upkeep'|'models'|'constitution'>('overview'),[arm,setArm]=useState(false),[busy,setBusy]=useState(false),[msg,setMsg]=useState(''),[gpu,setGpu]=useState<string|null>(null);
 const ok=hubHas(hub,'engine.system.health');
 const load=async()=>{try{const [a,b]=await Promise.all(['engine.system.health','engine.system.usage'].map(f=>fetch('/api/engine/'+f).then(r=>r.ok?r.json():null)));if(a)setD(a.result);if(b)setU(b.result)}catch{}};
 useEffect(()=>{if(ok)load()},[ok]);
 const act=async(action:string,body:object)=>{setBusy(true);setMsg('');try{setMsg(await post(action,body));await load()}catch(e){setMsg((e as Error).message)}finally{setBusy(false);setArm(false)}};
 const head=<div className="view-title"><h2>Systems</h2><p>How Atlas runs: the stop switch, what it uses and may spend, how it reaches your programs, and how it keeps itself in shape.</p></div>;
 const tabs=<div className="note-dest" role="tablist" aria-label="Systems">{([['overview','Overview'],['settings','Settings'],['agents','Agents'],['connections','Connections'],['voice','Voice'],['upkeep','Self-maintenance'],['models','Models'],['constitution','Constitution']] as const).map(([k,l])=><button key={k} role="tab" aria-selected={tab===k} className={'chip'+(tab===k?' on':'')} onClick={()=>{setTab(k);setMsg('')}}>{l}</button>)}</div>;
 if(!hub?.reachable||!ok)return <section className="panel page">{head}<HubCard hub={hub} onChange={onHub}/><SettingsTab/><EngineReadModel/><NotesSync/>{hub?.reachable&&<Outdated what="Systems"/>}</section>;
 if(!d)return <section className="panel page">{head}<p className="build-idle">Loading…</p></section>;
 const win=(w:UsageWin|undefined,agent:boolean)=>w?<>{agent?`${w.runs||0} runs`:`${w.calls||0} calls`} · {fmtK(w.tokens_in+w.tokens_out)} tokens{w.cost_usd?` · $${w.cost_usd}`:''}</>:'—';
 return <section className="panel page systems-page">{head}
  <div className={'kill-card'+(d.killed?' on':'')}><div><b>{d.killed?'Everything is stopped':'Atlas is running'}</b><small>{d.killed?'The builder is stopped, no new runs start and paid model calls are blocked; local models still work.':'Stop the builder and every paid model call at once. Resume any time.'}</small></div>
   {d.killed?<button className="approve" disabled={busy} onClick={()=>act('engine.system.kill_switch',{on:false})}>Resume</button>:arm?<span className="ei-actions"><button className="deny" disabled={busy} onClick={()=>act('engine.system.kill_switch',{on:true})}>Yes, stop everything</button><button className="linkish" onClick={()=>setArm(false)}>Cancel</button></span>:<button className="deny" disabled={busy||!hubHas(hub,'engine.system.kill_switch')} onClick={()=>setArm(true)}>Stop everything</button>}</div>
  {tabs}
  {msg&&<p className="save-status" role="status">{msg.split('\n')[0]}</p>}
  {tab==='settings'&&<SettingsTab/>}
  {tab==='agents'&&<AgentsTab hub={hub}/>}
  {tab==='overview'&&<div className="sys-grid">
   <div className="sys-card"><h5>Use <small>estimated; on a subscription it measures capacity, not a bill</small></h5>{u?<table className="sys-table"><thead><tr><th></th><th>Builder</th><th>Atlas's own calls</th></tr></thead><tbody>{(['today','week','month'] as const).map(k=><tr key={k}><td>{k==='week'?'7 days':k==='month'?'30 days':'Today'}</td><td>{win(u.agent[k],true)}</td><td>{win(u.hq[k],false)}</td></tr>)}</tbody></table>:<p className="build-idle">…</p>}</div>
   <div className="sys-card"><h5>Daily budgets <small>paid calls per automatic job; over budget it falls back to a rule or a local model. What you start is never capped.</small></h5><table className="sys-table"><tbody>{d.budgets.map(b=><tr key={b.job} className={b.used>=b.limit&&b.limit>0?'full':''}><td>{b.job}</td><td><span className="bar"><i style={{width:`${b.limit?Math.min(100,100*b.used/b.limit):0}%`}}/></span></td><td>{b.used} / {b.limit}</td><td>{b.deferred?<small className="warn">kept off paid models {b.deferred}×</small>:null}</td></tr>)}</tbody></table><small className="muted">Limits live in the constitution's [budgets]; Atlas can only propose changes.</small></div>
   {u&&u.by_job.length>0&&<div className="sys-card"><h5>By job, last 30 days</h5><table className="sys-table"><tbody>{u.by_job.slice(0,12).map((j,i)=><tr key={i}><td>{j.job}</td><td>{j.tier}</td><td>{j.calls} calls</td><td>{fmtK(j.tokens_in+j.tokens_out)}</td><td>{j.cost_usd?`$${j.cost_usd}`:''}</td></tr>)}</tbody></table></div>}
  </div>}
  {tab==='connections'&&<div className="sys-grid">
   <HubCard hub={hub} onChange={onHub}/>
   <EngineReadModel/>
   <NotesSync/>
   <ClaudeSignIn hub={hub}/>
   <div className="sys-card"><h5>Still to come</h5><ul className="later-conns"><li><b>Your other apps</b><span>Finance and the apps you will dissect (RA-023)</span></li></ul></div>
  </div>}
  {tab==='voice'&&<div className="sys-grid"><VoiceSettings/>
   <div className="sys-card"><h5>Whisper server and hosted API <small>used by the Engine on the Pi: Telegram voice notes, and dictation on the phone</small></h5><ul className="voice-tiers">{d.voice.order.length>0&&null}{Object.entries(d.voice.tiers).map(([k,t])=><li key={k}><b>{t.label}</b> <span className={'chip small'+(t.configured?' on':'')}>{t.configured?'set up':'not set up'}</span>{t.check&&<small className={t.check.ok?'':'warn'}>{t.check.message}</small>}
    <span className="ei-actions">{t.configured&&hubHas(hub,'engine.system.voice_setup')&&<button className="small-btn" disabled={busy} onClick={()=>act('engine.system.voice_setup',{test:k})}>Test</button>}</span>
    {k==='gpu'&&<span className="voice-url"><input placeholder="http://gpu-pc:9000" aria-label="Whisper server address" value={gpu??t.url??''} onChange={e=>setGpu(e.target.value)}/><button className="small-btn" disabled={busy||gpu===null} onClick={()=>act('engine.system.voice_setup',{tier:'gpu',url:gpu||''})}>Save</button></span>}
    {k==='cloud'&&!t.configured&&<small>Its key stays out of Atlas: set HQ_STT_CLOUD_URL and HQ_STT_CLOUD_KEY in the Pi's .env.</small>}</li>)}</ul></div>
  </div>}
  {tab==='upkeep'&&<div className="sys-grid">
   <div className="sys-card"><h5>What Atlas does by itself</h5><table className="sys-table upkeep"><tbody>{d.upkeep.map(x=><tr key={x.label} className={x.failed?'failed':''}><td><b>{x.label}</b><small>{x.when}</small></td><td>{ago(x.last)}{x.failed&&<small className="warn"> · failed</small>}</td><td><small>{x.detail}</small></td></tr>)}</tbody></table></div>
   <div className="sys-card"><h5>Automatic repairs <small>crashes are grouped; small, tested fixes to the Engine install by themselves, anything bigger waits for your review</small></h5><p className={'sys-state s-'+(d.repairs.enabled?'connected':'disconnected')}>{d.repairs.enabled?'On':'Off'} · {d.repairs.status}{d.repairs.checked?` · checked ${ago(d.repairs.checked)}`:''}</p>
    {hubHas(hub,'engine.system.repairs')&&<button disabled={busy} onClick={()=>act('engine.system.repairs',{enabled:!d.repairs.enabled})}>{d.repairs.enabled?'Turn off':'Turn on'}</button>}
    {d.repairs.incidents.length>0&&<table className="sys-table"><tbody>{d.repairs.incidents.map(i=><tr key={i.id}><td>{i.error_type}</td><td>{i.source}</td><td>{i.count}×</td><td>{i.status}</td><td>{ago(i.last_seen)}</td></tr>)}</tbody></table>}</div>
  </div>}
  {tab==='models'&&<div className="sys-card"><h5>Which model does what <small>tried in this order; bulk work goes local first, reasoning to the cloud first</small></h5><table className="sys-table"><tbody>{Object.entries(d.routing).map(([job,tiers])=><tr key={job}><td>{job}</td><td>{(Array.isArray(tiers)?tiers:[tiers]).join(' → ')}</td></tr>)}</tbody></table><small className="muted">Set in the constitution's [models.routing].</small></div>}
  {tab==='constitution'&&<div className="sys-card"><h5>Constitution <small>read-only: Atlas reads its limits from here and may only propose changes, which you approve and make yourself{d.phase!=null?` · phase ${d.phase}`:''}</small></h5><pre className="constitution">{d.constitution||'Not readable.'}</pre></div>}
 </section>;
}
const USE_NAMES:Record<string,string>={'hq-dashboard':'Atlas dashboard',telegram:'Telegram',cli:'Command line',automatic:'Runs by itself',atlas:'Atlas'};
function Inspector({picked,data,onPick,onFocus}:{picked:Picked;data:GraphData|null;onPick:(p:Picked)=>void;onFocus:(id:string)=>void}){
 if(!data)return null;
 const area=(id:string)=>data.domains.find(a=>a.id===id);
 const fnById=(id:string)=>data.functions.find(f=>f.id===id);
 if(picked.kind==='function'){const f=picked.item;const usedBy=data.functions.filter(x=>x.requires.includes(f.id));
  return <section className="panel inspector"><h2>◇ Selected function</h2><h3><span className="orange">◇</span>{f.label}</h3><div className="tags"><span>{f.programName} › {area(f.domain)?.name}</span><span className={'st st-'+f.status}>{f.status}</span><span>{f.effect}</span></div><p>{f.summary}</p>{picked.outside&&<p className="outside-note">Outside this focus: a function here needs it.</p>}
  {f.requires.length>0&&<><h4>Needs</h4>{f.requires.map(r=>{const x=fnById(r);return x&&<button className="connection-item" key={r} onClick={()=>onPick({kind:'function',item:x,outside:false})}>● <span>{x.label} · {x.programName}</span></button>})}</>}
  {usedBy.length>0&&<><h4>Needed by</h4>{usedBy.map(x=><button className="connection-item" key={x.id} onClick={()=>onPick({kind:'function',item:x,outside:false})}>● <span>{x.label} · {x.programName}</span></button>)}</>}
  {f.note&&<p className="fn-note">{f.note}</p>}
  {f.use.length>0&&<><h4>Use it today</h4><div className="tags">{f.use.map(u=><span key={u}>{USE_NAMES[u]||u}</span>)}</div></>}
  {f.atlasSection&&<p className="fn-home">In Atlas: <b>{f.atlasSection}</b>{f.reachable?'':' · reachable after the hub connection (RA-030)'}</p>}
  <h4>In focuses</h4><div className="tags">{f.focuses.length?f.focuses.map(id=><button className="chip small" key={id} onClick={()=>onFocus(id)}>{data.focuses.find(x=>x.id===id)?.name}</button>):<span>None yet</span>}</div>
  <small className="provenance">{f.evidence?`Evidence: ${f.evidence}`:'No evidence yet: not built'}{f.intent?` · intent ${f.intent}`:''}</small></section>}
 const item=picked.item;const fns=data.functions.filter(f=>picked.kind==='program'?f.program===item.id:f.domain===item.id);
 const live=fns.filter(f=>f.status==='live').length;const onScreen=fns.filter(f=>f.reachable).length;
 return <section className="panel inspector"><h2>◇ Selected {picked.kind==='program'?'program':'area'}</h2><h3><span className="orange">{picked.kind==='program'?'⬢':'◇'}</span>{item.name}</h3><div className="tags"><span>{fns.length} functions</span><span>{live} live</span><span>{onScreen} on Atlas's screen</span>{picked.kind==='program'&&(item as any).level&&<span>Level {(item as any).level}</span>}</div><p>{item.summary}</p>{fns.length>0&&<><h4>Functions</h4>{fns.slice(0,12).map(x=><button className="connection-item" key={x.id} onClick={()=>onPick({kind:'function',item:x,outside:false})}>● <span>{x.label} · {x.status}</span></button>)}</>}</section>;
}
type Hub={configured:boolean;reachable:boolean;url?:string;latencyMs?:number;commit?:string;error?:string;functions?:string[];actions?:string[]};
const hubHas=(hub:Hub|null,id:string)=>!!hub?.reachable&&!!(hub.functions?.includes(id)||hub.actions?.includes(id));
function Outdated({what}:{what:string}){return <p className="outdated" role="note">{what} needs a newer Engine on the Pi. Run <code>./hq.sh update</code> there, or wait for the automatic update (about 15 minutes).</p>}
class Safe extends React.Component<{name:string;children:React.ReactNode},{error:string}>{
 state={error:''};
 static getDerivedStateFromError(e:Error){return {error:String(e?.message||e)}}
 componentDidCatch(e:Error){console.error(this.props.name,e)}
 render(){return this.state.error?<section className="panel page"><h2>{this.props.name}</h2><p className="outdated" role="alert">This section hit an error and was stopped so the rest of Atlas keeps working: {this.state.error}</p><button onClick={()=>this.setState({error:''})}>Try again</button></section>:this.props.children}
}
type Need={key:string;kind:'decision'|'approval'|'task'|'already_done';url?:string;id:number;title:string;why:string;options:string[];holds:{id:number;title:string}[];description?:string;category?:string};
type Step={id:number;title:string;why:string;command:string;cwd:string;shell:string;claude:string};
type Stopped={id:number;tool:string;objective:string;stopped_at:string;resets:string;files:string[];commits:string[];last_said:string;resume:string;context:string};
type Attention={needs:Need[];steps:Step[];stopped:Stopped[]};
function useEngineAttention(hub:Hub|null){
 const [data,setData]=useState<Attention|null>(null),[hidden,setHidden]=useState<Set<string>>(new Set()),[note,setNote]=useState('');
 const load=async()=>{try{const [n,t,i]=await Promise.all(['engine.attention.needs','engine.attention.terminal_steps','engine.attention.interrupted_work'].map(async f=>{const r=await fetch('/api/engine/'+f);if(!r.ok)throw Error();return (await r.json()).result}));setData({needs:n,steps:t,stopped:i})}catch{}};
 useEffect(()=>{if(!hub?.reachable){setData(null);return}load();const t=setInterval(load,20000);return()=>clearInterval(t)},[hub?.reachable]);
 async function act(key:string,action:string,body:object){
  setHidden(h=>new Set(h).add(key));setNote('');
  try{const r=await fetch('/api/engine/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');setNote(b.message||'Done.');await load()}
  catch(e){setNote(String((e as Error).message));}
  finally{setHidden(h=>{const n=new Set(h);n.delete(key);return n})}
 }
 const visible=data&&{needs:data.needs.filter(n=>!hidden.has('n'+n.key)),steps:data.steps.filter(s=>!hidden.has('s'+s.id)),stopped:data.stopped.filter(s=>!hidden.has('i'+s.id))};
 const count=visible?visible.needs.length+visible.steps.length+visible.stopped.length:0;
 return {data:visible,count,act,note};
}
function Copy({text,label}:{text:string;label:string}){const [done,setDone]=useState(false);return <button className="small-btn" onClick={async()=>{try{await navigator.clipboard.writeText(text);setDone(true);setTimeout(()=>setDone(false),1500)}catch{}}}>{done?'Copied':label}</button>}
function DoOverChanges({id}:{id:number}){
 const [d,setD]=useState<{counts:Record<string,number>;groups:Record<string,{title:string;reason:string}[]>}|null>(null);
 useEffect(()=>{fetch('/api/engine/engine.build.do_over_changes?id='+id).then(r=>r.ok?r.json():null).then(b=>b&&setD(b.result)).catch(()=>{})},[id]);
 if(!d)return <p className="ei-why">Loading…</p>;
 const label:Record<string,string>={change:'Changed',done:'Closed as already built',drop:'Dropped',merge:'Merged',new:'New',keep:'Kept',goal_change:'Goals reworded',goal_place:'Goals placed under a vision',goal_drop:'Goals dropped',goal_merge:'Goals merged',idea_change:'Ideas reworded',idea_drop:'Ideas set aside'};
 return <div className="doover">{(['goal_place','goal_change','goal_merge','goal_drop','change','done','drop','merge','new','idea_change','idea_drop'] as const).filter(k=>d.groups[k]?.length).map(k=><div key={k}><h6>{label[k]} ({d.groups[k].length})</h6><ul>{d.groups[k].map((x,i)=><li key={i}><b>{x.title}</b> <small>{x.reason}</small></li>)}</ul></div>)}{d.counts.keep>0&&<small className="ei-why">{d.counts.keep} task(s) stay as they are.</small>}</div>;
}
function NeedItem({n,act,first=false}:{n:Need;act:(key:string,action:string,body:object)=>void;first?:boolean}){
 const cx=useCortex();
 const [text,setText]=useState(''),[more,setMore]=useState(false);
 const kb=(_:number)=>null;  // 1 · 2 · 3 on Today removed (owner, 2026-10-25): too easy to approve something by accident
 const doover=n.kind==='approval'&&n.title.startsWith('Plan re-evaluation');
 const title=n.title.replace(/\s*Details: hq \S+ show \d+\.?$/,'');
 const send=(body:object)=>act('n'+n.key,'engine.attention.answer_needs',{key:n.key,...body});
 return <li className={'engine-item '+n.kind}><div className="ei-head"><span className="chip">{n.kind==='decision'?'Decision':n.kind==='approval'?'Approval':n.kind==='already_done'?'Already done?':'Your task'}</span><strong>{title}</strong><small className="ei-key">{n.key}</small></div>
  {n.why&&<p className={doover?'doover-why':'ei-why'}>{n.why}</p>}{doover&&<><button className="linkish" onClick={()=>setMore(!more)}>{more?'Hide the changes':'Show the changes'}</button>{more&&<DoOverChanges id={n.id}/>}</>}{n.description&&<p className="ei-why">{n.description}</p>}
  {n.holds.length>0&&<p className="ei-holds">{n.kind==='task'?'Paused':'Holds up'}: {n.holds.map(h=>`#${h.id} ${h.title}`).join(', ')}</p>}
  {n.kind==='decision'&&<div className="ei-actions">{n.options.map((o,i)=><button key={o} onClick={()=>send({text:o})}>{kb(i)}{o}</button>)}<input placeholder="Or your own answer" value={text} onChange={e=>setText(e.target.value)}/><button disabled={!text.trim()} onClick={()=>send({text})}>Answer</button></div>}
  {n.kind==='approval'&&<div className="ei-actions"><button className="approve" onClick={()=>send({verdict:'approved',text})}>{kb(0)}Approve</button><button className="deny" onClick={()=>send({verdict:'denied',text})}>{kb(1)}Deny</button><input placeholder="Note (optional)" value={text} onChange={e=>setText(e.target.value)}/></div>}
  {n.kind==='already_done'&&<div className="ei-actions"><button className="approve" title="The task is finished: it closes, undoable" onClick={()=>send({text:'done'})}>{kb(0)}Yes, done</button><button onClick={()=>send({text:'partial'})}>{kb(1)}Partly</button><button className="deny" onClick={()=>send({text:'not_related'})}>{kb(2)}Not related</button>{n.url&&<a className="linkish" href={n.url} target="_blank" rel="noreferrer noopener">Open the PR</a>}</div>}
  {n.kind==='task'&&<div className="ei-actions"><input placeholder="What you did, or how to go on" value={text} onChange={e=>setText(e.target.value)}/><button disabled={!text.trim()} onClick={()=>send({text})}>Send to {cx}</button></div>}
 </li>;
}
function EngineAttention({att}:{att:ReturnType<typeof useEngineAttention>}){
 const {data,act,note}=att;
 if(!data)return null;
 const empty=!data.needs.length&&!data.steps.length&&!data.stopped.length;
 return <section className="engine-attention"><h4>From the Engine <small>live through the hub</small></h4>
  {note&&<p className="save-status" role="status">{note}</p>}
  {empty&&<p className="build-idle">Nothing from the Engine needs you.</p>}
  {data.stopped.length>0&&<><h5>Stopped by a usage limit</h5><ul>{data.stopped.map(i=><li className="engine-item stopped" key={'i'+i.id}><div className="ei-head"><span className="chip">{i.tool}</span><strong>{i.objective||'Unfinished session'}</strong></div><p className="ei-why">Stopped {i.stopped_at?new Date(i.stopped_at).toLocaleString('en-GB',{timeZone:'Europe/Paris'}):''}{i.resets?` · limit resets ${i.resets}`:''}{i.files?.length?` · ${i.files.length} files touched`:''}</p>
   {i.tool==='devloop'&&<p className="ei-why">First in line: the builder continues this task from its checkpoint by itself once the limit has reset.</p>}
   <div className="ei-actions">{i.resume&&<Copy text={i.resume} label="Copy resume command"/>}<Copy text={i.context} label="Copy context for Claude or Codex"/><button onClick={()=>act('i'+i.id,'engine.attention.finish_interrupted',{id:i.id,status:'resolved'})}>Finished</button><button onClick={()=>act('i'+i.id,'engine.attention.finish_interrupted',{id:i.id,status:'dismissed'})}>Not needed</button></div></li>)}</ul></>}
  {data.steps.length>0&&<><h5>Run in your terminal</h5><ul>{data.steps.map(c=><li className="engine-item step" key={'s'+c.id}><div className="ei-head"><strong>{c.title}</strong></div>{c.why&&<p className="ei-why">{c.why}</p>}<pre className="ei-cmd">{c.shell||c.command}</pre>
   <div className="ei-actions"><Copy text={c.shell||c.command} label="Copy command"/><Copy text={c.claude} label="Copy as a Claude prompt"/><button onClick={()=>act('s'+c.id,'engine.attention.finish_terminal_step',{id:c.id,status:'done'})}>Done</button><button onClick={()=>act('s'+c.id,'engine.attention.finish_terminal_step',{id:c.id,status:'dismissed'})}>Not needed</button></div></li>)}</ul></>}
  {data.needs.length>0&&<><h5>Needs your input</h5><ul>{data.needs.map((n,i)=><NeedItem key={n.key} n={n} act={act} first={i===0}/>)}</ul></>}
 </section>;
}
type Thought={id:number;text:string;source:string;status:string;result:string;created:string;changes:{id:number;action:string;table_name:string;row_id:number;undone:number;title:string}[]};
const KIND:Record<string,string>={tasks:'Task',ideas:'Idea',goals:'Goal',capabilities:'Skill'};
const THOUGHT_STATE:Record<string,string>={sorted:'Sorted',unsorted:'Waiting for a model',sorting:'Being sorted',new:'New'};
function EngineBrain({thoughts,onUndo}:{thoughts:Thought[]|null;onUndo?:(id:number)=>void}){
 const cx=useCortex();
 if(!thoughts)return null;
 return <section className="engine-attention brain"><h4>{cx} <small>your recent thoughts and what they became</small></h4>
  {!thoughts.length?<p className="build-idle">No thoughts yet. Tell one to {cx}, or press ✦ at the top.</p>:<ul>{thoughts.map(t=>{const open=t.changes.filter(c=>!c.undone);return <li className="engine-item" key={t.id}>
   <div className="ei-head"><span className={'chip state-'+t.status}>{THOUGHT_STATE[t.status]||t.status}</span><small className="ei-key">#{t.id} · {t.source} · {t.created?new Date(t.created).toLocaleString('en-GB',{timeZone:'Europe/Paris'}):''}</small></div>
   <p className="thought-text">{t.text}</p>{t.result&&<p className="ei-why">{t.result}</p>}
   {t.changes.length>0&&<ul className="became">{t.changes.map(c=><li key={c.id} className={c.undone?'undone':''}><span className="chip">{KIND[c.table_name]||c.table_name}</span>{c.action==='create'?'':'updated '}{c.title||`#${c.row_id}`}{c.undone?' · undone':''}</li>)}</ul>}
   {open.length>0&&onUndo&&<div className="ei-actions"><button onClick={()=>onUndo(t.id)}>Undo what this thought added</button></div>}
  </li>})}</ul>}
 </section>;
}
type Step2={id:number;title:string;owner:string;status:string;goal:string;why:string;effort_h:number;inFocus?:boolean};
type EFocus={focus:string;text:string;options:Record<string,string>;next:Step2|null;destination:{id:number;title:string;horizon:string}|null};
type Zoom={insights:{heading:string[];steer:string[];yourself:string[];think:string[]};goals:{id:number;title:string;horizon:string;done:number;total:number}[]};
type Objective={id:number;title:string;why:string;horizon:string;due?:string|null;done:number;total:number;open:number;progress:number;timeUsed:number;behind:number;daysSinceMoved:number|null;weekly:number[];dueSoon:number};
type Vision={id:number;title:string;why:string;created:string;objectives:Objective[];done:number;total:number;open:number;progress:number;daysSinceMoved:number|null;weekly:number[];dueSoon:number;unlocks:number;score:number;reasons:string[]};
type Directions={visions:Vision[];loose:{id:number;title:string;horizon:string}[];current:number|null;recommended:{id:number;title:string;reasons:string[]}|null};
const post=async(action:string,body:object)=>{const r=await fetch('/api/engine/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');return b.message as string};
function useDirection(hub:Hub|null,full:boolean){
 const [focus,setFocus]=useState<EFocus|null>(null),[steps,setSteps]=useState<Step2[]|null>(null),[zoom,setZoom]=useState<Zoom|null>(null),[dirs,setDirs]=useState<Directions|null>(null),[note,setNote]=useState(''),[modeNote,setModeNote]=useState('');
 const get=async(f:string)=>{const r=await fetch('/api/engine/'+f);if(!r.ok)throw Error();return (await r.json()).result};
 const load=async()=>{try{
  if(hubHas(hub,'engine.direction.directions'))setDirs(await get('engine.direction.directions'));
  setFocus(await get('engine.direction.current_focus'));
  if(full){setSteps(await get('engine.direction.ranked_next_steps?n=25'));if(hubHas(hub,'engine.direction.zoom_out'))setZoom(await get('engine.direction.zoom_out'))}}catch{}};
 useEffect(()=>{if(!hub?.reachable){setFocus(null);setSteps(null);setZoom(null);setDirs(null);return}load();const t=setInterval(load,60000);return()=>clearInterval(t)},[hub?.reachable,hub?.functions?.length,full]);
 async function run(action:string,body:object,optimistic?:()=>void,say=setNote){const old=dirs,oldF=focus;optimistic?.();say('');try{say(await post(action,body));await load()}catch(e){setDirs(old);setFocus(oldF);say('Not changed: '+(e as Error).message)}}
 const chooseMode=(k:string)=>run('engine.direction.set_focus',{focus:k},()=>setFocus(f=>f&&{...f,focus:k,text:f.options?.[k]||f.text}),setModeNote);
 const choose=(id:number|null)=>run('engine.direction.choose_direction',{goal:id},()=>setDirs(d=>d&&{...d,current:id}));
 const current=dirs?.visions?.find(d=>d.id===dirs.current)||null;
 return {focus,steps,zoom,dirs,current,choose,chooseMode,run,note,modeNote,reload:load};
}
type Dir=ReturnType<typeof useDirection>;
const OWNER:Record<string,string>={me:'You',ai:'Agent',both:'You + agent'};
const HORIZON:Record<string,string>={goal:'Goal',year:'This year',life:'Lifetime',quarter:'Quarter',month:'Month',week:'Week'};
function Timeline({d}:{d:{progress:number;timeUsed:number;horizon:string;total:number;due?:string|null}}){
 const behind=!!d.due&&d.total>0&&d.timeUsed-d.progress>0.05;
 return <div className="timeline" title={d.due?`${Math.round(d.progress*100)}% done · ${Math.round(d.timeUsed*100)}% of the time to ${d.due} used`:`${Math.round(d.progress*100)}% done`}>
  <div className="tl-track">{d.due&&<i className="tl-time" style={{width:d.timeUsed*100+'%'}}/>}<i className={'tl-done'+(behind?' behind':'')} style={{width:d.progress*100+'%'}}/></div>
  <div className="tl-legend"><span>{d.total?`${Math.round(d.progress*100)}% done`:'no work yet'}</span>{d.due&&<span>{Math.round(d.timeUsed*100)}% of the time to the deadline used</span>}</div>
 </div>;
}
function Momentum({weekly}:{weekly:number[]}){const m=Math.max(1,...weekly);return <span className="momentum" title="Tasks finished per week, last 8 weeks">{weekly.map((w,i)=><i key={i} style={{height:4+16*w/m+'px'}} className={w?'on':''}/>)}</span>}
function FocusPanel({dir,onOpen}:{dir:Dir;onOpen:()=>void}){
 const {dirs,current}=dir;
 if(!dirs||!dirs.visions)return <section className="panel focus"><div className="panel-heading"><h2><b className="orange">●</b> Focus</h2><button className="small-btn" onClick={onOpen}>Open</button></div><div className="focus-card"><span>◎</span><div><small>Vision</small><strong>Your visions appear here when the Engine is reachable</strong></div></div></section>;
 const rec=dirs.recommended;
 return <section className="panel focus"><div className="panel-heading"><h2><b className="orange">●</b> Focus</h2><button className="small-btn" onClick={onOpen}>Open</button></div>
  <div className="focus-card"><span>◎</span><div><small>Vision in focus</small><strong>{current?current.title:dirs.visions.length?'No focus chosen':'Name your visions'}</strong></div></div>
  {current&&current.objectives.slice(0,3).map(o=><div className="focus-obj" key={o.id}><span>{o.title}</span><Timeline d={o}/></div>)}
  {rec&&rec.id!==dirs.current&&<div className="focus-row"><span>✦</span><div><b>Recommended</b><p>{rec.title}{rec.reasons[0]?` — ${rec.reasons[0]}`:''}</p></div></div>}
 </section>;
}
const SUGGESTED=['Advance the intelligence of Atlas','Own products and income','Financial stability'];
function GoalRow({o,run}:{o:Objective;run:Dir['run']}){
 const [edit,setEdit]=useState(false),[title,setTitle]=useState(o.title);
 return <li><div className="obj-title">{edit?<span className="obj-edit"><input value={title} aria-label="Goal title" onChange={e=>setTitle(e.target.value)}/><button className="small-btn" disabled={!title.trim()} onClick={()=>{run('engine.direction.shape_directions',{rename:{goal:o.id,title}});setEdit(false)}}>Save</button><button className="linkish" onClick={()=>{setTitle(o.title);setEdit(false)}}>Cancel</button></span>:<button className="linkish goal-name" title="Rename" onClick={()=>setEdit(true)}>{o.title}</button>}
  <label className={'due-pick'+(o.due?' set':'')} title="A deadline is the only thing that raises the priority of this goal's tasks">{o.due?'due':'no deadline'}<input type="date" value={o.due||''} aria-label={'Deadline for '+o.title} onChange={e=>run('engine.direction.shape_directions',{due:{goal:o.id,date:e.target.value||null}})}/></label>
  {o.due&&<button className="linkish" title="Remove the deadline" onClick={()=>run('engine.direction.shape_directions',{due:{goal:o.id,date:null}})}>✕</button>}
  <button className="linkish drop-goal" title="Set this goal aside (undoable)" onClick={()=>run('engine.direction.shape_directions',{drop:o.id})}>Set aside</button></div><Timeline d={o}/></li>;
}
function VisionCard({v,current,rec,choose,run}:{v:Vision;current:boolean;rec:boolean;choose:(id:number|null)=>void;run:Dir['run']}){
 const [obj,setObj]=useState(''),[due,setDue]=useState('');
 return <article className={'vision'+(current?' current':'')+(rec?' rec':'')}>
  <div className="vision-head"><div><small>Vision{current?' · in focus':rec?' · recommended':''}</small><h3>{v.title}</h3>{v.why&&<p>{v.why}</p>}</div>
   {current?<button onClick={()=>choose(null)}>Clear focus</button>:<button className="approve" onClick={()=>choose(v.id)}>Focus on this</button>}</div>
  {v.objectives.length?<ul className="objectives">{v.objectives.map(o=><GoalRow key={o.id} o={o} run={run}/>)}</ul>:<p className="build-idle">No goals yet. What should this vision achieve?</p>}
  <div className="vision-foot"><Momentum weekly={v.weekly}/><span>{v.total?`${v.done} of ${v.total} steps done`:'no work under it yet'}</span>{v.reasons.length>0&&<span className="vision-why">{v.reasons.join(' · ')}</span>}</div>
  <div className="ei-actions add-goal"><input placeholder="A goal for this vision, e.g. Atlas finds opportunities itself" value={obj} onChange={e=>setObj(e.target.value)}/><label className="due-pick" title="Optional: only a deadline raises the priority of its tasks">deadline<input type="date" value={due} onChange={e=>setDue(e.target.value)}/></label><button disabled={!obj.trim()} onClick={()=>{run('engine.direction.shape_directions',{objective:{vision:v.id,title:obj,due:due||null}});setObj('');setDue('')}}>Add goal</button></div>
 </article>;
}
function FocusPage({dir,hub}:{dir:Dir;hub:Hub|null}){
 const {dirs,choose,run,note}=dir;
 const [title,setTitle]=useState(''),[why,setWhy]=useState('');
 if(!hub?.reachable)return <section className="panel page"><h2>Focus</h2><p>Your visions live in the Engine on the Pi; they appear here when it is reachable.</p></section>;
 if(!hubHas(hub,'engine.direction.directions'))return <section className="panel page"><h2>Focus</h2><Outdated what="Visions and the recommended focus"/></section>;
 if(!dirs||!dirs.visions)return <section className="panel page"><h2>Focus</h2>{dirs?<Outdated what="Visions and their goals"/>:<p className="build-idle">Loading…</p>}</section>;
 const rec=dirs.recommended;
 const create=(t:string,w='')=>{run('engine.direction.shape_directions',{create:{title:t,why:w}});setTitle('');setWhy('')};
 const missing=SUGGESTED.filter(x=>!dirs.visions.some(d=>d.title.toLowerCase()===x.toLowerCase()));
 return <section className="panel page focus-page"><h2>Focus</h2><p>Your lifetime visions and the goals under them. Choosing a vision puts its work first; a deadline on a goal is the only other thing that raises its tasks. The tasks themselves live in the Forge.</p>
  {note&&<p className="save-status" role="status">{note}</p>}
  {rec&&rec.id!==dirs.current&&<div className="rec-card"><div><small>Recommended focus</small><h3>{rec.title}</h3><ul>{rec.reasons.map((r,i)=><li key={i}>{r}</li>)}</ul></div><button className="approve" onClick={()=>choose(rec.id)}>Focus on this</button></div>}
  {!dirs.visions.length&&<p className="build-idle">No visions yet. Start with one of these, or name your own below.</p>}
  <div className="visions">{[...dirs.visions].sort((a,b)=>(b.id===dirs.current?1:0)-(a.id===dirs.current?1:0)).map(v=><VisionCard key={v.id} v={v} current={v.id===dirs.current} rec={v.id===rec?.id} choose={choose} run={run}/>)}</div>
  {dirs.loose.length>0&&<><h4 className="local-title">Goals under no vision <small>place them so focus and progress can follow them</small></h4><ul className="loose">{dirs.loose.map(g=><li key={g.id}><span>{g.title} {(g as any).due?<small>due {(g as any).due}</small>:null}</span><select defaultValue="" aria-label={'Vision for '+g.title} onChange={e=>e.target.value&&run('engine.direction.shape_directions',{goal:g.id,vision:Number(e.target.value)})}><option value="">Place under…</option>{dirs.visions.map(d=><option key={d.id} value={d.id}>{d.title}</option>)}</select></li>)}</ul></>}
  <h4 className="local-title">New vision <small>big words, a lifetime</small></h4>
  {missing.length>0&&<div className="note-dest">{missing.map(x=><button key={x} className="chip" onClick={()=>create(x)}>＋ {x}</button>)}</div>}
  <div className="ei-actions"><input placeholder="A vision, e.g. Live in good health" value={title} onChange={e=>setTitle(e.target.value)}/><input placeholder="Why it matters (optional)" value={why} onChange={e=>setWhy(e.target.value)}/><button disabled={!title.trim()} onClick={()=>create(title,why)}>Create</button></div>
 </section>;
}
type QItem={id:number;title:string;source:string;why:string;node:string;state:string;note:string;gate:string;effort_h:number;place?:number};
type Lane={worker?:string;device?:string;slot?:number;run:number;task_id:number;title:string;state:string;kind:string;started:string;elapsed:number;progress:number|null;model:string;pr_url:string;conflict:string[];summary:string;doing:string;step?:string;log:string[];in_stack_of:number|null};
type PR={number:number;title:string;url:string;stage:string;label:string;tasks:number[]};
type ProviderReset={waiting:boolean;reported?:boolean;at?:string;retry_at:string;source?:string;observed_at?:string;watch?:{checked_at?:string;interval_seconds?:number}};
function resetTime(value:string){return new Date(value).toLocaleString('en-GB',{timeZone:'Europe/Amsterdam',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})+' (Europe/Amsterdam)'}
// #193: how many devices build now (a device with two slots building counts once)
function buildingOn(s:BuildStatus|null|undefined){return new Set((s?.lanes||[]).filter(l=>l.state==='running').map(l=>l.worker||'pi')).size}
function builderState(s:BuildStatus){const n=buildingOn(s),one=(s.lanes||[]).find(l=>l.state==='running');return n>1?`Building on ${n} devices`:s.current?((s.devices||[]).length>1&&one?.device?`Building on ${one.device}`:'Building'):s.killed?'Stopped by the kill switch':!s.installed?'Claude Code is not installed':!s.enabled?'Builder switched off':s.paused?'Paused':s.provider_reset?.waiting?'Waiting for Claude usage reset':s.limit_reached?'Daily builder budget reached':s.batch>0&&s.open_prs>=s.batch?'Waiting for pull request review':s.next?'Ready for the next run':'Waiting for a ready task'}
type Device={name:string;display:string;device:string;role:'hub'|'worker';status:string;approval_id:number|null;slots:number;ceiling:number;busy:number;building:{run:number;task_id:number;title:string;slot:number}[];last_seen:string;online:boolean;resource_id:number|null;idle?:string};
type BuildStatus={slots?:number;slot_ceiling?:number;devices?:Device[];alongside?:number[];provider_reset?:ProviderReset;limit_reached?:string;enabled:boolean;paused:boolean;installed:boolean;killed:boolean;autonomy:string;autonomy_text:string;limits_text:string;batch:number;batch_choice?:number;batch_ceiling?:number|null;batch_choices:number[];batch_text:string;open_prs:number;why_idle:string;current:{task_id:number;title:string}|null;next:{id:number;title:string;resumes?:boolean}|null;plan:{lane?:QItem[];lane_cap?:number;mine:QItem[];proposed:QItem[];waiting:QItem[];next_id:number|null};lanes:Lane[];conflicts:{id:number;task_id:number;title:string;pr_url:string;conflict:string}[];conflict_scan:{at?:string}|null;delivery:{review:PR[];install:PR[];live?:PR[]};runs:{id:number;task_id:number;title:string;status:string;started:string;worker?:string;device?:string;slot?:number}[]};
const LEVELS:[string,string][]=[['off','Off'],['ask','Ask first'],['limits','Within limits'],['continuous','Continuous']];
function useBuilder(hub:Hub|null,active:boolean,every=15000){
 const [s,setS]=useState<BuildStatus|null>(null),[note,setNote]=useState(''),[busy,setBusy]=useState('');
 const ok=hubHas(hub,'engine.build.run_status');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.build.run_status');if(r.ok)setS((await r.json()).result)}catch{}};
 useEffect(()=>{if(!ok||!active){return}load();const t=setInterval(load,every);return()=>clearInterval(t)},[ok,active]);
 const [undoIds,setUndoIds]=useState<number[]>([]);
 async function act(key:string,action:string,body:object){setBusy(key);setNote('');setUndoIds([]);
  try{const r=await fetch('/api/engine/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');
   setNote(typeof b.message==='string'?b.message:'Done.');setUndoIds(b.changes||[]);await load()}catch(e){setNote('Not done: '+(e as Error).message)}finally{setBusy('')}}
 const undo=async()=>{const c=undoIds;setUndoIds([]);try{setNote(await post('engine.brain.undo_changes',{changes:c}));await load()}catch(e){setNote('Undo failed: '+(e as Error).message)}};
 return {s,ok,note,busy,act,load,undoIds,undo};
}
function Ring({done,total,label}:{done:number;total:number;label:string}){
 const r=46,c=2*Math.PI*r,f=total?done/total:0;
 return <div className="ring"><svg viewBox="0 0 110 110" aria-hidden="true"><circle cx="55" cy="55" r={r} className="ring-bg"/><circle cx="55" cy="55" r={r} className="ring-fg" strokeDasharray={`${c*f} ${c}`} transform="rotate(-90 55 55)"/></svg><div><b>{Math.round(100*f)}%</b><small>{done} of {total}</small></div><span>{label}</span></div>;
}
// Build › Live (owner, 2026-10-25; #179): each step is one plain sentence of what the builder is doing, written by
// the Engine (engine/buildsteps.py) so the screen, the lane summary and `hq dev lanes` say the same; the >_ button
// shows the real command output.
type PlainStep={icon:string;text:string;n:number;end?:boolean};
type PlainLog={steps:PlainStep[];total:number;said:string;now:string};
const RAW_KEY='atlas-console-raw';
function EngineConsole({run,running}:{run:number;running:boolean}){
 const [lines,setLines]=useState<string[]>([]),[plain,setPlain]=useState<PlainLog|null>(null),after=useRef(0),box=useRef<HTMLPreElement>(null);
 const [raw,setRawState]=useState(()=>{try{return localStorage.getItem(RAW_KEY)==='1'}catch{return false}});
 const setRaw=(v:boolean)=>{setRawState(v);try{localStorage.setItem(RAW_KEY,v?'1':'0')}catch{}};
 useEffect(()=>{after.current=0;setLines([]);setPlain(null);let alive=true;
  const load=async()=>{try{const r=await fetch(`/api/engine/engine.build.run_status?run=${run}&after=${after.current}`);if(!r.ok)return;const b=(await r.json()).result;if(!alive)return;
   if(b.plain)setPlain(b.plain);
   if(b.lines?.length){setLines(l=>[...l,...b.lines].slice(-400));after.current=b.total??(after.current+b.lines.length)}}catch{}};
  load();const t=running?setInterval(load,3000):undefined;return()=>{alive=false;if(t)clearInterval(t)}},[run,running]);
 useEffect(()=>{if(box.current)box.current.scrollTop=box.current.scrollHeight},[lines.length,raw]);
 const shown=plain?.steps||[],said=plain?.said||'',first=(plain?.total||0)-shown.length,cur=shown[shown.length-1];
 return <div className="console-wrap">
  <button className={'console-mode'+(raw?' on':'')} aria-pressed={raw} title={raw?'Back to the plain steps':'Show the actual command output'} onClick={()=>setRaw(!raw)}>{raw?'Steps':'>_ Output'}</button>
  {raw?<pre className="console" ref={box}>{lines.length?lines.join('\n'):'Waiting for output…'}</pre>
  :<div className="console steps-view" aria-live="polite">
    {said&&<p className="step-said"><span>{running?'Says':'Last said'}</span>{said}</p>}
    {!shown.length&&<p className="step-line now"><span className="step-icon">▶</span><span className="step-text">{running?'Getting ready to start the build.':'No steps were recorded.'}{running&&<span className="caret"/>}</span></p>}
    {shown.map((x,i)=>{const now=running&&x===cur&&!x.end;return <p key={first+i} className={'step-line'+(now?' now':'')+(i<shown.length-3?' old':'')}>
     <span className="step-icon">{x.icon}</span><span className="step-text">{x.text}{x.n>1&&<small> ×{x.n}</small>}{now&&<span className="caret"/>}</span></p>})}
   </div>}
 </div>;
}
function PathTracks({tasks,paths}:{tasks:Task[];paths:Path[]}){
 const groups=paths.map(p=>({p,items:tasks.filter(t=>t.path===p.id).sort((a,b)=>a.id.localeCompare(b.id))})).filter(g=>g.items.length);
 const open=groups.filter(g=>g.items.some(t=>t.status!=='done')).sort((a,b)=>Number(!b.items.some(t=>t.status==='in_progress'))-Number(!a.items.some(t=>t.status==='in_progress')));
 const finished=groups.filter(g=>g.items.every(t=>t.status==='done'));
 const track=({p,items}:{p:Path;items:Task[]})=>{const done=items.filter(t=>t.status==='done').length;const now=items.find(t=>t.status==='in_progress');
  return <div className="track" key={p.id}><div className="track-head"><b>{p.name}</b><small>{PROGRAM_NAMES[p.program]||p.program}</small><span>{done}/{items.length}</span></div>
   <div className="dots">{items.map(t=><i key={t.id} className={'dot-'+t.status} title={`${t.id} · ${t.title} — ${STATUS_TEXT[t.status]||t.status}`}/>)}</div>
   {now&&<small className="track-now">now: {now.id} · {now.title}</small>}</div>};
 return <div className="tracks">{open.map(track)}{finished.length>0&&<details className="done-list"><summary>Completed paths ({finished.length})</summary>{finished.map(track)}</details>}
  <div className="track-legend"><span><i className="dot-done"/>done</span><span><i className="dot-in_progress"/>building</span><span><i className="dot-review"/>in review</span><span><i className="dot-ready"/>ready</span><span><i className="dot-todo"/>later</span></div></div>;
}
type Builder=ReturnType<typeof useBuilder>;
function DragList<T extends {id:number;state?:string}>({items,render,onOrder,busy,className}:{items:T[];render:(x:T,i:number)=>any;onOrder:(ids:number[])=>void;busy:boolean;className:string}){
 const [drag,setDrag]=useState<number|null>(null),[over,setOver]=useState<number|null>(null);
 const drop=(target:number)=>{if(drag===null||drag===target){setDrag(null);setOver(null);return}
  const ids=items.map(x=>x.id).filter(i=>i!==drag);ids.splice(ids.indexOf(target)+(items.findIndex(x=>x.id===drag)<items.findIndex(x=>x.id===target)?1:0),0,drag);setDrag(null);setOver(null);onOrder(ids)};
 const move=(id:number,by:number)=>{const ids=items.map(x=>x.id),i=ids.indexOf(id),j=i+by;if(j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];onOrder(ids)};
 return <ol className={className+' draglist'}>{items.map((q,i)=><li key={q.id} className={'q-'+(q.state||'')+(drag===q.id?' dragging':'')+(over===q.id&&drag!==q.id?' over':'')} draggable={!busy}
  onDragStart={e=>{setDrag(q.id);e.dataTransfer.effectAllowed='move'}} onDragOver={e=>{e.preventDefault();setOver(q.id)}} onDragLeave={()=>setOver(o=>o===q.id?null:o)} onDrop={e=>{e.preventDefault();drop(q.id)}} onDragEnd={()=>{setDrag(null);setOver(null)}}>
  <span className="grip" aria-hidden="true" title="Drag to reorder">⋮⋮</span>{render(q,i)}
  <span className="move-keys"><button className="linkish" aria-label="Move up" disabled={busy||i===0} onClick={()=>move(q.id,-1)}>↑</button><button className="linkish" aria-label="Move down" disabled={busy||i===items.length-1} onClick={()=>move(q.id,1)}>↓</button></span></li>)}</ol>;
}
// The whole builder queue (#181): Build next on top, then everything else in build order, one numbering; a search box
// filters by title or #id as you type (locally, the same matching as the header search, #99/#100); opened by
// "All N", the deep link ?queue&highlight=N, or Cortex (view "Forge › Build › Queue").
// #72: ready AI tasks with no blockers between them and no files in common, proposed as one batch. Accepting it lets the
// builder's extra slots take exactly these tasks (#185); dismissing it means this set is not offered again. Both undoable.
type ParTask={id:number;title:string;files:string[];state?:string};
type PView={proposal:ParTask[];left_out:{id:number;title:string;why:string}[];in_flight:ParTask[];accepted:{id:number;ids:number[];open:number[];titles:Record<string,string>}|null;same:boolean};
function ParallelCard({refresh}:{refresh:unknown}){
 const [v,setV]=useState<PView|null>(null),[busy,setBusy]=useState(false),[note,setNote]=useState(''),[why,setWhy]=useState(false);
 const load=async()=>{try{const r=await fetch('/api/engine/engine.build.parallel');if(r.ok)setV((await r.json()).result)}catch{}};
 useEffect(()=>{load()},[refresh]);
 const act=async(body:object)=>{setBusy(true);setNote('');try{setNote(await post('engine.build.parallel_batch',body))}catch(e){setNote('Not done: '+(e as Error).message)}finally{setBusy(false);load()}};
 if(!v||(!v.proposal.length&&!v.accepted))return null;
 const ids=v.proposal.map(t=>t.id);
 return <div className="lane-block parallel-block" id="parallel-batch"><div className="dir-head"><h4 className="local-title">Side by side <small>no blockers between them and no files in common, so they can be built at the same time</small></h4></div>
  {v.accepted&&<p className="build-idle">Accepted: {v.accepted.open.map(i=>`#${i}`).join(', ')} may be built side by side once the builder has more than one slot.</p>}
  {v.proposal.length>0&&!v.same&&<><ul className="upnext">{v.proposal.map(t=><li key={t.id}><div><strong>#{t.id} {t.title}</strong><small>changes {t.files.slice(0,4).join(', ')}{t.files.length>4?` +${t.files.length-4}`:''}</small></div></li>)}</ul>
   <div className="ei-actions"><button className="approve" disabled={busy} title="The builder's extra slots may take only these tasks" onClick={()=>act({accept:ids})}>Accept this batch</button><button className="deny" disabled={busy} title="This set is not offered again" onClick={()=>act({dismiss:ids})}>Dismiss</button>
    {(v.left_out.length>0||v.in_flight.length>0)&&<button className="linkish" aria-expanded={why} onClick={()=>setWhy(x=>!x)}>{why?'Hide':'Why not the others'}</button>}</div></>}
  {why&&<ul className="parallel-why">{v.left_out.map(x=><li key={x.id}>#{x.id} {x.title}: {x.why}</li>)}{v.in_flight.map(f=><li key={'f'+f.id}>#{f.id} in flight ({f.state}): {f.files.slice(0,4).join(', ')||'files unknown'}</li>)}</ul>}
  {note&&<p className="save-status" role="status">{note}</p>}
 </div>}
function BuildOrder({s,busy,onOrder,onClose,highlight}:{s:any;busy:boolean;onOrder:(ids:number[])=>void;onClose:()=>void;highlight?:number|null}){
 const lane:QItem[]=s.plan.lane||[];
 const order:QItem[]=[...s.plan.mine,...s.plan.proposed].filter((q:QItem)=>q.state==='ready'||q.state==='needs_ok');
 const mine=new Set(s.plan.mine.map((q:QItem)=>q.id));
 const [q,setQ]=useState(''),box=useRef<HTMLDivElement>(null);
 const qq=q.trim().replace(/^#/,''),hit=(x:QItem)=>!qq||String(x.id)===qq||String(x.id).startsWith(qq)&&/^\d+$/.test(qq)||score(qq,`${x.id} ${x.title}`)>0;
 useEffect(()=>{const k=(e:KeyboardEvent)=>e.key==='Escape'&&onClose();window.addEventListener('keydown',k);return()=>window.removeEventListener('keydown',k)},[]);
 useEffect(()=>{if(!highlight)return;const t=setTimeout(()=>{const el=box.current?.querySelector(`[data-task="${highlight}"]`) as HTMLElement|null;if(el){el.scrollIntoView({block:'center'});el.classList.add('cx-highlight');setTimeout(()=>el.classList.remove('cx-highlight'),6000)}},150);return()=>clearTimeout(t)},[highlight,lane.length,order.length]);
 const row=(x:QItem,n:number,kind:'lane'|'order')=><><span className={'bo-n'+(kind==='lane'?' lane':n<=5?' top':'')}>{n}</span><div className="bo-body" data-task={x.id}><strong>#{x.id} {x.title}</strong><small>{kind==='order'&&(s.alongside||[]).includes(x.id)?'next free slot on any device · ':''}{kind==='lane'?'Build next: you put this here':mine.has(x.id)?'your order':'by the plan’s ranking'}{x.node?` · ${x.node}`:''}{x.state==='needs_ok'?' · waits for your OK':''}{x.why&&kind==='order'&&!mine.has(x.id)?` · ${x.why}`:''}</small></div></>;
 const waiting=(s.plan.waiting as QItem[]).filter(hit),shownLane=lane.filter(hit),shownOrder=order.filter(hit);
 return <div className="modal-back" role="dialog" aria-modal="true" aria-label="Builder queue" onClick={e=>e.target===e.currentTarget&&onClose()}><div className="modal build-order" ref={box}>
  <div className="bo-top"><div className="bo-head"><div><h3>Builder queue</h3><small>{lane.length+order.length} in build order{s.plan.waiting.length?`, ${s.plan.waiting.length} cannot start yet`:''}. Drag to change the order; the top five are what Forge › Build shows.{(s.devices||[]).length>1?' All devices claim from this one shared queue.':''}</small></div><button className="linkish" onClick={onClose} aria-label="Close">✕</button></div>
  <input className="bo-search" autoFocus value={q} placeholder="Filter by title or #id" aria-label="Filter the queue" onChange={e=>setQ(e.target.value)}/></div>
  {shownLane.length>0&&<><h4 className="bo-sub">Build next</h4><ol className="bo-rail lane">{shownLane.map(x=><li key={x.id}>{row(x,lane.indexOf(x)+1,'lane')}</li>)}</ol></>}
  {lane.length>0&&shownOrder.length>0&&<h4 className="bo-sub">Then</h4>}
  {qq?<ol className="bo-rail">{shownOrder.map(x=><li key={x.id}>{row(x,lane.length+order.indexOf(x)+1,'order')}</li>)}</ol>
   :<DragList className="bo-rail" items={order} busy={busy} onOrder={onOrder} render={(x,i)=>row(x,lane.length+i+1,'order')}/>}
  {qq&&!shownLane.length&&!shownOrder.length&&!waiting.length&&<p className="build-idle">Nothing in the queue matches “{q.trim()}”.</p>}
  {waiting.length>0&&<><h4 className="bo-sub">Cannot start yet ({waiting.length})</h4><ul className="bo-waiting">{waiting.map((x:QItem)=><li key={x.id} data-task={x.id}><span className="warn-tri" aria-hidden="true">⚠</span><strong>#{x.id} {x.title}</strong><small>{x.note}</small></li>)}</ul></>}
 </div></div>;
}
let pendingQueue:{highlight:number|null}|null=null;  // set by the deep link or Cortex, read when Forge › Build opens
const openQueue=(highlight:number|null)=>{pendingQueue={highlight};window.dispatchEvent(new CustomEvent('atlas-queue'))};
function ForgeBuild({hub,builder,build,tasks,paths,onPlan,dir}:{hub:Hub|null;builder:Builder;build:Build|null;tasks:Task[];paths:Path[];onPlan:()=>void;dir:Dir}){
 const {s,ok,note,busy,act}=builder;
 const [prompt,setPrompt]=useState(''),[rid,setRid]=useState(()=>crypto.randomUUID());
 const [settingsOpen,setSettingsOpen]=useState(false);const settingsRef=useRef<HTMLDetailsElement>(null);
 const atlas=tasks.filter(t=>(t.program||'atlas')==='atlas'),atlasDone=atlas.filter(t=>t.status==='done').length;
 const agentActive=!!build&&build.state!=='idle';
 const lanes=s?.lanes||[],running=lanes.filter(l=>l.state==='running');
 const [shown,setShown]=useState<5|10>(5),[allOpen,setAllOpen]=useState(false),[hl,setHl]=useState<number|null>(null);
 useEffect(()=>{const f=()=>{if(pendingQueue){setHl(pendingQueue.highlight);setAllOpen(true);pendingQueue=null}};f();window.addEventListener('atlas-queue',f);return()=>window.removeEventListener('atlas-queue',f)},[]);
 const order=s?[...s.plan.mine,...s.plan.proposed].filter(q=>q.state==='ready'||q.state==='needs_ok'):[];
 const next=order.slice(0,shown);
 const reorder=(ids:number[])=>act('order','engine.build.build_order',{order:ids});
 const state=s?builderState(s):'Connecting to the builder';
 const reset=s?.provider_reset;const watcherFresh=!!reset?.watch?.checked_at&&Date.now()-Date.parse(reset.watch.checked_at)<180000;
 const automatic=!!s&&s.enabled&&!s.paused&&!s.killed&&s.autonomy==='continuous'&&watcherFresh;
 const recent=(s?.runs||[]).filter(r=>r.status!=='running').slice(0,5);
 const devices=s?.devices||[],many=devices.length>1,alongside=new Set(s?.alongside||[]);
 const hubDev=devices.find(d=>d.role==='hub');
 const idleDevs=devices.filter(d=>!d.busy&&d.status!=='revoked'&&(running.length>0||d.role!=='hub'));  // #193: nothing runs → the Pi is the big Now lane
 const seen=(t:string)=>t?new Date(t).toLocaleString('en-GB',{dateStyle:'short',timeStyle:'short'}):'';
 const devState=(d:Device)=>d.status==='pending'?`waits for your approval #${d.approval_id}`:d.status!=='active'?'access revoked':!d.online?(d.last_seen?`offline since ${seen(d.last_seen)}`:'not connected yet'):d.busy?`${d.busy}/${d.slots} slot${d.slots>1?'s':''} in use · building ${d.building.map(b=>'#'+b.task_id).join(', ')}`:`online · 0/${d.slots} slot${d.slots>1?'s':''} in use`;
 const prs=s?[...s.conflicts.map(c=>({k:'c'+c.id,t:`#${c.task_id} ${c.title}`,label:'conflicts with main',url:c.pr_url,resolve:c.id})),...s.delivery.review.map(p=>({k:'r'+p.number,t:`#${p.number} ${p.title}`,label:'waiting for your review',url:p.url,resolve:0})),...s.delivery.install.map(p=>({k:'i'+p.number,t:`#${p.number} ${p.title}`,label:'merged · ⬆ Update installs it',url:p.url,resolve:0}))]:[];
 return <div className="forge-build">
  <div className="builder-control" role="status"><div><small>Run builder</small><strong>{state}</strong><span>{s?.autonomy_text}</span></div><button aria-label="Builder settings" title="Builder settings" aria-expanded={settingsOpen} aria-controls="builder-settings" onClick={()=>{setSettingsOpen(!settingsOpen);if(!settingsOpen)setTimeout(()=>settingsRef.current?.scrollIntoView({behavior:'smooth',block:'start'}),0)}}>⚙</button></div>
  {s&&<details className="q-block settings" id="builder-settings" ref={settingsRef} open={settingsOpen} onToggle={e=>setSettingsOpen(e.currentTarget.open)}><summary>Builder settings</summary>
   <div className="builder-bar"><div><small>Builder</small><b className={'bstate '+(s.current?'on':s.paused||s.killed?'off':'')}>{state}</b></div>
    <div className="ei-actions">{s.paused?<button className="approve" disabled={!!busy} onClick={()=>act('pause','engine.build.dev_controls',{pause:false})}>Resume</button>:<button disabled={!!busy} onClick={()=>act('pause','engine.build.dev_controls',{pause:true})}>Pause</button>}<button disabled={!!busy} onClick={()=>act('scan','engine.build.pr_conflicts',{scan:true})}>{busy==='scan'?'Scanning…':'Scan pull requests for conflicts'}</button></div></div>
   <div className="mode"><small>Daily budget</small><div className="note-dest" role="radiogroup" aria-label="Runs a day">{[3,5,10,25,0].map(n=>{const sx:any=s;const mine=sx.limit_choices?.runs??sx.limits?.runs;const cur=mine===n;const cap=sx.ceilings?.runs;return <button key={n} role="radio" aria-checked={cur} className={'chip'+(cur?' on':'')} title={n?`${n} runs a day`:cap?`No daily limit of your own; max_runs_per_day = ${cap} in [devloop] still caps it`:'No daily limit: the builder keeps going'} onClick={()=>act('lim','engine.build.dev_controls',{limit:{kind:'runs',value:n}})}>{n?`${n} runs`:'No limit'}</button>})}<button className={'chip'+((s as any).override_today?' on':'')} title="Ignore the daily budget for the rest of today; pause, kill switch and Claude's usage limit still hold" onClick={()=>act('ovr','engine.build.dev_controls',{override_today:!(s as any).override_today})}>{(s as any).override_today?'Pushed through today ✓':'Push through today'}</button></div><small className="ei-why">{s.limits_text}</small></div>
   <div className="mode"><small>Merging</small><div className="note-dest" role="radiogroup" aria-label="Merging">{([[false,'I merge'],[true,'Builder merges RA steps']] as const).map(([v,l])=><button key={String(v)} role="radio" aria-checked={!!(s as any).auto_merge===v} className={'chip'+(!!(s as any).auto_merge===v?' on':'')} onClick={()=>!!(s as any).auto_merge!==v&&act('merge','engine.build.dev_controls',{auto_merge:v})}>{l}</button>)}</div><small className="ei-why">{(s as any).auto_merge?'The builder merges its own pull request for an RA step once the Pi\u2019s tests pass. Other work waits for you.':'Every pull request waits for you to merge it.'}</small></div>
   <div className="mode"><small>Autonomy</small><div className="note-dest" role="radiogroup" aria-label="Autonomy">{LEVELS.map(([k,l])=><button key={k} role="radio" aria-checked={s.autonomy===k} className={'chip'+(s.autonomy===k?' on':'')} onClick={()=>s.autonomy!==k&&act('aut','engine.build.dev_controls',{autonomy:k})}>{l}</button>)}</div><small className="ei-why">{s.autonomy_text}</small></div>
   {dir.focus?.options&&<div className="mode"><small>Build mode</small><div className="note-dest" role="radiogroup" aria-label="Build mode">{Object.entries(dir.focus.options).map(([k,t])=><button key={k} role="radio" aria-checked={dir.focus!.focus===k} className={'chip'+(dir.focus!.focus===k?' on':'')} title={t} onClick={()=>dir.focus!.focus!==k&&dir.chooseMode(k)}>{t.split(':')[0]}</button>)}</div><small className="ei-why">{dir.modeNote||dir.focus.text}</small></div>}
   {devices.length>0&&<div className="mode devices"><small>Devices</small><ul className="dev-rows">{devices.map(d=><li key={d.name} className={'dev-'+d.role}><b>{d.display}</b><span className="chip small">{d.role==='hub'?'hub':'worker'}</span>{d.status==='active'&&d.ceiling>1&&<span className="note-dest inline" role="radiogroup" aria-label={'Builds at once on '+d.display}>{Array.from({length:d.ceiling},(_,i)=>i+1).map(n=><button key={n} role="radio" aria-checked={d.slots===n} className={'chip small'+(d.slots===n?' on':'')} title={n>1?'Builds at once; beyond the first, only tasks from an accepted Side by side batch':'One build at a time'} disabled={!!busy} onClick={()=>d.slots!==n&&act('slots','engine.build.dev_controls',{slots:n,worker:d.name})}>{n}</button>)}</span>}<small className="muted">{devState(d)}{d.role==='worker'&&d.last_seen&&d.online?` · last heartbeat ${seen(d.last_seen)}`:''}</small></li>)}</ul><small className="ei-why">Slots are builds at once per device, at most {devices[0].ceiling} ({devices[0].ceiling<4?'[devloop] max_slots_per_worker':'the code ceiling'}). Beyond the first slot a device only takes tasks from an accepted Side by side batch. The machines themselves are under Resources › Connected.</small></div>}
   <div className="mode"><small>Review batch</small><div className="note-dest" role="radiogroup" aria-label="Review batch">{s.batch_choices.map(n=>{const mine=s.batch_choice??s.batch;return <button key={n} role="radio" aria-checked={mine===n} className={'chip'+(mine===n?' on':'')} title={n?`Stop when ${n} pull request${n>1?'s':''} wait for review`:'Never stop for review'} onClick={()=>mine!==n&&act('batch','engine.build.dev_controls',{batch:n})}>{n?n:'Unlimited'}</button>})}</div><small className="ei-why">{s.batch_text}</small></div>
   <h5>Quick build <small>describe it; the builder starts at once</small></h5>
   <textarea className="quick" placeholder="What should be built or fixed? This becomes the full build request." value={prompt} onChange={e=>setPrompt(e.target.value)}/>
   <div className="ei-actions"><button className="approve" disabled={!prompt.trim()||!!busy} onClick={async()=>{await act('quick','engine.build.quick_build',{prompt,request_id:rid});setPrompt('');setRid(crypto.randomUUID())}}>{busy==='quick'?'Starting…':'Build this'}</button></div>
  </details>}
  {reset?.waiting&&<div className="builder-wait"><b>{reset.reported&&reset.at?'Claude reported a reset at '+resetTime(reset.at):'Claude did not report an exact reset time.'}</b><p>{automatic?'Automatic retry is armed: the Pi checks every minute and will try after '+resetTime(reset.retry_at)+'.':'Automatic retry is not armed here. Check builder settings and the Pi watcher.'} Existing daily budgets, dependency holds and review limits still apply.</p><small>{reset.source||'Saved usage-limit response'}{reset.observed_at?' · observed '+resetTime(reset.observed_at):''}{!reset.reported?' · retry time is a conservative estimate, not a confirmed reset':''}</small></div>}

  <div className="live-hero"><Ring done={atlasDone} total={atlas.length} label="Atlas's original plan"/>
   <div className="lanes">
    {agentActive&&build&&<article className={'lane agent '+build.state}><div className="lane-head"><span className="lane-who">Agent · Atlas</span><b>{build.taskId} · {build.title}</b></div><div className="tl-track"><i className="tl-done" style={{width:build.progress+'%'}}/></div><p>{build.state==='stalled'?'Paused: the agent stopped reporting (for example a usage limit). Work so far is kept.':build.message}</p><ol className="steps">{build.steps.map((x,i)=><li key={i} className={i+1<build.step?'done':i+1===build.step?'now':''}>{x}</li>)}</ol></article>}
    {running.map(l=><article className={'lane engine running '+((l.worker||'pi')==='pi'?'dev-hub':'dev-worker')} key={l.run}><div className="lane-head"><span className="lane-who">{l.device||(l.worker&&l.worker!=='pi'?l.worker:'Pi')}{(l.slot||1)>1?` · slot ${l.slot}`:''}{l.model?` · ${l.model}`:''}</span><b>#{l.task_id} {l.title}</b></div><div className="tl-track"><i className="tl-done" style={{width:(l.progress||2)+'%'}}/></div><p>{l.step||'Getting ready to start the build.'} <small>{Math.round(l.elapsed/60)} min{l.progress!=null?` · ${l.progress}% of the time limit`:''}</small></p><EngineConsole run={l.run} running={true}/><div className="ei-actions"><button className="deny" disabled={!!busy} onClick={()=>act('stop','engine.build.dev_controls',{stop:true})}>Stop this run</button></div></article>)}
    {!agentActive&&!running.length&&<article className="lane idle"><div className="lane-head"><span className="lane-who">{many&&hubDev?`${hubDev.display} · hub`:'Now'}</span><b>{s?state:'Nothing is building'}</b></div><p>{!hub?.reachable?'The Engine’s builder appears here when the hub is reachable.':!ok?'Needs a newer Engine on the Pi.':s?.paused?'The builder is paused; nothing new starts.':s?.next?`Next: #${s.next.id} ${s.next.title}${s.next.resumes?' (continues where a usage limit stopped it, once the limit has reset)':''}.`:s?.why_idle||''}</p>
     {s&&<div className="ei-actions">{s.paused?<button className="approve" disabled={!!busy} onClick={()=>act('pause','engine.build.dev_controls',{pause:false})}>Resume</button>:<button className="approve" disabled={!!busy||!s.next||!!reset?.waiting} onClick={()=>act('next','engine.build.dev_loop',{task:null})}>Build the next task now</button>}</div>}</article>}
    {many&&idleDevs.map(d=><article className={'lane idle compact dev-'+d.role} key={'dev-'+d.name}><div className="lane-head"><span className="lane-who">{d.display} · {d.role==='hub'?'hub':'worker'}{d.status==='active'&&d.online?'':' · '+(d.status==='pending'?'pending':d.online?d.status:'offline')}</span><b>{d.idle||'Idle'}</b></div></article>)}
   </div></div>
  {note&&<p className="save-status" role="status">{note}{builder.undoIds.length>0&&<button className="linkish" onClick={builder.undo}>Undo</button>}</p>}
  <div className="dir-grid"><div>
   {s&&hubHas(hub,'engine.build.build_next')&&<div className="lane-block" id="build-next-lane"><div className="dir-head"><h4 className="local-title">Build next <small>{(s.plan.lane||[]).length} of {s.plan.lane_cap||5} · built first, in this order, before the queue and any score</small></h4></div>
    {!(s.plan.lane||[]).length?<p className="build-idle">Empty. Press ⏭ on a task below, or in its details, to have it built next.</p>
    :<DragList className="upnext lane" items={s.plan.lane||[]} busy={!!busy} onOrder={ids=>act('lane','engine.build.build_next',{order:ids})} render={(q,i)=><><span className="un-n lane-n">{i+1}</span><div><strong>#{q.id} {q.title}</strong><small>{q.state==='ready'?'you put this here':q.note}</small></div><button className="linkish" aria-label={'Take #'+q.id+' out of Build next'} title="Take it out of Build next (undoable)" disabled={!!busy} onClick={()=>act('lane','engine.build.build_next',{task:q.id,position:0})}>✕</button></>}/>}</div>}
   {s&&hubHas(hub,'engine.build.parallel')&&<ParallelCard refresh={s}/>}
   <div className="dir-head"><h4 className="local-title">Up next <small>your order first, then the plan's ranking; ★ important first{many?' · every device takes its next task from this one queue':''}</small></h4><button className="linkish" onClick={onPlan}>The whole plan</button></div>
   {!s?<p className="build-idle">{ok?'Loading…':'—'}</p>:!next.length?<p className="build-idle">{s.why_idle||'Nothing queued for the builder.'}</p>:<DragList className="upnext" items={next} busy={!!busy} onOrder={ids=>reorder([...ids,...order.slice(shown).map(q=>q.id)].slice(0,shown))} render={(q,i)=><><span className="un-n">{i+1}</span><div><strong>#{q.id} {q.title}</strong><small>{alongside.has(q.id)&&<span className="chip small slot-any" title="In the accepted Side by side batch: built on the next free slot of any device">next free slot on any device</span>}{q.state==='needs_ok'?'waits for your OK in Today':q.note||q.why}</small></div>{hubHas(hub,'engine.build.build_next')&&<button className="lane-add" title="Build next: into the lane, at the end (undoable)" aria-label={'Build #'+q.id+' next'} disabled={!!busy||(s.plan.lane||[]).length>=(s.plan.lane_cap||5)} onClick={()=>act('lane','engine.build.build_next',{task:q.id,position:(s.plan.lane||[]).length+1})}>⏭</button>}{q.state==='ready'&&<button disabled={!!busy||!!s.current||!!reset?.waiting} onClick={()=>act('run'+q.id,'engine.build.dev_loop',{task:q.id})}>Build now</button>}</>}/>}
   {s&&order.length>0&&<div className="upnext-more"><span className="note-dest inline">{([5,10] as const).map(n=><button key={n} className={'chip small'+(shown===n?' on':'')} onClick={()=>setShown(n)}>{n}</button>)}<button className="chip small" title="The whole builder queue, with search" onClick={()=>{setHl(null);setAllOpen(true)}}>All {(s.plan.lane||[]).length+order.length+s.plan.waiting.length}</button></span><small className="muted">Drag to change the order; what you place is built first.</small></div>}
   {allOpen&&s&&<BuildOrder s={s} busy={!!busy} onOrder={reorder} highlight={hl} onClose={()=>{setAllOpen(false);setHl(null)}}/>}
   {s&&s.plan.waiting.length>0&&<small className="ei-why">{s.plan.waiting.length} more cannot start yet (dependencies, locked, pull request open).</small>}
  </div><div>
   <h4 className="local-title">Pull requests</h4>
   {!prs.length?<p className="build-idle">None waiting.</p>:<ul className="queue">{prs.map(p=><li key={p.k}><div className="ei-head"><strong>{p.t}</strong><span className="chip">{p.label}</span></div><div className="ei-actions">{p.url&&<a href={p.url} target="_blank" rel="noreferrer">Open on GitHub</a>}{p.resolve>0&&<button disabled={!!busy} onClick={()=>act('res'+p.resolve,'engine.build.pr_conflicts',{resolve:p.resolve})}>Resolve</button>}</div></li>)}</ul>}
   {recent.length>0&&<><h4 className="local-title">Recently finished</h4><ul className="recent">{recent.map(r=><li key={r.id}><span className={'chip st-'+(r.status==='pr_open'||r.status==='merged'?'ready':'failed')}>{r.status==='pr_open'?'PR open':r.status}</span>#{r.task_id} {r.title}{many&&r.device?<small className="muted"> · {r.device}{(r.slot||1)>1?` · slot ${r.slot}`:''}</small>:null}</li>)}</ul></>}
  </div></div>
  <details className="q-block"><summary>Atlas's own build: paths and handing work to the Claude app or Codex</summary>{tasks.length?<PathTracks tasks={tasks} paths={paths}/>:null}<ContinueBuilding build={build}/></details>
 </div>;
}
function Redirect({go}:{go:()=>void}){useEffect(()=>{go()},[]);return null}
function MiniBuild({build,builder,onOpen}:{build:Build|null;builder:Builder;onOpen:()=>void}){
 const s=builder.s,run=s?.lanes.find(l=>l.state==='running');
 const agent=build&&build.state!=='idle'?build:null;
 const active=!!run||agent?.state==='building';
 const title=run?`#${run.task_id} ${run.title}`:agent?`${agent.taskId} · ${agent.title}`:s?.next?`Next: #${s.next.id} ${s.next.title}`:'Nothing is building';
 const pct=run?(run.progress??5):agent?agent.progress:0;
 const sub=run?((buildingOn(s)>1?`${s!.lanes.filter(l=>l.state==='running').length} builds · `:(s?.devices||[]).length>1&&run.device?`on ${run.device} · `:'')+(run.step||'Getting ready to start the build.')):agent?(agent.state==='stalled'?'paused (usage limit?)':agent.message):s?.paused?'builder paused':`${s?.delivery.review.length||0} pull request(s) to review`;
 return <button className={'panel minibuild'+(active?' active':'')} onClick={onOpen} title="Open Forge › Build"><div className="panel-heading"><h2><b className="forge-dot">●</b> Build</h2><small>{active?(buildingOn(s)>1?`building on ${buildingOn(s)} devices`:'building'):'idle'}</small></div><b className="mb-title">{title}</b><div className="tl-track"><i className="tl-done" style={{width:pct+'%'}}/></div><small className="mb-sub">{sub}</small></button>;
}
type PTask={id:number;title:string;owner:string;status:string;due:string|null;score:number;goal_id:number|null;impact?:number;queued?:number|null;blocked?:string;new?:boolean};
const NewBadge=({on}:{on?:boolean})=>on?<span className="chip new-badge" title="Added recently; the mark fades after the window set on Today › Just added">new</span>:null;
type PGoal={id:number;title:string;horizon:string;why:string;children:PGoal[];tasks:PTask[];pct:number;done:number;total:number;recent_done:string[];parent_id:number|null;new?:boolean};
type PlanTree={mission:string;goals:PGoal[];loose:PTask[]};
function useUndoable(){
 const [toast,setToast]=useState<{msg:string;changes:number[]}|null>(null);const timer=useRef<number|undefined>(undefined);
 async function run(action:string,body:object,after?:()=>void){
  const r=await fetch('/api/engine/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');
  window.clearTimeout(timer.current);setToast({msg:b.message,changes:b.changes||[]});timer.current=window.setTimeout(()=>setToast(null),7000);after?.();return b.message as string}
 async function undo(after?:()=>void){if(!toast?.changes.length)return;const c=toast.changes;setToast(null);try{await post('engine.brain.undo_changes',{changes:c});setToast({msg:'Undone.',changes:[]});timer.current=window.setTimeout(()=>setToast(null),3000)}catch(e){setToast({msg:'Undo failed: '+(e as Error).message,changes:[]})}after?.()}
 return {toast,run,undo};
}
type WhyField={text:string;missing:boolean};
type WhyData={id:number;title:string;problem:WhyField;why_now:WhyField;expected_result:WhyField;goal:WhyField&{why:string};dependencies:WhyField&{none:boolean};effort:WhyField;uncertainty:WhyField;success_criterion:WhyField};
function WhyPopup({id}:{id:number}){
 const [open,setOpen]=useState(false),[d,setD]=useState<WhyData|null|false>(null);
 const toggle=()=>{const o=!open;setOpen(o);if(o&&!d)fetch('/api/engine/engine.direction.explain_rank?id='+id).then(r=>r.ok?r.json():null).then(b=>setD(b?.result||false)).catch(()=>setD(false))};
 const row=(label:string,f:WhyField,note?:string)=><p key={label}><b>{label}</b> {f.missing?<em className="why-missing">missing: nothing is recorded yet</em>:<>{f.text}{note?<small> · {note}</small>:null}</>}</p>;
 return <span className="why-pop"><button className="why-q" aria-label={`Why was task ${id} proposed?`} aria-expanded={open} title="Why was this proposed?" onClick={toggle}>?</button>
  {open&&<div className="why-card" role="dialog" aria-label="Build explanation"><button className="why-x" aria-label="Close" onClick={()=>setOpen(false)}>×</button>
   {d===null?<p>Loading…</p>:d===false?<p>The explanation is not available (the Engine on the Pi may need an update).</p>:<>
    <h6>{d.title}</h6>
    {row('The problem',d.problem)}{row('Why now',d.why_now)}{row('Expected result',d.expected_result)}
    {row('Moves toward',d.goal,d.goal.why)}
    <p><b>Depends on</b> {d.dependencies.none?'nothing else':d.dependencies.text}</p>
    {row('Effort',d.effort)}{row('Uncertainty',d.uncertainty)}{row('Success looks like',d.success_criterion)}</>}
  </div>}</span>;
}
function TaskDetail({id}:{id:number}){
 const [d,setD]=useState<any>(null);
 useEffect(()=>{fetch('/api/engine/engine.build.task_detail?id='+id).then(r=>r.ok?r.json():null).then(b=>setD(b?.result||{error:true})).catch(()=>setD({error:true}))},[id]);
 if(!d)return <p className="ei-why">Loading…</p>;
 if(d.error)return <p className="ei-why">Details are not available.</p>;
 const r=d.rationale||{};
 return <div className="task-detail">
  <LaneButton task={d.task}/>
  <p><b>Why it ranks here:</b> {d.priority_factors}</p>
  {d.recommended&&<p><b>Recommended now:</b> {d.why}</p>}
  {(['problem','why_now','benefit','tradeoffs','success_check'] as const).filter(k=>r[k]).map(k=><p key={k}><b>{({problem:'Problem',why_now:'Why now',benefit:'Benefit',tradeoffs:'Trade-offs',success_check:'Done when'} as Record<string,string>)[k]}:</b> {r[k]}</p>)}
  {d.dependencies?.length>0&&<p><b>After:</b> {d.dependencies.map((x:any)=>`#${x.id} ${x.title} (${x.status})`).join(', ')}</p>}
  {d.prereq?.line&&<p><b>First needed:</b> {d.prereq.line}</p>}
  {d.overlap&&d.overlap.verdict!=='new'&&<p><b>Already in Atlas?</b> {d.overlap.verdict}{d.overlap.why?` — ${d.overlap.why}`:''}</p>}
  {d.task.description&&<p className="desc">{d.task.description}</p>}
 </div>;
}
// Build next from a task's details (#182): put it in the lane, or take it out; undoable.
function LaneButton({task}:{task:any}){
 const [place,setPlace]=useState<number|null>(task?.build_next??null),[msg,setMsg]=useState(''),[undo,setUndo]=useState<number[]>([]);
 if(!task||!['ai','both'].includes(task.owner)||['done','dropped'].includes(task.status))return null;
 const go=async(position:number)=>{try{const r=await fetch('/api/engine/engine.build.build_next',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:task.id,position})});const b=await r.json();if(!r.ok)throw Error(b.error);setMsg(b.message);setUndo(b.changes||[]);setPlace(position?Math.min(position,99):null)}catch(e){setMsg('Not done: '+(e as Error).message)}};
 return <p className="lane-detail">{place?<><span className="chip queued">In Build next</span><button className="linkish" onClick={()=>go(0)}>Take out</button></>:<><button onClick={()=>go(1)} title="The builder takes it next, before everything else">⏭ Build next</button><button className="linkish" onClick={()=>go(99)} title="At the end of the Build next lane">…at the end of Build next</button></>}
  {msg&&<small className="ei-why"> {msg}{undo.length>0&&<button className="linkish" onClick={async()=>{try{setMsg(await post('engine.brain.undo_changes',{changes:undo}));setUndo([]);setPlace(task.build_next??null)}catch{}}}>Undo</button>}</small>}</p>;
}
const TASK_ICON:Record<string,string>={done:'✓',doing:'◌',blocked:'⧗',todo:'○',proposed:'◇'};
function PlanTask({t,act,busy}:{t:PTask;act:(a:string,b:object)=>void;busy:boolean}){
 const [open,setOpen]=useState(false);
 return <li className={'ptask st-'+t.status+(t.new?' is-new':'')}><div className="ptask-row"><span className="ticon">{TASK_ICON[t.status]||'○'}</span><button className="linkish ptitle" onClick={()=>setOpen(!open)} aria-expanded={open}>#{t.id} {t.title}</button><NewBadge on={t.new}/>{t.status==='proposed'&&<WhyPopup id={t.id}/>}
  <span className="chip">{OWNER[t.owner]||t.owner}</span>{t.due&&<span className="chip">due {t.due}</span>}
  <span className="pt-actions"><button className={'star'+(t.impact===5?' on':'')} title={t.impact===5?'Important: ranks first. Click to make it normal again':'Mark important: it ranks first, and the builder takes it next'} disabled={busy} onClick={()=>act('engine.build.task_status',{task:t.id,status:t.impact===5?'normal':'important'})}>★</button>
   {t.status!=='done'&&(t.queued?<span className="chip queued" title="In the builder's queue; change the order in Forge › Build">queue #{t.queued}</span>:t.blocked?<button className="blocked-build" disabled title={'Cannot be built yet: '+t.blocked}>⚠ Build</button>:<button disabled={busy} title="Into the builder's queue, as the last of the five shown in Forge › Build" onClick={()=>act('engine.build.task_status',{task:t.id,status:'builder'})}>Build</button>)}
   {t.owner==='me'&&<button disabled={busy} onClick={()=>act('engine.build.task_status',{task:t.id,status:'done'})}>Done</button>}
   <button className="deny" disabled={busy} title="Set aside (undoable)" onClick={()=>act('engine.build.task_status',{task:t.id,status:'dropped'})}>Delete</button></span></div>
  {open&&<TaskDetail id={t.id}/>}</li>;
}
function GoalNode({g,act,busy,depth}:{g:PGoal;act:(a:string,b:object)=>void;busy:boolean;depth:number}){
 const [adding,setAdding]=useState<''|'task'|'goal'>(''),[text,setText]=useState(''),[open,setOpen]=useState(depth<3);
 const child='goal';  // Vision → Goal → Task: no time layers (owner, 2026-10-25); goals are added in Focus
 const submit=()=>{if(!text.trim())return;adding==='task'?act('engine.build.edit_plan',{create:{table:'tasks',fields:{title:text,goal_id:g.id,owner:'me'}}}):act('engine.build.edit_plan',{create:{table:'goals',fields:{title:text,horizon:child,parent_id:g.id}}});setText('');setAdding('')};
 return <li className={'pgoal h-'+g.horizon+(g.new?' is-new':'')}><div className="pgoal-head"><button className="linkish" onClick={()=>setOpen(!open)} aria-expanded={open}>{open?'▾':'▸'}</button>{g.horizon==="life"?<span className="chip">vision</span>:(g as any).due?<span className="chip due" title="A deadline raises the priority of its tasks; change it in Focus">due {(g as any).due}</span>:null}<b>{g.title}</b><NewBadge on={g.new}/>
  <span className="bar"><i style={{width:g.pct+'%'}}/></span><small>{g.done}/{g.total}</small>
  <span className="pt-actions"><button disabled={busy} onClick={()=>setAdding(adding==='task'?'':'task')}>+ task</button></span></div>
  {adding&&<div className="ei-actions add-row"><input autoFocus placeholder={adding==='task'?'A task under this goal':`A ${child} goal under “${g.title}”`} value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/><button className="approve" disabled={!text.trim()} onClick={submit}>Add</button></div>}
  {open&&<>{g.tasks.length>0&&<ul className="ptasks">{g.tasks.map(t=><PlanTask key={t.id} t={t} act={act} busy={busy}/>)}</ul>}
   {g.recent_done.length>0&&<small className="recent-done">Recently done: {g.recent_done.join(' · ')}</small>}
   {g.children.length>0&&<ul className="pgoals">{g.children.map(c=><GoalNode key={c.id} g={c} act={act} busy={busy} depth={depth+1}/>)}</ul>}</>}
 </li>;
}
const BRAIN_STARTERS=['What should I do next, and why?','What is stuck, and what would unblock it?','Plan my week around my focus.','Which ideas fit my visions best?','What did the builder finish this week, and what does it mean for the plan?'];
type ChatMsg={id?:number;role:string;text:string;ts:string};
type Pending={text:string;mode:'ask'|'file'|'auto';failed?:string};
const CHAT_COMMANDS:[string,string][]=[['/mockup ','A mockup of a feature, made in Claude or ChatGPT'],['/find ','Find a screen, task, idea, note or function'],['/file ','Straight into the plan, without an answer'],['/new','Start a new conversation']];
const draftKey='atlas-cortex-draft';
const readDraft=()=>{try{return localStorage.getItem(draftKey)||''}catch{return ''}};
const saveDraft=(t:string)=>{try{t?localStorage.setItem(draftKey,t):localStorage.removeItem(draftKey)}catch{}};
// A small, safe Markdown renderer for Cortex's answers: React elements only, no HTML is ever injected.
function mdInline(t:string,key:string):React.ReactNode[]{
 const out:React.ReactNode[]=[];const re=/(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))|(#\d{1,6}\b)/g;let last=0,m:RegExpExecArray|null,i=0;
 while((m=re.exec(t))){if(m.index>last)out.push(t.slice(last,m.index));const x=m[0],k=key+'-'+(i++);
  if(m[1])out.push(<code key={k}>{x.slice(1,-1)}</code>);else if(m[2])out.push(<strong key={k}>{x.slice(2,-2)}</strong>);else if(m[3])out.push(<em key={k}>{x.slice(1,-1)}</em>);
  else if(m[4]){const j=x.indexOf('](');out.push(<a key={k} href={x.slice(j+2,-1)} target="_blank" rel="noopener noreferrer">{x.slice(1,j)}</a>)}else out.push(<span key={k} className="md-ref">{x}</span>);
  last=m.index+x.length}
 if(last<t.length)out.push(t.slice(last));return out}
function Md({text}:{text:string}){
 const lines=text.replace(/\r/g,'').split('\n'),blocks:React.ReactNode[]=[];let i=0,n=0;
 while(i<lines.length){const l=lines[i],k='b'+(n++);
  if(/^```/.test(l)){const lang=l.slice(3).trim().toLowerCase(),code:string[]=[];i++;while(i<lines.length&&!/^```/.test(lines[i]))code.push(lines[i++]);const closed=i<lines.length;i++;
   blocks.push(closed&&DRAW_KINDS.includes(lang)?<Drawing key={k} code={code.join('\n')} kind={lang}/>:<CodeBlock key={k} code={code.join('\n')}/>);continue}
  const h=/^(#{1,4})\s+(.*)$/.exec(l);if(h){blocks.push(<p key={k} className={'md-h md-h'+h[1].length}>{mdInline(h[2],k)}</p>);i++;continue}
  if(/^\s*([-*•]|\d+[.)])\s+/.test(l)){const ordered=/^\s*\d/.test(l),items:string[]=[];while(i<lines.length&&/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]))items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/,''));
   const li=items.map((x,j)=><li key={j}>{mdInline(x,k+j)}</li>);blocks.push(ordered?<ol key={k}>{li}</ol>:<ul key={k}>{li}</ul>);continue}
  if(/^>\s?/.test(l)){const q:string[]=[];while(i<lines.length&&/^>\s?/.test(lines[i]))q.push(lines[i++].replace(/^>\s?/,''));blocks.push(<blockquote key={k}>{mdInline(q.join(' '),k)}</blockquote>);continue}
  if(!l.trim()){i++;continue}
  const para:string[]=[];while(i<lines.length&&lines[i].trim()&&!/^(```|#{1,4}\s|>\s?|\s*([-*•]|\d+[.)])\s+)/.test(lines[i]))para.push(lines[i++]);
  blocks.push(<p key={k}>{para.flatMap((x,j)=>j?[<br key={'br'+j}/>,...mdInline(x,k+j)]:mdInline(x,k+j))}</p>)}
 return <div className="md">{blocks}</div>}
function CodeBlock({code}:{code:string}){const [done,setDone]=useState(false);return <div className="md-code"><button className="linkish" onClick={async()=>{try{await navigator.clipboard.writeText(code);setDone(true);setTimeout(()=>setDone(false),1500)}catch{}}}>{done?'Copied':'Copy'}</button><pre><code>{code}</code></pre></div>}
// #183: a finished ```svg or ```html block is drawn, not shown as code. The Engine sanitises it (mockup.drawing: no
// scripts, no outside addresses, its own Content-Security-Policy) and the frame has no sandbox permissions at all.
const DRAW_KINDS=['svg','html'];
const drawn=new Map<string,{page?:string;err?:string}>();
function Drawing({code,kind}:{code:string;kind:string}){
 const key=kind+':'+code,[d,setD]=useState(drawn.get(key)),[big,setBig]=useState(false),[showCode,setShowCode]=useState(false);
 const [saving,setSaving]=useState(false),[title,setTitle]=useState(''),[link,setLink]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{if(drawn.has(key)){setD(drawn.get(key));return}let live=true;
  post('engine.brain.mockup',{preview:{code,kind}}).then(page=>({page}),e=>({err:(e as Error).message})).then(x=>{drawn.set(key,x);if(live)setD(x)});
  return()=>{live=false}},[key]);
 if(d?.err)return <><CodeBlock code={code}/><p className="build-idle small">Not drawn: {d.err}</p></>;
 if(!d?.page)return <p className="build-idle">Drawing…</p>;
 const save=async()=>{const m=/^\s*(idea\s*)?#?(\d+)\s*$/i.exec(link);if(link.trim()&&!m){setNote('Type a task number such as #183, or "idea 12"');return}
  setBusy(true);setNote('');try{setNote(await post('engine.brain.mockup',{drawing:{code,kind,title,task:m&&!m[1]?m[2]:'',idea:m&&m[1]?m[2]:''}}));setSaving(false);window.dispatchEvent(new Event('atlas-mockups'))}
  catch(e){setNote('Not saved: '+(e as Error).message)}finally{setBusy(false)}};
 return <div className="md-draw">
  <div className="md-draw-frame"><iframe sandbox="" srcDoc={d.page} title={`Drawing (${kind})`} loading="lazy"/></div>
  <div className="bmsg-actions"><button className="linkish" onClick={()=>setBig(true)}>Open full size</button><button className="linkish" aria-expanded={showCode} onClick={()=>setShowCode(x=>!x)}>{showCode?'Hide':'Show'} code</button>
   <button className="linkish" aria-expanded={saving} title="Keep it as a mockup round, for a task or an idea" onClick={()=>setSaving(x=>!x)}>Save as mockup</button></div>
  {saving&&<div className="mock-form md-draw-save"><input value={title} maxLength={200} placeholder="Name (optional: taken from the drawing or the task)" aria-label="Mockup name" onChange={e=>setTitle(e.target.value)}/>
   <input value={link} maxLength={20} placeholder="For task #… or idea … (optional)" aria-label="For a task or an idea" onChange={e=>setLink(e.target.value)} onKeyDown={e=>e.key==='Enter'&&save()}/>
   <div className="ei-actions"><button className="approve" disabled={busy} onClick={save}>{busy?'Saving…':'Save'}</button><button className="deny" disabled={busy} onClick={()=>setSaving(false)}>Cancel</button></div></div>}
  {note&&<p className="save-status">{note}</p>}
  {showCode&&<CodeBlock code={code}/>}
  {big&&<div className="mock-zoom md-draw-zoom" role="dialog" aria-label="Drawing full size" onClick={()=>setBig(false)} onKeyDown={e=>e.key==='Escape'&&setBig(false)} tabIndex={-1} ref={el=>el?.focus()}><iframe sandbox="" srcDoc={d.page} title={`Drawing (${kind}), full size`}/><small>Click outside or press Esc to close</small></div>}
 </div>}
const when=(ts:string)=>{const d=new Date(ts);return isNaN(+d)?'':d.toLocaleString(undefined,{weekday:'short',hour:'2-digit',minute:'2-digit'})};
function useMockups(hub:Hub|null){
 const [v,setV]=useState<MView|null>(null),[busy,setBusy]=useState(''),[note,setNote]=useState('');
 const ok=hubHas(hub,'engine.brain.mockups')&&hubHas(hub,'engine.brain.mockup');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.brain.mockups');if(r.ok)setV((await r.json()).result)}catch{}};
 useEffect(()=>{if(ok)load()},[ok]);
 useEffect(()=>{const on=()=>load();window.addEventListener('atlas-mockups',on);return()=>window.removeEventListener('atlas-mockups',on)},[]);  // a drawing saved from the chat
 const act=async(key:string,body:object)=>{setBusy(key);setNote('');try{setNote(await post('engine.brain.mockup',body));return true}catch(e){setNote('Not done: '+(e as Error).message);return false}finally{setBusy('');load()}};
 return {ok,v,busy,note,setNote,act,load};
}
type Extras={after:number;proposals:Proposal[];choices:string[];recommended?:number;mockup:{title:string;brief:string}|null};
type Conv={conv:number;messages:number;started:string;last:string;title:string};
// A search typed in the chat ("go to the plan", "where is the bank call"): the hits, 1–5 when it is the latest thing.
function FindBubble({q,latest}:{q:string;latest:boolean}){
 const hits=useAtlasSearch(q,5),[at,setAt]=useState(-1);
 const pick=(i:number)=>{const h=hits[i];if(h){setAt(i);h.go()}};
 useEffect(()=>{if(!latest||!hits.length)return;const k=(e:KeyboardEvent)=>{const t=e.target as HTMLElement;if(e.ctrlKey||e.metaKey||e.altKey||(['INPUT','TEXTAREA'].includes(t.tagName)&&(t as HTMLTextAreaElement).value))return;
  if(/^[1-5]$/.test(e.key)&&hits[+e.key-1]){e.preventDefault();pick(+e.key-1)}};window.addEventListener('keydown',k);return()=>window.removeEventListener('keydown',k)},[latest,hits]);
 return <div className="bmsg-body"><p className="find-q">Found for “{q}”</p><HitList hits={hits} at={at} pick={pick}/></div>;
}
// Cortex (owner, 2026-10-25): the whole screen is one chat, like Claude or ChatGPT, and the full brain: it may propose
// any of Atlas's functions (run only after yes or its number), asks back with choices, offers a mockup when an idea has
// a visual side, and finds things. Earlier conversations sit behind a small button.
function BrainPage({go,hub,dir,convo,loadConvo,thoughts,loadThoughts,undoThought,pcVoice,cortex,onNamed}:{go:(label:string)=>void;cortex:{name:string;named:boolean}|null;onNamed:()=>void;hub:Hub|null;dir:Dir;convo:ChatMsg[]|null;loadConvo:()=>Promise<any>;thoughts:Thought[]|null;loadThoughts:()=>Promise<void>;undoThought:(id:number)=>void;pcVoice:LocalVoice|null}){
 const [text,setTextRaw]=useState(readDraft),[pending,setPending]=useState<Pending|null>(null),[note,setNote]=useState(''),[changes,setChanges]=useState<number[]>([]);
 const cx=useCortex(),[nick,setNick]=useState(''),[renaming,setRenaming]=useState(false),[seeing,setSeeing]=useState(false),[atEnd,setAtEnd]=useState(true),[unseen,setUnseen]=useState(false),[copied,setCopied]=useState(-1);
 const [extras,setExtras]=useState<Extras|null>(null),[finds,setFinds]=useState<{q:string;at:string}[]>([]),[convs,setConvs]=useState<{current:number;conversations:Conv[]}|null>(null),[showConvs,setShowConvs]=useState(false);
 const box=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),abortRef=useRef<AbortController|null>(null),chatFile=useRef<HTMLInputElement>(null);
 const [streaming,setLive]=useState<{text:string;tool:string}|null>(null),[attached,setAttached]=useState<string[]>([]);
 const mk=useMockups(hub);
 const reload=async()=>{await loadConvo();await loadThoughts()};
 const p=useProposals(go,reload,false);
 const ok=hubHas(hub,'engine.brain.talk'),busy=!!pending&&!pending.failed;
 const canConvs=hubHas(hub,'engine.brain.conversations');
 const loadConvs=async()=>{if(!canConvs)return;try{const r=await fetch('/api/engine/engine.brain.conversations');if(r.ok)setConvs((await r.json()).result)}catch{}};
 const setText=(t:string|((o:string)=>string))=>setTextRaw(o=>{const n=typeof t==='function'?t(o):t;saveDraft(n);return n});
 const name=async(n:string)=>{try{setNote(await post('engine.brain.name_cortex',{name:n}));setRenaming(false);onNamed()}catch(e){setNote((e as Error).message)}};
 const dict=useDictation({canWhisper:hubHas(hub,'engine.brain.voice_input'),canPC:!!pcVoice?.available,pcPreviews:pcVoice?.device==='cuda',append:t=>setText(b=>joinText(b,t)),say:setNote});
 useEffect(()=>{if(ok){loadConvo();loadThoughts();loadConvs()}},[ok]);
 useEffect(()=>{const el=input.current;if(!el)return;el.style.height='auto';el.style.height=Math.min(el.scrollHeight,Math.round(window.innerHeight/3))+'px'},[text]);
 useEffect(()=>{input.current?.focus()},[ok]);
 const toEnd=(smooth=true)=>{const el=box.current;if(el)el.scrollTo({top:el.scrollHeight,behavior:smooth?'smooth':'auto'});setUnseen(false)};
 const count=(convo?.length||0)+(pending?1:0)+(streaming?streaming.text.length:0)+(mk.v?.mockups.length||0)+(note?1:0)+finds.length+(extras?1:0)+(p.done?1:0);
 useEffect(()=>{if(atEnd)requestAnimationFrame(()=>toEnd(false));else setUnseen(true)},[count,busy]);
 const onScroll=()=>{const el=box.current;if(!el)return;const end=el.scrollHeight-el.scrollTop-el.clientHeight<80;setAtEnd(end);if(end)setUnseen(false)};
 const switchTo=async(body:object)=>{setText('');setExtras(null);setFinds([]);p.offer([]);setChanges([]);try{setNote(await post('engine.brain.new_conversation',body))}catch(e){setNote((e as Error).message)}await loadConvo();await loadConvs();setShowConvs(false)};
 const run=async(body:string,m:'ask'|'file'='ask')=>{
  const cmd=/^\/(\w+)\s*([\s\S]*)$/.exec(body.trim());
  if(cmd){const [,c,rest]=cmd;
   if(c==='new'){await switchTo({});return}
   if(c==='mockup'){if(!rest.trim()){setNote('Name the feature after /mockup, e.g. /mockup A weekly progress chart on Today');return}
    const [t,...more]=rest.trim().split('\n');setText('');await mk.act('new',{request:{title:t,brief:more.join('\n'),section:''}});return}
   if(c==='find'){setFinds(f=>[...f,{q:rest.trim(),at:new Date().toISOString()}]);setText('');return}
   if(c==='file'){body=rest;m='file'}}
  if(!body.trim())return;
  const fq=m==='ask'?findQuery(body):null;
  if(fq){setFinds(f=>[...f,{q:fq,at:new Date().toISOString()}]);setText('');setAtEnd(true);return}
  setPending({text:body,mode:m});setText('');setNote('');setChanges([]);setExtras(null);p.offer([]);p.setDone('');setAtEnd(true);
  if(m==='ask'&&hubHas(hub,'engine.brain.chat_stream')){await streamTurn(body);return}
  try{const r=await fetch('/api/engine/engine.brain.talk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:body,mode:m,full:m==='ask',views:SPEAK_VIEWS})});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');
   setChanges(b.changes||[]);const c=await loadConvo();await loadThoughts();loadConvs();setPending(null);
   if(b.message&&typeof b.message==='object'){const x=b.message;setExtras({after:c?c.length:0,proposals:x.proposals||[],choices:x.choices||[],mockup:x.mockup||null});p.offer(x.proposals||[])}
   else{const last=c&&c[c.length-1];if(!(last&&last.role!=='me'&&String(b.message||'').startsWith(String(last.text).split('\n\nFiled:')[0].slice(0,60))))setNote(b.message)}}
  catch(e){setPending({text:body,mode:m,failed:(e as Error).message})}
  finally{input.current?.focus()}};
 // The answer arrives word by word (RA-083): Claude Code on the Pi, real conversation turns, web search, look-ups in Atlas.
 const streamTurn=async(body:string)=>{const ctl=new AbortController();abortRef.current=ctl;setLive({text:'',tool:''});
  const files=attached;setAttached([]);
  try{const r=await fetch('/api/stream/engine.brain.chat_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:files.length?`${body}\n\n(Attached: ${files.join(', ')})`:body,views:SPEAK_VIEWS}),signal:ctl.signal});
   if(!r.ok||!r.body)throw Error((await r.json().catch(()=>({}))).error||'The Engine refused');
   const reader=r.body.getReader(),dec=new TextDecoder();let buf='',done:any=null,err='';
   for(;;){const {value,done:end}=await reader.read();if(end)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+1);if(!line.trim())continue;let ev:any;try{ev=JSON.parse(line)}catch{continue}
     if(ev.t==='text')setLive(l=>l&&{...l,text:l.text+ev.d,tool:''});else if(ev.t==='tool'||ev.t==='status')setLive(l=>l&&{...l,tool:String(ev.d)});else if(ev.t==='done')done=ev.d;else if(ev.t==='error')err=String(ev.d)}}
   if(err&&!done)throw Error(err);
   const c=await loadConvo();loadThoughts();loadConvs();setPending(null);setLive(null);
   if(done){setChanges(done.changes||[]);if((done.changes||[]).length)setNote('Cortex changed the plan.');const x=done.message||{};setExtras({after:c?c.length:0,proposals:x.proposals||[],choices:x.choices||[],recommended:x.recommended||0,mockup:x.mockup||null});p.offer(x.proposals||[])}}
  catch(e){const stopped=(e as Error).name==='AbortError';setLive(null);
   if(stopped){setPending(null);setNote('Stopped here. Cortex may still finish the answer in the Engine; it appears when you reopen the chat.');await loadConvo()}
   else setPending({text:body,mode:'ask',failed:(e as Error).message})}
  finally{abortRef.current=null;input.current?.focus()}};
 const attach=async(f:File|undefined|null)=>{if(!f)return;if(f.size>10*1024*1024){setNote('Files up to 10 MB.');return}
  try{const m=await post('engine.brain.chat_file',{name:f.name,data:await readBase64(f)}) as any;setAttached(a=>[...a,m.name||f.name])}catch(e){setNote('Not attached: '+(e as Error).message)}};
 const send=()=>{if(!busy&&text.trim())run(text)};
 const undo=async()=>{const c=changes;setChanges([]);try{setNote(await post('engine.brain.undo_changes',{changes:c}));await loadThoughts()}catch(e){setNote('Undo failed: '+(e as Error).message)}};
 const copy=async(i:number,t:string)=>{try{await navigator.clipboard.writeText(t);setCopied(i);setTimeout(()=>setCopied(-1),1500)}catch{}};
 const msgs:ChatMsg[]=convo||[];
 const lastMine=[...msgs].reverse().find(m=>m.role==='me');
 const live=extras&&extras.after===msgs.length?extras:null;
 const offer=live?.mockup&&!(mk.v?.mockups||[]).some(m=>m.title===live.mockup!.title)?live.mockup:null;
 // with an empty box: 1–3 runs a proposal (Y the selected, N declines), or 1–5 picks a choice
 useEffect(()=>{const k=(e:KeyboardEvent)=>{if(e.ctrlKey||e.metaKey||e.altKey||text||busy)return;const t=e.target as HTMLElement;if(['INPUT','SELECT'].includes(t.tagName)||(t.tagName==='TEXTAREA'&&t!==input.current))return;
  const n=/^[1-9]$/.test(e.key)?+e.key-1:-1,nc=live?live.choices.length:0;
  if(n>=0&&n<nc){e.preventDefault();run(live!.choices[n]);return}
  if(n>=0&&p.props[n-nc]){e.preventDefault();p.run(n-nc)}};
  window.addEventListener('keydown',k);return()=>window.removeEventListener('keydown',k)},[text,busy,p.props,p.sel,live]);
 const onKey=(e:React.KeyboardEvent<HTMLTextAreaElement>)=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send();return}
  if(e.key==='ArrowUp'&&!text&&lastMine){e.preventDefault();setText(lastMine.text);return}
  if(e.key==='Escape'){if(text&&!busy){setText('')}else input.current?.blur()}};
 if(!hub?.reachable)return <section className="panel page"><h2>{cx}</h2><p className="build-idle">{cx} runs in the Engine on the Pi; it appears here when the hub is online.</p></section>;
 if(!ok)return <section className="panel page"><h2>{cx}</h2><Outdated what={cx}/></section>;
 const first=msgs[0]?.ts||'';
 type Item={at:string;kind:'msg';m:ChatMsg;i:number}|{at:string;kind:'mock';m:Mock}|{at:string;kind:'find';q:string;n:number};
 const items:Item[]=[...msgs.map((m,i)=>({at:m.ts,kind:'msg' as const,m,i})),
  ...(mk.v?.mockups||[]).filter(m=>m.status==='open'||(first&&(m.updated||'')>=first)).map(m=>({at:m.status==='open'?(m.created||''):(m.updated||''),kind:'mock' as const,m})),
  ...finds.map((f,n)=>({at:f.at.slice(0,19),kind:'find' as const,q:f.q,n}))]
  .sort((x,y)=>x.at<y.at?-1:x.at>y.at?1:0);
 const slash=text.startsWith('/')&&!text.includes(' ')&&!text.includes('\n')?CHAT_COMMANDS.filter(([c])=>c.trim().startsWith(text.trim())):[];
 const empty=items.length===0&&!pending;
 const lastAtlas=msgs.length&&msgs[msgs.length-1].role!=='me'?msgs.length-1:-1;
 return <section className="panel page brain-page">
  <header className="chat-head">
   {canConvs&&<button className={'conv-toggle'+(showConvs?' on':'')} aria-expanded={showConvs} aria-label="Earlier conversations" title="Earlier conversations" onClick={()=>{setShowConvs(x=>!x);loadConvs()}}>☰</button>}
   <h2>{cx}{cx!=='Cortex'&&<small className="muted"> · Cortex</small>}</h2>
   <div className="chat-head-tools">{cortex?.named&&hubHas(hub,'engine.brain.name_cortex')&&<button className="linkish" title="Give it another name" onClick={()=>{setNick(cx==='Cortex'?'':cx);setRenaming(true)}}>rename</button>}
    <button className={'chip small'+(seeing?' on':'')} aria-expanded={seeing} title={`What ${cx} reads before it answers`} onClick={()=>setSeeing(x=>!x)}>What {cx} sees</button>
    {hubHas(hub,'engine.brain.new_conversation')&&<button className="chip small" disabled={busy||msgs.length===0} title="The earlier conversation is kept (/new)" onClick={()=>switchTo({})}>＋ New conversation</button>}</div>
   {seeing&&<div className="chat-sees" role="dialog" aria-label={`What ${cx} sees`}>
    <div className="bs-block"><small>Vision in focus</small><b>{dir.current?.title||'None chosen'}</b></div>
    {dir.steps&&dir.steps.length>0&&<div className="bs-block"><small>Next in the plan</small><ol>{dir.steps.slice(0,5).map((x:any)=><li key={x.id}>#{x.id} {x.title}</li>)}</ol></div>}
    <div className="bs-block"><small>What your thoughts became</small><EngineBrain thoughts={thoughts?thoughts.slice(0,5):null} onUndo={undoThought}/></div></div>}
  </header>
  <div className="chat-body">
  {showConvs&&convs&&<nav className="conv-list" aria-label="Conversations"><ul>{convs.conversations.map(c=><li key={c.conv}><button className={c.conv===convs.current?'on':''} disabled={busy} onClick={()=>c.conv!==convs.current&&switchTo({open:c.conv})}><b>{c.title}</b><small>{c.messages?`${c.messages} messages · ${when(c.last)}`:'empty'}</small></button></li>)}</ul></nav>}
  <div className="chat-scroll" ref={box} onScroll={onScroll} aria-live="polite">
   <div className="chat-col">
    {cortex&&(!cortex.named||renaming)&&hubHas(hub,'engine.brain.name_cortex')&&<div className="name-cortex" role="dialog" aria-label="Name Cortex"><b>Meet Cortex, Atlas's reasoning core.</b><p>It goes by Cortex. Give it a nickname of your own if you like; it shows everywhere in Atlas, and it answers to it.</p><div className="ei-actions"><input value={nick} maxLength={24} placeholder="A nickname, e.g. Nova" aria-label="Nickname" onChange={e=>setNick(e.target.value)} onKeyDown={e=>e.key==='Enter'&&nick.trim()&&name(nick)}/><button className="approve" disabled={!nick.trim()} onClick={()=>name(nick)}>Use this name</button><button onClick={()=>name('Cortex')}>Keep Cortex</button></div></div>}
    {empty&&<div className="brain-empty"><p className="chat-hello">What are we working on?</p><p className="muted">{cx} sees your visions, plan, ideas, resources and memory, and can use every function in Atlas: it proposes, you approve with 1, 2 or 3.</p><div className="starters">{BRAIN_STARTERS.map(q=><button key={q} className="chip" onClick={()=>run(q)}>{q}</button>)}</div></div>}
    {items.map(it=>it.kind==='mock'
     ?<div key={'m'+it.m.id} className="bmsg atlas wide"><span className="bmsg-who">{cx} · mockup</span><MockupMsg m={it.m} sections={mk.v?.sections||[]} busy={mk.busy} act={mk.act}/></div>
     :it.kind==='find'?<div key={'f'+it.n} className="bmsg atlas wide"><span className="bmsg-who">{cx} · find</span><FindBubble q={it.q} latest={it.n===finds.length-1&&!p.props.length}/></div>
     :<div key={'c'+(it.m.id??it.i)} className={'bmsg '+(it.m.role==='me'?'me':'atlas')} title={when(it.m.ts)}>
       {it.m.role!=='me'&&<span className="bmsg-who">{cx}</span>}
       <div className="bmsg-body">{it.m.role==='me'?<p>{it.m.text}</p>:<Md text={live&&it.i===lastAtlas&&(live.proposals.length||live.choices.length)?it.m.text.split(/\n1\. /)[0]:it.m.text}/>}</div>
       {it.m.role!=='me'&&it.i===lastAtlas&&live&&!busy&&<>
        {live.choices.length>0&&<div className="speak-choices">{live.choices.map((c,i)=><button key={i} className={'chip'+(live.recommended===i+1?' on':'')} title={live.recommended===i+1?'Recommended by Cortex':undefined} onClick={()=>run(c)}><kbd>{i+1}</kbd>{c}{live.recommended===i+1&&<small> · recommended</small>}</button>)}<button className="chip" onClick={()=>input.current?.focus()}>Something else…</button></div>}
        <ProposalList p={p} preview={false} offset={live.choices.length}/></>}
       <div className="bmsg-actions"><button className="linkish" onClick={()=>copy(it.i,it.m.text)}>{copied===it.i?'Copied':'Copy'}</button>
        {it.m.role==='me'&&it.m===lastMine&&!busy&&<button className="linkish" title="Put it back in the box to change and send again (↑)" onClick={()=>{setText(it.m.text);input.current?.focus()}}>Edit</button>}
        {it.m.role!=='me'&&it.i===msgs.length-1&&!busy&&lastMine&&<button className="linkish" title="Send your last message again" onClick={()=>run(lastMine.text)}>Ask again</button>}</div>
      </div>)}
    {offer&&!busy&&<div className="bmsg atlas wide mock-offer"><span className="bmsg-who">{cx} · visual first</span><div className="bmsg-body"><p>This idea has a visual side: <b>{offer.title}</b>. Want to see a mockup first? It is made in Claude or ChatGPT and shown here.</p>
     <div className="ei-actions"><button className="approve" disabled={!!mk.busy} onClick={async()=>{if(await mk.act('offer',{request:{title:offer.title,brief:offer.brief,section:''}}))setExtras(x=>x&&{...x,mockup:null})}}>Yes, mockup first</button><button onClick={()=>setExtras(x=>x&&{...x,mockup:null})}>No, not now</button></div></div></div>}
    {pending&&<div className={'bmsg me'+(pending.failed?' failed':'')}><div className="bmsg-body"><p>{pending.text}</p></div>
     {pending.failed?<div className="bmsg-actions"><span className="bad">Not sent: {pending.failed}</span><button className="linkish" onClick={()=>run(pending.text,pending.mode==='file'?'file':'ask')}>Retry</button><button className="linkish" onClick={()=>{setText(pending.text);setPending(null);input.current?.focus()}}>Edit</button><button className="linkish" onClick={()=>setPending(null)}>Discard</button></div>:null}</div>}
    {busy&&<div className="bmsg atlas live"><span className="bmsg-who">{cx}</span>{streaming&&streaming.text?<div className="bmsg-body"><Md text={streaming.text}/></div>:<div className="typing-dots" aria-label={`${cx} is thinking`}><i/><i/><i/></div>}
     {streaming&&streaming.tool&&<p className="live-tool">{streaming.tool}…</p>}
     {abortRef.current&&<div className="bmsg-actions"><button className="linkish" onClick={()=>abortRef.current?.abort()}>Stop</button></div>}</div>}
    {p.done&&<p className="chat-system" role="status">{p.done}{p.changes.length>0&&<button className="linkish" onClick={p.undo}>Undo</button>}<button className="linkish" aria-label="Dismiss" onClick={()=>p.setDone('')}>×</button></p>}
    {(note||mk.note)&&<p className="chat-system" role="status">{note||mk.note}{changes.length>0&&<button className="linkish" onClick={undo}>Undo</button>}<button className="linkish" aria-label="Dismiss" onClick={()=>{setNote('');mk.setNote('')}}>×</button></p>}
   </div>
  </div>
  </div>
  {unseen&&<button className="chat-jump" onClick={()=>toEnd()}>↓ Latest</button>}
  <div className="chat-compose"><div className="chat-col">
   {slash.length>0&&<ul className="chat-slash" role="listbox">{slash.map(([c,d])=><li key={c}><button onClick={()=>{setText(c);input.current?.focus()}}><b>{c.trim()}</b> {d}</button></li>)}</ul>}
   {(dict.listening||dict.working)&&<p className="dictation-live" aria-live="polite">{dict.live||(dict.working?'Writing out…':'Listening…')}</p>}
   <div className="chat-box" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();[...e.dataTransfer.files].forEach(attach)}}>
    {attached.length>0&&<div className="chat-files">{attached.map(n=><span key={n} className="chip small">📎 {n}</span>)}</div>}
    <textarea ref={input} rows={1} value={text} placeholder={`Message ${cx}… (/ for commands)`} aria-label={'Message to '+cx} onChange={e=>setText(e.target.value)} onKeyDown={onKey}
     onPaste={e=>{const f=[...e.clipboardData.items].filter(i=>i.kind==='file').map(i=>i.getAsFile());if(f.length&&hubHas(hub,'engine.brain.chat_file')){e.preventDefault();f.forEach(attach)}}}/>
    <div className="chat-box-row"><span className="chat-box-gap"/>
     {hubHas(hub,'engine.brain.chat_file')&&<><button className="tiny-attach" aria-label="Attach a file" title="Attach a screenshot, PDF or file; Cortex can read it (or paste or drop it)" onClick={()=>chatFile.current?.click()}>📎</button><input ref={chatFile} type="file" hidden multiple onChange={e=>{[...(e.target.files||[])].forEach(attach);e.target.value=''}}/></>}
     <button className={'tool'+(dict.listening?' on':'')} aria-label="Dictate" title={'Dictate with '+HEARING_TEXT[dict.hearing]} disabled={dict.working} onClick={dict.toggle}>🎙</button>
     <button className="chat-send" disabled={!text.trim()||busy} onClick={send} aria-label="Send" title="Enter to send · Shift+Enter for a new line">{busy?'…':'↑'}</button></div>
   </div>
   <p className="chat-hint">Enter sends · Shift+Enter new line · a number picks that option · ↑ edits your last message · / for commands</p>
  </div></div>
 </section>;
}
type MRound={round:number;feedback:string;notes:string;by:string;created:string;html:string;image?:string};
type Mock={id:number;task_id:number|null;idea_id?:number|null;idea_title?:string|null;title:string;brief:string;section:string;status:string;task_title?:string|null;rounds:MRound[];prompt?:string;created?:string;updated?:string};
type MView={choose:{task:number;title:string;owner:string;section:string}[];mockups:Mock[];sections:string[]};
const MOCK_STATUS:Record<string,string>={open:'waiting for your mockup or approval',approved:'approved',skipped:'skipped: built without one',set_aside:'set aside'};
// Visual first (#147): a mockup of a UI feature before it is built. Since 2026-10-25 the owner makes the picture in Claude or
// ChatGPT (Atlas writes the request) and brings it back here; each one is a round, and the builder waits for approval.
const MOCK_APPS={claude:{name:'Claude',url:(q:string)=>'https://claude.ai/new?q='+encodeURIComponent(q)},chatgpt:{name:'ChatGPT',url:(q:string)=>'https://chatgpt.com/?q='+encodeURIComponent(q)}} as const;
type MockApp=keyof typeof MOCK_APPS;
const toBase64=(buf:ArrayBuffer)=>{let s='';const b=new Uint8Array(buf);for(let i=0;i<b.length;i+=0x8000)s+=String.fromCharCode(...b.subarray(i,i+0x8000));return btoa(s)};
function MockupMsg({m,sections,busy,act}:{m:Mock;sections:string[];busy:string;act:(key:string,body:object)=>Promise<boolean>}){
 // an open mockup is worked on in the chat; a decided one is one line that can be opened again
 const [show,setShow]=useState(false);
 if(m.status==='open')return <MockupCard m={m} sections={sections} busy={busy} act={act}/>;
 return <div className="bmsg-body mock-done"><p><b>{m.task_id?`#${m.task_id} `:''}{m.title}</b> · mockup {MOCK_STATUS[m.status]||m.status}{m.rounds.length>0&&<> · {m.rounds.length} round{m.rounds.length>1?'s':''}</>}</p>
  <div className="bmsg-actions">{m.status==='approved'&&m.rounds.length>0&&<button className="linkish" onClick={()=>setShow(x=>!x)}>{show?'Hide':'Show'} it</button>}<button className="linkish" disabled={!!busy} title={m.task_id?'Change it again; the task waits for your approval again':'Change it again'} onClick={()=>act('reopen'+m.id,{mockup:m.id,do:'reopen'})}>Reopen</button></div>
  {show&&<MockupRound m={m} r={m.rounds[m.rounds.length-1]}/>}</div>;
}
function MockupFrame({html,title}:{html:string;title:string}){
 // sandbox without permissions: no scripts, no forms, no same-origin access; the page also carries its own CSP
 const box=useRef<HTMLDivElement>(null),[scale,setScale]=useState(0.5);
 useEffect(()=>{const el=box.current;if(!el)return;const fit=()=>setScale(el.clientWidth/1280||0.5);fit();const o=new ResizeObserver(fit);o.observe(el);return()=>o.disconnect()},[]);
 return <div className="mock-frame" ref={box}><iframe sandbox="" srcDoc={html} title={'Mockup: '+title} loading="lazy" style={{transform:`scale(${scale})`}}/></div>;
}
const mockPics=new Map<string,string>();
function MockupRound({m,r}:{m:Mock;r:MRound}){
 const key=m.id+':'+r.round,[src,setSrc]=useState(mockPics.get(key)||''),[err,setErr]=useState(''),[big,setBig]=useState(false);
 useEffect(()=>{if(!r.image||mockPics.has(key)){setSrc(mockPics.get(key)||'');return}let live=true;
  fetch(`/api/engine/engine.brain.mockups?image=${m.id}&round=${r.round}`).then(x=>x.json()).then(j=>{const u=`data:${j.result.type};base64,${j.result.data}`;mockPics.set(key,u);if(live)setSrc(u)}).catch(()=>live&&setErr('The picture could not be loaded.'));
  return()=>{live=false}},[key,r.image]);
 if(!r.image)return r.html?<MockupFrame html={r.html} title={m.title}/>:<p className="build-idle">This round has no picture.</p>;
 if(err)return <p className="build-idle">{err}</p>;
 return <>{src?<button className="mock-pic" title="Show it full size" onClick={()=>setBig(true)}><img src={src} alt={`Mockup of ${m.title}, round ${r.round}`}/></button>:<p className="build-idle">Loading the picture…</p>}
  {big&&src&&<div className="mock-zoom" role="dialog" aria-label="Mockup full size" onClick={()=>setBig(false)} onKeyDown={e=>e.key==='Escape'&&setBig(false)} tabIndex={-1} ref={el=>el?.focus()}><img src={src} alt=""/><small>Click or press Esc to close</small></div>}</>;
}
function MockupCard({m,sections,busy,act}:{m:Mock;sections:string[];busy:string;act:(key:string,body:object)=>Promise<boolean>}){
 const [pick,setPick]=useState(m.rounds.length),[text,setText]=useState(''),[from,setFrom]=useState<MockApp|'upload'>('upload'),[hint,setHint]=useState(''),[over,setOver]=useState(false),[html,setHtml]=useState('');
 const file=useRef<HTMLInputElement>(null);
 useEffect(()=>setPick(m.rounds.length),[m.rounds.length]);
 const r=m.rounds[pick-1]||m.rounds[m.rounds.length-1];
 const add=(s:string)=>setText(t=>t?t.replace(/\s*$/,'\n')+s:s);
 const request=()=>(m.prompt||'')+(text.trim()?`\n\nNow change this compared with the previous version: ${text.trim()}`:'');
 const make=async(app:MockApp)=>{const q=request();let copied=false;try{await navigator.clipboard.writeText(q);copied=true}catch{}
  window.open(MOCK_APPS[app].url(q),'_blank','noopener');setFrom(app);
  setHint(`${MOCK_APPS[app].name} opened with the request${copied?' (also copied, in case it does not fill in)':''}. When the mockup is there, copy the picture and paste it here with Ctrl+V, or drop the file.`)};
 const send=async(data:string,type:string)=>{if(await act('add'+m.id,{mockup:m.id,data,type,by:from,feedback:text})){setText('');setHtml('');setHint('')}};
 const take=async(f:File|Blob|null|undefined)=>{if(!f)return;if(f.size>10*1024*1024){setHint('That file is over 10 MB.');return}
  if(!/^image\/(png|jpeg|webp|gif)$/.test(f.type)&&!/html|text/.test(f.type)){setHint('Use a PNG, JPEG, WebP or GIF picture, or an HTML page.');return}
  await send(toBase64(await f.arrayBuffer()),f.type)};
 const paste=(e:React.ClipboardEvent)=>{const it=[...e.clipboardData.items].find(i=>i.type.startsWith('image/'));if(it){e.preventDefault();take(it.getAsFile())}};
 const fromClipboard=async()=>{try{const items=await (navigator.clipboard as any).read();for(const i of items){const t=i.types.find((x:string)=>x.startsWith('image/'));if(t){await take(await i.getType(t));return}}setHint('No picture on the clipboard. Copy the image in Claude or ChatGPT first (right-click › Copy image).')}catch{setHint('The browser did not allow reading the clipboard; press Ctrl+V on the drop area instead.')}};
 const sendHtml=()=>send(toBase64(new TextEncoder().encode(html).buffer as ArrayBuffer),'text/html');
 return <article className="engine-item mock-card" onPaste={paste}><div className="ei-head"><strong>{m.task_id?`#${m.task_id} `:''}{m.title}</strong>{m.idea_id&&<span className="chip">for idea {m.idea_id}</span>}{m.section&&<span className="chip">sits in {m.section}</span>}<small className="ei-key">mockup #{m.id}</small></div>
  {m.rounds.length>1&&<div className="note-dest inline" role="radiogroup" aria-label="Round">{m.rounds.map(x=><button key={x.round} role="radio" aria-checked={pick===x.round} className={'chip small'+(pick===x.round?' on':'')} title={x.feedback||'First version'} onClick={()=>setPick(x.round)}>Round {x.round}</button>)}</div>}
  {r?<><MockupRound m={m} r={r}/><p className="ei-why">{r.feedback&&<><b>You asked:</b> {r.feedback}<br/></>}{r.notes}{r.by==='rule'&&<span className="chip small">wireframe</span>}</p></>:<p className="build-idle">No mockup yet. Make it in Claude or ChatGPT: Atlas writes the request with its look, the section and the feature.</p>}
  <div className="mock-feedback">
   {r&&<><div className="starters small"><button className="chip small" onClick={()=>add('More of this: ')}>More of this…</button><button className="chip small" onClick={()=>add('Less of that: ')}>Less of that…</button>
    <select aria-label="How would it look in another section" value="" onChange={e=>e.target.value&&add(`How would it look in ${e.target.value}?`)}><option value="">How would it look in…</option>{sections.filter(x=>x!==m.section).map(x=><option key={x} value={x}>{x}</option>)}</select></div>
    <textarea value={text} placeholder="What to change for the next round (optional; it goes into the request)" aria-label={'Feedback on '+m.title} onChange={e=>setText(e.target.value)}/></>}
   <div className="ei-actions"><button className="magic" disabled={!m.prompt} title="Opens Claude with the request filled in" onClick={()=>make('claude')}>{r?'Next round in Claude':'Make it in Claude'}</button>
    <button disabled={!m.prompt} title="Opens ChatGPT with the request filled in" onClick={()=>make('chatgpt')}>{r?'…in ChatGPT':'Make it in ChatGPT'}</button>
    <button className="linkish" disabled={!m.prompt} title="Copy the request only" onClick={async()=>{try{await navigator.clipboard.writeText(request());setHint('Request copied.')}catch{setHint('Copy was not allowed by the browser.')}}}>Copy the request</button></div>
   {hint&&<p className="save-status" role="status">{hint}</p>}
   <div className={'mock-drop'+(over?' over':'')} tabIndex={0} role="button" aria-label="Add the mockup: paste with Ctrl+V, drop a file, or choose one"
    onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);take(e.dataTransfer.files[0])}}
    onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();file.current?.click()}}}>
    {busy==='add'+m.id?<b>Adding…</b>:<><b>Add the mockup</b><small>Paste the picture (Ctrl+V), drop it here, or <button className="linkish" onClick={()=>file.current?.click()}>choose a file</button> · <button className="linkish" onClick={fromClipboard}>from the clipboard</button></small></>}
    <input ref={file} type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif,.html,.htm" onChange={e=>{take(e.target.files?.[0]);e.target.value=''}}/></div>
   <details><summary>Got HTML instead of a picture?</summary><textarea value={html} placeholder="Paste the HTML page here (scripts and outside links are removed)" aria-label="Mockup HTML" onChange={e=>setHtml(e.target.value)}/>
    <button disabled={html.trim().length<50||!!busy} onClick={sendHtml}>Add this page</button></details>
   <div className="ei-actions"><button className="approve" disabled={!!busy||!r} title={m.task_id?'The builder may take the task now and builds to match this mockup':'Keep this as the agreed look'} onClick={()=>act('ok'+m.id,{mockup:m.id,do:'approve'})}>Approve{m.task_id?': build this':''}</button>
    <button className="deny" disabled={!!busy} title={m.task_id?'Kept, not deleted; the task is built without a mockup':'Kept, not deleted'} onClick={()=>act('aside'+m.id,{mockup:m.id,do:'set_aside'})}>Set aside</button></div></div>
 </article>;
}
function ForgePlan({dir,hub}:{dir:Dir;hub:Hub|null}){
 const {focus:f,zoom,current}=dir;
 const [only,setOnly]=useState(true),[tree,setTree]=useState<PlanTree|null>(null),[busy,setBusy]=useState(false),[err,setErr]=useState(''),[replanning,setReplanning]=useState(false),[openDraft,setOpenDraft]=useState<number|null>(null);
 const {toast,run,undo}=useUndoable();
 const canTree=hubHas(hub,'engine.build.plan_tree');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.build.plan_tree');if(r.ok)setTree((await r.json()).result)}catch{}};
 useEffect(()=>{if(canTree)load()},[canTree]);
 const act=async(action:string,body:object)=>{setBusy(true);setErr('');try{await run(action,body)}catch(e){setErr('Not changed: '+(e as Error).message)}finally{setBusy(false);load();dir.reload()}};
 if(!f)return <p className="build-idle">The plan comes from the Engine on the Pi; it appears here when the hub is online.</p>;
 const find=(gs:PGoal[],id:number):PGoal|null=>{for(const g of gs){if(g.id===id)return g;const c=find(g.children,id);if(c)return c}return null};
 const roots=tree?(current&&only?[find(tree.goals,current.id)].filter(Boolean) as PGoal[]:tree.goals):[];
 const drafts:PTask[]=[];const collect=(gs:PGoal[])=>gs.forEach(g=>{g.tasks.forEach(t=>t.status==='proposed'&&drafts.push(t));collect(g.children)});if(tree){collect(tree.goals);tree.loose.forEach(t=>t.status==='proposed'&&drafts.push(t))}
 const ins=zoom?.insights;
 const block=(title:string,items?:string[])=>items&&items.length>0&&<div className="dir-block"><h5>{title}</h5><ul>{items.map((x,i)=><li key={i}>{x}</li>)}</ul></div>;
 return <>
  <div className="inherit">{current?<>Focus: <b>{current.title}</b> <small>vision chosen in Focus; its work ranks first</small></>:<>No focus chosen: <small>everything ranks on its own merits. Choose a vision in Focus.</small></>}
   {current&&<span className="note-dest inline"><button className={'chip'+(only?' on':'')} onClick={()=>setOnly(true)}>In focus</button><button className={'chip'+(!only?' on':'')} onClick={()=>setOnly(false)}>Everything</button></span>}</div>
  {err&&<p className="save-status" role="alert">{err}</p>}
  {toast&&<div className="toast" role="status">{toast.msg.split('\n')[0]}{toast.changes.length>0&&<button onClick={()=>undo(load)}>Undo</button>}</div>}
  <div className="plan-wide">
   <div className="dir-head"><h4 className="local-title">Plan <small>{current&&only?'everything under the vision in focus':'every goal and task'}</small></h4>
    {hubHas(hub,'engine.build.do_over')&&<button className="small-btn" disabled={replanning} title="Claude re-checks the whole open plan and the open ideas against your visions; a keep/change/drop diff waits for your approval in Waiting on you" onClick={async()=>{setReplanning(true);setErr('');try{await run('engine.build.do_over',{})}catch(e){setErr((e as Error).message)}finally{setReplanning(false);load()}}}>{replanning?'Re-evaluating…':'Re-evaluate the whole plan'}</button>}{hubHas(hub,'engine.system.revisit')&&<button className="small-btn" disabled={replanning} title="Claude reviews where every function of Atlas lives (moves, merges, removals); proposals appear here as draft tasks" onClick={async()=>{setReplanning(true);setErr('');try{await run('engine.system.revisit',{placement:true})}catch(e){setErr((e as Error).message)}finally{setReplanning(false);load()}}}>Review where functions live</button>}</div>
   {drafts.length>0&&<div className="q-block proposals"><h5>Proposals to accept <small>from the Engine's reviews; nothing happens until you accept</small></h5><ul className="queue">{drafts.map(t=><li key={t.id}><div className="ei-head"><strong>#{t.id} {t.title}</strong><WhyPopup id={t.id}/><span className="chip">{OWNER[t.owner]||t.owner}</span></div><div className="ei-actions"><button className="approve" disabled={busy} onClick={()=>act('engine.build.task_status',{task:t.id,status:'todo'})}>Accept</button><button className="deny" disabled={busy} onClick={()=>act('engine.build.task_status',{task:t.id,status:'dropped'})}>Delete</button><button className="linkish" onClick={()=>setOpenDraft(openDraft===t.id?null:t.id)}>{openDraft===t.id?'Hide why':'Why?'}</button></div>{openDraft===t.id&&<TaskDetail id={t.id}/>}</li>)}</ul></div>}
   {!canTree?<Outdated what="The plan tree"/>:!tree?<p className="build-idle">Loading…</p>:!roots.length?<p className="build-idle">{current&&only?'Nothing under this vision yet: add an objective in Focus, then goals and tasks here.':'No goals yet.'}</p>:
    <ul className="pgoals root">{roots.map(g=><GoalNode key={g.id} g={g} act={act} busy={busy} depth={0}/>)}</ul>}
   {tree&&(!current||!only)&&tree.loose.length>0&&<details className="q-block"><summary>Tasks under no goal ({tree.loose.length})</summary><ul className="ptasks">{tree.loose.slice(0,40).map(t=><PlanTask key={t.id} t={t} act={act} busy={busy}/>)}</ul></details>}
   {zoom&&<details className="q-block leads"><summary>Where the work leads</summary>{block('Heading towards',ins?.heading)}{block('For you to do',ins?.yourself)}{block('To steer',ins?.steer)}{block('Needs your thinking',ins?.think)}</details>}
  </div>
 </>;
}
type Explored={summary:string;fits:{id:number;title:string;how:string}[];value:string;new_start:string;overlaps:string[];first_step:string;risks:string;take_as:'task'|'goal'|'later';under:number|null;title:string;why:string};
type Idea={id:number;title:string;category:string;description:string;impact:number;effort_h:number;place:{goal_id:number|null;goal:string;new_goal:string;why:string;by:string}|null;overlap:{verdict:string;why?:string}|null;explored?:Explored|null;new?:boolean};
type IdeasView={ideas:Idea[];goals:{id:number;title:string;horizon:string}[]};
function IdeaCard({i,goals,act,explore}:{i:Idea;goals:IdeasView['goals'];act:(id:number,action:string,body:object)=>void;explore:(id:number)=>Promise<void>}){
 const p=i.place,e=i.explored;const [goal,setGoal]=useState<number|''>(p?.goal_id||'');const [title,setTitle]=useState(p?.new_goal||i.title);
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false);
 const goalName=(id:number|null)=>goals.find(g=>g.id===id)?.title;
 const suggestion=e?(e.take_as==='later'?`Not now: ${e.why}`:e.take_as==='goal'?`As a new goal “${e.title}”${e.under?` under ${goalName(e.under)||'its vision'}`:''}`:`As a task under “${goalName(e.under)||'its goal'}”`):p?(p.goal_id?`As a task under “${p.goal}”`:'As a new goal'):'';
 return <li className={'engine-item idea-card'+(open?' open':'')+(i.new?' is-new':'')}><div className="ei-head"><span className="chip">{i.category}</span><strong>{i.title}</strong><NewBadge on={i.new}/><small className="ei-key">#{i.id}</small></div>
  {i.description&&<p className="ei-why">{i.description.split('\n')[0].slice(0,240)}</p>}
  {e&&!open&&<p className="idea-gist">{e.summary}</p>}
  {open&&(e?<div className="exploration">
    <p className="ex-sum">{e.summary}</p>
    {e.fits.length>0&&<div><h6>Serves</h6><ul>{e.fits.map((f,k)=><li key={k}><b>{f.title}</b> {f.how}</li>)}</ul></div>}
    {e.value&&<div><h6>Why it is worth having</h6><p>{e.value}</p></div>}
    {e.new_start&&<div><h6>Could start something new</h6><p>{e.new_start}</p></div>}
    {e.overlaps.length>0&&<div><h6>Already in Atlas</h6><ul>{e.overlaps.map((o,k)=><li key={k}>{o}</li>)}</ul></div>}
    {e.first_step&&<div><h6>First step</h6><p>{e.first_step}</p></div>}
    {e.risks&&<div><h6>Risks</h6><p>{e.risks}</p></div>}
   </div>:<p className="build-idle">{busy?'Exploring with Claude… this takes a little while.':'Not explored yet.'}</p>)}
  {suggestion&&<p className="idea-place"><b>{e?'Claude suggests':'Fits'}</b> {suggestion}{!e&&p?.why?<><br/><small>{p.why}</small></>:null}</p>}
  <div className="ei-actions"><button disabled={busy} onClick={async()=>{setOpen(true);if(!e){setBusy(true);try{await explore(i.id)}finally{setBusy(false)}}else setOpen(!open)}}>{busy?'Exploring…':e?(open?'Hide exploration':'Show exploration'):'Explore'}</button>
   {e&&<button disabled={busy} title="Explore again with the plan as it is now" onClick={async()=>{setBusy(true);try{await explore(i.id)}finally{setBusy(false)}}}>Explore again</button>}
   <button className="approve" disabled={busy||e?.take_as==='later'} title="The AI decides: a task under the goal it serves, or a new goal under a vision" onClick={()=>act(i.id,'engine.build.idea_to_plan',{idea:i.id,take:true})}>Take it</button>
   <button className="deny" disabled={busy} onClick={()=>act(i.id,'engine.build.edit_plan',{drop_idea:i.id})}>Set aside</button></div>
  <details className="choose-self"><summary>Choose myself</summary>
   <div className="ei-actions"><select value={goal} onChange={e=>setGoal(e.target.value?Number(e.target.value):'')} aria-label="Goal">{!p?.goal_id&&<option value="">Choose a goal…</option>}{goals.map(g=><option key={g.id} value={g.id}>{g.title}</option>)}</select><button disabled={!goal} onClick={()=>act(i.id,'engine.build.idea_to_plan',{idea:i.id,goal})}>As a task there</button></div>
   <div className="ei-actions"><input value={title} onChange={e=>setTitle(e.target.value)} aria-label="New goal title"/><button onClick={()=>act(i.id,'engine.build.idea_to_plan',{idea:i.id,new_goal:{title}})}>As a new goal</button></div></details>
 </li>;
}
function Forge({hub,dir,build,want,builder,tasks,paths}:{hub:Hub|null;dir:Dir;build:Build|null;want?:{stage:'ideas'|'plan'|'build';n:number}|null;builder:Builder;tasks:Task[];paths:Path[]}){
 const [stage,setStage]=useState<'ideas'|'plan'|'build'>('ideas'),[view,setView]=useState<IdeasView|null>(null),[hidden,setHidden]=useState<Set<number>>(new Set()),[note,setNote]=useState(''),[placing,setPlacing]=useState(false);
 useEffect(()=>{if(want)setStage(want.stage)},[want?.n]);
 const ok=hubHas(hub,'engine.build.ideas');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.build.ideas');if(r.ok)setView((await r.json()).result)}catch{}};
 useEffect(()=>{if(!ok){setView(null);return}load();const t=setInterval(load,60000);return()=>clearInterval(t)},[ok]);
 const call=post;
 async function act(id:number,action:string,body:object){setHidden(h=>new Set(h).add(id));setNote('');
  try{setNote(await call(action,body));await load()}catch(e){setNote('Not changed: '+(e as Error).message)}finally{setHidden(h=>{const n=new Set(h);n.delete(id);return n})}}
 async function place(){setPlacing(true);setNote('Asking Claude where each idea fits…');try{setNote(await call('engine.build.place_ideas',{}));await load()}catch(e){setNote((e as Error).message)}finally{setPlacing(false)}}
 const ideas=view?.ideas.filter(i=>!hidden.has(i.id))||[];
 return <section className="panel page forge"><h2>Forge</h2><p>The workshop: ideas become part of the plan, the plan becomes builds.</p>
  <div className="stations" role="tablist" aria-label="Forge stages">{([['ideas','Ideas','✧','where each one fits'],['plan','Plan','▤','everything to build'],['build','Build','⚒','what is being built']] as const).map(([k,l,icon,sub])=><button key={k} role="tab" aria-selected={stage===k} className={'station'+(stage===k?' on':'')+(k==='build'&&(builder.s?.lanes.some(x=>x.state==='running')||build?.state==='building')?' hot':'')} onClick={()=>setStage(k)}><span className="st-icon">{icon}</span><b>{l}</b><small>{sub}</small></button>)}</div>
  {stage==='plan'?<ForgePlan dir={dir} hub={hub}/>:stage==='build'?<ForgeBuild hub={hub} builder={builder} build={build} tasks={tasks} paths={paths} dir={dir} onPlan={()=>setStage('plan')}/>:
   !ok?(hub?.reachable?<Outdated what="Forge › Ideas"/>:<p className="build-idle">Ideas come from the Engine on the Pi; they appear here when the hub is online.</p>):
   <><div className="forge-bar"><p>A brainstorming room: explore an idea to see what it could mean for you, then take it (Claude decides whether it becomes a task or a goal) or set it aside.</p><button disabled={placing} onClick={place}>{placing?'Asking…':'Ask Claude where these fit'}</button></div>
    {note&&<p className="save-status" role="status">{note}</p>}
    {!view?<p className="build-idle">Loading…</p>:!ideas.length?<p className="build-idle">No open ideas. New ones arrive from ✦ notes, the Cortex chat, Telegram and research.</p>:<ul className="idea-list">{ideas.map(i=><IdeaCard key={i.id} i={i} goals={view.goals} act={act} explore={async(id:number)=>{setNote('');try{await call('engine.build.explore_idea',{idea:id});await load()}catch(e){setNote('Could not explore: '+(e as Error).message)}}}/>)}</ul>}</>}
 </section>;
}
type Hit={kind:'function'|'atlas task'|'task'|'idea'|'note'|'screen';id:string;title:string;sub:string;score:number;go:()=>void};
function score(q:string,text:string){const t=text.toLowerCase(),w=q.toLowerCase().split(/\s+/).filter(Boolean);if(!w.length)return 0;let s=0;for(const x of w){const i=t.indexOf(x);if(i<0)return 0;s+=i===0?3:t[i-1]===' '?2:1}return s+(t.startsWith(q.toLowerCase())?3:0)}
const SPEAK_VIEWS=['Today','Focus','Forge › Ideas','Forge › Plan','Forge › Build','Forge › Build › Queue','Cortex','Notes','Map','Research','Resources','Memory','Systems'];
type Proposal={label:string;why:string;action:string;body:object;view:string;highlight:string;steps?:{action:string;body:object}[]};
// Mark the item a proposal is about: the deepest element in the workspace that contains the text; a folded
// section around it is opened. Retries for a few seconds, since a section loads after it is opened.
function highlightText(text:string,tries=10){
 document.querySelectorAll('.cx-highlight').forEach(e=>e.classList.remove('cx-highlight'));
 const want=text.trim().toLowerCase();const main=document.querySelector('main');if(!want||!main)return;
 const hits=[...main.querySelectorAll<HTMLElement>('*')].filter(el=>(el.textContent||'').toLowerCase().includes(want)&&![...el.children].some(c=>(c.textContent||'').toLowerCase().includes(want)));
 for(const hit of hits){
  for(let d=hit.closest('details');d;d=d.parentElement?.closest('details')||null)(d as HTMLDetailsElement).open=true;
  if(!hit.offsetParent)continue;
  const target=hit.closest<HTMLElement>('li,article,tr,.engine-item,.ptask,.mock-card')||hit;
  target.classList.add('cx-highlight');target.scrollIntoView({block:'center',behavior:'smooth'});
  setTimeout(()=>target.classList.remove('cx-highlight'),9000);return}
 if(tries>0)setTimeout(()=>highlightText(text,tries-1),300)}
// Proposals from Cortex, shared by the ✦ button and the Cortex chat: run one only after yes / its number, with Undo.
function useProposals(go:(label:string)=>void,onChanged:()=>void,preview:boolean){
 const [props,setProps]=useState<Proposal[]>([]),[sel,setSel]=useState(0),[busy,setBusy]=useState(false),[done,setDone]=useState(''),[changes,setChanges]=useState<number[]>([]),[used,setUsed]=useState<number[]>([]);
 const show=(p:Proposal|undefined)=>{if(!p)return;const id=/#(\d+)/.exec(p.highlight||'');if(p.view)go(p.view==='Forge › Build › Queue'&&id?`${p.view} #${id[1]}`:p.view);if(p.highlight)setTimeout(()=>highlightText(p.highlight),p.view?450:0)};
 const offer=(ps:Proposal[])=>{setProps(ps);setUsed([]);setSel(0);setDone('');setChanges([]);if(preview)show(ps[0])};
 const run=async(i:number)=>{const p=props[i];if(!p||busy||used.includes(i))return;setSel(i);
  if(!p.action){show(p);setDone('Shown.');return}
  setBusy(true);try{const send=(id:string,body:object)=>fetch('/api/engine/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   let r=await send('engine.brain.cortex_apply',{proposal:p});  // bundles: all or nothing, one Undo (#186)
   if(r.status===404&&!p.steps)r=await send(p.action,p.body);  // an Engine from before #186
   const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');
   setDone(`${i+1}. ${p.label}: `+(typeof b.message==='string'?b.message:'done.'));setChanges(b.changes||[]);setUsed(u=>[...u,i]);onChanged();  // the other options stay open
   if(preview){if(p.view)setTimeout(()=>go(p.view),50);if(p.highlight)setTimeout(()=>highlightText(p.highlight),700)}}
  catch(e){setDone('Not done: '+(e as Error).message)}finally{setBusy(false)}};
 const undo=async()=>{const c=changes;setChanges([]);try{setDone(await post('engine.brain.undo_changes',{changes:c}));onChanged()}catch(e){setDone('Undo failed: '+(e as Error).message)}};
 const decline=()=>{setProps([]);setUsed([]);setDone('Nothing done.');document.querySelectorAll('.cx-highlight').forEach(e=>e.classList.remove('cx-highlight'))};
 return {props,sel,setSel,busy,done,setDone,changes,used,show,offer,run,undo,decline};
}
type Prop=ReturnType<typeof useProposals>;
function ProposalList({p,preview,offset=0}:{p:Prop;preview:boolean;offset?:number}){
 // numbered after the choices, so every number on screen means one thing (owner, 2026-10-25); click or press its number
 if(!p.props.length)return null;
 return <ol className="speak-props">{p.props.map((x,i)=><li key={i} className={(i===p.sel?'sel':'')+(p.used.includes(i)?' used':'')} onMouseEnter={()=>{p.setSel(i);if(preview)p.show(x)}}>
   <button className="speak-prop" disabled={p.busy||p.used.includes(i)} onClick={()=>p.run(i)} onFocus={()=>{p.setSel(i);if(preview)p.show(x)}}><kbd>{offset+i+1}</kbd><span><b>{p.used.includes(i)?'✓ ':''}{x.label}</b>{x.why&&<small>{x.why}</small>}{x.steps&&x.steps.length>1&&<small className="speak-where">{x.steps.length} steps, one Undo</small>}{x.view&&<small className="speak-where">{x.action?'in':'shows'} {x.view}</small>}</span></button>
   {!preview&&(x.view||x.highlight)&&<button className="linkish show-it" onClick={()=>p.show(x)}>Show</button>}</li>)}</ol>;
}
function Done({p}:{p:Prop}){return p.done?<p className="save-status" role="status">{p.done}{p.changes.length>0&&<button className="linkish" onClick={p.undo}>Undo</button>}</p>:null}
// Search hits you can step through: 1–5 opens one, ← → moves to the previous or next and opens it.
function HitList({hits,at,pick}:{hits:Hit[];at:number;pick:(i:number)=>void}){
 if(!hits.length)return <p className="build-idle">No match. Try other words.</p>;
 return <ol className="speak-hits">{hits.map((h,i)=><li key={h.kind+h.id} className={i===at?'sel':''}><button onClick={()=>pick(i)}><kbd>{i+1}</kbd><span><b>{h.title}</b><small>{h.kind==='atlas task'?'Atlas step':h.kind} · {h.sub}</small></span></button></li>)}</ol>;
}
// Keyboard shortcuts, set under Systems › Settings (kept in this browser). "" = not assigned.
const SHORTCUTS:{id:string;label:string;def:string}[]=[{id:'quick',label:'Open or close ✦ Quick Cortex',def:'Ctrl+J'},{id:'speak',label:'Open ✦ and start speaking',def:''},{id:'chat',label:'Open the Cortex chat',def:''}];
const readShortcuts=():Record<string,string>=>{let saved:Record<string,string>={};try{saved=JSON.parse(localStorage.getItem('atlas-shortcuts')||'{}')}catch{}return Object.fromEntries(SHORTCUTS.map(x=>[x.id,saved[x.id]??x.def]))};
const comboOf=(e:KeyboardEvent|React.KeyboardEvent)=>{const k=e.key.length===1?e.key.toUpperCase():e.key;if(['Control','Shift','Alt','Meta'].includes(k))return '';return [(e.ctrlKey?'Ctrl':''),(e.metaKey?'Meta':''),(e.altKey?'Alt':''),(e.shiftKey?'Shift':''),k].filter(Boolean).join('+')};
const goEvent=(label:string)=>window.dispatchEvent(new CustomEvent('atlas-go',{detail:label}));
type QuickKind='note'|'reminder'|'find'|'answer'|'do'|'local'|'waiting';
const KIND_LOOK:Record<QuickKind,string>={note:'Kept in Cortex',reminder:'Reminder set',find:'Found',answer:'',do:'Proposed',local:'Kept on this computer',waiting:'Waiting for Cortex'};
// Thoughts that could not reach the Engine wait here (this browser) and go to its drop box, in order, once it is back (#166).
const WAIT_KEY='atlas-waiting';
const readWaiting=():string[]=>{try{const v=JSON.parse(localStorage.getItem(WAIT_KEY)||'[]');return Array.isArray(v)?v.filter(x=>typeof x==='string'):[]}catch{return []}};
const writeWaiting=(l:string[])=>{try{l.length?localStorage.setItem(WAIT_KEY,JSON.stringify(l)):localStorage.removeItem(WAIT_KEY)}catch{}};
// The ✦ button (owner, 2026-10-25): one box; Cortex decides what the words are and the answer shows it by its look
// (kept in Cortex, a reminder, found, an answer, a proposal). The Cortex chat is the full place to think.
function QuickCortex({hub,view,go,pcVoice,onChanged,openChat}:{hub:Hub|null;view:string;go:(label:string)=>void;pcVoice:LocalVoice|null;onChanged:()=>void;openChat:()=>void}){
 const cx=useCortex(),[open,setOpenRaw]=useState(false),[text,setTextRaw]=useState(()=>{try{return localStorage.getItem('atlas-draft')||''}catch{return ''}});
 const [busy,setBusy]=useState(false),[asked,setAsked]=useState(''),[kind,setKind]=useState<QuickKind|null>(null),[reply,setReply]=useState(''),[choices,setChoices]=useState<string[]>([]),[find,setFind]=useState(''),[at,setAt]=useState(-1),[asks,setAsks]=useState(0),[turns,setTurns]=useState<{role:string;text:string}[]>([]);
 const input=useRef<HTMLTextAreaElement>(null),file=useRef<HTMLInputElement>(null),startSpeaking=useRef(false);
 const setText=(t:string|((o:string)=>string))=>setTextRaw(o=>{const n=typeof t==='function'?t(o):t;try{n?localStorage.setItem('atlas-draft',n):localStorage.removeItem('atlas-draft')}catch{}return n});  // the draft survives a reload
 const setOpen=(v:boolean|((o:boolean)=>boolean))=>setOpenRaw(o=>{const n=typeof v==='function'?v(o):v;if(!n){setAsks(0);setTurns([])}return n});  // a quick exchange is not saved unless you continue it in the chat
 const [waiting,setWaitingRaw]=useState<string[]>(readWaiting);const setWaiting=(l:string[])=>{writeWaiting(l);setWaitingRaw(l)};
 const p=useProposals(go,onChanged,true);
 const hits=useAtlasSearch(find,5);
 const canAsk=hubHas(hub,'engine.brain.speak_to'),canWrite=hubHas(hub,'engine.brain.write_thought');
 // the drop box takes the waiting thoughts once the Engine is back; each one leaves the queue only when the Engine has it
 useEffect(()=>{if(!canWrite||!waiting.length)return;(async()=>{let left=readWaiting();while(left.length){try{await post('engine.brain.write_thought',{text:left[0]})}catch{break}left=left.slice(1);setWaiting(left)}if(left.length<waiting.length)onChanged()})()},[hub,waiting.length]);
 const dict=useDictation({canWhisper:hubHas(hub,'engine.brain.voice_input'),canPC:!!pcVoice?.available,pcPreviews:pcVoice?.device==='cuda',append:t=>setText(b=>joinText(b,t)),say:p.setDone});
 const clear=()=>{setReply('');setChoices([]);setKind(null);p.offer([]);p.setDone('');setFind('');setAt(-1)};
 const pick=(i:number)=>{const h=hits[i];if(!h)return;setAt(i);h.go()};
 const keepLocal=async(t:string,remind:boolean)=>{const [first,...rest]=t.split('\n');const r=remind?await fetch('/api/reminders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:crypto.randomUUID(),title:first.slice(0,300),detail:rest.join('\n').trim()})}):await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:crypto.randomUUID(),body:t})});if(!r.ok)throw Error('Save failed');onChanged()};
 const sendWhenHeard=useRef(false);  // Send while recording: the transcript of this recording goes in, then the words are sent once
 const send=async(t=text)=>{if(dict.listening){sendWhenHeard.current=true;dict.toggle();return}const x=t.trim();if(!x||busy)return;clear();setAsked(x);
  const fq=findQuery(x);if(fq){setKind('find');setFind(fq);setText('');return}  // plainly a search: no model needed
  setBusy(true);
  try{if(!canAsk){setWaiting([...readWaiting(),x]);setKind('waiting');setReply(`${cx} is not reachable. The thought waits here and goes to the Engine's drop box when it is back.`);setText('');return}
   const main=document.querySelector('main') as HTMLElement|null;const visible=(main?.innerText||'').replace(/\n{3,}/g,'\n\n').slice(0,7000);
   const label=view==='Forge'?'Forge › '+((document.querySelector('main [role=tab][aria-selected=true]') as HTMLElement|null)?.innerText.trim()||'Plan'):view;
   const r=await fetch('/api/engine/engine.brain.speak_to',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:x,view:label,visible,views:SPEAK_VIEWS,history:turns.slice(-8)})});const b=await r.json();if(!r.ok)throw Error(b.error||'The Engine refused');
   const m=b.message,k:QuickKind=m.kind||((m.proposals||[]).length?'do':'answer');setText('');setKind(k);
   const said=[m.reply||'',...(m.choices||[]).map((c:string,i:number)=>`${i+1}. ${c}`),...(m.proposals||[]).map((q:Proposal,i:number)=>`${(m.choices||[]).length+i+1}. ${q.label}`)].filter(Boolean).join('\n');
   setTurns(t=>[...t,{role:'me',text:x},{role:'claude',text:said||(k==='reminder'?'Reminder: '+(m.reminder||x):k==='find'?'Search: '+(m.query||x):'')}]);
   if(k==='reminder'){await keepLocal(m.reminder||x,true);setReply(m.reminder||x)}
   else if(k==='find'){setFind(m.query||x)}
   else{setReply(m.reply||'');setChoices(m.choices||[]);p.offer(m.proposals||[]);if(k==='note')onChanged();if(k==='answer'||k==='do')setAsks(n=>n+1)}}
  catch(e){if(e instanceof TypeError){setWaiting([...readWaiting(),x]);setKind('waiting');setReply(`${cx} did not answer. The thought waits here and goes to the Engine's drop box when it is back.`);setText('')}else{setKind(null);setReply('Not sent: '+(e as Error).message+'. Your words are kept.')}}finally{setBusy(false)}};
 const attach=async(f:File|undefined)=>{if(!f)return;if(f.size>10*1024*1024){p.setDone('Files up to 10 MB.');return}setBusy(true);clear();
  try{const data=await readBase64(f);
   if(!hubHas(hub,'engine.brain.ingest')){const r=await fetch('/api/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.name,mediaType:mediaTypeOf(f),data})});const b=await r.json();if(!r.ok)throw Error(b.error);setKind('local');setReply(`${f.name} kept on this computer (Notes › Local sources).`)}
   else{setKind('note');setReply(await post('engine.brain.ingest',{name:f.name,data,note:text.slice(0,500)}));setText('')}}catch(e){p.setDone('Not sent: '+(e as Error).message)}finally{setBusy(false)}};
 const hitList=find?hits:[];
 const keepIt=async()=>{try{await post('engine.brain.speak_to',{keep:turns});setTurns([]);setOpen(false);openChat()}catch(e){p.setDone('Not kept: '+(e as Error).message)}};
 // shortcuts from Systems › Settings; inside the panel: 1–5 picks (a hit, a choice or a proposal), ← → steps through hits, Y/N for proposals
 useEffect(()=>{const k=(e:KeyboardEvent)=>{
  const combo=comboOf(e),sc=readShortcuts();
  if(combo&&combo===sc.quick){e.preventDefault();setOpen(o=>!o);return}
  if(combo&&combo===sc.speak){e.preventDefault();setOpen(true);if(!dict.listening)startSpeaking.current=true;return}
  if(combo&&combo===sc.chat){e.preventDefault();setOpen(false);openChat();return}
  if(!open||e.ctrlKey||e.metaKey||e.altKey)return;
  const t=e.target as HTMLElement;const typing=['INPUT','TEXTAREA','SELECT'].includes(t.tagName)&&!(t===input.current&&!text);
  if(e.key==='Escape'){if(p.props.length)p.decline();else setOpen(false);return}
  if(typing)return;
  const n=/^[1-9]$/.test(e.key)?+e.key-1:-1;
  if(n>=0&&n<choices.length){e.preventDefault();send(choices[n]);return}  // one numbering: the choices, then the proposals
  if(n>=0&&p.props[n-choices.length]){e.preventDefault();p.run(n-choices.length);return}
  if(p.props.length&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();const j=(p.sel+(e.key==='ArrowDown'?1:p.props.length-1))%p.props.length;p.setSel(j);p.show(p.props[j]);return}
  if(hitList.length){if(n>=0&&hitList[n]){e.preventDefault();pick(n)}else if(e.key==='ArrowRight'||e.key==='ArrowDown'){e.preventDefault();pick((at+1)%hitList.length)}else if(e.key==='ArrowLeft'||e.key==='ArrowUp'){e.preventDefault();pick((at-1+hitList.length)%hitList.length)}}};
  window.addEventListener('keydown',k);return()=>window.removeEventListener('keydown',k)},[open,p.props,p.sel,text,choices,hitList,at,busy,dict.listening]);
 useEffect(()=>{if(!open)return;setTimeout(()=>input.current?.focus(),30);if(startSpeaking.current){startSpeaking.current=false;if(!dict.listening)dict.toggle()}},[open]);
 useEffect(()=>{if(open&&startSpeaking.current){startSpeaking.current=false;if(!dict.listening)dict.toggle()}});
 useEffect(()=>{if(sendWhenHeard.current&&!dict.listening&&!dict.working){sendWhenHeard.current=false;send()}});
 const look=kind?KIND_LOOK[kind]:'';
 return <>
  <button className={'magic-btn'+(open?' on':'')} aria-expanded={open} title={`Quick ${cx} (${readShortcuts().quick||'no shortcut'})`} onClick={()=>setOpen(o=>!o)}><span className="magic-star" aria-hidden="true">✦</span><span className="magic-label">{cx}</span></button>
  {open&&<div className="speak-panel" role="dialog" aria-label={`Quick ${cx}`}>
   <div className="speak-head"><b>✦ {cx}</b><button className="linkish to-chat" title={`The full ${cx} chat: think things through, every function`} onClick={()=>{setOpen(false);openChat()}}>Open the chat →</button><button className="linkish" aria-label="Close" onClick={()=>setOpen(false)}>×</button></div>
   {asked&&(busy||kind||reply)&&<p className="speak-asked">“{asked}”</p>}
   {busy&&<div className="typing-dots"><i/><i/><i/></div>}
   {kind&&<div className={'speak-result k-'+kind}>{look&&<span className="k-tag">{look}</span>}
    {kind==='find'?<>{hitList.length?<HitList hits={hitList} at={at} pick={pick}/>:<p className="build-idle">No match for “{find}”.</p>}{hitList.length>1&&<small className="speak-keys">1–{hitList.length} opens · ← → next and previous</small>}</>
    :reply&&<Md text={reply}/>}
    {choices.length>0&&<div className="speak-choices">{choices.map((c,i)=><button key={i} className="chip" onClick={()=>send(c)}><kbd>{i+1}</kbd>{c}</button>)}</div>}</div>}
   {!kind&&reply&&<p className="save-status">{reply}</p>}
   {waiting.length>0&&<p className="save-status" role="status">{waiting.length} thought{waiting.length>1?'s':''} waiting for {cx}; sent when it is back.</p>}
   <ProposalList p={p} preview={true} offset={choices.length}/>
   <Done p={p}/>
   {!busy&&turns.length>0&&(asks>=2||choices.length>0)&&<button className="to-chat-line" title="Only then is this exchange saved, as a conversation in the chat" onClick={keepIt}>This is becoming a conversation. Continue in the {cx} chat →</button>}
   {(dict.listening||dict.working)&&<p className="dictation-live" aria-live="polite">{dict.live||(dict.working?'Writing out…':'Listening…')}</p>}
   <div className="speak-box" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();attach(e.dataTransfer.files?.[0])}}>
    <textarea ref={input} rows={1} value={text} placeholder="Say or type anything…" aria-label={'Say it to '+cx} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&text.trim()){e.preventDefault();e.stopPropagation();send()}}}/>
    <button className="tiny-attach" aria-label="Attach a file" title="Attach a screenshot, PDF or text file (or drop it here)" onClick={()=>file.current?.click()}>📎</button><input ref={file} type="file" hidden onChange={e=>{attach(e.target.files?.[0]);e.target.value=''}}/>
    <button className={'tool'+(dict.listening?' on':'')} aria-label="Speak" title={dict.listening?'Stop':'Speak'} disabled={dict.working} onClick={dict.toggle}>🎙</button>
    <button className="chat-send" disabled={!text.trim()||busy} aria-label="Send" onClick={()=>send()}>↑</button></div>
  </div>}
 </>;
}
type Agent={id:string;name:string;maker:string;command:string;headless:string;resume:string;limits:string;builder:string;handover:string;installed:boolean;state:string;position:number|null};
// Systems › Agents (RA-079): coding agents as switchable resources; your fallback order; a hand-over prompt for any of them.
function AgentsTab({hub}:{hub:Hub|null}){
 const [a,setA]=useState<{order:string[];agents:Agent[];next:string}|null>(null),[note,setNote]=useState(''),[task,setTask]=useState(''),[copied,setCopied]=useState(false);
 const ok=hubHas(hub,'engine.assets.agents');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.assets.agents');if(r.ok)setA((await r.json()).result)}catch{}};
 useEffect(()=>{if(ok)load()},[ok]);
 if(!ok)return <Outdated what="Agents"/>;
 if(!a)return <p className="build-idle">Loading…</p>;
 const save=async(order:string[])=>{try{setNote(await post('engine.assets.agent_order',{order}));await load()}catch(e){setNote((e as Error).message)}};
 const inOrder=[...a.order.map(id=>a.agents.find(x=>x.id===id)!).filter(Boolean),...a.agents.filter(x=>!a.order.includes(x.id))];
 const move=(id:string,by:number)=>{const o=[...a.order];const i=o.indexOf(id);if(i<0){o.push(id);save(o);return}const j=i+by;if(j<0||j>=o.length)return;[o[i],o[j]]=[o[j],o[i]];save(o)};
 const copy=async()=>{try{const r=await fetch('/api/engine/engine.assets.agents?handover='+encodeURIComponent(task.replace(/\D/g,'')));const b=await r.json();if(!r.ok)throw Error(b.error);await navigator.clipboard.writeText(b.result.prompt);setCopied(true);setTimeout(()=>setCopied(false),2000)}catch(e){setNote('Not copied: '+(e as Error).message)}};
 return <div className="agents-tab">
  <p className="ei-why">When one agent runs out (a usage limit), the next ready one in your order takes over. Installed agents are listed under Resources, so {'Cortex'} plans with them. Today only Claude Code drives the builder; the others take over with the hand-over prompt.{a.next&&<> Next to use: <b>{a.agents.find(x=>x.id===a.next)?.name}</b>.</>}</p>
  <ol className="agent-list">{inOrder.map(x=>{const pos=a.order.indexOf(x.id);return <li key={x.id} className={'agent '+(x.state==='ready'?'ready':x.installed?'out':'absent')}>
   <div className="agent-head"><span className="agent-n">{pos>=0?pos+1:'–'}</span><b>{x.name}</b><small>{x.maker}</small><span className="chip small">{x.state}</span>
    <span className="move-keys">{pos>=0?<><button className="linkish" aria-label="Earlier" disabled={pos===0} onClick={()=>move(x.id,-1)}>↑</button><button className="linkish" aria-label="Later" disabled={pos===a.order.length-1} onClick={()=>move(x.id,1)}>↓</button><button className="linkish" disabled={a.order.length<2} onClick={()=>save(a.order.filter(i=>i!==x.id))}>remove</button></>:<button className="linkish" onClick={()=>move(x.id,0)}>add to the order</button>}</span></div>
   <dl><dt>Builder</dt><dd>{x.builder}</dd><dt>Usage limits</dt><dd>{x.limits}</dd><dt>Takes over by</dt><dd>{x.handover}</dd><dt>Runs as</dt><dd><code>{x.headless}</code></dd></dl></li>})}</ol>
  <div className="sys-card"><h5>Hand over to another agent <small>a prompt that any of them can start from, in the repository</small></h5>
   <div className="ei-actions"><input value={task} placeholder="Task # (optional)" aria-label="Task number" onChange={e=>setTask(e.target.value)} style={{width:140}}/><button onClick={copy}>{copied?'Copied':'Copy the hand-over prompt'}</button><button className="linkish" onClick={()=>save(a.order)}>Check the agents again</button></div></div>
  {note&&<p className="save-status" role="status">{note}</p>}
 </div>;
}
// Systems › Settings (owner, 2026-10-25): every setting in one list, with where it lives; shortcuts are set here.
const SETTINGS:{group:string;name:string;what:string;where:string}[]=[
 {group:'Cortex',name:'Nickname',what:'What the reasoning core is called everywhere',where:'Cortex'},
 {group:'Cortex',name:'How Atlas hears you',what:'Speech on this PC, Whisper through the Engine, or the browser',where:'Systems'},
 {group:'Builder',name:'Daily budget and Push through today',what:'Runs a day, or no limit; ignore the budget for today',where:'Forge › Build'},
 {group:'Builder',name:'Merging',what:'You merge, or the builder merges RA steps once the tests pass',where:'Forge › Build'},
 {group:'Builder',name:'Autonomy and review batch',what:'Off, ask first, within limits or continuous; how many pull requests may wait',where:'Forge › Build'},
 {group:'Builder',name:'Build mode',what:'Plan first, balanced, ideas first or management agent first',where:'Forge › Build'},
 {group:'Builder',name:'Build order',what:'Drag the queue into your own order',where:'Forge › Build'},
 {group:'Builder',name:'Live console',what:'Plain steps or the full output (remembered in this browser)',where:'Forge › Build'},
 {group:'Agents',name:'Which coding agents and in what order',what:'Claude Code, Codex and others as switchable resources',where:'Systems'},
 {group:'Plan',name:'Just added window',what:'How long new tasks, ideas and goals stay marked new',where:'Today'},
 {group:'Plan',name:'Goal deadlines',what:'Only a deadline you set raises a goal’s priority',where:'Focus'},
 {group:'Research',name:'Sources and limits',what:'Which feeds are scanned and how many items each',where:'Research'},
 {group:'Atlas',name:'Stop everything',what:'The kill switch for the builder and every paid model call',where:'Systems'},
 {group:'Atlas',name:'Self-repair and backups',what:'Automatic crash repair; the private encrypted backup',where:'Systems'},
 {group:'Atlas',name:'Models and daily budgets',what:'Which model does which job; paid calls per day per job',where:'Systems'},
 {group:'Atlas',name:'Constitution',what:'The hard limits; only you edit the file',where:'Systems'},
 {group:'Atlas',name:'Hub connection',what:'How this screen reaches the Engine on the Pi',where:'Systems'}];
// What Cortex may do at once (#186): routine plan changes with Undo, or offer every change first.
function CortexAutonomy(){
 const [v,setV]=useState<{level:string;text:string;levels:string[]}|null>(null),[err,setErr]=useState('');
 const ask=async(body:object)=>{try{setV(await post('engine.brain.cortex_autonomy',body) as any);setErr('')}catch(e){setErr((e as Error).message)}};
 useEffect(()=>{ask({})},[]);
 const NAMES:Record<string,string>={routine:'Routine changes at once',ask:'Offer every change first'};
 if(!v)return err?<div className="sys-card"><h5>Cortex</h5><small className="ei-why">Needs a newer Engine on the Pi ({err}).</small></div>:null;
 return <div className="sys-card"><h5>Cortex <small>what it may do without asking</small></h5>
  <div className="note-dest" role="radiogroup" aria-label="Cortex autonomy">{v.levels.map(l=><button key={l} role="radio" aria-checked={v.level===l} className={'chip'+(v.level===l?' on':'')} onClick={()=>v.level!==l&&ask({level:l})}>{NAMES[l]||l}</button>)}</div>
  <small className="ei-why">{v.text} Money, external steps, accounts, outside code and the constitution always wait for your approval.</small></div>;
}
function SettingsTab(){
 const [sc,setSc]=useState(readShortcuts),[rec,setRec]=useState('');
 const save=(id:string,v:string)=>{const n={...sc,[id]:v};setSc(n);try{localStorage.setItem('atlas-shortcuts',JSON.stringify(n))}catch{}};
 const groups=[...new Set(SETTINGS.map(x=>x.group))];
 return <div className="settings-tab">
  <CortexAutonomy/>
  <div className="sys-card"><h5>Shortcuts <small>this browser; click Change, then press the keys</small></h5><ul className="settings-list">{SHORTCUTS.map(x=><li key={x.id}><span><b>{x.label}</b></span>
   {rec===x.id?<input autoFocus className="sc-rec" readOnly value="Press the keys… (Esc cancels)" onKeyDown={e=>{e.preventDefault();if(e.key==='Escape'){setRec('');return}const c=comboOf(e);if(c){save(x.id,c);setRec('')}}} onBlur={()=>setRec('')}/>
   :<><kbd className="sc-key">{sc[x.id]||'not assigned'}</kbd><button className="linkish" onClick={()=>setRec(x.id)}>Change</button>{sc[x.id]&&<button className="linkish" onClick={()=>save(x.id,'')}>Clear</button>}</>}</li>)}</ul></div>
  {groups.map(g=><div className="sys-card" key={g}><h5>{g}</h5><ul className="settings-list">{SETTINGS.filter(x=>x.group===g).map(x=><li key={x.name}><span><b>{x.name}</b><small>{x.what}</small></span><button className="linkish" onClick={()=>goEvent(x.where)}>{x.where} →</button></li>)}</ul></div>)}
 </div>;
}
// One search for the header, the ✦ button and the Cortex chat: functions, Atlas's steps, engine tasks and ideas, notes.
// A hit's go() opens its screen and marks the item there.
function useAtlasSearch(q:string,limit=12):Hit[]{
 const src=useContext(SearchCtx);const [engine,setEngine]=useState<{tasks:any[];ideas:any[]}|null>(null);const qq=q.trim();
 useEffect(()=>{if(qq.length<2||!src||!hubHas(src.hub,'engine.build.overview')){setEngine(null);return}const word=qq.replace(/^#/,'').split(/\s+/).filter(w=>w.length>2||/^\d+$/.test(w)).sort((a,b)=>b.length-a.length)[0]||qq;  // the engine matches one phrase; Atlas scores every word
  const t=setTimeout(async()=>{try{const r=await fetch('/api/engine/engine.build.overview?search='+encodeURIComponent(word));if(r.ok)setEngine((await r.json()).result)}catch{}},250);return()=>clearTimeout(t)},[qq,src?.hub?.reachable]);
 if(!src||qq.length<2)return [];
 const {graph,tasks,notes,open}=src,hits:Hit[]=[];
 const mark=(t:string)=>setTimeout(()=>highlightText(t),350);
 for(const f of graph?.functions||[]){const sc=3*score(qq,f.label)+score(qq,`${f.label} ${f.summary} ${f.programName}`);if(sc)hits.push({kind:'function',id:f.id,title:f.label,sub:`${f.programName} · ${f.atlasSection||f.domain}`,score:sc+1,go:()=>open('function',f)})}
 for(const t of tasks){const sc=3*score(qq,`${t.id} ${t.title}`);if(sc)hits.push({kind:'atlas task',id:t.id,title:`${t.id} · ${t.title}`,sub:STATUS_TEXT[t.status]||t.status,score:sc,go:()=>open('atlas task',t)})}
 const words=qq.replace(/^#/,'');
 for(const t of engine?.tasks||[]){const sc=3*score(words,`${t.id} ${t.title}`);if(sc||/^\d+$/.test(words))hits.push({kind:'task',id:'t'+t.id,title:`#${t.id} ${t.title}`,sub:`Task · ${t.status}`,score:sc||1,go:()=>{open('task',t);mark(t.title)}})}
 for(const i of engine?.ideas||[]){const sc=3*score(words,i.title);if(sc)hits.push({kind:'idea',id:'i'+i.id,title:i.title,sub:'Idea',score:sc,go:()=>{open('idea',i);mark(i.title)}})}
 for(const n of notes){const sc=score(qq,n.body);if(sc)hits.push({kind:'note',id:n.id,title:n.body.split('\n')[0].slice(0,80),sub:'Note',score:sc,go:()=>{open('note',n);mark(n.body.split('\n')[0].slice(0,60))}})}
 for(const v of SPEAK_VIEWS){const sc=3*score(qq,v);if(sc)hits.push({kind:'screen',id:'v'+v,title:v,sub:'Screen',score:sc+2,go:()=>open('screen',v)})}
 return hits.sort((a,b)=>b.score-a.score).slice(0,limit);
}
// "go to …", "where is …", "find …": what to look for, or null when the words are not about finding.
const FIND_WORDS=/^(?:please\s+)?(?:go to|goto|open|show(?: me)?|find|search(?: for)?|look up|where(?:'s| is| are)?|take me to|navigate to|jump to|ga naar|zoek|toon|waar is)\s+(?:the\s+)?(.+?)\??$/i;
const findQuery=(t:string)=>{const x=t.trim();if(/^#\d+$/.test(x))return x;const m=FIND_WORDS.exec(x);return m?m[1].trim():null};
function HeaderSearch(){
 const [q,setQ]=useState(''),[show,setShow]=useState(false),[sel,setSel]=useState(0);
 const top=useAtlasSearch(q),qq=q.trim();
 const pick=(h?:Hit)=>{if(!h)return;h.go();setShow(false);setQ('')};
 return <div className="hsearch"><label className="search"><span>⌕</span><input id="search" placeholder="Search functions, tasks, ideas, notes…" aria-label="Search Atlas" value={q} onFocus={()=>setShow(true)} onBlur={()=>setTimeout(()=>setShow(false),150)}
  onChange={e=>{setQ(e.target.value);setSel(0);setShow(true)}} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();setSel(s=>Math.min(s+1,top.length-1))}else if(e.key==='ArrowUp'){e.preventDefault();setSel(s=>Math.max(s-1,0))}else if(e.key==='Enter')pick(top[sel]);else if(e.key==='Escape'){setQ('');setShow(false)}}}/>{q&&<button aria-label="Clear search" onClick={()=>setQ('')}>×</button>}</label>
  {show&&qq.length>=2&&<ul className="hsearch-list" role="listbox">{top.length?top.map((h,i)=><li key={h.kind+h.id} role="option" aria-selected={i===sel} className={i===sel?'sel':''} onMouseDown={()=>pick(h)}><span className="chip">{h.kind}</span><b>{h.title}</b><small>{h.sub}</small></li>):<li className="none">No match. Try other words.</li>}</ul>}
 </div>;
}
type GoalGlance={id:number;title:string;horizon:string;total:number;done:number;pct:number;done_week:number;overdue:number;next:{id:number;title:string}|null};
type Glance={goals?:GoalGlance[];done_today:number;done_week:number;done_last_week:number;added_week:number;streak:number;best_streak:number;open:number;overdue:{id:number;title:string;due:string;days_late:number}[];due_soon:{id:number;title:string;due:string}[];stalled:{id:number;title:string}[];daily:{date:string;done:number}[];mine:{id:number;title:string;due:string|null;effort_h:number;status:string;new?:boolean}[];just_added?:{minutes:number;choices:number[];items:{kind:string;id:number;title:string;created:string}[]};proposals:number;review:{content:string;created:string}|null;why_first?:{task:number|null;why:string;last_rerank:{ts:string;detail:string}|null}};
const KIND_WORD:Record<string,string>={task:'Task',idea:'Idea',goal:'Goal'};
const windowLabel=(m:number)=>m>=1440?'1 day':m%60===0?`${m/60} h`:`${m} min`;
function JustAdded({j,hub,reload,onPlan}:{j:NonNullable<Glance['just_added']>;hub:Hub|null;reload:()=>void;onPlan:()=>void}){
 const [note,setNote]=useState('');
 const set=async(m:number)=>{setNote('');try{setNote(await post('engine.direction.fresh_window',{minutes:m}));reload()}catch(e){setNote('Not changed: '+(e as Error).message)}};
 return <div className="just-added"><h5>Just added <small>marked new for</small> {hubHas(hub,'engine.direction.fresh_window')?<select aria-label="How long new items stay marked" value={j.minutes} onChange={e=>set(Number(e.target.value))}>{[...new Set([...j.choices,j.minutes])].sort((a,b)=>a-b).map(m=><option key={m} value={m}>{windowLabel(m)}</option>)}</select>:<small>{windowLabel(j.minutes)}</small>}</h5>
  {j.items.length?<ul>{j.items.map(x=><li key={x.kind+x.id} className="is-new"><span className="chip small">{KIND_WORD[x.kind]||x.kind}</span><button className="linkish" onClick={onPlan} title="Open in Forge › Plan">{x.kind==='task'?`#${x.id} `:''}{x.title}</button><small>{new Date(x.created).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}</small></li>)}</ul>:<p className="build-idle">Nothing added in the last {windowLabel(j.minutes)}.</p>}
  {note&&<p className="save-status" role="status">{note}</p>}</div>;
}
function TodayGlance({hub,onPlan}:{hub:Hub|null;onPlan:()=>void}){
 const [g,setG]=useState<Glance|null>(null),[busy,setBusy]=useState(false),[note,setNote]=useState('');
 const ok=hubHas(hub,'engine.direction.progress');
 const load=async()=>{try{const r=await fetch('/api/engine/engine.direction.progress');if(r.ok)setG((await r.json()).result)}catch{}};
 useEffect(()=>{if(!ok)return;load();const t=setInterval(load,60000);return()=>clearInterval(t)},[ok]);
 if(!ok||!g)return null;
 const m=Math.max(1,...g.daily.slice(-14).map(d=>d.done));
 const attention=[...g.overdue.map(t=>({k:'o'+t.id,t:`#${t.id} ${t.title}`,s:`${t.days_late} day${t.days_late>1?'s':''} late`,c:'late'})),...g.due_soon.map(t=>({k:'d'+t.id,t:`#${t.id} ${t.title}`,s:`due ${t.due}`,c:'soon'})),...g.stalled.map(t=>({k:'s'+t.id,t:`#${t.id} ${t.title}`,s:'doing for a week without change',c:'stalled'}))];
 return <div className="glance">
  <div className="glance-stats"><div><b>{g.done_today}</b><span>done today</span></div><div><b>{g.done_week}</b><span>this week <small>({g.done_last_week} last week)</small></span></div><div><b>{g.streak}</b><span>day streak <small>(best {g.best_streak})</small></span></div>
   <div className="spark" title="Tasks finished per day, last 14 days">{g.daily.slice(-14).map(d=><i key={d.date} style={{height:3+22*d.done/m+'px'}} className={d.done?'on':''}/>)}</div></div>
  <div className="glance-cols">
   <div><h5>Next for you</h5>{g.mine.length?<ol>{g.mine.map(t=><li key={t.id} className={t.new?'is-new':''}>#{t.id} {t.title}<NewBadge on={t.new}/>{t.due?<small> · due {t.due}</small>:null}</li>)}</ol>:<p className="build-idle">Nothing of yours is open.</p>}
    {g.why_first?.why&&<p className="ei-why" title="Why this task is first">Why first: {g.why_first.why}{g.why_first.last_rerank&&<small> · last re-rank {g.why_first.last_rerank.ts}: {g.why_first.last_rerank.detail}</small>}</p>}
    {g.proposals>0&&<button className="linkish" onClick={onPlan}>{g.proposals} proposal{g.proposals>1?'s':''} to accept in Forge › Plan</button>}</div>
   <div><h5>Needs attention</h5>{attention.length?<ul className="attn">{attention.slice(0,6).map(a=><li key={a.k} className={a.c}>{a.t}<small>{a.s}</small></li>)}</ul>:<p className="build-idle">Nothing overdue, due soon or stalled.</p>}</div>
  </div>
  {g.just_added&&<JustAdded j={g.just_added} hub={hub} reload={load} onPlan={onPlan}/>}
  {(g.goals||[]).some(x=>x.total>0)&&<div className="goal-glance"><h5>Goals</h5><ul>{g.goals!.filter(x=>x.total>0).map(x=><li key={x.id}><div><b>{x.title}</b> <small>{x.horizon}</small></div><div className="tl-track" title={`${x.done} of ${x.total} done`}><i className="tl-done" style={{width:x.pct+'%'}}/></div><small>{x.pct}% · {x.done_week} this week{x.overdue?` · ${x.overdue} overdue`:''} · {x.next?`next: #${x.next.id} ${x.next.title}`:'nothing open'}</small></li>)}</ul></div>}
  <details className="review"><summary>Weekly review{g.review?` · ${new Date(g.review.created).toLocaleDateString('en-GB',{timeZone:'Europe/Paris',day:'numeric',month:'short'})}`:' · none yet'}</summary>
   {g.review&&<pre>{g.review.content}</pre>}{note&&<p className="save-status">{note}</p>}
   <button disabled={busy||!hubHas(hub,'engine.direction.weekly_review')} onClick={async()=>{setBusy(true);setNote('');try{await post('engine.direction.weekly_review',{});await load()}catch(e){setNote((e as Error).message)}finally{setBusy(false)}}}>{busy?'Writing…':'Write this week’s review'}</button></details>
 </div>;
}
function HubChip({hub,onOpen}:{hub:Hub|null;onOpen:()=>void}){
 if(!hub)return null;
 if(!hub.configured)return <button className="hub-chip setup" onClick={onOpen} title="Connect Atlas to the Engine on your Pi">Connect hub</button>;
 return <button className={'hub-chip '+(hub.reachable?'ok':'down')} onClick={onOpen} title={hub.reachable?`Engine hub at ${hub.url}`:hub.error}>● Hub {hub.reachable?`· ${hub.latencyMs} ms`:'offline'}</button>;
}
function HubCard({hub,onChange}:{hub:Hub|null;onChange:()=>void}){
 const [url,setUrl]=useState(''),[token,setToken]=useState(''),[busy,setBusy]=useState(false),[msg,setMsg]=useState('');
 async function connect(){setBusy(true);setMsg('Testing the connection…');try{const r=await fetch('/api/hub/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,token})});const b=await r.json();if(!r.ok)throw Error(b.error);setMsg(b.reachable?'Connected.':b.error||'Could not connect.');if(b.reachable){setToken('');onChange()}}catch(e){setMsg(String((e as Error).message))}finally{setBusy(false)}}
 async function forget(){await fetch('/api/hub/forget',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setMsg('Hub forgotten on this computer.');onChange()}
 return <section className="hub-card"><header><h3>Engine hub</h3><small>{!hub?'Checking…':!hub.configured?'Not connected':hub.reachable?`Connected to ${hub.url} · ${hub.latencyMs} ms · Engine ${hub.commit||''}`:`Offline: ${hub.error}`}</small></header>
  {(!hub?.configured||!hub.reachable)&&<div className="hub-form"><p>On the Pi, run <code>bash deploy/atlas-hub.sh</code> in the Atlas folder. It prints an address and a token; paste them here. The token stays on this computer.</p>
   <label>Hub address<input value={url} onChange={e=>setUrl(e.target.value)} placeholder="http://100.x.y.z:4320"/></label>
   <label>Hub token<input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Printed by the setup script" autoComplete="off"/></label>
   <div className="hub-actions"><button disabled={busy||!url||!token} onClick={connect}>{busy?'Connecting…':'Connect'}</button>{hub?.configured&&<button onClick={forget}>Forget hub</button>}</div></div>}
  {msg&&<p className="save-status" role="status">{msg}</p>}
  {hub?.configured&&hub.reachable&&<button className="link-button" onClick={forget}>Forget hub</button>}
 </section>;
}
function Roadmap(){return <section className="roadmap panel" aria-label="Research roadmap"><div className="panel-heading"><h2><b className="orange">●</b> Research roadmap</h2><small>Research direction</small></div><div className="stages">{stages.map((s,i)=><article key={s}><div className={'stage-art art-'+i}><i/><i/><i/><i/><i/></div><h3><span>{i+1}</span>{s}</h3><p>{captions[i]}</p></article>)}</div></section>}
createRoot(document.getElementById('root')!).render(<App/>);

type Source={id:string;contentHash:string;sourceType:string;mediaType:string;sizeBytes:number;originalName:string|null;retrievedAt:string;lastImportedAt:string;importCount:number;status:'pending'|'processing'|'done'|'failed'|'invalid';attempts:number;error:string|null};
const TEXT_EXT:Record<string,string>={txt:'text/plain',md:'text/markdown',markdown:'text/markdown',csv:'text/csv',json:'application/json'};
function mediaTypeOf(f:File){const ext=(f.name.split('.').pop()||'').toLowerCase();return TEXT_EXT[ext]||f.type||'application/octet-stream'}
function readBase64(file:File){return new Promise<string>((ok,bad)=>{const fr=new FileReader();fr.onload=()=>ok(String(fr.result).split(',')[1]||'');fr.onerror=()=>bad(Error('could not read the file'));fr.readAsDataURL(file)})}
const SOURCE_STATUS:Record<string,string>={pending:'waiting to be read',processing:'being read',done:'kept',failed:'reading failed; retried at the next start',invalid:'kept, but cannot be read as its type'};
function LocalSources({items,onChange}:{items:Source[]|null;onChange:()=>void}){
 const [msg,setMsg]=useState('');const files=(items||[]).filter(s=>s.sourceType!=='note');
 async function retry(id:string){setMsg('');try{const r=await fetch(`/api/sources/${id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const b=await r.json();setMsg(r.ok?`Read again: ${SOURCE_STATUS[b.status]||b.status}.`:b.error)}catch{setMsg('The service is not reachable.')}onChange()}
 if(items===null)return null;
 return <section className="engine-attention brain"><h4>Local sources <small>originals kept unchanged on this computer; the same content is kept once</small></h4>
  {!files.length?<p className="build-idle">No files yet. Press ✦ at the top, choose This computer and attach a file with 📎 or drop it on the box.</p>:<ul>{files.map(s=><li className="engine-item" key={s.id}><div className="ei-head"><strong>{s.originalName||'(no name)'}</strong><small className="ei-key">{s.mediaType} · {(s.sizeBytes/1024).toFixed(1)} KB{s.importCount>1?` · imported ${s.importCount}×`:''}</small></div>
   <small className={s.status==='done'?'muted':'warn'}>{SOURCE_STATUS[s.status]||s.status}{s.error?` (${s.error})`:''} · {new Date(s.retrievedAt).toLocaleString('en-GB',{timeZone:'Europe/Paris',dateStyle:'short',timeStyle:'short'})} · sha256 {s.contentHash.slice(0,12)}</small>
   <div className="ei-actions"><a className="linkish" href={`/api/sources/${s.id}/original`}>Original</a>{s.status!=='done'&&<button onClick={()=>retry(s.id)}>Retry</button>}</div></li>)}</ul>}
  {msg&&<p className="save-status" role="status">{msg}</p>}</section>}
