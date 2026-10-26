# Run Reasoning Atlas locally
Install Python 3.11+ and Node.js 22.12+ (tested: Python 3.12.10, Node 24.12.0). In File Explorer, double-click **Start Atlas.cmd** in the repository. It installs locked dependencies when needed, builds the client, and opens the app at http://127.0.0.1:4310. Refresh once if the browser opens before the service. Keep the service window open; closing it stops the app. Reopen the launcher to restart. Saved notes remain in ignored runtime/atlas.db.

For development: `npm ci`, `npm run build`, then `npm start`. You can keep the app open while it is rebuilt: each build becomes a new release and open windows switch to it by themselves. Tests: `npm test`. The Python service has no third-party dependencies; requirements.lock records that decision. package-lock.json pins web dependencies.

The service binds to loopback. Notes use SQLite transactions and idempotent client UUIDs. The Engine boundary reports synthetic mode, no connection and no live capabilities. Integration normalization is deferred to RA-008. Initial database creation is schema version 1; no existing deployed database is migrated.

The visual app is the owner's priority. Unbuilt navigation remains visible and labeled. The component tracker reads repository task status and refreshes periodically so new checkpoints appear without rebuilding.

## Watching construction
A bar under the title shows the active task, step and percentage while an agent works. Build progress lists the steps. "Paused" means the builder stopped reporting for 15 minutes, for example after a usage limit; nothing is lost.
Agents report progress with:
```
python scripts/progress.py start RA-020 "Module manifests" "Write schema" "Write tests" "Build client" "Save checkpoint"
python scripts/progress.py step 2
python scripts/progress.py done "Checkpoint saved"
```

## Reload and updates
The ⟳ button in the header reloads the screen. Screen changes apply by themselves after a build. When the service code changes (for example after pulling new commits), an orange dot appears on ⟳ with **Update ready · Restart**. Restart rebuilds the screen and restarts the service from the launcher window, then reloads the page; unsaved note drafts are kept. If Atlas was started without `Start Atlas.cmd`, the marker asks you to reopen the launcher instead. The launcher installs dependencies only when `package-lock.json` changes.

## Continue building from Atlas
Build progress has a **Continue building** panel. It lists the active or paused task first, then tasks in review and tasks ready to start. "Continue in Claude app" or "Continue in Codex" copies a handoff text with the task, where it stopped, what to read first and the working rules; paste it into that app. The app's progress then appears in Atlas. Atlas logs each switch, so the most frequent ones show what Atlas should learn to do itself.

