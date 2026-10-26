"""Telegram interface: Atlas in your pocket. Run with `hq bot` on the Pi.

Long-polling against the Bot API with urllib (no webhook, no open port, no extra packages).
Configuration (.env):
  TELEGRAM_BOT_TOKEN   token from @BotFather
  TELEGRAM_CHAT_ID     your private chat with the bot (= your Telegram user id). Only messages and button
                       taps sent by that user in that private chat are acted on; groups are not supported,
                       because every group member could otherwise tap Approve.

What it does
  text            → `hq dump` (saved to memory, turned into plan updates), then a re-rank
  links in text   → `hq intake`: page fetched and summarised into memory; your own words go to `hq dump`
  photo / image   → saved to memory/intake/ and read via models.see (screenshots, scans); caption → `hq dump`
  PDF             → saved to memory/intake/, its text summarised (scans: page 1 read via models.see)
  other files     → saved to memory/intake/ for a later reader
  voice note      → downloaded to memory/voice/, transcribed via models.transcribe, then `hq dump`;
                    if no speech-to-text is reachable, the file path is stored as a memory and you are told
  /next /brief /approvals /radar /progress /run <id> /delete <id> /dashboard /help
  /dev [pause|resume|run|stop]  the agent on the Pi · /kill  kill switch · /resume  undo it · /usage  tokens and cost
  approvals       → every pending approval is sent once with Approve / Deny buttons (decisions from
                    `hq ask --options` get one button per option); a tap is applied only with the
                    approval's one-time nonce (same check as the ntfy buttons)

Model work (dump extraction, transcription) runs on a background worker thread with its own database
connection, so slow models never delay approval buttons or new messages.

Untrusted input is data: message text only ever goes into memory and the dump extractor, never into
a shell or a command. Replies go only to Djeddi's own chat.
"""
import json
import os
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import db, dump, gatekeeper, intake, loop, models, priority, progress

API = "https://api.telegram.org"
VOICE_DIR = db.ROOT / "memory" / "voice"
MAX_TEXT = 4000  # Telegram's limit is 4096 characters per message

HELP = ("Atlas bot. Send any text or voice note and it goes into your plan (brain dump).\n"
        "Links, screenshots and files are saved and summarised into memory.\n"
        "/needs – what needs your input · /answer D12 your answer (A3 yes|no …, T5 what you did)\n/terminal – commands left for you to run in Termius\n/next – what to do next\n/brief – today's brief\n/approvals – pending approvals with buttons\n"
        "/radar – new developments this week\n/progress – how the plan is moving\n"
        "/run 12 – hand task #12 to the agent · /delete 12 – discard it\n/plan – goals and tasks as a tree\n/dashboard – your dashboard link\n"
        "/update – install merged work from GitHub and restart (tests first)\n"
        "/brain – Claude's focus and recommendations · /think – re-evaluate the plan now\n"
        "/ask … – ask Claude about your plan · /autonomy off|ask|limits|continuous – how freely the agent works\n"
        "/tree – skill tree and what the agent builds next (/tree focus plan|balanced|ideas, /tree ok 12)\n"
        "/promote 4 – idea #4 becomes a task (/promote 4 ai: for the AI agent) · /undo 12 – undo change #12\n"
        "/flow – where things go (thought → task or idea → task)\n"
        "/dev – what the agent on the Pi is doing (/dev pause, /dev resume, /dev run, /dev stop, /dev limit runs|tokens|usd N)\n"
        "/kill – KILL SWITCH: stop the agent and all paid model calls\n/resume – undo the kill switch\n"
        "/usage – tokens and estimated cost\n/help – this message")


class TelegramError(RuntimeError):
    pass


# ---------------- Bot API ----------------

