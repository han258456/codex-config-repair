import json
import sqlite3
import tempfile
import unittest
from functools import partial
from pathlib import Path

import tomlkit

from codex_repair.demo import create_demo, PARENT, FORK, CURRENT
from codex_repair.engine import (
    Options, RepairError, analyze, execute, file_hash, list_backups, redact_toml,
    restore, safe_path, ClosingConnection,
)

connect = partial(sqlite3.connect, factory=ClosingConnection)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.paths = create_demo(self.home)
        self.source = self.home / "config.toml-bf"

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, options=Options()):
        return analyze(self.home, self.source, options)

    def repair(self, plan=None, **kwargs):
        return execute(plan or self.plan(), process_probe=lambda: [], **kwargs)

    def db_values(self):
        with connect(self.home / "state_5.sqlite") as db:
            state = db.execute("SELECT * FROM threads ORDER BY id").fetchall()
        with connect(self.home / "thread_history_1.sqlite") as db:
            turns = db.execute("SELECT * FROM thread_turns ORDER BY thread_id,turn_id").fetchall()
            projection = db.execute("SELECT * FROM thread_history_projection_state ORDER BY thread_id").fetchall()
            items = db.execute("SELECT * FROM thread_items ORDER BY thread_id,item_id").fetchall()
        return state, turns, projection, items

    def test_scan_is_read_only_and_current_settings_win(self):
        hashes = {p: file_hash(p) for p in [self.home / "config.toml", self.source, *self.paths.values()]}
        plan = self.plan()
        self.assertEqual(plan.projects_added, 2)
        self.assertEqual(len(plan.rows), 2)
        candidate = next(c.new_data for c in plan.changes if c.kind == "config")
        config = tomlkit.parse(candidate.decode())
        self.assertEqual(config['model'], 'gpt-6-astra')
        self.assertEqual(config['notify'], ['CURRENT_RUNTIME'])
        self.assertEqual(config['desktop']['followUpQueueMode'], 'queue')
        self.assertNotIn('model_providers', config)
        self.assertNotIn('disable_response_storage', config)
        self.assertNotIn('demo_database', config['mcp_servers'])
        for p, digest in hashes.items():
            self.assertEqual(file_hash(p), digest)

    def test_round_trip_preserves_bodies_and_all_database_rows(self):
        original = self.db_values()
        files = {p: p.read_bytes() for p in [self.home / "config.toml", *self.paths.values()]}
        backup = self.repair()
        changed = self.db_values()
        self.assertEqual(changed[3], original[3])
        for before, after in zip(original[0], changed[0]):
            self.assertEqual(before[:1] + before[2:], after[:1] + after[2:])
            self.assertEqual(after[1], 'openai')
        for p in self.paths.values():
            self.assertEqual(files[p].split(b'\n', 1)[1], p.read_bytes().split(b'\n', 1)[1])
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.db_values(), original)
        for p, data in files.items():
            self.assertEqual(p.read_bytes(), data)

    def test_offset_digit_growth_and_physical_fork_identity(self):
        plan = self.plan()
        changes = {c.physical_id: c for c in plan.changes if c.kind == 'rollout'}
        self.assertEqual(changes[PARENT].delta, 1)
        self.assertEqual(changes[FORK].delta, 2)
        fork = json.loads(changes[FORK].new_data)['payload']
        self.assertEqual(fork['history_base']['end_byte_offset'], 1000)
        self.assertEqual(fork['id'], PARENT)
        old = self.db_values()[1]
        self.repair(plan)
        new = self.db_values()[1]
        for before, after in zip(old, new):
            delta = changes[before[0]].delta if before[0] in changes else 0
            self.assertEqual(after[2], before[2] + delta)
            self.assertEqual(after[3], before[3] + delta)

    def test_idempotent_after_repair(self):
        self.repair()
        self.assertFalse(self.plan().needed)

    def test_mcp_opt_in_retains_current_runtime_and_redacts_passwords(self):
        plan = self.plan(Options(merge_mcp=True))
        self.assertEqual(plan.mcp_added, 1)
        report = json.dumps(plan.report(), ensure_ascii=False)
        self.assertNotIn('FAKE_DATABASE_PASSWORD', report)
        self.assertNotIn('FAKE_DEMO_CREDENTIAL', report)
        self.assertNotIn('OBSOLETE_RUNTIME', plan.config_diff)

    def test_multiline_secret_redaction(self):
        content = '''[shell_environment_policy.set]\nOPAQUE_VALUE = """\nPRIVATE_LINE_ONE\nPRIVATE_LINE_TWO\n"""\n'''
        result = redact_toml(content)
        self.assertNotIn('PRIVATE_LINE', result)
        self.assertIn('已隐藏', result)

    def test_process_guard(self):
        with self.assertRaisesRegex(RepairError, '退出 Codex'):
            execute(self.plan(), process_probe=lambda: ['codex.exe'])
        self.assertFalse((self.home / 'repair-backups').exists())

    def test_stale_preview_is_rejected(self):
        plan = self.plan()
        with (self.home / 'config.toml').open('a', encoding='utf-8') as output:
            output.write('\n# Edited after preview\n')
        with self.assertRaisesRegex(RepairError, '预览后发生变化'):
            self.repair(plan)

    def test_write_failure_rolls_back(self):
        values = self.db_values()
        before = (self.home / 'config.toml').read_bytes()
        def fail(index):
            if index == 1:
                raise OSError('simulated write failure')
        with self.assertRaises(RepairError):
            self.repair(fault_hook=fail)
        self.assertEqual((self.home / 'config.toml').read_bytes(), before)
        self.assertEqual(self.db_values(), values)
        self.assertEqual(list_backups(self.home)[0]['state'], 'rolled_back')

    def test_restore_refuses_new_chat_content(self):
        backup = self.repair()
        path = self.paths[PARENT]
        with path.open('ab') as output:
            output.write(b'{"type":"new_user_message"}\n')
        before = self.db_values()
        with self.assertRaisesRegex(RepairError, '新内容'):
            restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.db_values(), before)
        self.assertTrue(path.read_bytes().endswith(b'{"type":"new_user_message"}\n'))

    def test_restore_preserves_unrelated_new_threads(self):
        backup = self.repair()
        with connect(self.home / 'state_5.sqlite') as db:
            db.execute("INSERT INTO threads VALUES('new','openai','unused','New chat',301,302,0,NULL)")
        restore(self.home, backup, process_probe=lambda: [])
        with connect(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("SELECT title FROM threads WHERE id='new'").fetchone()[0], 'New chat')

    def test_restore_conflicting_database_is_rejected(self):
        backup = self.repair()
        with connect(self.home / 'state_5.sqlite') as db:
            db.execute("UPDATE threads SET model_provider='another' WHERE id=?", (PARENT,))
        with self.assertRaisesRegex(RepairError, '索引在修复后发生了变化'):
            restore(self.home, backup, process_probe=lambda: [])

    def test_missing_history_database_fails_closed(self):
        (self.home / 'thread_history_1.sqlite').unlink()
        with self.assertRaisesRegex(RepairError, '分页数据库缺失'):
            self.plan()
        self.assertTrue(self.plan(Options(repair_history=False)).needed)

    def test_unknown_history_schema_fails_closed(self):
        with connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute('ALTER TABLE thread_turns ADD COLUMN future_byte_offset INTEGER')
        with self.assertRaisesRegex(RepairError, '未知'):
            self.plan()

    def test_bad_toml_does_not_expose_secret(self):
        (self.home / 'config.toml').write_text('api_key = "SECRET_DO_NOT_PRINT', encoding='utf-8')
        with self.assertRaises(RepairError) as error:
            self.plan()
        self.assertNotIn('SECRET_DO_NOT_PRINT', str(error.exception))

    def test_missing_old_config_is_optional(self):
        plan = analyze(self.home)
        self.assertEqual(plan.projects_added, 0)
        self.assertEqual(len(plan.rows), 2)

    def test_relative_path_escape_is_blocked(self):
        with self.assertRaises(RepairError):
            safe_path(self.home, '../outside.txt')

    def test_tampered_backup_path_is_blocked(self):
        backup = self.repair()
        manifest_path = backup / 'manifest.json'
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
        data['files'][0]['relative'] = '../outside.txt'
        manifest_path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(RepairError):
            restore(self.home, backup, process_probe=lambda: [])

    def test_profile_configuration_is_not_guessed(self):
        path = self.home / 'config.toml'
        path.write_text('profile = "work"\n' + path.read_text(encoding='utf-8'), encoding='utf-8')
        with self.assertRaisesRegex(RepairError, 'profile'):
            self.plan()

    def test_interrupted_operation_can_be_recovered(self):
        original = self.db_values()
        backup = self.repair()
        manifest_path = backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        # Simulate a crash after one file and one DB row were already rolled back.
        file = manifest['files'][0]['relative']
        (self.home / file).write_bytes((backup / 'originals' / file).read_bytes())
        with connect(self.home / 'state_5.sqlite') as db:
            db.execute("UPDATE threads SET model_provider='codex' WHERE id=?", (PARENT,))
        manifest['state'] = 'applying'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.db_values(), original)

    def test_traditional_jsonl_without_history_database(self):
        (self.home / 'thread_history_1.sqlite').unlink()
        for path in self.paths.values():
            header, body = path.read_bytes().split(b'\n', 1)
            record = json.loads(header)
            record['payload'].pop('history_mode', None)
            record['payload'].pop('history_base', None)
            path.write_bytes(json.dumps(record).encode() + b'\n' + body)
        plan = self.plan()
        self.assertIsNone(plan.history_db)
        backup = self.repair(plan)
        self.assertFalse(self.plan().needed)
        restore(self.home, backup, process_probe=lambda: [])

    def test_restore_rechecks_process_before_writing(self):
        backup = self.repair()
        calls = iter([[], ['codex.exe']])
        before = self.db_values()
        with self.assertRaisesRegex(RepairError, '退出 Codex'):
            restore(self.home, backup, process_probe=lambda: next(calls))
        self.assertEqual(self.db_values(), before)


if __name__ == '__main__':
    unittest.main()
