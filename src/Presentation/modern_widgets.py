from __future__ import annotations

from PySide6.QtCore import Property, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QLabel, QProgressBar, QVBoxLayout, QWidget


class AnimatedProgressBar(QProgressBar):
    def __init__(self) -> None:
        super().__init__()
        self._shift = 0
        self._active = False
        self.setRange(0, 1000)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(10)
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)
        self._refresh_style()

    def _tick(self) -> None:
        self._shift = (self._shift + 6) % 100
        self._refresh_style()

    def _refresh_style(self) -> None:
        start = max(0.0, min(1.0, self._shift / 100.0))
        middle = max(0.0, min(1.0, (self._shift + 22) / 100.0))
        end = max(0.0, min(1.0, (self._shift + 44) / 100.0))
        self.setStyleSheet(
            "QProgressBar {"
            "background: rgba(11, 16, 24, 0.92);"
            "border: 1px solid rgba(87, 104, 132, 0.34);"
            "border-radius: 5px;"
            "padding: 0px;"
            "}"
            f"QProgressBar::chunk {{background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(55,133,255,215), stop:{start:.2f} rgba(55,133,255,215), stop:{middle:.2f} rgba(148,208,255,250), stop:{end:.2f} rgba(55,133,255,215), stop:1 rgba(45,137,239,225)); border-radius: 5px;}}"
        )

    def set_progress(self, current: int, total: int, *, active: bool = False) -> None:
        safe_total = max(0, int(total or 0))
        safe_current = max(0, int(current or 0))
        if safe_total > 0:
            ratio = max(0.0, min(1.0, safe_current / safe_total))
            self.setRange(0, 1000)
            self.setValue(int(ratio * 1000))
        elif active:
            self.setRange(0, 0)
        else:
            self.setRange(0, 1000)
            self.setValue(0)
        self.set_active(active)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self._active and self.maximum() != 0:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._shift = 0
            self._refresh_style()


class MetricTile(QFrame):
    def __init__(self, title: str, value: str = "--", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("MetricTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("FieldLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("MutedText")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)
        layout.addStretch(1)

    def set_metric(self, value: str, subtitle: str = "") -> None:
        self.value_label.setText(value)
        self.subtitle_label.setText(subtitle)


class StatusPill(QLabel):
    _TONES = {
        "idle": ("rgba(79, 92, 114, 0.24)", "#AAB5C5"),
        "active": ("rgba(45, 137, 239, 0.22)", "#8BC5FF"),
        "success": ("rgba(34, 197, 94, 0.20)", "#78E39E"),
        "warning": ("rgba(245, 158, 11, 0.18)", "#FFD277"),
        "danger": ("rgba(239, 68, 68, 0.18)", "#FFB1B1"),
    }

    def __init__(self, text: str = "空闲", tone: str = "idle") -> None:
        super().__init__(text)
        self.setObjectName("StatusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(84)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        background, foreground = self._TONES.get(tone, self._TONES["idle"])
        self.setStyleSheet(
            f"background:{background}; color:{foreground}; border:1px solid rgba(255,255,255,0.08); border-radius:11px; padding:5px 10px; font-weight:600;"
        )


def apply_card_shadow(widget: QWidget) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(28)
    shadow.setOffset(0, 10)
    shadow.setColor(QColor(0, 0, 0, 70))
    widget.setGraphicsEffect(shadow)
