# Task #188: stronger automatic AI jobs within bounds (evidence)

Date: 2026-10-26. Built in a manual Claude Code session on the Pi.

## In code (this PR)
1. **Room to answer** (`engine/models.py`): `max_tokens` per job: `[models.jobs.<job>]`, then `[models]`, then the code
   default of 12000 for reevaluate and doover and 6000 for every other job (was 2500 for all). Ceiling 32000.
   A cut-off answer (cloud `stop_reason` max_tokens, local `done_reason` length, an incomplete JSON answer on Claude
   Code for a JSON job) is retried once with double the room. Still cut off: the error says "answer was cut off",
   not "no model reachable". Claude Code gets the same room through `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.
2. **Context for reevaluate and doover** (`engine/brain.py`, which `doover` reuses): open tasks up to 150 with the
   first 200 characters of the description; the 25 most relevant decisions, preferences and lessons at up to 600
   characters; thoughts from the last 30 days, up to 40. Above about 60k tokens (4 characters per token) the least
   relevant items are dropped first (ideas, memories, thoughts, then the lowest-ranked tasks, each to a floor), logged
   as `context_trimmed` and named in the prompt.
4. **Cloud timeout per job**: `[models.jobs.<job>] timeout`, default 300 s (was 120 s without thinking), ceiling 900 s.
   `claude_code_timeout` per job already worked.

## For the owner (approval; Atlas does not edit the constitution)
Parts 3 and 5. Add to `config/constitution.toml`:

```toml
[models.jobs.reevaluate]
model = "claude-opus-5-5"
thinking_tokens = 6000
claude_code_model = "opus"
timeout = 600
claude_code_timeout = 600

[models.jobs.doover]
model = "claude-opus-5-5"
thinking_tokens = 6000
claude_code_model = "opus"
timeout = 600
claude_code_timeout = 600

[models.jobs.revisit]
model = "claude-opus-5-5"
thinking_tokens = 6000
claude_code_model = "opus"
timeout = 600
claude_code_timeout = 600

[models.jobs.overlap]
model = "claude-sonnet-5-5"
thinking_tokens = 2000
claude_code_model = "sonnet"

[models.jobs.triage]
model = "claude-sonnet-5-5"
thinking_tokens = 2000
claude_code_model = "sonnet"
```

and change these lines in `[budgets]`:

```toml
reevaluate = 6
doover = 4
revisit = 4
overlap = 15                              # matches [overlap] max_items
```

The timeouts of 600 s for the Opus jobs are an addition of this session: Opus with 6000 thinking tokens and up to
12000 output tokens can need more than the default 300 s. Leave them out to keep 300 s.

## Before and after (with the approval applied)

| Job | Model | Thinking | max_tokens | Timeout (cloud / Claude Code) | Budget per day | Context |
|---|---|---|---|---|---|---|
| reevaluate | Sonnet → **Opus** | 0 → **6000** | 2500 → **12000** | 120 / 300 → **600 / 600** | 2 → **6** | 60 tasks, titles only; 6 memories × 300; 14 days / 30 thoughts → **150 tasks + 200-char description; 25 × 600; 30 days / 40; trimmed above ~60k tokens** |
| doover | Sonnet → **Opus** | 0 → **6000** | 2500 → **12000** | 120 / 300 → **600 / 600** | 2 → **4** | as reevaluate, plus visions, ideas, goals, ATLAS NOW (unchanged) |
| revisit | Sonnet → **Opus** | 0 → **6000** | 2500 → **6000** | 120 / 300 → **600 / 600** | 2 → **4** | unchanged |
| overlap | Sonnet | 0 → **2000** | 2500 → **6000** | 120 / 300 → **300 / 300** | 6 → **15** | unchanged |
| triage | Sonnet | 0 → **2000** | 2500 → **6000** | 120 / 300 → **300 / 300** | 12 (unchanged) | unchanged |

Without the approval only the code changes apply: max_tokens, the retry, the 300 s cloud default and the fuller context.
With thinking, the cloud call's max_tokens is at least thinking + 2000 (unchanged rule).

## Checks
`python3 -m unittest discover tests`: 782 tests, OK (1 skipped). New: tests/test_models_truncation.py
(room per job, retry with double room, cut-off reported, timeout ceiling, incomplete JSON on Claude Code,
context sizes, trimming order and log).
