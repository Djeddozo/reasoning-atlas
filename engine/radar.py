"""Radar: daily scan of new developments, ranked against Djeddi's goals and interests.

Sources (all public, no keys needed; GITHUB_TOKEN optional for higher rate limits):
  github    – new repositories gaining stars fastest (last 7 days)
  hn        – Hacker News stories with real traction (last 24 h)
  arxiv     – newest AI / ML papers
  hf        – trending models on Hugging Face
  labs      – announcements of OpenAI and Google DeepMind (RSS; Anthropic has no feed, see releases)
  hfpapers  – Hugging Face daily papers, and the Hugging Face blog (RSS)
  releases  – new releases of the tools Atlas is built with (GitHub Atom feeds; Claude Code, Codex, Ollama, …)

Research settings (dashboard Research → Sources & limits, stored in state 'research_settings'): which sources,
how often (`scan --due` runs only when the interval has passed), how many items per source, which repositories
to follow, and the focus words that make an item relevant. Everything is RSS/JSON over plain HTTP: no model calls,
deduplicated by (source, id), so a scan costs a few small downloads.

Every item is stored in the `radar` table (deduplicated), scored for relevance, and the top items
of each scan are written to memory so `hq think` and the dashboard can use them.
Content from these sources is untrusted data: it is displayed and scored, never executed or obeyed.
"""
import json
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from . import db


