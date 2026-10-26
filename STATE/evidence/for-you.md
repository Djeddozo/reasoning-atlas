# Notes › For you (owner request, 2026-10-23)

**Request.** A separate section in Notes for what the owner has to do, with a detailed explanation and
context, room for the owner's input, and the same information available to later CLI sessions.
First item: the GitHub portfolio checklist, including the questions already answered.

**Implemented.**
- `services/atlas/actions.py`: items from `STATE/owner-actions.json` (agents write them, versioned);
  step ticks, owner input and done live in the local SQLite database (`action_steps`, `action_state`,
  schema version 5) and never change the repository file.
- API: `GET /api/actions`, `POST /api/actions/<id>/step|input|done` (behind the existing Origin check).
- Screen: Notes › For you — context, checklist with "How" (details and copyable commands), answered
  questions, "Asked of you", an input box, **Copy as prompt** for a new Claude/Codex session, Mark done.
- CLI: `python -m services.atlas.actions list | show <id> | prompt <id>` (reads the app database read-only).

**Evidence.** `tests/test_actions.py` (8 tests); full suite 664 tests OK; `tsc --noEmit` and the Vite
build pass. Screenshots from a scratch server and database: `for-you/for-you.jpg` (checklist with one
step ticked and its How opened), `for-you/for-you-bottom.jpg` (answered questions, saved input).
