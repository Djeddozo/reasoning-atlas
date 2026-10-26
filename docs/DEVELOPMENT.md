# Development and resumption through desktop apps

## Shared contract
The repository preserves intent, tasks, decisions and evidence. Chat is assistance, not the only storage. Use short task branches and separate worktrees for concurrent agents. Commit before handoff and push so another machine can fetch the work; local commits alone are not remote backups.
English is required for all new project artifacts and source code. Use English branch names, such as feat/ra-002-data-contracts, and English commit/issue text. Preserve historical Git objects and external API identifiers.

## No owner-operated CLI required
1. Clone Djeddozo/Unconventional-Reasoning-Atlas using GitHub Desktop and verify the local destination.
2. Open it as a Codex project. Select the checkout or an explicitly desired worktree; provide the HANDOFF.md resume prompt.
3. In Claude desktop, use its Code environment, select the same repository on a separate branch/worktree and provide the same prompt. A Chat tab with a URL is not local code access.
4. Review changed files, checks and commits in the app. Use GitHub Desktop/GitHub to publish branches and review changes if the agent cannot.
5. In a later session, open the same branch or fetch pushed commits into another checkout. Compare Git state and checkpoint before continuing.

Codex supports project/worktree selection and review. Claude Code desktop supports local projects and isolated Git worktrees. UI labels, account access and installed versions may vary; a read-only GitHub connector does not imply execution rights. Official sources are in docs/SOURCES.md.
Desktop apps remain on Windows. The Pi hosts services and optionally the existing Engine runner; installing a desktop app on Pi is not required.

## Resume protocol
Inspect remote, branch, head, diff and pending files. Read active-task.json; verify whether another writer is active before taking over an expired claim. Review the last successful check and repeat only what is needed for the current state.
Claim one ready task with session ID/owner/UTC lease. Deliver one small vertical step with evidence and checkpoint. Verify the intended branch before pushing; never force push.

## Usage limits
Aim for checkpointable steps of roughly 15–45 minutes, as a guideline. Record progress after every meaningful step.
On a cap, record waiting_for_provider, reset time only if known, branch/commit, changed files, last check, uncertain external outcomes and the exact next action.
After an abrupt stop, inspect the working tree and command ledger. Never replay an external side effect merely because a chat stopped.
A replacement provider receives necessary context and the same tests and acceptance criteria. Do not silently enable a paid fallback.

## Claims and conflicts
STATE/active-task.json contains taskId, owner, sessionId, branch, leaseExpiresAt and lastCheckpointCommit.
Git is not a global lock: inspect the remote and publish claims. If work overlaps, coordinate through the PR/issue, choose another task or wait. An expired lease never authorizes deleting another agent's changes.
Reconcile conflicting state files against commits and executed checks, not whichever text happens to be newer.

## Definition of done
Acceptance criteria evidenced, relevant tests passing, documentation updated, no secrets, consistent state, commit and evidence discoverable. A mock is not live integration; a screenshot is not a functional test; a passing blueprint validator is not a finished product.