UA = {"User-Agent": "hq-radar/1.0"}
SOURCE_WEIGHT = {"github": 1.0, "hn": 0.9, "arxiv": 0.8, "hf": 0.8}


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def fetch_github(n=30):
    since = (date.today() - timedelta(days=7)).isoformat()
    q = urllib.parse.quote(f"created:>{since}")
    h = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    data = json.loads(_get(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={n}", h))
    for r in data.get("items", []):
        yield dict(source="github", external_id=r["full_name"], title=r["full_name"], url=r["html_url"],
                   summary=(r.get("description") or "")[:300], popularity=r.get("stargazers_count", 0),
                   extra=" ".join([r.get("language") or ""] + r.get("topics", [])))


def fetch_hn(n=30):
    since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           f"&numericFilters=points%3E80,created_at_i%3E{since}&hitsPerPage={n}")
    for h in json.loads(_get(url)).get("hits", []):
        yield dict(source="hn", external_id=h["objectID"], title=h.get("title") or "",
                   url=h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                   summary="", popularity=h.get("points", 0), extra="")


def fetch_arxiv(n=30):
    url = ("https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={n}")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(_get(url))
    for e in root.findall("a:entry", ns):
        aid = e.findtext("a:id", "", ns)
        yield dict(source="arxiv", external_id=aid.rsplit("/", 1)[-1], url=aid,
                   title=" ".join(e.findtext("a:title", "", ns).split()),
                   summary=" ".join(e.findtext("a:summary", "", ns).split())[:300], popularity=0, extra="")


def fetch_hf(n=20):
    for m in json.loads(_get(f"https://huggingface.co/api/models?sort=trendingScore&limit={n}")):
        yield dict(source="hf", external_id=m["id"], title=m["id"], url=f"https://huggingface.co/{m['id']}",
                   summary=m.get("pipeline_tag") or "", popularity=m.get("likes", 0),
                   extra=" ".join(m.get("tags", [])[:10]))


def _feed(source: str, url: str, n: int, label: str = ""):
    """Any RSS 2.0 or Atom feed: newest n entries."""
    root = ET.fromstring(_get(url))
    atom = "{http://www.w3.org/2005/Atom}"
    entries = root.findall(f"{atom}entry") or root.findall("./channel/item")
    for e in entries[:n]:
        if e.tag.startswith(atom):
            link = next((l.get("href") for l in e.findall(f"{atom}link") if l.get("rel", "alternate") == "alternate"), "")
            title, ident = e.findtext(f"{atom}title", ""), e.findtext(f"{atom}id", "") or link
            summary = e.findtext(f"{atom}summary", "") or e.findtext(f"{atom}content", "")
        else:
            link, title = e.findtext("link", ""), e.findtext("title", "")
            ident, summary = e.findtext("guid", "") or link, e.findtext("description", "")
        summary = " ".join(re.sub(r"<[^>]+>", " ", summary or "").split())[:300]
        title = " ".join((title or "").split())
        if title and link:
            yield dict(source=source, external_id=ident[:300], title=(f"{label}: " if label else "") + title, url=link,
                       summary=summary, popularity=0, extra=label)


LAB_FEEDS = {"OpenAI": "https://openai.com/news/rss.xml", "Google DeepMind": "https://deepmind.google/blog/rss.xml"}
DEFAULT_REPOS = ["anthropics/claude-code", "openai/codex", "ollama/ollama", "ggml-org/llama.cpp",
                 "modelcontextprotocol/servers", "huggingface/transformers"]


def fetch_labs(n=10):
    for label, url in LAB_FEEDS.items():
        try:
            yield from _feed("labs", url, n, label)
        except Exception:
            continue  # one lab down never hides the others


def fetch_hfpapers(n=15):
    for p in json.loads(_get(f"https://huggingface.co/api/daily_papers?limit={n}")):
        paper = p.get("paper") or {}
        if paper.get("id"):
            yield dict(source="hfpapers", external_id=paper["id"], title=paper.get("title", "").strip(),
                       url=f"https://huggingface.co/papers/{paper['id']}", summary=(paper.get("summary") or "")[:300],
                       popularity=paper.get("upvotes", 0), extra="paper")
    try:
        yield from _feed("hfpapers", "https://huggingface.co/blog/feed.xml", min(n, 10), "HF blog")
    except Exception:
        pass


def fetch_releases(n=3, repos=None):
    for repo in repos or DEFAULT_REPOS:
        try:
            for item in _feed("releases", f"https://github.com/{repo}/releases.atom", n, repo):
                item["popularity"] = 0
                yield item
        except Exception:
            continue


FETCHERS = {"github": fetch_github, "hn": fetch_hn, "arxiv": fetch_arxiv, "hf": fetch_hf,
            "labs": fetch_labs, "hfpapers": fetch_hfpapers, "releases": fetch_releases}
SOURCE_LABEL = {"github": "GitHub", "hn": "Hacker News", "arxiv": "arXiv", "hf": "HF models", "labs": "AI labs",
                "hfpapers": "HF papers", "releases": "Releases"}
SOURCE_WEIGHT.update({"labs": 1.3, "hfpapers": 0.9, "releases": 1.2})
# what makes a development matter for Atlas: words to look for, and what it could mean (rule-based, no model)
FOCUS = [
    (r"\bagent|agentic|multi-agent", "agents", "could make Atlas's build agent smarter or more autonomous"),
    (r"claude code|codex|coding|code generation|copilot|\bide\b|swe-bench", "coding tools", "could speed up or improve Atlas's dev loop"),
    (r"\bmcp\b|model context protocol|tool[ -]?(?:use|call)|function calling", "tools & MCP", "could connect Atlas to more tools and data"),
    (r"local|ollama|llama\.cpp|gguf|quantiz|on-device|self-hosted|edge", "local models", "could run privately on your GPU PC or the Pi"),
    (r"whisper|speech|voice|\btts\b|transcri", "voice", "could improve talking to Atlas"),
    (r"automat|workflow|pipeline|orchestrat", "automation", "could take a manual step off your plate"),
    (r"memory|retrieval|\brag\b|long context|embedding", "memory", "could improve what Atlas remembers and recalls"),
    (r"release|launch|introduc|announc|new model|gpt-|gemini|claude|sonnet|opus", "new capability", "a new model or feature Atlas may be able to use"),
]


def settings(con) -> dict:
    """Research settings, from the dashboard (state 'research_settings'), with safe defaults."""
    base = {"every_hours": 6, "sources": list(FETCHERS), "per_source": 20, "repos": DEFAULT_REPOS, "keep_days": 30}
    try:
        base.update(json.loads(db.get_state(con, "research_settings", "") or "{}"))
    except json.JSONDecodeError:
        pass
    base["every_hours"] = max(1, min(int(base["every_hours"]), 168))
    base["per_source"] = max(3, min(int(base["per_source"]), 50))
    base["sources"] = [x for x in base["sources"] if x in FETCHERS]
    base["repos"] = [r for r in base["repos"] if re.fullmatch(r"[\w.-]+/[\w.-]+", r)][:20]
    return base


def save_settings(con, new: dict) -> str:
    cur = settings(con)
    cur.update({k: v for k, v in new.items() if k in ("every_hours", "sources", "per_source", "repos", "keep_days")})
    db.set_state(con, "research_settings", json.dumps(cur))
    db.log(con, "me", "research_settings", json.dumps({k: cur[k] for k in ("every_hours", "per_source")}))
    con.commit()
    clean = settings(con)
    return f"Research scans every {clean['every_hours']} h, up to {clean['per_source']} items per source, {len(clean['sources'])} sources."


def last_scan(con) -> str:
    return con.execute("SELECT MAX(ts) FROM log WHERE action = 'radar_scan'").fetchone()[0] or ""


def due(con) -> bool:
    last = last_scan(con)
    if not last:
        return True
    return datetime.fromisoformat(last) <= datetime.now() - timedelta(hours=settings(con)["every_hours"])


def explain(item: dict) -> dict:
    """Why it matters and the likely impact on Atlas, from the item's own text (no model call)."""
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('extra', '')}".lower()
    topics = [(t, why) for pat, t, why in FOCUS if re.search(pat, text)]
    if not topics:
        return {"topics": [], "why": "", "impact": "low"}
    impact = "high" if len(topics) >= 3 or item.get("source") in ("labs", "releases") and len(topics) >= 2 else "medium" if len(topics) >= 2 else "low"
    return {"topics": [t for t, _ in topics], "why": topics[0][1][0].upper() + topics[0][1][1:] + ".", "impact": impact}