def api(token: str, method: str, params: dict | None = None, timeout: int = 30) -> object:
    """Call a Bot API method. `timeout` is the HTTP timeout; Telegram's own options go in `params`."""
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=json.dumps(params or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:  # Telegram puts the reason in the body
        try:
            data = json.load(e)
        except Exception:
            raise TelegramError(f"{method}: HTTP {e.code}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'not ok')}")
    return data.get("result")


def download(token: str, file_id: str, dest: Path, max_bytes: int) -> Path:
    info = api(token, "getFile", {"file_id": file_id})
    if info.get("file_size", 0) > max_bytes:
        raise TelegramError("file too large")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{API}/file/bot{token}/{info['file_path']}", timeout=60) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TelegramError("file too large")
    dest.write_bytes(data)
    return dest


def chunks(text: str, size: int = MAX_TEXT) -> list[str]:
    text = text.strip() or "(empty)"
    out = []
    while text:
        if len(text) <= size:
            out.append(text); break
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut]); text = text[cut:].lstrip("\n")
    return out


class Bot:
    def __init__(self, con, token: str, chat_id: str, cfg: dict | None = None):
        self.con, self.token, self.chat_id = con, token, str(chat_id)
        self.cfg = cfg if cfg is not None else loop.load_constitution().get("telegram", {})
        self.jobs: queue.Queue | None = None  # set by start_worker; None = run model work inline

    def is_owner(self, chat: dict | None, sender: dict | None) -> bool:
        """True only for Djeddi himself, in his private chat with the bot."""
        return (str((chat or {}).get("id", "")) == self.chat_id
                and str((sender or {}).get("id", "")) == self.chat_id)

    # -------- background worker for slow model calls --------
    def start_worker(self, connect) -> threading.Thread:
        """Run dump/voice jobs on a separate thread; `connect()` returns that thread's own DB connection."""
        self.jobs = queue.Queue()
        t = threading.Thread(target=self._work, args=(connect,), name="hq-bot-worker", daemon=True)
        t.start()
        return t

    def _work(self, connect) -> None:
        con = connect()
        while True:
            job = self.jobs.get()
            if job is None:  # stop signal
                con.close()
                return
            try:
                self._run_job(con, *job)
            except Exception:  # never let the worker die: later messages would queue up unprocessed
                pass
            finally:
                self.jobs.task_done()

    def _run_job(self, con, fn, *args) -> None:
        try:
            fn(con, *args)
        except Exception as e:
            try:  # e.g. 'database is locked': drop the half-done write so this connection holds no lock
                con.rollback()
                db.log(con, "hq", "telegram_error", f"{e.__class__.__name__}: {str(e)[:150]}")
                con.commit()
            except Exception:
                pass
            try:
                self.send(f"Something went wrong handling that ({e.__class__.__name__}); it is logged.")
            except Exception:
                pass

    def submit(self, fn, *args) -> None:
        if self.jobs is None:
            fn(self.con, *args)  # inline (tests, `hq bot --once`); errors reach poll_once's handler
        else:
            self.jobs.put((fn, *args))

    # -------- sending (only ever to Djeddi's own chat) --------
    def send(self, text: str, **extra) -> dict:
        msg = {}
        for part in chunks(text):
            msg = api(self.token, "sendMessage", {"chat_id": self.chat_id, "text": part,
                                                  "disable_web_page_preview": True, **extra})
            extra = {}  # buttons only on the first part
        return msg

    # -------- commands --------
    def cmd_next(self, args: str = "") -> str:
        rules = loop.load_constitution()
        priority.rescore(self.con, rules.get("priority"), current_phase=rules["identity"].get("current_phase", 0))
        rows = priority.next_tasks(self.con, 5, owner="me")
        if not rows:
            return "Nothing open."
        out = "\n".join(f"#{r['id']} {r['title']} (~{r['effort_h']:g}h{', due ' + r['due'] if r['due'] else ''})"
                        for r in rows)
        wf = priority.why_first(self.con, "me", rules.get("priority"))
        out += f"\nWhy first: {wf['why']}"
        if wf["last_rerank"]:
            out += f"\nLast re-rank: {wf['last_rerank']['ts']} — {wf['last_rerank']['detail']}"
        return out

    def cmd_brief(self, args: str = "") -> str:
        return loop.brief(self.con).replace("**", "")

    def cmd_approvals(self, args: str = "") -> str:
        n = self.push_approvals(resend=True)
        return "No pending approvals." if not n else f"{n} pending approval(s) above."

    def cmd_needs(self, args: str = "") -> str:
        from .. import needs
        return needs.render(needs.items(self.con))

    def cmd_terminal(self, args: str = "") -> str:
        """Steps left for Djeddi's terminal (hq.commands): each as one line to copy into Termius."""
        from .. import commands
        rows = commands.items(self.con)
        if not rows:
            return "No commands waiting for your terminal."
        return ("Run in your terminal (Termius). Unsure what one does? Type claude first and paste the Claude version from Today.\n\n"
                + "\n\n".join(f"{n}. {c['title']}" + (f"\n{c['why']}" if c["why"] else "") + f"\n{c['shell']}\n(done: /done_cmd {c['id']})"
                                for n, c in enumerate(rows, 1)))

    def cmd_done_cmd(self, args: str = "") -> str:
        from .. import commands
        try:
            return commands.finish(self.con, int(args.strip().lstrip("#")), "done", "me")
        except ValueError as e:
            return str(e) or "Usage: /done_cmd 3"

    def cmd_answer(self, args: str = "") -> str:
        from .. import needs
        key, _, text = args.strip().partition(" ")
        if not key:
            return "Usage: /answer D12 your answer · /answer A3 yes|no (note) · /answer T5 what you did or how to go on"
        try:
            kind, id_ = needs.parse_key(key)
        except ValueError as e:
            return str(e)
        if kind == "task":  # goes through the brain dump (a model call): off the polling thread
            self.submit(self.answer_job, kind, id_, text)
            return f"Working on your input for task #{id_}…"
        try:
            reply = needs.answer(self.con, kind, id_, text, source="telegram")
        except ValueError as e:
            return str(e)
        loop.run_cycle(self.con)
        return reply

    def answer_job(self, con, kind: str, id_: int, text: str) -> None:
        from .. import needs
        try:
            reply = needs.answer(con, kind, id_, text, source="telegram")
            loop.run_cycle(con)
        except ValueError as e:
            reply = str(e)
        self.send(reply)

    def cmd_progress(self, args: str = "") -> str:
        return progress.summary(progress.stats(self.con))

    def _task_ids(self, args: str) -> list[int]:
        return [int(x) for x in args.replace(",", " ").replace("#", " ").split() if x.isdigit()][:10]

    def _task_action(self, fn, args: str, usage: str) -> str:
        ids = self._task_ids(args)
        if not ids:
            return usage
        replies = []
        for tid in ids:
            try:
                replies.append(fn(self.con, tid))
            except ValueError as e:
                replies.append(str(e))
        loop.run_cycle(self.con)
        return "\n".join(replies)

    def cmd_run(self, args: str = "") -> str:
        from .. import flow
        return self._task_action(flow.run, args, "Usage: /run 12 (hand task #12 to the agent).")

    def cmd_delete(self, args: str = "") -> str:
        from .. import flow
        return self._task_action(flow.discard, args, "Usage: /delete 12 (discard task #12; undoable).")

    def cmd_done(self, args: str = "") -> str:
        return ("Tasks are marked done by the agent once it has implemented them. "
                "Use /run 12 to hand a task to the agent, or /delete 12 to discard it.")

    def cmd_update(self, args: str = "") -> str:
        from .. import updater
        try:
            if args.strip().lower() == "check":
                info = updater.check()
                return (f"Atlas {info['current']['sha']} ({info['current']['date']}). {info['behind']} new change(s)."
                        + "".join(f"\n• {c['subject']}" for c in info["commits"][:8])
                        + (f"\n\nBlocked: {info['blocker']}" if info["blocker"] else ""))
            return updater.apply(self.con)["message"]
        except Exception as e:
            return f"Update failed: {e}"

    def cmd_ask(self, args: str = "") -> str:
        if not args.strip():
            return "Usage: /ask what should I focus on this week?"
        self.submit(self.ask_job, args)
        return "Asking Claude…"

    def ask_job(self, con, text: str) -> None:
        from .. import chat
        r = chat.ask(con, text)
        self.send(r["reply"] + (f"\n\nFiled: {r['added']}" if r["added"] else ""))

    def cmd_autonomy(self, args: str = "") -> str:
        from .. import devloop
        level = args.strip().lower()
        if not level:
            return f"Agent autonomy: {devloop.autonomy(self.con)}. {devloop.LEVEL_TEXT[devloop.autonomy(self.con)]}"
        try:
            return devloop.set_autonomy(self.con, level)
        except ValueError as e:
            return str(e)

    def cmd_brain(self, args: str = "") -> str:
        from .. import brain
        return brain.text(self.con)

    def cmd_tree(self, args: str = "") -> str:
        from .. import skilltree
        parts = args.replace("#", " ").split()
        try:
            if parts[:1] == ["focus"]:
                return skilltree.set_focus(self.con, parts[1] if len(parts) > 1 else "")
            if parts[:1] in (["ok"], ["no"]) and len(parts) > 1 and parts[1].isdigit():
                return skilltree.decide(self.con, int(parts[1]), parts[0] == "ok")
        except ValueError as e:
            return str(e)
        return skilltree.text(self.con)

    def cmd_think(self, args: str = "") -> str:
        self.submit(self.think_job)
        return "Re-evaluating your plan with Claude; the result follows in a moment."

    def think_job(self, con) -> None:
        from .. import brain, doover
        self.send(doover.replan_text(doover.replan(con, force=True)) + "\n\n" + brain.text(con))  # same path as Forge › Plan (#124)

    def cmd_queue(self, args: str = "") -> str:
        from .. import brain
        parts = args.replace("#", " ").split()
        if not parts or not parts[0].isdigit():
            return "Usage: /promote 4 (idea #4 becomes your task) or /promote 4 ai (for the AI agent)."
        try:
            tid = brain.promote_idea(self.con, int(parts[0]), "ai" if "ai" in parts[1:] else "me")
        except ValueError as e:
            return str(e)
        return f"Idea #{parts[0]} is now task #{tid}" + (" for the AI agent." if "ai" in parts[1:] else ".")

    def cmd_flow(self, args: str = "") -> str:
        from .. import flow
        return flow.MAP

    def cmd_undo(self, args: str = "") -> str:
        from .. import brain
        n = args.replace("#", "").strip()
        if not n.isdigit():
            return "Usage: /undo 12 (the number in [brackets] from /brain)."
        try:
            return brain.undo(self.con, int(n))
        except ValueError as e:
            return str(e)

    def cmd_plan(self, args: str = "") -> str:
        from .. import plan
        return plan.text(plan.tree(self.con), tasks_per_goal=3)

    def cmd_dashboard(self, args: str = "") -> str:
        from .. import envfile
        return ("Your dashboard (open once per browser; it remembers the token). It only works on the Pi's "
                f"network or through Tailscale:\n{envfile.dashboard_link()}")

    def cmd_dev(self, args: str = "") -> str:
        from .. import devloop, usage
        word = args.strip().lower()
        if word in ("pause", "resume"):
            devloop.set_paused(self.con, word == "pause")
        elif word == "stop":
            return devloop.stop(self.con)
        elif word.startswith("limit"):  # /dev limit [runs|tokens|usd N]
            parts = word.split()
            return devloop.set_limit(self.con, parts[1], parts[2]) if len(parts) == 3 else devloop.limits_text(self.con)
        elif word == "run":
            return devloop.why_not_now(self.con) or devloop.start_background()
        return devloop.status(self.con)

    def cmd_kill(self, args: str = "") -> str:
        from .. import usage
        return usage.kill(self.con, "telegram")

    def cmd_resume(self, args: str = "") -> str:
        from .. import usage
        return usage.unkill(self.con)

    def cmd_usage(self, args: str = "") -> str:
        from .. import usage
        return usage.text(self.con)

    def cmd_radar(self, args: str = "") -> str:
        rows = self.con.execute("""SELECT * FROM radar WHERE state != 'dismissed' AND found >= date('now','-7 day')
                                   ORDER BY state='starred' DESC, score DESC LIMIT 8""").fetchall()
        if not rows:
            return "Radar is empty this week. The daily scan runs at 06:50."
        return "\n\n".join(f"#{r['id']} [{r['source']}] {r['title'][:90]}\n{r['url']}" for r in rows)

    COMMANDS = {"/needs": "cmd_needs", "/answer": "cmd_answer", "/terminal": "cmd_terminal", "/done_cmd": "cmd_done_cmd", "/next": "cmd_next", "/brief": "cmd_brief", "/approvals": "cmd_approvals",
                "/radar": "cmd_radar", "/progress": "cmd_progress", "/done": "cmd_done", "/run": "cmd_run", "/delete": "cmd_delete", "/dashboard": "cmd_dashboard", "/plan": "cmd_plan", "/brain": "cmd_brain", "/ask": "cmd_ask", "/autonomy": "cmd_autonomy", "/think": "cmd_think", "/queue": "cmd_queue", "/promote": "cmd_queue", "/flow": "cmd_flow", "/tree": "cmd_tree", "/undo": "cmd_undo", "/update": "cmd_update", "/dev": "cmd_dev",
                "/kill": "cmd_kill", "/resume": "cmd_resume", "/usage": "cmd_usage", "/help": None, "/start": None}

    # -------- approvals with buttons --------
    def push_approvals(self, resend: bool = False) -> int:
        """Send pending approvals that have no Telegram message yet (or all of them with resend)."""
        q = "SELECT * FROM approvals WHERE status = 'pending'" + ("" if resend else " AND tg_message_id = 0")
        n = 0
        for a in self.con.execute(q + " ORDER BY id").fetchall():
            nonce = a["nonce"]
            if not nonce:  # e.g. approvals proposed by the agent: give them a nonce now
                nonce = secrets.token_urlsafe(9)
                self.con.execute("UPDATE approvals SET nonce = ? WHERE id = ?", (nonce, a["id"]))
            cost = f" · €{a['cost_eur']:g}" if a["cost_eur"] else ""
            options = [o for o in (a["options"] or "").split("|") if o]
            kind = "your decision" if options else "approval"
            text = f"Atlas needs {kind} #{a['id']} [{a['category']}{cost}]\n{a['request']}"
            if a["reason"]:
                text += f"\n\nWhy: {a['reason']}"
            if options:
                text += f"\n\nOwn answer: /answer D{a['id']} …"
            if options:  # a decision: one button per option, each on its own row
                buttons = {"inline_keyboard": [[{"text": o[:60], "callback_data": f"choose {a['id']} {nonce} {i}"}]
                                               for i, o in enumerate(options)]}
            else:
                buttons = {"inline_keyboard": [[
                    {"text": "Approve", "callback_data": f"approved {a['id']} {nonce}"},
                    {"text": "Deny", "callback_data": f"denied {a['id']} {nonce}"}]]}
            msg = self.send(text, reply_markup=buttons)
            self.con.execute("UPDATE approvals SET tg_message_id = ? WHERE id = ?", (msg.get("message_id", -1), a["id"]))
            n += 1
        self.con.commit()
        return n

    def on_callback(self, cq: dict) -> None:
        chat = (cq.get("message") or {}).get("chat")
        if not self.is_owner(chat, cq.get("from")):
            db.log(self.con, "hq", "telegram_ignored",
                   f"button from user {(cq.get('from') or {}).get('id')} in chat {(chat or {}).get('id')}")
            self.con.commit()
            try:
                api(self.token, "answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Not allowed."})
            except Exception:
                pass
            return
        parts = (cq.get("data") or "").split()
        try:
            op, aid, nonce = parts[0], int(parts[1]), parts[2]
        except (IndexError, ValueError):
            op, aid, nonce = "", 0, ""
        ok = gatekeeper.decide_with_nonce(self.con, aid, op, nonce, "telegram", parts[3] if len(parts) > 3 else None)
        self.con.commit()
        if ok and op == "choose":
            op = "chose: " + self.con.execute("SELECT answer FROM approvals WHERE id = ?", (aid,)).fetchone()[0]
        note = f"#{aid} {op}"[:190] if ok else "Already decided or not valid."
        api(self.token, "answerCallbackQuery", {"callback_query_id": cq["id"], "text": note})
        if ok and cq.get("message"):  # the decision is already stored; this edit is cosmetic
            suffix = f"\n\n→ {op}"[:200]
            old = cq["message"].get("text", "")[:MAX_TEXT - len(suffix)]
            try:
                api(self.token, "editMessageText", {"chat_id": self.chat_id, "message_id": cq["message"]["message_id"],
                                                    "text": old + suffix})
            except TelegramError:
                pass

    # -------- incoming messages --------
    def on_message(self, msg: dict) -> None:
        if not self.is_owner(msg.get("chat"), msg.get("from")):
            db.log(self.con, "hq", "telegram_ignored",
                   f"message from user {(msg.get('from') or {}).get('id')} in chat {(msg.get('chat') or {}).get('id')}")
            self.con.commit()
            return
        if "voice" in msg or "audio" in msg:
            self.submit(self.voice_job, msg.get("voice") or msg.get("audio"))
            return
        caption = (msg.get("caption") or "").strip()
        if msg.get("photo"):  # Telegram sends several sizes; the last is the largest
            self.submit(self.file_job, msg["photo"][-1], "image.jpg", caption)
            return
        if msg.get("document"):
            doc = msg["document"]
            self.submit(self.file_job, doc, doc.get("file_name") or "file", caption)
            return
        text = (msg.get("text") or caption).strip()
        if not text:
            self.send("Only text, voice notes, links, photos and files are understood for now.")
            return
        if text.startswith("/"):
            cmd = text.split()[0].split("@")[0].lower()
            if cmd not in self.COMMANDS:
                self.send(f"Unknown command {cmd}.\n\n{HELP}")
                return
            name = self.COMMANDS[cmd]
            self.send(getattr(self, name)(text.split(None, 1)[1] if " " in text else "") if name else HELP)
            return
        urls, _ = intake.find_urls(text)
        if urls:
            self.submit(self.link_job, text)
            return
        from .. import talk  # one brain with the app (RA-058): a question is answered, anything else is sorted
        self.submit(self.ask_job if talk.is_question(text) else self.dump_job, text)

    def dump(self, con, text: str, prefix: str = "") -> str:
        db.log(con, "me", "telegram_dump", text[:200])
        summary = dump.process(con, text, source="telegram")  # re-ranks at the source
        return prefix + summary

    def dump_job(self, con, text: str) -> None:
        self.send(self.dump(con, text))

    def link_job(self, con, text: str) -> None:
        """Links → intake (page content is data); Djeddi's own words around them → dump."""
        urls, rest = intake.find_urls(text)
        conf = intake.cfg()
        limit = int(conf.get("max_links_per_message", 5))
        replies = [intake.describe(intake.add_link(con, u, rest, conf)) for u in urls[:limit]]
        if len(urls) > limit:
            replies.append(f"Only the first {limit} links were saved.")
        if len(rest.split()) >= 2:  # a real note, not just "this" or "👀"; the whole message, so the task can cite the link
            replies.append(self.dump(con, intake.redact(text)))
        else:
            loop.run_cycle(con)
        self.send("\n\n".join(replies))

    def file_job(self, con, f: dict, name: str, caption: str) -> None:
        conf = intake.cfg()
        is_image = Path(name).suffix.lower() in intake.IMAGE_SUFFIXES or (f.get("mime_type") or "").startswith("image/")
        is_pdf = Path(name).suffix.lower() == ".pdf" or (f.get("mime_type") or "") == "application/pdf"
        max_mb = (conf.get("max_image_mb", 10) if is_image else conf.get("max_pdf_mb", 20) if is_pdf
                  else self.cfg.get("max_voice_mb", 20))
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[-80:].lstrip(".") or "file"
        if is_image and Path(safe).suffix.lower() not in intake.IMAGE_SUFFIXES:  # the reader needs the real type
            mime = (f.get("mime_type") or "image/jpeg").split("/")[-1]
            safe += {"png": ".png", "webp": ".webp", "gif": ".gif"}.get(mime, ".jpg")
        dest = intake.FILES_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-{safe}"
        try:
            path = download(self.token, f["file_id"], dest, int(float(max_mb) * 1024 * 1024))
        except (TelegramError, OSError) as e:
            self.send(f"Could not download that file ({e}).")
            return
        row = intake.add_image(con, path, caption) if is_image else intake.add_file(con, path, caption)
        reply = intake.describe(row)
        if len(caption.split()) >= 2:
            reply += "\n\n" + self.dump(con, caption)
        self.send(reply)

    def voice_job(self, con, voice: dict) -> None:
        max_bytes = int(self.cfg.get("max_voice_mb", 20)) * 1024 * 1024
        name = re.sub(r"[^A-Za-z0-9_-]", "", voice.get("file_unique_id", "")) or secrets.token_hex(6)
        dest = VOICE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.oga"
        try:
            path = download(self.token, voice["file_id"], dest, max_bytes)
        except (TelegramError, OSError) as e:
            self.send(f"Could not download the voice note ({e.__class__.__name__}).")
            return
        try:
            text = models.transcribe(path)
        except models.ModelUnavailable as e:
            from .. import talk
            talk.keep_untranscribed(con, path, e)
            con.commit()
            self.send("Saved the voice note, but transcription needs the GPU PC (set HQ_WHISPER_URL). "
                      "It is kept in memory and goes into your plan once a Whisper tier answers (hq cycle retries hourly).")
            return
        self.send(self.dump(con, text, prefix=f"Heard: {text[:300]}\n\n"))

    # -------- loop --------
    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            self.on_callback(update["callback_query"])
        elif "message" in update:
            self.on_message(update["message"])

    def poll_once(self, timeout: int | None = None) -> int:
        timeout = int(self.cfg.get("poll_seconds", 25)) if timeout is None else timeout
        offset = int(db.get_state(self.con, "telegram_offset", "0"))
        updates = get_updates(self.token, offset, timeout)
        for u in updates:
            # advance the offset first, so a message that crashes the handler is not retried forever
            db.set_state(self.con, "telegram_offset", u["update_id"] + 1)
            self.con.commit()
            try:
                self.handle(u)
            except Exception as e:
                db.log(self.con, "hq", "telegram_error", f"{e.__class__.__name__}: {str(e)[:150]}")
                self.con.commit()
                try:
                    self.send(f"Something went wrong handling that ({e.__class__.__name__}); it is logged.")
                except Exception:
                    pass
        self.push_approvals()
        return len(updates)

    def run(self, connect=None) -> None:
        self.start_worker(connect or (lambda: _connect()))
        db.log(self.con, "hq", "telegram_start", f"chat {self.chat_id}")
        self.con.commit()
        backoff = 5
        while True:
            try:
                self.poll_once()
                backoff = 5
            except KeyboardInterrupt:
                raise
            except Exception as e:  # network down, Telegram hiccup, database briefly locked: wait and retry
                try:
                    self.con.rollback()
                    db.log(self.con, "hq", "telegram_poll_error", f"{e.__class__.__name__}: {str(e)[:150]}")
                    self.con.commit()
                except Exception:
                    pass
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)


