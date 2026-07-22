"""
ui/styles/theme.py — 디자인 시스템 (뉴트럴 프로 · 라이트/다크 토큰)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
토큰 기반 테마. 모든 컴포넌트 스타일은 objectName / 동적 property 선택자로
글로벌 QSS에 정의한다. 영구 위젯은 per-instance setStyleSheet를 쓰지 않고
이 QSS에 의존하며, 상태 변화는 setProperty + restyle()로 처리한다.

직접 페인트/HTML 위젯(ToggleSwitch, 미리보기 QTextBrowser)만 paint 시점에
current_palette()를 읽어 refresh_theme()로 갱신한다.

  set_mode("light"|"dark") → COLORS 라이브 매핑 갱신
  apply_theme(app, mode)   → QApplication에 글로벌 QSS 적용
"""

from string import Template


# ══════════════════════════════════════════════════════════════
# ▌스케일 (간격 / 반경 / 타이포)
# ══════════════════════════════════════════════════════════════

SPACE  = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 8, "md": 12, "lg": 16, "pill": 999}
FONT   = {"xs": 11, "sm": 12, "md": 13, "lg": 15, "xl": 18, "xxl": 22}


# ══════════════════════════════════════════════════════════════
# ▌팔레트 토큰 — 라이트 / 다크 (동일 키)
# ══════════════════════════════════════════════════════════════

LIGHT = {
    "bg":            "#F6F7F9",
    "surface":       "#FFFFFF",
    "surface_alt":   "#F2F4F7",
    "surface_hover": "#E9EDF2",
    "border":        "#E3E7EC",
    "border_strong": "#CBD2DA",
    "border_light":  "#EEF1F5",   # 레거시 별칭
    "text":          "#1A1D23",
    "text_sub":      "#586172",
    "text_muted":    "#8A93A1",
    "text_dim":      "#B6BDC7",
    "accent":        "#5E6AD2",
    "accent_hover":  "#4E5AC0",
    "accent_press":  "#414DAE",
    "accent_fg":     "#FFFFFF",
    "accent_soft":   "#EEF0FB",
    "navy":          "#5E6AD2",   # 레거시 별칭(primary)
    # ks-works 사용자 칩 라임 (브랜드 고정색 — 라이트/다크 동일)
    "lime":          "#CEFF00",
    "lime_hover":    "#B9E600",
    "lime_press":    "#A5CC00",
    "lime_fg":       "#000000",
    "success":       "#157F3C",
    "success_bg":    "#E7F5EC",
    "success_border":"#BCE3C7",
    "error":         "#D63B3B",
    "error_bg":      "#FCEBEB",
    "error_border":  "#F3C7C7",
    "warning":       "#B4690E",
    "warning_bg":    "#FBF0DA",
    "warning_border":"#F0D9A8",
    "warning_strong":"#B4690E",   # 선택된 검수 칩(진한 경고색 + 흰글씨)
    "info":          "#2563EB",   # 청색(직접수정 등)
    # 교정 소스 뱃지
    "dict_bg":     "#FFF4E8", "dict_fg":   "#B45309", "dict_border":   "#F3D9B5",
    "typo_bg":     "#E9F6EE", "typo_fg":   "#157F3C", "typo_border":   "#BCE3C7",
    "polish_bg":   "#EFEDFB", "polish_fg": "#6D4FD0", "polish_border": "#D7CFF3",
    # 활동 로그 레벨
    "log_info":    "#8A93A1",
    "log_ok":      "#157F3C",
    "log_warn":    "#B4690E",
    "log_err":     "#D63B3B",
}

