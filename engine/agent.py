"""`hq think`: Claude reads Atlas's memory and proposes what should change.

Safety model
- Claude only PROPOSES. Tasks/ideas land with status 'proposed' / gate 'idea';
  anything needing money, access, sending or installing lands in the approval queue.
- The constitution is sent with every call, and is enforced here in code too:
  proposals are inserted as data, never executed.
- Model choice (local GPU vs cloud) is handled by hq.models.
"""
import json

from . import db, models

SYSTEM = """You are Atlas, a personal management system serving one person ({owner}).
Mission: {mission}

You receive a snapshot of Atlas's memory: goals, resources, open tasks, ideas, recent memories and log.
Decide what should change. Think about: what is most valuable next, what new information changes
priorities, what Atlas needs to expand (and what it would cost), and which ideas deserve a cheap test.

Rules you must follow (the constitution):
ALLOWED alone: {allowed}
REQUIRES APPROVAL: {requires}
NEVER: {never}

Reply with ONLY a JSON object, no prose:
{{"tasks":[{{"title":"","description":"","owner":"me|ai|both","impact":1-5,"effort_h":0,"confidence":0-1,"due":"YYYY-MM-DD or null"}}],
  "ideas":[{{"title":"","category":"","description":"","impact":1-5,"effort_h":0,"confidence":0-1,"cost_eur":0}}],
  "approvals":[{{"request":"","reason":"","cost_eur":0,"category":"spend|access|send|publish|install|action"}}],
  "memories":[{{"kind":"lesson|fact|decision","content":"","tags":""}}]}}
At most {max_n} items in total. Prefer fewer, better proposals. Empty lists are fine."""


def snapshot(con) -> str:
    def rows(q, *a):
        return [dict(r) for r in con.execute(q, a).fetchall()]

    return json.dumps({
        "goals": rows("SELECT id,title,horizon,why FROM goals WHERE status='active'"),
        "resources": rows("SELECT kind,name,details,value,unit FROM resources WHERE COALESCE(status, 'active') = 'active'"),
        "open_tasks": rows("SELECT id,title,owner,status,impact,effort_h,due,score FROM tasks "
                           "WHERE status NOT IN ('done','dropped') ORDER BY score DESC LIMIT 25"),
        "recently_done": rows("SELECT title,updated FROM tasks WHERE status='done' ORDER BY updated DESC LIMIT 10"),
        "ideas": rows("SELECT title,category,gate,score FROM ideas WHERE gate='idea' ORDER BY score DESC LIMIT 15"),
        "memories": rows("SELECT kind,content,created FROM memories ORDER BY id DESC LIMIT 30"),
        "arsenal_projects": rows("SELECT name, description FROM projects"),
        "radar_top": rows("SELECT source,title,matched FROM radar WHERE state IN ('new','starred') ORDER BY score DESC LIMIT 10"),
        "pending_approvals": rows("SELECT request,cost_eur FROM approvals WHERE status='pending'"),
        "log": rows("SELECT ts,actor,action,detail FROM log ORDER BY id DESC LIMIT 15"),
    }, default=str)


def _parse(text: str) -> dict:
    return models.parse_json(text)


def apply_proposals(con, p: dict, max_n: int) -> int:
    n = 0
    for t in p.get("tasks", [])[:max_n]:
        db.insert(con, "tasks", status="proposed", title=t["title"], description=t.get("description", ""),
                  owner=t.get("owner", "me"), impact=int(t.get("impact", 3)), effort_h=float(t.get("effort_h", 1)),
                  confidence=float(t.get("confidence", 0.6)), due=t.get("due") or None); n += 1
    for i in p.get("ideas", [])[:max(0, max_n - n)]:
        db.insert(con, "ideas", title=i["title"], category=i.get("category", "general"),
                  description=i.get("description", ""), impact=int(i.get("impact", 3)),
                  effort_h=float(i.get("effort_h", 4)), confidence=float(i.get("confidence", 0.4)),
                  cost_eur=float(i.get("cost_eur", 0))); n += 1
    for a in p.get("approvals", [])[:max(0, max_n - n)]:
        db.insert(con, "approvals", request=a["request"], reason=a.get("reason", ""),
                  cost_eur=float(a.get("cost_eur", 0)), category=a.get("category", "action")); n += 1
    for m in p.get("memories", [])[:5]:
        db.insert(con, "memories", kind=m.get("kind", "lesson"), content=m["content"], tags=m.get("tags", ""), source="agent")
    db.log(con, "agent", "think", f"{n} proposals")
    con.commit()
    return n


def think(con, rules: dict) -> int:
    a = rules["autonomy"]
    max_n = rules["agent"].get("max_proposals_per_cycle", 5)
    system = SYSTEM.format(owner=rules["identity"]["owner"], mission=rules["identity"]["mission"],
                           allowed="; ".join(a["allowed"]), requires="; ".join(a["requires_approval"]),
                           never="; ".join(a["never"]), max_n=max_n)
    text = models.complete("think", system, snapshot(con))
    return apply_proposals(con, _parse(text), max_n)
