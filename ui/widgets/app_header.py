"""
ui/widgets/app_header.py — 메인 컬럼 상단 헤더 (커스텀 타이틀바)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
좌측: 테마 토글.  우측: 창 최소화 + 종료.
네이티브 타이틀바를 제거(프레임리스)했으므로 창 컨트롤을 직접 제공한다.
헤더 본체(버튼 외 영역)는 드래그로 창을 이동할 수 있다(MainWindow의 nativeEvent).
브랜드 로고는 좌측 사이드바(Sidebar)가 담당한다.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout

from ui.widgets.components import icon_button, make_button
from ui.widgets.theme_toggle import ThemeToggleButton
from ui.styles.theme import restyle


class AppHeader(QFrame):
    theme_toggled      = Signal()
    new_file_requested = Signal()
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested    = Signal()
    curator_requested  = Signal()
    login_requested    = Signal()
    logout_requested   = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "header")
        self.setFixedHeight(53)
        self._build_ui()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(8)

        # ── 좌측: 로그인/계정 + 큐레이터 진입 ──
        # ks-works 헤더 사용자 칩과 동일한 라임 pill + 사람 아이콘(검정).
        # 로그인 시 사용자 이름을 표시하는 역할이 ks-works 칩과 같다.
        self._logged_in = False
        self._auth_btn = make_button("로그인", variant="userchip", icon="user",
                                     icon_role="lime_fg", on_click=self._on_auth_clicked)
        self._auth_btn.setToolTip("사내 계정으로 로그인 (공유 용어 사전 활성화)")
        lay.addWidget(self._auth_btn)

        self._curator_btn = icon_button(
            "clipboard-check", tooltip="사내 용어 큐레이션 (관리자)", size=17,
            on_click=self.curator_requested.emit)
        self._curator_btn.setVisible(False)
        lay.addWidget(self._curator_btn)

        lay.addStretch()

        # ── 우측: 창 컨트롤 ──────────────────────────
        self._theme_btn = ThemeToggleButton()
        self._theme_btn.toggled_mode.connect(lambda mode: self.theme_toggled.emit())
        lay.addWidget(self._theme_btn)
        
        lay.addSpacing(24)
        
        self._min_btn = icon_button("minus", tooltip="최소화", size=16,
                                    on_click=self.minimize_requested.emit)
        lay.addWidget(self._min_btn)
        
        self._max_btn = icon_button("square", tooltip="최대화/복원", size=14,
                                    on_click=self.maximize_requested.emit)
        lay.addWidget(self._max_btn)
        
        self._close_btn = icon_button("x", tooltip="닫기", size=16,
                                      on_click=self.close_requested.emit)
        self._close_btn.setProperty("winctl", "close")   # 빨강 호버
        restyle(self._close_btn)
        lay.addWidget(self._close_btn)

    def set_theme_icon(self, mode: str):
        self._theme_btn.set_mode(mode)

    def set_curator_visible(self, visible: bool):
        """큐레이터(관리자) 세션일 때만 큐레이션 진입 버튼을 노출."""
        self._curator_btn.setVisible(bool(visible))

    def _on_auth_clicked(self):
        if self._logged_in:
            self.logout_requested.emit()
        else:
            self.login_requested.emit()

    def set_auth_state(self, user: dict = None):
        """로그인 상태 반영 — user=None이면 '로그인', 있으면 이름 표시(클릭=로그아웃)."""
        self._logged_in = user is not None
        # make_button(icon=...)이 아이콘-텍스트 간격용 선행 공백 2칸을 넣으므로 동일하게 유지.
        if user:
            name = user.get("name") or user.get("email") or "계정"
            self._auth_btn.setText(f"  {name}")
            self._auth_btn.setToolTip("클릭하면 로그아웃합니다.")
        else:
            self._auth_btn.setText("  로그인")
            self._auth_btn.setToolTip("사내 계정으로 로그인 (공유 용어 사전 활성화)")
