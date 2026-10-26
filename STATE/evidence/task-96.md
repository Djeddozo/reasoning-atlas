# Task #96: Repair failing tests on main (evidence)

Date: 2026-10-26. Checked by a manual Claude Code session on the Pi.

## Check
`python3 -m unittest discover tests` on origin/main at cda485e (Merge PR #28), in a full git clone.

Result: `Ran 769 tests in 106.694s` — `OK (skipped=1)`.

The failure recorded in the task (test_selfupdate `test_health_reports_running_commit` on 7146253) no longer
occurs; that test passes on cda485e.

## Note on a false alarm
Running the same suite in a `git archive` export (no `.git`) gives one failure and one error:
`test_review_batch.test_unlimited_batch_never_stops_for_review` (`devloop.stack_base` needs git) and
`test_updater.LocalSettingsTest.test_enable_script_moves_old_settings_out_of_the_constitution` (clones the tree
with git). Both pass in a git checkout. The updater runs tests in a git tree, so no change is needed; a test run
for evidence must use a clone, not an export.

## Outcome
No code change. Main is green; this file is the evidence for closing the task.
