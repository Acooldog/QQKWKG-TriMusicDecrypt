from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.Application.transcode_batch_service import (
    ALL_SOURCE_FORMAT,
    TRANSCODE_SOURCE_FORMATS,
    TRANSCODE_TARGET_FORMATS,
)


class _RoundButton(QPushButton):
    def __init__(self, text: str, *, danger: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("DangerRoundButton" if danger else "RoundButton")
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class _InputPathRow(QFrame):
    choose_requested = Signal(object)
    add_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("选择要批量转码的输入目录")
        self.choose_button = QPushButton("选择目录")
        self.choose_button.setObjectName("SecondaryButton")
        self.add_button = _RoundButton("+")
        self.remove_button = _RoundButton("-", danger=True)

        layout.addWidget(self.edit, 1)
        layout.addWidget(self.choose_button)
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)

        self.choose_button.clicked.connect(lambda: self.choose_requested.emit(self))
        self.add_button.clicked.connect(self.add_requested.emit)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(value)


class _RuleRow(QFrame):
    add_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.source_combo = QComboBox()
        self.source_combo.addItems(list(TRANSCODE_SOURCE_FORMATS))
        self.target_combo = QComboBox()
        self.target_combo.addItems(list(TRANSCODE_TARGET_FORMATS))
        self.add_button = _RoundButton("+")
        self.remove_button = _RoundButton("-", danger=True)

        layout.addWidget(QLabel("输入格式"))
        layout.addWidget(self.source_combo, 1)
        layout.addWidget(QLabel("输出格式"))
        layout.addWidget(self.target_combo, 1)
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)

        self.add_button.clicked.connect(self.add_requested.emit)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))

    def value(self) -> dict[str, str]:
        return {
            "source_format": self.source_combo.currentText().strip() or ALL_SOURCE_FORMAT,
            "target_format": self.target_combo.currentText().strip() or "m4a",
        }

    def set_value(self, source_format: str, target_format: str) -> None:
        if source_format in [self.source_combo.itemText(i) for i in range(self.source_combo.count())]:
            self.source_combo.setCurrentText(source_format)
        else:
            self.source_combo.setCurrentText(ALL_SOURCE_FORMAT)
        if target_format in [self.target_combo.itemText(i) for i in range(self.target_combo.count())]:
            self.target_combo.setCurrentText(target_format)
        else:
            self.target_combo.setCurrentText("m4a")


