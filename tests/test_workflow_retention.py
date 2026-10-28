"""Explicit task removal with disposable stores, no models, network or live state."""
import hashlib
import json
import os
import sqlite3
import shutil
import tempfile
import threading
import time
import unittest
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import ultra
import workflow_retention as retention
from workflow_runtime import RunStore, WorkflowError, WorkflowService

RUN = 'a' * 24
OTHER = 'b' * 24
KEY = 'c' * 24
PRIVATE_QUESTION = 'A task question that must disappear from logical task records'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workflows'
        self.settings = replace(ultra.Settings(), enabled=True, workflows_enabled=True, tools_enabled=True)
        self.service = self.make_service()
        self.addCleanup(self.close_services)
        self.extra_services = []

    def make_service(self):
        return WorkflowService(lambda: self.settings, Mock(), Mock(), threading.BoundedSemaphore(2),
                               'test', root=self.root)

    def close_services(self):
        for service in [self.service] + self.extra_services:
            if service.store:
                service.store.close()
                service.store = None

    def seed(self, identifier=RUN, **changes):
        if self.service.store is None:
            self.service.store = RunStore(self.root)
        now = time.time()
        run = {'id': identifier, 'question': PRIVATE_QUESTION, 'workflow': 'general',
            'created_at': now, 'expires_at': now + 1000, 'settings': self.settings.__dict__,
            'status': 'complete', 'phase': 'complete', 'cleanup': 'complete',
            'agents': {}, 'tasks': [], 'events': [], 'sequence': 0, 'tool_calls': 0,
            'restarts': 0, 'result': {'answer': 'Sensitive answer', 'nodes': [], 'about_notes': False}}
        run.update(changes)
        self.service.store.save(run)
        return run

    def files(self, identifier=RUN):
        paths = []
        for kind, subdir, name in retention.artifact_names(identifier):
            parent = self.root / subdir if subdir else self.root
            parent.mkdir(exist_ok=True)
            path = parent / name
            path.write_text('Saved source text for ' + kind)
            paths.append(path)
        return paths

    def assert_error(self, status, callback):
        with self.assertRaises(WorkflowError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def erase(self, identifier=RUN):
        plan = self.service.erase_plan(identifier)
        return self.service.erase(identifier, plan['revision'], True)

    def test_empty_reads_never_initialize_or_create_storage(self):
        with patch('workflow_runtime.RunStore', side_effect=AssertionError('must not initialize')), \
                patch('workflow_retention.remove', side_effect=AssertionError('must not remove')):
            result = self.service.retention_catalog()
            self.assertFalse(result['available'])
            self.assertEqual(result['items'], [])
            self.assert_error(404, lambda: self.service.erase_plan(RUN))
        self.assertFalse(self.root.exists())

    def test_detached_existing_catalog_read_does_not_write_or_migrate(self):
        self.root.mkdir()
        path = self.root / 'catalog.sqlite'
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        run = {'id': RUN, 'question': 'old task', 'created_at': 1, 'status': 'complete', 'cleanup': 'complete'}
        conn.execute('INSERT INTO runs VALUES (?,?)', (RUN, json.dumps(run)))
        conn.commit(); conn.close()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in self.root.iterdir()}
        with patch('workflow_runtime.RunStore', side_effect=AssertionError('must not initialize')):
            result = self.service.retention_catalog()
            plan = self.service.erase_plan(RUN)
        self.assertTrue(result['available'])
        self.assertEqual(result['items'][0]['label'], 'old task')
        self.assertTrue(plan['eligible'])
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in self.root.iterdir()})
        conn = sqlite3.connect(path)
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='erasures'").fetchone())
        conn.close()

    def test_only_terminal_cleanup_complete_unowned_tasks_are_eligible(self):
        self.seed()
        for status, cleanup in [('running', 'complete'), ('cancelling', 'pending'), ('complete', 'pending'), ('failed', 'none')]:
            with self.subTest(status=status, cleanup=cleanup):
                self.service.change(RUN, status=status, cleanup=cleanup)
                plan = self.service.erase_plan(RUN)
                self.assertFalse(plan['eligible'])
                self.assert_error(409, lambda: self.service.erase(RUN, plan['revision'], True))
        for status in ('complete', 'failed', 'cancelled', 'interrupted'):
            self.service.change(RUN, status=status, cleanup='complete')
            self.assertTrue(self.service.erase_plan(RUN)['eligible'])
        self.service.owners[RUN] = {}
        self.assertFalse(self.service.erase_plan(RUN)['eligible'])
        self.service.owners.clear()
        self.service.pending_tools[RUN] = 1
        self.assertFalse(self.service.erase_plan(RUN)['eligible'])
        self.service.pending_tools.clear()
        self.service.finished_executors.add(RUN)
        self.assertFalse(self.service.erase_plan(RUN)['eligible'])
        self.service.finished_executors.clear()

    def test_preview_hash_binds_state_and_actual_artifact_content(self):
        self.seed()
        paths = self.files()
        plan = self.service.erase_plan(RUN)
        self.assertEqual(len(plan['revision']), 64)
        self.assertNotIn(PRIVATE_QUESTION, json.dumps(plan))
        self.assertNotIn(str(self.root), json.dumps(plan))
        self.service.change(RUN, sequence=1)
        self.assert_error(409, lambda: self.service.erase(RUN, plan['revision'], True))
        plan = self.service.erase_plan(RUN)
        paths[0].write_text('changed')
        self.assert_error(409, lambda: self.service.erase(RUN, plan['revision'], True))
        self.assertEqual(self.service.store.get(RUN)['sequence'], 1)
        self.assertIsNone(self.service.store.erasure(RUN))

    def test_exact_removal_leaves_other_tasks_and_external_stores_alone(self):
        self.seed(); self.seed(OTHER)
        target, unrelated = self.files(), self.files(OTHER)
        arbitrary = self.root / (RUN + '.unexpected')
        arbitrary.write_text('unrelated filename')
        learning = Path(self.tmp.name) / 'learning.sqlite'
        learning.write_text('minimized outcome hashes')
        preferences = Path(self.tmp.name) / 'preferences.json'
        preferences.write_text('explicit preferences and undo are excluded')
        plan = self.service.erase_plan(RUN)
        result = self.service.erase(RUN, plan['revision'], True)
        self.assertEqual((result['state'], result['erased'], result['retryable']), ('erased', True, False))
        self.assertEqual(result['revision'], plan['revision'])
        self.assertEqual(len(result['removed_artifacts']), len(target) + 1)
        self.assertFalse(any(p.exists() for p in target))
        self.assertTrue(all(p.exists() for p in unrelated))
        self.assertTrue(arbitrary.exists())
        self.assertEqual(learning.read_text(), 'minimized outcome hashes')
        self.assertTrue(preferences.exists())
        self.assertEqual([r['id'] for r in self.service.store.all()], [OTHER])
        self.assertIsNone(self.service.store.conn.execute('SELECT data FROM runs WHERE id=?', (RUN,)).fetchone())
        marker = self.service.store.erasure(RUN)
        self.assertEqual(set(marker), {'id', 'revision', 'state', 'created_at', 'erased_at'})
        self.assertNotIn(PRIVATE_QUESTION, json.dumps(marker))
        item = next(row for row in self.service.retention_catalog()['items'] if row['id'] == RUN)
        self.assertEqual(item['label'], 'Removed task')
        self.assertFalse(plan['limits']['secure_wipe'])
        self.assertTrue(any('SQLite free pages' in text for text in plan['exclusions']))

    def test_tombstone_blocks_old_request_resume_and_late_writes_forever(self):
        run = self.seed()
        fingerprint = hashlib.sha256(json.dumps([PRIVATE_QUESTION, 'general']).encode()).hexdigest()
        self.service.store.conn.execute('INSERT INTO admissions VALUES (?,?,?)', (KEY, fingerprint, RUN))
        self.service.store.conn.commit()
        receipt = self.erase()
        for callback in [lambda: self.service.store.get(RUN),
                         lambda: self.service.store.save(run),
                         lambda: self.service.store.admit(run, None, fingerprint),
                         lambda: self.service.start(PRIVATE_QUESTION, 'general', request_key=KEY),
                         lambda: self.service.start('different task', 'general', request_key=KEY),
                         lambda: self.service.start(resume=RUN),
                         lambda: self.service.detail(RUN)]:
            self.assert_error(410, callback)
        self.assertIsNotNone(self.service.store.conn.execute('SELECT run_id FROM admissions WHERE request_key=?', (KEY,)).fetchone())
        self.assertEqual(self.service.erase(RUN, receipt['revision'], True)['state'], 'erased')
        self.service.store.close(); self.service.store = None
        fresh = self.make_service(); self.extra_services.append(fresh)
        self.assertEqual(fresh.erase_plan(RUN)['state'], 'erased')
        self.assertEqual(fresh.erase(RUN, receipt['revision'], True)['revision'], receipt['revision'])

    def test_unlink_failure_pending_receipt_and_explicit_retry_after_restart(self):
        self.seed()
        paths = self.files()
        plan = self.service.erase_plan(RUN)
        with patch('workflow_retention.os.unlink', side_effect=PermissionError('fixture')):
            result = self.service.erase(RUN, plan['revision'], True)
        self.assertEqual((result['state'], result['erased'], result['retryable']), ('pending', False, True))
        self.assertTrue(all(p.exists() for p in paths))
        self.assertEqual(self.service.erase_plan(RUN)['revision'], plan['revision'])
        self.assert_error(410, lambda: self.service.store.get(RUN))
        self.service.store.close(); self.service.store = None
        fresh = self.make_service(); self.extra_services.append(fresh)
        with patch('workflow_retention.remove', side_effect=AssertionError('GET must not retry')):
            self.assertEqual(fresh.retention_catalog()['items'][0]['state'], 'pending')
            self.assertEqual(fresh.erase_plan(RUN)['revision'], plan['revision'])
        self.assertTrue(all(p.exists() for p in paths))
        result = fresh.erase(RUN, plan['revision'], True)
        self.assertTrue(result['erased'])
        self.assertFalse(any(p.exists() for p in paths))

    def test_partial_unlink_progress_missing_files_are_restart_safe(self):
        self.seed()
        paths = self.files()
        plan = self.service.erase_plan(RUN)
        original = os.unlink
        calls = []
        def fail_second(name, **kwargs):
            calls.append(name)
            if len(calls) == 2:
                raise PermissionError('fixture')
            return original(name, **kwargs)
        with patch('workflow_retention.os.unlink', side_effect=fail_second):
            receipt = self.service.erase(RUN, plan['revision'], True)
        self.assertFalse(receipt['erased'])
        self.assertFalse(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertTrue(self.service.erase(RUN, plan['revision'], True)['erased'])
        self.assertFalse(any(p.exists() for p in paths))

    def test_pending_replacement_is_never_removed_under_original_approval(self):
        self.seed()
        paths = self.files()
        plan = self.service.erase_plan(RUN)
        with patch('workflow_retention.os.unlink', side_effect=PermissionError('fixture')):
            self.service.erase(RUN, plan['revision'], True)
        paths[0].unlink(); paths[0].write_text('new unrelated file')
        result = self.service.erase(RUN, plan['revision'], True)
        self.assertEqual(result['state'], 'pending')
        self.assertFalse(result['erased'])
        self.assertEqual(paths[0].read_text(), 'new unrelated file')
        # After the unsafe replacement is independently removed, explicit retry
        # completes without trying to recreate or delete that replacement.
        paths[0].unlink()
        self.assertTrue(self.service.erase(RUN, plan['revision'], True)['erased'])

    def test_unsafe_artifacts_fail_closed(self):
        self.seed()
        path = self.root / (RUN + '.sqlite')
        external = Path(self.tmp.name) / 'external'
        external.write_text('must remain')
        makers = [lambda: path.symlink_to(external), lambda: os.link(external, path),
                  lambda: os.mkfifo(path), lambda: path.mkdir()]
        for maker in makers:
            with self.subTest(maker=maker):
                maker()
                self.assert_error(409, lambda: self.service.erase_plan(RUN))
                self.assertIsNone(self.service.store.erasure(RUN))
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()
        self.assertEqual(external.read_text(), 'must remain')

    def test_unsafe_journal_directory_and_root_replacement_fail_closed(self):
        self.seed()
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        journal = self.root / 'tool-journals'
        journal.symlink_to(outside, target_is_directory=True)
        self.assert_error(409, lambda: self.service.erase_plan(RUN))
        journal.unlink()
        plan = self.service.erase_plan(RUN)
        moved = self.root.with_name('moved')
        self.root.rename(moved)
        self.root.mkdir()
        self.assert_error(409, lambda: self.service.erase(RUN, plan['revision'], True))
        self.root.rmdir(); moved.rename(self.root)

    def test_bounds_confirmation_and_identifiers(self):
        self.seed()
        self.files()
        with patch.object(retention, 'MAX_ARTIFACT_BYTES', 2):
            self.assert_error(409, lambda: self.service.erase_plan(RUN))
        with patch.object(retention, 'MAX_TOTAL_BYTES', 2):
            self.assert_error(409, lambda: self.service.erase_plan(RUN))
        with patch.object(retention, 'MAX_STATE_BYTES', 2):
            self.assert_error(409, lambda: self.service.erase_plan(RUN))
        plan = self.service.erase_plan(RUN)
        for confirmation in (False, 1, 'true', None):
            self.assert_error(400, lambda: self.service.erase(RUN, plan['revision'], confirmation))
        for identifier in ('../catalog', RUN.upper(), '', None):
            self.assert_error(404, lambda: self.service.erase_plan(identifier))
        self.assert_error(400, lambda: self.service.erase(RUN, 'bad', True))
        self.assertEqual(self.service.store.get(RUN)['id'], RUN)

    def test_readonly_catalog_sidecar_refuses_recovery_and_does_not_delete(self):
        self.seed()
        self.service.store.close(); self.service.store = None
        sidecar = self.root / 'catalog.sqlite-journal'
        sidecar.write_bytes(b'fixture')
        self.assert_error(409, self.service.retention_catalog)
        self.assertEqual(sidecar.read_bytes(), b'fixture')

    def test_catalog_path_replaced_after_preview_never_deletes_artifacts(self):
        self.seed()
        paths = self.files()
        plan = self.service.erase_plan(RUN)
        original = self.root / 'catalog.sqlite'
        saved = self.root / 'saved-catalog.sqlite'
        original.rename(saved)
        shutil.copyfile(saved, original)
        try:
            self.assert_error(409, lambda: self.service.erase(RUN, plan['revision'], True))
            self.assertTrue(all(path.exists() for path in paths))
            self.assertIsNone(self.service.store.erasure(RUN))
            conn = sqlite3.connect(original)
            self.assertEqual(conn.execute('SELECT count(*) FROM erasures').fetchone()[0], 0)
            conn.close()
        finally:
            original.unlink(); saved.rename(original)

    def test_detached_catalog_reader_pins_database_and_rejects_root_swap(self):
        self.seed()
        self.service.store.close(); self.service.store = None
        moved = self.root.with_name('moved-root')
        replacement = self.root.with_name('replacement-root')
        replacement.mkdir()
        conn = sqlite3.connect(replacement / 'catalog.sqlite')
        conn.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        conn.execute('INSERT INTO runs VALUES (?,?)', (RUN, json.dumps({'id': RUN, 'question': 'WRONG',
            'created_at': 1, 'status': 'complete', 'cleanup': 'complete'})))
        conn.commit(); conn.close()
        original_connect = sqlite3.connect
        def exchange(path, **kwargs):
            self.root.rename(moved)
            replacement.rename(self.root)
            # Actual SQLite read goes through the already pinned original fd.
            result = original_connect(path, **kwargs)
            self.assertEqual(json.loads(result.execute('SELECT data FROM runs').fetchone()[0])['question'], PRIVATE_QUESTION)
            return result
        try:
            with patch('workflow_retention.sqlite3.connect', side_effect=exchange):
                self.assert_error(409, self.service.retention_catalog)
        finally:
            self.root.rename(replacement); moved.rename(self.root)
        self.assertFalse(any(p.name.endswith(('-wal', '-shm', '-journal')) for p in self.root.iterdir()))

    def test_cold_erase_preserves_deferred_recovery_of_other_abandoned_tasks(self):
        self.seed(); self.seed(OTHER, status='running', phase='retrieve', cleanup='none')
        self.service.store.close(); self.service.store = None
        fresh = self.make_service(); self.extra_services.append(fresh)
        plan = fresh.erase_plan(RUN)
        self.assertTrue(fresh.erase(RUN, plan['revision'], True)['erased'])
        self.assertTrue(fresh._deferred_recovery)
        self.assertEqual(fresh.store.get(OTHER)['status'], 'running')
        self.assertEqual(fresh.retention_catalog()['items'][0]['status'], 'running')
        module = types.ModuleType('langgraph.checkpoint.sqlite')
        module.SqliteSaver = object
        with patch.dict('sys.modules', {'langgraph.checkpoint.sqlite': module}):
            fresh.initialize()
        self.assertFalse(fresh._deferred_recovery)
        self.assertEqual(fresh.store.get(OTHER)['status'], 'interrupted')
        self.assertEqual(fresh.store.get(OTHER)['events'][-1]['kind'], 'interrupted')

    def test_pending_cleanup_remains_discoverable_and_occupies_capacity(self):
        self.seed()
        plan = self.service.erase_plan(RUN)
        with patch('workflow_retention.remove', side_effect=PermissionError('fixture')):
            self.assertFalse(self.service.erase(RUN, plan['revision'], True)['erased'])
        with patch('workflow_runtime.MAX_RUNS', 1):
            self.assertFalse(self.service.store.capacity())
        now = time.time()
        rows = []
        for index in range(retention.MAX_CATALOG_ITEMS + 1):
            identifier = format(index, '024x')
            marker = {'id': identifier, 'revision': 'e' * 64, 'created_at': now + index,
                      'erased_at': now + index, 'state': 'erased'}
            rows.append((identifier, now + index, json.dumps(marker)))
        self.service.store.conn.executemany('INSERT INTO erasures VALUES (?,?,?)', rows)
        self.service.store.conn.commit()
        self.assertEqual(self.service.retention_catalog()['items'][0]['id'], RUN)
        self.service.store.close(); self.service.store = None
        self.assertEqual(self.service.retention_catalog()['items'][0]['id'], RUN)
        receipt = self.service.erase(RUN, plan['revision'], True)
        self.assertTrue(receipt['erased'])
        with patch('workflow_runtime.MAX_RUNS', 1):
            self.assertTrue(self.service.store.capacity())

    def test_owner_release_follows_final_capture_and_all_task_writes(self):
        self.seed(status='running', cleanup='none')
        self.assertTrue(self.service.slots.acquire(blocking=False))
        owner = {'thread': threading.current_thread(), 'cancel': threading.Event(),
                 'bridge': None, 'bridge_cleanup_pending': False, 'bridge_lock': threading.Lock()}
        self.service.owners[RUN] = owner
        captured = []
        def capture(run):
            captured.append(run['id'])
            self.assertIn(RUN, self.service.owners)
            self.assertNotIn(RUN, self.service.finished_executors)
            self.assertFalse(self.service.erase_plan(RUN)['eligible'])
            return 'off'
        original_save = self.service.store.save
        writes = []
        def save(run):
            self.assertIn(RUN, self.service.owners)
            writes.append(run.get('outcome_collection'))
            return original_save(run)
        with patch.dict('sys.modules', {'langgraph.checkpoint.sqlite': None}), \
                patch('outcome_collection.capture', side_effect=capture), \
                patch.object(self.service.store, 'save', side_effect=save):
            self.service._execute(RUN, False, owner['cancel'])
        self.assertEqual(captured, [RUN])
        self.assertEqual(writes[-1], 'off')
        self.assertNotIn(RUN, self.service.owners)
        self.assertTrue(self.service.erase_plan(RUN)['eligible'])


if __name__ == '__main__':
    unittest.main()