def tuning(con) -> dict:
    """Djeddi's adjustments to the interests (state 'radar_tuning'): mute = stop matching, boost = count double, add = extra words."""
    try:
        raw = json.loads(db.get_state(con, "radar_tuning", "") or "{}")
    except json.JSONDecodeError:
        raw = {}
    clean = lambda v: sorted({str(w).strip().lower() for w in (v if isinstance(v, list) else []) if re.fullmatch(r"[\w.+-]{2,30}", str(w).strip())})
    return {k: clean(raw.get(k)) for k in ("mute", "boost", "add")}


def save_tuning(con, mute=None, boost=None, add=None) -> str:
    """Replace the given adjustments (None keeps that list). Logged; the next scan ranks with them."""
    cur = tuning(con)
    for k, v in (("mute", mute), ("boost", boost), ("add", add)):
        if v is not None:
            cur[k] = v
    db.set_state(con, "radar_tuning", json.dumps(cur))
    cur = tuning(con)
    cur["boost"] = [w for w in cur["boost"] if w not in cur["mute"]]
    db.set_state(con, "radar_tuning", json.dumps(cur))
    db.log(con, "me", "radar_tuning", json.dumps(cur))
    con.commit()
    return f"Interests tuned: {len(cur['mute'])} muted, {len(cur['boost'])} boosted, {len(cur['add'])} added. The next scan ranks with them."


def interests(con, rules: dict) -> list[str]:
    words = [w.lower() for w in rules.get("radar", {}).get("interests", [])]
    stop = {"the", "and", "for", "with", "that", "this", "from", "into", "runs", "tells", "what", "next", "own", "my"}
    for (title,) in con.execute("SELECT title FROM goals WHERE status = 'active'"):
        words += [w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in stop]
    t = tuning(con)
    return sorted((set(words) | set(t["add"])) - set(t["mute"]))


