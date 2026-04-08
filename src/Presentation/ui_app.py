from __future__ import annotations

import ctypes
import json
import pathlib
import subprocess
import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QMouseEvent, QShowEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.Application.platform_task_queue import PlatformTaskQueue
from src.Application.transcode_batch_service import run_transcode_batch
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
    TRANSCODE_BITRATE_OPTIONS,
    TRANSCODE_SAMPLE_RATE_OPTIONS,
)
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Presentation.modern_widgets import AnimatedProgressBar, MetricTile, StatusPill, apply_card_shadow
from src.Presentation.transcode_card import TranscodeBatchCard
from src.Presentation.window_effects import apply_win10_acrylic
from qfluentwidgets import FluentIcon as FIF, NavigationInterface, NavigationItemPosition

WINDOW_BG = "#101215"
SHELL_BG = "#171A1F"
CARD_BG = "#1E232B"
CARD_ALT = "#202630"
BORDER = "#2B313C"
TEXT = "#FFFFFF"
TEXT_MUTED = "#EDF3FB"
ACCENT = "#2D89EF"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

FORMATS = ["auto"] + [item for item in supported_transcode_formats()
                      if item != "auto"]
QQ_RULE_FORMATS = ["flac", "m4a", "mp3", "wav"]

REASON_TRANSLATIONS: dict[str, tuple[str, str]] = {
    "success": ("成功", "Success"),
    "already_decrypted": ("已跳过，结果已存在", "Skipped because the output already exists"),
    "stopped_by_user": ("用户已停止任务", "Stopped by user"),
    "qq_decrypt_failed": ("QQ 旧链解密失败", "QQ legacy decrypt failed"),
    "unrecognized_audio_container": ("输出不是可识别的音频容器", "Output is not a recognized audio container"),
    "qq_internal_direct_timeout": ("QQ 新链直解超时", "QQ internal direct decrypt timed out"),
    "qq_internal_direct_attach_failed": ("QQ 新链附加失败", "QQ internal direct attach failed"),
    "qq_internal_direct_hook_error": ("QQ 新链 Hook 失败", "QQ internal direct hook failed"),
    "qq_internal_direct_output_missing": ("QQ 新链未生成输出文件", "QQ internal direct output missing"),
    "qq_export_flac_not_found": ("未找到 QQ 导出的 FLAC", "QQ exported FLAC not found"),
    "target_process_not_detected": ("未检测到目标进程", "Target process was not detected"),
    "unsupported_input": ("输入文件类型不受支持", "Unsupported input file type"),
    "unknown_error": ("未知错误", "Unknown error"),
}

USAGE_TIPS: list[dict[str, str]] = [
    {
        "id": "qq_latest_version_container",
        "title": "QQ 音乐容器识别",
        "summary": "请使用最新版 QQ 音乐下载待解密文件，否则导出函数可能无法识别容器。",
        "detail": (
            "请使用最新版本 QQ 音乐，并且确保待解密文件是最新版 QQ 音乐所下载的，"
            "否则导出函数将不识别容器。\n\n"
            "建议：\n"
            "1. 先把 QQ 音乐更新到最新版本。\n"
            "2. 再重新下载需要解密的歌曲。\n"
            "3. 尽量不要混用旧版本客户端下载的缓存和新版本客户端下载的缓存。\n"
            "4. 如果出现“容器无法识别”“导出失败”，优先先重下该文件再测试。"
        ),
    },
        {
        "id": "transcode_profile_enable_rule",
        "title": "转码参数生效条件",
        "summary": "只有勾选“解密成功后进行转码”时，采样率和比特率设置才会生效。",
        "detail": (
            "平台解密页里的格式规则、采样率和比特率，都属于转码阶段配置。\n\n"
            "规则如下：\n"
            "1. 勾选“解密成功后进行转码”后，才会按格式规则和音频参数调用 ffmpeg。\n"
            "2. 未勾选时，会直接保留解密后的原始格式输出，不会改动原始音频。\n"
            "3. 采样率和比特率只对转码产物生效，不会改写原始解密文件。"
        ),
    }
]


def _status_label(ok: bool, skipped: bool) -> str:
    if skipped:
        return "跳过"
    if ok:
        return "成功"
    return "失败"


def _translate_reason_segment(segment: str) -> tuple[str, str]:
    text = str(segment or "").strip()
    if not text:
        return REASON_TRANSLATIONS["unknown_error"]
    for code, (zh, en) in REASON_TRANSLATIONS.items():
        if text == code:
            return zh, en
        if text.startswith(f"{code}:"):
            tail = text.split(":", 1)[1].strip()
            if tail:
                return f"{zh}：{tail}", f"{en}: {tail}"
            return zh, en
    return f"未翻译原因：{text}", f"Untranslated reason: {text}"


def _bilingual_reason(reason: str, *, ok: bool, skipped: bool) -> tuple[str, str]:
    text = str(reason or "").strip()
    if not text:
        if skipped:
            return REASON_TRANSLATIONS["already_decrypted"]
        if ok:
            return REASON_TRANSLATIONS["success"]
        return REASON_TRANSLATIONS["unknown_error"]
    zh_parts: list[str] = []
    en_parts: list[str] = []
    for raw_segment in text.split(";"):
        segment = raw_segment.strip()
        if not segment:
            continue
        zh, en = _translate_reason_segment(segment)
        if zh not in zh_parts:
            zh_parts.append(zh)
        if en not in en_parts:
            en_parts.append(en)
    if not zh_parts:
        return REASON_TRANSLATIONS["unknown_error"]
    return "；".join(zh_parts), " ; ".join(en_parts)


def is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


ADMIN_ACTION_CONTINUE = "continue"
ADMIN_ACTION_RELAUNCH = "relaunch"
ADMIN_ACTION_ALWAYS = "always"


def _current_launch_command() -> tuple[str, str, str | None]:
    executable = sys.executable
    arguments = list(sys.argv[1:])
    working_directory = None
    if getattr(sys, "frozen", False):
        working_directory = str(pathlib.Path(executable).resolve().parent)
    else:
        script_path = pathlib.Path(sys.argv[0]).resolve()
        arguments = [str(script_path), *arguments]
        working_directory = str(script_path.parent)
    return executable, subprocess.list2cmdline(arguments), working_directory


def relaunch_current_process_as_admin() -> tuple[bool, str | None]:
    executable, parameters, working_directory = _current_launch_command()
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters or None,
            working_directory,
            1,
        )
    except Exception as exc:
        return False, str(exc)
    if result <= 32:
        return False, f"提权启动失败，系统返回代码 {result}"
    return True, None


def set_always_run_as_admin(paths: RuntimePaths, enabled: bool) -> None:
    root_config, config = load_config(paths)
    config.setdefault("shared", {})["always_run_as_admin"] = bool(enabled)
    save_config(paths, root_config, config)


class GuardedComboBox(QComboBox):
    def __init__(self) -> None:
        super().__init__()
        popup = QListView()
        popup.setObjectName("ComboPopup")
        popup.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setView(popup)
        self.setMaxVisibleItems(12)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus() or self.view().isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class NoWheelTabBar(QTabBar):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


