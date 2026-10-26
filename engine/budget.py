"""Budgets for Atlas's automatic model use: background jobs never quietly spend Claude capacity or API credit.

Each automatic job (the daily re-evaluation, overlap check, idea placement, skill tree linking, …) may make
at most `[budgets] <job>` paid model calls a day: Claude API ("cloud") or Claude Code on Djeddi's subscription
("claude_code"). Local models (Ollama on the GPU PC) are free and not counted. Over budget, `models.complete`
skips the paid tiers for that job: the job falls back to its rule or a local model, exactly as when no model is
reachable (every caller already handles `ModelUnavailable`). Nothing expensive starts silently: the deferral is
logged once per job per day and shown as a warning under System → Agent & limits.

Jobs Djeddi starts himself (chat, brain dump, research, reading a screenshot) are not in [budgets] and never
capped; the monthly API cap ([models] monthly_cap_usd) and the kill switch still hold for everything.
Sync work (PR lifecycle, update installs, radar scans, usage-limit recovery) makes no model calls at all.
"""
import json
from datetime import date

from . import db

# Paid calls per day per automatic job; [budgets] in the constitution overrides these. 0 = never on a paid model.
DEFAULTS = {"think": 2, "triage": 12, "reevaluate": 2, "doover": 2, "link": 2, "overlap": 6,
            "revalidate": 2, "revisit": 2, "review": 1, "summarise": 0}
PAID = ("cloud", "claude_code")


def limits() -> dict:
    from . import loop
    conf = loop.load_constitution().get("budgets", {})
    return DEFAULTS | {k: int(v) for k, v in conf.items() if isinstance(v, (int, float)) and k in DEFAULTS}


def used_today(con, job: str) -> int:
    return con.execute("SELECT COUNT(*) FROM model_usage WHERE job = ? AND tier IN ('cloud', 'claude_code') AND ts >= ?",
                       (job, date.today().isoformat())).fetchone()[0]


def allowed(job: str) -> bool:
    """May this job use a paid tier now? Jobs without a budget (started by Djeddi) always may."""
    lim = limits()
    if job not in lim:
        return True
    try:
        con = db.connect()
        try:
            return used_today(con, job) < lim[job]
        finally:
            con.close()
    except Exception:
        return True  # the budget is a guard rail; a database hiccup must not break a model call


def note_deferred(job: str) -> None:
    """Remember that a job was kept off paid models today (shown on System; logged once per job per day)."""
    try:
        con = db.connect()
        today = date.today().isoformat()
        seen = json.loads(db.get_state(con, "budget:deferred", "{}") or "{}")
        day = seen.get(today, {})
        if job not in day:
            db.log(con, "hq", "budget_deferred", f"{job}: daily budget of {limits().get(job, 0)} paid call(s) reached")
        day[job] = day.get(job, 0) + 1
        db.set_state(con, "budget:deferred", json.dumps({today: day}))
        con.commit()
        con.close()
    except Exception:
        pass


def status(con) -> list[dict]:
    """Per automatic job: paid calls today, the budget, and how often it was kept off paid models today."""
    lim = limits()
    deferred = json.loads(db.get_state(con, "budget:deferred", "{}") or "{}").get(date.today().isoformat(), {})
    return [{"job": job, "used": used_today(con, job), "limit": n, "deferred": deferred.get(job, 0)}
            for job, n in sorted(lim.items())]