DARK = {
    "bg":            "#14161A",
    "surface":       "#1B1E24",
    "surface_alt":   "#21252C",
    "surface_hover": "#2A2F38",
    "border":        "#2E343D",
    "border_strong": "#3C434E",
    "border_light":  "#262B33",   # 레거시 별칭
    "text":          "#E7EAEE",
    "text_sub":      "#A6AFBC",
    "text_muted":    "#6E7884",
    "text_dim":      "#4A525D",
    "accent":        "#7682FF",
    "accent_hover":  "#8893FF",
    "accent_press":  "#6571F0",
    "accent_fg":     "#FFFFFF",
    "accent_soft":   "#23273A",
    "navy":          "#7682FF",   # 레거시 별칭(primary)
    # ks-works 사용자 칩 라임 (브랜드 고정색 — 라이트/다크 동일)
    "lime":          "#CEFF00",
    "lime_hover":    "#B9E600",
    "lime_press":    "#A5CC00",
    "lime_fg":       "#000000",
    "success":       "#54D17F",
    "success_bg":    "#16301F",
    "success_border":"#225538",
    "error":         "#F1726E",
    "error_bg":      "#341B1B",
    "error_border":  "#5A2C2C",
    "warning":       "#E8B468",
    "warning_bg":    "#33270F",
    "warning_border":"#574215",
    "warning_strong":"#B4690E",   # 선택된 검수 칩(진한 경고색 + 흰글씨)
    "info":          "#6BA1FF",   # 청색(직접수정 등)
    # 교정 소스 뱃지
    "dict_bg":     "#2C2415", "dict_fg":   "#E8B468", "dict_border":   "#4A3A1C",
    "typo_bg":     "#16301F", "typo_fg":   "#6FD899", "typo_border":   "#225538",
    "polish_bg":   "#262339", "polish_fg": "#A99CF0", "polish_border": "#3C3766",
    # 활동 로그 레벨
    "log_info":    "#6E7884",
    "log_ok":      "#54D17F",
    "log_warn":    "#E8B468",
    "log_err":     "#F1726E",
}


# ══════════════════════════════════════════════════════════════
# ▌현재 모드 상태 + 라이브 매핑(COLORS)
# ══════════════════════════════════════════════════════════════

_MODE = "light"
PALETTES = {"light": LIGHT, "dark": DARK}

# COLORS는 항상 "현재 팔레트"를 가리키는 라이브 dict.
# 같은 dict 객체를 유지(.clear/.update)하므로 import 시점 참조가 깨지지 않는다.
COLORS = dict(LIGHT)


def current_mode() -> str:
    return _MODE


def current_palette() -> dict:
    return PALETTES[_MODE]


def set_mode(mode: str):
    """현재 테마 모드 설정 + COLORS 라이브 매핑 갱신."""
    global _MODE
    _MODE = "dark" if str(mode).lower() == "dark" else "light"
    COLORS.clear()
    COLORS.update(PALETTES[_MODE])


# ══════════════════════════════════════════════════════════════
# ▌글로벌 QSS 템플릿  ($token = 팔레트 값, { } = CSS 블록)
# ══════════════════════════════════════════════════════════════

