import json
import tempfile
import sqlite3
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest import mock
from engine import atlasplan, db, devloop, skilltree


class AtlasPlan(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:'); self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close); db.init(self.con)
        self.steps=[{'id':'RA-001','title':'First','status':'ready','dependsOn':[]},
                    {'id':'RA-002','title':'Second','status':'todo','dependsOn':['RA-001']},
                    {'id':'RA-003','title':'Review','status':'review','dependsOn':[]},
                    {'id':'RA-004','title':'After review','status':'todo','dependsOn':['RA-003']}]
        patch=mock.patch.object(atlasplan,'repository_steps',return_value=self.steps)
        patch.start();self.addCleanup(patch.stop)
        work=tempfile.TemporaryDirectory();self.addCleanup(work.cleanup)  # a build holding this checkout's .devloop/lock never counts
        for p in (mock.patch.object(devloop,'WORK',Path(work.name)),mock.patch.object(devloop,'LOCK',Path(work.name)/'lock')):
            p.start();self.addCleanup(p.stop)

    def tid(self, ra):
        return next(r[0] for r in self.con.execute("SELECT id, title FROM tasks") if atlasplan.step_id({"title": r[1]}) == ra)

    def test_steps_are_plan_tasks_under_one_goal_without_a_button(self):
        legacy = db.insert(self.con, 'tasks', title=atlasplan.PREFIX + 'RA-001 · First', owner='ai'); self.con.commit()
        self.assertEqual(atlasplan.sync(self.con)['added'], 3)
        self.assertEqual(atlasplan.sync(self.con), {'added': 0, 'closed': 0, 'updated': 0})  # idempotent
        self.assertEqual(self.tid('RA-001'), legacy)  # history kept, title in the plain plan form
        row = self.con.execute("SELECT t.title, t.owner, t.queued_by, g.title FROM tasks t JOIN goals g ON g.id = t.goal_id WHERE t.id = ?",
                               (legacy,)).fetchone()
        self.assertEqual(tuple(row), ('RA-001 · First', 'ai', 'me', atlasplan.GOAL))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM goals").fetchone()[0], 1)

    def test_status_follows_the_repository(self):
        atlasplan.sync(self.con)
        dropped = self.tid('RA-004'); db.update(self.con, 'tasks', dropped, status='dropped')
        self.steps[0]['status'] = 'done'
        self.assertEqual(atlasplan.sync(self.con)['closed'], 1)
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (self.tid('RA-001'),)).fetchone()[0], 'done')
        self.assertEqual(self.con.execute("SELECT reason FROM brain_changes WHERE field = 'status' ORDER BY id DESC").fetchone()[0],
                         'RA-001 is done in the repository plan')
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id = ?", (dropped,)).fetchone()[0], 'dropped')  # never reopened
        self.assertEqual(atlasplan.block_reason({'title': 'RA-003 · Review'}), 'RA-003: repository status review')
        self.assertEqual(atlasplan.block_reason({'title': 'RA-002 · Second'}), '')  # its dependency is done now

    def test_auto_merge_switch(self):
        self.assertFalse(atlasplan.auto_merge(self.con))
        atlasplan.set_auto_merge(self.con, True)
        self.assertTrue(atlasplan.auto_merge(self.con))
        self.assertTrue(atlasplan.is_plan_task({'title': 'RA-002 · Second'}))
        self.assertFalse(atlasplan.is_plan_task({'title': 'Fix RA-002 later'}))

    def test_dependencies_use_repository_evidence_and_force_does_not_bypass(self):
        atlasplan.sync(self.con)
        row=self.con.execute("SELECT * FROM tasks WHERE id = ?", (self.tid('RA-002'),)).fetchone()
        with mock.patch.object(devloop,'outside_blocked',return_value=''):
            self.assertIsNone(devloop.pick_task(self.con, row['id']))
        self.steps[0]['status']='done'
        self.assertEqual(atlasplan.block_reason(row),'')
        self.steps[1]['automationHold']='Owner deferred this work'
        self.assertEqual(atlasplan.block_reason(row),'Owner deferred this work')

    def test_reported_zone_and_unknown_reset(self):
        now=datetime(2026,10,20,14,48,tzinfo=timezone.utc)
        reset=atlasplan.parsed_reset('resets 7pm (Europe/Amsterdam)',now)
        self.assertEqual(reset.astimezone(timezone.utc),datetime(2026,10,20,17,tzinfo=timezone.utc))
        atlasplan.note_limit(self.con,'usage limit',now)
        info=atlasplan.reset_status(self.con)
        self.assertFalse(info['reported']);self.assertEqual(info['at'],'')
        self.assertEqual(atlasplan.until('resets 3am',datetime(2026,10,20,16)),datetime(2026,10,21,3))
        self.assertIsNone(atlasplan.parsed_reset('resets 99:99',now))

    def test_watcher_never_forces_or_ignores_pause(self):
        atlasplan.sync(self.con)
        with mock.patch.object(devloop,'cfg',return_value={'enabled':True}), \
             mock.patch.object(devloop,'autonomy',return_value='continuous'), \
             mock.patch.object(devloop,'paused',return_value=False), \
             mock.patch('engine.usage.killed',return_value=False), \
             mock.patch.object(atlasplan,'waiting',return_value=False), \
             mock.patch.object(devloop,'current',return_value=None), \
             mock.patch.object(devloop,'batch_full',return_value=False), \
             mock.patch.object(devloop,'limit_reached',return_value=''), \
             mock.patch.object(devloop,'pick_task',return_value={'id':1}), \
             mock.patch.object(devloop,'start_background') as start:
            atlasplan.tick(self.con);start.assert_called_once_with(force=False)
            start.reset_mock()
            with mock.patch.object(devloop,'paused',return_value=True): atlasplan.tick(self.con)
            start.assert_not_called()
            with mock.patch.object(atlasplan,'waiting',return_value=True): atlasplan.tick(self.con)
            start.assert_not_called()

    def test_usage_limit_failures_do_not_exhaust_retries(self):
        atlasplan.sync(self.con)
        tid=self.tid('RA-001')
        for _ in range(2):
            db.insert(self.con,'dev_runs',task_id=tid,status='failed',summary="You've hit your session limit · resets 7pm")
        plan=skilltree.build_plan(self.con)
        self.assertEqual(next(t for t in plan['mine'] if t['id']==tid)['state'],'ready')
