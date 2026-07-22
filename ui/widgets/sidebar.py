"""
ui/widgets/sidebar.py — 좌측 영구 사이드바(LNB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
상단 브랜드 워드마크(AI 교정교열) + 단계 네비게이션(StepperRail) + 하단 저작권.
헤더/풋터와 분리된 '전체 높이' 컬럼이며 워크스페이스 전 단계에 항상 표시된다.
스텝퍼 자체는 embedded 모드로 두어 배경/테두리/폭은 이 사이드바가 담당한다.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel

from ui.widgets.components import label
from ui.widgets.stepper_rail import StepperRail
from ui.styles.icons import logo_pixmap

SIDEBAR_WIDTH = 160
_PAD = 18              # 좌우 본문 여백(px)

# 버전 단일 출처는 최상위 version.py — 빌드 스크립트·자동 업데이터와 같은 값을 본다.
#   (과거엔 여기 하드코딩돼 있어 빌드본과 어긋날 여지가 있었다.)
try:
    from version import APP_VERSION, DATA_VERSION
except Exception:          # 경로 문제 등 — UI는 버전 없이도 떠야 한다
    APP_VERSION = "0.0.0"
    DATA_VERSION = "-"


def _data_version() -> str:
    """표시할 사전 데이터 버전 — 실제 로드된 DB 값을 우선한다.

    DB를 못 읽으면 패키징 값으로 대체하고, 그것도 없으면 '-'. UI는 어떤 경우에도 뜬다.
    """
    try:
        from nikl_dict import data_version
        return data_version() or DATA_VERSION
    except Exception:
        return DATA_VERSION


class Sidebar(QFrame):
    """로고·네비·저작권을 담는 전체 높이 좌측 컬럼.

    내부 StepperRail은 `self.rail`로 노출해 MainWindow가 기존처럼 직접 제어한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 22, 0, 16)
        root.setSpacing(0)

        # ── 브랜드 워드마크(상단) ───────────────────
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(_PAD, 0, _PAD, 0)
        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._refresh_logo()
        logo_row.addWidget(self._logo)
        logo_row.addStretch()
        root.addLayout(logo_row)
        root.addSpacing(24)

        # ── 단계 네비게이션 ─────────────────────────
        self.rail = StepperRail(embedded=True)
        root.addWidget(self.rail, 1)

        # ── 저작권(하단) ───────────────────────────
        foot = QVBoxLayout()
        foot.setContentsMargins(_PAD + 6, 0, _PAD, 0)
        foot.setSpacing(2)
        foot.addWidget(label("© kim daekyung", role="copyright"))
        # 앱 버전과 데이터 버전은 **따로 올라간다**(코드 수정에 283MB 재배포를 피하기 위함).
        #   데이터 버전은 실제로 로드된 stdict.db의 meta.data_version이 진실이다 —
        #   패키징 값(version.DATA_VERSION)과 어긋나면 그 사실이 바로 보여야 한다.
        foot.addWidget(label(f"version {APP_VERSION}", role="version"))
        foot.addWidget(label(f"database {_data_version()}", role="version"))
        root.addLayout(foot)

    def _refresh_logo(self):
        # 라이트/다크 전용 워드마크를 사이드바 본문 폭에 맞춰 표시
        self._logo.setPixmap(logo_pixmap(width=SIDEBAR_WIDTH - 2 * _PAD))

    def refresh_theme(self):
        self._refresh_logo()
