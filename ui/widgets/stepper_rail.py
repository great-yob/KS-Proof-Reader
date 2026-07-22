"""
ui/widgets/stepper_rail.py — 좌측 영구 파이프라인 스텝퍼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4개 매크로 단계(설정·분석·검토·완료)의 상태를 항상 표시한다.
엔진의 세부 5스텝은 footer/activity가 담당하므로 레일은 깔끔하게 유지.
각 단계엔 결과 부제(예: "검출 142건", "적용 138건")를 남길 수 있어
이전 단계 진행상황을 한눈에 확인 가능 — 사용자 지적 ①을 해소한다.
"""

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QGraphicsDropShadowEffect
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient, QFont, QFontMetrics

from ui.widgets.components import label
from ui.styles.theme import restyle, current_mode, current_palette
from ui.widgets.result_panel import _MinimalChevron


# 좌측 레일 고정 폭 — 헤더 로고를 사이드바 폭에 맞추는 데도 참조한다.
RAIL_WIDTH = 172

# (key, 표시번호, 제목)
STEPS = [
    ("setup",   "1", "설정"),
    ("analyze", "2", "분석"),
    ("review",  "3", "검토"),
    ("done",    "4", "완료"),
]
_ORDER = [s[0] for s in STEPS]


class _HLine(QFrame):
    """1px 수평 디바이더 (테마 자동 대응)."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(1)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _e):
        dark = current_mode() == "dark"
        col = QColor(255, 255, 255, 55) if dark else QColor(0, 0, 0, 18)
        pa = QPainter(self)
        pa.fillRect(self.rect(), col)
        pa.end()


class _BottomShadow(QFrame):
    """하단 선 아래 방사형 쉐도우 — 가운데 진하고 양쪽으로 페이드."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(12)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _e):
        dark = current_mode() == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if dark:
            shadow_col = QColor(0, 0, 0, 90)
        else:
            shadow_col = QColor(0, 0, 0, 25)

        cx = w / 2
        rx = w * 0.42   # 수평 반지름 (전체폭의 ~84%)
        ry = h           # 수직 반지름

        p.save()
        p.translate(cx, 0)
        p.scale(1.0, ry / rx)  # 타원형으로 찌그러뜨림

        from PySide6.QtGui import QRadialGradient
        grad = QRadialGradient(0, 0, rx)
        grad.setColorAt(0.0, shadow_col)
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(0, 0), rx, rx)

        p.restore()
        p.end()


class _SectionHeader(QFrame):
    """상단선 — 텍스트 — 하단선 — 방사형 쉐도우 구조의 섹션 디바이더.

    ⚠ 라벨 색은 인스턴스 스타일시트로 직접 칠하므로(사이드바 배경에 맞춘 반투명 톤)
    테마 토글 때 반드시 refresh_theme()로 다시 칠해야 한다 — 예전엔 생성 시점에
    다크일 때만 흰 글씨를 박아 두고 갱신하지 않아, 라이트로 바꾸면 흰 배경에 흰 글씨로
    사라졌다(사용자 보고 2026-07-21). MainWindow._refresh_all_themes가 트리를 훑어
    호출한다.
    """

    def __init__(self, text="진행 단계"):
        super().__init__()
        self.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        col.addWidget(_HLine())

        self._lbl = label(text, role="sub")
        self._lbl.setProperty("muted", "true")
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setContentsMargins(0, 8, 0, 8)
        col.addWidget(self._lbl)

        col.addWidget(_HLine())
        col.addWidget(_BottomShadow())
        self.refresh_theme()

    def refresh_theme(self):
        # _SidebarDivider·_HLine과 같은 관용(다크=흰 반투명 / 라이트=검정 반투명).
        color = ("rgba(255, 255, 255, 0.45)" if current_mode() == "dark"
                 else "rgba(0, 0, 0, 0.45)")
        self._lbl.setStyleSheet(f"color: {color}; background: transparent;")


def _make_section_header(text="진행 단계") -> QFrame:
    return _SectionHeader(text)


