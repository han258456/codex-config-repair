from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Codex 配置修复助手")
    parser.add_argument("--demo", action="store_true", help="在独立临时目录展示模拟数据")
    parser.add_argument("--smoke-test", action="store_true", help="使用模拟数据自动检查界面，不访问真实 Codex 数据")
    parser.add_argument("--screenshot", type=Path, help="烟雾测试时保存界面截图")
    args = parser.parse_args()
    if args.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from codex_repair.demo import create_demo
    from codex_repair.engine import running_codex
    from codex_repair.window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("CodexConfigRepair")
    app.setOrganizationName("CodexConfigRepair")
    if args.smoke_test and os.name == 'nt':
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        for filename in ['msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf']:
            font = fonts / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
    temporary = None
    if args.demo or args.smoke_test:
        temporary = tempfile.TemporaryDirectory(prefix="codex-repair-demo-")
        home = Path(temporary.name)
        create_demo(home)
        window = MainWindow(home)
        # The exception applies only to our newly created isolated fixture directory.
        window.probe = lambda: [] if Path(window.home_edit.text()).resolve() == home.resolve() else running_codex()
        window.setWindowTitle("Codex 配置修复助手 · 模拟数据演示")
    else:
        window = MainWindow()
    window.show()
    if args.smoke_test:
        from PySide6.QtCore import Qt
        def wait_idle():
            deadline = time.monotonic() + 30
            while window.busy and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(15)
            if window.busy:
                raise RuntimeError("GUI worker timeout")
            app.processEvents()
        QTest.mouseClick(window.scan_button, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.plan is not None and len(window.plan.rows) == 2, window.message.text()
        assert window.repair_button.isEnabled()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(args.screenshot))
        QTest.mouseClick(window.repair_button, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.last_backup is not None, window.message.text()
        window.change_page(3)
        assert window.backup_table.topLevelItemCount() == 1
        assert window.restore_button.isEnabled()
        QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
        wait_idle()
        manifest = json.loads((window.last_backup / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["state"] == "restored"
        # Exercise connection changes against the same isolated fixture.
        from codex_repair.engine import connection_summary, RELAY_PROVIDER
        window.change_page(1)
        assert window.direct_button.isChecked()
        QTest.mouseClick(window.relay_button, Qt.MouseButton.LeftButton)
        assert window.relay_url.isEnabled() and window.relay_key.isEnabled()
        window.relay_url.setText('https://gateway.example.com/v1')
        window.relay_key.setText('FAKE_GUI_RELAY_KEY')
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is not None, window.connection_message.text()
        assert window.connection_plan.provider == RELAY_PROVIDER
        assert len(window.connection_plan.rows) == 3
        assert 'FAKE_GUI_RELAY_KEY' not in window.connection_diff.toPlainText()
        original_probe = window.probe
        window.probe = lambda: ['codex.exe']
        window.update_availability()
        assert not window.connection_apply.isEnabled() and window.connection_preview.isEnabled()
        window.probe = original_probe
        window.update_availability()
        if args.screenshot:
            assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-connection')))
        QTest.mouseClick(window.connection_apply, Qt.MouseButton.LeftButton)
        wait_idle()
        assert connection_summary(home)['provider'] == RELAY_PROVIDER, window.connection_message.text()
        relay_backup = window.last_backup
        assert not window.relay_key.text()
        assert '第三方中转' in window.connection_current.text()
        # An empty field must not reuse the saved key at a different endpoint.
        window.relay_url.setText('https://another.example.com/v1')
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is None
        assert '密钥' in window.connection_message.text()
        assert not window.connection_apply.isEnabled()
        QTest.mouseClick(window.direct_button, Qt.MouseButton.LeftButton)
        assert not window.relay_key.isEnabled()
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is not None
        QTest.mouseClick(window.connection_apply, Qt.MouseButton.LeftButton)
        wait_idle()
        assert connection_summary(home)['provider'] == 'openai'
        assert connection_summary(home)['has_key']
        direct_backup = window.last_backup
        # Pick exact backups: multiple operations may share a timestamp second.
        window.change_page(3)
        for backup in [direct_backup, relay_backup]:
            for i in range(window.backup_table.topLevelItemCount()):
                item = window.backup_table.topLevelItem(i)
                if item.data(0, Qt.ItemDataRole.UserRole)['path'] == backup:
                    window.backup_table.setCurrentItem(item)
                    break
            assert window.restore_button.isEnabled()
            QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
            wait_idle()
            assert json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'restored'
        # Read saved direct/relay files into the candidate form before writing.
        from PySide6.QtWidgets import QFileDialog, QLineEdit
        import tomlkit
        direct_file = home / 'direct-connection.toml'
        direct_file.write_bytes('model_provider = "openai"\nmodel = "imported-direct-model"\n'.encode('utf-16'))
        relay_file = home / 'relay-connection.toml-bf'
        relay_file.write_text('model_provider = "existing_gateway"\nmodel = "imported-relay-model"\n'
                              '[model_providers.existing_gateway]\nbase_url = "https://import.example.com/v1/"\n'
                              'wire_api = "responses"\nexperimental_bearer_token = "FAKE_GUI_FILE_KEY"\n', encoding='utf-8')
        invalid_file = home / 'invalid-connection.bak'
        invalid_file.write_text('api_key = "FAKE_GUI_FILE_SECRET', encoding='utf-8')
        original_config = (home / 'config.toml').read_bytes()
        fixture_auth = home / 'auth.json'
        fixture_auth.write_bytes(b'{"login":"FAKE_GUI_PRESERVED_LOGIN"}')
        original_auth = fixture_auth.read_bytes()

        def read_connection_file(path):
            dialog = QFileDialog.getOpenFileName
            QFileDialog.getOpenFileName = lambda *a, **kw: (str(path) if path else '', '')
            try:
                QTest.mouseClick(window.connection_import, Qt.MouseButton.LeftButton)
            finally:
                QFileDialog.getOpenFileName = dialog

        window.change_page(1)
        for source, provider, model in [(direct_file, 'openai', 'imported-direct-model'),
                                        (relay_file, RELAY_PROVIDER, 'imported-relay-model')]:
            source_bytes = source.read_bytes()
            window.show_key.setChecked(True)
            read_connection_file(source)
            assert window.connection_model.text() == model
            assert window.direct_button.isChecked() == (provider == 'openai')
            assert not window.show_key.isChecked()
            assert window.relay_key.echoMode() == QLineEdit.EchoMode.Password
            assert window.connection_plan is None and not window.connection_apply.isEnabled()
            assert (home / 'config.toml').read_bytes() == original_config
            assert source.read_bytes() == source_bytes
            if provider == RELAY_PROVIDER:
                assert window.relay_url.text() == 'https://import.example.com/v1'
                assert window.relay_key.text() == 'FAKE_GUI_FILE_KEY'
            QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
            wait_idle()
            assert window.connection_plan is not None, window.connection_message.text()
            assert window.connection_plan.provider == provider
            assert 'FAKE_GUI_FILE_KEY' not in window.connection_diff.toPlainText()
            assert 'FAKE_GUI_FILE_KEY' not in json.dumps(window.connection_plan.report())
            if args.screenshot and provider == RELAY_PROVIDER:
                assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-connection-import')))
            QTest.mouseClick(window.connection_apply, Qt.MouseButton.LeftButton)
            wait_idle()
            imported_backup = window.last_backup
            assert connection_summary(home)['provider'] == provider
            assert connection_summary(home)['model'] == model
            assert fixture_auth.read_bytes() == original_auth
            assert source.read_bytes() == source_bytes
            window.change_page(3)
            for i in range(window.backup_table.topLevelItemCount()):
                item = window.backup_table.topLevelItem(i)
                if item.data(0, Qt.ItemDataRole.UserRole)['path'] == imported_backup:
                    window.backup_table.setCurrentItem(item)
                    break
            QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
            wait_idle()
            assert (home / 'config.toml').read_bytes() == original_config
            window.change_page(1)
        read_connection_file(relay_file)
        form = (window.relay_url.text(), window.relay_key.text(), window.connection_model.text())
        read_connection_file(None)
        assert form == (window.relay_url.text(), window.relay_key.text(), window.connection_model.text())
        read_connection_file(invalid_file)
        assert form == (window.relay_url.text(), window.relay_key.text(), window.connection_model.text())
        assert 'FAKE_GUI_FILE_SECRET' not in window.connection_message.text()
        assert window.connection_plan is None and not window.connection_apply.isEnabled()
        assert (home / 'config.toml').read_bytes() == original_config
        # Current custom providers must prefill the active endpoint, without keys.
        custom = tomlkit.parse(original_config.decode('utf-8'))
        custom['model_provider'] = 'existing_gateway'
        custom['model_providers'] = {'existing_gateway': {'base_url': 'https://current.example.com/v1',
                                                        'experimental_bearer_token': 'FAKE_CURRENT_CUSTOM_KEY'}}
        (home / 'config.toml').write_bytes(tomlkit.dumps(custom).encode('utf-8'))
        app.processEvents()
        QTest.mouseClick(window.connection_reload, Qt.MouseButton.LeftButton)
        assert window.relay_button.isChecked()
        assert window.relay_url.text() == 'https://current.example.com/v1', (window.relay_url.text(), window.connection_message.text(), connection_summary(home), window.connection_reload.isEnabled())
        assert not window.relay_key.text()
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is not None, window.connection_message.text()
        assert 'FAKE_CURRENT_CUSTOM_KEY' not in window.connection_diff.toPlainText()
        (home / 'config.toml').write_bytes(original_config)
        QTest.mouseClick(window.connection_reload, Qt.MouseButton.LeftButton)
        # An orphan and a relocated local rollout must not block either page.
        import sqlite3
        from codex_repair.demo import PARENT
        from codex_repair.engine import ClosingConnection
        orphan_id = '55555555-5555-4555-8555-555555555555'
        with sqlite3.connect(home / 'state_5.sqlite', factory=ClosingConnection) as db:
            original_path = db.execute('SELECT rollout_path FROM threads WHERE id=?', (PARENT,)).fetchone()[0]
            stale_path = str(home / 'sessions' / 'old-directory' / Path(original_path).name)
            db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (stale_path, PARENT))
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (orphan_id, 'codex', 'archived_sessions/missing-history.jsonl', 'Missing sample', 1, 2, 1, 2))
        window.change_page(0)
        QTest.mouseClick(window.scan_button, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.plan is not None, window.message.text()
        assert len(window.plan.skipped_threads) == len(window.plan.relinked_threads) == 1
        assert window.repair_button.isEnabled()
        assert '跳过 1' in window.message.text()
        assert any(orphan_id in window.preview.topLevelItem(i).text(1)
                   for i in range(window.preview.topLevelItemCount()))
        if args.screenshot:
            assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-history-recovery')))
        window.change_page(1)
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is not None, window.connection_message.text()
        assert len(window.connection_plan.skipped_threads) == len(window.connection_plan.relinked_threads) == 1
        assert window.connection_apply.isEnabled()
        assert '跳过 1' in window.connection_message.text()
        assert orphan_id in window.connection_diff.toPlainText()
        if args.screenshot:
            assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-connection-recovery')))
        QTest.mouseClick(window.connection_apply, Qt.MouseButton.LeftButton)
        wait_idle()
        recovery_backup = window.last_backup
        assert '跳过 1' in window.connection_message.text(), window.connection_message.text()
        with sqlite3.connect(home / 'state_5.sqlite', factory=ClosingConnection) as db:
            assert db.execute('SELECT rollout_path FROM threads WHERE id=?', (PARENT,)).fetchone()[0] == Path(original_path).as_posix()
            assert db.execute('SELECT model_provider FROM threads WHERE id=?', (orphan_id,)).fetchone()[0] == 'codex'
        QTest.mouseClick(window.connection_preview, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.connection_plan is not None and not window.connection_plan.needed
        assert not window.connection_apply.isEnabled()
        assert '跳过 1' in window.connection_message.text()
        window.change_page(3)
        for i in range(window.backup_table.topLevelItemCount()):
            item = window.backup_table.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)['path'] == recovery_backup:
                window.backup_table.setCurrentItem(item)
                break
        QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
        wait_idle()
        assert json.loads((recovery_backup / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'restored'
        with sqlite3.connect(home / 'state_5.sqlite', factory=ClosingConnection) as db:
            assert db.execute('SELECT rollout_path FROM threads WHERE id=?', (PARENT,)).fetchone()[0] == stale_path
            db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (original_path, PARENT))
            db.execute('DELETE FROM threads WHERE id=?', (orphan_id,))
        # Content editing handles invalid originals without touching real data.
        from codex_repair.engine import read_toml
        from PySide6.QtWidgets import QFileDialog
        secret = 'FAKE_GUI_CONTENT_SECRET'
        broken = ('```toml\r\nmodel = “demo-model”\r\nmodel_supports_reasoning_summaries = True\r\n'
                  'model_provider = "openai"\r\napi_key = "' + secret + '"\r\n```\r\n').encode('utf-8')
        config = home / 'config.toml'
        config.write_bytes(broken)
        window.change_page(2)
        assert window.content_editor.isReadOnly()
        assert secret not in window.content_editor.toPlainText()
        QTest.mouseClick(window.content_scan, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.content_check is not None and not window.content_check.valid
        assert not window.content_apply.isEnabled()
        assert config.read_bytes() == broken
        # Invalid-content reports must be available, and never contain input.
        original_dialog = QFileDialog.getSaveFileName
        report_path = home / 'content-report.json'
        QFileDialog.getSaveFileName = lambda *a, **kw: (str(report_path), '')
        try:
            QTest.mouseClick(window.content_export, Qt.MouseButton.LeftButton)
        finally:
            QFileDialog.getSaveFileName = original_dialog
        assert report_path.is_file() and secret not in report_path.read_text(encoding='utf-8')
        imported = home / 'import-content.toml'
        imported.write_bytes(broken.decode('utf-8').encode('utf-16'))
        original_open_dialog = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = lambda *a, **kw: (str(imported), '')
        try:
            QTest.mouseClick(window.content_import, Qt.MouseButton.LeftButton)
        finally:
            QFileDialog.getOpenFileName = original_open_dialog
        assert window._content_text == broken.decode('utf-8')
        assert window.content_check is None and window.content_plan is None
        assert config.read_bytes() == broken
        QTest.mouseClick(window.content_auto, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.content_plan is not None, window.content_message.text()
        assert len(window.content_check.fixes) >= 3
        assert config.read_bytes() == broken
        assert secret not in window.content_diff.toPlainText()
        assert secret not in json.dumps(window.content_plan.report(), ensure_ascii=False)
        assert secret not in window.content_editor.toPlainText()
        # Editing invalidates the preview; hiding raw content keeps the edits.
        QTest.mouseClick(window.content_show, Qt.MouseButton.LeftButton)
        assert not window.content_editor.isReadOnly()
        assert secret in window.content_editor.toPlainText()
        edited = window.content_editor.toPlainText().replace('demo-model', 'edited-demo-model')
        window.content_editor.setPlainText(edited)
        assert window.content_plan is None and not window.content_apply.isEnabled()
        QTest.mouseClick(window.content_show, Qt.MouseButton.LeftButton)
        assert window._content_text == edited and secret not in window.content_editor.toPlainText()
        QTest.mouseClick(window.content_scan, Qt.MouseButton.LeftButton)
        wait_idle()
        assert window.content_plan is not None and window.content_apply.isEnabled()
        window.probe = lambda: ['codex.exe']
        window.update_availability()
        assert not window.content_apply.isEnabled() and window.content_scan.isEnabled()
        window.probe = original_probe
        window.update_availability()
        if args.screenshot:
            window.content_tabs.setCurrentIndex(0)
            assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-content')))
            window.content_tabs.setCurrentIndex(1)
            assert window.grab().save(str(args.screenshot.with_stem(args.screenshot.stem + '-content-check')))
        QTest.mouseClick(window.content_apply, Qt.MouseButton.LeftButton)
        wait_idle()
        content_backup = window.last_backup
        assert content_backup is not None
        assert (content_backup / 'originals' / 'config.toml').read_bytes() == broken
        assert read_toml(config)[1]['model'] == 'edited-demo-model'
        assert b'\r\n' in config.read_bytes()
        assert window.content_editor.isReadOnly() and not window.content_show.isChecked()
        assert secret not in window.content_editor.toPlainText()
        window.change_page(3)
        for i in range(window.backup_table.topLevelItemCount()):
            item = window.backup_table.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)['path'] == content_backup:
                window.backup_table.setCurrentItem(item)
                break
        QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
        wait_idle()
        assert config.read_bytes() == broken
        window.change_page(2)
        assert window.content_original_hash is not None
        window.home_edit.setText(str(home / 'missing-directory'))
        assert window.content_plan is None and window.content_check is None
        assert window.content_original_hash is None and not window.content_apply.isEnabled()
        assert not window._content_text and not window.content_editor.toPlainText()
        window.close()
        temporary.cleanup()
        print(json.dumps({"gui_scan": "passed", "gui_repair": "passed", "gui_restore": "passed", "gui_relay_switch": "passed", "gui_direct_switch": "passed", "gui_connection_restore": "passed", "gui_connection_import": "passed", "gui_current_custom_read": "passed", "gui_history_recovery": "passed", "gui_connection_recovery": "passed", "gui_content_check": "passed", "gui_content_auto_fix": "passed", "gui_content_edit": "passed", "gui_content_save_restore": "passed", "gui_content_redaction": "passed", "fixture_only": True}))
        return 0
    result = app.exec()
    if temporary:
        temporary.cleanup()
    return result


if __name__ == "__main__":
    diagnostic = None
    if '--smoke-test' in sys.argv:
        if '--screenshot' in sys.argv:
            destination = Path(sys.argv[sys.argv.index('--screenshot') + 1]).with_suffix('.log')
        else:
            destination = Path(tempfile.gettempdir()) / 'codex-config-repair-smoke.log'
        destination.parent.mkdir(parents=True, exist_ok=True)
        diagnostic = destination.open('w', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = diagnostic
    try:
        result = main()
    except Exception:
        if diagnostic:
            traceback.print_exc(file=diagnostic)
            result = 1
        else:
            raise
    finally:
        if diagnostic:
            diagnostic.close()
    raise SystemExit(result)
