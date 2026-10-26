"""Where is this heading: the task queue rolled up into the skill tree nodes and goals it builds towards.

The data layer behind the zoom-out view (task #54, design in docs/design/ZOOM_OUT.md). Three levels:

  tasks   the ranked queue; each task says which node it builds, and whether finishing it unlocks that node
  nodes   skill tree capabilities with open work: how much is left, which goals they serve, what they open next
  goals   active goals with the nodes their tasks build and how far they are

Plus four short insight lists for Djeddi: what Atlas is building towards, what to steer, what to do himself and
what needs his thinking. Rule-based and read-only: no model calls, nothing is written.
`hq direction` prints it; the dashboard view follows once Djeddi has picked a mockup.
"""
import json

from . import skilltree

OPEN = ("todo", "doing", "blocked")  # work that counts towards a node; drafts ('proposed') are not work yet
HEADING_WINDOW = 10                  # 'heading': the next N queued tasks


def _count(con, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


def _goal_titles(con) -> dict[int, dict]:
    return {g["id"]: dict(g) for g in con.execute("SELECT id, title, horizon, parent_id FROM goals WHERE status = 'active'")}


def rollup(con) -> dict:
    caps = skilltree.nodes(con)
    goals = _goal_titles(con)

    children: dict[int, list[int]] = {}
    for c in caps.values():
        for r in c["requires"]:
            children.setdefault(r, []).append(c["id"])
    for c in caps.values():
        open_ = [t for t in c["tasks"] if t["status"] in OPEN]
        c["left"] = len(open_)
        counts: dict[int, int] = {}
        for t in c["tasks"]:
            if t["goal_id"] in goals:
                counts[t["goal_id"]] = counts.get(t["goal_id"], 0) + 1
        c["goal_ids"] = sorted(counts, key=lambda g: -counts[g])
    for c in caps.values():
        # locked nodes whose only missing requirement is this one: they open when this node unlocks
        c["opens"] = [k for k in children.get(c["id"], []) if caps[k]["state"] == "locked"
                      and all(caps[r]["state"] == "unlocked" for r in caps[k]["requires"] if r != c["id"])]

    queue = []
    for t in con.execute("""SELECT id, title, owner, status, effort_h, goal_id, capability_id, depends_on, score
                            FROM tasks WHERE status IN ('todo', 'doing') ORDER BY status = 'doing' DESC, score DESC, id"""):
        t = dict(t)
        node = caps.get(t["capability_id"])
        t["node"] = node["title"] if node else ""
        t["goal"] = goals[t["goal_id"]]["title"] if t["goal_id"] in goals else ""
        # finishing this task unlocks its node when it is the node's last open task and the node's requirements are met
        t["unlocks"] = bool(node and node["state"] in ("available", "building") and node["left"] == 1)
        t["opens"] = [caps[k]["title"] for k in node["opens"]] if t["unlocks"] else []
        queue.append(t)

    goal_rows = []
    for g in goals.values():
        all_ = con.execute("SELECT status FROM tasks WHERE goal_id = ? AND status NOT IN ('dropped', 'proposed')",
                           (g["id"],)).fetchall()
        done = sum(r[0] == "done" for r in all_)
        mine = [c for c in caps.values() if g["id"] in c["goal_ids"]]
        goal_rows.append({"id": g["id"], "title": g["title"], "horizon": g["horizon"], "done": done, "total": len(all_),
                          "nodes": sorted({c["title"] for c in mine if c["state"] != "unlocked"}),
                          "built": sorted({c["title"] for c in mine if c["state"] == "unlocked"})})

    node_rows = [{k: c[k] for k in ("id", "title", "branch", "state", "done", "total", "left", "depth")}
                 | {"goals": [goals[g]["title"] for g in c["goal_ids"]], "opens": [caps[k]["title"] for k in c["opens"]]}
                 for c in caps.values() if c["state"] != "unlocked" or c["left"]]
    node_rows.sort(key=lambda n: (["building", "available", "locked"].index(n["state"]) if n["state"] != "unlocked" else 3,
                                  n["left"] or 99, n["depth"]))
    return {"queue": queue, "nodes": node_rows, "goals": goal_rows,
            "insights": insights(con, queue, caps, goals, goal_rows)}


def insights(con, queue: list, caps: dict, goals: dict, goal_rows: list) -> dict:
    head = queue[:HEADING_WINDOW]
    by_node: dict[str, int] = {}
    by_goal: dict[str, int] = {}
    for t in head:
        if t["node"]:
            by_node[t["node"]] = by_node.get(t["node"], 0) + 1
        if t["goal"]:
            by_goal[t["goal"]] = by_goal.get(t["goal"], 0) + 1
    heading = []
    if by_node:
        heading.append(f"The next {len(head)} tasks mostly build: "
                       + ", ".join(f"{n} ({k})" for n, k in sorted(by_node.items(), key=lambda x: -x[1])[:3]) + ".")
    if by_goal:
        g, k = max(by_goal.items(), key=lambda x: x[1])
        heading.append(f"{k} of them serve the goal '{g}'.")
    near = [c for c in caps.values() if c["state"] in ("available", "building") and c["left"]]
    near.sort(key=lambda c: c["left"])
    for c in near[:2]:
        opens = [caps[k]["title"] for k in c["opens"]]
        heading.append(f"{c['title']} unlocks after {c['left']} more task{'s' if c['left'] != 1 else ''}"
                       + (f", and then opens {', '.join(opens)}." if opens else "."))

    steer = []
    unlinked = [t for t in queue if not t["node"]]
    if unlinked:
        steer.append(f"{len(unlinked)} of {len(queue)} queued tasks build no skill tree node, so they do not show "
                     f"where they lead (e.g. #{unlinked[0]['id']}). Claude links them daily (now: hq tree autolink); by hand: hq tree link <task> <node>.")
    ai_goals = {t["goal_id"] for t in queue if t["owner"] in ("ai", "both")}
    for g in goal_rows:
        if g["id"] not in ai_goals:
            steer.append(f"Goal '{g['title']}' has no AI work queued: add or queue a task if it should move now.")
    idle = [c["title"] for c in caps.values() if c["state"] == "available" and not c["tasks"]]
    if idle:
        steer.append(f"Ready to build but nothing queued: {', '.join(idle[:4])}. Queue one or leave it for later.")

    blocks: dict[int, int] = {}
    for r in con.execute("SELECT depends_on FROM tasks WHERE status NOT IN ('done', 'dropped') AND depends_on != ''"):
        for i in skilltree.ids(r[0]):
            blocks[i] = blocks.get(i, 0) + 1
    mine = [t for t in queue if t["owner"] in ("me", "both")]
    mine.sort(key=lambda t: (-blocks.get(t["id"], 0), -(t["score"] or 0)))
    yourself = [f"#{t['id']} {t['title']}" + (f" (unblocks {blocks[t['id']]})" if blocks.get(t["id"]) else "")
                for t in mine[:4]]

    think = []
    drafts = _count(con, "SELECT COUNT(*) FROM tasks WHERE status = 'proposed'")
    if drafts:
        think.append(f"{drafts} proposed task{'s' if drafts != 1 else ''} wait for you to accept, edit or drop.")
    pending = _count(con, "SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
    if pending:
        think.append(f"{pending} approval{'s' if pending != 1 else ''} pending (big steps, outside code, products).")
    for g in goal_rows:
        if not g["total"]:
            think.append(f"Goal '{g['title']}' has no tasks yet: what is the first step?")
        elif not g["nodes"] and not g["built"]:
            think.append(f"Goal '{g['title']}' builds no capability yet: which skill tree node gets it there?")
    stuck = [c["title"] for c in caps.values() if c["state"] == "locked" and not c["tasks"]
             and all(caps[r]["state"] == "locked" for r in c["requires"])]
    if stuck:
        think.append(f"Far ahead, no path yet: {', '.join(stuck[:3])}. Keep, reshape or drop?")
    return {"heading": heading, "steer": steer, "yourself": yourself, "think": think}


def text(r: dict, n: int = 8) -> str:
    lines = ["Where this is heading", ""]
    i = r["insights"]
    for title, key in (("Building towards", "heading"), ("Steer", "steer"), ("Do yourself", "yourself"),
                       ("Needs your thinking", "think")):
        if i[key]:
            lines += [f"{title}:"] + [f"• {x}" for x in i[key]] + [""]
    lines.append("Queue → node → goal:")
    for t in r["queue"][:n]:
        path = " → ".join(x for x in (t["node"] or "(no node)", t["goal"]) if x)
        mark = f"  ★ unlocks {t['node']}" + (f", opens {', '.join(t['opens'])}" if t["opens"] else "") if t["unlocks"] else ""
        lines.append(f"• #{t['id']} {t['title'][:60]} → {path}{mark}")
    lines += ["", "Goals:"]
    for g in r["goals"]:
        lines.append(f"• {g['title']} ({g['done']}/{g['total']} tasks)" + (f": builds {', '.join(g['nodes'])}" if g["nodes"] else "")
                     + (f"; built {', '.join(g['built'])}" if g["built"] else ""))
    return "\n".join(lines)


def as_json(r: dict) -> str:
    return json.dumps(r, default=str)
