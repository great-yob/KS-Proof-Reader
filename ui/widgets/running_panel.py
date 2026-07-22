"""
ui/widgets/running_panel.py — 분석/적용 진행 중 중앙 패널
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
세부 진행은 footer(진행바)·rail(단계)·activity(로그)가 담당하므로
중앙은 현재 작업과 단계 요약만 차분하게 보여준다.
진행률 그래픽은 페이지를 넘기는 '책'(BookProgress) — 원형 링을 대체.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from ui.widgets.components import section_card, label, AnimatedGradientBorder
from ui.widgets.book_progress import BookProgress


class RunningPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card, cl = section_card("", "")

        cl.setAlignment(Qt.AlignCenter)
        cl.setSpacing(0)

        self._book = BookProgress()
        cl.addWidget(self._book, alignment=Qt.AlignCenter)
        cl.addSpacing(2)

        self._title = label("교정 분석 중", role="h1")
        self._title.setAlignment(Qt.AlignCenter)
        cl.addWidget(self._title)
        # 내부 카드의 기본 테두리 제거 및 배경 투명화 (바깥 그라데이션이 배경 역할 수행)
        card.setStyleSheet("background: transparent; border: none; border-radius: 12px;")

        # center_disc: 책+%+제목 뒤 중앙 불투명 원판 — 배경 리본/글자 파티클 가림
        self._border_wrap = AnimatedGradientBorder(card, border_width=2, radius=14,
                                                   particles=True, center_disc=112)
        outer.addWidget(self._border_wrap)

    def refresh_theme(self):
        self._book.refresh_theme()
        self._border_wrap.update()

    # ── API ───────────────────────────────────────
    def set_animating(self, animating: bool):
        self._border_wrap.set_animating(animating)
        self._book.set_animating(animating)

    def set_progress(self, percent: int):
        self._book.set_progress(percent)

    def set_title(self, text: str):
        self._title.setText(text)

    def set_detail(self, text: str, tone: str = ""):
        """상세(작은 텍스트) 행은 UI에서 제거됨 — 호출부 호환용 no-op.
        세부 진행 메시지는 footer 진행바와 ActivityPanel 로그가 담당한다."""
