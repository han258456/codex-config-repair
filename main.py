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
        window.change_page(2)
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
        window.change_page(2)
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
        window.close()
        temporary.cleanup()
        print(json.dumps({"gui_scan": "passed", "gui_repair": "passed", "gui_restore": "passed", "gui_relay_switch": "passed", "gui_direct_switch": "passed", "gui_connection_restore": "passed", "fixture_only": True}))
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
