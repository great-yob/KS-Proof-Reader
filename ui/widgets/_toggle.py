"""
ui/widgets/_toggle.py — iOS 스타일 토글 스위치
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
직접 페인트 위젯이므로 QSS가 적용되지 않는다. paint 시점에
current_palette()를 읽어 라이트/다크 양쪽에 대응한다.
"""

from PySide6.QtCore import Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QBrush
from PySide6.QtWidgets import QWidget

from ui.styles.theme import current_palette


class ToggleSwitch(QWidget):
    """클릭으로 on/off 전환되는 알약 형태 스위치"""

    toggled = Signal(bool)

    def __init__(self, on: bool = False, parent=None):
        super().__init__(parent)
        self._on = bool(on)
        self._progress = 1.0 if self._on else 0.0
        self.setFixedSize(36, 20)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim_value)

    def _on_anim_value(self, value: float):
        self._progress = value
        self.update()

    # ── API ──────────────────────────────
    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool, emit: bool = True):
        on = bool(on)
        if self._on != on:
            self._on = on
            
            self._anim.stop()
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(1.0 if self._on else 0.0)
            self._anim.start()
            
            if emit:
                self.toggled.emit(on)

    def refresh_theme(self):
        """테마 전환 시 호출 — 다시 그리기."""
        self.update()

    # ── 이벤트 ────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.set_on(not self._on)
        super().mousePressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = current_palette()

        # 색상 보간
        color_off = QColor(pal["border_strong"])
        color_on = QColor(pal["accent"])
        
        if not self.isEnabled():
            track = QColor(pal["border"])
        else:
            r = int(color_off.red() + (color_on.red() - color_off.red()) * self._progress)
            g = int(color_off.green() + (color_on.green() - color_off.green()) * self._progress)
            b = int(color_off.blue() + (color_on.blue() - color_off.blue()) * self._progress)
            track = QColor(r, g, b)

        p.setBrush(QBrush(track))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, 36, 20, 10, 10)

        # 손잡이 — 흰색 원 보간
        thumb_x = 2.0 + (18.0 - 2.0) * self._progress
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(thumb_x, 2, 16, 16)