def build_app_stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: Microsoft YaHei UI; font-size: 13px; }}
    QWidget#RootWindow {{ background: transparent; }}
    QFrame#Shell {{ background: rgba(17, 21, 27, 0.96); border: 1px solid rgba(88, 103, 130, 0.40); border-radius: 22px; }}
    QFrame#WindowSurface {{ background: rgba(11, 15, 21, 0.28); }}
    QFrame#TopBanner {{ background: rgba(18, 23, 31, 0.58); border-bottom: 1px solid rgba(120, 145, 182, 0.20); }}
    QFrame#TitleBar {{ background: transparent; border-bottom: 1px solid rgba(88, 103, 130, 0.22); }}
    QLabel#TitleLabel {{ font-size: 18px; font-weight: 700; }}
    QLabel#SubtitleLabel, QLabel#MutedText, QLabel#CardSubtitle, QLabel#HeroSubtitle {{ color: rgba(255, 255, 255, 0.84); }}
    QLabel#HeroTitle {{ font-size: 26px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: 16px; font-weight: 700; }}
    QLabel#CardTitle {{ font-size: 17px; font-weight: 700; }}
    QLabel#MetricValue {{ font-size: 28px; font-weight: 700; color: white; }}
    QLabel#FieldLabel {{ color: rgba(255, 255, 255, 0.78); font-size: 12px; }}
    QFrame#HeroCard {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(31, 40, 56, 0.92), stop:1 rgba(24, 32, 46, 0.88)); border: 1px solid rgba(94, 120, 158, 0.32); border-radius: 22px; }}
    QFrame#SidebarCard, QFrame#WorkspaceCard, QFrame#InfoCard, QFrame#ConfigCard, QFrame#PlatformCard, QFrame#NoticeCard {{ background: rgba(29, 35, 45, 0.82); border: 1px solid rgba(120, 145, 182, 0.24); border-radius: 18px; }}
    QFrame#SidebarNavHost {{ background: transparent; border: none; }}
    NavigationInterface {{ background: transparent; }}\n    NavigationPanel, NavigationWidget, NavigationTreeWidget, NavigationTreeItem, NavigationToolButton {{ background: transparent; color: rgba(255, 255, 255, 0.98); font-size: 14px; }}\n    NavigationTreeItem:hover, NavigationToolButton:hover {{ background: rgba(255, 255, 255, 0.08); border-radius: 10px; }}\n    NavigationTreeItem[isSelected=true], NavigationToolButton[isSelected=true] {{ background: rgba(45, 137, 239, 0.24); color: white; border-radius: 10px; }}
    QFrame#MiniCard, QFrame#StatusBox, QFrame#MetricTile {{ background: rgba(17, 21, 27, 0.58); border: 1px solid rgba(120, 145, 182, 0.18); border-radius: 14px; }}
    QFrame#WorkspaceCard:hover, QFrame#PlatformCard:hover {{ border-color: rgba(82, 148, 255, 0.42); background: rgba(31, 38, 50, 0.88); }}
    QLineEdit, QComboBox, QPlainTextEdit {{ background: rgba(11, 16, 24, 0.90); border: 1px solid rgba(88, 103, 130, 0.32); border-radius: 12px; padding: 9px 12px; selection-background-color: #3B82F6; }}
    QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: rgba(82, 148, 255, 0.48); }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
    QComboBox {{ padding-right: 34px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border-left: 1px solid rgba(88, 103, 130, 0.24); background: rgba(20, 25, 33, 0.92); border-top-right-radius: 12px; border-bottom-right-radius: 12px; }}
    QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid {TEXT_MUTED}; margin-right: 8px; }}
    QComboBox QAbstractItemView {{ background: rgba(11, 16, 24, 0.96); color: {TEXT}; border: 1px solid rgba(82, 148, 255, 0.45); outline: none; selection-background-color: {ACCENT}; selection-color: white; padding: 4px; }}
    QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 6px 10px; background: transparent; color: {TEXT}; }}
    QComboBox QAbstractItemView::item:selected {{ background: {ACCENT}; color: white; }}
    QComboBox QAbstractItemView::item:hover {{ background: rgba(43, 60, 88, 0.92); color: {TEXT}; }}
    QListView#ComboPopup {{ background: rgba(11, 16, 24, 0.96); color: {TEXT}; border: 1px solid rgba(82, 148, 255, 0.45); outline: none; selection-background-color: {ACCENT}; selection-color: white; }}
    QListView#ComboPopup::item {{ min-height: 30px; padding: 6px 10px; background: transparent; color: {TEXT}; }}
    QListView#ComboPopup::item:selected {{ background: {ACCENT}; color: white; }}
    QListView#ComboPopup::item:hover {{ background: rgba(43, 60, 88, 0.92); color: {TEXT}; }}
    QPushButton {{ border-radius: 12px; padding: 9px 16px; border: 1px solid rgba(88, 103, 130, 0.30); background: rgba(33, 41, 56, 0.88); }}
    QPushButton#PrimaryButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2D89EF, stop:1 #4B9BFF); border-color: rgba(109, 174, 255, 0.72); color: white; font-weight: 700; }}
    QPushButton#SecondaryButton {{ background: rgba(37, 50, 70, 0.86); border-color: rgba(88, 114, 148, 0.46); }}
    QPushButton#GhostButton {{ background: rgba(255, 255, 255, 0.03); }}
    QPushButton#DangerButton {{ background: rgba(86, 31, 40, 0.86); border-color: rgba(177, 74, 92, 0.56); }}
    QPushButton#RoundButton {{ background: rgba(37, 50, 70, 0.86); border-color: rgba(88, 114, 148, 0.46); font-size: 16px; font-weight: 700; padding: 0px; }}
    QPushButton#DangerRoundButton {{ background: rgba(86, 31, 40, 0.86); border-color: rgba(177, 74, 92, 0.56); font-size: 16px; font-weight: 700; padding: 0px; }}
    QPushButton:hover {{ border-color: rgba(82, 148, 255, 0.60); background: rgba(42, 52, 70, 0.94); }}
    QPushButton#PrimaryButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3A93F6, stop:1 #62ACFF); border-color: rgba(130, 189, 255, 0.82); }}
    QPushButton#DangerButton:hover {{ background: rgba(103, 39, 49, 0.92); border-color: rgba(207, 95, 113, 0.68); }}
    QPushButton:pressed {{ background: rgba(24, 30, 40, 0.96); }}
    QPushButton#PrimaryButton:pressed {{ background: #226EBD; }}
    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid rgba(88, 103, 130, 0.40); background: rgba(11, 16, 24, 0.92); }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QRadioButton {{ spacing: 8px; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid rgba(88, 103, 130, 0.40); background: rgba(11, 16, 24, 0.92); }}
    QRadioButton::indicator:hover {{ border-color: rgba(82, 148, 255, 0.48); }}
    QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QPlainTextEdit#LogView {{ background: rgba(9, 12, 18, 0.94); border-radius: 14px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(71, 87, 110, 0.88); min-height: 28px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: rgba(101, 129, 166, 0.92); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: rgba(71, 87, 110, 0.88); min-width: 28px; border-radius: 5px; }}
    QScrollBar::handle:horizontal:hover {{ background: rgba(101, 129, 166, 0.92); }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QTabWidget::pane {{ border: 1px solid rgba(88, 103, 130, 0.22); border-radius: 16px; top: -1px; background: rgba(27, 33, 42, 0.82); }}
    QTabBar::tab {{ background: rgba(24, 30, 40, 0.86); color: {TEXT_MUTED}; border: 1px solid rgba(88, 103, 130, 0.22); padding: 11px 18px; border-top-left-radius: 11px; border-top-right-radius: 11px; min-width: 108px; }}
    QTabBar::tab:selected {{ background: rgba(30, 36, 46, 0.92); color: {TEXT}; border-color: rgba(82, 148, 255, 0.42); }}
    QTabBar::tab:hover:!selected {{ background: rgba(34, 42, 54, 0.92); color: {TEXT}; }}
    QSplitter::handle {{ background: transparent; }}
    """


class UiBridge(QObject):
    states_changed = Signal(object)
    log_line = Signal(str)
    collision_request = Signal(object)
    runtime_prompt_request = Signal(object)
    transcode_confirmation_request = Signal(object)
    submission_result = Signal(object)
    transcode_event = Signal(object)


class TitleBar(QFrame):
    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None
        self._parent_widget = parent
        self.setObjectName("TitleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 10)
        layout.setSpacing(10)

        app_icon = parent.windowIcon()
        if app_icon.isNull():
            instance = QApplication.instance()
            if instance is not None:
                app_icon = instance.windowIcon()
        if not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setFixedSize(24, 24)
            icon_label.setPixmap(app_icon.pixmap(22, 22))
            layout.addWidget(icon_label)

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

        self.tips_button = QPushButton("使用技巧")
        self.tips_button.setObjectName("SecondaryButton")
        self.min_button = QPushButton("最小化")
        self.min_button.setObjectName("GhostButton")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("DangerButton")
        self.tips_button.clicked.connect(self._handle_tips)
        self.min_button.clicked.connect(parent.showMinimized)
        self.close_button.clicked.connect(parent.close)
        layout.addWidget(self.tips_button)
        layout.addWidget(self.min_button)
        layout.addWidget(self.close_button)

    def _handle_tips(self) -> None:
        callback = getattr(self._parent_widget, "_show_usage_tips", None)
        if callable(callback):
            callback()

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


class AdminRequiredDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, relaunch_error: str | None = None, always_enabled: bool = False) -> None:
        super().__init__(parent)
        self._drag_origin: QPoint | None = None
        self.selected_action = ADMIN_ACTION_CONTINUE
        self.setWindowTitle("管理员权限提示")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(560, 360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("Shell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        outer.addWidget(shell)

        title_bar = TitleBar(self, "管理员权限提示")
        title_bar.min_button.hide()
        shell_layout.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("NoticeCard")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 20, 22, 20)
        body_layout.setSpacing(12)

        title = QLabel("当前不是管理员启动")
        title.setObjectName("CardTitle")
        message = QLabel(
            "非管理员模式下，部分平台的进程访问、数据库读取和运行时目录写入可能受限。\n\n"
            "你可以先继续运行；如果要完整能力，可以立即提权启动，也可以直接开启‘以后始终以管理员运行’。"
        )
        message.setWordWrap(True)
        message.setObjectName("MutedText")

        state_note = QLabel(
            "当前设置：以后始终以管理员运行 - {}".format("已开启" if always_enabled else "未开启")
        )
        state_note.setObjectName("MutedText")

        body_layout.addWidget(title)
        body_layout.addWidget(message)
        body_layout.addWidget(state_note)

        if relaunch_error:
            error_label = QLabel(f"上一次提权启动未成功：{relaunch_error}")
            error_label.setWordWrap(True)
            error_label.setStyleSheet(f"color: {WARNING};")
            body_layout.addWidget(error_label)

        body_layout.addStretch(1)

        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 8, 0, 0)
        button_column.setSpacing(10)

        continue_button = QPushButton("继续运行")
        continue_button.setObjectName("SecondaryButton")
        continue_button.setMinimumHeight(40)
        continue_button.clicked.connect(lambda: self._finish(ADMIN_ACTION_CONTINUE))

        relaunch_button = QPushButton("以管理员启动")
        relaunch_button.setObjectName("PrimaryButton")
        relaunch_button.setMinimumHeight(40)
        relaunch_button.clicked.connect(lambda: self._finish(ADMIN_ACTION_RELAUNCH))

        always_button = QPushButton("以后始终以管理员运行")
        always_button.setObjectName("GhostButton")
        always_button.setMinimumHeight(40)
        always_button.clicked.connect(lambda: self._finish(ADMIN_ACTION_ALWAYS))

        button_column.addWidget(continue_button)
        button_column.addWidget(relaunch_button)
        button_column.addWidget(always_button)
        body_layout.addLayout(button_column)
        shell_layout.addWidget(body)

        self.setStyleSheet(build_app_stylesheet())

    def _finish(self, action: str) -> None:
        self.selected_action = action
        self.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
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
        self.edit.setCursorPosition(0)


class BatchDetailDialog(QDialog):
    def __init__(
        self,
        title: str,
        *,
        summary: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        fallback_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(980, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(title)
        header.setObjectName("CardTitle")
        layout.addWidget(header)

        summary = dict(summary or {})
        rows = list(rows or [])
        if rows:
            self._all_rows = rows
            summary_label = QLabel(
                f"平台：{summary.get('platform_id', '未知')}    成功：{summary.get('success_count', 0)}    "
                f"跳过：{summary.get('skipped_count', 0)}    失败：{summary.get('failed_count', 0)}"
            )
            summary_label.setObjectName("MutedText")
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)

            filter_row = QHBoxLayout()
            filter_row.setContentsMargins(0, 0, 0, 0)
            filter_row.setSpacing(12)
            filter_label = QLabel("分组查看")
            filter_label.setObjectName("FieldLabel")
            filter_row.addWidget(filter_label)
            self.filter_group = QButtonGroup(self)
            self.filter_buttons: dict[str, QRadioButton] = {}
            for value, text in (
                ("all", "全部"),
                ("success", "成功"),
                ("skipped", "跳过"),
                ("failed", "失败"),
            ):
                radio = QRadioButton(text)
                self.filter_group.addButton(radio)
                self.filter_buttons[value] = radio
                filter_row.addWidget(radio)
            filter_row.addStretch(1)
            layout.addLayout(filter_row)

            splitter = QSplitter(Qt.Orientation.Vertical)
            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["状态", "输入文件", "输出文件", "原因摘要"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            header_view = self.table.horizontalHeader()
            header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

            for row_index, item in enumerate(rows):
                status_text = str(item.get("status_text", "") or "")
                input_name = str(item.get("input_name", "") or "")
                output_name = str(item.get("output_name", "") or "")
                reason_summary = str(item.get("reason_zh", "") or "")
                cells = [
                    QTableWidgetItem(status_text),
                    QTableWidgetItem(input_name),
                    QTableWidgetItem(output_name),
                    QTableWidgetItem(reason_summary),
                ]
                for column, cell in enumerate(cells):
                    cell.setData(Qt.ItemDataRole.UserRole, item)
                    self.table.setItem(row_index, column, cell)

            self.detail_view = QPlainTextEdit()
            self.detail_view.setObjectName("LogView")
            self.detail_view.setReadOnly(True)

            splitter.addWidget(self.table)
            splitter.addWidget(self.detail_view)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            layout.addWidget(splitter, 1)

            self.table.currentCellChanged.connect(self._show_row_detail)
            self.table.cellDoubleClicked.connect(lambda *_: self._show_row_detail(self.table.currentRow(), 0, -1, -1))
            for value, radio in self.filter_buttons.items():
                radio.toggled.connect(lambda checked, mode=value: self._apply_filter(mode) if checked else None)
            self.filter_buttons["all"].setChecked(True)
        else:
            self.editor = QPlainTextEdit()
            self.editor.setObjectName("LogView")
            self.editor.setReadOnly(True)
            self.editor.setPlainText(fallback_text or "暂无详情")
            layout.addWidget(self.editor, 1)

        close_button = QPushButton("关闭")
        close_button.setObjectName("PrimaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _show_row_detail(self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int) -> None:
        if current_row < 0 or not hasattr(self, "table") or not hasattr(self, "detail_view"):
            return
        cell = self.table.item(current_row, 0)
        if cell is None:
            return
        item = dict(cell.data(Qt.ItemDataRole.UserRole) or {})
        lines = [
            f"状态：{item.get('status_text', '')}",
            f"输入文件：{item.get('input_name', '')}",
            f"输出文件：{item.get('output_name', '') or '无'}",
            "",
            "中文原因：",
            str(item.get("reason_zh", "") or "无"),
            "",
            "English Reason:",
            str(item.get("reason_en", "") or "N/A"),
        ]
        raw_reason = str(item.get("raw_reason", "") or "").strip()
        if raw_reason:
            lines.extend(["", "原始原因代码：", raw_reason])
        self.detail_view.setPlainText("\n".join(lines))

    def _apply_filter(self, mode: str) -> None:
        if not hasattr(self, "_all_rows"):
            return
        if mode == "success":
            rows = [item for item in self._all_rows if item.get("status_text") == "成功"]
        elif mode == "skipped":
            rows = [item for item in self._all_rows if item.get("status_text") == "跳过"]
        elif mode == "failed":
            rows = [item for item in self._all_rows if item.get("status_text") == "失败"]
        else:
            rows = list(self._all_rows)
        self._populate_rows(rows)

    def _populate_rows(self, rows: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, item in enumerate(rows):
            status_text = str(item.get("status_text", "") or "")
            input_name = str(item.get("input_name", "") or "")
            output_name = str(item.get("output_name", "") or "")
            reason_summary = str(item.get("reason_zh", "") or "")
            cells = [
                QTableWidgetItem(status_text),
                QTableWidgetItem(input_name),
                QTableWidgetItem(output_name),
                QTableWidgetItem(reason_summary),
            ]
            color = QColor("#60A5FA") if status_text in {"成功", "跳过"} else QColor("#F87171")
            for column, cell in enumerate(cells):
                cell.setData(Qt.ItemDataRole.UserRole, item)
                cell.setForeground(color)
                self.table.setItem(row_index, column, cell)
        if rows:
            self.table.selectRow(0)
            self._show_row_detail(0, 0, -1, -1)
        else:
            self.detail_view.setPlainText("当前分组没有记录。")


class UsageTipDetailDialog(QDialog):
    def __init__(self, tip: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(str(tip.get("title", "使用技巧") or "使用技巧"))
        self.setModal(True)
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(str(tip.get("title", "使用技巧") or "使用技巧"))
        title.setObjectName("CardTitle")
        summary = QLabel(str(tip.get("summary", "") or ""))
        summary.setObjectName("MutedText")
        summary.setWordWrap(True)

        detail = QPlainTextEdit()
        detail.setObjectName("LogView")
        detail.setReadOnly(True)
        detail.setPlainText(str(tip.get("detail", "") or ""))

        close_button = QPushButton("关闭")
        close_button.setObjectName("PrimaryButton")
        close_button.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addWidget(summary)
        layout.addWidget(detail, 1)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)


class UsageTipsDialog(QDialog):
    def __init__(self, tips: list[dict[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tips = list(tips)
        self.setWindowTitle("使用技巧")
        self.setModal(True)
        self.resize(860, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("使用技巧")
        title.setObjectName("CardTitle")
        subtitle = QLabel("这里汇总了常见平台的使用建议。点击卡片上的“查看详情”可查看完整说明。")
        subtitle.setObjectName("MutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(12)

        for tip in self._tips:
            card = QFrame()
            card.setObjectName("ConfigCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(10)

            card_title = QLabel(str(tip.get("title", "使用技巧") or "使用技巧"))
            card_title.setObjectName("CardTitle")
            card_summary = QLabel(str(tip.get("summary", "") or ""))
            card_summary.setObjectName("MutedText")
            card_summary.setWordWrap(True)

            button_row = QHBoxLayout()
            button_row.setContentsMargins(0, 0, 0, 0)
            button_row.setSpacing(8)
            button_row.addStretch(1)
            detail_button = QPushButton("查看详情")
            detail_button.setObjectName("SecondaryButton")
            detail_button.clicked.connect(lambda _=False, item=dict(tip): self._show_tip_detail(item))
            button_row.addWidget(detail_button)

            card_layout.addWidget(card_title)
            card_layout.addWidget(card_summary)
            card_layout.addLayout(button_row)
            container_layout.addWidget(card)

        container_layout.addStretch(1)
        scroll.setWidget(container)

        close_button = QPushButton("关闭")
        close_button.setObjectName("PrimaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _show_tip_detail(self, tip: dict[str, str]) -> None:
        dialog = UsageTipDetailDialog(tip, self)
        dialog.exec()



class PlatformCard(QFrame):
    run_requested = Signal(str)
    stop_requested = Signal(str)
    detail_requested = Signal(str)

    def __init__(self, platform_id: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.platform_id = platform_id
        self.setObjectName("WorkspaceCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._format_widgets: dict[str, QComboBox] = {}
        self._extra_fields: dict[str, PathField] = {}
        self._radio_groups: dict[str, tuple[QButtonGroup, dict[str, QRadioButton]]] = {}
        self._transcode_profile_toggles: dict[str, QCheckBox] = {}
        self._transcode_profile_combos: dict[str, QComboBox] = {}
        self._batch_report_json = ""
        self._batch_report_txt = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
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
        self.status_pill = StatusPill("空闲", "idle")
        header_row.addLayout(header, 1)
        header_row.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header_row)

        self.progress_caption = QLabel("当前进度：0 / 0")
        self.progress_caption.setObjectName("MutedText")
        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.set_progress(0, 0, active=False)
        root.addWidget(self.progress_caption)
        root.addWidget(self.progress_bar)

        self.input_field = PathField("输入目录或文件", directory=True)
        root.addWidget(self.input_field)

        self.form_layout = QGridLayout()
        self.form_layout.setHorizontalSpacing(10)
        self.form_layout.setVerticalSpacing(8)
        self.form_layout.setColumnStretch(1, 1)
        root.addLayout(self.form_layout)

        self.transcode_frame = QFrame()
        self.transcode_frame.setObjectName("MiniCard")
        self.transcode_layout_root = QVBoxLayout(self.transcode_frame)
        self.transcode_layout_root.setContentsMargins(14, 12, 14, 12)
        self.transcode_layout_root.setSpacing(8)
        self.transcode_title = QLabel("转码设置")
        self.transcode_title.setObjectName("SectionTitle")
        self.transcode_checkbox = QCheckBox("解密成功后进行转码")
        self.transcode_hint = QLabel("未启用转码。当前平台只输出解密后的原始格式。")
        self.transcode_hint.setObjectName("MutedText")
        self.transcode_hint.setWordWrap(True)
        self.transcode_form_layout = QGridLayout()
        self.transcode_form_layout.setHorizontalSpacing(10)
        self.transcode_form_layout.setVerticalSpacing(8)
        self.transcode_form_layout.setColumnStretch(1, 1)
        self.transcode_layout_root.addWidget(self.transcode_title)
        self.transcode_layout_root.addWidget(self.transcode_checkbox)
        self.transcode_layout_root.addWidget(self.transcode_hint)
        self.transcode_layout_root.addLayout(self.transcode_form_layout)
        root.addWidget(self.transcode_frame)

        state_card = QFrame()
        state_card.setObjectName("MiniCard")
        status_layout = QGridLayout(state_card)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setHorizontalSpacing(14)
        status_layout.setVerticalSpacing(6)
        status_layout.setColumnStretch(1, 1)
        status_layout.setColumnStretch(3, 1)
        self.status_label = QLabel("状态：空闲")
        self.message_label = QLabel("说明：等待任务")
        self.count_label = QLabel("统计：成功 0，恢复 0，跳过 0，失败 0")
        self.file_label = QLabel("当前文件：无")
        self.progress_label = QLabel("进度：0 / 0")
        self.timing_label = QLabel("热点：无")
        self.failed_file_label = QLabel("最近失败：无")
        self.failed_reason_label = QLabel("失败原因：无")
        for widget in (
            self.status_label,
            self.message_label,
            self.count_label,
            self.file_label,
            self.progress_label,
            self.timing_label,
            self.failed_file_label,
            self.failed_reason_label,
        ):
            widget.setWordWrap(True)
        status_layout.addWidget(self.status_label, 0, 0, 1, 2)
        status_layout.addWidget(self.count_label, 0, 2, 1, 2)
        status_layout.addWidget(self.message_label, 1, 0, 1, 4)
        status_layout.addWidget(self.file_label, 2, 0, 1, 2)
        status_layout.addWidget(self.progress_label, 2, 2, 1, 2)
        status_layout.addWidget(self.timing_label, 3, 0, 1, 2)
        status_layout.addWidget(self.failed_file_label, 3, 2, 1, 2)
        status_layout.addWidget(self.failed_reason_label, 4, 0, 1, 4)
        root.addWidget(state_card)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(10)
        self.continuous_checkbox = QCheckBox("持续解密（循环扫描新文件）")
        footer_row.addWidget(self.continuous_checkbox)
        footer_row.addStretch(1)
        root.addLayout(footer_row)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.run_button = QPushButton("开始该平台任务")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.platform_id))
        self.stop_button = QPushButton("停止当前任务")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.platform_id))
        self.detail_button = QPushButton("查看详情")
        self.detail_button.setObjectName("SecondaryButton")
        self.detail_button.setEnabled(False)
        self.detail_button.clicked.connect(lambda: self.detail_requested.emit(self.platform_id))
        button_row.addWidget(self.run_button, 1)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.detail_button)
        root.addLayout(button_row)

        self.transcode_checkbox.toggled.connect(self._update_transcode_controls)
        self._update_transcode_controls()
        apply_card_shadow(self)

    def add_format_combo(self, key: str, label: str, values: list[str]) -> None:
        combo = GuardedComboBox()
        combo.addItems(values)
        combo.setObjectName("ComboBox")
        row = self.transcode_form_layout.rowCount()
        label_widget = QLabel(label)
        label_widget.setObjectName("FieldLabel")
        self.transcode_form_layout.addWidget(label_widget, row, 0)
        self.transcode_form_layout.addWidget(combo, row, 1)
        self._format_widgets[key] = combo

    def add_transcode_profile_controls(self) -> None:
        if self._transcode_profile_combos:
            return
        self._add_optional_transcode_combo("sample_rate_hz", "指定采样率", [str(value) for value in TRANSCODE_SAMPLE_RATE_OPTIONS], suffix="Hz")
        self._add_optional_transcode_combo("bitrate_kbps", "指定比特率", [str(value) for value in TRANSCODE_BITRATE_OPTIONS], suffix="kbps")
        self._update_transcode_controls()

    def _add_optional_transcode_combo(self, key: str, label: str, values: list[str], *, suffix: str) -> None:
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(10)
        toggle = QCheckBox(label)
        combo = GuardedComboBox()
        combo.setObjectName("ComboBox")
        for value in values:
            combo.addItem(f"{value} {suffix}", value)
        combo.setMinimumWidth(160)
        wrapper_layout.addWidget(toggle)
        wrapper_layout.addWidget(combo, 1)
        row = self.transcode_form_layout.rowCount()
        self.transcode_form_layout.addWidget(wrapper, row, 0, 1, 2)
        self._transcode_profile_toggles[key] = toggle
        self._transcode_profile_combos[key] = combo
        toggle.toggled.connect(self._update_transcode_controls)

    def set_transcode_profile(self, *, sample_rate_hz: int | None, bitrate_kbps: int | None) -> None:
        self._set_optional_profile_value("sample_rate_hz", sample_rate_hz)
        self._set_optional_profile_value("bitrate_kbps", bitrate_kbps)
        self._update_transcode_controls()

    def _set_optional_profile_value(self, key: str, value: int | None) -> None:
        toggle = self._transcode_profile_toggles[key]
        combo = self._transcode_profile_combos[key]
        if value is None:
            toggle.setChecked(False)
            combo.setCurrentIndex(0)
            return
        index = combo.findData(str(int(value)))
        toggle.setChecked(True)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def transcode_sample_rate_hz(self) -> int | None:
        return self._optional_profile_value("sample_rate_hz")

    def transcode_bitrate_kbps(self) -> int | None:
        return self._optional_profile_value("bitrate_kbps")

    def _optional_profile_value(self, key: str) -> int | None:
        toggle = self._transcode_profile_toggles.get(key)
        combo = self._transcode_profile_combos.get(key)
        if toggle is None or combo is None or not toggle.isChecked():
            return None
        data = combo.currentData()
        if data in (None, ""):
            return None
        return int(data)

    def add_extra_field(self, key: str, label: str, *, directory: bool) -> PathField:
        field = PathField(label, directory=directory)
        row = self.form_layout.rowCount()
        self.form_layout.addWidget(field, row, 0, 1, 2)
        self._extra_fields[key] = field
        return field

    def add_radio_group(self, key: str, label: str, options: list[tuple[str, str]], note: str) -> None:
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(6)

        radio_row = QHBoxLayout()
        radio_row.setContentsMargins(0, 0, 0, 0)
        radio_row.setSpacing(14)
        group = QButtonGroup(wrapper)
        radios: dict[str, QRadioButton] = {}
        for value, text in options:
            radio = QRadioButton(text)
            group.addButton(radio)
            radios[value] = radio
            radio_row.addWidget(radio)
        radio_row.addStretch(1)
        wrapper_layout.addLayout(radio_row)

        note_label = QLabel(note)
        note_label.setObjectName("MutedText")
        note_label.setWordWrap(True)
        wrapper_layout.addWidget(note_label)

        row = self.form_layout.rowCount()
        label_widget = QLabel(label)
        label_widget.setObjectName("FieldLabel")
        self.form_layout.addWidget(label_widget, row, 0, alignment=Qt.AlignmentFlag.AlignTop)
        self.form_layout.addWidget(wrapper, row, 1)
        self._radio_groups[key] = (group, radios)

    def set_format_value(self, key: str, value: str) -> None:
        combo = self._format_widgets[key]
        value = value if value in [combo.itemText(i) for i in range(combo.count())] else combo.itemText(0)
        combo.setCurrentText(value)

    def format_value(self, key: str) -> str:
        return self._format_widgets[key].currentText().strip()

    def set_radio_value(self, key: str, value: str) -> None:
        _, radios = self._radio_groups[key]
        selected = radios.get(value)
        if selected is None:
            selected = next(iter(radios.values()))
        selected.setChecked(True)

    def radio_value(self, key: str) -> str:
        _, radios = self._radio_groups[key]
        for value, radio in radios.items():
            if radio.isChecked():
                return value
        return next(iter(radios))

    def extra_field(self, key: str) -> PathField:
        return self._extra_fields[key]

    def set_formats_enabled(self, enabled: bool) -> None:
        for combo in self._format_widgets.values():
            combo.setEnabled(enabled)

    def set_transcode_enabled(self, enabled: bool) -> None:
        self.transcode_checkbox.setChecked(bool(enabled))
        self._update_transcode_controls()

    def transcode_enabled(self) -> bool:
        return self.transcode_checkbox.isChecked()

    def _update_transcode_controls(self) -> None:
        enabled = self.transcode_checkbox.isChecked()
        self.set_formats_enabled(enabled)
        for key, toggle in self._transcode_profile_toggles.items():
            combo = self._transcode_profile_combos[key]
            if not enabled and toggle.isChecked():
                toggle.blockSignals(True)
                toggle.setChecked(False)
                toggle.blockSignals(False)
            toggle.setEnabled(enabled)
            combo.setEnabled(enabled and toggle.isChecked())
        if enabled:
            self.transcode_hint.setText("已启用转码。解密成功后会按下方格式规则与可选音频参数统一转码。")
        else:
            self.transcode_hint.setText("未启用转码。当前平台只输出解密后的原始格式。")

    def detail_paths(self) -> tuple[str, str]:
        return self._batch_report_json, self._batch_report_txt

    def apply_state(self, payload: dict[str, Any]) -> None:
        status = str(payload.get("status", "idle") or "idle")
        mapping = {
            "idle": ("空闲", "idle"),
            "queued": ("排队中", "warning"),
            "running": ("运行中", "active"),
            "waiting": ("等待下一轮", "warning"),
            "stopping": ("停止中", "warning"),
            "stopped": ("已停止", "idle"),
            "success": ("已完成", "success"),
            "skipped": ("已跳过", "idle"),
            "failed": ("失败", "danger"),
        }
        status_text, tone = mapping.get(status, (status, "idle"))
        self.status_pill.setText(status_text)
        self.status_pill.set_tone(tone)
        self.status_label.setText(f"状态：{status_text}")
        self.message_label.setText(f"说明：{payload.get('message', '无')}")
        self.count_label.setText(
            "统计：成功 {success}，恢复 {recovered}，跳过 {skipped}，失败 {failed}".format(
                success=int(payload.get("success_count", 0) or 0),
                recovered=int(payload.get("recovered_count", 0) or 0),
                skipped=int(payload.get("skipped_count", 0) or 0),
                failed=int(payload.get("failed_count", 0) or 0),
            )
        )
        current = int(payload.get("current_index", 0) or 0)
        total = int(payload.get("current_total", 0) or 0)
        self.progress_label.setText(f"进度：{current} / {total}")
        current_file = pathlib.Path(str(payload.get("current_file", "") or "")).name or "无"
        self.file_label.setText(f"当前文件：{current_file}")
        hotspot = payload.get("timing_hotspot") or {}
        hotspot_text = f"{hotspot.get('stage', '无')} / {hotspot.get('ratio', 0)}" if hotspot else "无"
        self.timing_label.setText(f"热点：{hotspot_text}")
        failed_file = pathlib.Path(str(payload.get("last_failed_file", "") or "")).name or "无"
        failed_reason = str(payload.get("last_failed_reason", "") or "无")
        self.failed_file_label.setText(f"最近失败：{failed_file}")
        self.failed_reason_label.setText(f"失败原因：{failed_reason}")
        self._batch_report_json = str(payload.get("batch_report_json", "") or "")
        self._batch_report_txt = str(payload.get("batch_report_txt", "") or "")
        active = status in {"queued", "running", "waiting", "stopping"}
        self.progress_caption.setText(f"当前进度：{current} / {total}" if total else "当前进度：等待任务")
        self.progress_bar.set_progress(current, total, active=active)
        self.run_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.detail_button.setEnabled(bool(self._batch_report_json or self._batch_report_txt))
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
            transcode_confirmation_resolver=self._resolve_transcode_confirmation,
            max_running=2,
        )
        self._submission_inflight: set[str] = set()
        self._transcode_running = False
        self._drag_origin: QPoint | None = None
        self._cards: dict[str, PlatformCard] = {}
        self._acrylic_applied = False
        self._tab_platform_ids: list[str] = []
        self._workspace_pages: dict[str, QWidget] = {}
        self._build_ui()
        self._connect_signals()
        self._load_config_into_widgets()
        self._append_log("界面初始化完成。")


    def _build_ui(self) -> None:
        self.setWindowTitle(PROJECT_NAME_EN)
        self.setObjectName("RootWindow")
        icon_path = self.paths.root_dir / "封面" / "封面.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(1280, 820)
        self.resize(1500, 940)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        surface = QFrame()
        surface.setObjectName("WindowSurface")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        outer.addWidget(surface)

        top_banner = QFrame()
        top_banner.setObjectName("TopBanner")
        top_layout = QHBoxLayout(top_banner)
        top_layout.setContentsMargins(22, 16, 22, 14)
        top_layout.setSpacing(12)
        banner_box = QVBoxLayout()
        banner_box.setContentsMargins(0, 0, 0, 0)
        banner_box.setSpacing(2)
        banner_title = QLabel(f"{PROJECT_NAME_EN} | {PROJECT_NAME_ZH}")
        banner_title.setObjectName("TitleLabel")
        banner_subtitle = QLabel("Steam++ 风格工作台 | 平台解密、批量转码、设置与日志分区清晰独立")
        banner_subtitle.setObjectName("SubtitleLabel")
        banner_box.addWidget(banner_title)
        banner_box.addWidget(banner_subtitle)
        top_layout.addLayout(banner_box, 1)
        self.header_tips_button = QPushButton("使用技巧")
        self.header_tips_button.setObjectName("SecondaryButton")
        self.header_output_button = QPushButton("打开输出目录")
        self.header_output_button.setObjectName("GhostButton")
        self.header_reload_button = QPushButton("重新读取配置")
        self.header_reload_button.setObjectName("GhostButton")
        top_layout.addWidget(self.header_tips_button)
        top_layout.addWidget(self.header_output_button)
        top_layout.addWidget(self.header_reload_button)
        surface_layout.addWidget(top_banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        surface_layout.addWidget(scroll, 1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 20, 22, 22)
        body_layout.setSpacing(16)
        scroll.setWidget(body)

        hero_card = QFrame()
        hero_card.setObjectName("HeroCard")
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(24, 22, 24, 22)
        hero_layout.setSpacing(20)

        hero_left = QVBoxLayout()
        hero_left.setContentsMargins(0, 0, 0, 0)
        hero_left.setSpacing(10)
        title = QLabel("统一解密工作台")
        title.setObjectName("HeroTitle")
        desc = QLabel(
            "保留无边框体验，把共享设置、平台解密和批量转码拆成清晰的工作区。先确认输出与附加策略，再切到平台页执行任务。"
        )
        desc.setWordWrap(True)
        desc.setObjectName("HeroSubtitle")
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(8)
        for label, tone in (("PySide6", "active"), ("无边框", "warning"), ("FFmpeg 内置", "success")):
            pill = StatusPill(label, tone)
            chip_row.addWidget(pill)
        chip_row.addStretch(1)
        link = QLabel(f'<a href="{PROJECT_ADDRESS}">{PROJECT_ADDRESS}</a>')
        link.setOpenExternalLinks(True)
        legal = QLabel(f"QQ：{PROJECT_QQ}\n{QQMUSIC_ATTRIBUTION}\n{LEGAL_NOTICE}")
        legal.setWordWrap(True)
        legal.setObjectName("MutedText")
        hero_left.addWidget(title)
        hero_left.addWidget(desc)
        hero_left.addLayout(chip_row)
        hero_left.addWidget(link)
        hero_left.addWidget(legal)

        hero_right = QWidget()
        hero_right_layout = QGridLayout(hero_right)
        hero_right_layout.setContentsMargins(0, 0, 0, 0)
        hero_right_layout.setHorizontalSpacing(12)
        hero_right_layout.setVerticalSpacing(12)
        self.metric_running_tile = MetricTile("运行任务", "0", "当前正在运行")
        self.metric_queue_tile = MetricTile("排队任务", "0", "FIFO 队列")
        self.metric_transcode_tile = MetricTile("转码状态", "待命", "批量与平台共用 ffmpeg")
        self.metric_output_tile = MetricTile("输出模式", "共享", "可切到分平台目录")
        hero_right_layout.addWidget(self.metric_running_tile, 0, 0)
        hero_right_layout.addWidget(self.metric_queue_tile, 0, 1)
        hero_right_layout.addWidget(self.metric_transcode_tile, 1, 0)
        hero_right_layout.addWidget(self.metric_output_tile, 1, 1)

        hero_layout.addLayout(hero_left, 3)
        hero_layout.addWidget(hero_right, 2)
        body_layout.addWidget(hero_card)

        shared_card = QFrame()
        shared_card.setObjectName("ConfigCard")
        shared_layout = QVBoxLayout(shared_card)
        shared_layout.setContentsMargins(18, 16, 18, 16)
        shared_layout.setSpacing(12)
        shared_title = QLabel("共享设置")
        shared_title.setObjectName("SectionTitle")
        shared_layout.addWidget(shared_title)

        self.output_mode_group = QButtonGroup(shared_card)
        self.output_mode_shared_radio = QRadioButton("共享统一输出目录")
        self.output_mode_platform_radio = QRadioButton("每个平台单独一个输出目录")
        self.output_mode_group.addButton(self.output_mode_shared_radio)
        self.output_mode_group.addButton(self.output_mode_platform_radio)
        self.output_mode_note = QLabel(
            "共享模式下所有平台共用一个输出目录；分平台模式下会在基础输出目录下自动创建 qq / kuwo / kugou / netease 子目录。"
        )
        self.output_mode_note.setObjectName("MutedText")
        self.output_mode_note.setWordWrap(True)

        self.output_field = PathField("共享输出目录", directory=True)
        self.recursive_checkbox = QCheckBox("递归扫描子目录")

        self.cover_mode_group = QButtonGroup(shared_card)
        self.cover_enable_radio = QRadioButton("自动补封面")
        self.cover_disable_radio = QRadioButton("不添加封面")
        self.cover_mode_group.addButton(self.cover_enable_radio)
        self.cover_mode_group.addButton(self.cover_disable_radio)
        self.cover_note = QLabel("提示：封面补写对 QQ、酷我和酷狗都生效。会优先查本地图片和缓存，必要时才联网，可能会明显变慢。")
        self.cover_note.setObjectName("MutedText")
        self.cover_note.setWordWrap(True)

        self.album_mode_group = QButtonGroup(shared_card)
        self.album_enable_radio = QRadioButton("补充专辑信息")
        self.album_disable_radio = QRadioButton("不补充专辑信息")
        self.album_mode_group.addButton(self.album_enable_radio)
        self.album_mode_group.addButton(self.album_disable_radio)
        self.album_note = QLabel(
            "提示：专辑信息补全目前仅对 m4a 生效，因为其他格式自带信息，优先使用本地已有信息，缺失时才会网络兜底。会变慢大约 5 倍。\n"
            "由于 wav 的格式限制，无法写入封面以及专辑信息。"
        )
        self.album_note.setObjectName("MutedText")
        self.album_note.setWordWrap(True)

        output_mode_row = QHBoxLayout()
        output_mode_row.setContentsMargins(0, 0, 0, 0)
        output_mode_row.setSpacing(14)
        output_mode_label = QLabel("输出目录模式")
        output_mode_label.setObjectName("FieldLabel")
        output_mode_row.addWidget(output_mode_label)
        output_mode_row.addWidget(self.output_mode_shared_radio)
        output_mode_row.addWidget(self.output_mode_platform_radio)
        output_mode_row.addStretch(1)

        cover_row = QHBoxLayout()
        cover_row.setContentsMargins(0, 0, 0, 0)
        cover_row.setSpacing(14)
        cover_label = QLabel("封面处理")
        cover_label.setObjectName("FieldLabel")
        cover_row.addWidget(cover_label)
        cover_row.addWidget(self.cover_enable_radio)
        cover_row.addWidget(self.cover_disable_radio)
        cover_row.addStretch(1)

        album_row = QHBoxLayout()
        album_row.setContentsMargins(0, 0, 0, 0)
        album_row.setSpacing(14)
        album_label = QLabel("专辑信息")
        album_label.setObjectName("FieldLabel")
        album_row.addWidget(album_label)
        album_row.addWidget(self.album_enable_radio)
        album_row.addWidget(self.album_disable_radio)
        album_row.addStretch(1)

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
        shared_layout.addLayout(output_mode_row)
        shared_layout.addWidget(self.output_mode_note)
        shared_layout.addWidget(self.output_field)
        shared_layout.addWidget(self.recursive_checkbox)
        shared_layout.addLayout(cover_row)
        shared_layout.addWidget(self.cover_note)
        shared_layout.addLayout(album_row)
        shared_layout.addWidget(self.album_note)
        shared_layout.addLayout(action_row)

        body_shell = QHBoxLayout()
        body_shell.setContentsMargins(0, 0, 0, 0)
        body_shell.setSpacing(14)

        nav_frame = QFrame()
        nav_frame.setObjectName("SidebarNavHost")
        nav_frame.setFixedWidth(252)
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(14, 16, 14, 16)
        nav_layout.setSpacing(10)
        nav_title = QLabel("导航")
        nav_title.setObjectName("SectionTitle")
        nav_subtitle = QLabel("像 Steam++ 一样，把常用任务拆成独立页面。")
        nav_subtitle.setObjectName("MutedText")
        nav_subtitle.setWordWrap(True)
        nav_layout.addWidget(nav_title)
        nav_layout.addWidget(nav_subtitle)

        self.workspace_nav = NavigationInterface(nav_frame, showMenuButton=False, showReturnButton=False, collapsible=False)
        self.workspace_nav.setMinimumWidth(220)
        nav_layout.addWidget(self.workspace_nav, 1)
        body_shell.addWidget(nav_frame, 0)

        workspace_host = QWidget()
        workspace_host.setObjectName("WorkspaceHost")
        workspace_layout = QVBoxLayout(workspace_host)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.workspace_stack = QStackedWidget()
        workspace_layout.addWidget(self.workspace_stack, 1)
        body_shell.addWidget(workspace_host, 1)
        body_layout.addLayout(body_shell, 1)

        tabs_card = QFrame()
        tabs_card.setObjectName("ConfigCard")
        tabs_layout = QVBoxLayout(tabs_card)
        tabs_layout.setContentsMargins(18, 16, 18, 16)
        tabs_layout.setSpacing(12)
        tabs_title = QLabel("平台解密")
        tabs_title.setObjectName("SectionTitle")
        tabs_hint = QLabel("每个平台都单独维护输入路径、格式规则、运行状态和最近失败原因。")
        tabs_hint.setObjectName("MutedText")
        tabs_hint.setWordWrap(True)
        self.platform_tabs = QTabWidget()
        self.platform_tabs.setDocumentMode(True)
        self.platform_tabs.setTabBar(NoWheelTabBar())
        tabs_layout.addWidget(tabs_title)
        tabs_layout.addWidget(tabs_hint)
        tabs_layout.addWidget(self.platform_tabs, 1)

        decrypt_page = QWidget()
        decrypt_layout = QVBoxLayout(decrypt_page)
        decrypt_layout.setContentsMargins(0, 0, 0, 0)
        decrypt_layout.setSpacing(0)
        decrypt_layout.addWidget(tabs_card, 1)

        def add_platform_tab(card: PlatformCard, title_text: str) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 8, 8, 8)
            page_layout.setSpacing(0)
            page_layout.addWidget(card, 1)
            self._tab_platform_ids.append(card.platform_id)
            self.platform_tabs.addTab(page, title_text)

        qq_card = PlatformCard("qq", "QQ音乐", "运行期解密，开始任务前会检查 QQ 音乐进程。")
        qq_card.add_format_combo("mflac", "mflac 输出格式", QQ_RULE_FORMATS)
        qq_card.add_format_combo("mgg", "mgg 输出格式", QQ_RULE_FORMATS)
        qq_card.add_format_combo("mmp4", "mmp4 输出格式", QQ_RULE_FORMATS)
        qq_card.add_transcode_profile_controls()
        add_platform_tab(qq_card, "QQ音乐")
        qq_card.add_extra_field("output_dir", "当前平台输出目录", directory=True)
        self._cards["qq"] = qq_card

        kuwo_card = PlatformCard("kuwo", "酷我音乐", "运行期解密，开始任务前会检查 kwmusic.exe 进程。")
        kuwo_card.add_format_combo("format_kwm", "kwm 输出格式", FORMATS)
        kuwo_card.add_extra_field("exe_path", "酷我程序路径（可选）", directory=False)
        kuwo_card.add_extra_field("signature_file", "签名文件路径", directory=False)
        kuwo_card.add_transcode_profile_controls()
        add_platform_tab(kuwo_card, "酷我音乐")
        kuwo_card.add_extra_field("output_dir", "当前平台输出目录", directory=True)
        self._cards["kuwo"] = kuwo_card

        kugou_card = PlatformCard("kugou", "酷狗音乐", "文件级离线解密，不要求 KuGou 运行。")
        kugou_card.add_format_combo("target_format_kgma", "kgma/kgm/vpr 输出格式", FORMATS)
        kugou_card.add_format_combo("target_format_kgg", "kgg 输出格式", FORMATS)
        kugou_card.add_extra_field("key_file", "kugou_key.xz 路径", directory=False)
        kugou_card.add_extra_field("kgg_db_path", "KGMusicV3.db 路径", directory=False)
        kugou_card.add_transcode_profile_controls()
        add_platform_tab(kugou_card, "酷狗音乐")
        kugou_card.add_extra_field("output_dir", "当前平台输出目录", directory=True)
        self._cards["kugou"] = kugou_card

        netease_card = PlatformCard("netease", "网易云音乐", "文件级离线解密，直接处理 .ncm 文件，不要求网易云音乐运行。")
        netease_card.add_format_combo("target_format_ncm", "ncm 输出格式", FORMATS)
        netease_card.add_transcode_profile_controls()
        add_platform_tab(netease_card, "网易云音乐")
        netease_card.add_extra_field("output_dir", "当前平台输出目录", directory=True)
        self._cards["netease"] = netease_card
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(0)
        settings_splitter = QSplitter(Qt.Orientation.Horizontal)
        settings_layout.addWidget(settings_splitter, 1)
        settings_splitter.addWidget(shared_card)

        settings_side = QWidget()
        settings_side_layout = QVBoxLayout(settings_side)
        settings_side_layout.setContentsMargins(0, 0, 0, 0)
        settings_side_layout.setSpacing(12)

        settings_summary_card = QFrame()
        settings_summary_card.setObjectName("ConfigCard")
        settings_summary_layout = QVBoxLayout(settings_summary_card)
        settings_summary_layout.setContentsMargins(18, 16, 18, 16)
        settings_summary_layout.setSpacing(8)
        settings_summary_title = QLabel("策略摘要")
        settings_summary_title.setObjectName("SectionTitle")
        settings_summary_body = QLabel("这一页只统一管理输出目录、封面、专辑信息和递归扫描策略。平台解密页只负责执行。")
        settings_summary_body.setObjectName("MutedText")
        settings_summary_body.setWordWrap(True)
        settings_summary_layout.addWidget(settings_summary_title)
        settings_summary_layout.addWidget(settings_summary_body)

        settings_tools_card = QFrame()
        settings_tools_card.setObjectName("ConfigCard")
        settings_tools_layout = QVBoxLayout(settings_tools_card)
        settings_tools_layout.setContentsMargins(18, 16, 18, 16)
        settings_tools_layout.setSpacing(10)
        settings_tools_title = QLabel("工具入口")
        settings_tools_title.setObjectName("SectionTitle")
        settings_tools_hint = QLabel("从这里快速打开输出目录、重载配置或查看使用技巧。")
        settings_tools_hint.setObjectName("MutedText")
        settings_tools_hint.setWordWrap(True)
        settings_tools_row = QHBoxLayout()
        settings_tools_row.setContentsMargins(0, 0, 0, 0)
        settings_tools_row.setSpacing(10)
        settings_open_output = QPushButton("打开输出目录")
        settings_open_output.setObjectName("SecondaryButton")
        settings_open_output.clicked.connect(self._open_output_dir)
        settings_reload = QPushButton("重新读取配置")
        settings_reload.setObjectName("GhostButton")
        settings_reload.clicked.connect(self._reload_config)
        settings_tips = QPushButton("使用技巧")
        settings_tips.setObjectName("GhostButton")
        settings_tips.clicked.connect(self._show_usage_tips)
        settings_tools_row.addWidget(settings_open_output)
        settings_tools_row.addWidget(settings_reload)
        settings_tools_row.addWidget(settings_tips)
        settings_tools_row.addStretch(1)
        settings_tools_layout.addWidget(settings_tools_title)
        settings_tools_layout.addWidget(settings_tools_hint)
        settings_tools_layout.addLayout(settings_tools_row)

        settings_notice_card = QFrame()
        settings_notice_card.setObjectName("NoticeCard")
        settings_notice_layout = QVBoxLayout(settings_notice_card)
        settings_notice_layout.setContentsMargins(18, 16, 18, 16)
        settings_notice_layout.setSpacing(8)
        settings_notice_title = QLabel("运行提示")
        settings_notice_title.setObjectName("SectionTitle")
        settings_notice_body = QLabel("QQ 音乐平台会在启动任务前检查进程和程序路径，而其他平台则以离线文件处理为主。")
        settings_notice_body.setObjectName("MutedText")
        settings_notice_body.setWordWrap(True)
        settings_notice_layout.addWidget(settings_notice_title)
        settings_notice_layout.addWidget(settings_notice_body)

        settings_side_layout.addWidget(settings_summary_card)
        settings_side_layout.addWidget(settings_tools_card)
        settings_side_layout.addWidget(settings_notice_card)
        settings_side_layout.addStretch(1)
        settings_splitter.addWidget(settings_side)
        settings_splitter.setStretchFactor(0, 3)
        settings_splitter.setStretchFactor(1, 2)

        transcode_page = QWidget()
        transcode_layout = QVBoxLayout(transcode_page)
        transcode_layout.setContentsMargins(0, 0, 0, 0)
        transcode_layout.setSpacing(12)
        transcode_hint_card = QFrame()
        transcode_hint_card.setObjectName("ConfigCard")
        transcode_hint_layout = QVBoxLayout(transcode_hint_card)
        transcode_hint_layout.setContentsMargins(18, 16, 18, 16)
        transcode_hint_layout.setSpacing(8)
        transcode_title = QLabel("批量转码")
        transcode_title.setObjectName("SectionTitle")
        transcode_hint = QLabel("对已存在的音频目录做批量格式转换，可同时配置多个输入目录、多条格式规则和 ffmpeg 音频参数。")
        transcode_hint.setObjectName("MutedText")
        transcode_hint.setWordWrap(True)
        transcode_hint_layout.addWidget(transcode_title)
        transcode_hint_layout.addWidget(transcode_hint)
        self.transcode_card = TranscodeBatchCard()
        transcode_layout.addWidget(transcode_hint_card)
        transcode_layout.addWidget(self.transcode_card, 1)

        log_card = QFrame()
        log_card.setObjectName("ConfigCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(18, 16, 18, 16)
        log_layout.setSpacing(12)
        queue_title = QLabel("队列与日志")
        queue_title.setObjectName("SectionTitle")
        self.queue_label = QLabel("最多同时运行 2 个平台任务，超出部分进入 FIFO 队列。")
        self.queue_label.setWordWrap(True)
        self.queue_label.setObjectName("MutedText")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogView")
        self.log_view.setMinimumHeight(260)
        self.log_view.setMaximumBlockCount(800)
        log_layout.addWidget(queue_title)
        log_layout.addWidget(self.queue_label)
        log_layout.addWidget(self.log_view, 1)

        logs_page = QWidget()
        logs_layout = QVBoxLayout(logs_page)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.setSpacing(0)
        logs_splitter = QSplitter(Qt.Orientation.Horizontal)
        logs_layout.addWidget(logs_splitter, 1)
        logs_splitter.addWidget(log_card)

        log_side = QWidget()
        log_side_layout = QVBoxLayout(log_side)
        log_side_layout.setContentsMargins(0, 0, 0, 0)
        log_side_layout.setSpacing(12)

        log_runtime_card = QFrame()
        log_runtime_card.setObjectName("ConfigCard")
        log_runtime_layout = QVBoxLayout(log_runtime_card)
        log_runtime_layout.setContentsMargins(18, 16, 18, 16)
        log_runtime_layout.setSpacing(8)
        log_runtime_title = QLabel("当前会话")
        log_runtime_title.setObjectName("SectionTitle")
        log_runtime_body = QLabel("右侧区域负责帮你快速看清队列压力、最近失败和平台运行状态。")
        log_runtime_body.setObjectName("MutedText")
        log_runtime_body.setWordWrap(True)
        log_runtime_layout.addWidget(log_runtime_title)
        log_runtime_layout.addWidget(log_runtime_body)

        log_tools_card = QFrame()
        log_tools_card.setObjectName("ConfigCard")
        log_tools_layout = QVBoxLayout(log_tools_card)
        log_tools_layout.setContentsMargins(18, 16, 18, 16)
        log_tools_layout.setSpacing(8)
        log_tools_title = QLabel("快捷入口")
        log_tools_title.setObjectName("SectionTitle")
        log_tools_body = QLabel("遇到异常时，可以从这里打开输出目录或小窗口技巧系统。")
        log_tools_body.setObjectName("MutedText")
        log_tools_body.setWordWrap(True)
        log_tools_row = QHBoxLayout()
        log_tools_row.setContentsMargins(0, 0, 0, 0)
        log_tools_row.setSpacing(10)
        log_tools_output = QPushButton("打开输出目录")
        log_tools_output.setObjectName("SecondaryButton")
        log_tools_output.clicked.connect(self._open_output_dir)
        log_tools_tips = QPushButton("使用技巧")
        log_tools_tips.setObjectName("GhostButton")
        log_tools_tips.clicked.connect(self._show_usage_tips)
        log_tools_row.addWidget(log_tools_output)
        log_tools_row.addWidget(log_tools_tips)
        log_tools_row.addStretch(1)
        log_tools_layout.addWidget(log_tools_title)
        log_tools_layout.addWidget(log_tools_body)
        log_tools_layout.addLayout(log_tools_row)

        log_side_layout.addWidget(log_runtime_card)
        log_side_layout.addWidget(log_tools_card)
        log_side_layout.addStretch(1)
        logs_splitter.addWidget(log_side)
        logs_splitter.setStretchFactor(0, 3)
        logs_splitter.setStretchFactor(1, 2)

        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(12)
        overview_layout.addWidget(hero_card)

        overview_split = QSplitter(Qt.Orientation.Horizontal)
        overview_layout.addWidget(overview_split, 1)

        overview_left = QWidget()
        overview_left_layout = QVBoxLayout(overview_left)
        overview_left_layout.setContentsMargins(0, 0, 0, 0)
        overview_left_layout.setSpacing(12)

        quick_card = QFrame()
        quick_card.setObjectName("ConfigCard")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(18, 16, 18, 16)
        quick_layout.setSpacing(10)
        quick_title = QLabel("快速开始")
        quick_title.setObjectName("SectionTitle")
        quick_body = QLabel("像 Steam++ 一样，常用功能在总览页给出直接入口。")
        quick_body.setObjectName("MutedText")
        quick_body.setWordWrap(True)
        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(10)
        quick_decrypt = QPushButton("平台解密")
        quick_decrypt.setObjectName("PrimaryButton")
        quick_decrypt.clicked.connect(lambda: self._switch_workspace_page("decrypt"))
        quick_transcode = QPushButton("批量转码")
        quick_transcode.setObjectName("SecondaryButton")
        quick_transcode.clicked.connect(lambda: self._switch_workspace_page("transcode"))
        quick_settings = QPushButton("设置")
        quick_settings.setObjectName("GhostButton")
        quick_settings.clicked.connect(lambda: self._switch_workspace_page("settings"))
        quick_row.addWidget(quick_decrypt)
        quick_row.addWidget(quick_transcode)
        quick_row.addWidget(quick_settings)
        quick_row.addStretch(1)
        quick_layout.addWidget(quick_title)
        quick_layout.addWidget(quick_body)
        quick_layout.addLayout(quick_row)

        overview_note = QFrame()
        overview_note.setObjectName("ConfigCard")
        overview_note_layout = QVBoxLayout(overview_note)
        overview_note_layout.setContentsMargins(18, 16, 18, 16)
        overview_note_layout.setSpacing(8)
        overview_title = QLabel("工作台说明")
        overview_title.setObjectName("SectionTitle")
        overview_text = QLabel("左侧是全局导航，右侧是当前页面内容。总览负责给你提供快速入口，设置页统一维护策略，日志页则负责观察运行状态。")
        overview_text.setObjectName("MutedText")
        overview_text.setWordWrap(True)
        overview_note_layout.addWidget(overview_title)
        overview_note_layout.addWidget(overview_text)

        overview_left_layout.addWidget(quick_card)
        overview_left_layout.addWidget(overview_note)
        overview_left_layout.addStretch(1)

        overview_right = QWidget()
        overview_right_layout = QVBoxLayout(overview_right)
        overview_right_layout.setContentsMargins(0, 0, 0, 0)
        overview_right_layout.setSpacing(12)

        platform_health_card = QFrame()
        platform_health_card.setObjectName("ConfigCard")
        platform_health_layout = QVBoxLayout(platform_health_card)
        platform_health_layout.setContentsMargins(18, 16, 18, 16)
        platform_health_layout.setSpacing(10)
        platform_health_title = QLabel("平台速览")
        platform_health_title.setObjectName("SectionTitle")
        platform_health_body = QLabel("QQ、酷我、酷狗、网易云的解密入口已在左侧分组，可直接切入执行。")
        platform_health_body.setObjectName("MutedText")
        platform_health_body.setWordWrap(True)
        platform_health_layout.addWidget(platform_health_title)
        platform_health_layout.addWidget(platform_health_body)
        for title_text, desc_text in (("QQ音乐", "运行期解密，需要检测进程"), ("酷我音乐", "kwmusic.exe 运行时可直接进入解密"), ("酷狗音乐", "离线文件解密，封装规则不同"), ("网易云音乐", "直接处理 ncm 文件")):
            item_frame = QFrame()
            item_frame.setObjectName("MiniCard")
            item_layout = QVBoxLayout(item_frame)
            item_layout.setContentsMargins(12, 10, 12, 10)
            item_layout.setSpacing(4)
            item_title = QLabel(title_text)
            item_title.setObjectName("FieldLabel")
            item_desc = QLabel(desc_text)
            item_desc.setObjectName("MutedText")
            item_desc.setWordWrap(True)
            item_layout.addWidget(item_title)
            item_layout.addWidget(item_desc)
            platform_health_layout.addWidget(item_frame)

        overview_right_layout.addWidget(platform_health_card)
        overview_right_layout.addStretch(1)
        overview_split.addWidget(overview_left)
        overview_split.addWidget(overview_right)
        overview_split.setStretchFactor(0, 3)
        overview_split.setStretchFactor(1, 2)
        overview_layout.addStretch(1)

        pages = {
            "overview": overview_page,
            "decrypt": decrypt_page,
            "transcode": transcode_page,
            "settings": settings_page,
            "logs": logs_page,
        }
        for key, page in pages.items():
            self._workspace_pages[key] = page
            self.workspace_stack.addWidget(page)

        self.workspace_nav.addItem("overview", FIF.HOME, "总览", onClick=lambda: self._switch_workspace_page("overview"), position=NavigationItemPosition.SCROLL)
        self.workspace_nav.addItem("decrypt", FIF.APPLICATION, "平台解密", onClick=lambda: self._switch_workspace_page("decrypt"), position=NavigationItemPosition.SCROLL)
        self.workspace_nav.addItem("transcode", FIF.SYNC, "批量转码", onClick=lambda: self._switch_workspace_page("transcode"), position=NavigationItemPosition.SCROLL)
        self.workspace_nav.addItem("settings", FIF.SETTING, "设置", onClick=lambda: self._switch_workspace_page("settings"), position=NavigationItemPosition.SCROLL)
        self.workspace_nav.addItem("logs", FIF.DOCUMENT, "日志", onClick=lambda: self._switch_workspace_page("logs"), position=NavigationItemPosition.SCROLL)
        self.workspace_nav.addItem("tips", FIF.HELP, "使用技巧", onClick=self._show_usage_tips, selectable=False, position=NavigationItemPosition.BOTTOM)
        self.workspace_nav.addItem("output", FIF.FOLDER, "打开输出目录", onClick=self._open_output_dir, selectable=False, position=NavigationItemPosition.BOTTOM)

        self.setStyleSheet(build_app_stylesheet())
        for widget in (hero_card, shared_card, tabs_card, transcode_hint_card, log_card, overview_note):
            apply_card_shadow(widget)
        self._switch_workspace_page("overview")

    def _connect_signals(self) -> None:
        self.output_field.button.clicked.connect(
            lambda: self._choose_path(self.output_field))
        self.output_mode_shared_radio.toggled.connect(self._update_output_mode_widgets)
        self.output_mode_platform_radio.toggled.connect(self._update_output_mode_widgets)
        self.save_button.clicked.connect(self._save_config_from_widgets)
        self.reload_button.clicked.connect(self._reload_config)
        self.open_output_button.clicked.connect(self._open_output_dir)
        self.header_tips_button.clicked.connect(self._show_usage_tips)
        self.header_output_button.clicked.connect(self._open_output_dir)
        self.header_reload_button.clicked.connect(self._reload_config)
        for platform_id, card in self._cards.items():
            card.input_field.button.clicked.connect(
                lambda _=False, pid=platform_id: self._choose_path(self._cards[pid].input_field))
            card.extra_field("output_dir").button.clicked.connect(
                lambda _=False, pid=platform_id: self._choose_path(self._cards[pid].extra_field("output_dir")))
            card.run_requested.connect(self._handle_platform_action)
            card.stop_requested.connect(self._handle_platform_stop)
            card.detail_requested.connect(self._handle_platform_detail)
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
        self.bridge.transcode_confirmation_request.connect(self._handle_transcode_confirmation_request)
        self.bridge.submission_result.connect(self._handle_submission_result)
        self.bridge.transcode_event.connect(self._handle_transcode_event)
        self.transcode_card.choose_input_requested.connect(self._handle_transcode_choose_input)
        self.transcode_card.choose_output_requested.connect(self._handle_transcode_choose_output)
        self.transcode_card.start_requested.connect(self._handle_transcode_start)


    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._acrylic_applied:
            QTimer.singleShot(0, self._ensure_acrylic_effect)

    def _ensure_acrylic_effect(self) -> None:
        if self._acrylic_applied or not self.isVisible():
            return
        self._acrylic_applied = apply_win10_acrylic(self)
    def _switch_workspace_page(self, key: str) -> None:
        page = self._workspace_pages.get(key)
        if page is None:
            return
        self.workspace_stack.setCurrentWidget(page)
        self.workspace_nav.setCurrentItem(key)

    def _platform_title(self, platform_id: str) -> str:
        return {"qq": "QQ音乐", "kuwo": "酷我音乐", "kugou": "酷狗音乐", "netease": "网易云音乐"}[platform_id]

    def _load_config_into_widgets(self) -> None:
        self.root_config, self.config = load_config(self.paths)
        shared = self.config["shared"]
        output_mode = str(shared.get("output_mode", "shared") or "shared").lower()
        if output_mode == "per_platform":
            self.output_mode_platform_radio.setChecked(True)
        else:
            self.output_mode_shared_radio.setChecked(True)
        self.output_field.setText(
            str(shared.get("output_dir", self.paths.output_dir)))
        self.recursive_checkbox.setChecked(bool(shared.get("recursive", True)))
        self._update_output_mode_widgets()
        if bool(shared.get("embed_cover_art", True)):
            self.cover_enable_radio.setChecked(True)
        else:
            self.cover_disable_radio.setChecked(True)
        if bool(shared.get("supplement_album_metadata", False)):
            self.album_enable_radio.setChecked(True)
        else:
            self.album_disable_radio.setChecked(True)

        qq = self.config["qq"]
        self._cards["qq"].input_field.setText(str(qq.get("input_dir", "")))
        self._cards["qq"].set_format_value("mflac", str(
            (qq.get("format_rules") or {}).get("mflac", "flac")))
        self._cards["qq"].set_format_value("mgg", str(
            (qq.get("format_rules") or {}).get("mgg", "m4a")))
        self._cards["qq"].set_format_value("mmp4", str(
            (qq.get("format_rules") or {}).get("mmp4", "m4a")))
        self._cards["qq"].set_transcode_profile(sample_rate_hz=qq.get("transcode_sample_rate_hz"), bitrate_kbps=qq.get("transcode_bitrate_kbps"))
        self._cards["qq"].set_transcode_enabled(bool(qq.get("transcode_enabled", True)))
        kuwo = self.config["kuwo"]
        self._cards["qq"].extra_field("output_dir").setText(str(qq.get("output_dir", pathlib.Path(self.paths.output_dir) / "qq")))
        self._cards["kuwo"].input_field.setText(str(kuwo.get("input_dir", "")))
        self._cards["kuwo"].set_format_value(
            "format_kwm", str(kuwo.get("format_kwm", "auto")))
        self._cards["kuwo"].set_transcode_profile(sample_rate_hz=kuwo.get("transcode_sample_rate_hz"), bitrate_kbps=kuwo.get("transcode_bitrate_kbps"))
        self._cards["kuwo"].set_transcode_enabled(bool(kuwo.get("transcode_enabled", True)))
        self._cards["kuwo"].extra_field("exe_path").setText(
            str(kuwo.get("exe_path", "")))
        self._cards["kuwo"].extra_field("signature_file").setText(
            str(kuwo.get("signature_file", "")))
        self._cards["kuwo"].extra_field("output_dir").setText(
            str(kuwo.get("output_dir", pathlib.Path(self.paths.output_dir) / "kuwo")))

        kugou = self.config["kugou"]
        self._cards["kugou"].input_field.setText(
            str(kugou.get("input_dir", "")))
        self._cards["kugou"].set_format_value(
            "target_format_kgma", str(kugou.get("target_format_kgma", "auto")))
        self._cards["kugou"].set_format_value(
            "target_format_kgg", str(kugou.get("target_format_kgg", "auto")))
        self._cards["kugou"].set_transcode_profile(sample_rate_hz=kugou.get("transcode_sample_rate_hz"), bitrate_kbps=kugou.get("transcode_bitrate_kbps"))
        self._cards["kugou"].set_transcode_enabled(bool(kugou.get("transcode_enabled", True)))
        self._cards["kugou"].extra_field("key_file").setText(
            str(kugou.get("key_file", "")))
        self._cards["kugou"].extra_field("kgg_db_path").setText(
            str(kugou.get("kgg_db_path", "")))
        self._cards["kugou"].extra_field("output_dir").setText(
            str(kugou.get("output_dir", pathlib.Path(self.paths.output_dir) / "kugou")))
        netease = self.config["netease"]
        self._cards["netease"].input_field.setText(
            str(netease.get("input_dir", "")))
        self._cards["netease"].set_format_value(
            "target_format_ncm", str(netease.get("target_format_ncm", "auto")))
        self._cards["netease"].set_transcode_profile(sample_rate_hz=netease.get("transcode_sample_rate_hz"), bitrate_kbps=netease.get("transcode_bitrate_kbps"))
        self._cards["netease"].set_transcode_enabled(bool(netease.get("transcode_enabled", True)))

        self._cards["netease"].extra_field("output_dir").setText(
            str(netease.get("output_dir", pathlib.Path(self.paths.output_dir) / "netease")))

        transcode_batch = self.config.get("transcode_batch", {})
        self.transcode_card.set_input_paths(list(transcode_batch.get("input_paths", [])))
        self.transcode_card.set_output_dir(str(transcode_batch.get("output_dir", pathlib.Path(self.paths.output_dir) / "transcode")))
        self.transcode_card.set_recursive(bool(transcode_batch.get("recursive", True)))
        self.transcode_card.set_rules(list(transcode_batch.get("rules", [])))

    def _save_config_from_widgets(self, *, announce: bool = True) -> None:
        shared = {
            "output_mode": "per_platform" if self.output_mode_platform_radio.isChecked() else "shared",
            "output_dir": self.output_field.text() or str(self.paths.output_dir),
            "cli_collision_policy": "suffix",
            "recursive": self.recursive_checkbox.isChecked(),
            "transcode_enabled": any(card.transcode_enabled() for card in self._cards.values()),
            "embed_cover_art": self.cover_enable_radio.isChecked(),
            "supplement_album_metadata": self.album_enable_radio.isChecked(),
        }
        qq = {
            "input_dir": self._cards["qq"].input_field.text(),
            "process_match": "qqmusic",
            "output_dir": self._cards["qq"].extra_field("output_dir").text(),
            "transcode_enabled": self._cards["qq"].transcode_enabled(),
            "auto_transcode_after_decode": bool(self.config.get("qq", {}).get("auto_transcode_after_decode", False)),
            "transcode_sample_rate_hz": self._cards["qq"].transcode_sample_rate_hz(),
            "transcode_bitrate_kbps": self._cards["qq"].transcode_bitrate_kbps(),
            "format_rules": {
                "mflac": self._cards["qq"].format_value("mflac"),
                "mgg": self._cards["qq"].format_value("mgg"),
                "mmp4": self._cards["qq"].format_value("mmp4"),
            },
        }
        kuwo = {
            "input_dir": self._cards["kuwo"].input_field.text(),
            "process_name": "kwmusic.exe",
            "output_dir": self._cards["kuwo"].extra_field("output_dir").text(),
            "exe_path": self._cards["kuwo"].extra_field("exe_path").text(),
            "signature_file": self._cards["kuwo"].extra_field("signature_file").text(),
            "transcode_enabled": self._cards["kuwo"].transcode_enabled(),
            "format_kwm": self._cards["kuwo"].format_value("format_kwm"),
            "auto_transcode_after_decode": bool(self.config.get("kuwo", {}).get("auto_transcode_after_decode", False)),
            "transcode_sample_rate_hz": self._cards["kuwo"].transcode_sample_rate_hz(),
            "transcode_bitrate_kbps": self._cards["kuwo"].transcode_bitrate_kbps(),
        }
        kugou = {
            "input_dir": self._cards["kugou"].input_field.text(),
            "output_dir": self._cards["kugou"].extra_field("output_dir").text(),
            "kgg_db_path": self._cards["kugou"].extra_field("kgg_db_path").text(),
            "key_file": self._cards["kugou"].extra_field("key_file").text(),
            "transcode_enabled": self._cards["kugou"].transcode_enabled(),
            "target_format_kgma": self._cards["kugou"].format_value("target_format_kgma"),
            "target_format_kgg": self._cards["kugou"].format_value("target_format_kgg"),
            "auto_transcode_after_decode": bool(self.config.get("kugou", {}).get("auto_transcode_after_decode", False)),
            "transcode_sample_rate_hz": self._cards["kugou"].transcode_sample_rate_hz(),
            "transcode_bitrate_kbps": self._cards["kugou"].transcode_bitrate_kbps(),
        }
        netease = {
            "input_dir": self._cards["netease"].input_field.text(),
            "output_dir": self._cards["netease"].extra_field("output_dir").text(),
            "transcode_enabled": self._cards["netease"].transcode_enabled(),
            "target_format_ncm": self._cards["netease"].format_value("target_format_ncm"),
            "auto_transcode_after_decode": bool(self.config.get("netease", {}).get("auto_transcode_after_decode", False)),
            "transcode_sample_rate_hz": self._cards["netease"].transcode_sample_rate_hz(),
            "transcode_bitrate_kbps": self._cards["netease"].transcode_bitrate_kbps(),
        }
        transcode_batch = {
            "input_paths": self.transcode_card.input_paths(),
            "output_dir": self.transcode_card.output_dir() or str(self.paths.output_dir / "transcode"),
            "recursive": self.transcode_card.recursive(),
            "max_workers": int(self.config.get("transcode_batch", {}).get("max_workers", 2) or 2),
            "rules": self.transcode_card.rules(),
        }
        self.config = {"shared": shared, "qq": qq,
                       "kuwo": kuwo, "kugou": kugou, "netease": netease, "transcode_batch": transcode_batch}
        save_config(self.paths, self.root_config, self.config)
        if announce:
            self._append_log("配置已保存。")

    def _reload_config(self) -> None:
        self._load_config_into_widgets()
        self._append_log("已重新读取配置文件。")

    def _open_output_dir(self) -> None:
        if self.output_mode_platform_radio.isChecked() and self._tab_platform_ids:
            current_index = max(0, self.platform_tabs.currentIndex())
            platform_id = self._tab_platform_ids[current_index]
            output_dir = self._resolve_output_dir(platform_id)
        else:
            output_dir = pathlib.Path(
                self.output_field.text() or str(self.paths.output_dir)
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(output_dir.as_uri())



    def _handle_transcode_choose_input(self, index: int) -> None:
        start = self.transcode_card.input_path_at(index) or str(self.paths.root_dir)
        selected = QFileDialog.getExistingDirectory(self, "选择转码输入目录", start)
        if selected:
            self.transcode_card.set_input_path(index, selected)

    def _handle_transcode_choose_output(self) -> None:
        start = self.transcode_card.output_dir() or str(self.paths.output_dir / "transcode")
        selected = QFileDialog.getExistingDirectory(self, "选择转码输出目录", start)
        if selected:
            self.transcode_card.set_output_dir(selected)

    def _handle_transcode_start(self) -> None:
        if self._transcode_running:
            self._append_log("[批量转码] 当前已有任务在运行，请稍候。")
            return
        self._save_config_from_widgets(announce=False)
        input_paths = [pathlib.Path(item) for item in self.transcode_card.input_paths()]
        if not input_paths:
            self._show_message("缺少输入目录", "请至少添加一个转码输入目录")
            return
        output_dir = pathlib.Path(self.transcode_card.output_dir() or (self.paths.output_dir / "transcode"))
        rules = self.transcode_card.rules()
        self._transcode_running = True
        self.transcode_card.set_running(True)
        self.transcode_card.status_label.setText("状态：准备转码任务")
        self.transcode_card.detail_label.setText("说明：正在生成批量转码计划")
        self._append_log("[批量转码] 正在准备任务...")
        threading.Thread(
            target=self._run_transcode_batch_task,
            args=(input_paths, output_dir, rules, self.transcode_card.recursive(), int(self.config.get("transcode_batch", {}).get("max_workers", 2) or 2)),
            daemon=True,
        ).start()

    def _run_transcode_batch_task(
        self,
        input_paths: list[pathlib.Path],
        output_dir: pathlib.Path,
        rules: list[dict[str, str]],
        recursive: bool,
        max_workers: int,
    ) -> None:
        try:
            run_transcode_batch(
                input_paths=input_paths,
                output_dir=output_dir,
                rules=rules,
                recursive=recursive,
                max_workers=max_workers,
                event_sink=lambda event_name, payload: self.bridge.transcode_event.emit((event_name, dict(payload))),
            )
        except Exception as exc:
            self.bridge.transcode_event.emit(("job_failed", {"input_path": "批量转码任务", "reason": str(exc), "output_path": "", "target_format": "", "elapsed_sec": 0}))
            self.bridge.transcode_event.emit(("batch_finished", {"success_count": 0, "failed_count": 1, "elapsed_sec": 0, "total_jobs": 0}))

    def _handle_transcode_event(self, payload: object) -> None:
        event_name, data = payload if isinstance(payload, tuple) and len(payload) == 2 else ("unknown", {})
        data = data if isinstance(data, dict) else {}
        self.transcode_card.apply_event(str(event_name), data)
        if event_name == "plan_ready":
            self._append_log(f"[批量转码] 已生成 {int(data.get('total_jobs', 0) or 0)} 个任务，并发 {int(data.get('worker_count', 0) or 0)} 路。")
        elif event_name == "warning":
            self._append_log(f"[批量转码] {data.get('message', '')}")
        elif event_name == "job_started":
            self._append_log(f"[批量转码] 开始：{pathlib.Path(str(data.get('input_path', '') or '')).name} -> {data.get('target_format', '')}")
        elif event_name == "job_succeeded":
            self._append_log(f"[批量转码] 完成：{pathlib.Path(str(data.get('output_path', '') or '')).name}")
        elif event_name == "job_failed":
            self._append_log(f"[批量转码] 失败：{pathlib.Path(str(data.get('input_path', '') or '')).name}：{data.get('reason', '')}")
        elif event_name == "batch_finished":
            self._transcode_running = False
            self.transcode_card.set_running(False)
            self._append_log(
                f"[批量转码] 已结束：成功 {int(data.get('success_count', 0) or 0)}，失败 {int(data.get('failed_count', 0) or 0)}，耗时 {data.get('elapsed_sec', 0)}s"
            )

    def _update_output_mode_widgets(self) -> None:
        per_platform = self.output_mode_platform_radio.isChecked()
        self.output_field.setVisible(not per_platform)
        for card in self._cards.values():
            card.extra_field("output_dir").setVisible(per_platform)
        if per_platform:
            self.output_field.label.setText("基础输出目录")
            self.output_mode_note.setText(
                "分平台模式已启用：每个平台都可以在各自标签页里设置独立的输出目录。"
            )
        else:
            self.output_field.label.setText("共享输出目录")
            self.output_mode_note.setText("共享模式已启用：所有平台共用同一个输出目录。")

    def _resolve_output_dir(self, platform_id: str) -> pathlib.Path:
        base_output = pathlib.Path(self.output_field.text() or str(self.paths.output_dir))
        if self.output_mode_platform_radio.isChecked():
            configured = self._cards[platform_id].extra_field("output_dir").text()
            if configured:
                return pathlib.Path(configured)
            return base_output / platform_id
        return base_output

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
        output_dir = self._resolve_output_dir(platform_id)
        settings = dict(self.config[platform_id])
        settings["transcode_enabled"] = bool(self.config.get(platform_id, {}).get("transcode_enabled", True))
        settings["embed_cover_art"] = bool(self.config.get("shared", {}).get("embed_cover_art", True))
        settings["supplement_album_metadata"] = bool(self.config.get("shared", {}).get("supplement_album_metadata", False))
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

    def _handle_platform_detail(self, platform_id: str) -> None:
        card = self._cards.get(platform_id)
        if card is None:
            return
        json_path_text, txt_path_text = card.detail_paths()
        detail_payload = self._build_detail_payload(json_path_text, txt_path_text)
        if detail_payload is None:
            self._show_message("暂无详情", "当前平台还没有可查看的批次详情。")
            return
        dialog = BatchDetailDialog(
            f"{self._platform_title(platform_id)} 批次详情",
            summary=detail_payload.get("summary"),
            rows=detail_payload.get("rows"),
            fallback_text=detail_payload.get("fallback_text"),
            parent=self,
        )
        dialog.exec()

    def _show_usage_tips(self) -> None:
        dialog = UsageTipsDialog(USAGE_TIPS, self)
        dialog.exec()

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

    def _resolve_transcode_confirmation(self, platform_id: str, payload: dict[str, Any]) -> tuple[bool, bool] | None:
        event = threading.Event()
        holder: dict[str, Any] = {"should_transcode": False, "remember_choice": False}
        self.bridge.transcode_confirmation_request.emit((event, holder, platform_id, dict(payload)))
        event.wait()
        return bool(holder.get("should_transcode", False)), bool(holder.get("remember_choice", False))

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

    def _handle_transcode_confirmation_request(self, payload: object) -> None:
        event, holder, platform_id, data = payload
        platform_title = self._platform_title(str(platform_id))
        ready_count = int(data.get("ready_count", 0) or 0)
        pending_count = int(data.get("pending_count", 0) or 0)
        transcode_enabled_setting = bool(data.get("transcode_enabled_setting", True))
        checkbox = QCheckBox("下次该平台解码完成后直接转码，不再提醒")
        checkbox.setChecked(bool(self.config.get(str(platform_id), {}).get("auto_transcode_after_decode", False)))
        box = QMessageBox(self)
        box.setWindowTitle(f"{platform_title} 解码完成")
        box.setIcon(QMessageBox.Icon.Question if pending_count > 0 else QMessageBox.Icon.Information)
        box.setText(f"{platform_title} 已完成解码。")
        if pending_count > 0:
            box.setInformativeText(
                f"共 {ready_count} 个文件完成解码，其中 {pending_count} 个文件需要按当前设置统一转码。是否现在开始转码？"
            )
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            box.setCheckBox(checkbox)
        else:
            if transcode_enabled_setting:
                box.setInformativeText(
                    f"共 {ready_count} 个文件完成解码。当前批次无需转码，解码结果将直接输出。"
                )
            else:
                box.setInformativeText(
                    f"共 {ready_count} 个文件完成解码。当前处于仅解码模式，本批不会转码。"
                )
            checkbox.setEnabled(False)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setDefaultButton(QMessageBox.StandardButton.Ok)
        choice = box.exec()
        should_transcode = pending_count > 0 and choice == QMessageBox.StandardButton.Yes
        remember_choice = should_transcode and checkbox.isChecked()
        if bool(self.config.get(str(platform_id), {}).get("auto_transcode_after_decode", False)) != remember_choice:
            self.config[str(platform_id)]["auto_transcode_after_decode"] = remember_choice
            save_config(self.paths, self.root_config, self.config)
        holder["should_transcode"] = should_transcode
        holder["remember_choice"] = remember_choice
        event.set()

    def _build_detail_payload(self, json_path_text: str, txt_path_text: str) -> dict[str, Any] | None:
        json_path = pathlib.Path(json_path_text) if json_path_text else pathlib.Path()
        txt_path = pathlib.Path(txt_path_text) if txt_path_text else pathlib.Path()

        if json_path and json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                summary = dict(payload.get("summary", {}) or {})
                results = list(payload.get("results", []) or [])
                rows: list[dict[str, Any]] = []
                for item in results:
                    input_name = pathlib.Path(str(item.get("input_path", "") or "")).name or "未知文件"
                    output_name = pathlib.Path(str(item.get("output_path", "") or "")).name if item.get("output_path") else ""
                    reason = str(item.get("reason", "") or "").strip()
                    ok = bool(item.get("ok", False))
                    skipped = bool(item.get("skipped", False))
                    reason_zh, reason_en = _bilingual_reason(reason, ok=ok, skipped=skipped)
                    rows.append(
                        {
                            "status_text": _status_label(ok, skipped),
                            "input_name": input_name,
                            "output_name": output_name or ("已存在结果" if skipped else "无"),
                            "reason_zh": reason_zh,
                            "reason_en": reason_en,
                            "raw_reason": reason,
                        }
                    )
                return {"summary": summary, "rows": rows, "fallback_text": None}
            except Exception:
                pass

        if txt_path and txt_path.exists():
            try:
                return {"summary": {}, "rows": [], "fallback_text": txt_path.read_text(encoding="utf-8")}
            except Exception:
                try:
                    return {"summary": {}, "rows": [], "fallback_text": txt_path.read_text(encoding="utf-8-sig")}
                except Exception:
                    return {"summary": {}, "rows": [], "fallback_text": txt_path.read_text(errors="ignore")}
        return None

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
            self._cards[platform_id].status_label.setText("状态：失败")
            self._cards[platform_id].message_label.setText(f"说明：{error or '任务未提交'}")
            self._cards[platform_id].failed_file_label.setText("最近失败：启动前检查")
            self._cards[platform_id].failed_reason_label.setText(f"失败原因：{error or '任务未提交'}")
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
        scrollbar = self.log_view.verticalScrollBar()
        previous_value = scrollbar.value()
        was_at_bottom = previous_value >= max(0, scrollbar.maximum() - 4)
        self.log_view.appendPlainText(message)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))

    def _show_message(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    paths = RuntimePaths.discover()
    runtime_root = paths.root_dir
    icon_path = runtime_root / "封面" / "封面.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    root_config, config = load_config(paths)
    shared = config.get("shared", {}) if isinstance(config, dict) else {}
    always_run_as_admin = bool(shared.get("always_run_as_admin", False))
    relaunch_error: str | None = None

    if not is_running_as_admin() and always_run_as_admin:
        relaunched, relaunch_error = relaunch_current_process_as_admin()
        if relaunched:
            return 0

    if not is_running_as_admin():
        dialog = AdminRequiredDialog(
            relaunch_error=relaunch_error,
            always_enabled=always_run_as_admin,
        )
        dialog.exec()
        action = dialog.selected_action if dialog.result() == QDialog.DialogCode.Accepted else ADMIN_ACTION_CONTINUE
        if action == ADMIN_ACTION_ALWAYS:
            config.setdefault("shared", {})["always_run_as_admin"] = True
            save_config(paths, root_config, config)
            action = ADMIN_ACTION_RELAUNCH
        if action == ADMIN_ACTION_RELAUNCH:
            relaunched, relaunch_error = relaunch_current_process_as_admin()
            if relaunched:
                return 0
            QMessageBox.warning(None, "提权启动失败", relaunch_error or "无法以管理员身份重新启动程序。将继续以当前权限运行。")

    window = MainWindow()
    window.show()
    QTimer.singleShot(120, lambda: StartupNoticeDialog(window).exec())
    return app.exec()

