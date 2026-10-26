# Compute strategy: who does what

Principle: **the Pi is always on, the GPU PC is muscle on demand, the cloud is the thinker.**
Pay for capability you actually use; avoid subscriptions that overlap.

| Machine / service | Role in the Engine | Why |
|---|---|---|
| **Raspberry Pi** (always on, ~3 W) | Host: memory DB, hourly loop, radar, dashboard, Telegram bot, schedules, backups to NAS | Costs almost nothing to run 24/7; replaces a VPS |
| **GPU PC** (woken when needed) | Local brain via Ollama: radar summaries, tagging, transcription (faster-whisper), embeddings, private documents | Free per use, private, fast for bulk jobs; sleeps otherwise |
| **Claude API** (pay per use) | Planning (`hq think`), brain-dump extraction, project packages | Best reasoning; typically a few euros a month at the Engine's volume. Set a spend limit in the console |
| **Claude subscription** (Pro/Max) | You + Claude Code building the Engine itself | Interactive development; separate from the Engine's own API usage |
| **ChatGPT** | Optional | Only worth paying for if it does something specific you use weekly; otherwise it overlaps |
| **NAS** | Backups of `hq.db`, storage for datasets and recordings | Already owned |
| **Tailscale** (free tier) | Private network joining Pi, PC, phone, NAS | Dashboard reachable from school without opening ports |

## Routing (config/constitution.toml → [models.routing])
- `think`, `dump` → cloud first, local fallback
- `summarise` and other bulk jobs → local first, cloud fallback
- If the PC is asleep, the Pi sends Wake-on-LAN, waits, runs the job, and lets it sleep again (Phase 2)

## Choosing the local model
Fit the model to the GPU's VRAM (rule of thumb at 4-bit quantisation: ~0.6 GB per billion parameters
plus headroom). Around 8 GB → 7–8B models; 12–16 GB → 12–14B; 24 GB → up to ~32B. Benchmark two or three
on the Engine's real jobs (radar summaries, dump extraction) before settling; record the result with
`hq add memory "..." --category decision`.

## When to spend on more
Only through an expansion request in the approval queue, with: what it enables, monthly cost, and
what it replaces. Examples: a larger SSD for the Pi, more API budget during a heavy build month.

## Per-job model settings (RA-082)
A job can override the shared model settings in its own table; anything not set there uses `[models]`:

```toml
[models.jobs.chat]
model = "claude-opus-5-5"     # cloud tier (needs ANTHROPIC_API_KEY); default: cloud_model
thinking_tokens = 4000        # thinking time before the answer (cloud tier; on the subscription tier via MAX_THINKING_TOKENS)
max_tokens = 8000             # longest answer (cloud tier); default 2500
claude_code_model = "opus"    # subscription tier: passed to `claude --model`
```
Only the owner edits `config/constitution.toml`.
