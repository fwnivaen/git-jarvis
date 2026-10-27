"""Deterministic synthetic boundary fixtures. No provider/model/remote evidence."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from execution_control import ControlStore, ControlError, feature_flags
from execution_gateway import ToolGateway
from ultra_tools import Registry, ToolError
import ultra

VERSIONS = {'engine': 'fixture-v1', 'model': 'synthetic', 'tools': 'v1', 'behavior': 'incumbent'}


class ControlFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]
        self.path = Path(self.tmp.name)/'private'/'control.sqlite'
        self.store = ControlStore(self.path, clock=lambda: self.now[0])
        self.addCleanup(self.store.close)
        self.writes = []
        self.registry = Registry(lambda: replace(ultra.Settings(), enabled=True), {
            'notes_search': lambda query: {'notes': []},
            'notes_capture': lambda text: self.writes.append(text) or {'saved': True}})
        self.gateway = ToolGateway(self.store, self.registry)
        self.run, _ = self.admit('request')
        self.store.transition(self.run['id'], 'running')
        self.token = self.store.issue(self.run['id'])

    def admit(self, key, **changes):
        args = dict(conversation_id='conversation', project_id='project', engine='fixture',
                    versions=VERSIONS, permissions={'notes_search','notes_capture'}, duration_s=60)
        args.update(changes)
        return self.store.admit(key, **args)

    def call(self, op='operation', token=None, run=None, **changes):
        args = dict(deadline=time.monotonic()+2)
        args.update(changes)
        return self.gateway.call(token or self.token, run or self.run['id'], op,
                                 'notes_capture', {'text':'synthetic'}, **args)

    def test_duplicate_admission_is_atomic_under_race(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:self.admit('duplicate'),range(20)))
        self.assertEqual(len({r[0]['id'] for r in results}),1)
        self.assertEqual(sum(r[1] for r in results),1)
        with self.assertRaises(ControlError):self.admit('duplicate',engine='different')

    def test_one_owner_until_actual_settlement(self):
        other,_=self.admit('other')
        with self.assertRaises(ControlError):self.store.transition(other['id'],'running')
        self.store.transition(self.run['id'],'cancelling')
        with self.assertRaises(ControlError):self.store.transition(other['id'],'running')
        self.store.transition(self.run['id'],'cancelled')
        self.store.transition(other['id'],'running')

    def test_explicit_two_owner_limit_counts_waiting_and_cancelling(self):
        store = ControlStore(Path(self.tmp.name)/'two-owner.sqlite', max_active=2)
        self.addCleanup(store.close)
        def admit(key):
            return store.admit(key, conversation_id='c', project_id='p', engine='fixture',
                versions=VERSIONS, permissions=[], duration_s=60)[0]['id']
        first, second, third = [admit(key) for key in ('one', 'two', 'three')]
        store.transition(first, 'running')
        store.transition(second, 'running')
        store.transition(first, 'waiting')
        store.transition(second, 'cancelling')
        with self.assertRaises(ControlError):
            store.transition(third, 'running')
        store.transition(second, 'cancelled')
        store.transition(third, 'running')
        self.assertEqual(store.get(first)['status'], 'waiting')
        self.assertEqual(store.get(third)['status'], 'running')

    def test_invalid_explicit_owner_limit_is_rejected_before_store_creation(self):
        for value in (True, 0, -1, 9, ' 2', 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ControlStore(Path(self.tmp.name)/'invalid.sqlite', max_active=value)
        self.assertFalse((Path(self.tmp.name)/'invalid.sqlite').exists())

    def test_permissions_cannot_expand(self):
        with self.assertRaises(ControlError):
            self.store.issue(self.run['id'],parent_token=self.token,permissions={'shell'})

    def test_child_cannot_expand_lifetime_or_budget(self):
        for kw in ({'expires_at':1061},{'max_calls':25}):
            with self.assertRaises(ControlError):self.store.issue(self.run['id'],parent_token=self.token,**kw)

    def test_recursion_and_worker_count_rejected(self):
        child=self.store.issue(self.run['id'],parent_token=self.token)
        with self.assertRaises(ControlError):self.store.issue(self.run['id'],parent_token=child)
        for _ in range(2):self.store.issue(self.run['id'],parent_token=self.token)
        with self.assertRaises(ControlError):self.store.issue(self.run['id'],parent_token=self.token)
        with self.assertRaises(ControlError):self.admit('nested',parent_run_id=self.run['id'])

    def test_impersonation_cross_run_and_expiry(self):
        other,_=self.admit('other')
        for token,run in (('Scout',self.run['id']),(self.token,other['id'])):
            with self.assertRaises(ControlError):self.call(token=token,run=run)
        self.now[0]=1061
        with self.assertRaises(ControlError):self.call()
        self.assertEqual(self.writes,[])

    def test_children_share_parent_and_global_budget(self):
        parent=self.store.issue(self.run['id'],max_calls=1)
        first=self.store.issue(self.run['id'],parent_token=parent)
        second=self.store.issue(self.run['id'],parent_token=parent)
        self.call('first',token=first)
        with self.assertRaises(ControlError):self.call('second',token=second)
        self.assertEqual(len(self.writes),1)

    def test_schema_and_undeclared_tool_fail_before_reservation(self):
        with self.assertRaises(ToolError):
            self.gateway.call(self.token,self.run['id'],'bad','notes_capture',{'text':False},deadline=time.monotonic()+1)
        with self.assertRaises(ToolError):
            self.gateway.call(self.token,self.run['id'],'bad','shell',{},deadline=time.monotonic()+1)
        self.assertEqual(self.store.get(self.run['id'])['calls'],0)

    def test_completed_write_replay_returns_same_receipt_once(self):
        first=self.call();second=self.call()
        self.assertEqual(first,second)
        self.assertEqual(self.writes,['synthetic'])
        self.assertEqual(self.store.get(self.run['id'])['calls'],1)

    def test_uncertain_write_is_never_retried(self):
        def crash(text):
            self.writes.append(text)
            raise OSError('simulated after effect')
        self.registry.callbacks['notes_capture']=crash
        with self.assertRaises(ToolError):self.call()
        with self.assertRaises(ControlError):self.call()
        self.assertEqual(len(self.writes),1)
        with self.assertRaises(ControlError):self.store.transition(self.run['id'],'complete')

    def test_cancel_fences_inflight_result_and_future_calls(self):
        entered,release=threading.Event(),threading.Event()
        def callback(text):
            entered.set();release.wait(2);return {'saved': True}
        self.registry.callbacks['notes_capture']=callback
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.call)
            self.assertTrue(entered.wait(1))
            self.store.transition(self.run['id'],'cancelling')
            release.set()
            with self.assertRaises(ControlError):future.result(2)
        with self.assertRaises(ControlError):self.call('late')

    def test_deadline_or_cancel_prevents_new_effects(self):
        cancel=threading.Event();cancel.set()
        for kwargs in ({'deadline':time.monotonic()-1},{'cancel':cancel}):
            with self.assertRaises(ControlError):self.call(**kwargs)
        self.assertEqual(self.writes,[])

    def test_restart_revokes_identities_and_retains_events(self):
        self.call()
        before=self.store.events(self.run['id'])
        self.store.recover()
        with self.assertRaises(ControlError):self.call('stale')
        after=self.store.events(self.run['id'],before[-1]['id'])
        self.assertEqual([r['kind'] for r in after],['interrupted'])
        self.assertEqual(self.store.get(self.run['id'])['cleanup'],'pending')

    def test_completed_terminal_state_cannot_resume_silently(self):
        self.store.transition(self.run['id'],'complete')
        with self.assertRaises(ControlError):self.store.transition(self.run['id'],'running')
        with self.assertRaises(ControlError):self.store.issue(self.run['id'])

    def test_backup_restores_real_sqlite_and_private_modes(self):
        self.call()
        target=Path(self.tmp.name)/'restored.sqlite'
        self.store.backup(target)
        restored=ControlStore(target)
        try:self.assertEqual(restored.get(self.run['id']),self.store.get(self.run['id']))
        finally:restored.close()
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertNotIn(self.token,self.path.read_bytes().decode(errors='ignore'))

    def test_flags_default_off_and_invalid_values_fail_closed(self):
        path=Path(self.tmp.name)/'flags.json'
        self.assertFalse(any(feature_flags(path).values()))
        for data in ({'deepagents':'true'},{'unknown':True}):
            path.write_text(json.dumps(data))
            with self.assertRaises(ControlError):feature_flags(path)


if __name__=='__main__':unittest.main()
