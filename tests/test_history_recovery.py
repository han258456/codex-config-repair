import json
import sqlite3
import tempfile
import unittest
from functools import partial
from pathlib import Path

import tomlkit

from codex_repair.demo import CURRENT, FORK, OTHER, PARENT, create_demo
from codex_repair.engine import (
    ClosingConnection, ConnectionSettings, Options, RELAY_PROVIDER, RepairError,
    analyze, execute, file_hash, restore,
)


connect = partial(sqlite3.connect, factory=ClosingConnection)


class HistoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / '.codex'
        self.paths = create_demo(self.home)
        self.source = self.home / 'config.toml-bf'

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, options=Options()):
        return analyze(self.home, self.source, options)

    def apply(self, plan):
        return execute(plan, process_probe=lambda: [])

    def set_path(self, thread_id, path):
        with connect(self.home / 'state_5.sqlite') as db:
            db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(path), thread_id))

    def state_rows(self):
        with connect(self.home / 'state_5.sqlite') as db:
            db.row_factory = sqlite3.Row
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM threads ORDER BY id')}

    def database_rows(self):
        with connect(self.home / 'state_5.sqlite') as db:
            state = db.execute('SELECT * FROM threads ORDER BY id').fetchall()
        with connect(self.home / 'thread_history_1.sqlite') as db:
            history = {
                table: db.execute(f'SELECT * FROM {table} ORDER BY thread_id').fetchall()
                for table in ('thread_turns', 'thread_history_projection_state', 'thread_items')
            }
        return state, history

    def home_hashes(self):
        return {p.relative_to(self.home).as_posix(): file_hash(p) for p in self.home.rglob('*') if p.is_file()}

    def rollout_bytes(self):
        return {path: path.read_bytes() for path in self.paths.values() if path.exists()}

    def assert_bodies_equal(self, originals):
        for path, data in originals.items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_bytes().split(b'\n', 1)[1], data.split(b'\n', 1)[1])

    def test_missing_archive_is_skipped_without_blocking_valid_history(self):
        missing_archive = self.paths[OTHER]
        missing_archive.unlink()
        original_rows = self.database_rows()
        original_state = self.state_rows()
        originals = self.rollout_bytes()
        hashes = self.home_hashes()

        plan = self.plan()

        self.assertEqual(self.home_hashes(), hashes, 'Preview must not write files or databases')
        self.assertEqual([row['id'] for row in plan.rows], [PARENT])
        self.assertEqual(plan.relinked_threads, [])
        self.assertEqual(len(plan.skipped_threads), 1)
        skipped = plan.skipped_threads[0]
        self.assertEqual(skipped['id'], OTHER)
        self.assertEqual(skipped['provider'], 'custom')
        self.assertEqual(skipped['rollout_path'], original_state[OTHER]['rollout_path'])
        self.assertTrue(skipped['reason'])
        report = plan.report()
        self.assertEqual(report['legacy_threads'], 1)
        self.assertEqual(report['legacy_providers'], {'codex': 1})
        self.assertEqual(report['rollout_files'], 2)
        self.assertEqual(report['skipped_legacy_threads'], 1)
        self.assertEqual(report['skipped_threads'], plan.skipped_threads)
        self.assertEqual(report['relinked_legacy_threads'], 0)

        body_hashes = {path: file_hash(path, body=True) for path in originals}
        backup = self.apply(plan)

        after = self.state_rows()
        self.assertEqual(after[PARENT]['model_provider'], 'openai')
        self.assertEqual(after[OTHER], original_state[OTHER])
        self.assertEqual(after[OTHER]['archived'], 1)
        self.assertFalse(missing_archive.exists())
        for path, digest in body_hashes.items():
            self.assertEqual(file_hash(path, body=True), digest)
        for physical_id in (PARENT, FORK):
            header = json.loads(self.paths[physical_id].read_bytes().split(b'\n', 1)[0])
            self.assertEqual(header['payload']['model_provider'], 'openai')
        changed_history = self.database_rows()[1]
        for table in ('thread_turns', 'thread_history_projection_state'):
            self.assertEqual(
                [row for row in changed_history[table] if row[0] == OTHER],
                [row for row in original_rows[1][table] if row[0] == OTHER],
            )
        self.assertEqual(changed_history['thread_items'], original_rows[1]['thread_items'])
        saved_report = json.loads((backup / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(saved_report['skipped_legacy_threads'], 1)
        self.assertEqual(saved_report['skipped_threads'], plan.skipped_threads)

        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), original_rows)
        self.assertFalse(missing_archive.exists())
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_stale_path_relinks_unique_local_physical_id_and_restores_both_fields(self):
        stale_path = self.root / 'old-user' / '.codex' / 'sessions' / self.paths[PARENT].name
        self.set_path(PARENT, stale_path)
        original_rows = self.database_rows()
        originals = self.rollout_bytes()
        hashes = self.home_hashes()

        plan = self.plan()

        self.assertEqual(self.home_hashes(), hashes)
        self.assertEqual(plan.skipped_threads, [])
        expected_path = self.paths[PARENT].resolve().as_posix()
        self.assertEqual(plan.relinked_threads, [{'id': PARENT, 'before': str(stale_path), 'after': expected_path}])
        parent_row = next(row for row in plan.rows if row['id'] == PARENT)
        self.assertEqual(parent_row['before_rollout_path'], str(stale_path))
        self.assertEqual(parent_row['after_rollout_path'], expected_path)
        self.assertEqual(plan.report()['relinked_legacy_threads'], 1)
        self.assertEqual(plan.report()['relinked_threads'], plan.relinked_threads)

        backup = self.apply(plan)

        self.assertEqual(self.state_rows()[PARENT]['model_provider'], 'openai')
        self.assertEqual(self.state_rows()[PARENT]['rollout_path'], expected_path)
        self.assert_bodies_equal(originals)
        manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
        patch = next(patch for patch in manifest['patches'] if patch['table'] == 'threads' and patch['key']['id'] == PARENT)
        self.assertEqual(patch['before'], {'model_provider': 'codex', 'rollout_path': str(stale_path)})
        self.assertEqual(patch['after'], {'model_provider': 'openai', 'rollout_path': expected_path})

        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), original_rows)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(stale_path.exists())

    def test_outside_file_is_skipped_and_never_changed(self):
        outside = self.root / 'outside' / self.paths[OTHER].name
        outside.parent.mkdir()
        outside.write_bytes(self.paths[OTHER].read_bytes())
        self.paths[OTHER].unlink()
        self.set_path(OTHER, outside)
        original_rows = self.database_rows()
        external_bytes = outside.read_bytes()
        external_mtime = outside.stat().st_mtime_ns

        plan = self.plan()

        self.assertEqual([row['id'] for row in plan.skipped_threads], [OTHER])
        self.assertIn('之外', plan.skipped_threads[0]['reason'])
        self.assertEqual(plan.report()['skipped_legacy_threads'], 1)
        self.assertFalse(any(change.physical_id == OTHER for change in plan.changes))
        backup = self.apply(plan)
        self.assertEqual(self.state_rows()[OTHER]['model_provider'], 'custom')
        self.assertEqual(self.state_rows()[OTHER]['rollout_path'], str(outside))
        self.assertEqual(outside.read_bytes(), external_bytes)
        self.assertEqual(outside.stat().st_mtime_ns, external_mtime)
        manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(any(patch.get('key', {}).get('id') == OTHER for patch in manifest['patches']))

        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), original_rows)
        self.assertEqual(outside.read_bytes(), external_bytes)
        self.assertEqual(outside.stat().st_mtime_ns, external_mtime)

    def test_connection_switch_continues_with_skipped_archive_and_relinked_history(self):
        self.paths[OTHER].unlink()
        stale_path = self.home / 'sessions' / 'missing' / self.paths[PARENT].name
        self.set_path(PARENT, stale_path)
        auth = self.home / 'auth.json'
        auth.write_bytes(b'{"login":"FAKE_PRESERVED_LOGIN"}')
        original_config = (self.home / 'config.toml').read_bytes()
        original_auth = auth.read_bytes()
        original_rows = self.database_rows()
        originals = self.rollout_bytes()
        options = Options(False, False, True, ConnectionSettings('relay', 'https://relay.example.com/v1', 'FAKE_KEY'))

        plan = self.plan(options)

        self.assertEqual(plan.report()['skipped_legacy_threads'], 1)
        self.assertEqual(plan.report()['relinked_legacy_threads'], 1)
        self.assertEqual(plan.report()['legacy_threads'], 2)
        self.assertEqual({row['id'] for row in plan.rows}, {PARENT, CURRENT})
        backup = self.apply(plan)
        parsed = tomlkit.parse((self.home / 'config.toml').read_text(encoding='utf-8'))
        self.assertEqual(parsed['model_provider'], RELAY_PROVIDER)
        for thread_id in (PARENT, CURRENT):
            self.assertEqual(self.state_rows()[thread_id]['model_provider'], RELAY_PROVIDER)
        self.assertEqual(self.state_rows()[OTHER]['model_provider'], 'custom')
        self.assertEqual(self.state_rows()[PARENT]['rollout_path'], self.paths[PARENT].resolve().as_posix())
        self.assertEqual(auth.read_bytes(), original_auth)
        self.assert_bodies_equal(originals)

        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), original_rows)
        self.assertEqual((self.home / 'config.toml').read_bytes(), original_config)
        self.assertEqual(auth.read_bytes(), original_auth)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_preview_rejects_changed_skipped_path_or_provider(self):
        self.paths[OTHER].unlink()
        for field, value in (('rollout_path', 'another-missing.jsonl'), ('model_provider', 'another-provider')):
            with self.subTest(field=field):
                plan = self.plan()
                with connect(self.home / 'state_5.sqlite') as db:
                    db.execute(f'UPDATE threads SET {field}=? WHERE id=?', (value, OTHER))
                fresh = self.plan()
                self.assertNotEqual(plan.signature, fresh.signature)
                rows = self.database_rows()
                config = (self.home / 'config.toml').read_bytes()
                with self.assertRaisesRegex(RepairError, '预览后发生变化'):
                    self.apply(plan)
                self.assertEqual(self.database_rows(), rows)
                self.assertEqual((self.home / 'config.toml').read_bytes(), config)
                self.assertFalse((self.home / 'repair-backups').exists())

    def test_preview_rejects_missing_archive_reappearing(self):
        missing = self.paths[OTHER]
        original = missing.read_bytes()
        missing.unlink()
        plan = self.plan()
        missing.write_bytes(original)
        rows = self.database_rows()
        hashes = self.home_hashes()

        with self.assertRaisesRegex(RepairError, '预览后发生变化'):
            self.apply(plan)

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual(self.home_hashes(), hashes)
        self.assertFalse((self.home / 'repair-backups').exists())

    def test_restore_rejects_rollout_path_changed_after_repair(self):
        stale_path = self.root / 'old' / self.paths[PARENT].name
        self.set_path(PARENT, stale_path)
        backup = self.apply(self.plan())
        self.set_path(PARENT, self.home / 'new-index.jsonl')
        rows = self.database_rows()
        config = (self.home / 'config.toml').read_bytes()

        with self.assertRaisesRegex(RepairError, '索引在修复后发生了变化'):
            restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual((self.home / 'config.toml').read_bytes(), config)

    def test_current_provider_stale_path_remains_outside_migration_scope(self):
        stale_path = self.root / 'old' / self.paths[CURRENT].name
        self.set_path(CURRENT, stale_path)
        plan = self.plan()

        self.assertFalse(any(row['id'] == CURRENT for row in plan.rows))
        self.assertFalse(any(row['id'] == CURRENT for row in plan.skipped_threads))
        self.assertFalse(any(row['id'] == CURRENT for row in plan.relinked_threads))
        self.apply(plan)
        self.assertEqual(self.state_rows()[CURRENT]['rollout_path'], str(stale_path))
        self.assertEqual(self.state_rows()[CURRENT]['model_provider'], 'openai')

    def test_duplicate_physical_id_remains_unsafe_to_relink(self):
        self.set_path(PARENT, self.root / 'old' / self.paths[PARENT].name)
        duplicate = self.home / 'archived_sessions' / self.paths[PARENT].name
        duplicate.write_bytes(self.paths[PARENT].read_bytes())
        rows = self.database_rows()
        hashes = self.home_hashes()

        with self.assertRaisesRegex(RepairError, '重复'):
            self.plan()

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual(self.home_hashes(), hashes)

    def test_valid_local_fork_reference_is_preserved(self):
        # Existing forks can use their parent's public ID in the metadata.
        # A valid indexed path must win over a different physical-ID match.
        self.set_path(PARENT, self.paths[FORK])
        plan = self.plan()
        parent_row = next(row for row in plan.rows if row['id'] == PARENT)

        self.assertEqual(parent_row['before_rollout_path'], str(self.paths[FORK]))
        self.assertEqual(parent_row['after_rollout_path'], str(self.paths[FORK]))
        self.assertEqual(plan.relinked_threads, [])
        self.apply(plan)
        self.assertEqual(self.state_rows()[PARENT]['rollout_path'], str(self.paths[FORK]))
        self.assertEqual(self.state_rows()[PARENT]['model_provider'], 'openai')

    def test_stale_fork_path_relinks_by_physical_id_instead_of_public_metadata_id(self):
        stale_path = self.root / 'old' / self.paths[FORK].name
        with connect(self.home / 'state_5.sqlite') as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (FORK, 'codex', str(stale_path), 'Fork', 101, 202, 0, None))
        original = self.database_rows()
        plan = self.plan()
        row = next(row for row in plan.rows if row['id'] == FORK)
        self.assertEqual(row['after_rollout_path'], self.paths[FORK].resolve().as_posix())
        self.assertNotEqual(row['after_rollout_path'], self.paths[PARENT].resolve().as_posix())

        backup = self.apply(plan)
        self.assertEqual(self.state_rows()[FORK]['model_provider'], 'openai')
        self.assertEqual(self.state_rows()[FORK]['rollout_path'], self.paths[FORK].resolve().as_posix())
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.database_rows(), original)

    def test_preview_rejects_relinked_file_edit_even_when_its_provider_already_matches(self):
        parent = self.paths[PARENT]
        header, body = parent.read_bytes().split(b'\n', 1)
        record = json.loads(header)
        record['payload']['model_provider'] = 'openai'
        parent.write_bytes(json.dumps(record, separators=(',', ':')).encode() + b'\n' + body)
        self.set_path(PARENT, self.root / 'old' / parent.name)
        plan = self.plan()
        self.assertFalse(any(change.physical_id == PARENT for change in plan.changes))
        self.assertEqual([item['id'] for item in plan.relinked_threads], [PARENT])
        with parent.open('ab') as output:
            output.write(b'{"type":"new_user_message"}\n')
        rows = self.database_rows()
        hashes = self.home_hashes()

        with self.assertRaisesRegex(RepairError, '预览后发生变化'):
            self.apply(plan)

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual(self.home_hashes(), hashes)
        self.assertFalse((self.home / 'repair-backups').exists())

    def test_provider_only_patches_from_old_backups_still_restore(self):
        original_rows = self.database_rows()
        originals = self.rollout_bytes()
        config = (self.home / 'config.toml').read_bytes()
        backup = self.apply(self.plan())
        manifest_path = backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for patch in manifest['patches']:
            if patch['table'] == 'threads':
                patch['before'].pop('rollout_path')
                patch['after'].pop('rollout_path')
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), original_rows)
        self.assertEqual((self.home / 'config.toml').read_bytes(), config)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_restore_refuses_new_chat_in_relinked_file_that_was_not_rewritten(self):
        parent = self.paths[PARENT]
        header, body = parent.read_bytes().split(b'\n', 1)
        record = json.loads(header)
        record['payload']['model_provider'] = 'openai'
        parent.write_bytes(json.dumps(record, separators=(',', ':')).encode() + b'\n' + body)
        self.set_path(PARENT, self.root / 'old' / parent.name)
        plan = self.plan()
        self.assertFalse(any(change.physical_id == PARENT for change in plan.changes))
        backup = self.apply(plan)
        self.assertEqual(self.state_rows()[PARENT]['rollout_path'], parent.resolve().as_posix())
        with parent.open('ab') as output:
            output.write(b'{"type":"new_user_message","text":"KEEP_NEW_CHAT"}\n')
        rows = self.database_rows()
        hashes = self.home_hashes()

        with self.assertRaisesRegex(RepairError, '新内容'):
            restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual(self.home_hashes(), hashes)
        self.assertTrue(parent.read_bytes().endswith(b'{"type":"new_user_message","text":"KEEP_NEW_CHAT"}\n'))

    def test_relinked_file_not_rewritten_is_a_restore_dependency_and_roundtrips(self):
        parent = self.paths[PARENT]
        header, body = parent.read_bytes().split(b'\n', 1)
        record = json.loads(header)
        record['payload']['model_provider'] = 'openai'
        parent.write_bytes(json.dumps(record, separators=(',', ':')).encode() + b'\n' + body)
        stale_path = self.root / 'old' / parent.name
        self.set_path(PARENT, stale_path)
        rows = self.database_rows()
        originals = self.rollout_bytes()
        config = (self.home / 'config.toml').read_bytes()
        plan = self.plan()
        self.assertFalse(any(change.physical_id == PARENT for change in plan.changes))
        backup = self.apply(plan)
        manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))

        self.assertEqual(manifest['rollout_dependencies'], [{
            'relative': parent.relative_to(self.home).as_posix(),
            'sha256': file_hash(parent),
        }])
        self.assertFalse(any(item['relative'] == parent.relative_to(self.home).as_posix() for item in manifest['files']))
        restore(self.home, backup, process_probe=lambda: [])

        self.assertEqual(self.database_rows(), rows)
        self.assertEqual(self.state_rows()[PARENT]['rollout_path'], str(stale_path))
        self.assertEqual((self.home / 'config.toml').read_bytes(), config)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
