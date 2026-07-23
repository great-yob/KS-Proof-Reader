"""
ui/widgets/sidebar.py — 좌측 영구 사이드바(LNB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
상단 브랜드 워드마크(AI 교정교열) + 단계 네비게이션(StepperRail) + 하단 저작권.
헤더/풋터와 분리된 '전체 높이' 컬럼이며 워크스페이스 전 단계에 항상 표시된다.
스텝퍼 자체는 embedded 모드로 두어 배경/테두리/폭은 이 사이드바가 담당한다.
"""

from PySide6.QtCore import Qt, Signal
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


class _UpdateBadge(QLabel):
    """버전 푸터 아래의 '업데이트 있음' 배지 — 클릭하면 업데이트 창을 연다.

    QLabel엔 clicked가 없어 마우스 이벤트로 직접 만든다(전용 위젯 하나를 위해
    QPushButton을 QSS로 라벨처럼 위장시키는 것보다 단순하다).
    """

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "chip")
        self.setProperty("tone", "accent")
        self.setCursor(Qt.PointingHandCursor)
        self.setVisible(False)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Sidebar(QFrame):
    """로고·네비·저작권을 담는 전체 높이 좌측 컬럼.

    내부 StepperRail은 `self.rail`로 노출해 MainWindow가 기존처럼 직접 제어한다.
    """

    update_clicked = Signal()      # 업데이트 배지 클릭 → MainWindow가 창을 연다

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
        # 새 릴리스가 있을 때만 나타난다(평소엔 숨김 = 레이아웃 변화 없음).
        #   모달을 닫아도 이 배지는 남아, 언제든 다시 열 수 있는 상시 진입점이 된다.
        self._update_badge = _UpdateBadge()
        self._update_badge.clicked.connect(self.update_clicked)
        foot.addSpacing(6)
        foot.addWidget(self._update_badge, 0, Qt.AlignLeft)
        root.addLayout(foot)

    def set_update_available(self, versions):
        """업데이트 배지 표시/숨김. versions=[] 이면 숨긴다.

        인자는 표시할 버전 문자열 목록(앱·데이터가 동시에 있을 수 있다).
        """
        if not versions:
            self._update_badge.setVisible(False)
            return
        self._update_badge.setText("업데이트 " + " · ".join(versions))
        self._update_badge.setVisible(True)

    def _refresh_logo(self):
        # 라이트/다크 전용 워드마크를 사이드바 본문 폭에 맞춰 표시
        self._logo.setPixmap(logo_pixmap(width=SIDEBAR_WIDTH - 2 * _PAD))

    def refresh_theme(self):
        self._refresh_logo()
