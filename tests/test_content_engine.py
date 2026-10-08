import json
import tempfile
import unittest
from pathlib import Path

from codex_repair.engine import (
    BACKUP_DIR, MAX_CONFIG, RepairError, analyze_content, execute,
    list_backups, read_config_content, restore, sha,
)


class ContentEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / "config.toml"
        self.original = b'model = "gpt-6-astra"\nmodel_provider = "openai"\n'
        self.config.write_bytes(self.original)
        self.candidate = self.original.decode() + 'personality = "friendly"\n'
        self.auth = self.home / "auth.json"
        self.auth_bytes = b'{"fake_login":"CONTENT_EDITOR_MUST_NOT_TOUCH_AUTH"}'
        self.auth.write_bytes(self.auth_bytes)

    def tearDown(self):
        self.assertEqual(self.auth.read_bytes(), self.auth_bytes)

    def plan(self, text=None, original_hash=None):
        return analyze_content(self.home, self.candidate if text is None else text,
                               original_hash=original_hash)

    def save(self, plan=None, **kwargs):
        return execute(plan or self.plan(), process_probe=lambda: [], **kwargs)

    def manifest(self, backup):
        return json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

    def test_invalid_original_preview_save_and_restore_exact_bytes(self):
        original = b'model = "unterminated\n# original invalid bytes\n'
        self.config.write_bytes(original)
        plan = self.plan(original_hash=sha(original))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse((self.home / BACKUP_DIR).exists())
        self.assertEqual([(change.relative, change.kind) for change in plan.changes],
                         [("config.toml", "config")])
        self.assertEqual(plan.rows, [])
        self.assertIsNone(plan.state_db)
        self.assertIsNone(plan.history_db)
        backup = self.save(plan)
        self.assertEqual((backup / "originals" / "config.toml").read_bytes(), original)
        self.assertEqual(self.config.read_bytes(), self.candidate.encode())
        manifest = self.manifest(backup)
        self.assertEqual(manifest["state"], "completed")
        self.assertEqual(manifest["patches"], [])
        self.assertEqual(manifest["databases"], [])
        self.assertEqual(manifest["summary"]["operation"], "content_repair")
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.manifest(backup)["state"], "restored")

    def test_utf8_bom_and_crlf_are_preserved(self):
        original = b"\xef\xbb\xbf" + self.original.replace(b"\n", b"\r\n")
        self.config.write_bytes(original)
        raw, text = read_config_content(self.config)
        self.assertEqual(raw, original)
        self.assertEqual(text, self.original.decode().replace("\n", "\r\n"))
        backup = self.save()
        expected = b"\xef\xbb\xbf" + self.candidate.encode().replace(b"\n", b"\r\n")
        self.assertEqual(self.config.read_bytes(), expected)
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), original)

    def test_utf16_converts_to_utf8_and_restores_original_encoding(self):
        text = self.original.decode().replace("\n", "\r\n")
        original = text.encode("utf-16")
        self.config.write_bytes(original)
        self.assertEqual(read_config_content(self.config), (original, text))
        plan = self.plan()
        self.assertTrue(any("UTF-16" in note for note in plan.notes))
        backup = self.save(plan)
        self.assertEqual(self.config.read_bytes(), self.candidate.encode().replace(b"\n", b"\r\n"))
        self.assertEqual((backup / "originals" / "config.toml").read_bytes(), original)
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), original)

    def test_invalid_candidate_never_creates_backup_or_writes_file(self):
        with self.assertRaises(RepairError):
            self.plan('model = "unterminated\n')
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_unchanged_content_has_no_save_operation(self):
        plan = self.plan(self.original.decode())
        self.assertFalse(plan.needed)
        with self.assertRaises(RepairError):
            self.save(plan)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_original_changed_after_preview_is_not_overwritten(self):
        plan = self.plan()
        newer = self.original + b'# later external edit\n'
        self.config.write_bytes(newer)
        with self.assertRaises(RepairError):
            self.save(plan)
        self.assertEqual(self.config.read_bytes(), newer)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_loaded_original_hash_rejects_external_changes_before_preview(self):
        loaded_hash = sha(self.original)
        newer = self.original + b'# external edit after loading\n'
        self.config.write_bytes(newer)
        with self.assertRaises(RepairError):
            self.plan(original_hash=loaded_hash)
        self.assertEqual(self.config.read_bytes(), newer)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_oversized_candidate_is_rejected_without_mutation(self):
        with self.assertRaises(RepairError):
            self.plan("#" + "a" * MAX_CONFIG)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_oversized_original_is_rejected_even_when_candidate_small(self):
        original = b"#" + b"a" * MAX_CONFIG
        self.config.write_bytes(original)
        with self.assertRaises(RepairError):
            read_config_content(self.config)
        with self.assertRaises(RepairError):
            self.plan()
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_running_codex_prevents_save_before_backup(self):
        plan = self.plan()
        with self.assertRaises(RepairError):
            execute(plan, process_probe=lambda: ["codex.exe"])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.home / BACKUP_DIR).exists())

    def test_codex_started_during_backup_prevents_config_replacement(self):
        calls = iter([[], ["codex.exe"]])
        with self.assertRaises(RepairError):
            execute(self.plan(), process_probe=lambda: next(calls))
        self.assertEqual(self.config.read_bytes(), self.original)
        backups = list_backups(self.home)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["state"], "cancelled")
        self.assertEqual((backups[0]["path"] / "originals" / "config.toml").read_bytes(), self.original)

    def test_failed_save_rolls_back_invalid_original(self):
        original = b'model = "unterminated\n'
        self.config.write_bytes(original)
        def fail_after_replace(_index):
            raise OSError("FAKE_WRITE_FAILURE")
        with self.assertRaises(RepairError):
            self.save(fault_hook=fail_after_replace)
        self.assertEqual(self.config.read_bytes(), original)
        backups = list_backups(self.home)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["state"], "rolled_back")
        self.assertEqual((backups[0]["path"] / "originals" / "config.toml").read_bytes(), original)

    def test_broken_history_files_and_databases_are_not_read_or_modified(self):
        paths = [self.home / "state_5.sqlite", self.home / "thread_history_1.sqlite",
                 self.home / "sessions" / "malformed.jsonl"]
        original = b"BROKEN_HISTORY_MUST_NOT_BE_SCANNED"
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
        backup = self.save()
        self.assertEqual(self.manifest(backup)["databases"], [])
        self.assertEqual(self.manifest(backup)["patches"], [])
        self.assertEqual(len(self.manifest(backup)["files"]), 1)
        restore(self.home, backup, process_probe=lambda: [])
        for path in paths:
            self.assertEqual(path.read_bytes(), original)

    def test_secrets_are_absent_from_plan_reports_and_progress(self):
        old_secret = "FAKE_ORIGINAL_CONTENT_SECRET_DO_NOT_EXPOSE"
        new_secret = "FAKE_CANDIDATE_CONTENT_SECRET_DO_NOT_EXPOSE"
        self.config.write_bytes((self.original.decode() + f'api_key = "{old_secret}"\n').encode())
        candidate = self.candidate + f'api_key = "{new_secret}"\n'
        progress = []
        plan = analyze_content(self.home, candidate, progress=progress.append)
        self.assertNotIn(old_secret, repr(plan))
        self.assertNotIn(new_secret, repr(plan))
        self.assertNotIn(new_secret, repr(plan.options))
        report = json.dumps(plan.report(), ensure_ascii=False)
        for secret in (old_secret, new_secret):
            self.assertNotIn(secret, report)
        backup = execute(plan, progress=progress.append, process_probe=lambda: [])
        for path in (backup / "manifest.json", backup / "report.json"):
            saved_report = path.read_text(encoding="utf-8")
            for secret in (old_secret, new_secret):
                self.assertNotIn(secret, saved_report)
        for secret in (old_secret, new_secret):
            self.assertNotIn(secret, "\n".join(progress))

    def test_invalid_input_errors_do_not_echo_sensitive_lines(self):
        secret = "FAKE_INVALID_CONTENT_SECRET_DO_NOT_EXPOSE"
        candidate = self.candidate + f'api_key = "{secret}\n'
        with self.assertRaises(RepairError) as raised:
            self.plan(candidate)
        self.assertNotIn(secret, str(raised.exception))
        self.config.write_bytes(candidate.encode())
        plan = self.plan()
        self.assertNotIn(secret, repr(plan))
        self.assertNotIn(secret, json.dumps(plan.report(), ensure_ascii=False))
        self.assertEqual(self.config.read_bytes(), candidate.encode())


if __name__ == "__main__":
    unittest.main()