def score_item(item: dict, keys: list[str], boost: list[str] | tuple = ()) -> tuple[float, list[str]]:
    text = f"{item['title']} {item['summary']} {item.get('extra', '')}".lower()
    hits = [k for k in keys if re.search(rf"\b{re.escape(k)}", text)]
    weight = sum(2 if k in boost else 1 for k in hits)
    pop = math.log10(1 + item["popularity"]) if item["popularity"] else 1.0
    return round(SOURCE_WEIGHT.get(item["source"], 1) * (1 + pop) * (1 + 0.6 * weight), 3), hits


def review(con, rules: dict, min_hits: int = 3) -> dict:
    """After the first scans: for each interest, how many findings it matched and what Djeddi did with them
    (saved / made an idea = useful, hidden = noise). Verdict by rule; no model call."""
    t = tuning(con)
    keys = sorted(set(interests(con, rules)) | set(t["mute"]))
    rows = con.execute("SELECT matched, state FROM radar WHERE matched != ''").fetchall()
    out = []
    for k in keys:
        n = useful = noise = 0
        for r in rows:
            if k in [m.strip() for m in r["matched"].split(",")]:
                n += 1
                useful += r["state"] in ("starred", "idea")
                noise += r["state"] == "dismissed"
        if k in t["mute"]:
            verdict = "muted"
        elif useful and useful >= noise:
            verdict = "useful"
        elif noise >= 2 and noise > useful:
            verdict = "noisy"
        elif n >= 10 and not useful:
            verdict = "unproven"
        elif n < min_hits:
            verdict = "quiet"
        else:
            verdict = "ok"
        out.append({"word": k, "hits": n, "useful": useful, "noise": noise, "verdict": verdict,
                    "boosted": k in t["boost"], "own": k in t["add"]})
    order = {"noisy": 0, "unproven": 1, "useful": 2, "ok": 3, "quiet": 4, "muted": 5}
    out.sort(key=lambda x: (order[x["verdict"]], -x["hits"], x["word"]))
    total = con.execute("SELECT COUNT(*) FROM radar").fetchone()[0]
    return {"interests": out, "tuning": t, "findings": total,
            "ready": total >= 20 and any(x["useful"] or x["noise"] for x in out),
            "note": "Saved findings and ideas count as useful, hidden ones as noise. Mute noisy words, boost useful ones (counted double), or add your own."}


