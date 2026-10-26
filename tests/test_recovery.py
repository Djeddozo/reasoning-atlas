import json
import sqlite3
import unittest
from unittest.mock import patch, Mock
from engine import recovery, db, brain, taskdetail

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(':memory:');self.con.row_factory=sqlite3.Row;db.init(self.con);self.addCleanup(self.con.close)
    def test_repeat_incidents_deduplicate_without_secret_values(self):
        a=recovery.record(self.con,'server','TypeError','engine/dashboard.py:34 password=secret')
        b=recovery.record(self.con,'server','TypeError','engine/dashboard.py:34 password=other')
        self.assertEqual(a,b)
        row=self.con.execute('SELECT * FROM incidents').fetchone()
        self.assertEqual(row['occurrences'],2);self.assertEqual(row['frames'],'engine/dashboard.py:34')
    def test_only_narrow_tested_repairs_eligible(self):
        self.assertTrue(recovery.safe_changes(['engine/overview.py','tests/test_overview.py'],'4\t2\thq/overview.py\n10\t0\ttests/test_overview.py'))
        for files,stats in [(['engine/models.py','tests/test_models.py'],'1\t1\tx'),(['engine/overview.py'],'1\t1\tx'),(['deploy/x','tests/test_x.py'],'1\t1\tx'),(['engine/overview.py','tests/test_x.py'],'600\t1\tx'),(['hq/a.bin','tests/test_x.py'],'-\t-\tx')]:
            self.assertFalse(recovery.safe_changes(files,stats))
    def test_disabled_and_kill_switch_do_not_start_repairs(self):
        recovery.record(self.con,'server','TypeError','engine/dashboard.py:34')
        with patch.object(recovery,'service_health'),patch.object(recovery.devloop,'start_background') as launch:
            self.assertIn('off',recovery.tick(self.con))
            db.set_state(self.con,'recovery_enabled','1')
            with patch.object(recovery.usage,'killed',return_value=True):self.assertIn('paused',recovery.tick(self.con))
        launch.assert_not_called()
    def test_expired_auth_waits_without_spawning(self):
        db.set_state(self.con,'recovery_enabled','1');recovery.record(self.con,'server','TypeError')
        with patch.object(recovery,'service_health'),patch.object(recovery.usage,'killed',return_value=False),patch.object(recovery.devloop,'paused',return_value=False),patch.object(recovery.claudeauth,'check',return_value={'state':'disconnected'}),patch.object(recovery.devloop,'start_background') as launch:
            self.assertIn('Reconnect',recovery.tick(self.con))
        launch.assert_not_called()
    def test_regression_test_failure_never_merges(self):
        head='a'*40
        run={'head':head,'base_run':None,'pr_url':'https://github.com/x/y/pull/3','branch':'repair'}
        info={'headRefOid':head,'headRefName':'repair','baseRefName':'main','state':'OPEN','mergeable':'MERGEABLE','statusCheckRollup':[]}
        with patch.object(recovery,'command',return_value=json.dumps(info)) as command,patch.object(recovery.updater,'_git',side_effect=['','engine/overview.py\ntests/test_overview.py','1\t1\tx']),patch.object(recovery.subprocess,'run',return_value=Mock(returncode=0)),patch.object(recovery.updater,'_tests_pass_on',return_value=(False,'failed')):
            result=recovery.install_repair(self.con,{'task_id':1},run)
        self.assertIn('tests failed',result)
        self.assertEqual(command.call_count,1)
    def test_moved_head_never_tested_or_merged(self):
        run={'head':'a'*40,'base_run':None,'pr_url':'https://github.com/x/y/pull/3','branch':'repair'}
        with patch.object(recovery,'command',return_value=json.dumps({'headRefOid':'b'*40})) as command,patch.object(recovery.updater,'_tests_pass_on') as tests:
            self.assertIn('changed',recovery.install_repair(self.con,{'task_id':1},run))
        tests.assert_not_called();self.assertEqual(command.call_count,1)
    def test_successful_repair_merges_only_tested_head(self):
        tid=db.insert(self.con,'tasks',title='Repair')
        fingerprint=recovery.record(self.con,'server','TypeError')
        self.con.execute('UPDATE incidents SET task_id=?',(tid,));self.con.commit()
        incident=dict(self.con.execute('SELECT * FROM incidents').fetchone())
        head='a'*40
        run={'head':head,'base_run':None,'pr_url':'https://github.com/x/y/pull/3','branch':'repair'}
        info={'headRefOid':head,'headRefName':'repair','baseRefName':'main','state':'OPEN','mergeable':'MERGEABLE','statusCheckRollup':[]}
        db.set_state(self.con,'recovery_enabled','1');self.con.commit()
        with patch.object(recovery,'command',side_effect=[json.dumps(info),'']) as command,patch.object(recovery.updater,'_git',side_effect=['','engine/overview.py\ntests/test_overview.py','1\t1\tx']),patch.object(recovery.subprocess,'run',return_value=Mock(returncode=0)),patch.object(recovery.updater,'_tests_pass_on',return_value=(True,'')) as tests,patch.object(recovery.usage,'killed',return_value=False),patch.object(recovery.devloop,'paused',return_value=False):
            self.assertIn('tested and merged',recovery.install_repair(self.con,incident,run))
        tests.assert_called_once_with(head)
        self.assertEqual(command.call_args.args,('gh','pr','merge','3','--squash','--match-head-commit',head))
        self.assertEqual(self.con.execute('SELECT status FROM incidents').fetchone()[0],'awaiting_update')

    def test_automatic_launch_respects_limits_and_attempt_cap(self):
        from contextlib import ExitStack
        db.set_state(self.con,'recovery_enabled','1');recovery.record(self.con,'server','TypeError')
        with ExitStack() as stack:
            stack.enter_context(patch.object(recovery,'service_health'))
            stack.enter_context(patch.object(recovery.usage,'killed',return_value=False))
            stack.enter_context(patch.object(recovery.devloop,'paused',return_value=False))
            stack.enter_context(patch.object(recovery.claudeauth,'check',return_value={'state':'connected'}))
            stack.enter_context(patch.object(recovery.devloop,'why_not_now',return_value=''))
            stack.enter_context(patch.object(recovery.devloop,'cfg',return_value={'enabled':True}))
            stack.enter_context(patch.object(recovery.devloop,'batch_full',return_value=False))
            limit=stack.enter_context(patch.object(recovery.devloop,'limit_reached',return_value='Daily budget reached'))
            launch=stack.enter_context(patch.object(recovery.devloop,'start_background'))
            self.assertIn('budget',recovery.tick(self.con));launch.assert_not_called()
            limit.return_value=''
            recovery.tick(self.con);recovery.tick(self.con)
            self.assertIn('two attempts',recovery.tick(self.con))
            self.assertEqual(launch.call_count,2)
            self.assertFalse(launch.call_args.kwargs['force'])
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)

    def test_restart_baseline_and_detection(self):
        with patch.object(recovery,'command',return_value='NRestarts=2\nActiveState=active'):
            recovery.service_health(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],0)
        with patch.object(recovery,'command',return_value='NRestarts=3\nActiveState=active'):
            recovery.service_health(self.con)
        self.assertEqual(self.con.execute('SELECT error_type FROM incidents').fetchone()[0],'UnexpectedServiceRestart')

class TaskDetailTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(':memory:');self.con.row_factory=sqlite3.Row;db.init(self.con);self.addCleanup(self.con.close)
        self.tid=db.insert(self.con,'tasks',title='Build assistant',description='Manage dependencies')
    def test_rationale_is_preserved_and_bounded(self):
        brain._store_focus(self.con,{'recommended':[{'task_id':self.tid,'why':'Reduce coordination','rationale':{'problem':'x'*2000,'benefit':'Fewer missed dependencies','unknown':'ignored'}}]},'batch')
        detail=taskdetail.detail(self.con,self.tid)
        self.assertEqual(len(detail['rationale']['problem']),1500)
        self.assertNotIn('unknown',detail['rationale']);self.assertTrue(detail['recommended'])
    def test_missing_reasoning_remains_missing(self):
        detail=taskdetail.detail(self.con,self.tid)
        self.assertEqual(detail['rationale'],{})
        self.assertEqual(detail['task']['description'],'Manage dependencies')
    def test_malformed_model_rationale_does_not_crash(self):
        brain._store_focus(self.con,{'recommended':[{'task_id':self.tid,'rationale':'not a dict'}]},'batch')
        self.assertEqual(taskdetail.detail(self.con,self.tid)['rationale'],{})

class ManagementFocusTests(unittest.TestCase):
    def test_management_focus_boosts_linked_agent_capabilities(self):
        from engine import skilltree,devloop
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;db.init(con)
        try:
            cap=db.insert(con,'capabilities',title='Planning',branch='mind',requires='')
            tid=db.insert(con,'tasks',title='Improve planning explanations',owner='ai',capability_id=cap)
            other=db.insert(con,'tasks',title='Other feature',owner='ai')
            before={i['task']['id']:i for i in skilltree.build_order(con,devloop.DEFAULTS)['ready']}
            skilltree.set_focus(con,'management')
            after={i['task']['id']:i for i in skilltree.build_order(con,devloop.DEFAULTS)['ready']}
            self.assertGreater(after[tid]['weight'],before[tid]['weight'])
            self.assertEqual(after[other]['weight'],before[other]['weight'])
            self.assertIn('management agent',after[tid]['why'])
        finally:con.close()
