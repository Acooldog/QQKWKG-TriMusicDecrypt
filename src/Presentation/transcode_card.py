from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
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
    TRANSCODE_BITRATE_OPTIONS,
    TRANSCODE_SAMPLE_RATE_OPTIONS,
    TRANSCODE_SOURCE_FORMATS,
    TRANSCODE_TARGET_FORMATS,
)
from src.Presentation.modern_widgets import AnimatedProgressBar, StatusPill, apply_card_shadow


class _RoundButton(QPushButton):
    def __init__(self, text: str, *, danger: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("DangerRoundButton" if danger else "RoundButton")
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class _InputPathRow(QFrame):
    choose_requested = Signal(object)
    add_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("MiniCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
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
        self.edit.setCursorPosition(0)


class _RuleRow(QFrame):
    add_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("MiniCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.source_combo = QComboBox()
        self.source_combo.addItems(list(TRANSCODE_SOURCE_FORMATS))
        self.target_combo = QComboBox()
        self.target_combo.addItems(list(TRANSCODE_TARGET_FORMATS))
        self.add_button = _RoundButton("+")
        self.remove_button = _RoundButton("-", danger=True)

        top_row.addWidget(QLabel("输入格式"))
        top_row.addWidget(self.source_combo, 1)
        top_row.addWidget(QLabel("输出格式"))
        top_row.addWidget(self.target_combo, 1)
        top_row.addWidget(self.add_button)
        top_row.addWidget(self.remove_button)

        option_grid = QGridLayout()
        option_grid.setContentsMargins(0, 0, 0, 0)
        option_grid.setHorizontalSpacing(10)
        option_grid.setVerticalSpacing(6)

        self.sample_rate_checkbox = QCheckBox("指定采样率")
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems([str(item) for item in TRANSCODE_SAMPLE_RATE_OPTIONS])
        self.sample_rate_combo.setCurrentText("44100")

        self.bitrate_checkbox = QCheckBox("指定比特率")
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems([str(item) for item in TRANSCODE_BITRATE_OPTIONS])
        self.bitrate_combo.setCurrentText("256")

        option_grid.addWidget(self.sample_rate_checkbox, 0, 0)
        option_grid.addWidget(self.sample_rate_combo, 0, 1)
        option_grid.addWidget(QLabel("Hz"), 0, 2)
        option_grid.addWidget(self.bitrate_checkbox, 0, 3)
        option_grid.addWidget(self.bitrate_combo, 0, 4)
        option_grid.addWidget(QLabel("kbps"), 0, 5)
        option_grid.setColumnStretch(1, 1)
        option_grid.setColumnStretch(4, 1)

        self.tip_label = QLabel("采样率适用于所有输出格式；比特率主要对 m4a / mp3 生效。")
        self.tip_label.setObjectName("MutedText")
        self.tip_label.setWordWrap(True)

        root.addLayout(top_row)
        root.addLayout(option_grid)
        root.addWidget(self.tip_label)

        self.add_button.clicked.connect(self.add_requested.emit)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        self.sample_rate_checkbox.toggled.connect(self._refresh_option_controls)
        self.bitrate_checkbox.toggled.connect(self._refresh_option_controls)
        self._refresh_option_controls()

    def _refresh_option_controls(self) -> None:
        self.sample_rate_combo.setEnabled(self.sample_rate_checkbox.isChecked())
        self.bitrate_combo.setEnabled(self.bitrate_checkbox.isChecked())

    def value(self) -> dict[str, Any]:
        sample_rate_hz = int(self.sample_rate_combo.currentText()) if self.sample_rate_checkbox.isChecked() else None
        bitrate_kbps = int(self.bitrate_combo.currentText()) if self.bitrate_checkbox.isChecked() else None
        return {
            "source_format": self.source_combo.currentText().strip() or ALL_SOURCE_FORMAT,
            "target_format": self.target_combo.currentText().strip() or "m4a",
            "sample_rate_hz": sample_rate_hz,
            "bitrate_kbps": bitrate_kbps,
        }

    def set_value(
        self,
        source_format: str,
        target_format: str,
        sample_rate_hz: int | None = None,
        bitrate_kbps: int | None = None,
    ) -> None:
        if source_format in [self.source_combo.itemText(i) for i in range(self.source_combo.count())]:
            self.source_combo.setCurrentText(source_format)
        else:
            self.source_combo.setCurrentText(ALL_SOURCE_FORMAT)
        if target_format in [self.target_combo.itemText(i) for i in range(self.target_combo.count())]:
            self.target_combo.setCurrentText(target_format)
        else:
            self.target_combo.setCurrentText("m4a")
        if sample_rate_hz and str(sample_rate_hz) in [self.sample_rate_combo.itemText(i) for i in range(self.sample_rate_combo.count())]:
            self.sample_rate_checkbox.setChecked(True)
            self.sample_rate_combo.setCurrentText(str(sample_rate_hz))
        else:
            self.sample_rate_checkbox.setChecked(False)
        if bitrate_kbps and str(bitrate_kbps) in [self.bitrate_combo.itemText(i) for i in range(self.bitrate_combo.count())]:
            self.bitrate_checkbox.setChecked(True)
            self.bitrate_combo.setCurrentText(str(bitrate_kbps))
        else:
            self.bitrate_checkbox.setChecked(False)
        self._refresh_option_controls()


class TranscodeBatchCard(QFrame):
    choose_input_requested = Signal(int)
    choose_output_requested = Signal()
    start_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("WorkspaceCard")
        self._input_rows: list[_InputPathRow] = []
        self._rule_rows: list[_RuleRow] = []
        self._running = False

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
        header_box = QVBoxLayout()
        header_box.setContentsMargins(0, 0, 0, 0)
        header_box.setSpacing(4)
        title = QLabel("批量转码")
        title.setObjectName("SectionTitle")
        subtitle = QLabel("使用软件内置 ffmpeg 进行批量转码。支持多个输入目录、多条格式规则，以及可选的采样率和比特率设置。")
        subtitle.setObjectName("MutedText")
        subtitle.setWordWrap(True)
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        self.status_pill = StatusPill("空闲", "idle")
        header_row.addLayout(header_box, 1)
        header_row.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)

        self.progress_label = QLabel("任务进度：0 / 0")
        self.progress_label.setObjectName("MutedText")
        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.set_progress(0, 0, active=False)

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
        rule_tip = QLabel("“全部”表示把所有支持的输入格式都转成指定输出格式。你也可以为每条规则单独启用采样率或比特率。")
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

        state_card = QFrame()
        state_card.setObjectName("MiniCard")
        state_layout = QVBoxLayout(state_card)
        state_layout.setContentsMargins(12, 12, 12, 12)
        state_layout.setSpacing(6)
        state_layout.addWidget(self.status_label)
        state_layout.addWidget(self.summary_label)
        state_layout.addWidget(self.detail_label)

        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_requested.emit)

        root.addLayout(header_row)
        root.addWidget(self.progress_label)
        root.addWidget(self.progress_bar)
        root.addWidget(self.tabs, 1)
        root.addWidget(state_card)
        root.addWidget(self.start_button)

        self.output_button.clicked.connect(self.choose_output_requested.emit)
        self.add_input_row()
        self.add_rule_row()
        self._refresh_row_controls()
        apply_card_shadow(self)

    def add_input_row(self, value: str = "") -> None:
        row = _InputPathRow()
        row.set_text(value)
        row.choose_requested.connect(self._handle_input_choose)
        row.add_requested.connect(lambda: self.add_input_row())
        row.remove_requested.connect(self._remove_input_row)
        self._input_rows.append(row)
        self.input_rows_layout.addWidget(row)
        self._refresh_row_controls()

    def add_rule_row(
        self,
        source_format: str = ALL_SOURCE_FORMAT,
        target_format: str = "m4a",
        sample_rate_hz: int | None = None,
        bitrate_kbps: int | None = None,
    ) -> None:
        row = _RuleRow()
        row.set_value(source_format, target_format, sample_rate_hz, bitrate_kbps)
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

    def set_rules(self, rules: list[dict[str, Any]]) -> None:
        for row in list(self._rule_rows):
            self.rule_rows_layout.removeWidget(row)
            row.deleteLater()
        self._rule_rows.clear()
        if not rules:
            rules = [{"source_format": ALL_SOURCE_FORMAT, "target_format": "m4a", "sample_rate_hz": None, "bitrate_kbps": None}]
        for item in rules:
            self.add_rule_row(
                str(item.get("source_format", ALL_SOURCE_FORMAT)),
                str(item.get("target_format", "m4a")),
                int(item.get("sample_rate_hz")) if item.get("sample_rate_hz") not in (None, "", False) else None,
                int(item.get("bitrate_kbps")) if item.get("bitrate_kbps") not in (None, "", False) else None,
            )
        self._refresh_row_controls()

    def rules(self) -> list[dict[str, Any]]:
        return [row.value() for row in self._rule_rows]

    def set_output_dir(self, value: str) -> None:
        self.output_edit.setText(value)
        self.output_edit.setCursorPosition(0)

    def output_dir(self) -> str:
        return self.output_edit.text().strip()

    def set_recursive(self, value: bool) -> None:
        self.recursive_checkbox.setChecked(bool(value))

    def recursive(self) -> bool:
        return self.recursive_checkbox.isChecked()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.start_button.setEnabled(not self._running)
        self.status_pill.setText("运行中" if self._running else "空闲")
        self.status_pill.set_tone("active" if self._running else "idle")
        self.progress_bar.set_active(self._running)
        for row in self._input_rows:
            row.edit.setEnabled(not self._running)
            row.choose_button.setEnabled(not self._running)
            row.add_button.setEnabled(not self._running)
            row.remove_button.setEnabled(not self._running and len(self._input_rows) > 1)
        for row in self._rule_rows:
            row.source_combo.setEnabled(not self._running)
            row.target_combo.setEnabled(not self._running)
            row.sample_rate_checkbox.setEnabled(not self._running)
            row.sample_rate_combo.setEnabled(not self._running and row.sample_rate_checkbox.isChecked())
            row.bitrate_checkbox.setEnabled(not self._running)
            row.bitrate_combo.setEnabled(not self._running and row.bitrate_checkbox.isChecked())
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
            self.summary_label.setText(f"队列：共 {total_jobs} 个任务，并发 {worker_count} 路")
            self.detail_label.setText(f"说明：输出目录 {payload.get('output_dir', '')}")
            self.progress_label.setText(f"任务进度：0 / {total_jobs}")
            self.progress_bar.set_progress(0, total_jobs, active=total_jobs > 0)
            self.status_pill.setText("待执行")
            self.status_pill.set_tone("warning")
        elif event_name == "warning":
            self.detail_label.setText(f"说明：{payload.get('message', '')}")
            self.status_pill.setText("注意")
            self.status_pill.set_tone("warning")
        elif event_name == "job_started":
            target = str(payload.get("target_format", "") or "")
            sample_rate = payload.get("sample_rate_hz")
            bitrate = payload.get("bitrate_kbps")
            extras: list[str] = []
            if sample_rate:
                extras.append(f"{sample_rate} Hz")
            if bitrate:
                extras.append(f"{bitrate} kbps")
            extra_text = f"（{' / '.join(extras)}）" if extras else ""
            self.status_label.setText("状态：正在转码")
            self.detail_label.setText(f"说明：{payload.get('input_path', '')} -> {target}{extra_text}")
            self.status_pill.setText("转码中")
            self.status_pill.set_tone("active")
        elif event_name == "queue_progress":
            queued = int(payload.get("queued", 0) or 0)
            running = int(payload.get("running", 0) or 0)
            completed = int(payload.get("completed", 0) or 0)
            total_jobs = int(payload.get("total_jobs", 0) or 0)
            self.summary_label.setText(f"队列：待处理 {queued}，执行中 {running}，已完成 {completed} / {total_jobs}")
            self.progress_label.setText(f"任务进度：{completed} / {total_jobs}")
            self.progress_bar.set_progress(completed, total_jobs, active=(queued + running) > 0)
        elif event_name == "job_succeeded":
            self.detail_label.setText(f"说明：已完成 {payload.get('output_path', '')}（{payload.get('elapsed_sec', 0)}s）")
            self.status_pill.setText("成功")
            self.status_pill.set_tone("success")
        elif event_name == "job_failed":
            self.detail_label.setText(f"说明：失败 {payload.get('input_path', '')}，原因：{payload.get('reason', '')}")
            self.status_pill.setText("失败")
            self.status_pill.set_tone("danger")
        elif event_name == "batch_finished":
            self.status_label.setText("状态：转码完成")
            self.summary_label.setText(
                f"队列：成功 {payload.get('success_count', 0)}，失败 {payload.get('failed_count', 0)}，总耗时 {payload.get('elapsed_sec', 0)}s"
            )
            self.detail_label.setText("说明：批量转码任务已结束")
            total = int(payload.get('total_jobs', 0) or (int(payload.get('success_count', 0) or 0) + int(payload.get('failed_count', 0) or 0)))
            done = int(payload.get('success_count', 0) or 0) + int(payload.get('failed_count', 0) or 0)
            self.progress_label.setText(f"任务进度：{done} / {total}")
            self.progress_bar.set_progress(done, total, active=False)
            self.status_pill.setText("已完成")
            self.status_pill.set_tone("success")

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
