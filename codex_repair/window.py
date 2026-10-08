from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QStyle, QStyleOptionButton,
)

from . import __version__
from .content import check_content
from .engine import (ConnectionSettings, Options, Plan, RELAY_PROVIDER, RepairError, analyze,
                     analyze_content, connection_summary, default_home, execute, list_backups,
                     read_config_content, redact_toml, restore, running_codex, sha)


STYLE = """
QMainWindow, QWidget#canvas { background: #F1F4F8; color: #172B3F; }
QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'; font-size: 13px; color: #172B3F; }
QFrame#sidebar { background: #142333; border: none; }
QLabel#brand { color: #F1FBF9; font-size: 20px; font-weight: 700; }
QLabel#brandSub { color: #9CAEBE; font-size: 11px; }
QLabel#sideFoot { color: #8FA6B7; font-size: 11px; line-height: 1.5; }
QPushButton#nav { text-align: left; padding: 13px 16px; border: none; border-radius: 8px; color: #B8C8D6; background: transparent; }
QPushButton#nav:checked { color: #DDFDF4; background: #21493F; font-weight: 600; }
QPushButton#nav:hover { background: #243748; }
QLabel#eyebrow { color: #087E70; font-size: 11px; font-weight: 700; }
QLabel#heading { color: #152B3E; font-size: 26px; font-weight: 700; }
QLabel#subtle { color: #6E7D8E; font-size: 12px; }
QLabel#cardTitle { font-size: 13px; font-weight: 600; color: #30475A; }
QLabel#metric { font-size: 25px; font-weight: 700; color: #183C46; }
QLabel#metricSmall { font-size: 18px; font-weight: 700; color: #183C46; }
QLabel#badge { color: #916126; background: #FFF1D9; border: 1px solid #EFDCB9; padding: 6px 10px; border-radius: 6px; font-size: 11px; }
QLabel#badge[running="false"] { color: #137566; background: #DDF2E9; border-color: #C5E5D8; }
QFrame#card { background: white; border: 1px solid #DCE4ED; border-radius: 10px; }
QLineEdit { background: #F8FAFC; border: 1px solid #D9E2EC; border-radius: 6px; padding: 9px 10px; selection-background-color: #BEDFD8; }
QLineEdit:focus { border: 1px solid #168C7F; background: white; }
QPushButton { background: white; border: 1px solid #CDD9E4; border-radius: 6px; padding: 9px 15px; font-weight: 500; }
QPushButton:hover { border-color: #168C7F; color: #08796D; background: #F6FCFA; }
QPushButton:disabled { color: #98A4AF; background: #EDF1F5; border-color: #DEE5EC; }
QPushButton#mode:checked { color: #08796D; background: #E1F3EC; border: 1px solid #168C7F; }
QPushButton#primary { color: white; background: #087F72; border-color: #087F72; font-weight: 600; }
QPushButton#primary:hover { background: #086B61; }
QPushButton#primary:disabled { background: #A9C8C2; border-color: #A9C8C2; color: #F7FAF9; }
QCheckBox { spacing: 7px; color: #405469; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #BBCAD8; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #087F72; border: 3px solid #BDE6DC; }
QTabWidget::pane { border: 1px solid #DCE4ED; background: white; border-radius: 8px; }
QTabBar::tab { padding: 10px 17px; color: #718293; background: transparent; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #087F72; border-bottom: 2px solid #087F72; font-weight: 600; }
QTreeWidget, QPlainTextEdit { background: white; border: none; padding: 6px; selection-background-color: #D9EFE9; selection-color: #16483F; }
QHeaderView::section { background: #F7F9FC; color: #667A8D; border: none; border-bottom: 1px solid #E8EDF3; padding: 9px; font-size: 11px; }
QTreeWidget::item { padding: 7px; }
QTreeWidget::item:selected { background: #E2F2EC; color: #184E41; }
QProgressBar { border: none; border-radius: 2px; background: #DAE5EB; max-height: 4px; min-height: 4px; }
QProgressBar::chunk { background: #129B86; border-radius: 2px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: #ECF0F4; margin: 0; }
QScrollBar::handle:vertical { background: #CAD5DF; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #EAF3F7; background: #243E50; border: none; padding: 6px; }
"""


