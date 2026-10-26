"""Usage tracking and the kill switch.

Usage: every model call Atlas makes (models.complete / models.see) and every dev-loop run is recorded:
tokens in/out and an estimated cost. Claude Code reports its own cost per run; for Atlas's own API calls
the cost is estimated from the prices in [models] of the constitution (cloud_usd_per_mtok_in/_out;
leave them 0 to see tokens only). On a Claude subscription, dev-loop costs are what the same work would
cost on the API, a measure of how much capacity it uses, not a bill.

Kill switch: `hq kill`, /kill in Telegram or the dashboard button. It creates memory/KILL, which
  - stops the current dev-loop run at once and blocks new ones,
  - blocks every paid (cloud) model call; local models on the GPU PC keep working.
`hq unkill` (/resume, dashboard) removes it. It works without the database, so it also holds if
something else is broken.
"""
from datetime import date, timedelta

from . import db

KILL_FILE = db.ROOT / "memory" / "KILL"


def killed() -> bool:
    return KILL_FILE.exists()


def kill(con, reason: str = "") -> str:
    KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_FILE.write_text(f"{db.now()} {reason}\n")
    from . import devloop
    stopped = devloop.stop(con)
    db.log(con, "me", "kill_switch_on", reason[:200])
    con.commit()
    return ("Kill switch ON: the agent is stopped and no new runs start; paid model calls are blocked. "
            f"{stopped if not stopped.startswith('Nothing') else ''}").strip() + "\nUndo with /resume (or hq unkill)."


def unkill(con) -> str:
    if KILL_FILE.exists():
        KILL_FILE.unlink()
    db.log(con, "me", "kill_switch_off", "")
    con.commit()
    return "Kill switch OFF: Atlas and the dev loop may run again (within their limits)."


# ---------------- recording ----------------

def recorder(path=None):
    """A callback for models.on_usage that writes each call to the model_usage table."""
    def record(job: str, tier: str, model: str, tokens_in: int, tokens_out: int, cost: float) -> None:
        try:
            con = db.connect(path or db.DB_PATH)
            con.execute("""INSERT INTO model_usage(ts, job, tier, model, tokens_in, tokens_out, cost_usd)
                           VALUES (?,?,?,?,?,?,?)""", (db.now(), job, tier, model, tokens_in, tokens_out, cost))
            con.commit()
            con.close()
        except Exception:
            pass  # usage tracking must never break a model call
    return record


def estimate(cfg: dict, tokens_in: int, tokens_out: int) -> float:
    return round((tokens_in * float(cfg.get("cloud_usd_per_mtok_in", 0) or 0)
                  + tokens_out * float(cfg.get("cloud_usd_per_mtok_out", 0) or 0)) / 1e6, 4)


# ---------------- summaries ----------------

def _window(con, table: str, since: str, cols: str) -> dict:
    r = con.execute(f"SELECT {cols} FROM {table} WHERE {'started' if table == 'dev_runs' else 'ts'} >= ?", (since,)).fetchone()
    return {k: (r[k] or 0) for k in r.keys()}


def summary(con, today: date | None = None) -> dict:
    today = today or date.today()
    windows = {"today": today, "week": today - timedelta(days=6), "month": today - timedelta(days=29)}
    dev_cols = ("COUNT(*) runs, SUM(turns) turns, SUM(tokens_in) tokens_in, SUM(tokens_out) tokens_out, "
                "ROUND(SUM(cost_usd), 2) cost_usd, SUM(seconds) seconds")
    hq_cols = "COUNT(*) calls, SUM(tokens_in) tokens_in, SUM(tokens_out) tokens_out, ROUND(SUM(cost_usd), 2) cost_usd"
    out = {"killed": killed(), "agent": {}, "hq": {}}
    for name, start in windows.items():
        out["agent"][name] = _window(con, "dev_runs", start.isoformat(), dev_cols)
        out["hq"][name] = _window(con, "model_usage", start.isoformat(), hq_cols)
    out["by_job"] = [dict(r) for r in con.execute(
        """SELECT job, tier, COUNT(*) calls, SUM(tokens_in) tokens_in, SUM(tokens_out) tokens_out,
                  ROUND(SUM(cost_usd), 2) cost_usd FROM model_usage WHERE ts >= ? GROUP BY job, tier ORDER BY cost_usd DESC, calls DESC""",
        (windows["month"].isoformat(),))]
    return out


def _k(n) -> str:
    n = n or 0
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def text(con) -> str:
    s = summary(con)
    lines = ["Kill switch is ON." if s["killed"] else "Kill switch is off."]
    for name, label in (("today", "Today"), ("week", "7 days"), ("month", "30 days")):
        a, h = s["agent"][name], s["hq"][name]
        lines.append(f"{label}: agent {a['runs']} runs, {_k(a['tokens_in'] + a['tokens_out'])} tokens, ~${a['cost_usd']:.2f}"
                     f" · Atlas API {h['calls']} calls, {_k(h['tokens_in'] + h['tokens_out'])} tokens, ~${h['cost_usd']:.2f}")
    return "\n".join(lines)
