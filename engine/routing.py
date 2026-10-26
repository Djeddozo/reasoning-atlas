"""Model routing for the dev loop: pick the model for a task before the agent starts on it.

Three levels, each mapped to a Claude Code model in [models.dev] of the constitution:
  light    – small, well-defined work (wording, docs, config, one-line fixes): cheap and fast
  standard – ordinary features and fixes
  strong   – large or risky work (architecture, security, schema, routing, many hours)

The choice uses the task's type (keywords in title and description), its estimated effort and impact,
and the budget: when this month's agent spend (API-equivalent, as reported by Claude Code) reaches
`budget_share` of `monthly_budget_usd`, the choice steps down one level. A repair run steps up one level,
since the first attempt already failed. Each choice carries a short reason; the dev loop stores both on the
run (dev_runs.model / model_reason) and in the log, so the dashboard and `hq dev status` can show them.

The dev loop needs Claude Code, so only Claude models are options here. The GPU PC (task #14) can join
once a local coding agent exists; Atlas's own short model calls are routed per job in models.py.
"""
import re
from datetime import date

from . import loop

LEVELS = ("light", "standard", "strong")
DEFAULTS = {"enabled": True, "light": "haiku", "standard": "sonnet", "strong": "opus",
            "light_max_hours": 1.0,      # at most this many estimated hours (and a light-type task) → light
            "strong_min_hours": 4.0,     # at least this many estimated hours → strong
            "monthly_budget_usd": 0.0,   # 0 = no budget step-down
            "budget_share": 0.8}         # step down once this share of the budget is used

LIGHT_WORDS = ("typo", "wording", "rename", "readme", "docs", "documentation", "comment", "label", "copy",
               "text", "tooltip", "bump", "config", "setting", "colour", "color", "spelling")
STRONG_WORDS = ("architecture", "refactor", "security", "gatekeeper", "migration", "schema", "router", "routing",
                "concurrency", "sandbox", "auth", "approval", "approvals", "multi-agent", "workstream", "redesign",
                "self-repair", "self-improving", "database", "permissions")


def cfg() -> dict:
    return DEFAULTS | loop.load_constitution().get("models", {}).get("dev", {})


def _words(text: str, vocab: tuple[str, ...]) -> list[str]:
    text = text.lower()
    return [w for w in vocab if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text)]


def month_spend(con, today: date | None = None) -> float:
    start = (today or date.today()).replace(day=1).isoformat()
    return float(con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM dev_runs WHERE started >= ?", (start,)).fetchone()[0])


def choose(con, task, conf: dict | None = None, today: date | None = None) -> dict:
    """{'level', 'model', 'reason'} for this task. model '' = Claude Code's own default (routing off)."""
    conf = conf or cfg()
    if not conf.get("enabled", True):
        return {"level": "", "model": "", "reason": "routing off: Claude Code's default model"}
    text = f"{task['title']} {task['description'] or ''}"
    hours = float(task["effort_h"] or 0)
    impact = int(task["impact"] or 0)
    strong, light = _words(text, STRONG_WORDS), _words(text, LIGHT_WORDS)
    if strong:
        level, why = "strong", f"risky type ({', '.join(strong[:2])})"
    elif hours >= float(conf["strong_min_hours"]):
        level, why = "strong", f"large ({hours:g} h)"
    elif impact >= 5 and hours >= 2:
        level, why = "strong", f"high impact and {hours:g} h"
    elif light and hours <= float(conf["light_max_hours"]):
        level, why = "light", f"small {light[0]} task ({hours:g} h)"
    else:
        level, why = "standard", f"ordinary task ({hours:g} h)"
    budget = float(conf.get("monthly_budget_usd") or 0)
    if budget and level != "light":
        spent = month_spend(con, today)
        if spent >= budget * float(conf["budget_share"]):
            level = LEVELS[LEVELS.index(level) - 1]
            why += f"; stepped down: ${spent:.2f} of ${budget:g} budget used this month"
    return {"level": level, "model": str(conf[level]), "reason": why}


def escalate(choice: dict, conf: dict | None = None) -> dict:
    """The choice for a repair run: one level stronger (unchanged when routing is off or already strong)."""
    conf = conf or cfg()
    if not choice["level"] or choice["level"] == "strong":
        return choice
    level = LEVELS[LEVELS.index(choice["level"]) + 1]
    return {"level": level, "model": str(conf[level]), "reason": f"repair after a failed {choice['level']} attempt"}


def text(choice: dict) -> str:
    return f"{choice['model'] or 'default'} ({choice['level'] or 'off'}): {choice['reason']}"


def chosen(con, task_ids=None) -> dict[int, dict]:
    """{task_id: {'model', 'reason'}}: the model routing picked on each task's latest routed run (a repair run's
    escalation included). Tasks never built by the dev loop are absent."""
    q = "SELECT task_id, model, model_reason FROM dev_runs WHERE model != ''"
    rows = con.execute(q + " ORDER BY id").fetchall()
    want = None if task_ids is None else set(task_ids)
    return {r["task_id"]: {"model": r["model"], "reason": r["model_reason"] or ""}
            for r in rows if want is None or r["task_id"] in want}


def line(model: str, reason: str) -> str:
    """'sonnet: ordinary task (2 h)', for one-line displays; '' when no model was recorded."""
    return f"{model}: {reason}" if model and reason else model or ""