def scan(con, rules: dict, sources: list[str] | None = None, fetchers: dict | None = None) -> dict:
    custom = fetchers is not None
    fetchers = fetchers or FETCHERS
    conf = settings(con)
    sources = sources or (conf["sources"] if not custom else rules.get("radar", {}).get("sources", list(fetchers)))
    keys = interests(con, rules)
    boost = tuning(con)["boost"]
    report, new_ids = {}, []
    for src in sources:
        try:
            added = 0
            fetch = fetchers[src]
            if custom:
                items = fetch()
            elif src == "releases":
                items = fetch(repos=conf["repos"])
            elif src in ("github", "hn", "arxiv"):
                items = fetch(conf["per_source"])
            else:
                items = fetch(max(3, conf["per_source"] // 2))
            for item in items:
                s, hits = score_item(item, keys, boost)
                cur = con.execute(
                    """INSERT OR IGNORE INTO radar(source, external_id, title, url, summary, popularity, matched, score, found)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (item["source"], item["external_id"], item["title"], item["url"], item["summary"],
                     item["popularity"], ", ".join(hits), s, db.now()))
                if cur.rowcount:
                    added += 1
                    new_ids.append(cur.lastrowid)
                else:  # refresh popularity of items we already know
                    con.execute("UPDATE radar SET popularity = ?, score = ? WHERE source = ? AND external_id = ?",
                                (item["popularity"], s, item["source"], item["external_id"]))
            report[src] = added
        except Exception as e:  # one failing source never stops the scan
            report[src] = f"error: {e.__class__.__name__}"
    top_n = rules.get("radar", {}).get("to_memory_per_scan", 5)
    if new_ids:
        marks = ",".join("?" * len(new_ids))
        for r in con.execute(f"SELECT * FROM radar WHERE id IN ({marks}) ORDER BY score DESC LIMIT ?", (*new_ids, top_n)):
            db.insert(con, "memories", kind="info", source="radar", tags=f"radar {r['source']}",
                      content=f"New on {r['source']}: {r['title']} — {r['summary'][:160]} ({r['url']})")
    db.log(con, "hq", "radar_scan", json.dumps(report))
    con.commit()
    if new_ids:
        from . import priority
        priority.rerank(con, "radar", f"{len(new_ids)} new items")
    return report


TOOL_SOURCES = {"github", "releases", "hf"}
TOOL_WORDS = re.compile(r"github\.com/|huggingface\.co/(?!papers)|\b(?:library|framework|sdk|cli|open[- ]source|self-hosted|plugin|"
                        r"extension|toolkit|server|app)\b|^show hn", re.I)
APPROACH = {"adopt": ("Adopt", "Use the existing tool as it is: install or connect it (outside code: sandbox and approval first)."),
            "adapt": ("Adapt", "Take parts of this open-source work and fit them into Atlas or a project (licence check first)."),
            "build": ("Build", "Build our own version of the underlying idea inside Atlas, without the outside code.")}


def kind(item: dict) -> str:
    """'tool' (something he can use, adopt or adapt) or 'development' (news, research, a new capability to know about)."""
    if item.get("source") in TOOL_SOURCES:
        return "tool"
    text = f"{item.get('url') or ''} {item.get('title') or ''}"
    return "tool" if item.get("source") == "hn" and TOOL_WORDS.search(text) else "development"


def set_state(con, rid: int, state: str, approach: str = "") -> int | None:
    """Keep/hide a finding, or turn it into an idea: plain ("→ Opportunity"), or with an approach for a tool
    (adopt | adapt | build), which names the route in the idea so Forge plans it that way. Returns the idea id."""
    con.execute("UPDATE radar SET state = ? WHERE id = ?", (state, rid))
    idea = None
    if state == "idea":
        r = con.execute("SELECT * FROM radar WHERE id = ?", (rid,)).fetchone()
        if approach in APPROACH:
            label, how = APPROACH[approach]
            idea = db.insert(con, "ideas", title=f"{label}: {r['title']}", category="system",
                             description=f"{how}\n{r['summary']}\n{r['url']}")
        else:
            idea = db.insert(con, "ideas", title=f"Explore: {r['title']}", category="system",
                             description=f"{r['summary']}\n{r['url']}")
    db.log(con, "me", f"radar_{state}", f"{rid} {approach}".strip())
    con.commit()
    return idea


def curated(con) -> dict:
    """Research: the curated feed (relevance and why it matters, by rule), saved findings, deep research notes, settings.
    Only what touches Atlas or the owner's interests; tools carry what has to be there first. No model calls."""
    from . import deepresearch, prereq, skilltree
    conf = settings(con)
    feed, caps = [], skilltree.nodes(con)
    for r in con.execute("""SELECT * FROM radar WHERE state IN ('new', 'starred') AND found >= datetime('now', ?)
                            ORDER BY score DESC LIMIT 200""", (f"-{int(conf['keep_days'])} day",)):
        item = dict(r)
        ex = explain(item)
        if ex["topics"] or item["matched"] or item["state"] == "starred":
            k = kind(item)
            extra = {"label": SOURCE_LABEL.get(item["source"], item["source"]), "kind": k}
            if k == "tool":  # before adopting: what has to be there first (skill tree, sandbox); rules only
                pre = prereq.check(con, f"{item['title']} {item['summary'] or ''} {item['url'] or ''}", caps=caps)
                extra["prereq"] = {"line": prereq.line(pre), "ready": pre["ready"]}
            feed.append(item | ex | extra)
    rank = {"high": 0, "medium": 1, "low": 2}
    feed.sort(key=lambda i: i["found"][:10] if i["found"] else "", reverse=True)
    feed.sort(key=lambda i: rank[i["impact"]])  # stable: most relevant first, newest first within each level
    from . import loop
    return {"feed": feed[:80], "review": review(con, loop.load_constitution()), "notes": deepresearch.notes(con), "settings": conf, "all_sources": SOURCE_LABEL,
            "last_scan": last_scan(con), "due": due(con)}
