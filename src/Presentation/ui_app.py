from __future__ import annotations

import pathlib
import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.Application.platform_task_queue import PlatformTaskQueue
from src.Infrastructure.config_repository import (
    LEGAL_NOTICE,
    PROJECT_ADDRESS,
    PROJECT_NAME_EN,
    PROJECT_NAME_ZH,
    PROJECT_QQ,
    QQMUSIC_ATTRIBUTION,
    auto_find_kgg_db_path,
    auto_find_kugou_key,
    build_banner,
    load_config,
    save_config,
    save_default_config_if_missing,
    supported_transcode_formats,
)
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths

WINDOW_BG = "#101215"
SHELL_BG = "#171A1F"
CARD_BG = "#1E232B"
CARD_ALT = "#202630"
BORDER = "#2B313C"
TEXT = "#F3F6FA"
TEXT_MUTED = "#AAB5C5"
ACCENT = "#2D89EF"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

FORMATS = ["auto"] + [item for item in supported_transcode_formats()
                      if item != "auto"]
QQ_RULE_FORMATS = ["flac", "ogg", "m4a", "mp3", "wav"]


def build_app_stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: Microsoft YaHei UI; font-size: 13px; }}
    QFrame#Shell {{ background: {SHELL_BG}; border: 1px solid {BORDER}; border-radius: 18px; }}
    QFrame#TitleBar {{ background: transparent; border-bottom: 1px solid {BORDER}; }}
    QLabel#TitleLabel {{ font-size: 18px; font-weight: 700; }}
    QLabel#SubtitleLabel, QLabel#MutedText, QLabel#CardSubtitle, QLabel#HeroSubtitle {{ color: {TEXT_MUTED}; }}
    QLabel#HeroTitle {{ font-size: 24px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#CardTitle {{ font-size: 16px; font-weight: 700; }}
    QLabel#FieldLabel {{ color: {TEXT_MUTED}; font-size: 12px; }}
    QFrame#InfoCard, QFrame#ConfigCard, QFrame#PlatformCard, QFrame#NoticeCard {{ background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 16px; }}
    QFrame#PlatformCard:hover {{ background: #202735; border-color: #3E5678; }}
    QFrame#StatusBox {{ background: {CARD_ALT}; border: 1px solid {BORDER}; border-radius: 12px; }}
    QLineEdit, QComboBox, QPlainTextEdit {{ background: #11151B; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px; selection-background-color: #3B82F6; }}
    QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: #3E5678; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
    QPushButton {{ border-radius: 10px; padding: 8px 14px; border: 1px solid {BORDER}; background: #222834; }}
    QPushButton#PrimaryButton {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 700; }}
    QPushButton#SecondaryButton {{ background: #243042; border-color: #314055; }}
    QPushButton#GhostButton {{ background: transparent; }}
    QPushButton#DangerButton {{ background: #3B1D22; border-color: #5B2830; }}
    QPushButton:hover {{ border-color: {ACCENT}; background: #273042; }}
    QPushButton#PrimaryButton:hover {{ background: #4A9DF1; border-color: #4A9DF1; }}
    QPushButton#SecondaryButton:hover {{ background: #2B3850; border-color: #476081; }}
    QPushButton#GhostButton:hover {{ background: #1A1F28; }}
    QPushButton#DangerButton:hover {{ background: #51242B; border-color: #7A343F; }}
    QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; background: #1B2230; }}
    QPushButton#PrimaryButton:pressed {{ background: #226EBD; }}
    QPushButton#SecondaryButton:pressed {{ background: #1F2938; }}
    QPushButton#DangerButton:pressed {{ background: #3A181E; }}
    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: #11151B; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QPlainTextEdit#LogView {{ background: #0D1015; border-radius: 12px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 2px; }}
    QScrollBar::handle:vertical {{ background: #394557; min-height: 28px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: #4F6483; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: #394557; min-width: 28px; border-radius: 5px; }}
    QScrollBar::handle:horizontal:hover {{ background: #4F6483; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """


class UiBridge(QObject):
    states_changed = Signal(object)
    log_line = Signal(str)
    collision_request = Signal(object)
    runtime_prompt_request = Signal(object)
    submission_result = Signal(object)


class TitleBar(QFrame):
    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None
        self.setObjectName("TitleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 10)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("TitleLabel")
        subtitle = QLabel("PySide6 UI | Win10/11 风格")
        subtitle.setObjectName("SubtitleLabel")
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        text_box.addWidget(title_label)
        text_box.addWidget(subtitle)
        layout.addLayout(text_box)
        layout.addStretch(1)

        self.min_button = QPushButton("最小化")
        self.min_button.setObjectName("GhostButton")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("DangerButton")
        self.min_button.clicked.connect(parent.showMinimized)
        self.close_button.clicked.connect(parent.close)
        layout.addWidget(self.min_button)
        layout.addWidget(self.close_button)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - \
                self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class StartupNoticeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_origin: QPoint | None = None
        self.setWindowTitle("免费软件提示")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(520, 280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("Shell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        outer.addWidget(shell)

        title_bar = TitleBar(self, "免费软件提示")
        title_bar.min_button.hide()
        shell_layout.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("NoticeCard")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 20, 22, 20)
        body_layout.setSpacing(12)

        title = QLabel("本软件为免费软件")
        title.setObjectName("CardTitle")
        message = QLabel(
            "如果你是付费获取的，请立即退款。\n\n"
            "本项目仅供学习交流使用，禁止商用，禁止倒卖。\n"
            "如发现倒卖或商用行为，将举报平台并持续追责。"
        )
        message.setWordWrap(True)
        message.setObjectName("MutedText")

        confirm = QPushButton("我知道了")
        confirm.setObjectName("PrimaryButton")
        confirm.clicked.connect(self.accept)

        body_layout.addWidget(title)
        body_layout.addWidget(message)
        body_layout.addStretch(1)
        body_layout.addWidget(confirm)
        shell_layout.addWidget(body)

        self.setStyleSheet(build_app_stylesheet())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - \
                self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)


class PathField(QFrame):
    def __init__(self, label: str, *, directory: bool) -> None:
        super().__init__()
        self.directory = directory
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.label = QLabel(label)
        self.label.setObjectName("FieldLabel")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.button = QPushButton("选择")
        self.button.setObjectName("SecondaryButton")
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        layout.addWidget(self.label)
        layout.addLayout(row)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value)


class PlatformCard(QFrame):
    run_requested = Signal(str)
    stop_requested = Signal(str)

    def __init__(self, platform_id: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.platform_id = platform_id
        self.setObjectName("PlatformCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._format_widgets: dict[str, QComboBox] = {}
        self._extra_fields: dict[str, PathField] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("CardSubtitle")
        subtitle_label.setWordWrap(True)
        header.addWidget(title_label)
        header.addWidget(subtitle_label)
        root.addLayout(header)

        self.input_field = PathField("输入目录或文件", directory=True)
        root.addWidget(self.input_field)

        self.form_layout = QGridLayout()
        self.form_layout.setHorizontalSpacing(12)
        self.form_layout.setVerticalSpacing(12)
        root.addLayout(self.form_layout)

        status_box = QFrame()
        status_box.setObjectName("StatusBox")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(6)
        self.status_label = QLabel("状态：空闲")
        self.message_label = QLabel("等待任务")
        self.count_label = QLabel("统计：成功 0，跳过 0，失败 0")
        self.progress_label = QLabel("进度：0 / 0")
        self.file_label = QLabel("当前文件：无")
        self.timing_label = QLabel("热点：无")
        for widget in (self.status_label, self.message_label, self.count_label, self.progress_label, self.file_label, self.timing_label):
            widget.setWordWrap(True)
            status_layout.addWidget(widget)
        root.addWidget(status_box)

        self.continuous_checkbox = QCheckBox("持续解密（循环扫描新文件）")
        root.addWidget(self.continuous_checkbox)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)
        self.run_button = QPushButton("开始该平台任务")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(
            lambda: self.run_requested.emit(self.platform_id))
        self.stop_button = QPushButton("停止当前任务")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(
            lambda: self.stop_requested.emit(self.platform_id))
        button_row.addWidget(self.run_button, 1)
        button_row.addWidget(self.stop_button, 1)
        root.addLayout(button_row)

    def add_format_combo(self, key: str, label: str, values: list[str]) -> None:
        combo = QComboBox()
        combo.addItems(values)
        combo.setObjectName("ComboBox")
        row = self.form_layout.rowCount()
        self.form_layout.addWidget(QLabel(label), row, 0)
        self.form_layout.addWidget(combo, row, 1)
        self._format_widgets[key] = combo

    def add_extra_field(self, key: str, label: str, *, directory: bool) -> PathField:
        field = PathField(label, directory=directory)
        row = self.form_layout.rowCount()
        self.form_layout.addWidget(field, row, 0, 1, 2)
        self._extra_fields[key] = field
        return field

    def set_format_value(self, key: str, value: str) -> None:
        combo = self._format_widgets[key]
        value = value if value in [combo.itemText(
            i) for i in range(combo.count())] else combo.itemText(0)
        combo.setCurrentText(value)

    def format_value(self, key: str) -> str:
        return self._format_widgets[key].currentText().strip()

    def extra_field(self, key: str) -> PathField:
        return self._extra_fields[key]

    def apply_state(self, payload: dict[str, Any]) -> None:
        status = str(payload.get("status", "idle") or "idle")
        mapping = {
            "idle": "空闲",
            "queued": "排队中",
            "running": "运行中",
            "waiting": "等待下一轮",
            "stopping": "停止中",
            "stopped": "已停止",
            "success": "已完成",
            "skipped": "已跳过",
            "failed": "失败",
        }
        self.status_label.setText(f"状态：{mapping.get(status, status)}")
        self.message_label.setText(f"说明：{payload.get('message', '无')}")
        self.count_label.setText(
            "统计：成功 {success}，恢复 {recovered}，跳过 {skipped}，失败 {failed}".format(
                success=int(payload.get("success_count", 0) or 0),
                recovered=int(payload.get("recovered_count", 0) or 0),
                skipped=int(payload.get("skipped_count", 0) or 0),
                failed=int(payload.get("failed_count", 0) or 0),
            )
        )
        self.progress_label.setText(
            f"进度：{payload.get('current_index', 0)} / {payload.get('current_total', 0)}")
        current_file = pathlib.Path(
            str(payload.get("current_file", "") or "")).name or "无"
        self.file_label.setText(f"当前文件：{current_file}")
        hotspot = payload.get("timing_hotspot") or {}
        hotspot_text = f"{hotspot.get('stage', '无')} / {hotspot.get('ratio', 0)}" if hotspot else "无"
        self.timing_label.setText(f"热点：{hotspot_text}")
        active = status in {"queued", "running", "waiting", "stopping"}
        self.run_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.continuous_checkbox.setEnabled(not active)


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.paths = RuntimePaths.discover()
        self.paths.ensure_runtime_dirs()
        save_default_config_if_missing(self.paths)
        self.root_config, self.config = load_config(self.paths)
        self.bridge = UiBridge()
        self._collision_waiter: tuple[threading.Event,
                                      dict[str, str], str, str, str | None] | None = None
        self._task_queue = PlatformTaskQueue(
            task_starter=self._start_task_thread,
            state_sink=lambda states: self.bridge.states_changed.emit(states),
            log_sink=lambda line: self.bridge.log_line.emit(line),
            collision_resolver=self._resolve_collision,
            max_running=2,
        )
        self._submission_inflight: set[str] = set()
        self._drag_origin: QPoint | None = None
        self._cards: dict[str, PlatformCard] = {}
        self._build_ui()
        self._connect_signals()
        self._load_config_into_widgets()
        self._append_log("界面初始化完成。")

    def _build_ui(self) -> None:
        self.setWindowTitle(PROJECT_NAME_EN)
        icon_path = self.paths.root_dir / "封面" / "封面.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(900, 620)
        self.resize(1040, 680)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("Shell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        outer.addWidget(shell)

        shell_layout.addWidget(
            TitleBar(self, f"{PROJECT_NAME_EN} | {PROJECT_NAME_ZH}"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        shell_layout.addWidget(scroll, 1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 18, 20, 18)
        body_layout.setSpacing(16)
        scroll.setWidget(body)

        info_card = QFrame()
        info_card.setObjectName("InfoCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(8)
        title = QLabel("统一解密工作台")
        title.setObjectName("HeroTitle")
        desc = QLabel(
            "支持 QQ 音乐、酷我音乐、酷狗音乐。QQ 和酷我需要软件保持运行，酷狗为纯文件级离线解密，前提是必须安装酷狗音乐。")
        desc.setWordWrap(True)
        desc.setObjectName("HeroSubtitle")
        link = QLabel(f'<a href="{PROJECT_ADDRESS}">{PROJECT_ADDRESS}</a>')
        link.setOpenExternalLinks(True)
        legal = QLabel(
            f"QQ：{PROJECT_QQ}\n{QQMUSIC_ATTRIBUTION}\n{LEGAL_NOTICE}")
        legal.setWordWrap(True)
        legal.setObjectName("MutedText")
        info_layout.addWidget(title)
        info_layout.addWidget(desc)
        info_layout.addWidget(link)
        info_layout.addWidget(legal)
        body_layout.addWidget(info_card)

        shared_card = QFrame()
        shared_card.setObjectName("ConfigCard")
        shared_layout = QVBoxLayout(shared_card)
        shared_layout.setContentsMargins(18, 16, 18, 16)
        shared_layout.setSpacing(12)
        shared_title = QLabel("共享设置")
        shared_title.setObjectName("SectionTitle")
        shared_layout.addWidget(shared_title)
        self.output_field = PathField("共享输出目录", directory=True)
        self.recursive_checkbox = QCheckBox("递归扫描子目录")
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        self.save_button = QPushButton("保存配置")
        self.save_button.setObjectName("SecondaryButton")
        self.reload_button = QPushButton("重新读取配置")
        self.reload_button.setObjectName("GhostButton")
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.setObjectName("GhostButton")
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.reload_button)
        action_row.addWidget(self.open_output_button)
        action_row.addStretch(1)
        shared_layout.addWidget(self.output_field)
        shared_layout.addWidget(self.recursive_checkbox)
        shared_layout.addLayout(action_row)
        body_layout.addWidget(shared_card)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        body_layout.addLayout(cards_row)

        qq_card = PlatformCard("qq", "QQ音乐", "运行期解密，开始任务前会检查 QQ 音乐进程。")
        qq_card.add_format_combo("mflac", "mflac 输出格式", QQ_RULE_FORMATS)
        qq_card.add_format_combo("mgg", "mgg 输出格式", QQ_RULE_FORMATS)
        qq_card.add_format_combo("mmp4", "mmp4 输出格式", QQ_RULE_FORMATS)
        cards_row.addWidget(qq_card, 1)
        self._cards["qq"] = qq_card

        kuwo_card = PlatformCard(
            "kuwo", "酷我音乐", "运行期解密，开始任务前会检查 kwmusic.exe 进程。")
        kuwo_card.add_format_combo("format_kwm", "kwm 输出格式", FORMATS)
        kuwo_card.add_extra_field("exe_path", "酷我程序路径（可选）", directory=False)
        kuwo_card.add_extra_field("signature_file", "签名文件路径", directory=False)
        cards_row.addWidget(kuwo_card, 1)
        self._cards["kuwo"] = kuwo_card

        kugou_card = PlatformCard("kugou", "酷狗音乐", "文件级离线解密，不要求 KuGou 运行。")
        kugou_card.add_format_combo(
            "target_format_kgma", "kgma/kgm/vpr 输出格式", FORMATS)
        kugou_card.add_format_combo("target_format_kgg", "kgg 输出格式", FORMATS)
        kugou_card.add_extra_field(
            "key_file", "kugou_key.xz 路径", directory=False)
        kugou_card.add_extra_field(
            "kgg_db_path", "KGMusicV3.db 路径", directory=False)
        cards_row.addWidget(kugou_card, 1)
        self._cards["kugou"] = kugou_card

        right_card = QFrame()
        right_card.setObjectName("ConfigCard")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.setSpacing(12)
        queue_title = QLabel("队列与日志")
        queue_title.setObjectName("SectionTitle")
        self.queue_label = QLabel("最多同时运行 2 个平台任务，超出部分进入 FIFO 队列。")
        self.queue_label.setWordWrap(True)
        self.queue_label.setObjectName("MutedText")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogView")
        self.log_view.setMinimumHeight(180)
        self.log_view.setMaximumBlockCount(800)
        right_layout.addWidget(queue_title)
        right_layout.addWidget(self.queue_label)
        right_layout.addWidget(self.log_view, 1)
        body_layout.addWidget(right_card)

        self.setStyleSheet(build_app_stylesheet())

    def _connect_signals(self) -> None:
        self.output_field.button.clicked.connect(
            lambda: self._choose_path(self.output_field))
        self.save_button.clicked.connect(self._save_config_from_widgets)
        self.reload_button.clicked.connect(self._reload_config)
        self.open_output_button.clicked.connect(self._open_output_dir)
        for platform_id, card in self._cards.items():
            card.input_field.button.clicked.connect(
                lambda _=False, pid=platform_id: self._choose_path(self._cards[pid].input_field))
            card.run_requested.connect(self._handle_platform_action)
            card.stop_requested.connect(self._handle_platform_stop)
        self._cards["kuwo"].extra_field("exe_path").button.clicked.connect(lambda: self._choose_file(
            self._cards["kuwo"].extra_field("exe_path"), "选择酷我程序", "程序 (*.exe);;所有文件 (*.*)"))
        self._cards["kuwo"].extra_field("signature_file").button.clicked.connect(lambda: self._choose_file(
            self._cards["kuwo"].extra_field("signature_file"), "选择签名文件", "JSON (*.json);;所有文件 (*.*)"))
        self._cards["kugou"].extra_field("key_file").button.clicked.connect(lambda: self._choose_file(
            self._cards["kugou"].extra_field("key_file"), "选择 kugou_key.xz", "XZ 文件 (*.xz);;所有文件 (*.*)"))
        self._cards["kugou"].extra_field("kgg_db_path").button.clicked.connect(lambda: self._choose_file(
            self._cards["kugou"].extra_field("kgg_db_path"), "选择 KGMusicV3.db", "数据库 (*.db);;所有文件 (*.*)"))
        self.bridge.states_changed.connect(self._apply_states)
        self.bridge.log_line.connect(self._append_log)
        self.bridge.collision_request.connect(self._handle_collision_request)
        self.bridge.runtime_prompt_request.connect(
            self._handle_runtime_prompt_request)
        self.bridge.submission_result.connect(self._handle_submission_result)

    def _platform_title(self, platform_id: str) -> str:
        return {"qq": "QQ音乐", "kuwo": "酷我音乐", "kugou": "酷狗音乐"}[platform_id]

    def _load_config_into_widgets(self) -> None:
        self.root_config, self.config = load_config(self.paths)
        shared = self.config["shared"]
        self.output_field.setText(
            str(shared.get("output_dir", self.paths.output_dir)))
        self.recursive_checkbox.setChecked(bool(shared.get("recursive", True)))

        qq = self.config["qq"]
        self._cards["qq"].input_field.setText(str(qq.get("input_dir", "")))
        self._cards["qq"].set_format_value("mflac", str(
            (qq.get("format_rules") or {}).get("mflac", "flac")))
        self._cards["qq"].set_format_value("mgg", str(
            (qq.get("format_rules") or {}).get("mgg", "ogg")))
        self._cards["qq"].set_format_value("mmp4", str(
            (qq.get("format_rules") or {}).get("mmp4", "m4a")))

        kuwo = self.config["kuwo"]
        self._cards["kuwo"].input_field.setText(str(kuwo.get("input_dir", "")))
        self._cards["kuwo"].set_format_value(
            "format_kwm", str(kuwo.get("format_kwm", "auto")))
        self._cards["kuwo"].extra_field("exe_path").setText(
            str(kuwo.get("exe_path", "")))
        self._cards["kuwo"].extra_field("signature_file").setText(
            str(kuwo.get("signature_file", "")))

        kugou = self.config["kugou"]
        self._cards["kugou"].input_field.setText(
            str(kugou.get("input_dir", "")))
        self._cards["kugou"].set_format_value(
            "target_format_kgma", str(kugou.get("target_format_kgma", "auto")))
        self._cards["kugou"].set_format_value(
            "target_format_kgg", str(kugou.get("target_format_kgg", "auto")))
        self._cards["kugou"].extra_field("key_file").setText(
            str(kugou.get("key_file", "")))
        self._cards["kugou"].extra_field("kgg_db_path").setText(
            str(kugou.get("kgg_db_path", "")))

    def _save_config_from_widgets(self, *, announce: bool = True) -> None:
        shared = {
            "output_dir": self.output_field.text() or str(self.paths.output_dir),
            "cli_collision_policy": "suffix",
            "recursive": self.recursive_checkbox.isChecked(),
        }
        qq = {
            "input_dir": self._cards["qq"].input_field.text(),
            "process_match": "qqmusic",
            "format_rules": {
                "mflac": self._cards["qq"].format_value("mflac"),
                "mgg": self._cards["qq"].format_value("mgg"),
                "mmp4": self._cards["qq"].format_value("mmp4"),
            },
        }
        kuwo = {
            "input_dir": self._cards["kuwo"].input_field.text(),
            "process_name": "kwmusic.exe",
            "exe_path": self._cards["kuwo"].extra_field("exe_path").text(),
            "signature_file": self._cards["kuwo"].extra_field("signature_file").text(),
            "format_kwm": self._cards["kuwo"].format_value("format_kwm"),
        }
        kugou = {
            "input_dir": self._cards["kugou"].input_field.text(),
            "kgg_db_path": self._cards["kugou"].extra_field("kgg_db_path").text(),
            "key_file": self._cards["kugou"].extra_field("key_file").text(),
            "target_format_kgma": self._cards["kugou"].format_value("target_format_kgma"),
            "target_format_kgg": self._cards["kugou"].format_value("target_format_kgg"),
        }
        self.config = {"shared": shared, "qq": qq,
                       "kuwo": kuwo, "kugou": kugou}
        save_config(self.paths, self.root_config, self.config)
        if announce:
            self._append_log("配置已保存。")

    def _reload_config(self) -> None:
        self._load_config_into_widgets()
        self._append_log("已重新读取配置文件。")

    def _open_output_dir(self) -> None:
        output_dir = pathlib.Path(
            self.output_field.text() or str(self.paths.output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(output_dir.as_uri())

    def _choose_path(self, field: PathField) -> None:
        start = field.text() or str(self.paths.root_dir)
        selected = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if selected:
            field.setText(selected)

    def _choose_file(self, field: PathField, title: str, filter_text: str) -> None:
        start = field.text() or str(self.paths.root_dir)
        selected, _ = QFileDialog.getOpenFileName(
            self, title, start, filter_text)
        if selected:
            field.setText(selected)

    def _handle_platform_action(self, platform_id: str) -> None:
        title = self._platform_title(platform_id)
        if platform_id in self._submission_inflight:
            self._append_log(f"[{title}] 正在准备任务，请稍候。")
            return
        self._save_config_from_widgets()
        input_path = pathlib.Path(self._cards[platform_id].input_field.text())
        output_dir = pathlib.Path(
            self.output_field.text() or str(self.paths.output_dir))
        settings = dict(self.config[platform_id])
        recursive = self.recursive_checkbox.isChecked()
        continuous = self._cards[platform_id].continuous_checkbox.isChecked()
        if not input_path.exists():
            self._show_message("输入路径无效", f"{title} 的输入路径不存在。")
            return
        self._submission_inflight.add(platform_id)
        self._cards[platform_id].run_button.setEnabled(False)
        self._append_log(f"[{title}] 正在准备任务...")
        threading.Thread(
            target=self._prepare_and_submit_platform_task,
            args=(platform_id, title, input_path, output_dir,
                  recursive, continuous, settings),
            daemon=True,
        ).start()

    def _prepare_and_submit_platform_task(
        self,
        platform_id: str,
        title: str,
        input_path: pathlib.Path,
        output_dir: pathlib.Path,
        recursive: bool,
        continuous: bool,
        settings: dict[str, Any],
    ) -> None:
        adapter = build_platform_adapter(platform_id)
        settings_updates: dict[str, str] = {}

        if platform_id == "kugou":
            if not settings.get("key_file"):
                found = auto_find_kugou_key(self.paths)
                if found is not None:
                    settings["key_file"] = str(found)
                    settings_updates["key_file"] = str(found)
            if not settings.get("kgg_db_path"):
                found_db = auto_find_kgg_db_path()
                if found_db is not None:
                    settings["kgg_db_path"] = str(found_db)
                    settings_updates["kgg_db_path"] = str(found_db)

        if adapter.requires_running_process():
            while True:
                ok, reason = adapter.validate_runtime(settings)
                if ok:
                    break
                event = threading.Event()
                holder: dict[str, bool] = {"accepted": False}
                self.bridge.runtime_prompt_request.emit(
                    (event, holder, title, reason or "未检测到对应进程。")
                )
                event.wait()
                if not holder.get("accepted"):
                    self.bridge.submission_result.emit(
                        {
                            "platform_id": platform_id,
                            "title": title,
                            "submitted": False,
                            "error": "用户取消了运行前检测。",
                            "settings_updates": settings_updates,
                            "cancelled": True,
                        }
                    )
                    return
        else:
            ok, reason = adapter.validate_runtime(settings)
            if not ok:
                self.bridge.submission_result.emit(
                    {
                        "platform_id": platform_id,
                        "title": title,
                        "submitted": False,
                        "error": reason or "当前平台运行环境不可用。",
                        "settings_updates": settings_updates,
                    }
                )
                return

        submitted, error = self._task_queue.submit(
            platform_id=platform_id,
            title=title,
            input_path=input_path,
            output_dir=output_dir,
            recursive=recursive,
            settings=settings,
            continuous=continuous,
        )
        self.bridge.submission_result.emit(
            {
                "platform_id": platform_id,
                "title": title,
                "submitted": submitted,
                "error": error,
                "settings_updates": settings_updates,
            }
        )

    def _handle_platform_stop(self, platform_id: str) -> None:
        title = self._platform_title(platform_id)
        stopped, error = self._task_queue.stop(platform_id)
        if not stopped:
            self._show_message("无法停止任务", error or "当前平台没有可停止的任务。")
            return
        self._append_log(f"[{title}] 已请求停止。")

    def _start_task_thread(self, target) -> None:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()

    def _resolve_collision(self, base_name: str, extension: str, existing_platform: str | None) -> str:
        event = threading.Event()
        holder: dict[str, str] = {"choice": "suffix"}
        self.bridge.collision_request.emit(
            (event, holder, base_name, extension, existing_platform))
        event.wait()
        return holder["choice"]

    def _handle_collision_request(self, payload: object) -> None:
        event, holder, base_name, extension, existing_platform = payload
        text = f"共享输出目录中已存在同名文件：{base_name}.{extension}\n现有平台：{existing_platform or '未知'}\n请选择处理方式。"
        box = QMessageBox(self)
        box.setWindowTitle("输出冲突")
        box.setText(text)
        suffix_btn = box.addButton("加平台后缀", QMessageBox.ButtonRole.AcceptRole)
        subdir_btn = box.addButton("分平台子目录", QMessageBox.ButtonRole.ActionRole)
        overwrite_btn = box.addButton(
            "覆盖", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_btn:
            holder["choice"] = "overwrite"
        elif clicked is subdir_btn:
            holder["choice"] = "subdir"
        else:
            holder["choice"] = "suffix"
        event.set()

    def _handle_runtime_prompt_request(self, payload: object) -> None:
        event, holder, title, reason = payload
        choice = QMessageBox.question(
            self,
            f"{title} 未运行",
            f"{reason}\n请先开启对应软件，然后点击“是”重新检测。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        holder["accepted"] = choice == QMessageBox.StandardButton.Yes
        event.set()

    def _handle_submission_result(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        platform_id = str(data.get("platform_id", "") or "")
        if not platform_id:
            return
        title = str(data.get("title", platform_id) or platform_id)
        self._submission_inflight.discard(platform_id)

        settings_updates = data.get("settings_updates") or {}
        if platform_id == "kugou":
            if "key_file" in settings_updates:
                self._cards["kugou"].extra_field("key_file").setText(
                    str(settings_updates["key_file"]))
            if "kgg_db_path" in settings_updates:
                self._cards["kugou"].extra_field("kgg_db_path").setText(
                    str(settings_updates["kgg_db_path"]))
            if settings_updates:
                self._save_config_from_widgets(announce=False)

        submitted = bool(data.get("submitted", False))
        error = str(data.get("error", "") or "")
        if not submitted:
            self._cards[platform_id].run_button.setEnabled(True)
            if error:
                self._append_log(f"[{title}] {error}")
            if not data.get("cancelled"):
                self._show_message("任务未提交", error or "当前平台任务已在运行或排队。")
            return

        self._append_log(f"[{title}] 任务已提交。")
        self._save_config_from_widgets(announce=False)

    def _apply_states(self, states: object) -> None:
        states = states if isinstance(states, list) else []
        running = 0
        queued = 0
        for payload in states:
            platform_id = str(payload.get("platform_id", "") or "")
            card = self._cards.get(platform_id)
            if card is not None:
                card.apply_state(payload)
                if platform_id in self._submission_inflight:
                    card.run_button.setEnabled(False)
            status = str(payload.get("status", "idle") or "idle")
            if status in {"running", "waiting", "stopping"}:
                running += 1
            elif status == "queued":
                queued += 1
        self.queue_label.setText(
            f"并发上限 2 个平台任务。当前运行/等待：{running}，排队：{queued}。持续解密会按 FIFO 队列循环重扫。")

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def _show_message(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    runtime_root = RuntimePaths.discover().root_dir
    icon_path = runtime_root / "封面" / "封面.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    QTimer.singleShot(120, lambda: StartupNoticeDialog(window).exec())
    return app.exec()
