"""
ui/widgets/review_panel.py — 교정 항목 검토 패널
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
좌측: 원고 미리보기(본문 + 교정 위치 하이라이트)
우측: 교정 제안 카드 — 본문 등장 위치마다 카드 1개(반복 단어는 N개 모두 표출,
      각 카드에 '반복 k/n' 표시). 카드별 수락/거절(분리된 아이콘) + 상태별 카드
      색상 차별화.
반복 일괄 처리: 어떤 반복 카드든 사용자가 수락/거절을 선택하면 같은 단어의
      나머지(사용자가 직접 만지지 않은) 반복 카드에 같은 선택이 자동 전파된다.
      자동 처리된 카드를 반대로 뒤집으면 확인 팝업 후 그 카드만 반대 허용.
용어 일관성 통일: 띄어쓰기 일관성 카드(consistency_flip)는 '개별 항목 수락/거절'이
      아니라 '문서 전체를 어느 표기로 통일할지'의 선택이다. 수락=교정 표기로,
      거절=원문 표기로 통일(반대 방향 교정을 즉시 합성·수락). 두 방향 모두 확인
      팝업을 거치며, 그룹 전체가 함께 움직인다(부분 처리 없음).
적용 실행은 워크스페이스 footer가 트리거한다.
"""

import html
import time

from PySide6.QtCore import Qt, Signal, QTimer, QRect, QSize, QPoint
from PySide6.QtGui import QTextOption, QPainter, QFont, QColor, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDialog, QPushButton,
    QGraphicsDropShadowEffect,
    QScrollArea, QFrame, QTextBrowser, QSplitter, QTextEdit, QLayout, QSizePolicy,
)

from ui.widgets.components import (
    label, sub_label, divider, chip, badge, IconButton,
    AnimatedGradientBorder
)
from ui.styles.theme import current_palette, restyle, LIGHT


_SOURCE_LABEL = {"dict": "사전검증", "ai_typo": "AI 오탈자", "ai_polish": "AI 윤문",
                 "dict_flag": "사전 검수", "spacing": "띄어쓰기", "punct": "문장부호"}

# 검수 필요(미등재 dict_flag·저신뢰)는 카테고리와 배타로 한 묶음(반드시 사용자 확인).
REVIEW_CAT = "검수 필요"

# '미적용' 칩 클릭 시 적용/거절 미선택(pending) 카드만 표출하는 특수 필터 토큰.
PENDING_FILTER = "__pending__"


class _GradientTextLabel(QLabel):
    """텍스트에 좌→우 선형 그라디언트를 입히는 QLabel."""

    def __init__(self, text: str, stops: list[tuple[float, str]],
                 font_size: int = 13, font_weight: int = 700,
                 parent: QWidget | None = None):
        super().__init__(text, parent)
        self._stops = stops
        f = self.font()
        f.setPixelSize(font_size)
        f.setWeight(QFont.Weight(font_weight))
        self.setFont(f)
        # 배경 투명 — paintEvent 에서 직접 그린다
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent; color: transparent;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.font())
        grad = QLinearGradient(0, 0, self.width(), 0)
        for pos, color in self._stops:
            grad.setColorAt(pos, QColor(color))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        # drawText 로 글리프 경로를 채운다
        from PySide6.QtGui import QPainterPath, QFontMetrics
        fm = QFontMetrics(self.font())
        path = QPainterPath()
        path.addText(0, fm.ascent(), self.font(), self.text())
        p.fillPath(path, grad)
        p.end()

    def minimumSizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()), fm.height())

    def sizeHint(self):
        return self.minimumSizeHint()


def _needs_review(c: dict) -> bool:
    return c.get("source") == "dict_flag" or c.get("confidence") == "low"


_ZWSP = "\u200b"   # ZERO WIDTH SPACE


def _soft_breakable(s: str) -> str:
    """표시 전용 라벨 텍스트에 ZWSP(폭 0 공백)를 끼워 넣어 어디서든 줄바꿈 가능하게.

    '나타난다.정보통신기획평가원(2025a)·…' 같은 무공백 장문은 Qt 줄바꿈 단위가
    수백 px가 돼 카드 최소 폭을 밀어 올려 가로 스크롤을 만든다(실측 339px).
    영문·숫자 연속 구간(2025a, OECD 등) 안에는 넣지 않아 토큰 중간이 끊기지 않는다.
    """
    if not s:
        return s
    out = [s[0]]
    prev = s[0]
    for ch in s[1:]:
        if not (prev.isascii() and prev.isalnum() and ch.isascii() and ch.isalnum()):
            out.append(_ZWSP)
        out.append(ch)
        prev = ch
    return "".join(out)


class GrowingTextEdit(QTextEdit):
    """카드 폭에 맞춰 줄바꿈(WrapAnywhere)하고 내용 높이만큼 세로로 자라는
    교정값 인라인 입력기 — QLineEdit는 줄바꿈이 안 돼 긴 교정값이 가로로
    잘리던 것을 대체한다. Enter=입력 확정(개행 금지), 포커스 아웃 시
    editingFinished 발신(QLineEdit 인터페이스 호환: text()도 제공)."""

    editingFinished = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(2)
        self.setPlainText(text)
        self.textChanged.connect(self._sync_height)
        self._sync_height()

    def text(self) -> str:
        # 개행은 값에 남기지 않는다(교정값은 한 줄 성격 — 붙여넣기 방어).
        return self.toPlainText().replace("\r", "").replace("\n", " ")

    def insertFromMimeData(self, source):
        self.insertPlainText((source.text() or "").replace("\r", "").replace("\n", " "))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.clearFocus()          # 확정 — editingFinished는 focusOut에서 발신
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.editingFinished.emit()

    def wheelEvent(self, e):
        e.ignore()                     # 내부 스크롤 없음 — 카드 목록 스크롤로 넘김

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_height()

    def _sync_height(self):
        doc = self.document()
        w = self.viewport().width()
        if w > 0:
            doc.setTextWidth(w)
        # 문서 높이(docMargin 포함) + 크롬(보더·패딩). frameWidth()는 QSS 보더가
        #   있으면 5를 반환해(실제 1px) 높이를 10px 과대계산 → '빈 줄 한 줄'처럼
        #   보였다. 실측 크롬(위젯-뷰포트 높이 차)을 쓰고, 레이아웃 전엔 4로 가정
        #   (QSS: 보더 1px×2 + 패딩 1px×2).
        chrome = self.height() - self.viewport().height()
        if not (2 <= chrome <= 12):
            chrome = 4
        h = int(doc.size().height()) + chrome
        h = max(h, self.fontMetrics().height() + chrome)
        if h != self.height():
            self.setFixedHeight(h)


def _display_category(c: dict) -> str:
    """카드/집계 공통 카테고리 — 검수 필요는 카테고리보다 우선(배타 집계)."""
    if _needs_review(c):
        return REVIEW_CAT
    return c.get("category") or _SOURCE_LABEL.get(c.get("source", "dict"), "교정")


def _pill_qss(pal: dict, kind: str) -> str:
    """집계칩·카드 카테고리 배지 공통 알약(pill) 스타일 — 라운드풀·색/글자색 통일.

    kind: 'primary'(전체/활성 필터) · 'review'(검수 필요) ·
          'review_active'(선택된 검수 — 진한 경고색+흰글씨) · 'normal'(그 외).
    """
    # bd=테두리색. normal·review는 카드 배경과 잘 구분되도록 대비 테두리를 둔다.
    #   primary·review_active는 배경색과 같은 색 테두리를 둬(보이지 않음) 박스 크기만 통일.
    if kind == "primary":
        fg, bg = pal["accent_fg"], pal["accent"]
        bd = bg
    elif kind == "review_active":
        fg, bg = "#FFFFFF", pal.get("warning_strong", pal["warning"])
        bd = bg
    elif kind == "review":
        fg, bg = pal["warning"], pal.get("warning_bg", pal["surface_alt"])
        bd = pal.get("warning_border", pal["warning"])
    else:
        fg, bg = pal["text_sub"], pal["surface_alt"]
        bd = pal["border_strong"]
    return (f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:11px; "
            f"padding:3px 11px; font-size:12px; font-weight:400;")


def _outline_pill_qss(color: str, border_color: str = None) -> str:
    """배경 없는(테두리만) 알약 칩 — 글자색=color, 테두리=border_color(없으면 color).

    color는 팔레트에서 받은 테마 대응 색(예: text=흰/검, success=녹, error=적, info=청).
    글자는 레귤러(font-weight:400) — 숫자만 <b>로 볼드 처리한다(_count_html).
    """
    bd = border_color or color
    return (f"color:{color}; background:transparent; border:1px solid {bd}; "
            f"border-radius:11px; padding:3px 11px; font-size:12px; font-weight:400;")


def _ghost_pill_qss(pal: dict, fg: str) -> str:
    """배경=고스트, 글자색만 지정하는 알약 칩(적용/거절/직접수정용).

    surface_alt보다 한 단계 진한 surface_hover를 써서 라이트(흰 배경)·다크 양쪽에서
    카드 배경과 대비가 더 또렷하게(=더 잘 보이게) 한다.
    """
    bg = pal.get("surface_hover", pal["surface_alt"])
    return (f"color:{fg}; background:{bg}; border:none; "
            f"border-radius:11px; padding:3px 11px; font-size:12px; font-weight:400;")


def _count_html(prefix: str, n: int, suffix: str = "건") -> str:
    """칩 텍스트 — 라벨은 레귤러, 숫자 n만 볼드(<b>). 3-6 요구."""
    import html as _h
    return f"{_h.escape(prefix)}<b>{n}</b>{_h.escape(suffix)}"


