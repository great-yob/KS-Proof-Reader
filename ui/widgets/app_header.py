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
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested    = Signal()
    curator_requested  = Signal()
    login_requested    = Signal()
    logout_requested   = Signal()
    password_change_requested = Signal()
    members_requested  = Signal()

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
        self._user = None
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
            self._show_account_menu()
        else:
            self.login_requested.emit()

    def _show_account_menu(self):
        """계정 메뉴 — 비밀번호 변경 / 사용자 관리(관리자) / 로그아웃.

        예전엔 칩 클릭이 곧 로그아웃이었다. 비밀번호 변경·사용자 관리가 생기면서
        메뉴로 바꿨다(로그아웃을 실수로 누를 일도 함께 줄어든다).
        """
        menu = self._build_account_menu()
        menu.exec(self._auth_btn.mapToGlobal(self._auth_btn.rect().bottomLeft()))

    def _build_account_menu(self):
        from PySide6.QtWidgets import QMenu
        from ui.styles.theme import current_palette
        pal = current_palette()
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{pal['surface']};color:{pal['text']};"
            f"border:1px solid {pal['border_strong']};border-radius:8px;padding:6px;}}"
            f"QMenu::item{{padding:7px 18px;border-radius:6px;}}"
            f"QMenu::item:selected{{background:{pal['surface_hover']};}}"
            f"QMenu::item:disabled{{color:{pal['text_dim']};}}"
            f"QMenu::separator{{height:1px;background:{pal['border']};margin:5px 6px;}}")
        u = self._user or {}
        if u.get("can_change_password"):
            menu.addAction("비밀번호 변경", self.password_change_requested.emit)
        elif u.get("kind", "employee") == "employee":
            # 직원 계정은 같은 auth.users를 ks-works와 공유한다 — 거기서 바꾼다.
            act = menu.addAction("비밀번호 변경은 ks-works 마이페이지에서")
            act.setEnabled(False)
        if u.get("role") == "admin":
            menu.addAction("사용자 관리", self.members_requested.emit)
        menu.addSeparator()
        menu.addAction("로그아웃", self.logout_requested.emit)
        return menu

    def set_auth_state(self, user: dict = None):
        """로그인 상태 반영 — user=None이면 '로그인', 있으면 이름 표시(클릭=계정 메뉴)."""
        self._logged_in = user is not None
        self._user = dict(user) if user else None
        # make_button(icon=...)이 아이콘-텍스트 간격용 선행 공백 2칸을 넣으므로 동일하게 유지.
        if user:
            name = user.get("name") or user.get("email") or "계정"
            self._auth_btn.setText(f"  {name}")
            self._auth_btn.setToolTip("계정 메뉴 (비밀번호 변경·로그아웃)")
        else:
            self._auth_btn.setText("  로그인")
            self._auth_btn.setToolTip("사내 계정으로 로그인 (공유 용어 사전 활성화)")