class _SidebarDivider(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(1)

    def paintEvent(self, e):
        dark = current_mode() == "dark"
        color = QColor(255, 255, 255, int(255 * 0.15)) if dark else QColor(0, 0, 0, int(255 * 0.10))
        p = QPainter(self)
        p.fillRect(self.rect(), color)
        p.end()


class _SidebarChevron(_MinimalChevron):
    def paintEvent(self, _e):
        pal = current_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        r = min(w, h) * 0.25

        col = QColor(pal["text_muted"])
        col.setAlphaF(min(0.6, self._p))
        p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        if self.direction == "right":
            p.drawPolyline([
                QPointF(cx - r * 0.55, cy - r),
                QPointF(cx + r * 0.45, cy),
                QPointF(cx - r * 0.55, cy + r),
            ])
        else:  # down
            p.drawPolyline([
                QPointF(cx - r, cy - r * 0.55),
                QPointF(cx, cy + r * 0.45),
                QPointF(cx + r, cy - r * 0.55),
            ])
        p.end()


class StepperRail(QFrame):
    """단계 클릭 시 step_clicked(key) emit (완료된 단계 재방문용)."""

    step_clicked = Signal(str)

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        if embedded:
            # 사이드바 안에 들어갈 때는 사이드바가 배경/테두리/폭을 담당한다.
            self.setProperty("role", "railEmbedded")
        else:
            self.setProperty("role", "rail")
            self.setFixedWidth(RAIL_WIDTH)
        self._items = {}      # key -> dict(frame, num, title, sub)
        self._phase = "setup"
        self._errors = set()
        self._build_ui()
        self.set_phase("setup")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(4)

        header = _make_section_header("진행 단계")
        root.addWidget(header)
        root.addSpacing(6)

        for i, (key, num, title) in enumerate(STEPS):
            if i > 0:
                chev = _SidebarChevron(direction="down")
                chev.setFixedHeight(18)
                chev.start()
                root.addWidget(chev, 0, Qt.AlignCenter)
            root.addWidget(self._make_item(key, num, title))

        root.addStretch()

    def _make_item(self, key: str, num: str, title: str) -> QFrame:
        item = QFrame()
        item.setProperty("role", "railItem")
        item.setCursor(Qt.PointingHandCursor)
        item.mousePressEvent = lambda _e, k=key: self.step_clicked.emit(k)

        lay = QVBoxLayout(item)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setProperty("role", "stepTitle")
        title_lbl.setProperty("state", "todo")
        title_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(title_lbl)

        div = _SidebarDivider()
        lay.addWidget(div)

        sub_lbl = QLabel("대기중")
        sub_lbl.setProperty("role", "stepSub")
        sub_lbl.setProperty("state", "todo")
        sub_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub_lbl)

        self._items[key] = {"frame": item, "title": title_lbl, "sub": sub_lbl, "base_title": title}
        return item

    def _apply_state(self, key: str, state: str):
        it = self._items[key]
        title, frame, sub, base_title = it["title"], it["frame"], it["sub"], it["base_title"]
        title.setProperty("state", state)
        sub.setProperty("state", state)

        current_sub = sub.text()
        default_subs = ("대기중", f"{base_title} 중...", f"{base_title} 완료")

        if state == "active":
            frame.setProperty("active", "true")
            if current_sub in default_subs or not current_sub:
                sub.setText(f"{base_title} 중...")
        else:
            frame.setProperty("active", "false")
            if state == "done":
                if current_sub in default_subs or not current_sub:
                    sub.setText(f"{base_title} 완료")
            else:  # todo or error
                if current_sub in default_subs or not current_sub:
                    sub.setText("대기중")

        for w in (title, frame, sub):
            restyle(w)

    def set_phase(self, phase: str):
        """현재 활성 단계 설정 — 이전=done, 이후=todo."""
        if phase not in _ORDER:
            return
        self._phase = phase
        idx = _ORDER.index(phase)
        for i, key in enumerate(_ORDER):
            if key in self._errors:
                self._apply_state(key, "error")
            elif i < idx:
                self._apply_state(key, "done")
            elif i == idx:
                self._apply_state(key, "active")
            else:
                self._apply_state(key, "todo")

    def set_step_result(self, key: str, subtitle: str):
        """단계별 결과 부제 표시(예: '검출 142건')."""
        if key not in self._items:
            return
        sub = self._items[key]["sub"]
        sub.setText(subtitle)

    def set_error(self, key: str):
        if key in self._items:
            self._errors.add(key)
            self._apply_state(key, "error")

    def complete_all(self):
        """작업 종료 시 이전 단계들은 완료 상태로, '완료' 단계는 활성 상태로 둔다."""
        self._phase = "done"
        for key in _ORDER:
            if key in self._errors:
                self._apply_state(key, "error")
            elif key == "done":
                self._apply_state(key, "active")
            else:
                self._apply_state(key, "done")

    def reset(self):
        self._errors.clear()
        for it in self._items.values():
            it["sub"].setText("대기중")
        self.set_phase("setup")
