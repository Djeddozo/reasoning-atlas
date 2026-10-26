"""Register a versioned guide here when adding a substantial interactive feature."""
GUIDES = [
    {"id": "build-briefings-v1", "title": "Understand why a feature matters", "steps": [
        {"tab": "today", "target": "recommended", "text": "Use Why this? on a recommendation to open its build briefing: problem, timing, expected result, goal, dependencies and uncertainty."},
        {"tab": "plan", "view": "tasks", "target": "allList", "text": "Every task has the same explanation in Task overview. Missing reasoning is labelled honestly; Re-evaluate tasks refreshes the agent’s recommendations once Claude is connected."}]},
    {"id": "build-now-v1", "title": "Build now or save for later", "steps": [
        {"tab": "today", "target": "dump", "text": "Describe the feature or fix you want Atlas to build. This text becomes the full build request."},
        {"tab": "today", "target": "buildNowBtn", "text": "Build now creates a task and immediately starts the existing agent. If it cannot start, the reason stays visible and the task is saved. Signing in to Claude may be required."},
        {"tab": "today", "target": "laterThought", "text": "For thoughts you do not want to build yet, open Save a thought for later and choose Add to brain."}]},
    {"id": "task-overview-v1", "title": "Find old and new work", "steps": [
        {"tab": "plan", "view": "tasks", "target": "fSearch", "text": "Task overview is the central place to find work. Search titles, descriptions or a task number such as #28."},
        {"tab": "plan", "view": "tasks", "target": "fSort", "text": "Switch between newest, oldest and highest or lowest priority. Each task shows its creation date and priority score."},
        {"tab": "plan", "view": "tasks", "target": "fCreatedFrom", "text": "Use Created from and to to find when a feature entered the plan. Unknown historical dates remain labelled as unknown."},
        {"tab": "plan", "view": "tasks", "target": "fRecommendation", "text": "Recommended now uses Atlas’s current advice and shows the reason. This is different from the agent’s execution order: your explicitly queued work runs first."}]},
    {"id": "claude-reconnect-v1", "title": "Reconnect Claude", "steps": [
        {"tab": "system", "view": "connections", "target": "claudeConnection", "text": "Use Check connection when Claude stops responding. Reconnect Claude starts the official sign-in flow."},
        {"tab": "system", "view": "connections", "target": "claudeConnection", "text": "Open Claude sign-in, approve your existing account, and paste the returned code into Atlas if asked. Codes stay out of Atlas’s logs and database. Cancel or start again if the link expires."}]},
    {"id": "automatic-repair-v1", "title": "Automatic crash recovery", "steps": [
        {"tab": "system", "view": "recovery", "target": "recoveryCard", "text": "Atlas records unexpected server errors, browser crashes and service restarts here. Repeated occurrences are grouped into one repair task."},
        {"tab": "system", "view": "recovery", "target": "recoveryCard", "text": "The watchdog runs independently of the app. It starts repairs within the agent’s limits. Small repairs with regression tests can be tested, merged and installed automatically; protected or broader changes remain for review. An expired Claude login requires Reconnect Claude."}]},
    {"id": "layout-v4", "title": "Find your way: Today, Forge, Research, System", "steps": [
        {"tab": "today", "target": "glance", "text": "Today is your day at a glance: what needs you, what the agent is building, what you finished and your next step."},
        {"tab": "forge", "target": "forgeFlow", "text": "Forge is the workshop where raw ideas become real things, in three stages: Ideas → Plan → Build. This strip moves you between them and shows how much sits in each."},
        {"tab": "research", "view": "feed", "target": "researchFeed", "text": "Research watches AI labs, papers, releases and open source for what could improve what you build, and says why. Save a finding, turn it into an idea, or explore it deeper."},
        {"tab": "system", "view": "constitution", "target": "sysNav", "text": "System manages Atlas itself: connections, agent limits, the constitution, recovery, voice, costs, updates and appearance."}]},
    {"id": "idea-flow-v1", "title": "From idea to plan to build", "steps": [
        {"tab": "brain", "target": "ideaList", "text": "A new idea never lands in the plan as a loose task. Atlas suggests the goal it serves, with the reason, or proposes it as a new goal. ✦ AI means Claude placed it; rule means word overlap."},
        {"tab": "brain", "target": "ideaList", "text": "Add to plan puts it under that goal as a task. Start as new goal creates the goal with the idea as its first step. Other goal… lets you choose yourself."},
        {"tab": "plan", "view": "master", "target": "goals", "text": "In the master plan each goal shows its tasks and how many ideas would fit it. → Build hands a task to the agent's queue."},
        {"tab": "plan", "view": "master", "target": "forgeFlow", "text": "The Forge strip at the top shows the flow Ideas → Plan → Build and how much sits in each stage; tap a stage to open it."}]},
    {"id": "live-build-v1", "title": "Watch the agent build", "steps": [
        {"tab": "build", "view": "live", "target": "pipeline", "text": "The pipeline shows where work stands: queued, building, waiting for your review, and recently merged. Tap a step to open it."},
        {"tab": "build", "view": "live", "target": "agentNow", "text": "The run in progress: which task, how much of its time limit is used, and what the agent is doing right now."},
        {"tab": "build", "view": "live", "target": "agentLog", "text": "Every step streams in live, like Claude Code in a terminal: what the agent says, which tool it uses (edit, run, read) and when it finishes. Scroll up to read back; it keeps following otherwise."}]},
    {"id": "terminal-steps-v1", "title": "Commands left for your terminal", "steps": [
        {"tab": "today", "target": "cmdCard", "text": "When Claude finished something but a shell command is still needed (for example while you were away), it appears here first, as your highest priority."},
        {"tab": "today", "target": "cmdCard", "text": "Copy command gives one line for Termius; the comment after # says what it is for. Copy for Claude gives a prompt with the context: type claude in Termius first, then paste, and Claude explains, runs and fixes it. Mark it Done afterwards. On Telegram: /terminal."}]},
    {"id": "usage-limit-v1", "title": "Work stopped by a usage limit", "steps": [
        {"tab": "today", "target": "intrCard", "text": "When Claude Code or Codex stops because a usage limit is reached, Atlas finds it in their session logs within five minutes and puts it here first. It is unfinished work, never marked done."},
        {"tab": "today", "target": "intrCard", "text": "Copy resume command reopens the same session with everything it knew; paste it in Termius once the limit has reset. Copy context gives a prompt for a fresh session. The item disappears by itself once the session continues."}]},
    {"id": "research-v1", "title": "Research: what is new, and why it matters", "steps": [
        {"tab": "research", "view": "feed", "target": "researchFeed", "text": "Only developments that touch your work show here, most relevant first, each with why it matters. Scans are light: feeds and public APIs, no AI calls."},
        {"tab": "research", "view": "deep", "target": "deepQ", "text": "Deep research answers one question for your situation: what it is, why it matters for Atlas, risks and next steps. One model call; the answer is kept and can become an idea."},
        {"tab": "research", "view": "sources", "target": "researchForm", "text": "Choose the sources, how often Atlas scans and how much it keeps. Add the GitHub repositories whose releases you want to follow."}]},
    {"id": "build-next-v1", "title": "Build next: tasks you want built first", "steps": [
        {"tab": "build", "view": "queue", "target": "build-next-lane", "text": "Build next is a short lane at the top of Forge › Build. The builder takes these tasks first, in this order, before your queue and before any score, so a task you place here never drifts down."},
        {"tab": "build", "view": "queue", "target": "build-next-lane", "text": "Press ⏭ on a task in Up next or in its details to add it; drag to reorder; ✕ takes it out. A task leaves the lane by itself once it is built or dropped. Every change can be undone, and Cortex can propose \"build #N next\"."}]},
    {"id": "builder-queue-v1", "title": "The whole builder queue", "steps": [
        {"tab": "build", "view": "queue", "target": "build-next-lane", "text": "All N under Up next opens the whole builder queue: Build next on top, then every task in build order, with its place number, and what cannot start yet."},
        {"tab": "build", "view": "queue", "target": "build-next-lane", "text": "Type in the filter to find a task by title or #id. A link like ?queue&highlight=180 opens the queue at that task, and Cortex can show you a task there."}]},
]