class FlowLayout(QLayout):
    """좌→우로 채우다 폭이 모자라면 다음 줄로 넘기는 레이아웃(칩 줄바꿈용).

    각 아이템은 sizeHint 너비를 그대로 가져 글자가 잘리지 않는다. 컨테이너 위젯의
    sizePolicy에 heightForWidth를 켜면 줄 수에 맞춰 높이가 동적으로 늘어난다.
    """
    def __init__(self, parent=None, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_h = rect.x(), rect.y(), 0
        right = rect.right()
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            next_x = x + w
            if next_x > right and line_h > 0:
                x = rect.x()
                y = y + line_h + self._vspace
                next_x = x + w
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x = next_x + self._hspace
            line_h = max(line_h, h)
        return y + line_h - rect.y()


class DeltaScrollSync:
    """두 스크롤바 간의 절대값이 아닌 상태 변화량(delta)만 동기화하여 서로 다른 길이를 가진 텍스트 뷰가 각각의 앵커 위치를 유지하며 스크롤되도록 함."""
    def __init__(self, sb1, sb2):
        self.sb1 = sb1
        self.sb2 = sb2
        self.sb1_last = sb1.value()
        self.sb2_last = sb2.value()
        self.active = False
        self.suspended = False

        self.sb1.valueChanged.connect(self.on_sb1_changed)
        self.sb2.valueChanged.connect(self.on_sb2_changed)

    def on_sb1_changed(self, val):
        if self.active or self.suspended:
            if self.suspended:
                self.sb1_last = val
            return
        self.active = True
        delta = val - self.sb1_last
        self.sb1_last = val
        
        new_val2 = self.sb2.value() + delta
        self.sb2.setValue(new_val2)
        self.sb2_last = self.sb2.value()
        self.active = False

    def on_sb2_changed(self, val):
        if self.active or self.suspended:
            if self.suspended:
                self.sb2_last = val
            return
        self.active = True
        delta = val - self.sb2_last
        self.sb2_last = val
        
        new_val1 = self.sb1.value() + delta
        self.sb1.setValue(new_val1)
        self.sb1_last = self.sb1.value()
        self.active = False
        
    def suspend(self):
        self.suspended = True
        
    def resume(self):
        self.sb1_last = self.sb1.value()
        self.sb2_last = self.sb2.value()
        self.suspended = False


# ══════════════════════════════════════════════════════════════
# 라이트 고정 카드 표면 — 확인 팝업(LightConfirmDialog)과 토스트가 공유한다.
#   앱이 다크 모드여도 항상 라이트: 전역 QSS를 이기도록 모든 시각 속성을
#   objectName 선택자(#lcd*)로 다시 선언한다. 두 위젯은 서로 다른 트리에서
#   각자 로컬 stylesheet로 쓰므로 objectName이 겹쳐도 무해하다.
# ══════════════════════════════════════════════════════════════
LIGHT_CARD_QSS = f"""
    QFrame#lcdBox {{ background:{LIGHT['surface']};
                     border:1px solid {LIGHT['border']}; border-radius:12px; }}
    QLabel#lcdTitle {{ color:{LIGHT['text']}; background:transparent; border:none;
                       font-size:15px; font-weight:700; }}
    QFrame#lcdLine {{ background:{LIGHT['border']}; border:none; }}
    QLabel#lcdMsg {{ color:{LIGHT['text_sub']}; background:transparent; border:none;
                     font-size:13px; font-weight:400; }}
"""

_CARD_MIN_W = 320
_CARD_MAX_W = 460


def build_light_card(title: str, message: str, margins=(22, 18, 22, 18)):
    """헤드타이틀 + 구분선 + 본문 한 줄로 된 라이트 고정 카드 — (box, title_lbl, msg_lbl).

    호출자가 box.layout()에 버튼 행 등을 이어 붙일 수 있다. 스타일시트는 붙이지
    않는다(호출자가 LIGHT_CARD_QSS를 자신의 트리에 적용).
    """
    box = QFrame()
    box.setObjectName("lcdBox")
    shadow = QGraphicsDropShadowEffect(box)
    shadow.setBlurRadius(34)
    shadow.setOffset(0, 8)
    shadow.setColor(QColor(0, 0, 0, 90))
    box.setGraphicsEffect(shadow)

    lay = QVBoxLayout(box)
    lay.setContentsMargins(*margins)
    lay.setSpacing(0)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("lcdTitle")
    # 크기 계산(sizeHint·fontMetrics)은 QSS의 font-size를 반영하지 않는다 —
    #   폭 산출이 필요한 라벨은 QFont로도 같은 크기를 직접 지정한다.
    tf = title_lbl.font()
    tf.setPixelSize(15)
    tf.setWeight(QFont.Weight(700))
    title_lbl.setFont(tf)
    lay.addWidget(title_lbl)
    lay.addSpacing(13)

    line = QFrame()
    line.setObjectName("lcdLine")
    line.setFixedHeight(1)
    lay.addWidget(line)
    lay.addSpacing(16)

    msg_lbl = QLabel(message)
    msg_lbl.setObjectName("lcdMsg")
    msg_lbl.setWordWrap(True)
    msg_lbl.setTextFormat(Qt.PlainText)
    mf = msg_lbl.font()
    mf.setPixelSize(13)
    msg_lbl.setFont(mf)
    set_card_msg_width(msg_lbl, message)
    lay.addWidget(msg_lbl)
    return box, title_lbl, msg_lbl


def set_card_msg_width(msg_lbl: QLabel, message: str):
    """wordWrap 라벨은 sizeHint 폭이 최소값으로 붕괴해(실측 230px) 한 줄 문구가
    3줄로 접힌다 — 실제 글자 폭을 재서 [_CARD_MIN_W, _CARD_MAX_W] 폭을 보장한다."""
    natural = msg_lbl.fontMetrics().horizontalAdvance(message) + 8
    msg_lbl.setMinimumWidth(max(min(natural, _CARD_MAX_W), _CARD_MIN_W))
    msg_lbl.setMaximumWidth(_CARD_MAX_W)


class LightConfirmDialog(QDialog):
    """라이트 테마 고정 프레임리스 확인 팝업(용어 일관성 통일 등).

    · 앱이 다크 모드여도 항상 라이트 — 전역 QSS는 objectName 로컬 선택자로 덮는다.
    · 헤드타이틀을 본문 안에 직접 그리므로 OS 타이틀바 색(다크 프레임)에 좌우되지 않는다.
    · 기본 버튼 = '확인'(강조), Esc/취소 = 거부.
    """

    def __init__(self, parent, title: str, message: str,
                 yes_text: str = "확인", no_text: str = "취소"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle(title)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)   # 그림자 여백
        outer.setSpacing(0)

        box, _title_lbl, _msg_lbl = build_light_card(title, message)
        outer.addWidget(box)

        lay = box.layout()
        lay.addSpacing(20)
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)
        btns.addStretch()
        no_btn = QPushButton(no_text)
        no_btn.setObjectName("lcdNo")
        no_btn.setCursor(Qt.PointingHandCursor)
        no_btn.setAutoDefault(False)
        no_btn.clicked.connect(self.reject)
        yes_btn = QPushButton(yes_text)
        yes_btn.setObjectName("lcdYes")
        yes_btn.setCursor(Qt.PointingHandCursor)
        yes_btn.setDefault(True)
        yes_btn.clicked.connect(self.accept)
        btns.addWidget(no_btn)
        btns.addWidget(yes_btn)
        lay.addLayout(btns)

        # 라이트 팔레트 고정 — 전역(앱) QSS의 QLabel/QPushButton 규칙을 덮도록
        #   모든 시각 속성을 objectName 선택자로 명시한다.
        self.setStyleSheet(LIGHT_CARD_QSS + f"""
            QPushButton#lcdNo {{ color:{LIGHT['text_sub']}; background:{LIGHT['surface']};
                                 border:1px solid {LIGHT['border_strong']}; border-radius:7px;
                                 padding:8px 18px; font-size:13px; font-weight:500; }}
            QPushButton#lcdNo:hover {{ background:{LIGHT['surface_alt']}; }}
            QPushButton#lcdYes {{ color:{LIGHT['accent_fg']}; background:{LIGHT['accent']};
                                  border:1px solid {LIGHT['accent']}; border-radius:7px;
                                  padding:8px 22px; font-size:13px; font-weight:700; }}
            QPushButton#lcdYes:hover {{ background:{LIGHT['accent_hover']};
                                        border-color:{LIGHT['accent_hover']}; }}
            QPushButton#lcdYes:pressed {{ background:{LIGHT['accent_press']};
                                          border-color:{LIGHT['accent_press']}; }}
        """)
        self.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        # 앱 창 정중앙 — 프레임리스라 OS가 위치를 잡아 주지 않는다.
        host = self.parent().window() if self.parent() else None
        if host is not None:
            geo = host.frameGeometry()
            self.move(geo.center() - self.rect().center())

    @classmethod
    def ask(cls, parent, title: str, message: str,
            yes_text: str = "확인", no_text: str = "취소") -> bool:
        dlg = cls(parent, title, message, yes_text, no_text)
        ok = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()
        return ok