def app_icon(size=96) -> QIcon:
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0B8072"))
    painter.drawRoundedRect(0, 0, size, size, size * .22, size * .22)
    painter.setPen(QPen(QColor("#E5FFF5"), size * .075, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawLine(int(size*.40), int(size*.31), int(size*.23), int(size*.50))
    painter.drawLine(int(size*.23), int(size*.50), int(size*.40), int(size*.69))
    painter.drawLine(int(size*.59), int(size*.31), int(size*.76), int(size*.50))
    painter.drawLine(int(size*.76), int(size*.50), int(size*.59), int(size*.69))
    painter.end()
    return QIcon(image)


def label(text, role=None, wrap=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(wrap)
    if role:
        result.setObjectName(role)
    return result


def card():
    frame = QFrame()
    frame.setObjectName("card")
    return frame


class Worker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function(self.progress.emit)
            self.succeeded.emit(result)
        except RepairError as error:
            self.failed.emit(str(error))
        except (OSError, ValueError):
            self.failed.emit("文件读取或写入失败。请检查目录访问权限、文件是否损坏以及可用空间。")
        except Exception:
            self.failed.emit("操作未能完成。原始备份会保留；请在备份页检查是否需要恢复。")


class CheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('white'), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            center = rect.center()
            painter.drawLine(center.x()-4, center.y(), center.x()-1, center.y()+3)
            painter.drawLine(center.x()-1, center.y()+3, center.x()+4, center.y()-3)
            painter.end()


class MainWindow(QMainWindow):
    def __init__(self, home: Path | None = None, source: Path | None = None, process_probe=running_codex):
        super().__init__()
        self.plan: Plan | None = None
        self.connection_plan: Plan | None = None
        self.connection_loaded_home: str | None = None
        self.content_plan: Plan | None = None
        self.content_check = None
        self.content_loaded_home: str | None = None
        self.content_original_hash: str | None = None
        self._content_text = ""
        self._content_syncing = False
        self.worker: Worker | None = None
        self.probe = process_probe
        self.last_backup: Path | None = None
        self.busy = False
        self.setWindowTitle("Codex 配置修复助手")
        self.setWindowIcon(app_icon())
        self.resize(1180, 860)
        self.setMinimumSize(1040, 720)
        self.setStyleSheet(STYLE)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        shell = QHBoxLayout(canvas)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.setCentralWidget(canvas)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 30, 18, 24)
        side.setSpacing(7)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        side.addWidget(logo)
        side.addSpacing(8)
        side.addWidget(label("Codex 修复助手", "brand"))
        side.addWidget(label("CONFIG & HISTORY REPAIR", "brandSub"))
        side.addSpacing(30)
        self.nav = []
        for index, text in enumerate(["01   检查与修复", "02   连接切换", "03   配置内容修复", "04   备份与恢复", "05   使用帮助"]):
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.change_page(i))
            side.addWidget(button)
            self.nav.append(button)
        side.addStretch()
        side.addWidget(label("全部处理均在本机完成\n配置凭据不会上传", "sideFoot", True))
        side.addSpacing(12)
        side.addWidget(label(f"Windows · PySide6\nVersion {__version__}", "sideFoot"))
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self._make_repair_page(home or default_home(), source)
        self._make_connection_page()
        self._make_content_page()
        self._make_backups_page()
        self._make_help_page()
        self.change_page(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_availability)
        self.timer.start(2500)
        self.update_availability()

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        panel.setObjectName("canvas")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        scroll.setWidget(panel)
        self.pages.addWidget(scroll)
        return layout

    def heading(self, layout, eyebrow, title, subtitle):
        layout.addWidget(label(eyebrow, "eyebrow"))
        layout.addWidget(label(title, "heading"))
        layout.addWidget(label(subtitle, "subtle", True))

    def _make_repair_page(self, home, source):
        layout = self.page()
        head = QHBoxLayout()
        head.addWidget(label("WORKSPACE RECOVERY  /  本地兼容修复", "eyebrow"))
        head.addStretch()
        self.badge = label("正在检测运行状态", "badge")
        head.addWidget(self.badge)
        layout.addLayout(head)
        layout.addWidget(label("找回旧会话，延续当前配置。", "heading"))
        layout.addWidget(label("选择 Codex 目录与旧配置，预览差异后完成备份和修复。", "subtle"))

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metrics = []
        for title, value, small in [("当前连接", "等待扫描", True), ("待兼容会话", "—", False), ("可恢复项目", "—", False)]:
            frame = card()
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(18, 12, 18, 14)
            inner.setSpacing(6)
            inner.addWidget(label(title, "subtle"))
            metric = label(value, "metricSmall" if small else "metric")
            inner.addWidget(metric)
            metrics.addWidget(frame, 1)
            self.metrics.append(metric)
        layout.addLayout(metrics)

        paths = card()
        form = QVBoxLayout(paths)
        form.setContentsMargins(18, 15, 18, 16)
        form.setSpacing(8)
        form.addWidget(label("配置位置", "cardTitle"))
        self.home_edit = QLineEdit(str(home))
        self.home_edit.setAccessibleName("Codex 数据目录")
        candidate = source or (home / "config.toml-bf" if (home / "config.toml-bf").exists() else None)
        self.source_edit = QLineEdit(str(candidate) if candidate else "")
        self.source_edit.setAccessibleName("旧配置文件")
        self.source_edit.setPlaceholderText("可选：config.toml-bf、.bak 或其他旧配置")
        self.browse_home = QPushButton("选择目录")
        self.browse_home.clicked.connect(self.select_home)
        self.browse_source = QPushButton("选择文件")
        self.browse_source.clicked.connect(self.select_source)
        for title, edit, button in [("Codex 目录", self.home_edit, self.browse_home), ("旧配置文件", self.source_edit, self.browse_source)]:
            row = QHBoxLayout()
            caption = label(title, "subtle")
            caption.setFixedWidth(78)
            row.addWidget(caption)
            row.addWidget(edit, 1)
            row.addWidget(button)
            form.addLayout(row)
        self.home_edit.textChanged.connect(self.invalidate)
        self.home_edit.textChanged.connect(self.connection_home_changed)
        self.home_edit.textChanged.connect(self.content_home_changed)
        self.source_edit.textChanged.connect(self.invalidate)
        layout.addWidget(paths)

        options = QHBoxLayout()
        self.merge_check = CheckBox("合并项目和界面偏好")
        self.history_check = CheckBox("兼容旧历史记录")
        self.mcp_check = CheckBox("恢复自定义 MCP 工具")
        self.merge_check.setChecked(True)
        self.history_check.setChecked(True)
        self.mcp_check.setToolTip("从旧配置补充当前缺失的自定义工具及其连接凭据；桌面内置运行工具会跳过。")
        for check in [self.merge_check, self.history_check, self.mcp_check]:
            options.addWidget(check)
            check.toggled.connect(self.invalidate)
        options.addStretch()
        layout.addLayout(options)

        self.tabs = QTabWidget()
        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(["检查项", "处理结果"])
        self.preview.setRootIsDecorated(False)
        self.preview.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.preview.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.preview.addTopLevelItem(QTreeWidgetItem(["尚未扫描", "先读取配置与历史索引；扫描不会修改数据。 "]))
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(QFont("Consolas", 10))
        self.diff.setPlaceholderText("扫描后展示配置差异，敏感值会自动隐藏。")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("这里会显示处理进度。")
        self.tabs.addTab(self.preview, "修复预览")
        self.tabs.addTab(self.diff, "配置差异")
        self.tabs.addTab(self.log, "运行记录")
        self.tabs.setMinimumHeight(195)
        layout.addWidget(self.tabs, 1)

        self.message = label("先扫描，确认当前连接与待修复内容。", "subtle", True)
        self.message.setMinimumHeight(30)
        layout.addWidget(self.message)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        actions = QHBoxLayout()
        self.export_button = QPushButton("导出检测报告")
        self.export_button.clicked.connect(self.export_report)
        self.scan_button = QPushButton("扫描并预览")
        self.scan_button.clicked.connect(self.scan)
        self.repair_button = QPushButton("备份并修复")
        self.repair_button.setObjectName("primary")
        self.repair_button.clicked.connect(self.repair)
        actions.addWidget(self.export_button)
        actions.addStretch()
        actions.addWidget(self.scan_button)
        actions.addWidget(self.repair_button)
        layout.addLayout(actions)

    def _make_connection_page(self):
        layout = self.page()
        self.heading(layout, "CONNECTION SETTINGS", "连接方式，随时切换。", "选择官方直连或第三方中转。先预览，退出 Codex 后备份并应用。")
        current = card()
        current_layout = QVBoxLayout(current)
        current_layout.setContentsMargins(18, 14, 18, 14)
        row = QHBoxLayout()
        self.connection_current = label("等待读取当前连接", "cardTitle", True)
        row.addWidget(self.connection_current, 1)
        self.connection_reload = QPushButton("重新读取")
        self.connection_reload.clicked.connect(self.load_connection)
        row.addWidget(self.connection_reload)
        current_layout.addLayout(row)
        self.connection_home = label("", "subtle", True)
        current_layout.addWidget(self.connection_home)
        layout.addWidget(current)

        modes = QHBoxLayout()
        self.connection_group = QButtonGroup(self)
        self.direct_button = QPushButton("OpenAI 直连")
        self.relay_button = QPushButton("第三方中转")
        for button in [self.direct_button, self.relay_button]:
            button.setObjectName("mode")
            button.setCheckable(True)
            button.setMinimumHeight(46)
            self.connection_group.addButton(button)
            modes.addWidget(button, 1)
        self.direct_button.setChecked(True)
        layout.addLayout(modes)

        settings = card()
        form = QVBoxLayout(settings)
        form.setContentsMargins(18, 15, 18, 16)
        form.setSpacing(8)
        self.connection_hint = label("", "subtle", True)
        form.addWidget(self.connection_hint)
        self.relay_url = QLineEdit()
        self.relay_url.setAccessibleName("中转接口地址")
        self.relay_url.setPlaceholderText("例如 https://gateway.example.com/v1")
        self.relay_key = QLineEdit()
        self.relay_key.setAccessibleName("第三方 API 密钥")
        self.relay_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.relay_key.setPlaceholderText("填写第三方服务商提供的密钥")
        self.relay_url.textEdited.connect(lambda _: self.relay_key.clear())
        self.show_key = CheckBox("显示")
        self.show_key.toggled.connect(lambda checked: self.relay_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        self.connection_model = QLineEdit()
        self.connection_model.setAccessibleName("目标模型名称")
        self.connection_model.setPlaceholderText("可选：留空保留当前模型；中转模型名称以服务商为准")
        for title, edit in [("接口地址", self.relay_url), ("API 密钥", self.relay_key), ("模型名称", self.connection_model)]:
            row = QHBoxLayout()
            caption = label(title, "subtle")
            caption.setFixedWidth(76)
            row.addWidget(caption)
            row.addWidget(edit, 1)
            if edit is self.relay_key:
                row.addWidget(self.show_key)
            form.addLayout(row)
            edit.textChanged.connect(self.invalidate_connection)
        self.connection_history = CheckBox("同步兼容历史记录（推荐）")
        self.connection_history.setChecked(True)
        self.connection_history.toggled.connect(self.invalidate_connection)
        form.addWidget(self.connection_history)
        form.addWidget(label("密钥以明文保存在本机 config.toml，输入框和报告默认隐藏。原始备份可能含密钥，请妥善保存。", "subtle", True))
        layout.addWidget(settings)

        self.connection_diff = QPlainTextEdit()
        self.connection_diff.setReadOnly(True)
        self.connection_diff.setFont(QFont("Consolas", 10))
        self.connection_diff.setPlaceholderText("点击「预览切换」查看配置差异；本工具不会发送网络请求。")
        self.connection_diff.setMinimumHeight(145)
        layout.addWidget(self.connection_diff, 1)
        self.connection_message = label("选择连接方式后，预览本次切换。", "subtle", True)
        layout.addWidget(self.connection_message)
        self.connection_progress = QProgressBar()
        self.connection_progress.setTextVisible(False)
        self.connection_progress.setValue(0)
        layout.addWidget(self.connection_progress)
        actions = QHBoxLayout()
        self.connection_export = QPushButton("导出检测报告")
        self.connection_export.clicked.connect(lambda: self.export_report(connection=True))
        self.connection_preview = QPushButton("预览切换")
        self.connection_preview.clicked.connect(self.scan_connection)
        self.connection_apply = QPushButton("备份并切换")
        self.connection_apply.setObjectName("primary")
        self.connection_apply.clicked.connect(self.apply_connection)
        actions.addWidget(self.connection_export)
        actions.addStretch()
        actions.addWidget(self.connection_preview)
        actions.addWidget(self.connection_apply)
        layout.addLayout(actions)
        self.direct_button.toggled.connect(self.connection_mode_changed)
        self.connection_mode_changed()

    def connection_home_changed(self, *_):
        self.connection_loaded_home = None
        if hasattr(self, "relay_key"):
            self.relay_key.clear()
            self.invalidate_connection()

    def load_connection(self):
        if self.busy:
            return
        self.invalidate_connection()
        self.relay_key.clear()
        self.show_key.setChecked(False)
        self.relay_url.clear()
        self.connection_model.clear()
        self.connection_home.setText("数据目录：" + self.home_edit.text() + "（可在检查与修复页更改）")
        self.connection_loaded_home = self.home_edit.text()
        try:
            data = connection_summary(Path(self.home_edit.text()).expanduser())
        except (RepairError, OSError, ValueError, TypeError, AttributeError):
            self.connection_current.setText("当前配置无法读取")
            self.connection_message.setText("请先在检查与修复页选择包含 config.toml 的 Codex 数据目录。")
            return
        mode = "OpenAI 直连" if data["provider"] == "openai" else "第三方中转" if data["provider"] == RELAY_PROVIDER else "自定义连接 · " + data["provider"]
        if data["provider"] == "openai" and data["has_overrides"]:
            mode = "OpenAI（存在地址覆盖）"
        self.connection_current.setText(f"当前：{mode}   /   模型：{data['model']}")
        self.relay_url.setText(data["base_url"])
        self.relay_key.setPlaceholderText("已有密钥；地址不变时留空沿用，填写可更新" if data["has_key"] else "填写第三方服务商提供的密钥")
        (self.relay_button if data["provider"] == RELAY_PROVIDER else self.direct_button).setChecked(True)
        self.connection_message.setText("已读取当前配置。选择目标连接方式后，点击「预览切换」。")

    def connection_mode_changed(self, *_):
        relay = self.relay_button.isChecked()
        self.connection_hint.setText("中转接口需支持 Responses API；基础地址通常以 /v1 结尾。更换地址时请重新输入对应密钥。" if relay else "直连使用官方默认地址和现有 OpenAI 登录。首次使用时，请在 Codex 中完成官方登录。")
        if not relay:
            self.show_key.setChecked(False)
        self.invalidate_connection()

    def invalidate_connection(self, *_):
        self.connection_plan = None
        if hasattr(self, "connection_message"):
            self.connection_message.setStyleSheet("")
            self.connection_message.setText("连接选项已更新，请预览本次切换。")
            self.connection_diff.clear()
            self.update_availability()

    def scan_connection(self):
        if self.busy:
            return
        home = Path(self.home_edit.text().strip()).expanduser()
        connection = ConnectionSettings("relay" if self.relay_button.isChecked() else "direct",
                                        self.relay_url.text(), self.relay_key.text(), self.connection_model.text())
        options = Options(False, False, self.connection_history.isChecked(), connection)
        self.connection_plan = None
        self.launch(lambda progress: analyze(home, None, options, progress), self.show_connection_plan)

    def show_connection_plan(self, plan):
        self.connection_plan = plan
        target = "OpenAI 直连" if plan.provider == "openai" else "第三方中转"
        config_changed = any(change.kind == "config" for change in plan.changes)
        self.connection_diff.setPlainText(plan.config_diff or ("密钥等敏感配置有更新，具体值已隐藏。" if config_changed else "配置内容无需修改。"))
        history = f"同步 {len(plan.rows)} 条会话索引、{sum(c.kind == 'rollout' for c in plan.changes)} 个历史文件" if plan.options.repair_history else "本次仅切换配置，未同步历史记录"
        next_step = "退出 Codex 后可备份并切换。" if self.probe() else "点击「备份并切换」应用。"
        self.connection_message.setText(f"目标：{target} · {plan.model}；{history}。" + (next_step if plan.needed else "已经一致，无需切换。"))

    def apply_connection(self):
        if self.connection_plan is None or self.busy:
            return
        plan = self.connection_plan
        self.launch(lambda progress: execute(plan, progress, self.probe), self.connection_done)

    def connection_done(self, backup):
        self.last_backup = backup
        self.invalidate()
        self.invalidate_connection()
        self.relay_key.clear()
        self.connection_loaded_home = None
        self.content_home_changed()
        self.connection_message.setText("切换完成，校验通过。重新打开 Codex 后生效；备份已保存，可在备份页恢复。")
        self.log.appendPlainText("连接切换备份：" + str(backup))
        self.refresh_backups()

    def _make_content_page(self):
        layout = self.page()
        self.heading(layout, "CONFIG CONTENT  /  本地检查与编辑", "检查内容，修复配置。",
                     "自动修复明确的格式错误，或手动编辑配置；预览后先备份，再保存。")
        frame = card()
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(18, 14, 18, 14)
        self.content_home = label("", "subtle", True)
        inner.addWidget(self.content_home)
        controls = QHBoxLayout()
        self.content_show = CheckBox("显示并编辑原文（可能包含密钥）")
        self.content_show.toggled.connect(self.render_content)
        controls.addWidget(self.content_show)
        controls.addStretch()
        self.content_import = QPushButton("导入配置内容")
        self.content_import.clicked.connect(self.import_content)
        self.content_reload = QPushButton("重新读取")
        self.content_reload.clicked.connect(self.load_content)
        controls.addWidget(self.content_import)
        controls.addWidget(self.content_reload)
        inner.addLayout(controls)
        inner.addWidget(label("默认仅显示脱敏内容。勾选后可编辑原文；导入和自动修复只更新编辑区，点击保存才写入文件。", "subtle", True))
        layout.addWidget(frame)
        self.content_tabs = QTabWidget()
        self.content_editor = QPlainTextEdit()
        self.content_editor.setFont(QFont("Consolas", 10))
        self.content_editor.setAccessibleName("配置内容编辑区")
        self.content_editor.setPlaceholderText("选择包含 config.toml 的目录，点击「重新读取」。损坏的配置也可以打开。")
        self.content_editor.setMinimumHeight(280)
        self.content_editor.setReadOnly(True)
        self.content_editor.textChanged.connect(self.content_edited)
        self.content_issues = QTreeWidget()
        self.content_issues.setHeaderLabels(["级别", "位置", "检查结果"])
        self.content_issues.setRootIsDecorated(False)
        self.content_issues.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.content_issues.itemDoubleClicked.connect(self.goto_content_issue)
        self.content_diff = QPlainTextEdit()
        self.content_diff.setReadOnly(True)
        self.content_diff.setFont(QFont("Consolas", 10))
        self.content_diff.setPlaceholderText("检查通过后显示脱敏差异。")
        self.content_tabs.addTab(self.content_editor, "配置内容")
        self.content_tabs.addTab(self.content_issues, "检查结果")
        self.content_tabs.addTab(self.content_diff, "脱敏差异")
        layout.addWidget(self.content_tabs, 1)
        self.content_message = label("读取配置后，可以检查内容或编辑。", "subtle", True)
        layout.addWidget(self.content_message)
        self.content_progress = QProgressBar()
        self.content_progress.setTextVisible(False)
        self.content_progress.setValue(0)
        layout.addWidget(self.content_progress)
        actions = QHBoxLayout()
        self.content_export = QPushButton("导出检测报告")
        self.content_export.clicked.connect(self.export_content_report)
        self.content_scan = QPushButton("检查并预览")
        self.content_scan.clicked.connect(lambda: self.scan_content(False))
        self.content_auto = QPushButton("自动修复并预览")
        self.content_auto.clicked.connect(lambda: self.scan_content(True))
        self.content_apply = QPushButton("备份并保存")
        self.content_apply.setObjectName("primary")
        self.content_apply.clicked.connect(self.apply_content)
        actions.addWidget(self.content_export)
        actions.addStretch()
        actions.addWidget(self.content_scan)
        actions.addWidget(self.content_auto)
        actions.addWidget(self.content_apply)
        layout.addLayout(actions)

    def content_home_changed(self, *_):
        self.content_loaded_home = None
        self.content_original_hash = None
        self._content_text = ""
        if hasattr(self, "content_show"):
            self.content_show.setChecked(False)
            self.render_content()
            self.invalidate_content()

    def load_content(self):
        if self.busy:
            return
        self.content_home_changed()
        self.content_home.setText("配置文件：" + str(Path(self.home_edit.text()).expanduser() / "config.toml") + "（目录可在检查与修复页更改）")
        self.content_loaded_home = self.home_edit.text()
        try:
            raw, text = read_config_content(Path(self.home_edit.text()).expanduser() / "config.toml")
        except (RepairError, OSError, ValueError):
            self.content_message.setText("读取失败，请选择含 config.toml 的目录。支持 UTF-8 / UTF-16，文件不能超过 2 MB。")
            self.update_availability()
            return
        self.content_original_hash = sha(raw)
        self._content_text = text
        self.render_content()
        self.content_message.setText("已读取原文件。可以直接检查，或勾选显示原文后编辑；保存前会自动备份。")
        self.update_availability()

    def render_content(self, *_):
        self._content_syncing = True
        self.content_editor.setReadOnly(not self.content_show.isChecked())
        self.content_editor.setPlainText(self._content_text if self.content_show.isChecked() else
                                         redact_toml(self._content_text) if self._content_text else "")
        self._content_syncing = False

    def content_edited(self):
        if self._content_syncing or not self.content_show.isChecked():
            return
        self._content_text = self.content_editor.toPlainText()
        self.invalidate_content()

    def invalidate_content(self):
        self.content_plan = None
        self.content_check = None
        if hasattr(self, "content_message"):
            self.content_message.setStyleSheet("")
            self.content_message.setText("内容已更新，请检查并预览后保存。")
            self.content_diff.clear()
            self.content_issues.clear()
            self.update_availability()

    def import_content(self):
        if self.busy or self.content_original_hash is None:
            return
        selected, _ = QFileDialog.getOpenFileName(self, "导入配置内容（不会立即保存）", self.home_edit.text(),
                                                 "配置文件 (*.toml *.toml-bf *.bak);;所有文件 (*)")
        if not selected:
            return
        try:
            _, text = read_config_content(Path(selected))
        except (RepairError, OSError, ValueError):
            self.content_message.setText("导入失败。请选择 2 MB 以内的 UTF-8 / UTF-16 配置文件。")
            return
        self._content_text = text
        self.render_content()
        self.invalidate_content()
        self.content_message.setText("已导入编辑区，请检查并预览。目标仍是所选目录的 config.toml。")

    def scan_content(self, auto_fix=False):
        if self.busy or self.content_original_hash is None:
            return
        home = Path(self.home_edit.text().strip()).expanduser()
        text, original_hash = self._content_text, self.content_original_hash
        self.content_plan = None
        self.content_check = None
        self.content_diff.clear()

        def job(progress):
            progress("正在检查配置内容…")
            result = check_content(text, auto_fix)
            plan = analyze_content(home, result.text, original_hash, progress) if result.valid else None
            return result, plan

        self.launch(job, self.show_content_plan)

    def show_content_plan(self, result):
        check, plan = result
        self.content_check, self.content_plan = check, plan
        self._content_text = check.text
        self.render_content()
        self.content_issues.clear()
        names = {"error": "需修正", "warning": "提示", "info": "说明"}
        for issue in check.issues:
            item = QTreeWidgetItem([names.get(issue.severity, "说明"), f"第 {issue.line} 行" if issue.line else "—", issue.message])
            item.setData(0, Qt.ItemDataRole.UserRole, issue.line)
            item.setToolTip(2, issue.message)
            self.content_issues.addTopLevelItem(item)
        for fix in check.fixes:
            self.content_issues.addTopLevelItem(QTreeWidgetItem(["已修复", "—", fix]))
        if plan:
            for note in plan.notes:
                self.content_issues.addTopLevelItem(QTreeWidgetItem(["说明", "—", note]))
            self.content_diff.setPlainText(plan.config_diff or ("敏感配置有更新，具体值已隐藏。" if plan.needed else "配置内容无需修改。"))
            action = "退出 Codex 后可备份并保存。" if self.probe() else "点击「备份并保存」应用。"
            prefix = f"已自动修复 {len(check.fixes)} 类问题；" if check.fixes else ""
            self.content_message.setText(prefix + "检查通过。" + (action if plan.needed else "内容未改变，无需保存。"))
        else:
            errors = sum(issue.severity == "error" for issue in check.issues)
            self.content_message.setText(f"发现 {errors} 个需修正的问题，请在编辑区修改后重新检查。原文件尚未改变。")
        self.content_tabs.setCurrentIndex(1)

    def goto_content_issue(self, item, column):
        line = item.data(0, Qt.ItemDataRole.UserRole)
        if not line or not self.content_show.isChecked():
            return
        block = self.content_editor.document().findBlockByNumber(line - 1)
        if block.isValid():
            cursor = self.content_editor.textCursor()
            cursor.setPosition(block.position())
            self.content_editor.setTextCursor(cursor)
            self.content_tabs.setCurrentIndex(0)
            self.content_editor.setFocus()

    def apply_content(self):
        if self.content_plan is None or self.busy:
            return
        plan = self.content_plan
        self.launch(lambda progress: execute(plan, progress, self.probe), self.content_done)

    def content_done(self, backup):
        self.last_backup = backup
        self.invalidate()
        self.connection_home_changed()
        self.content_home_changed()
        self.content_message.setText("配置已保存，校验通过。原文件已自动备份，可在备份页完整恢复。重新打开 Codex 后生效。")
        self.log.appendPlainText("配置内容修复备份：" + str(backup))
        self.refresh_backups()

    def export_content_report(self):
        if self.content_check is None or self.busy:
            return
        report = self.content_plan.report() if self.content_plan else {"operation": "content_repair", "valid": False}
        report["content_check"] = {
            "valid": self.content_check.valid,
            "issues": [{"severity": i.severity, "message": i.message, "line": i.line, "fixable": i.fixable}
                       for i in self.content_check.issues],
            "fixes": self.content_check.fixes,
        }
        path, _ = QFileDialog.getSaveFileName(self, "保存脱敏检测报告", "codex-content-report.json", "JSON 文件 (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.content_message.setText("已导出脱敏检测报告。")
            except OSError:
                self.on_error("报告无法保存，请选择可写入的位置。")

    def _make_backups_page(self):
        layout = self.page()
        self.heading(layout, "RECOVERY CENTER", "每次修改，都有备份。", "历史修复、连接切换和配置内容保存的备份均保存在所选 Codex 目录的 repair-backups 文件夹。连续操作请从新到旧逐次恢复。")
        top = QHBoxLayout()
        top.addStretch()
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh_backups)
        open_folder = QPushButton("打开备份目录")
        open_folder.clicked.connect(self.open_backup_root)
        top.addWidget(refresh)
        top.addWidget(open_folder)
        layout.addLayout(top)
        self.backup_table = QTreeWidget()
        self.backup_table.setHeaderLabels(["备份时间", "状态", "文件数", "会话数"])
        self.backup_table.setRootIsDecorated(False)
        self.backup_table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.backup_table.itemSelectionChanged.connect(self.update_availability)
        layout.addWidget(self.backup_table, 1)
        layout.addWidget(label("恢复只回滚该次修复。若相关文件已有新对话或其他修改，工具会停止恢复，保留新内容供手动合并。", "subtle", True))
        self.restore_message = label("选择一份备份后可恢复。", "subtle", True)
        layout.addWidget(self.restore_message)
        actions = QHBoxLayout()
        self.open_selected_button = QPushButton("打开所选备份")
        self.open_selected_button.clicked.connect(self.open_selected_backup)
        self.restore_button = QPushButton("恢复所选备份")
        self.restore_button.clicked.connect(self.restore_backup)
        actions.addWidget(self.open_selected_button)
        actions.addStretch()
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)

    def _make_help_page(self):
        layout = self.page()
        self.heading(layout, "A QUICK START", "修复与切换，从这里开始。", "用于已有 Codex 安装的配置合并、连接切换与本地会话兼容修复。")
        for number, title, text in [
            ("01", "选择并扫描", "默认识别当前用户的 .codex 目录和 config.toml-bf。没有旧配置时，也可以单独修复历史记录。点击「扫描并预览」查看具体变更。"),
            ("02", "退出 Codex 后修复", "保存正在进行的工作，关闭 Codex 桌面程序和命令行。返回本工具点击「备份并修复」；完成后重新打开 Codex。"),
            ("03", "按需恢复", "在「备份与恢复」中选择该次备份。工具先校验文件，再回滚受影响字段，保留无关的新会话。若有冲突则停止自动恢复。"),
            ("04", "切换直连与中转", "进入「连接切换」，选 OpenAI 直连或填写中转接口地址、密钥和可选模型名称。预览后退出 Codex，点击「备份并切换」。中转需支持 Responses API。切回直连沿用已有官方登录，保存的中转密钥可供下次切换使用。"),
            ("05", "修复配置内容", "进入「配置内容修复」，读取或导入配置。勾选显示原文后可手动编辑；点击检查或自动修复查看结果和脱敏差异。检查通过后退出 Codex，备份并保存。无法明确判断的问题需手动修改；更换提供方后可在检查与修复页兼容历史记录。"),
        ]:
            frame = card()
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(20, 18, 20, 18)
            inner.addWidget(label(number + "  /  " + title, "cardTitle"))
            inner.addWidget(label(text, "subtle", True))
            layout.addWidget(frame)
        detail = card()
        inner = QVBoxLayout(detail)
        inner.setContentsMargins(20, 18, 20, 18)
        inner.addWidget(label("处理范围", "cardTitle"))
        inner.addWidget(label("支持标准本地 config.toml、JSONL 会话以及已识别结构的分页 SQLite 历史。当前连接和模型优先；不自动恢复旧网关、内置运行路径和旧浏览器校验值。", "subtle", True))
        inner.addWidget(label("采用独立 profile、外置 sqlite_home、缺失文件或未知数据库结构时，会停止相关操作。不会调用模型、重新登录或上传配置；实际续聊需在 Codex 内验证。", "subtle", True))
        inner.addWidget(label("原始备份可能包含配置中的凭据，请仅在本人电脑保管。对外分享可使用已脱敏的检测报告。", "subtle", True))
        layout.addWidget(detail)
        layout.addStretch()

    def change_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav):
            button.setChecked(i == index)
        if index == 1:
            if self.connection_loaded_home != self.home_edit.text() and not self.busy:
                self.load_connection()
        if index == 2:
            if self.content_loaded_home != self.home_edit.text() and not self.busy:
                self.load_content()
        if index == 3:
            self.refresh_backups()

    def invalidate(self, *_):
        self.plan = None
        if hasattr(self, "message"):
            self.message.setText("路径或选项已更新，请重新扫描。")
            self.diff.clear()
            self.preview.clear()
            self.preview.addTopLevelItem(QTreeWidgetItem(["等待重新扫描", "当前预览已失效。 "]))
            for metric in self.metrics:
                metric.setText("—")
            self.update_availability()

    def select_home(self):
        selected = QFileDialog.getExistingDirectory(self, "选择 Codex 数据目录", self.home_edit.text())
        if selected:
            self.home_edit.setText(selected)
            candidate = Path(selected) / "config.toml-bf"
            self.source_edit.setText(str(candidate) if candidate.exists() else "")

    def select_source(self):
        selected, _ = QFileDialog.getOpenFileName(self, "选择旧配置文件", self.home_edit.text(), "配置文件 (*.toml *.toml-bf *.bak);;所有文件 (*)")
        if selected:
            self.source_edit.setText(selected)

    def update_availability(self):
        if not hasattr(self, "restore_button"):
            return
        running = bool(self.probe())
        if self.badge.property('running') != running:
            self.badge.setProperty('running', running)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)
        self.badge.setText("Codex 运行中 · 可扫描" if running else "Codex 已退出 · 可修复")
        self.badge.setToolTip("写入和恢复前需要退出 Codex，避免与会话写入冲突。")
        self.scan_button.setEnabled(not self.busy)
        self.repair_button.setEnabled(not self.busy and not running and self.plan is not None and self.plan.needed)
        self.export_button.setEnabled(not self.busy and self.plan is not None)
        self.connection_preview.setEnabled(not self.busy)
        self.connection_reload.setEnabled(not self.busy)
        self.connection_apply.setEnabled(not self.busy and not running and self.connection_plan is not None and self.connection_plan.needed)
        self.connection_export.setEnabled(not self.busy and self.connection_plan is not None)
        has_content = self.content_original_hash is not None
        for widget in [self.content_scan, self.content_auto, self.content_import, self.content_show]:
            widget.setEnabled(not self.busy and has_content)
        self.content_reload.setEnabled(not self.busy)
        self.content_editor.setEnabled(not self.busy and has_content)
        self.content_apply.setEnabled(not self.busy and not running and self.content_plan is not None and self.content_plan.needed)
        self.content_export.setEnabled(not self.busy and self.content_check is not None)
        for widget in [self.direct_button, self.relay_button, self.connection_model, self.connection_history]:
            widget.setEnabled(not self.busy)
        for widget in [self.relay_url, self.relay_key, self.show_key]:
            widget.setEnabled(not self.busy and self.relay_button.isChecked())
        selected = self.backup_table.currentItem()
        state = selected.data(0, Qt.ItemDataRole.UserRole)["state"] if selected else None
        self.restore_button.setEnabled(not self.busy and not running and state in {"completed", "prepared", "applying", "recovery_required", "restoring"})
        self.open_selected_button.setEnabled(selected is not None)
        for widget in [self.home_edit, self.source_edit, self.browse_home, self.browse_source, self.merge_check, self.history_check, self.mcp_check]:
            widget.setEnabled(not self.busy)

    def launch(self, function, success):
        self.busy = True
        self.message.setStyleSheet("")
        self.connection_message.setStyleSheet("")
        self.content_message.setStyleSheet("")
        self.progress_bar.setRange(0, 0)
        self.connection_progress.setRange(0, 0)
        self.content_progress.setRange(0, 0)
        self.update_availability()
        self.worker = Worker(function, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.succeeded.connect(success)
        self.worker.failed.connect(self.on_error)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def on_progress(self, text):
        self.message.setText(text)
        self.restore_message.setText(text)
        self.connection_message.setText(text)
        self.content_message.setText(text)
        self.log.appendPlainText(text)

    def on_error(self, text):
        self.message.setStyleSheet("color: #A43D39;")
        self.message.setText(text)
        self.restore_message.setText(text)
        self.connection_message.setStyleSheet("color: #A43D39;")
        self.connection_message.setText(text)
        self.content_message.setStyleSheet("color: #A43D39;")
        self.content_message.setText(text)
        self.log.appendPlainText("未完成：" + text)
        self.refresh_backups()

    def finished(self):
        self.busy = False
        if self.worker:
            self.worker.function = None
            self.worker.deleteLater()
            self.worker = None
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.connection_progress.setRange(0, 100)
        self.connection_progress.setValue(100)
        self.content_progress.setRange(0, 100)
        self.content_progress.setValue(100)
        if self.pages.currentIndex() == 1 and self.connection_loaded_home is None:
            completed_text = self.connection_message.text()
            self.load_connection()
            self.connection_message.setText(completed_text)
        if self.pages.currentIndex() == 2 and self.content_loaded_home is None:
            completed_text = self.content_message.text()
            self.load_content()
            self.content_message.setText(completed_text)
        self.update_availability()

    def scan(self):
        if self.busy:
            return
        home = Path(self.home_edit.text().strip()).expanduser()
        source = Path(self.source_edit.text().strip()).expanduser() if self.source_edit.text().strip() else None
        options = Options(self.merge_check.isChecked(), self.mcp_check.isChecked(), self.history_check.isChecked())
        self.plan = None
        self.log.clear()
        self.launch(lambda progress: analyze(home, source, options, progress), self.show_plan)

    def show_plan(self, plan: Plan):
        self.plan = plan
        self.metrics[0].setText(plan.provider)
        self.metrics[1].setText(str(len(plan.rows)))
        self.metrics[2].setText(str(plan.projects_added))
        self.preview.clear()
        rows = [("登录方式 / 模型", f"保留 {plan.provider} · {plan.model}"),
                ("历史兼容", f"{len(plan.rows)} 条会话索引，{sum(c.kind == 'rollout' for c in plan.changes)} 个历史文件"),
                ("项目与工具", f"补充 {plan.projects_added} 个项目、{plan.mcp_added} 个自定义 MCP 工具"),
                ("备份方式", "先备份原文件及数据库，再写入；会话正文按 SHA-256 校验")]
        if plan.provider_counts:
            rows.insert(2, ("旧会话提供方", "、".join(f"{name} × {count}" for name, count in plan.provider_counts.items()) + f" → {plan.provider}"))
        rows.extend(("处理说明", note) for note in plan.notes)
        for title, result in rows:
            item = QTreeWidgetItem([title, result])
            item.setToolTip(1, result)
            self.preview.addTopLevelItem(item)
        self.diff.setPlainText(plan.config_diff or "当前配置无需修改。")
        self.tabs.setCurrentIndex(0)
        if not plan.needed:
            self.message.setText("扫描完成：当前配置与历史标记已经兼容，无需修复。")
        elif self.probe():
            self.message.setText("预览已就绪。保存工作并退出 Codex 后，「备份并修复」会自动可用。")
        else:
            self.message.setText("预览已就绪。点击「备份并修复」应用以上变更。")

    def repair(self):
        if self.plan is None or self.busy:
            return
        plan = self.plan
        self.tabs.setCurrentIndex(2)
        self.launch(lambda progress: execute(plan, progress, self.probe), self.repair_done)

    def repair_done(self, backup):
        self.last_backup = backup
        self.plan = None
        self.connection_home_changed()
        self.content_home_changed()
        self.message.setText("修复完成，校验通过。现在可以重新打开 Codex；备份已保存，可在备份页查看。")
        self.log.appendPlainText("备份位置：" + str(backup))
        self.refresh_backups()

    def refresh_backups(self):
        if not hasattr(self, "backup_table"):
            return
        self.backup_table.clear()
        try:
            backups = list_backups(Path(self.home_edit.text()).expanduser().resolve())
        except (OSError, RepairError):
            backups = []
        names = {"completed": "可恢复", "restored": "已恢复", "rolled_back": "失败已回滚", "cancelled": "未写入", "prepared": "待处理", "applying": "中断待恢复", "recovery_required": "需恢复", "restoring": "恢复中断"}
        for backup in backups:
            item = QTreeWidgetItem([backup["created_at"].replace("T", " "), names.get(backup["state"], backup["state"]), str(backup["files"]), str(backup["threads"])])
            item.setData(0, Qt.ItemDataRole.UserRole, backup)
            self.backup_table.addTopLevelItem(item)
        if backups:
            self.backup_table.setCurrentItem(self.backup_table.topLevelItem(0))
        self.update_availability()

    def selected_backup(self):
        item = self.backup_table.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole)["path"] if item else None

    def restore_backup(self):
        backup = self.selected_backup()
        if not backup or self.busy:
            return
        home = Path(self.home_edit.text()).expanduser()
        self.launch(lambda progress: restore(home, backup, progress, self.probe), self.restore_done)

    def restore_done(self, backup):
        self.plan = None
        self.connection_home_changed()
        self.content_home_changed()
        self.restore_message.setText("该次修复已恢复，校验通过。其他会话保持不变。")
        self.refresh_backups()

    def open_selected_backup(self):
        path = self.selected_backup()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_backup_root(self):
        path = Path(self.home_edit.text()).expanduser() / "repair-backups"
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.restore_message.setText("尚无本工具生成的备份。首次执行修复时会自动创建。")

    def export_report(self, checked=False, connection=False):
        plan = self.connection_plan if connection else self.plan
        if not plan:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存脱敏检测报告", "codex-repair-report.json", "JSON 文件 (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(plan.report(), ensure_ascii=False, indent=2), encoding="utf-8")
                self.message.setText("已导出脱敏检测报告。")
                self.connection_message.setText("已导出脱敏检测报告。")
            except OSError:
                self.on_error("报告无法保存，请选择可写入的位置。")

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "操作正在进行", "请等待当前操作完成后再关闭，避免中断文件写入。")
            event.ignore()
        else:
            event.accept()