def _connect():
    con = db.connect()
    db.init(con)
    return con


def get_updates(token: str, offset: int, timeout: int) -> list:
    """Long-poll: Telegram holds the request up to `timeout` seconds until a message arrives."""
    return api(token, "getUpdates", {"offset": offset, "timeout": timeout,
                                     "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 10) or []


def from_env(con) -> Bot:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see README → Telegram).")
    if not chat:
        raise SystemExit("TELEGRAM_CHAT_ID is not set. Message your bot, then run `hq bot --whoami`.")
    return Bot(con, token, chat)


def whoami() -> str:
    """List chats that recently messaged the bot, to find TELEGRAM_CHAT_ID. Reads only; does not reply."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
    seen, groups = {}, set()
    for u in get_updates(token, 0, 0):
        c = (u.get("message") or {}).get("chat") or {}
        if c.get("type") == "private":
            seen[c["id"]] = c.get("username") or c.get("first_name") or "?"
        elif c:
            groups.add(c.get("title") or str(c.get("id")))
    note = f"\n(Ignored group chats: {', '.join(sorted(groups))}. Atlas only works in a private chat.)" if groups else ""
    if not seen:
        return "No private messages yet. Open your bot in Telegram, send it any message, then run this again." + note
    return "\n".join(f"TELEGRAM_CHAT_ID={cid}   ({who})" for cid, who in seen.items()) + note
