# The repository's RA steps in the plan (RA-068, replaces the RA queue of RA-067)

The repository's build plan stays `STATE/tasks.json`. There is no separate queue or card for it. `engine/atlasplan.py` mirrors it into the Engine's plan:

- every RA step that is not done is one task, titled `RA-xxx · title`, under the goal **Build Reasoning Atlas**, owned by the builder and placed first in its queue;
- a step marked done in the repository is closed in the plan (change log, undoable); a step in review, waiting on another step or held by the owner (`automationHold`) cannot start, and the reason shows on the task;
- a task the owner dropped is never brought back; tasks keep their run history (titles from the earlier queue, `Atlas build plan: RA-xxx …`, are recognised and renamed);
- the sync runs by itself: every minute in `atlas-builder-watch.timer` when the plan changed, and in the hourly cycle.

The same watcher starts the next run after Claude's usage limit resets when autonomy is Continuous; it never forces a run, and pause, the stop switch, daily budgets and review limits still hold. Builder settings (the gear in Forge › Build) hold the daily budget, merging (the builder may merge its own RA pull requests after the Pi's tests pass), autonomy, build mode and review batch.