class TranscodeBatchCard(QFrame):
    choose_input_requested = Signal(int)
    choose_output_requested = Signal()
    start_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ConfigCard")
        self._input_rows: list[_InputPathRow] = []
        self._rule_rows: list[_RuleRow] = []
        self._running = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("批量转码")
        title.setObjectName("SectionTitle")
        subtitle = QLabel("使用软件内置 ffmpeg 进行批量转码。支持多个输入目录和多条格式规则，任务会按队列并发执行。")
        subtitle.setObjectName("MutedText")
        subtitle.setWordWrap(True)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        path_page = QWidget()
        path_layout = QVBoxLayout(path_page)
        path_layout.setContentsMargins(10, 10, 10, 10)
        path_layout.setSpacing(10)
        path_tip = QLabel("可以添加多个输入目录，统一输出到一个转码目录。")
        path_tip.setObjectName("MutedText")
        path_tip.setWordWrap(True)
        path_layout.addWidget(path_tip)
        self.input_rows_layout = QVBoxLayout()
        self.input_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.input_rows_layout.setSpacing(8)
        path_layout.addLayout(self.input_rows_layout)

        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(8)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择转码输出目录")
        self.output_button = QPushButton("选择输出目录")
        self.output_button.setObjectName("SecondaryButton")
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        path_layout.addLayout(output_row)

        self.recursive_checkbox = QCheckBox("递归扫描输入目录中的子目录")
        path_layout.addWidget(self.recursive_checkbox)
        path_layout.addStretch(1)

        rule_page = QWidget()
        rule_layout = QVBoxLayout(rule_page)
        rule_layout.setContentsMargins(10, 10, 10, 10)
        rule_layout.setSpacing(10)
        rule_tip = QLabel("“全部”表示把所有支持的输入格式都转成指定输出格式。可以添加多条规则并行处理。")
        rule_tip.setObjectName("MutedText")
        rule_tip.setWordWrap(True)
        rule_layout.addWidget(rule_tip)
        self.rule_rows_layout = QVBoxLayout()
        self.rule_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rule_rows_layout.setSpacing(8)
        rule_layout.addLayout(self.rule_rows_layout)
        rule_layout.addStretch(1)

        self.tabs.addTab(path_page, "转码路径")
        self.tabs.addTab(rule_page, "格式规则")

        self.status_label = QLabel("状态：空闲")
        self.status_label.setWordWrap(True)
        self.summary_label = QLabel("队列：尚未开始")
        self.summary_label.setObjectName("MutedText")
        self.summary_label.setWordWrap(True)
        self.detail_label = QLabel("说明：等待开始")
        self.detail_label.setObjectName("MutedText")
        self.detail_label.setWordWrap(True)

        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.start_requested.emit)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.tabs)
        root.addWidget(self.status_label)
        root.addWidget(self.summary_label)
        root.addWidget(self.detail_label)
        root.addWidget(self.start_button)

        self.output_button.clicked.connect(self.choose_output_requested.emit)
        self.add_input_row()
        self.add_rule_row()
        self._refresh_row_controls()

    def add_input_row(self, value: str = "") -> None:
        row = _InputPathRow()
        row.set_text(value)
        row.choose_requested.connect(self._handle_input_choose)
        row.add_requested.connect(lambda: self.add_input_row())
        row.remove_requested.connect(self._remove_input_row)
        self._input_rows.append(row)
        self.input_rows_layout.addWidget(row)
        self._refresh_row_controls()

    def add_rule_row(self, source_format: str = ALL_SOURCE_FORMAT, target_format: str = "m4a") -> None:
        row = _RuleRow()
        row.set_value(source_format, target_format)
        row.add_requested.connect(lambda: self.add_rule_row())
        row.remove_requested.connect(self._remove_rule_row)
        self._rule_rows.append(row)
        self.rule_rows_layout.addWidget(row)
        self._refresh_row_controls()

    def set_input_paths(self, values: list[str]) -> None:
        for row in list(self._input_rows):
            self.input_rows_layout.removeWidget(row)
            row.deleteLater()
        self._input_rows.clear()
        for value in values or [""]:
            self.add_input_row(str(value))
        self._refresh_row_controls()

    def input_paths(self) -> list[str]:
        return [row.text() for row in self._input_rows if row.text()]

    def input_path_at(self, index: int) -> str:
        if 0 <= index < len(self._input_rows):
            return self._input_rows[index].text()
        return ""

    def set_input_path(self, index: int, value: str) -> None:
        if 0 <= index < len(self._input_rows):
            self._input_rows[index].set_text(value)

    def set_rules(self, rules: list[dict[str, str]]) -> None:
        for row in list(self._rule_rows):
            self.rule_rows_layout.removeWidget(row)
            row.deleteLater()
        self._rule_rows.clear()
        if not rules:
            rules = [{"source_format": ALL_SOURCE_FORMAT, "target_format": "m4a"}]
        for item in rules:
            self.add_rule_row(str(item.get("source_format", ALL_SOURCE_FORMAT)), str(item.get("target_format", "m4a")))
        self._refresh_row_controls()

    def rules(self) -> list[dict[str, str]]:
        return [row.value() for row in self._rule_rows]

    def set_output_dir(self, value: str) -> None:
        self.output_edit.setText(value)

    def output_dir(self) -> str:
        return self.output_edit.text().strip()

    def set_recursive(self, value: bool) -> None:
        self.recursive_checkbox.setChecked(bool(value))

    def recursive(self) -> bool:
        return self.recursive_checkbox.isChecked()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.start_button.setEnabled(not self._running)
        for row in self._input_rows:
            row.edit.setEnabled(not self._running)
            row.choose_button.setEnabled(not self._running)
            row.add_button.setEnabled(not self._running)
            row.remove_button.setEnabled(not self._running and len(self._input_rows) > 1)
        for row in self._rule_rows:
            row.source_combo.setEnabled(not self._running)
            row.target_combo.setEnabled(not self._running)
            row.add_button.setEnabled(not self._running)
            row.remove_button.setEnabled(not self._running and len(self._rule_rows) > 1)
        self.output_edit.setEnabled(not self._running)
        self.output_button.setEnabled(not self._running)
        self.recursive_checkbox.setEnabled(not self._running)

    def apply_event(self, event_name: str, payload: dict[str, Any]) -> None:
        if event_name == "plan_ready":
            total_jobs = int(payload.get("total_jobs", 0) or 0)
            worker_count = int(payload.get("worker_count", 0) or 0)
            self.status_label.setText("状态：已生成转码计划")
            self.summary_label.setText(
                f"队列：共 {total_jobs} 个任务，并发 {worker_count} 路"
            )
            self.detail_label.setText(f"说明：输出目录 {payload.get('output_dir', '')}")
        elif event_name == "warning":
            self.detail_label.setText(f"说明：{payload.get('message', '')}")
        elif event_name == "job_started":
            self.status_label.setText("状态：正在转码")
            self.detail_label.setText(
                f"说明：{payload.get('input_path', '')} -> {payload.get('target_format', '')}"
            )
        elif event_name == "queue_progress":
            queued = int(payload.get("queued", 0) or 0)
            running = int(payload.get("running", 0) or 0)
            completed = int(payload.get("completed", 0) or 0)
            total_jobs = int(payload.get("total_jobs", 0) or 0)
            self.summary_label.setText(
                f"队列：待处理 {queued}，执行中 {running}，已完成 {completed} / {total_jobs}"
            )
        elif event_name == "job_succeeded":
            self.detail_label.setText(
                f"说明：已完成 {payload.get('output_path', '')}（{payload.get('elapsed_sec', 0)}s）"
            )
        elif event_name == "job_failed":
            self.detail_label.setText(
                f"说明：失败 {payload.get('input_path', '')}：{payload.get('reason', '')}"
            )
        elif event_name == "batch_finished":
            self.status_label.setText("状态：转码完成")
            self.summary_label.setText(
                f"队列：成功 {payload.get('success_count', 0)}，失败 {payload.get('failed_count', 0)}，总耗时 {payload.get('elapsed_sec', 0)}s"
            )
            self.detail_label.setText("说明：批量转码任务已结束")

    def _refresh_row_controls(self) -> None:
        for row in self._input_rows:
            row.remove_button.setEnabled(len(self._input_rows) > 1 and not self._running)
        for row in self._rule_rows:
            row.remove_button.setEnabled(len(self._rule_rows) > 1 and not self._running)

    def _handle_input_choose(self, row: object) -> None:
        try:
            index = self._input_rows.index(row)
        except ValueError:
            return
        self.choose_input_requested.emit(index)

    def _remove_input_row(self, row: object) -> None:
        if len(self._input_rows) <= 1:
            return
        try:
            index = self._input_rows.index(row)
        except ValueError:
            return
        widget = self._input_rows.pop(index)
        self.input_rows_layout.removeWidget(widget)
        widget.deleteLater()
        self._refresh_row_controls()

    def _remove_rule_row(self, row: object) -> None:
        if len(self._rule_rows) <= 1:
            return
        try:
            index = self._rule_rows.index(row)
        except ValueError:
            return
        widget = self._rule_rows.pop(index)
        self.rule_rows_layout.removeWidget(widget)
        widget.deleteLater()
        self._refresh_row_controls()