_QSS_TEMPLATE = Template("""
/* ── 전역 기본 ──────────────────────────────── */
QWidget {
    font-family: "Pretendard";
    font-size: 13px;
    color: $text;
    background-color: $bg;
}
QMainWindow, QDialog, QStackedWidget { background-color: $bg; }
QLabel { background: transparent; }
QToolTip {
    font-family: "Pretendard"; font-size: 12px;
    background: $surface; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 5px 8px;
}
/* 팝업/대화상자 — 폰트를 Pretendard로 통일(시스템 기본 폰트 폴백 방지) */
QMessageBox, QMessageBox QLabel, QInputDialog, QInputDialog QLabel {
    font-family: "Pretendard";
}

/* ── 버튼: 기본 리셋 ────────────────────────── */
QPushButton {
    background: $surface_alt; color: $text_sub;
    border: 1px solid $border; border-radius: 10px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
    min-height: 18px;
}
QPushButton:hover { background: $surface_hover; }
QPushButton:disabled { color: $text_dim; background: $surface_alt; border-color: $border; }

/* primary */
QPushButton[variant="primary"] {
    background: $accent; color: $accent_fg; border: none;
    border-radius: 10px; padding: 10px 20px; font-weight: 700;
}
QPushButton[variant="primary"]:hover  { background: $accent_hover; }
QPushButton[variant="primary"]:pressed{ background: $accent_press; }
QPushButton[variant="primary"]:disabled { background: $border; color: $text_dim; }

/* userchip — ks-works 헤더 사용자 칩과 동일한 라임 pill(검정 글씨)
   pill은 '고정 높이 + 반경=높이/2'로 보장한다. Qt QSS는 과대 border-radius(예: 999px)를
   무시하고 기본값으로 폴백할 수 있어, icon variant(32px/16px=원형)와 동일한 방식을 쓴다. */
QPushButton[variant="userchip"] {
    background: $lime; color: $lime_fg; border: none;
    border-radius: 15px; padding: 0 16px;
    min-height: 30px; max-height: 30px; font-weight: 600;
}
QPushButton[variant="userchip"]:hover  { background: $lime_hover; }
QPushButton[variant="userchip"]:pressed{ background: $lime_press; }
QPushButton[variant="userchip"]:disabled { background: $border; color: $text_dim; }

/* ghost — surface_alt보다 한 단계 진한 surface_hover로 라이트·다크 양쪽에서 더 또렷하게 */
QPushButton[variant="ghost"] {
    background: $surface_hover; color: $text_sub; border: 1px solid $border_strong;
}
QPushButton[variant="ghost"]:hover { background: $border_strong; color: $text; }

/* success */
QPushButton[variant="success"] {
    background: $success_bg; color: $success; border: 1px solid $success_border;
}
QPushButton[variant="success"]:hover { background: $success_border; }

/* success_solid (진한 녹색, 다크모드 공통) */
QPushButton[variant="success_solid"] {
    background: #157F3C; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 10px 20px; font-weight: 700;
}
QPushButton[variant="success_solid"]:hover { background: #10602D; }

/* danger */
QPushButton[variant="danger"] {
    background: $error_bg; color: $error; border: 1px solid $error_border;
}
QPushButton[variant="danger"]:hover { background: $error_border; }

/* danger_solid */
QPushButton[variant="danger_solid"] {
    background: $error; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 9px 16px; font-weight: 600;
}
QPushButton[variant="danger_solid"]:hover { background: #b82e2e; }

/* action_mint */
QPushButton[variant="action_mint"] {
    background: #84CC16; color: #1A1D23; border: none;
    border-radius: 10px; padding: 10px 20px; font-weight: 700;
}
QPushButton[variant="action_mint"]:hover { background: #65A30D; color: #FFFFFF; }
QPushButton[variant="action_mint"]:pressed { background: #4D7C0F; color: #FFFFFF; }
QPushButton[variant="action_mint"]:disabled { background: $border; color: $text_dim; }

/* action_pink */
QPushButton[variant="action_pink"] {
    background: #FF4081; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 10px 20px; font-weight: 700;
}
QPushButton[variant="action_pink"]:hover { background: #F50057; }
QPushButton[variant="action_pink"]:pressed { background: #C51162; }
QPushButton[variant="action_pink"]:disabled { background: $border; color: $text_dim; }

/* icon (원형) */
QPushButton[variant="icon"] {
    background: $border; color: $text_sub; border: none;
    border-radius: 16px; min-width: 32px; max-width: 32px;
    min-height: 32px; max-height: 32px; padding: 0; font-size: 14px;
}
QPushButton[variant="icon"]:hover { background: $border_strong; color: $text; }
/* 창 종료 컨트롤 — 빨강 호버 */
QPushButton[variant="icon"][winctl="close"]:hover { background: $error_bg; color: $error; }

/* link (텍스트 버튼) */
QPushButton[variant="link"] {
    background: transparent; border: none; color: $accent;
    padding: 4px 6px; font-size: 12px; font-weight: 600;
}
QPushButton[variant="link"]:hover { color: $accent_hover; }

/* 세그먼트 아이콘 버튼(카드 내 수락/거절) */
QPushButton[variant="seg"] {
    border-radius: 8px; padding: 4px 9px; min-width: 18px; min-height: 16px;
}
QPushButton[variant="seg"][state="off"]        { background: $surface; border: 1px solid $border_strong; }
QPushButton[variant="seg"][state="off"]:hover  { background: $surface_hover; }
QPushButton[variant="seg"][state="on-accept"]  { background: $success; border: 1px solid $success; }
QPushButton[variant="seg"][state="on-reject"]  { background: $error;   border: 1px solid $error; }

/* ── 컨테이너 역할 ──────────────────────────── */
QFrame[role="card"] {
    background: $surface; border: 1px solid $border; border-radius: 12px;
}
QFrame[role="card"][status="accepted"] {
    background: $success_bg; border: 1px solid $success_border;
}
QFrame[role="card"][status="rejected"] {
    background: $error_bg; border: 1px solid $error_border;
}


QFrame[role="section"] {
    background: $surface; border: 1px solid $border; border-radius: 14px;
}
QFrame[role="inset"] {
    background: $surface_alt; border: 1px solid $border_light; border-radius: 10px;
}
QFrame[role="header"] {
    background: $surface; border-bottom: 1px solid $border;
}
QFrame[role="footer"] {
    background: $surface; border-top: 1px solid $border;
}
QFrame[role="rail"] {
    background: $surface; border-right: 1px solid $border;
}
/* 좌측 영구 사이드바(로고+네비+저작권 컬럼) */
QFrame[role="sidebar"] {
    background: $surface; border-right: 1px solid $border;
}
/* 사이드바 내부에 임베드된 스텝퍼 — 사이드바가 배경/테두리를 담당 */
QFrame[role="railEmbedded"] { background: transparent; border: none; }
QFrame[role="panel"] {
    background: $surface; border-left: 1px solid $border;
}
QFrame[role="divider"] { background: $border; border: none; }

/* ── 뱃지 / 칩 ──────────────────────────────── */
QLabel[role="badge"] {
    background: $surface_alt; color: $text_sub; border: 1px solid $border;
    border-radius: 6px; padding: 2px 8px; font-size: 10px; font-weight: 700;
}
QLabel[role="chip"] {
    background: $surface_alt; color: $text_sub; border: 1px solid $border;
    border-radius: 11px; padding: 3px 10px; font-size: 11px; font-weight: 600;
}
QLabel[tone="accent"]  { background: $accent_soft;   color: $accent;  border-color: $accent_soft; }
QLabel[tone="primary"] { background: $accent;        color: $accent_fg; border-color: $accent; }
QLabel[tone="success"] { background: $success_bg; color: $success; border-color: $success_border; }
QLabel[tone="error"]   { background: $error_bg;   color: $error;   border-color: $error_border; }
QLabel[tone="warning"] { background: $warning_bg; color: $warning; border-color: $warning_border; }

/* ── 텍스트 색 헬퍼 ─────────────────────────── */
QLabel[muted="true"]   { color: $text_muted; }
QLabel[role="h1"] { font-size: 18px; font-weight: 800; color: $text; }
QLabel[role="h2"] { font-size: 15px; font-weight: 700; color: $text; }
QLabel[role="title"] { font-size: 13px; font-weight: 700; color: $text; }
QLabel[role="sub"] { font-size: 12px; color: $text_muted; }
QLabel[role="text_sub"] { color: $text_sub; }
QLabel[role="copyright"] { font-size: 11px; color: $text_muted; }
QLabel[role="version"]   { font-size: 10px; color: $text_dim; }

/* 텍스트 전용 톤 (역할보다 우선순위를 갖기 위해 아래 배치) */
QLabel[tone="text_success"] { color: $success; }

/* ── 입력 ───────────────────────────────────── */
QLineEdit {
    background: $surface_alt; border: 1px solid $border; border-radius: 8px;
    padding: 8px 10px; font-size: 12px; color: $text;
}
QLineEdit:focus { border-color: $accent; }
QTextEdit, QPlainTextEdit, QTextBrowser {
    background: $surface_alt; border: 1px solid $border; border-radius: 10px;
    padding: 10px 12px; font-size: 11px; color: $text_sub;
}

/* ── 체크/라디오 ────────────────────────────── */
QCheckBox, QRadioButton {
    spacing: 8px; font-size: 12px; color: $text_sub; background: transparent;
}

/* ── 프로그레스 ─────────────────────────────── */
QProgressBar {
    border: none; border-radius: 4px; background: $surface_hover;
    text-align: center; font-size: 10px; color: $text_muted; max-height: 7px;
}
QProgressBar::chunk { border-radius: 4px; background: $accent; }

/* ── 스크롤 ─────────────────────────────────── */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $text_dim; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 8px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── 스플리터 ───────────────────────────────── */
QSplitter::handle { background: $border; }
QSplitter::handle:horizontal { width: 1px; }

/* ── 드롭존 ─────────────────────────────────── */
QFrame[role="dropzone"] {
    border: 2px dashed $border_strong; border-radius: 16px; background: $surface_alt;
}
QFrame[role="dropzone"][active="true"] {
    border: 2px dashed $accent; background: $accent_soft;
}
QFrame[role="dropzone_selected"] {
    border: 2px solid $accent; border-radius: 16px; background: $surface_alt;
}

/* ── 선택 카드(교정 방식) ───────────────────── */
QFrame[role="choice"] {
    background: $surface; border: 1px solid $border; border-radius: 12px;
}
QFrame[role="choice"]:hover { border-color: $accent; }
QFrame[role="choice"][selected="true"] {
    background: $accent_soft; border: 2px solid $accent;
}

/* ── 토글 행 ────────────────────────────────── */
QFrame[role="toggleRow"] {
    background: $surface_alt; border: 1px solid $border_light; border-radius: 10px;
}

/* ── 스텝퍼 레일 아이템 ─────────────────────── */
QFrame[role="railItem"] { background: transparent; border: 1px solid $border; border-radius: 12px; }
QFrame[role="railItem"][active="true"] { background: $accent_soft; border: 1px solid $accent; }

QLabel[role="stepNum"] {
    border-radius: 13px; font-size: 12px; font-weight: 700;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
}
QLabel[role="stepNum"][state="todo"]   { background: $surface_alt; color: $text_dim; border: 1px solid $border; }
QLabel[role="stepNum"][state="active"] { background: $accent; color: $accent_fg; }
QLabel[role="stepNum"][state="done"]   { background: $success_bg; color: $success; border: 1px solid $success_border; }
QLabel[role="stepNum"][state="error"]  { background: $error_bg; color: $error; border: 1px solid $error_border; }

QLabel[role="stepTitle"] { font-size: 12px; font-weight: 600; }
QLabel[role="stepTitle"][state="todo"]   { color: $text_dim; font-weight: 500; }
QLabel[role="stepTitle"][state="active"] { color: $text; font-weight: 700; }
QLabel[role="stepTitle"][state="done"]   { color: $text; }
QLabel[role="stepTitle"][state="error"]  { color: $error; }
QLabel[role="stepSub"] { font-size: 11px; }
QLabel[role="stepSub"][state="todo"] { color: $text_muted; }
QLabel[role="stepSub"][state="active"] { color: $text; font-weight: 600; }
QLabel[role="stepSub"][state="done"] { color: $text_sub; }
QLabel[role="stepSub"][state="error"] { color: $error; }
""")


def build_qss(palette: dict) -> str:
    """팔레트로 글로벌 QSS 문자열 생성."""
    return _QSS_TEMPLATE.substitute(palette)


# ══════════════════════════════════════════════════════════════
# ▌적용 API
# ══════════════════════════════════════════════════════════════

def apply_theme(app, mode: str = None):
    """QApplication에 현재(또는 지정) 모드의 글로벌 QSS 적용."""
    if mode is not None:
        set_mode(mode)
    app.setStyleSheet(build_qss(current_palette()))


def restyle(widget):
    """동적 property 변경 후 QSS 재적용(unpolish→polish)."""
    s = widget.style()
    s.unpolish(widget)
    s.polish(widget)
    widget.update()
