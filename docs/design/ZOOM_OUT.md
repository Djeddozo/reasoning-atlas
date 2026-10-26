# Zoom-out: where is this heading (task #54, absorbs #78)

**Question for Djeddi at the end. Nothing in the dashboard changes until you pick an option.**
Built so far: the data layer every option needs (`engine/direction.py`, `hq direction`).

## 1. What the view must do
1. Zoom out from the ranked queue to the skill tree nodes those tasks build, and to the goals the nodes serve.
2. Show that finishing a task **unlocks** something larger (a node, and the nodes it opens next).
3. Say in a few lines: what the Engine is building towards, what to **steer**, what to **do yourself**, what needs your **thinking**.
4. Respect P3: no second copy of the skill tree, Plan tab only, works on a phone.

## 2. Visual patterns reviewed
| Pattern | Where it comes from | What it is good at | Weakness for the Engine |
|---|---|---|---|
| Tech tree | Civilization, Factorio | Prerequisites, "what opens next", a clear unlock moment | Gets wide; tasks are invisible |
| Passive/skill web | Path of Exile, RPGs | Feeling of progress, glowing paths | Decorative, hard to act on (your #45 remark) |
| Semantic zoom | Google Maps, Figma, Miro | Same canvas, different detail per zoom level | Needs pinch/zoom handling in plain JS |
| Now / Next / Later roadmap | Product roadmaps | Time horizon in three columns, very legible | Loses the dependency structure |
| Impact map / Opportunity–solution tree | Gojko Adzic, Teresa Torres | Goal → capability → task, the *why* of each task | Static; usually drawn by hand |
| Story map | Jeff Patton | Backbone (big steps) with tasks hanging beneath | Horizontal, poor on phones |
| Sankey / flow lines | Data viz | Where effort flows: queue → node → goal | Lines get noisy beyond ~15 items |
| Progress rings / pips | Apple Fitness, Duolingo | Instant "how far", satisfying completion | Shows amount, not direction |

What carries over: **one structure, several zoom levels** (semantic zoom), **unlock moments** (tech tree),
the **why-chain** goal → node → task (impact map), and **pips** for "tasks left" (rings).

## 3. What the data says today (`hq direction` on the snapshot)
- 22 of 33 queued tasks are linked to **no node**, and no task owned only by you is. Any zoom-out view would show
  two thirds of the queue as "unsorted". *Whatever option you choose, the first build step is automatic
  node linking* (Claude suggests `capability_id` during re-evaluation; you can undo it).
  **Built:** `engine/autolink.py` — daily at 06:30, before the re-evaluation, Claude links open tasks without a node
  (`hq tree autolink`, `--dry` to preview, `--undo` for the last run). A link you changed or undid is never redone.
- Goals and nodes are linked only through tasks. That is enough: a node serves a goal when its tasks do.
- Nearest unlocks: Budget & spending and Calendar & email, each one task away, and both tasks need you (owner `both`).

## 4. Three options

### Option A — Zoom levels on the existing skill tree (semantic zoom)
One canvas, the current skill tree, with a three-step zoom control. No new tab, no duplicate tree.
```
 Zoom: [ Tasks ]  Nodes   Goals                 Zoom:  Tasks  [ Nodes ]  Goals
┌─────────────────────────────────┐             ┌──────────────────────────────────┐
│ Planning & ranking   ●●●○ 3/4   │             │  Memory ✓──Planning ✓──Goal map ◌ │
│  #28 Reorder the queue   ★unlock│             │              │                    │
│ Prototype builder   ○○ 0/2      │   zoom out  │  Self-building ✓──Prototype ◐──▶ │
│  #61 Scaffold web app           │  ───────▶   │                    ╲   First      │
│  #62 Deploy preview             │             │                     ╲  product 🔒 │
│ (no node)  6 tasks  [sort ▸]    │             │  ◐ building ◌ ready 🔒 locked      │
└─────────────────────────────────┘             └──────────────────────────────────┘
                     Zoom: Tasks  Nodes  [ Goals ]
                     ┌──────────────────────────────────────────┐
                     │ Atlas runs on its own ███████░░░ 22/50     │
                     │   ◐ Checkpoint  ◌ Budget  ◌ Calendar      │
                     │ Run Atlas by voice    ███░░░░░░░  3/9      │
                     │ Find opportunities   ██░░░░░░░░  2/10      │
                     └──────────────────────────────────────────┘
```
Finishing the last task of a node: the node's pips fill, it turns green with a short glow, and the nodes it
opens light up as "ready". Insights sit in a strip above the canvas.
- For: reuses what you already like; one place for tree, queue and goals (P3); unlocks are native.
- Against: largest build (zoom state, layout per level); the tree is wide on a phone, so zoom level 2 becomes
  a vertical list of branches there.

### Option B — Why-chain flow: queue → nodes → goals
Three columns (rows on a phone) joined by lines. Tap a goal and only its nodes and tasks stay lit;
tap a task and you see the chain up to its goal.
```
 QUEUE (ranked)              NODES                    GOALS
 #54 Zoom-out design ──┐
 #28 Reorder queue ────┼──▶ Planning & ranking ●●●○ ─┐
 #50 Why-first line ───┘                              ├─▶ Atlas runs on its own 22/50
 #21 Virtual card (you) ───▶ Budget & spending ●○  ★ ─┘
 #32 Dump confirmation ────▶ Phone & Telegram ✓ ───────▶ Run Atlas by voice     3/9
 #17 Calendar (you) ───────▶ Calendar & email ●○   ★ ──▶ Find opportunities     2/10
                             ★ = one task from unlocking
```
- For: answers "why is this task here" for every task at once; strongest steering view (a goal with no
  lines into it is obviously starved).
- Against: a second representation next to the skill tree (P3 risk); lines get noisy above ~15 tasks, so it
  shows the top of the queue only.

### Option C — Unlock ladder (cards, phone-first)
No graph. A short ranked list of the nodes closest to unlocking, each a card with pips for the tasks left,
the goal it serves and what it opens. Insights first.
```
┌ Where this is heading ───────────────────────────────┐
│ Building towards: Planning & ranking · Checkpoints   │
│ Steer: 22 tasks build no node · Voice goal starved    │
│ You: #10 Tailscale (unblocks 2) · #17 Calendar (1)    │
│ Think: Physical products has no path. Keep or drop?   │
└──────────────────────────────────────────────────────┘
┌ Budget & spending ───────────── ●○ 1 left ── ★ next ┐
│ serves: Atlas runs on its own → opens: needs packages│
│ #21 Virtual card with monthly cap (you)  [Do it]     │
└──────────────────────────────────────────────────────┘
┌ Checkpoint & resume ──────────── ○ 1 left ──────────┐
│ serves: Atlas runs on its own                        │
│ #47 Checkpoint before usage limits        [Queue]    │
└──────────────────────────────────────────────────────┘
```
Finishing a task fills a pip; the last pip flips the card to "Unlocked ✓, opens …" for a day, then it drops off.
- For: smallest build, best on a phone, every card ends in a button (#45: lead directly to queueable tasks).
- Against: no picture of the whole; the skill tree stays the "map" and this is the "route".

## 5. Common to all three
- Data: `engine/direction.py` (`rollup()` → queue with node/goal/unlocks, nodes with tasks left and what they open,
  goals with the nodes they build, four insight lists). CLI: `hq direction`, `hq direction --json`.
- Place: Plan tab, at the top of the skill tree section. No new tab.
- Related: #43 (progress per goal) becomes the Goals zoom level / goal rows; #50 ("why first") is the
  sentence on the top task in every option.

## 6. Recommendation and decision
**Recommendation: A**, built in two steps: first the Goals and Nodes levels as vertical lists on the phone
(close to C in effort), then the zoomable canvas on larger screens. It extends the tree you already like
instead of adding a second view.

> **Decision for Djeddi:** which zoom-out view should the Engine build?
> 1. **A — zoom levels on the skill tree** (recommended; most coherent, largest build)
> 2. **B — why-chain flow** queue → nodes → goals (best for steering, adds a second view)
> 3. **C — unlock ladder cards** (smallest, phone-first, no overall picture)