class ReviewPanel(QWidget):
    counts_changed = Signal(int, int, int)   # pending, accepted, total (고유 단어 기준)

    # ── 카드 점진 로딩 튜닝 (2026-07-21 실측 기반) ────────────────────────
    #   카드 1장 = 생성 4.1ms + 삽입·레이아웃 ~4ms, 그리고 **배치 1회마다 ~30ms
    #   고정비**(전체 목록 레이아웃 패스)가 붙는다. 그래서 50장 배치 = ~433ms 프리즈,
    #   반대로 1장씩 쪼개면 장당 33ms라 총량이 폭증한다 — 배치 크기만으론 못 푼다.
    #   해법: ① 첫 배치는 화면을 채울 만큼만 ② 나머지는 **사용자가 조작하지 않는 틈**에
    #   프리페치(조작 즉시 중단) ③ 그래도 바닥에 닿으면 작은 배치로 즉시 보충.
    _INITIAL_CARDS = 10     # 첫 화면(카드 ~4장 보임)을 채우고 조금 남는 정도
    _SCROLL_CHUNK = 8       # 프리페치보다 빨리 내려갔을 때 즉시 보충
    _IDLE_CHUNK = 4         # 유휴 프리페치 1틱 — 고정비가 크므로 잘게 쪼개도 이득 없음
    _IDLE_TICK_MS = 30      # 틱 간격(UI가 숨 쉴 틈)
    _IDLE_QUIET_MS = 250    # 마지막 조작 후 이만큼 조용해야 프리페치 재개
    _PREFETCH_AHEAD = 2     # 화면 아래로 유지할 여유분(뷰포트 높이 배수)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._corrections = []   # 고유 교정 목록(적용/카운트용)
        self._occ = []           # 등장 위치별 항목(카드 1:1)
        self._cards = []
        self._options = {}
        self._full_text = ""
        self._active_ci = None
        self._build_ui()
        self._build_toast()

    # ══════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(0)
        root.addWidget(self._build_body(), 1)
        self._apply_card_theme()

    def _build_body(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(16)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        splitter.setChildrenCollapsible(False)
        # 세 칸 모두 동일한 작은 최소 너비 → 어떤 칸도 1/3보다 커지지 않게(1:1:1 보장)
        for wdg in (self._build_source_panel(), self._build_preview_panel(),
                    self._build_card_panel()):
            wdg.setMinimumWidth(140)
            splitter.addWidget(wdg)
        for i in range(3):
            splitter.setStretchFactor(i, 1)
        splitter.setSizes([10000, 10000, 10000])
        # 3단 1:1:1 항상 유지 — 사용자가 핸들을 드래그하면 즉시 균등 복원.
        splitter.splitterMoved.connect(lambda *_: self._equalize_splitter())
        self._splitter = splitter
        return splitter

    def _equalize_splitter(self):
        sp = getattr(self, "_splitter", None)
        if sp is None:
            return
        w = max(sp.width(), 3)
        third = w // 3
        sizes = [third, third, w - 2 * third]
        if sp.sizes() != sizes:
            sp.blockSignals(True)
            sp.setSizes(sizes)
            sp.blockSignals(False)

    def _build_source_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "section")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 14)
        hdr.addWidget(sub_label("원문"))
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(divider())
        lay.addSpacing(14)

        self._source_view = QTextBrowser()
        self._source_view.setOpenExternalLinks(False)
        self._source_view.setFrameShape(QFrame.NoFrame)
        self._source_view.setStyleSheet("background: transparent; border: none;")
        self._source_view.anchorClicked.connect(self._on_text_clicked)
        
        from ui.widgets.components import SmoothScrollFilter
        self._source_smooth = SmoothScrollFilter(self._source_view.verticalScrollBar(), self)
        self._source_view.viewport().installEventFilter(self._source_smooth)
        
        # 스크롤 동기화 설정은 _build_preview_panel 이후에 연결
        lay.addWidget(self._source_view, 1)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "section")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 14)
        hdr.addWidget(sub_label("교정문"))
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(divider())
        lay.addSpacing(14)

        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._preview.setFrameShape(QFrame.NoFrame)
        self._preview.setStyleSheet("background: transparent; border: none;")
        self._preview.anchorClicked.connect(self._on_text_clicked)
        
        from ui.widgets.components import SmoothScrollFilter
        self._preview_smooth = SmoothScrollFilter(self._preview.verticalScrollBar(), self)
        self._preview.viewport().installEventFilter(self._preview_smooth)
        
        lay.addWidget(self._preview, 1)
        
        # 스크롤 동기화 (상대적 델타 동기화)
        self._scroll_sync = DeltaScrollSync(self._source_view.verticalScrollBar(), self._preview.verticalScrollBar())
        return panel

    def _build_card_panel(self) -> QWidget:
        self._inner_card = QFrame()
        
        lay = QVBoxLayout(self._inner_card)
        lay.setContentsMargins(6, 0, 6, 12)
        lay.setSpacing(0)

        # 헤더
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 14, 10, 10)
        bar.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        _icon = QLabel("✦")
        _icon.setStyleSheet("font-size: 14px; color: #7B5CFF; background: transparent;")
        title_row.addWidget(_icon)
        title_row.addWidget(_GradientTextLabel(
            "교정 제안",
            stops=[(0.0, "#A88BFF"), (0.5, "#5BB3FF"), (1.0, "#3DD9D6")],
        ))
        bar.addLayout(title_row)
        bar.addStretch()
        bar_w = QFrame()
        bar_w.setLayout(bar)
        bar_w.setProperty("role", "header")
        lay.addWidget(bar_w)

        # 집계 칩 2행 — 1행: 전체/미적용/검수 필요(필터), 2행: 적용/거절/직접수정(상태).
        self._chips_box = QWidget()
        chips_v = QVBoxLayout(self._chips_box)
        chips_v.setContentsMargins(12, 2, 12, 0)
        chips_v.setSpacing(13)   # 두 행 사이 간격을 넉넉히
        # 칩 묶음의 자연 너비가 카드 컬럼의 최소 너비를 키워 3단 1:1:1을 깨뜨린다
        #   (기본 창 크기에서 카드 칸만 넓어짐). 너비 제약을 풀어 칸이 1/3로 줄 수 있게 한다.
        #   카테고리 칩은 FlowLayout으로 폭이 모자라면 다음 줄로 넘겨 글자가 잘리지 않게 한다.
        chips_v.setSizeConstraint(QLayout.SetNoConstraint)
        self._chips_box.setMinimumWidth(0)
        box_sp = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box_sp.setHeightForWidth(True)   # 카테고리 줄바꿈에 따라 높이가 늘게
        self._chips_box.setSizePolicy(box_sp)

        # 1행: 전체·미적용·검수 필요(필터) — 1:1:1 균등 폭으로 가로 꽉 채움.
        self._title_chips_lay = QHBoxLayout()
        self._title_chips_lay.setSpacing(6)

        # 2행: 상태 칩(적용/거절/직접수정) — 1:1:1 균등 폭으로 가로 꽉 채움.
        self._status_row_lay = QHBoxLayout()
        self._status_row_lay.setSpacing(6)

        # (예비) 카테고리 필터 줄 — 현재는 검수 필요가 1행으로 이동해 비어 있음.
        self._cat_row_w = QWidget()
        self._cat_flow = FlowLayout(self._cat_row_w, hspacing=6, vspacing=6)
        cat_sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        cat_sp.setHeightForWidth(True)
        self._cat_row_w.setSizePolicy(cat_sp)

        chips_v.addLayout(self._title_chips_lay)
        chips_v.addLayout(self._status_row_lay)
        chips_v.addWidget(self._cat_row_w)
        lay.addWidget(self._chips_box)

        # 칩 섹션 ↔ 카드 목록 구분선 — 위아래 여백을 2.5배(8→20)로 넉넉히.
        #   role="divider" 글로벌 QSS는 _inner_card의 로컬 'background:surface' 캐스케이드에
        #   덮여 안 보였다 → 카드 내부 구분선과 동일하게 로컬 스타일시트로 직접 칠한다.
        div_wrap = QWidget()
        dwl = QVBoxLayout(div_wrap)
        dwl.setContentsMargins(12, 20, 12, 20)
        dwl.setSpacing(0)
        self._sec_divider = QFrame()
        self._sec_divider.setFixedHeight(1)
        self._sec_divider.setStyleSheet(
            f"background: {current_palette()['border']}; border: none;")
        dwl.addWidget(self._sec_divider)
        lay.addWidget(div_wrap)

        self._active_filter = None   # None=전체, PENDING_FILTER=미적용, 그 외=카테고리

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 카드 텍스트는 전부 세로 줄바꿈(원본=ZWSP 라벨, 교정=GrowingTextEdit)으로
        #   처리하므로 가로 스크롤은 항상 끈다 — 기본 창 크기에서 카드가 잘리며
        #   가로 스크롤바가 생기던 문제의 마지막 안전망.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QWidget#ScrollContent { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        self._card_scroll = scroll
        self._new_scroll_content()
        self._loaded_card_count = 0
        scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # 유휴 프리페치 타이머 — 사용자가 조작하지 않는 틈에 카드를 조금씩 미리 만든다.
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setInterval(self._IDLE_TICK_MS)
        self._prefetch_timer.timeout.connect(self._prefetch_tick)
        self._last_interaction = 0.0

        from ui.widgets.components import SmoothScrollFilter
        self._card_smooth = SmoothScrollFilter(scroll.verticalScrollBar(), self)
        scroll.viewport().installEventFilter(self._card_smooth)
        
        lay.addWidget(scroll, 1)
        
        wrap = AnimatedGradientBorder(self._inner_card, border_width=2, radius=14)
        wrap.set_animating(False)
        return wrap

    def _new_scroll_content(self):
        """카드 목록을 담을 새 콘텐츠 위젯으로 교체 — 옛 위젯은 QScrollArea가 파괴한다.

        카드를 한 장씩 `setParent(None)`으로 떼면 200장에 309ms가 든다(장당 1.5ms —
        위젯이 잠시 최상위 창이 되며 유령 창 위험까지 있는 그 경로). 콘텐츠를 통째로
        갈아끼우면 자식 트리가 C++에서 한 번에 파괴돼 64ms로 끝난다(실측 5배).
        ⚠ 교체 직후 옛 카드 위젯은 즉시 무효 — 호출자는 self._cards를 반드시 비울 것.
        """
        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("ScrollContent")
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setSpacing(9)
        self._scroll_layout.setContentsMargins(14, 12, 14, 14)
        self._scroll_layout.addStretch()
        self._card_scroll.setWidget(self._scroll_content)

    # ── 토스트 ───────────────────────────────────
    def _build_toast(self):
        """확인 팝업(LightConfirmDialog)과 같은 라이트 고정 카드 표면 — 헤드타이틀 +
        구분선 + 본문. 버튼만 없다(자동 소멸). 테마와 무관하므로 refresh_theme 불필요."""
        # 바깥 컨테이너는 그림자 여백만 담당(투명) — 그림자는 안쪽 box에 걸린다.
        self._toast_w = QWidget(self)
        self._toast_w.setObjectName("lcdToastWrap")
        # 앱 정중앙에 뜨므로 아래 콘텐츠 클릭을 가로채지 않게 마우스 통과(표시 전용).
        self._toast_w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        wrap = QVBoxLayout(self._toast_w)
        wrap.setContentsMargins(20, 20, 20, 20)
        wrap.setSpacing(0)
        box, self._toast_title, self._toast_lbl = build_light_card("", "")
        wrap.addWidget(box)
        # ⚠ 배경 없는 plain QWidget은 전역 QSS에 물들 수 있어 명시적으로 투명 고정.
        self._toast_w.setStyleSheet(
            "QWidget#lcdToastWrap { background: transparent; border: none; }"
            + LIGHT_CARD_QSS)
        self._toast_w.setVisible(False)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast_w.setVisible(False))

    def _toast(self, message: str, title: str = "반복 일괄 처리"):
        # 패널이 아닌 '앱 창' 정중앙에 표출 — 표시 시점에 최상위 창으로 옮겨
        #   사이드바/활동 패널에 의해 중심이 치우치지 않게 한다. (__init__ 시점엔
        #   아직 창에 안 붙어 있어 여기서 지연 reparent.)
        win = self.window() or self
        if self._toast_w.parentWidget() is not win:
            self._toast_w.setParent(win)
        self._toast_title.setText(title)
        self._toast_lbl.setText(message)
        set_card_msg_width(self._toast_lbl, message)
        self._toast_w.adjustSize()
        # 좁은 창에서 가로로 넘치지 않게 — 래퍼(20×2)+카드(22×2) 여백을 뺀 폭으로 제한.
        avail = max(200, win.width() - 40)
        if self._toast_w.width() > avail:
            self._toast_lbl.setMaximumWidth(max(140, avail - 84))
            self._toast_w.adjustSize()
        self._position_toast()
        self._toast_w.setVisible(True)
        self._toast_w.raise_()
        self._toast_timer.start(6000)

    def _position_toast(self):
        host = self._toast_w.parentWidget() or self
        tw = self._toast_w.width()
        th = self._toast_w.height()
        x = (host.width() - tw) // 2
        y = (host.height() - th) // 2
        self._toast_w.move(max(12, x), max(12, y))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._equalize_splitter()
        if self._toast_w.isVisible():
            self._position_toast()

    def _on_scroll(self, value):
        self._note_interaction()
        sb = self._card_scroll.verticalScrollBar()
        # 바닥에 닿기 전(80%)에 미리 보충 — 프리페치가 못 따라온 경우의 안전망.
        if sb.maximum() > 0 and value >= sb.maximum() * 0.8:
            self._load_more_cards(self._SCROLL_CHUNK)
        else:
            # 여유분이 줄었으면 유휴 프리페치를 다시 깨운다(버퍼가 차면 스스로 잔다).
            self._schedule_prefetch()

    # ── 유휴 프리페치 ─────────────────────────────
    def _note_interaction(self):
        """사용자 조작 시각 기록 — 조작 중에는 프리페치를 쉬게 해 끊김을 없앤다."""
        self._last_interaction = time.monotonic()

    def _schedule_prefetch(self):
        """남은 카드가 있으면 유휴 프리페치 시작, 다 만들었으면 정지."""
        if not hasattr(self, "_prefetch_timer"):
            return
        if self._loaded_card_count < len(self._occ):
            if not self._prefetch_timer.isActive():
                self._prefetch_timer.start()
        else:
            self._prefetch_timer.stop()

    def _prefetch_tick(self):
        if self._loaded_card_count >= len(self._occ):
            self._prefetch_timer.stop()
            return
        # 방금 스크롤·클릭이 있었으면 이번 틱은 건너뛴다(타이머는 계속 돈다).
        if (time.monotonic() - self._last_interaction) * 1000 < self._IDLE_QUIET_MS:
            return
        # 이미 화면 아래로 충분한 여유분이 쌓여 있으면 쉰다 — 카드를 무한정 미리
        #   만들지 않는다. 삽입 비용은 **이미 만들어 둔 카드 수에 비례**해 커지므로
        #   (150장 상태에서 1장 삽입도 ~77ms) 목록을 짧게 유지하는 게 곧 반응성이다.
        #   사용자가 내려오면 _on_scroll이 다시 깨운다.
        sb = self._card_scroll.verticalScrollBar()
        if sb.maximum() - sb.value() > self._PREFETCH_AHEAD * self._card_scroll.viewport().height():
            self._prefetch_timer.stop()
            return
        self._load_more_cards(self._IDLE_CHUNK)

    # ══════════════════════════════════════════════
    # 공개 API
    # ══════════════════════════════════════════════
    def load(self, corrections, options, file_name="", full_text=""):
        self._full_text = full_text or ""
        self._corrections = self._sort_by_position(corrections)
        self._options = options
        self._active_filter = None        # 새 문서 — 카테고리 필터 초기화
        self._build_occurrences()
        self._rebuild_cards()
        self._refresh_preview(keep_scroll=False)   # 새 문서는 맨 위에서 시작
        self._emit_counts()
        # 첫 표시 시 3단 1:1:1 강제(기본 창 크기에서도).
        QTimer.singleShot(0, self._equalize_splitter)

    def get_corrections(self) -> list:
        return self._corrections

    def _sort_by_position(self, corrections):
        if not self._full_text:
            return list(corrections)
        text = self._full_text
        end = len(text) + 1

        def pos(c):
            orig = c.get("original", "")
            idx = text.find(orig) if orig else -1
            return idx if idx >= 0 else end
        return sorted(corrections, key=pos)

    # ── 등장 위치별 항목 구성 ─────────────────────
    def _build_occurrences(self):
        self._occ = []
        text = self._full_text
        auto_apply = self._options.get("auto_apply", False)
        
        for ci, c in enumerate(self._corrections):
            if auto_apply:
                if c.get("status") == "rejected":
                    continue
                if c.get("source") == "dict_flag":
                    continue
                    
            orig = c.get("original", "")
            positions = []
            if text and orig:
                start = 0
                while True:
                    idx = text.find(orig, start)
                    if idx == -1:
                        break
                    positions.append(idx)
                    start = idx + len(orig)
            if not positions:
                positions = [None]   # 본문에서 못 찾음 → 합성 단일 항목
            total = len(positions)
            init = c.get("status", "pending")
            for k, p in enumerate(positions):
                self._occ.append({
                    "ci": ci, "c": c, "pos": p,
                    "end": (p + len(orig)) if p is not None else None,
                    "rep_index": k + 1, "rep_total": total,
                    "status": init, "shadowed": False,
                    # 반복 일괄 처리 플래그 — auto: 다른 반복 카드의 선택이 자동
                    #   전파된 상태, by_user: 사용자가 이 카드를 직접 선택(전파로
                    #   덮어쓰지 않음). _set_status 참조. 로드 시점에 상태가 이미
                    #   정해져 있으면(auto_apply 사전 결정 등) 일괄 처리로 취급해
                    #   반대 선택 시 확인 팝업을 거치게 한다.
                    "auto": init != "pending", "by_user": False,
                })
        # 문서 등장 순으로 정렬(없는 항목은 끝)
        self._occ.sort(key=lambda o: (o["pos"] is None, o["pos"] or 0))
        self._mark_stem_boundary_skips()
        self._resolve_overlaps()
        self._derive_all()

    def _mark_stem_boundary_skips(self):
        """순수 '분리' 띄어쓰기 교정('성장단계'→'성장 단계')은 결정론 다수결이 **어절
        base**(조사 제거) 기준으로 판정한 것이라, 원문 어절이 정확히 그 base일 때만
        적용해야 한다. 그러나 등장 탐색은 부분문자열이라 '성장단계별·성장단계론' 같은
        **더 긴 복합어**까지 잡아 반복 수(N)와 실제 치환을 근거 수치(13회)보다 부풀린다
        (사용자 보고: 근거 13회인데 반복 83). → 그런 복합어 등장을 excluded로 표시해
        카드/카운트/하이라이트에서 빼고(=shadowed) 적용에서 skip한다. 남는 등장은 어절
        base가 original과 정확히 일치하는 것(조사 변형 포함)뿐이라 근거 수치와 정합.

        dict_flag(검수 필요) 카드도 같은 필터를 받는다(2026-07-06) — 미등재어 토큰은 한글 런
        단위로 추출된 것이라 더 긴 낱말 속 조각('책임연'⊂'책임연구원으로')은 등장이 아니다.

        kiwi 미설치 시 strip_josa=None → 필터 미적용(현행 부분문자열 동작 유지·무회귀).
        """
        text = self._full_text
        if not text:
            return
        try:
            from core import morph as _morph
        except Exception:
            return
        if not _morph.available():
            return
        import re as _re
        _hang = _re.compile(r"[^가-힣]")
        for o in self._occ:
            p = o.get("pos")
            if p is None:
                continue
            c = o["c"]
            orig = c.get("original", "") or ""
            corr = c.get("corrected", "") or ""
            # 대상 ①: 순수 '분리' 띄어쓰기 교정(글자 불변 + 원문 공백 없음 + 공백 추가).
            is_split = not (" " in orig or not corr or orig == corr
                            or orig.replace(" ", "") != corr.replace(" ", "")
                            or corr.count(" ") <= orig.count(" "))
            # 대상 ②: dict_flag(검수 필요) 카드. 미등재어 토큰은 사전 스크리닝이 **한글 런**
            #   단위로 추출한 것이라, 더 긴 낱말 속 우연한 부분문자열은 같은 낱말이 아니다
            #   (사용자 보고 2026-07-06: '책임연'(표 안 저자 줄임) 카드가 본문 정상 표기
            #   '책임연구원으로' **속**을 반복 1/2로 하이라이트 — 교정란 편집·수락 시
            #   '책임연구원구원' 오염 경로. [R] 가드가 AI 확장 카드를 드롭하자 안전망 [6]의
            #   covered 억제가 풀려 dict 카드로 재발한 케이스). 조사형('키메세지를')은
            #   아래 base 비교가 같은 낱말로 인정하므로 계속 포함된다.
            is_flag = c.get("source") == "dict_flag" and orig and " " not in orig
            # 대상 ③: 자기확장 카드(2026-07-15 실측 '미생성'→'미생성 코드') — 순수 한글 원문이
            #   교정문(공백 제거)의 진부분문자열인 경우. AI 조사형 확장('미생성이'→'미생성 코드가')
            #   을 일관성 Case A가 bare 카드로 전파하면, 부분문자열 등장 탐색이 더 긴 복합어
            #   ('미생성코드', 524곳 대부분) 속 접두까지 잡아 수락 시 '미생성 코드코드' 중복 오염이
            #   생긴다. 확장 교정은 낱말이 홀로 선 등장(조사형 포함)에만 유효하므로 어절 base 정확
            #   일치만 남긴다(교정형을 이미 담은 어절 — '미생성코드' 속 '미생성' — 은 제외).
            #   부호 섞인 카드(punct 괄호 보충 등)는 한글-only 판정 밖이라 무영향.
            corr_ds = corr.replace(" ", "")
            is_expand = bool(
                orig and corr_ds != orig and orig in corr_ds
                and _re.fullmatch(r"[가-힣]+", orig)
                and _re.fullmatch(r"[가-힣]+", corr_ds)
            )
            # 대상 ④: 순수 '결합' 띄어쓰기 카드('적정 급여'→'적정급여', 워커 [7] 다수결 통일의
            #   붙임 방향) — 글자 불변·순수 한글 다어절 원문·공백 감소(2026-07-15 사용자 보고).
            #   근거 수치(morph.find_compound_spacing_consistency)는 어절 경계 pair(왼쪽
            #   (?<![가-힣]) + 오른쪽 조사 런)만 세는데, 등장 탐색은 부분문자열이라 '부적정
            #   급여'(왼쪽 다른 낱말)·'개선 방안연구'(오른쪽 다른 낱말)까지 잡아 반복 수가
            #   근거보다 부풀고('관리 체계' 1회↔반복 3), 수락 시 제3의 낱말 경계까지 바꾼다.
            #   아래 한글 런 base 비교가 양쪽 경계를 근거 수치와 같은 기준으로 판정한다
            #   (조사형 '적정 급여를'은 base 일치로 유지). 부호 섞인 결합 카드(punct ⑥
            #   '있다 (경향신문'→'있다(경향신문' 등)는 한글-only 판정 밖이라 무영향.
            is_join = bool(
                orig and corr and " " in orig
                and orig.replace(" ", "") == corr_ds
                and corr.count(" ") < orig.count(" ")
                and _re.fullmatch(r"[가-힣]+(?: [가-힣]+)+", orig)
            )
            if not (is_split or is_flag or is_expand or is_join):
                continue
            orig_h = _hang.sub("", orig)
            if not orig_h:
                continue
            # 이 등장을 감싼 어절 추출 후 조사 제거 base 비교.
            #   ⚠ 어절 경계는 공백이 아니라 **한글 런**으로 잡는다(2026-07-03 치명 버그 수정).
            #   공백 경계 토큰은 붙은 문장부호를 포함해('언어스타일로,'·'물어봄으로써.')
            #   strip_josa가 조사를 못 떼 base 불일치 → 멀쩡한 조사형 등장이 excluded로
            #   오판정됐다. excluded는 카드에서 숨겨지고 적용에서 무조건 skip이라, 사용자가
            #   보지도 못한 채 **수락한 교정이 조용히 누락**됐다(고독사 hwpx 실측: '언어스타일'
            #   16곳 중 4곳('언어스타일로,') 등 8곳+ 무단 미치환 — 신뢰성 치명 보고).
            #   등장 양끝에서 한글만 이어붙이면 부호가 끼지 않아 strip_josa가 정상 동작한다.
            left = p
            while left > 0 and "가" <= text[left - 1] <= "힣":
                left -= 1
            right = p + len(orig)
            while right < len(text) and "가" <= text[right] <= "힣":
                right += 1
            token = text[left:right]
            token_h = _hang.sub("", token)
            # ⚠ 등장 어절 = 원문 그대로(조사 포함)면 무조건 포함. 원문 자체가 조사를 담은
            #   카드('…채널을'→'…채널 을 분리' 등)에서 조사를 떼고 비교하면 전부 불일치로
            #   제외돼 카드가 통째로 사라졌다(2026-07-02 미탐 보고 — 회귀 수정).
            if token_h == orig_h:
                continue
            base = _morph.strip_josa(token) or token
            if _hang.sub("", base) != orig_h:       # 어절 base ≠ original → 더 긴 복합어 → 제외
                o["excluded"] = True

    def _resolve_overlaps(self):
        """겹치는 등장 해소 — '키메세지'(bare)가 '키메세지를' 안에서 부분문자열로
        매칭돼 같은 위치에 카드가 둘 뜨던 문제를 제거한다(가장 긴 매치가 승리).

        진 쪽은 shadowed=True로 표시 — 카드/카운트/하이라이트에서 빼고, 적용 시
        해당 등장은 skip해(긴 교정이 그 자리를 담당) 이중 치환을 막는다.
        등장 인덱스는 그대로 두므로(브리지 RepeatFind 부분문자열 순서와 정렬)
        부분 거절 skip 인덱스 정합성이 유지된다.
        """
        real = [o for o in self._occ if o["pos"] is not None]
        # 긴 span 우선, 같으면 앞 위치 우선
        real.sort(key=lambda o: (-(o["end"] - o["pos"]), o["pos"]))
        occupied = []
        for o in real:
            s, e = o["pos"], o["end"]
            # 어절 base 불일치로 제외된 등장(_mark_stem_boundary_skips) — 카드/카운트에서
            #   숨기되(shadowed) 자리를 점유하지 않는다(치환 안 하므로). 스킵은 excluded로 별도 처리.
            if o.get("excluded"):
                o["shadowed"] = True
                continue
            if any(not (e <= os_ or s >= oe) for os_, oe in occupied):
                o["shadowed"] = True
            else:
                o["shadowed"] = False
                occupied.append((s, e))
        # 카드 표시용 반복 번호를 '보이는(=shadowed 아님)' 등장 기준으로 재계산
        from collections import defaultdict
        groups = defaultdict(list)
        for o in self._occ:
            if not o["shadowed"]:
                groups[o["ci"]].append(o)
        for occs in groups.values():
            occs.sort(key=lambda o: (o["pos"] is None, o["pos"] or 0))
            for k, o in enumerate(occs):
                o["rep_index"] = k + 1
                o["rep_total"] = len(occs)

    def _create_card(self, occ: dict, cid: int) -> QFrame:
        data = occ["c"]
        card = QFrame()
        card.setObjectName(f"card_{cid}")
        card.setProperty("occ_id", cid)
        # 세로 Maximum(성장 금지) — 카드가 적어 목록에 여분 공간이 남으면 스크롤
        #   레이아웃이 카드를 세로로 부풀리고, 그 여분이 원본 라벨로 흘러 들어가
        #   빈 공간이 생겼다(실측 137px vs 정답 14px). 필요 높이(hfw)만큼만 차지.
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        card.setCursor(Qt.PointingHandCursor)
        card.mousePressEvent = lambda _e, c=cid: self._scroll_to(c)
        cl = QVBoxLayout(card)
        cl.setSpacing(10)
        cl.setContentsMargins(14, 12, 14, 14)

        pal = current_palette()

        # 상단: 타이틀 + 수락/거절 버튼
        top = QHBoxLayout()
        top.setSpacing(6)
        # 카테고리 배지만 표출(source 라벨 'AI 오탈자/사전검증' 제거) — '왜 교정 제안했는지'.
        #   집계 칩과 동일한 _pill_qss로 라운드풀·색/글자색 통일. 검수 필요는 경고색.
        needs_review = _needs_review(data)
        cat = _display_category(data)
        cat_chip = label(cat)
        cat_chip.setStyleSheet(_pill_qss(pal, "review" if needs_review else "normal"))
        top.addWidget(cat_chip)
        if occ["rep_total"] > 1:
            rep_lbl = label(f"반복 {occ['rep_index']}/{occ['rep_total']}")
            rep_lbl.setStyleSheet(
                f"color: {pal['text_muted']}; font-size: 11px; border: none; background: transparent;")
            top.addWidget(rep_lbl)

        top.addStretch()
        accept_btn = IconButton("check", size=15, role="text_dim")
        reject_btn = IconButton("x", size=15, role="text_dim")
        
        auto_apply = self._options.get("auto_apply", False)
        if auto_apply:
            accept_btn.setEnabled(False)
            reject_btn.setEnabled(False)
            accept_btn.setCursor(Qt.ArrowCursor)
            reject_btn.setCursor(Qt.ArrowCursor)
        else:
            accept_btn.clicked.connect(lambda _e, o=occ, c=card: self._set_status(o, c, "accepted"))
            reject_btn.clicked.connect(lambda _e, o=occ, c=card: self._set_status(o, c, "rejected"))
            
        top.addWidget(accept_btn)
        top.addWidget(reject_btn)
        cl.addLayout(top)

        # 교정 본문 (내부 박스)
        inner_box = QFrame()
        inner_box.setStyleSheet(f"QFrame#inner_{cid} {{ background: {pal['surface']}; border: 1px solid {pal['border']}; border-radius: 6px; }}")
        inner_box.setObjectName(f"inner_{cid}")
        il = QVBoxLayout(inner_box)
        il.setContentsMargins(12, 10, 12, 10)
        il.setSpacing(8)

        orig_row = QHBoxLayout()
        orig_lbl = sub_label("원본")
        orig_lbl.setStyleSheet(f"color: {pal['text_muted']}; border: none; font-weight: normal; width: 30px; background: transparent;")
        # PlainText 고정(escape 불필요·엔티티 원문 그대로) + ZWSP로 어디서든 줄바꿈.
        #   무공백 장문의 '줄바꿈 불가 단위'가 카드 최소 폭을 밀어 올려(실측 339px)
        #   기본 창 크기에서 가로 스크롤을 만들던 원인 제거 — 카드 폭 고정·세로 확장.
        #   사이즈 정책은 기본값 유지 — ZWSP만으로 최소 폭이 글자 1개 수준으로
        #   줄어들고, 커스텀 Ignored 정책은 heightForWidth 계산을 망가뜨려
        #   라벨이 세로로 부풀었다(실측 127px vs 정답 14px).
        orig_val = label(_soft_breakable(data.get("original", "")))
        orig_val.setTextFormat(Qt.PlainText)
        orig_val.setStyleSheet(f"border: none; color: {pal['text_sub']}; background: transparent;")
        orig_val.setWordWrap(True)
        orig_row.addWidget(orig_lbl)
        orig_row.addSpacing(10)
        orig_row.addWidget(orig_val, 1)
        il.addLayout(orig_row)

        divider_line = QFrame()
        divider_line.setFixedHeight(1)
        divider_line.setStyleSheet(f"background: {pal['border']}; border: none;")
        il.addWidget(divider_line)

        corr_row = QHBoxLayout()
        corr_lbl = sub_label("교정")
        corr_lbl.setStyleSheet(f"color: {pal['text_muted']}; border: none; font-weight: normal; width: 30px; background: transparent;")
        
        auto_apply = self._options.get("auto_apply", False)
        is_flag = data.get("source") == "dict_flag"
        # 교정값을 인라인 편집 가능 — 값을 고친 뒤 적용하면 '수정 후 적용'(edit_accept).
        #   검수 필요(dict_flag)는 치환 후보가 없어 원본(original==corrected)을 그대로 채워
        #   두고, 사용자가 올바른 표기로 직접 수정하게 한다. 고치지 않고 적용하면
        #   corrected==original → HWP는 그대로 두고 정오표에만 '검수'로 기록된다
        #   (apply_worker._flag_only / errata is_flag_review).
        corr_val = GrowingTextEdit(data.get("corrected", ""))
        if is_flag:
            corr_val.setPlaceholderText("올바른 표기로 직접 수정")
            corr_val.setToolTip(
                "검수가 필요한 항목입니다. 올바른 표기로 직접 수정한 뒤 적용(✓)하세요.\n"
                "수정하지 않고 적용하면 본문은 그대로 두고 정오표에만 ‘검수’로 기록됩니다.")
        else:
            corr_val.setToolTip("값을 직접 수정한 뒤 적용(✓)하면 ‘수정 후 적용’으로 반영됩니다.")
        # 전역 QSS의 QTextEdit 규칙(font-size 11px·padding 10px 등)을 전부 명시적으로
        #   덮어써 기존 QLineEdit(전역 12px) 시절과 같은 외형을 유지한다.
        corr_val.setStyleSheet(
            f"QTextEdit{{border:1px solid transparent; border-radius:5px; font-weight:bold; "
            f"font-size:12px; color:{pal['text']}; background:transparent; padding:1px 4px;}}"
            f"QTextEdit:hover{{border:1px dashed {pal['border_strong']};}}"
            f"QTextEdit:focus{{border:1px solid {pal['accent']}; background:{pal['surface']};}}")
        if auto_apply:
            corr_val.setReadOnly(True)
        else:
            corr_val.editingFinished.connect(
                lambda d=data, le=corr_val: self._on_corrected_edited(d, le))
        corr_row.addWidget(corr_lbl)
        corr_row.addSpacing(10)
        corr_row.addWidget(corr_val, 1)
        il.addLayout(corr_row)

        cl.addWidget(inner_box)

        reason = data.get("description", data.get("reason", ""))
        if reason:
            reason_lbl = sub_label(reason, wrap=True)
            reason_lbl.setStyleSheet(f"color: {pal['text_muted']}; border: none; background: transparent; font-size: 12px;")
            cl.addWidget(reason_lbl)

        # 띄어쓰기 일관성 '통일' 카드: 수락/거절이 곧 '문서 전체 통일 방향' 선택이다
        #   (별도 '반대 표기로 통일' 버튼은 2026-07-21 제거 — 사용자가 그 버튼을 못 보고
        #   그냥 '거절'만 하면 두 표기가 문서에 혼재한 채 남는 사용성 결함 때문).
        #   실제 처리는 _set_status → _unify_dialog. 버튼 의미를 툴팁으로 명시한다.
        if not auto_apply and self._flip_info(data)[0]:
            _o, _c = data.get("original", ""), data.get("corrected", "")
            accept_btn.setToolTip(f"문서 전체를 '{_c}' 표기로 통일합니다.")
            reject_btn.setToolTip(f"문서 전체를 '{_o}' 표기로 통일합니다.")

        card._occ = occ
        card._accept_btn = accept_btn
        card._reject_btn = reject_btn
        self._style_card(card)
        return card

    def _style_card(self, card: QFrame):
        st = card._occ["status"]
        card.setProperty("status", st)
        acc = st == "accepted"
        rej = st == "rejected"
        pal = current_palette()
        
        def set_btn(btn, is_on, on_bg):
            if is_on:
                btn.setStyleSheet(f"QPushButton {{ background: {on_bg}; border: none; border-radius: 8px; padding: 4px 9px; min-width: 18px; min-height: 16px; }}")
                btn.set_icon_role("accent_fg")
            else:
                btn.setStyleSheet(f"QPushButton {{ background: {pal['surface']}; border: 1px solid {pal['border_strong']}; border-radius: 8px; padding: 4px 9px; min-width: 18px; min-height: 16px; }}")
                btn.set_icon_role("text_dim")

        set_btn(card._accept_btn, acc, pal["success"])
        set_btn(card._reject_btn, rej, pal["error"])
        
        c_data = card._occ["c"]
        needs_review = _needs_review(c_data)
        border_color = pal["border"]
        bg_color = pal["surface"]
        left_accent = None
        is_active = (card.property("occ_id") == getattr(self, "_active_occ_id", None))

        if acc:
            border_color = pal["success"]
            bg_color = pal["success_bg"]
        elif rej:
            border_color = pal["error"]
            bg_color = pal["error_bg"]
        else:
            # 대기 + 검수 필요(미등재/저신뢰): 카드 배경은 surface 유지(요약 칩·카드 내부
            #   배지의 경고 틴트와 색이 겹쳐 '구분 불가'였던 문제 해결) — 경고 테두리 +
            #   좌측 강조선으로만 '확인 필요'를 표시한다. 배지는 흰 배경 위라 잘 보인다.
            if needs_review:
                border_color = pal.get("warning_border", pal["warning"])
                left_accent = pal["warning"]
            # 단순 클릭으로 선택된 카드는 그레이 계열(테두리·배경)로 표시 — 강조색 대신.
            if is_active:
                border_color = pal["border_strong"]
                bg_color = pal["surface_hover"]

        cid = card.property("occ_id")
        left = f"border-left: 3px solid {left_accent}; " if (left_accent and not acc and not rej) else ""
        card.setStyleSheet(
            f"QFrame#card_{cid} {{ background: {bg_color}; border: 1px solid {border_color}; "
            f"{left}border-radius: 8px; }}")

    # ── 상태 토글 ─────────────────────────────────
    @staticmethod
    def _short_orig(c: dict, limit: int = 14) -> str:
        orig = c.get("original", "") or ""
        return orig if len(orig) <= limit else orig[:limit] + "…"

    def _set_status(self, occ: dict, card: QFrame, status: str):
        """카드 수락/거절 — 반복 일괄 처리 포함.

        · 사용자가 아직 직접 만지지 않은 같은 단어(ci)의 다른 반복 카드에는 같은
          선택을 자동 전파(auto=True)한다 — 반복 78곳을 78번 클릭하지 않도록.
        · 자동 전파된 카드를 반대로 선택하면 확인 팝업을 띄우고, 확인 시 그
          카드만 반대로 허용(사용자 의도 존중). 전파는 하지 않는다.
        · 직접 선택(by_user)한 카드는 이후 다른 카드발 전파로 덮어쓰지 않는다.
        · ⚠ 예외: 띄어쓰기 일관성 통일 카드(_flip_info)는 '개별 항목 판정'이 아니라
          '문서 전체를 어느 표기로 통일할지'의 선택이라 위 반복 전파 규칙을 타지 않고
          _unify_dialog(그룹 전체 + 반대 방향 동시 처리)로 라우팅한다. 이 라우팅은
          auto 분기보다 우선한다 — 그렇지 않으면 통일로 생성된(auto=True) 반대 카드를
          되돌릴 때 '이 카드만' 팝업이 가로채 통일 복귀가 불가능해진다.
        """
        self._note_interaction()   # 연타 중엔 프리페치를 쉬게 해 클릭 반응을 지킨다
        if occ["status"] == status:
            # 같은 선택 재클릭 — 자동 전파 상태였다면 '직접 확인'으로 승격만.
            occ["auto"] = False
            occ["by_user"] = True
            return
        if self._flip_info(occ["c"])[0]:
            self._unify_dialog(occ, status)
            return   # ⚠ 여기서 반드시 종료 — 아래 코드는 삭제된 카드 위젯을 만진다.
        ci = occ["ci"]
        confirmed = False
        if occ.get("auto"):
            cur_lbl = "수락" if occ["status"] == "accepted" else "거절"
            new_lbl = "수락" if status == "accepted" else "거절"
            ok = self._confirm_popup(
                "반복 항목 반대 처리",
                f"‘{self._short_orig(occ['c'])}’의 반복 항목이 ‘{cur_lbl}’로 일괄 처리되어 "
                f"있습니다.\n이 카드만 ‘{new_lbl}’(반대)로 처리할까요?",
                yes_text=f"이 카드만 {new_lbl}")
            if not ok:
                return
            occ["status"] = status
            occ["auto"] = False
            occ["by_user"] = True
            confirmed = True
        else:
            occ["status"] = status
            occ["auto"] = False
            occ["by_user"] = True
            # 같은 단어의 나머지 반복 카드(사용자 미개입)에 동일 선택 전파.
            others = [o for o in self._occ
                      if o["ci"] == ci and o is not occ
                      and not o.get("shadowed") and not o.get("by_user")]
            for o in others:
                o["status"] = status
                o["auto"] = True
            if others:
                lbl = "수락" if status == "accepted" else "거절"
                self._toast(
                    f"‘{self._short_orig(occ['c'])}’ 반복 {len(others) + 1}항목을 ‘{lbl}’ 선택으로 "
                    f"일괄 처리했습니다.")
        self._derive(ci)
        # 전파로 상태가 바뀐 그룹의 로드된 카드 전부 리스타일(미로드 카드는 생성 시 반영).
        for cd in self._cards:
            if cd._occ["ci"] == ci:
                self._style_card(cd)
        self._scroll_to(card.property("occ_id"))
        self._emit_counts()
        if not confirmed:
            self._check_conflict(ci)

    def _on_corrected_edited(self, data: dict, le):
        """교정값 인라인 수정 — 값이 바뀌면 correction에 반영하고 '수정 후 적용' 표시.

        같은 단어의 반복 카드는 동일 correction(dict)을 공유하므로 값은 즉시 일관 적용된다
        (다른 카드의 입력칸 텍스트는 다음 재구성 때 갱신). 적용 시 corrected는 수정값을 쓴다.
        """
        new = le.text().strip()
        old = data.get("corrected", "")
        if not new or new == old:
            return
        data["corrected"] = new
        data["_edited"] = True
        self._refresh_preview()
        self._emit_counts()   # '직접수정' 집계 칩 즉시 갱신

    def _check_conflict(self, ci: int):
        sts = [o["status"] for o in self._occ if o["ci"] == ci]
        if "accepted" in sts and "rejected" in sts:
            orig = self._corrections[ci].get("original", "")
            n_acc = sts.count("accepted")
            n_rej = sts.count("rejected")
            self._warn_popup(
                "반복 항목 처리가 엇갈립니다",
                f"‘{orig}’의 반복 {len(sts)}곳 처리가 엇갈립니다 "
                f"(수락 {n_acc} · 거절 {n_rej}).\n의도한 것인지 확인하세요.")

    def _warn_popup(self, title: str, message: str):
        """경고 팝업(모달). 반복 항목 상충 등 사용자 확인이 필요한 안내에 사용."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _confirm_popup(self, title: str, message: str, yes_text: str = "확인") -> bool:
        """확인/취소 팝업 — 자동 일괄 처리된 반복 카드를 반대로 뒤집을 때 의도 확인.
        기본 버튼은 '취소'(실수 방지) — 반대 처리는 의도적 클릭이어야 한다."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(message)
        yes = box.addButton(yes_text, QMessageBox.AcceptRole)
        cancel = box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is yes

    def _derive(self, ci: int):
        """occurrence 상태 → 고유 교정의 적용 상태 + 부분거절 skip 인덱스 산출.
        occ는 self._occ의 문서 등장 순(=브리지 RepeatFind 순)과 일치한다."""
        occs = [o for o in self._occ if o["ci"] == ci]
        c = self._corrections[ci]
        
        if not occs:
            if "skip_occurrences" not in c:
                c["skip_occurrences"] = []
            return
            
        # 카드 상태 집계는 '보이는(shadowed 아닌)' 등장만 본다.
        visible_sts = [o["status"] for o in occs if not o.get("shadowed")]
        
        if "accepted" in visible_sts:
            c["status"] = "accepted"
        elif "rejected" in visible_sts:
            c["status"] = "rejected"
        else:
            c["status"] = "pending"
        # 적용에서 제외(skip)할 등장 인덱스(0-based, 문서 등장 순 = 브리지 RepeatFind 순).
        #   · 보이는(shadowed 아님) 등장: 사용자가 수락하지 않은 것만 skip.
        #   · shadowed(겹침) 등장은 skip하지 '않는다'. 겹친 긴 교정이 부분문자열을 그대로
        #     두는 경우(띄어쓰기·외래어 등, 예: '뱃지만들기'→'뱃지 만들기')엔 짧은 교정이
        #     그 자리도 정규화해야 문서가 일관된다(→'배지 만들기'). 긴 교정이 부분문자열을
        #     없애는 경우(예: '뱃지를'→'배지를')엔 RepeatFind가 그 자리를 아예 못 찾으므로
        #     skip하지 않아도 무해하다. ⚠ shadowed를 skip 인덱스에 넣으면 적용 시점에 등장
        #     집합이 달라져(긴 교정 선적용으로 텍스트 변형) 인덱스가 밀려 '수락 등장 미적용·
        #     거절 등장 오적용' 버그가 났다 → 넣지 않는다. (조사 잉여 변형은 분석 단계
        #     drop_redundant_josa_variants가 별도 제거 — consistency_pass 참조.)
        #   · excluded(어절 base 불일치 복합어, 예 '성장단계별') 등장: 항상 skip. 원문이
        #     부분문자열로 잡히지만 근거 수치(다수결 base)에 포함 안 된 자리라 치환 금지.
        c["skip_occurrences"] = [k for k, o in enumerate(occs)
                                 if o.get("excluded")
                                 or (not o.get("shadowed") and o["status"] != "accepted")]
        # 본문 실등장 수 — 적용 후 '수락 등장 수 vs 실제 치환 수' 대조(부분 반영 감지)용.
        #   apply_worker가 (occurrences − skip) > replaced 면 실패 항목으로 표출한다
        #   (수락한 교정이 조용히 일부만 반영되는 치명 오류 방지 — 사용자 보고 2026-07-03).
        #   ⚠ 겹침 shadowed(excluded 아님)는 세지 않는다(2026-07-06 거짓 '부분 반영' 보고):
        #   '메세지를'의 등장 5곳 중 4곳이 '키메세지를' 카드에 가려진 경우, 적용 시 더 긴
        #   카드가 먼저 치환해 그 자리를 소진하므로 이 카드의 replaced는 1이 정상이다.
        #   shadowed를 세면 기대 5 vs 실제 1로 멀쩡한 교정이 실패 항목에 오른다.
        #   (excluded는 skip_occurrences에 들어 있어 apply_worker가 빼 주므로 계속 센다.)
        c["occurrences"] = sum(1 for o in occs if o["pos"] is not None
                               and not (o.get("shadowed") and not o.get("excluded")))

    def _derive_all(self):
        for ci in range(len(self._corrections)):
            self._derive(ci)

    # ══════════════════════════════════════════════
    # 용어 일관성 통일 (일관성 카드 = 문서 전체 표기 선택)
    # ══════════════════════════════════════════════
    def _count_substring(self, s: str) -> int:
        """본문에서 s의 비중첩 등장 수 — 등장 탐색(_build_occurrences)·브리지
        RepeatFind와 같은 부분문자열 규칙이라 '실제로 바뀌는 곳' 수와 일치한다."""
        text = self._full_text
        if not s or not text:
            return 0
        n, start = 0, 0
        while True:
            i = text.find(s, start)
            if i < 0:
                return n
            n += 1
            start = i + len(s)

    def _flip_info(self, c: dict):
        """(방향 뒤집기 가능 여부, 반대 원문(=corrected)의 본문 등장 수).

        일관성 '통일' 카드(consistency_flip 마커)만 대상 — 통일 방향이 옳고 그름이
        아닌 편집 선택인 카드에 한정한다. ⚠ 규범 교정(norm_map 등)에 열면 비표준
        표기로의 통일을 조장하므로 마커 없는 카드는 항상 불가. 안전을 위해 순수
        띄어쓰기 차이(글자 불변)와 반대 표기의 실제 존재를 재확인한다.
        """
        if not c.get("consistency_flip") or not self._full_text:
            return False, 0
        orig = c.get("original", "") or ""
        corr = c.get("corrected", "") or ""
        if not orig or not corr or orig == corr:
            return False, 0
        if orig.replace(" ", "") != corr.replace(" ", ""):
            return False, 0
        n = self._count_substring(corr)
        return (n > 0), n

    def _unify_dialog(self, occ: dict, status: str):
        """일관성 카드 수락/거절 = 문서 전체 통일 방향 선택 — 확인 팝업 후 반영.

        수락 → 교정 표기(corrected)로 통일, 거절 → 원문 표기(original)로 통일
        (반대 방향 교정을 즉시 합성해 수락 상태로 추가). 두 방향 모두 그룹 전체가
        함께 움직이며, 반대 카드에서 다시 선택하면 원래 방향으로 복귀한다(대칭).
        """
        c = occ["c"]
        orig, corr = c.get("original", ""), c.get("corrected", "")
        target = corr if status == "accepted" else orig
        if not LightConfirmDialog.ask(
                self, "용어 일관성 통일",
                f"문서 전체를 '{target}' 표기로 통일합니다."):
            return
        # 팝업이 완전히 닫힌 '다음 틱'에 실행 — 아래 작업(재판정·카드 재생성·미리보기
        #   재렌더)이 큰 문서에서 UI를 수 초 점유하는데, 팝업 창이 파괴되기 전에
        #   프리즈가 시작되면 빈 유령 창(응답 없는 흰 창)으로 화면에 남는다(사용자 보고).
        #   singleShot(0)으로 대화상자 테어다운을 먼저 완료시킨다.
        QTimer.singleShot(0, lambda: self._do_unify(occ, orig, corr, status))

    def _do_unify(self, occ: dict, orig: str, corr: str, status: str):
        """_unify_dialog 확인 후 실제 반영(무거운 부분) — 대기 커서로 감싼다.

        ⚠ 이 경로는 _rebuild_cards로 카드 위젯을 전부 파괴한다. 호출한 _set_status는
          이 함수 호출 뒤 카드 객체를 만지지 않고 즉시 반환해야 한다(삭제된 C++ 객체).
        """
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QCursor
        c, ci = occ["c"], occ["ci"]
        anchor = self._capture_card_anchor(occ)
        # 카드 목록이 '전부 사라졌다 다시 채워지는' 깜빡임 차단(사용자 보고 2026-07-21).
        #   재구성은 전량 파괴 → 첫 10장 → (다음 틱)앵커까지 로드 → (정착까지)스크롤 보정
        #   순서라, 중간 상태가 그대로 그려지면 빈 패널이 한 번 번쩍인다. 갱신을 끊어
        #   두면 옛 화면이 남아 있다가 완료 시점에 새 화면으로 한 번에 바뀐다.
        self._card_scroll.setUpdatesEnabled(False)
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            if status == "accepted":
                self._apply_unify_forward(ci, orig, corr, active_occ=occ)
            else:
                self._apply_flip(c, ci, orig, corr, active_occ=occ)
            # 앵커 카드까지는 **여기서 동기로** 만든다 — 다음 틱으로 미루면 그 사이
            #   '카드 10장뿐인' 화면이 한 프레임 새어 나간다.
            occ_ref = anchor[0]
            while (not any(cd._occ is occ_ref for cd in self._cards)
                   and self._loaded_card_count < len(self._occ)):
                self._load_more_cards(50)
        finally:
            QApplication.restoreOverrideCursor()

        def _unfreeze():
            if not self._card_scroll.updatesEnabled():
                self._card_scroll.setUpdatesEnabled(True)
        # 일반 카드가 _set_status 끝에서 _scroll_to로 하는 것과 동일하게, 미리보기를
        #   방금 처리한 등장 위치로 옮긴다(일관성 카드는 조기 반환이라 그 경로를 안 탄다
        #   — 사용자 보고 2026-07-21: 수락/거절해도 원문·교정문이 따라오지 않음).
        #   ⚠ _refresh_preview가 예약한 '스크롤 원위치 복원' 뒤에 실행돼야 하므로 지연.
        QTimer.singleShot(0, lambda: self._scroll_previews_to(self._active_occ_id))
        # 스크롤 위치가 정착한 뒤 갱신 재개. 혹시 정착 콜백이 오지 않아도 화면이
        #   영영 얼지 않도록 시간 기반 백스톱을 함께 건다.
        self._restore_card_anchor(anchor, on_done=_unfreeze)
        QTimer.singleShot(1200, _unfreeze)

    # ── 카드 목록 스크롤 고정 ──────────────────────
    def _capture_card_anchor(self, occ: dict):
        """방금 조작한 카드가 화면에서 놓인 위치를 기억 — 재구성 후 그대로 되돌리기 위해.

        _rebuild_cards는 카드를 전부 파괴하고 앞 50장만 다시 만들기 때문에, 아무것도
        안 하면 스크롤바 값이 달라진 콘텐츠 높이에 클램프돼 엉뚱한 곳으로 튄다
        (사용자 보고 2026-07-21 — 반대 카드 생성 시 화면이 날아감).
        """
        sb = self._card_scroll.verticalScrollBar()
        card = next((cd for cd in self._cards if cd._occ is occ), None)
        # (기준 등장, 뷰포트 안에서의 y 오프셋, 폴백용 원래 스크롤 값)
        return (occ, (card.pos().y() - sb.value()) if card is not None else 0, sb.value())

    def _restore_card_anchor(self, anchor, on_done=None):
        if not anchor:
            if on_done:
                on_done()
            return
        occ, offset, fallback = anchor

        def stage_load():
            # 기준 카드가 아직 로드 범위 밖이면 나올 때까지 이어서 로드.
            while (not any(cd._occ is occ for cd in self._cards)
                   and self._loaded_card_count < len(self._occ)):
                self._load_more_cards(50)
            if any(cd._occ is occ for cd in self._cards):
                self._place_card(occ, offset, on_done=on_done)
            else:
                # 필터에 걸려 카드가 없는 경우(예: '미적용' 필터) — 값만 되돌린다.
                sb = self._card_scroll.verticalScrollBar()
                sb.setValue(min(fallback, sb.maximum()))
                if on_done:
                    on_done()

        QTimer.singleShot(0, stage_load)

    def _place_card(self, occ: dict, offset: int, smooth: bool = False,
                    attempt: int = 0, prev_y: int | None = None, on_done=None):
        """카드가 뷰포트 안 `offset` 위치에 오도록 스크롤 — **위치가 안정될 때까지** 반복.

        ⚠ 카드를 새로 만든 직후엔 pos()가 한 번에 확정되지 않는다: 각 카드의 높이가
        heightForWidth로 여러 레이아웃 패스에 걸쳐 풀리며 위치가 계단식으로 밀린다
        (실측 y: 0 → 877 → 2953). 한 틱만 미뤄 읽으면 중간값을 잡아 화면이 엉뚱한 데
        멈춘다 — 직전 측정과 같아질 때까지(=레이아웃 정착) 다시 잰다.
        """
        def step():
            sb = self._card_scroll.verticalScrollBar()
            card = next((cd for cd in self._cards if cd._occ is occ), None)
            if card is None:
                if on_done:
                    on_done()
                return
            y = card.pos().y()
            target = max(0, min(sb.maximum(), y - offset))
            if smooth and hasattr(self, "_card_smooth"):
                self._card_smooth.anim.stop()
                self._card_smooth._target_value = target
                self._card_smooth.anim.setStartValue(sb.value())
                self._card_smooth.anim.setEndValue(int(target))
                self._card_smooth.anim.start()
            else:
                if hasattr(self, "_card_smooth"):
                    self._card_smooth.anim.stop()   # 진행 중 부드러운 스크롤 무효화
                    self._card_smooth._target_value = target
                sb.setValue(target)
            if attempt < 6 and y != prev_y:
                self._place_card(occ, offset, smooth, attempt + 1, y, on_done)
            elif on_done:
                on_done()      # 위치 정착 완료 — 호출자가 화면 갱신을 재개할 시점

        QTimer.singleShot(0, step)

    def _find_reverse(self, orig: str, corr: str):
        """반대 방향 교정(corr→orig)이 이미 목록에 있으면 반환."""
        return next((r for r in self._corrections
                     if r.get("original") == corr and r.get("corrected") == orig), None)

    def _mark_active_occ(self, active_occ):
        """미리보기 하이라이트의 '활성' 등장을 갱신 — 카드 재생성·미리보기 재렌더보다
        먼저 호출해야 새 카드/HTML이 활성 상태로 그려진다(재렌더 2회를 피한다)."""
        if active_occ is None:
            return
        try:
            self._active_occ_id = self._occ.index(active_occ)
        except ValueError:
            pass

    def _apply_unify_forward(self, fwd_ci: int, orig: str, corr: str, active_occ=None):
        """'교정 표기로 통일'(수락 방향) — 이 그룹 전체 수락 + 반대 교정 거절.

        반대 방향 카드를 새로 만들 필요는 없다: 원문 등장을 전부 corr로 바꾸면
        문서에 원래 있던 corr 등장은 그대로라 그 자체로 통일이 완성된다. 다만 이전에
        '원문 표기로 통일'을 했다면 반대 교정(corr→orig)이 수락 상태로 남아 있어
        서로 상쇄되므로 반드시 거절로 되돌린다.
        """
        for o in self._occ:
            if o["ci"] == fwd_ci:
                o["status"] = "accepted"
                o["auto"] = False
                o["by_user"] = True
        rev = self._find_reverse(orig, corr)
        if rev is not None:
            rev["status"] = "rejected"
            for o in self._occ:
                if o["c"] is rev:
                    o["status"] = "rejected"
                    o["auto"] = False
                    o["by_user"] = True
        self._mark_active_occ(active_occ)
        self._derive_all()
        self._rebuild_cards()
        self._refresh_preview()
        self._emit_counts()

    def _apply_flip(self, c: dict, fwd_ci: int, orig: str, corr: str, active_occ=None):
        """'원문 표기로 통일'(거절 방향) — 이 그룹 전체 거절 + 반대 교정(corr→orig) 수락."""
        # 1) 이 방향(A→B)의 등장 전부 거절.
        for o in self._occ:
            if o["ci"] == fwd_ci:
                o["status"] = "rejected"
                o["auto"] = False
                o["by_user"] = True

        # 2) 반대 교정(B→A) 찾기(재뒤집기·복귀) 또는 생성 + 등장 합류.
        rev = self._find_reverse(orig, corr)
        if rev is None:
            rev = {
                "original": corr, "corrected": orig,
                "reason": (f"[검수] 띄어쓰기 일관성(사용자 선택) — 문서 전체를 "
                           f"'{orig}' 표기로 통일"),
                "source": c.get("source", "spacing"),
                "color": c.get("color", 0),
                "category": c.get("category") or "띄어쓰기",
                "confidence": "low",
                "status": "accepted",
                "consistency_flip": True,   # 반대 카드에서 재뒤집기(원방향 복귀) 허용
            }
            self._corrections.append(rev)
            rev_ci = len(self._corrections) - 1
            positions, start = [], 0
            while True:
                i = self._full_text.find(corr, start)
                if i < 0:
                    break
                positions.append(i)
                start = i + len(corr)
            for k, p in enumerate(positions):
                self._occ.append({
                    "ci": rev_ci, "c": rev, "pos": p, "end": p + len(corr),
                    "rep_index": k + 1, "rep_total": len(positions),
                    "status": "accepted", "shadowed": False,
                    "auto": False, "by_user": True,
                })
        else:
            rev["status"] = "accepted"
            for o in self._occ:
                if o["c"] is rev:
                    o["status"] = "accepted"
                    o["auto"] = False
                    o["by_user"] = True

        # 3) 전역 재정렬·어절경계/겹침 재판정(결정적·멱등) 후 파생값·카드·미리보기 갱신.
        #    ⚠ _build_occurrences 전체 재구성은 다른 카드의 부분 수락/거절(occ 단위)
        #    상태를 지우므로 쓰지 않는다 — occ 상태를 보존한 채 판정만 다시 돈다.
        self._occ.sort(key=lambda o: (o["pos"] is None, o["pos"] or 0))
        self._mark_stem_boundary_skips()
        self._resolve_overlaps()
        # ⚠ 활성 등장 인덱스는 재정렬·역방향 등장 합류로 바뀐다 — 여기서 다시 잡는다.
        self._mark_active_occ(active_occ)
        self._derive_all()
        self._rebuild_cards()
        self._refresh_preview()
        self._emit_counts()

    # ══════════════════════════════════════════════
    # 미리보기
    # ══════════════════════════════════════════════
    def _refresh_preview(self, keep_scroll: bool = True):
        """원문·교정문 미리보기 재렌더.

        ⚠ setHtml은 문서를 통째로 갈아끼우므로 QTextBrowser 스크롤이 **0으로 리셋**된다
        — 카드 하나 수락했을 뿐인데 읽던 위치가 맨 위로 튀어 오른다(사용자 보고
        2026-07-21). 새 문서를 여는 load()만 맨 위에서 시작하고, 그 외에는 위치를 지킨다.
        복원은 즉시 한 번 + 다음 틱 한 번(문서 레이아웃이 끝나야 스크롤 최대값이 확정).
        """
        src_sb = self._source_view.verticalScrollBar()
        prv_sb = self._preview.verticalScrollBar()
        keep = (src_sb.value(), prv_sb.value()) if keep_scroll else None
        sync = getattr(self, "_scroll_sync", None)
        if sync:
            sync.suspend()      # 복원 중 두 뷰가 서로를 밀지 않게
        if not self._full_text:
            self._source_view.setHtml(self._render_fallback(original=True))
            self._preview.setHtml(self._render_fallback(original=False))
        else:
            self._source_view.setHtml(self._render_with_text(original=True))
            self._preview.setHtml(self._render_with_text(original=False))
        if keep is None:
            if sync:
                sync.resume()
            return

        def restore():
            src_sb.setValue(min(keep[0], src_sb.maximum()))
            prv_sb.setValue(min(keep[1], prv_sb.maximum()))
            if sync:
                sync.resume()
        restore()
        QTimer.singleShot(0, restore)

    def _hl_colors(self):
        pal = current_palette()
        return {
            "pending":        ("transparent", pal["accent"], "600"),
            "pending_active": (pal["accent"], pal["accent_fg"], "700"),
            "accepted":       (pal["success_bg"], pal["success"], "600"),
            "accepted_active":(pal["success"], pal["accent_fg"], "700"),
            "rejected":       (pal["error_bg"], pal["error"], "600"),
            "rejected_active":(pal["error"], pal["accent_fg"], "700"),
        }

    def _render_with_text(self, original: bool) -> str:
        text = self._full_text
        items = [(i, o) for i, o in enumerate(self._occ)
                 if o["pos"] is not None and not o.get("shadowed")]
        items.sort(key=lambda io: (-(io[1]["end"] - io[1]["pos"]), io[1]["pos"]))
        chosen, occupied = [], []
        for i, o in items:
            s, e = o["pos"], o["end"]
            if any(not (e <= os_ or s >= oe) for os_, oe in occupied):
                continue
            chosen.append((i, o))
            occupied.append((s, e))
        chosen.sort(key=lambda io: io[1]["pos"])

        hl = self._hl_colors()
        parts, cursor = [], 0
        for i, o in chosen:
            s, e = o["pos"], o["end"]
            if cursor < s:
                parts.append(self._escape_text(text[cursor:s]))
            is_active = (i == getattr(self, "_active_occ_id", None))
            st = o["status"]
            
            if st == "accepted":
                bg, fg, weight = hl["accepted_active"] if is_active else hl["accepted"]
                shown_text = html.escape(o["c"].get("corrected", "")) if not original else self._escape_text(text[s:e])
            elif st == "rejected":
                bg, fg, weight = hl["rejected_active"] if is_active else hl["rejected"]
                shown_text = self._escape_text(text[s:e])
            else:
                bg, fg, weight = hl["pending_active"] if is_active else hl["pending"]
                shown_text = self._escape_text(text[s:e])

            parts.append(
                f'<a name="c{i}" href="cid:{i}" style="text-decoration:none;">'
                f'<span style="background:{bg}; padding:1px 4px; '
                f'border-radius:4px; color:{fg}; font-weight:{weight};">'
                f'{shown_text}</span></a>')
            cursor = e
        if cursor < len(text):
            parts.append(self._escape_text(text[cursor:]))
        pal = current_palette()
        return (f'<div style="line-height:2.0; font-size:14px; color:{pal["text"]};">'
                f'{"".join(parts)}</div>')

    def _render_fallback(self, original: bool) -> str:
        pal = current_palette()
        visible = [(i, o) for i, o in enumerate(self._occ)
                   if not o.get("shadowed")]
        if not visible:
            return (f'<p style="color:{pal["text_muted"]}; font-size:14px;">'
                    '표시할 교정 항목이 없습니다.</p>')
        hl = self._hl_colors()
        parts = []
        for n, (i, o) in enumerate(visible):
            if n > 0:
                parts.append(' … ')
            
            is_active = (i == getattr(self, "_active_occ_id", None))
            st = o["status"]
            
            if st == "accepted":
                bg, fg, weight = hl["accepted_active"] if is_active else hl["accepted"]
                shown = o["c"].get("corrected") if not original else o["c"].get("original")
            elif st == "rejected":
                bg, fg, weight = hl["rejected_active"] if is_active else hl["rejected"]
                shown = o["c"].get("original")
            else:
                bg, fg, weight = hl["pending_active"] if is_active else hl["pending"]
                shown = o["c"].get("original")
                
            parts.append(
                f'<a name="c{i}" href="cid:{i}" style="text-decoration:none;">'
                f'<span style="background:{bg}; padding:1px 4px; '
                f'border-radius:4px; color:{fg}; font-weight:{weight};">'
                f'{html.escape(shown or "")}</span></a>')
        return (f'<div style="line-height:2.0; font-size:14px; color:{pal["text"]};">'
                f'{"".join(parts)}</div>')

    @staticmethod
    def _escape_text(s: str) -> str:
        return html.escape(s).replace("\n", "<br>")

    def _on_text_clicked(self, url):
        if url.scheme() == "cid":
            try:
                cid = int(url.path())
                self._scroll_to(cid)
            except ValueError:
                pass

    def _scroll_previews_to(self, cid):
        """원문·교정문 미리보기만 해당 등장 위치로 이동(카드 목록 스크롤은 건드리지 않음).

        ⚠ _refresh_preview(keep_scroll=True)가 예약해 둔 '원위치 복원'보다 **뒤에**
        실행돼야 한다(둘 다 singleShot(0)이면 FIFO라 자연히 뒤). 앞서면 복원이
        앵커 이동을 되돌린다.
        """
        if cid is None:
            return
        sync = getattr(self, "_scroll_sync", None)
        if sync:
            sync.suspend()
        self._source_view.scrollToAnchor(f"c{cid}")
        self._preview.scrollToAnchor(f"c{cid}")
        if sync:
            sync.resume()

    def _scroll_to(self, cid: int):
        self._note_interaction()
        while getattr(self, '_loaded_card_count', 0) <= cid and getattr(self, '_loaded_card_count', 0) < len(getattr(self, '_occ', [])):
            self._load_more_cards(batch_size=50)
            
        prev_active = getattr(self, "_active_occ_id", None)
        self._active_occ_id = cid
        self._refresh_preview()
        
        # O(1) 업데이트: 활성 상태가 변경된 카드만 스타일 갱신
        for card in self._cards:
            c_id = card.property("occ_id")
            if c_id == cid or c_id == prev_active:
                self._style_card(card)
            
        from PySide6.QtCore import QTimer
        def do_scroll():
            self._scroll_previews_to(cid)

            target_card = next((c for c in self._cards if c.property("occ_id") == cid), None)
            if target_card and hasattr(self, "_card_scroll"):
                # ⚠ 방금 만들어진 카드는 pos()가 여러 레이아웃 패스에 걸쳐 확정된다 —
                #   한 번만 읽고 애니메이션을 걸면 중간값으로 튄다(첫 배치를 10장으로
                #   줄이면서 드러난 실버그). _place_card가 정착까지 재조준한다.
                self._place_card(target_card._occ, 0, smooth=True)

        QTimer.singleShot(0, do_scroll)

    # ══════════════════════════════════════════════
    # 카운트 / 일괄
    # ══════════════════════════════════════════════
    def get_counts(self):
        """(대기, 수락, 전체) — 보이는 카드(shadowed 제외) 단위."""
        vis = [o for o in self._occ if not o.get("shadowed")]
        pending = sum(1 for o in vis if o["status"] == "pending")
        accepted = sum(1 for o in vis if o["status"] == "accepted")
        return pending, accepted, len(vis)

    def _emit_counts(self):
        self.counts_changed.emit(*self.get_counts())
        self._update_cat_summary()

    # 카테고리 우선 표시 순서(그 외는 뒤에 임의 순)
    _CAT_ORDER = ("맞춤법", "띄어쓰기", "표준어", "외래어", "규범표기",
                  "어순", "군더더기", "비문", "어휘", "번역투", "검수 필요")

    @staticmethod
    def _fill_chip(c):
        """칩을 셀 폭에 맞춰 균등(1:1:1)하게 늘리고 글자는 가운데 정렬."""
        c.setAlignment(Qt.AlignCenter)
        c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return c

    def _make_count_chip(self, text: str, kind: str = "normal", on_click=None):
        """집계용 알약(pill) 칩(라운드풀·색/글자색 통일). on_click 주면 클릭 필터."""
        c = self._fill_chip(label(text))
        c.setStyleSheet(_pill_qss(current_palette(), kind))
        if on_click is not None:
            c.setCursor(Qt.PointingHandCursor)
            c.mousePressEvent = lambda _e, f=on_click: f()
        return c

    def _make_outline_chip(self, text: str, color: str, on_click=None, border_color=None):
        """배경 없는(테두리만) 요약 칩. color=글자색, border_color=테두리색(없으면 color)."""
        c = self._fill_chip(label(text))
        c.setStyleSheet(_outline_pill_qss(color, border_color))
        if on_click is not None:
            c.setCursor(Qt.PointingHandCursor)
            c.mousePressEvent = lambda _e, f=on_click: f()
        return c

    def _make_ghost_chip(self, text: str, fg: str, on_click=None):
        """배경 고스트(surface_alt) + 글자색만 지정하는 칩(적용/거절/직접수정)."""
        c = self._fill_chip(label(text))
        c.setStyleSheet(_ghost_pill_qss(current_palette(), fg))
        if on_click is not None:
            c.setCursor(Qt.PointingHandCursor)
            c.mousePressEvent = lambda _e, f=on_click: f()
        return c

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()            # 유령 창 방지 — _rebuild_cards 주석 참조
                w.setParent(None)
                w.deleteLater()

    def _update_cat_summary(self):
        """집계 칩 갱신 — 2행 배치.

        1행: 전체 · 미적용 · 검수 필요(필터 선택). 전체↔미적용은 선택된 쪽이
             primary(채움), 나머지는 테두리로 모양을 맞바꾼다(기본 선택=전체).
             검수 필요는 선택 시 진한 경고색+흰글씨, 평소엔 연한 경고 칩.
        2행: 적용(녹)·거절(적)·직접수정(청) — 배경 고스트, 글자만 색.
        모든 칩은 라벨 레귤러·숫자만 볼드.
        """
        if not hasattr(self, "_cat_flow"):
            return
        pal = current_palette()
        self._clear_layout(self._title_chips_lay)
        self._clear_layout(self._status_row_lay)
        self._clear_layout(self._cat_flow)

        from collections import Counter
        cnt, total, accepted, rejected, edited = Counter(), 0, 0, 0, 0
        for o in self._occ:
            if o.get("shadowed"):
                continue
            total += 1
            st = o["status"]
            accepted += st == "accepted"
            rejected += st == "rejected"
            edited += bool(o["c"].get("_edited"))
            cnt[_display_category(o["c"])] += 1
        pending = total - accepted - rejected

        # ── 1행: 전체 · 미적용 · 검수 필요 (필터 선택) ──
        #   선택된 쪽이 primary(채움), 나머지는 테두리 — 클릭하면 두 칩의 모양이 맞바뀐다.
        def _filter_chip(text, active, target):
            if active:
                return self._make_count_chip(
                    text, "primary", on_click=lambda: self._set_filter(target))
            # 미선택 칩: 글자는 기본색 유지, 테두리만 primary(accent)로.
            return self._make_outline_chip(
                text, pal["text"], on_click=lambda: self._set_filter(target),
                border_color=pal["accent"])

        self._title_chips_lay.addWidget(_filter_chip(
            _count_html("전체 ", total), self._active_filter is None, None), 1)
        self._title_chips_lay.addWidget(_filter_chip(
            _count_html("미적용 ", pending), self._active_filter == PENDING_FILTER,
            PENDING_FILTER), 1)
        # 검수 필요 — 선택 시 진한 경고색+흰글씨, 평소엔 연한 경고 칩.
        review_n = cnt.get(REVIEW_CAT, 0)
        if review_n:
            kind = "review_active" if self._active_filter == REVIEW_CAT else "review"
            self._title_chips_lay.addWidget(self._make_count_chip(
                _count_html("검수 필요 ", review_n), kind,
                on_click=lambda: self._set_filter(REVIEW_CAT)), 1)

        # ── 2행: 적용(녹)·거절(적)·직접수정(청) — 배경 고스트, 글자만 색 ──
        self._status_row_lay.addWidget(self._make_ghost_chip(_count_html("적용 ", accepted), pal["success"]), 1)
        self._status_row_lay.addWidget(self._make_ghost_chip(_count_html("거절 ", rejected), pal["error"]), 1)
        self._status_row_lay.addWidget(self._make_ghost_chip(
            _count_html("직접수정 ", edited), pal.get("info", pal["accent"])), 1)

        # 카테고리행은 이제 비어 있음(검수 필요는 1행으로 이동) — 빈 줄 여백 제거.
        self._cat_row_w.setVisible(self._cat_flow.count() > 0)

    def _set_filter(self, cat):
        """필터 토글 — 같은 칩 재클릭 시 해제. 카드만 필터(미리보기는 전체).
        cat: None(전체) · PENDING_FILTER(미선택만) · 카테고리명."""
        self._active_filter = None if self._active_filter == cat else cat
        self._rebuild_cards()
        self._emit_counts()

    def _occ_matches_filter(self, occ) -> bool:
        """현재 활성 필터에 이 등장이 보여야 하는지."""
        flt = getattr(self, "_active_filter", None)
        if flt is None:
            return True
        if flt == PENDING_FILTER:
            return occ["status"] == "pending"
        return _display_category(occ["c"]) == flt

    def _accept_all(self):
        for o in self._occ:
            if not o.get("shadowed"):
                o["status"] = "accepted"
                # 일괄 처리 = 자동 전파와 같은 취급 — 이후 개별 카드에서 반대
                #   선택 시 확인 팝업을 거쳐 그 카드만 뒤집을 수 있다(_set_status).
                o["auto"] = True
                o["by_user"] = False
        self._derive_all()
        self._rebuild_cards()
        self._refresh_preview()
        self._emit_counts()

    def _rebuild_cards(self):
        # 프리페치는 재구성 중 옛 목록을 건드리지 않게 먼저 세운다(끝에서 재시작).
        if hasattr(self, "_prefetch_timer"):
            self._prefetch_timer.stop()
        # 카드별 hide+setParent(None)+deleteLater 대신 콘텐츠 위젯 통째 교체(_new_scroll_content).
        #   장당 1.5ms의 분리 비용이 사라지고(200장 309ms→64ms), 위젯이 최상위 창이 되는
        #   구간 자체가 없어 유령 창(빈 'python' 창) 위험도 원천 제거된다.
        self._cards.clear()
        self._new_scroll_content()
        self._loaded_card_count = 0
        # 첫 배치는 화면을 채울 만큼만 — 나머지는 유휴 프리페치가 이어 만든다.
        #   (50장 일괄은 재구성 때마다 ~433ms 프리즈였다.)
        self._load_more_cards(self._INITIAL_CARDS)

    def _load_more_cards(self, batch_size=50):
        if not hasattr(self, '_occ'): return
        n = len(self._occ)
        idx = self._loaded_card_count
        if idx >= n:
            return
        # 필터가 켜지면 매칭 카드가 띄엄띄엄 있어, '등장 인덱스'로만 batch를 끊으면
        #   첫 50개 중 매칭이 1~2개뿐이라 카드가 거의 안 뜨고, 스크롤도 안 생겨 추가
        #   로드 트리거가 안 걸린다(검수 47건인데 1건만 보이던 버그). 그래서 '실제로
        #   생성한 카드 수'를 기준으로 batch를 채운다 — 매칭을 찾을 때까지 등장을 훑는다.
        added = 0
        while idx < n and added < batch_size:
            occ = self._occ[idx]
            cur = idx
            idx += 1
            if occ.get("shadowed"):
                continue   # 더 긴 교정에 가려진 중복 등장 — 카드 없음
            if not self._occ_matches_filter(occ):
                continue   # 활성 필터(카테고리/미선택)에 맞는 카드만 노출
            card = self._create_card(occ, cur)
            self._scroll_layout.insertWidget(self._scroll_layout.count() - 1, card)
            self._cards.append(card)
            added += 1
        self._loaded_card_count = idx
        # ⚠ 여기서 _emit_counts를 부르지 않는다 — 집계는 self._occ에서 나오므로 카드를
        #   더 만든다고 달라지지 않는데(호출부가 이미 각자 emit한다), 칩 위젯을 매번
        #   파괴·재생성해 배치마다 헛비용이 붙었다.
        self._schedule_prefetch()

    def _reject_all(self):
        for o in self._occ:
            if not o.get("shadowed"):
                o["status"] = "rejected"
                o["auto"] = True      # _accept_all과 동일 — 개별 반대는 확인 후 허용
                o["by_user"] = False
        self._derive_all()
        self._rebuild_cards()
        self._refresh_preview()
        self._emit_counts()

    def refresh_theme(self):
        self._rebuild_cards()
        self._refresh_preview()
        self._apply_card_theme()
        self._update_cat_summary()   # 집계/요약 칩 색을 새 테마로 재적용

    def _apply_card_theme(self):
        pal = current_palette()
        self._inner_card.setStyleSheet(f"background: {pal['surface']}; border: none; border-radius: 12px;")
        if hasattr(self, "_sec_divider"):
            self._sec_divider.setStyleSheet(f"background: {pal['border']}; border: none;")
