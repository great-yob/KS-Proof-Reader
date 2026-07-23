"""
ui/widgets/result_panel.py — 교정 완료 보고서 패널 (글래스 대시보드)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
완료 단계를 '완료 보고서 대시보드'로 구성한다:
  · 헤더 — 문서명 · 완료 시각(정오표/폴더 열기 액션은 하단 StatusFooter로 이동)
  · 파이프라인 히어로 — 01 분석(글자 수) → 02 교정 제안(사전·규칙/AI) → 03 적용(건·곳·적용률)
  · 상세 분석 — 제안 소스 도넛 · 적용률 게이지 · 오류 유형 바 차트
  · 실패 항목(사유 인라인 — 별도 다이얼로그 없이) · 교정 진행 로그

시각 언어(네이버 AI탭 레퍼런스의 글래스모피즘):
  · 배경 = 비비드한 블루-바이올렛 애니메이티드 메시. 저해상도 버퍼에 채도 높은
    라디얼 블롭을 그려 업스케일(업스케일=블러)하고 위상으로 천천히 표류시킨다.
    중앙은 밝게 비워 시선이 콘텐츠에 집중되게 한다.
  · 카드 = GlassCard. 자기 위치의 배경 메시를 샘플링(유리 뒤가 실제로 비침) →
    낮은 알파의 흰 프로스트 → 바닥에 고이는 빛(bottom glow) → 발광 그라디언트
    보더.
  · 콘텐츠 = 중앙 최대폭 컬럼(여백 확보). 카드가 한 장씩 천천히 떠오른다(_Riser).
  · 텍스트 = focus-in-contract(자간 1em→0 수축 + blur 12px→0 + 페이드,
    easeOutQuad) — 블러는 축소→확대 리샘플로 구현한다.

숫자가 주인공: AnimatedNumberLabel이 OutExpo 카운트업 후 '착지 팝'으로 강조한다.
용어 규칙(메모리 bracket-apply-runaway-and-terms): 건=교정 항목 수, 곳=본문 등장/치환 수.
테마: 직접 페인트 위젯은 paint 시점 current_palette()를 읽고, refresh_theme()는
전체 재렌더(애니메이션 없이 최종값)한다. 큰 숫자 폰트는 전역 QSS(QWidget 13px)에
덮이지 않도록 per-instance stylesheet로 지정한다(카드류는 재렌더되므로 허용 패턴).
⚠ QLabel은 QFrame 서브클래스 — per-instance QSS의 `QFrame{}` 선택자는 자식
라벨까지 물들이므로 반드시 objectName으로 스코프를 한정한다.
"새 파일"·정오표 생성/열기·폴더 열기는 하단 StatusFooter(초기화-정오표-폴더열기 순)가 담당한다.
"""

import html
import math
import time
import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTextEdit, QSizePolicy,
    QGraphicsOpacityEffect,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QFontMetrics,
    QImage, QBrush, QPainterPath, QRadialGradient, QLinearGradient,
)
from PySide6.QtCore import (
    Qt, QRectF, QPoint, QPointF, QVariantAnimation, QEasingCurve,
    QTimer,
)

from ui.widgets.components import IconLabel
from ui.styles.theme import current_palette, current_mode


# 오류 유형 폴백 라벨(검수 카드와 동일 언어 — review_panel._SOURCE_LABEL과 맞춤)
_SOURCE_LABEL = {"dict": "사전검증", "ai_typo": "AI 오탈자", "ai_polish": "AI 윤문",
                 "dict_flag": "검수 필요", "spacing": "띄어쓰기", "punct": "문장부호"}


# ══════════════════════════════════════════════════════════════
# ▌완료 대시보드 진행 로그
#   ⚠ 여기서 로그를 **다시 가공하지 않는다.** 예전엔 활동 패널이 원문 로그를
#   주고 이 파일이 자기만의 규칙(_LOG_DROP/_LOG_LEAD/합산)으로 한 번 더
#   큐레이션했다 — 규칙이 두 벌이라 같은 실행의 로그가 라이브 패널과 완료
#   화면에서 다르게 읽혔고, 표기를 바꿀 때마다 양쪽을 맞춰야 했다.
#   이제 `ActivityPanel.get_proofreading_log()`가 **화면에 보이는 요약본
#   그대로**(`[태그] 내용`) 넘겨주므로 그대로 렌더한다(사용자 지시 2026-07-23).
#   표기를 바꿀 곳은 ui/widgets/activity_panel.py의 _RULES 한 곳뿐이다.
# ══════════════════════════════════════════════════════════════

# 메시 배경 전용 브랜드 하이라이트(장식 색 — 팔레트와 별개, 네이버 AI탭 레퍼런스 톤)
_MESH_VIOLET = "#7C5CFF"
_MESH_CYAN = "#38BDF8"
_MESH_PINK = "#FF5CA8"
_MESH_BLUE = "#4F5DFF"

# 완료 강조색 — success_solid 버튼 배경(라이트)과 동일(사용자 지정). 히어로 적용 카드의
# 메인 수치 + 교정 적용률 게이지의 수락 아크/버블. 다크에서는 어두운 유리 배경 위에서
# 시인성이 떨어져(사용자 보고) 더 밝은 네온 그린으로 갈아탄다 — _success_color() 경유 필수.
_SUCCESS_COLOR_LIGHT = "#157F3C"
_SUCCESS_COLOR_DARK = "#00E676"
# '거절' 강조색 — action_pink 버튼의 hover 색과 동일(사용자 지정). 교정 적용률 게이지의
# 거절 아크/버블 전용. 라이트/다크 공통(밝은 핑크라 다크 배경에서도 시인성 충분).
_ACCEPT_COLOR = "#F50057"


def _success_color() -> str:
    """완료 강조색 — 다크모드에서는 더 밝은 네온 그린(_SUCCESS_COLOR_DARK)을 쓴다."""
    return _SUCCESS_COLOR_DARK if current_mode() == "dark" else _SUCCESS_COLOR_LIGHT

# 전역 모션 배속 — 값이 클수록 느리다(2.0 = 기존의 2배 천천히). 등장 지연(_render의
#   start 루프)과 모든 애니메이션 지속(setDuration)에 곱해져 한 곳에서 템포를 통제한다.
_MOTION = 2.0


def _fmt_num(v: float, decimals: int = 0) -> str:
    if decimals > 0:
        return f"{v:.{decimals}f}"
    return f"{int(round(v)):,}"


def _rgba(color: str, alpha: float) -> str:
    """QSS rgba() 문자열 — alpha 0.0~1.0."""
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{max(0, min(100, int(alpha * 100)))}%)"


def _track_color() -> QColor:
    """차트 트랙(빈 궤도) — 글래스 표면 위에서도 자연스러운 반투명."""
    if current_mode() == "dark":
        return QColor(255, 255, 255, 30)
    return QColor(20, 25, 40, 26)


def _ctext(text: str, *, px: int = 14, color: str = "text_sub", weight: int = 500,
           ls: float = 0.0, wrap: bool = False) -> QLabel:
    """per-instance 스타일시트 라벨 — 전역 QSS(QWidget 13px)에 덮이지 않게 크기를
    직접 지정한다. 카드류는 테마 전환 시 _render로 재생성되므로 색 갱신도 자동."""
    lbl = QLabel(text)
    pal = current_palette()
    ls_s = f"letter-spacing:{ls}px; " if ls else ""
    lbl.setStyleSheet(f"color:{pal[color]}; font-size:{px}px; font-weight:{weight}; "
                      f"{ls_s}background:transparent; border:none;")
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def _twidget() -> QWidget:
    """투명 컨테이너 — plain QWidget은 전역 QSS `QWidget{background:$bg}`를
    불투명하게 그려 메시 배경을 가리므로, 선언 전용 스타일시트(하위 트리
    범용 적용)로 투명화한다."""
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    return w


def _hline(on_glass: bool = True) -> QFrame:
    """반투명 구분선 — 글래스 표면/메시 배경 어디서든 자연스럽다."""
    d = QFrame()
    d.setFixedHeight(1)
    dark = current_mode() == "dark"
    if on_glass:
        color = _rgba("#FFFFFF", 0.13) if dark else _rgba("#1A1D23", 0.10)
    else:
        color = _rgba("#FFFFFF", 0.10) if dark else _rgba("#FFFFFF", 0.50)
    d.setStyleSheet(f"background:{color}; border:none;")
    return d


class _MinimalChevron(QWidget):
    """파이프라인 카드 사이의 진행 표시 — 미니멀 쉐브런(">"). 얇은 2획 스트로크만
    그린다(채움·그라디언트·글로우 없음). 등장 시(start) 옅게 페이드인만 한다."""

    def __init__(self, parent=None, direction="right"):
        super().__init__(parent)
        self.direction = direction
        self.setStyleSheet("background: transparent;")
        self.setFixedWidth(34)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._p = 1.0
        self._anim = None

    def start(self, delay: int = 0):
        self._p = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(420 * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, v):
        self._p = float(v)
        self.update()

    def paintEvent(self, _e):
        if self._p <= 0.0:
            return
        pal = current_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = 5.0            # 쉐브런 반폭(">"의 위아래 벌어짐)

        # text_dim은 불투명 표면 전제라 유리 배경(책 이미지가 비치는 가변 명도)
        # 위에서는 거의 안 보인다(사용자 보고) — text_sub로 대비를 확보한다.
        col = QColor(pal["text_sub"])
        col.setAlphaF(min(1.0, self._p))
        pen = QPen(col, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if self.direction == "right":
            p.drawPolyline([
                QPointF(cx - r * 0.55, cy - r),
                QPointF(cx + r * 0.55, cy),
                QPointF(cx - r * 0.55, cy + r),
            ])
        else:
            p.drawPolyline([
                QPointF(cx - r, cy - r * 0.55),
                QPointF(cx, cy + r * 0.55),
                QPointF(cx + r, cy - r * 0.55),
            ])


class _SavedTimeClock(QWidget):
    """헤더 우측 — "절약한 시간 HH:MM"을 레트로 플립 시계(split-flap board) 스타일로
    표시. 자릿수마다 독립된 다크 카드(모서리만 살짝 둥글게) + 카드 정가운데를
    가로지르는 이음선(실물 플립시계의 상판/하판 경계) + 굵은 흰 숫자 — 사용자가
    첨부한 실물 플립시계 사진과 동일한 항상-다크 카드(실물 기기 느낌은 앱 라이트/
    다크 테마를 안 탄다 — 카드·숫자색은 고정, 옆의 "절약한 시간" 라벨만 테마 텍스트
    색을 따라간다)."""

    def __init__(self, hours: int, minutes: int, parent=None):
        super().__init__(parent)
        self._hours = hours
        self._minutes = minutes
        self.setStyleSheet("background: transparent;")
        self._card_w, self._card_h = 25.0, 38.0
        self._card_gap = 4.0          # 같은 그룹(시/분) 내 자릿수 사이 간격
        self._group_gap = 11.0        # 시-분 그룹 사이(콜론 자리)
        self._label_gap = 10.0
        self.setToolTip("페이지 수 기준 수작업 예상 시간(1인 8시간=100쪽) 대비 절약된 시간")
        fm = QFontMetrics(self._label_font())
        label_w = fm.horizontalAdvance("절약한 시간")
        digits_w = self._card_w * 4 + self._card_gap * 2 + self._group_gap
        self.setFixedSize(int(label_w + self._label_gap + digits_w + 4),
                          int(self._card_h + 8))

    def _label_font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setWeight(QFont.Weight(700))
        return f

    def _draw_digit_card(self, p: QPainter, x: float, y: float, ch: str):
        w, h = self._card_w, self._card_h
        rect = QRectF(x, y, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        p.fillPath(path, QColor(18, 18, 20))

        # 상판/하판을 미세하게 다른 명도로 — 플립 메커니즘의 두 쪽 느낌.
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(x, y, w, h / 2), QColor(255, 255, 255, 40))
        p.fillRect(QRectF(x, y + h / 2, w, h / 2), QColor(255, 255, 255, 10))
        p.restore()

        font = QFont(self.font())
        font.setPixelSize(int(h * 0.72))
        font.setWeight(QFont.Weight(800))
        p.setFont(font)
        p.setPen(QColor(240, 240, 242))
        p.drawText(rect, Qt.AlignCenter, ch)

        # 가운데 이음선(플립 경계).
        p.setPen(QPen(QColor(0, 0, 0, 180), 1.4))
        p.drawLine(QPointF(x + 3, y + h / 2), QPointF(x + w - 3, y + h / 2))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        label_col = QColor(current_palette()["text"])
        p.setPen(label_col)
        p.setFont(self._label_font())
        fm = QFontMetrics(self._label_font())
        label_w = fm.horizontalAdvance("절약한 시간")
        label_rect = QRectF(0, 0, label_w, self.height())
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, "절약한 시간")

        text = f"{self._hours:02d}{self._minutes:02d}"
        y = (self.height() - self._card_h) / 2
        x = label_w + self._label_gap
        for i, ch in enumerate(text):
            self._draw_digit_card(p, x, y, ch)
            x += self._card_w
            if i == 1:
                p.setPen(Qt.NoPen)
                p.setBrush(label_col)
                cx = x + self._group_gap / 2
                r = 2.2
                p.drawEllipse(QPointF(cx, y + self._card_h * 0.33), r, r)
                p.drawEllipse(QPointF(cx, y + self._card_h * 0.67), r, r)
                x += self._group_gap
            else:
                x += self._card_gap


# ══════════════════════════════════════════════════════════════
# 글래스 카드 — 배경 메시 샘플링 + 프로스트 + 글로우 + shine
# ══════════════════════════════════════════════════════════════

class GlassCard(QFrame):
    """글래스모피즘 카드. 조상 ResultPanel의 메시 이미지를 자기 위치만큼
    샘플링해 그린 뒤(유리 뒤가 실제로 비침) 흰 프로스트·바닥 글로우·발광
    보더를 얹는다. start(delay)로 표면을 스치는 shine sweep을 1회 재생한다.
    _reveal(0~1)은 _Riser가 등장 진행도를 동기화하는 값."""

    RADIUS = 20

    def __init__(self, tint: str = "default", parent=None):
        super().__init__(parent)
        self._tint = tint          # "default" | "accent"(히어로 카드 워시)
        self._shine = 1.0          # 1.0 = 정적/종료 상태
        self._reveal = 1.0
        self._anim = None
        self._panel_ref = None

    def set_tint(self, tint: str):
        self._tint = tint
        self.update()

    def start(self, delay: int = 0):
        self._shine = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(950 * _MOTION))
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, p):
        self._shine = float(p)
        self.update()

    def _panel(self):
        if self._panel_ref is not None:
            return self._panel_ref
        w = self.parentWidget()
        while w is not None and not hasattr(w, "_mesh_image"):
            w = w.parentWidget()
        self._panel_ref = w
        return w

    def refresh_theme(self):
        self.update()

    def paintEvent(self, _e):
        pal = current_palette()
        dark = current_mode() == "dark"
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        r = QRectF(0.5, 0.5, w - 1, h - 1)
        path = QPainterPath()
        path.addRoundedRect(r, self.RADIUS, self.RADIUS)
        p.setClipPath(path)

        # 1) 굴절(refraction) — 배경 메시를 축소→확대 리샘플해 두꺼운 유리 너머
        #    사물이 흐릿하게 비치는 효과.
        panel = self._panel()
        if panel is not None and panel.width() > 0:
            off = self.mapTo(panel, QPoint(0, 0))
            mesh = panel._mesh_image()
            sx = off.x() / max(1, panel.width()) * mesh.width()
            sy = off.y() / max(1, panel.height()) * mesh.height()
            sw = w / max(1, panel.width()) * mesh.width()
            sh = h / max(1, panel.height()) * mesh.height()
            crop = mesh.copy(int(sx), int(sy), max(1, int(sw)), max(1, int(sh)))
            tiny = crop.scaled(max(1, crop.width() // 4), max(1, crop.height() // 4),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            blurred = tiny.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p.drawImage(self.rect(), blurred)
        else:
            p.fillRect(self.rect(), QColor(pal["surface"]))

        # 2) 프로스트 틴트 — 전보다 더 투명하게(배경이 충분히 비침).
        fg = QLinearGradient(0, 0, 0, h)
        if dark:
            # 다크모드: 어두운 색을 덧칠하면 배경에 묻히므로, 하얀 서리를 얇게 깔아 밝기를 올립니다.
            top = QColor(255, 255, 255, 12)
            bot = QColor(255, 255, 255, 28)
        else:
            top = QColor(255, 255, 255, 45)
            bot = QColor(255, 255, 255, 75)
        fg.setColorAt(0.0, top)
        fg.setColorAt(1.0, bot)
        p.fillRect(self.rect(), QBrush(fg))

        # 3) 바닥에 고이는 빛 — 유리 하단의 은은한 발광.
        def bottom_glow(color, alpha, rad=0.78):
            c = QColor(color)
            c.setAlpha(alpha)
            g = QRadialGradient(w * 0.5, h * 1.18, max(w, h) * rad)
            g.setColorAt(0.0, c)
            c0 = QColor(c); c0.setAlpha(0)
            g.setColorAt(1.0, c0)
            p.fillRect(self.rect(), QBrush(g))
        bottom_glow(pal["accent"], 28 if dark else 16)

        # 4) 좌상단 모서리 스펙큘러 — 밝은 방사형 하이라이트(유리 모서리 반사).
        spec_r = min(w, h) * 0.45
        sg = QRadialGradient(self.RADIUS * 0.8, self.RADIUS * 0.8, spec_r)
        sg.setColorAt(0.0, QColor(255, 255, 255, 40 if dark else 120))
        sg.setColorAt(0.5, QColor(255, 255, 255, 10 if dark else 35))
        sg.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), QBrush(sg))

        # 5) shine sweep — 표면을 스치는 사선 빛줄기(등장 시 1회)
        s = self._shine
        if 0.0 <= s < 1.0:
            band = w * 0.45
            x = -band + s * (w + band * 2)
            shine_g = QLinearGradient(x, 0, x + band, h * 0.4)
            shine_g.setColorAt(0.0, QColor(255, 255, 255, 0))
            shine_g.setColorAt(0.5, QColor(255, 255, 255, 34 if dark else 80))
            shine_g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(self.rect(), QBrush(shine_g))

        # 6) 테두리 — 4면 균일한 유리 테두리. 다크모드에서는 진한 색.
        p.setClipping(False)
        if dark:
            p.setPen(QPen(QColor(96, 96, 96, 80), 1.0))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 100), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


# ══════════════════════════════════════════════════════════════
# 모션 프리미티브
# ══════════════════════════════════════════════════════════════

class _Riser(QWidget):
    """카드/섹션 래퍼 — start() 시 아래에서 천천히 떠오르며 페이드인.
    (임시 QGraphicsOpacityEffect + 상단 마진 수축, 종료 후 이펙트 제거)
    자식이 GlassCard면 _reveal을 동기화해 패널 그림자도 함께 짙어진다.
    start를 부르지 않으면 최종 상태(마진 0, 불투명)로 정적 표시된다."""

    def __init__(self, child: QWidget, dist: int = 26, duration: int = 680,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")   # 전역 QSS $bg 차단
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addWidget(child)
        self._child = child
        self._dist = dist
        self._dur = duration
        self._anim = None

    def _sync_reveal(self, v: float):
        if isinstance(self._child, GlassCard):
            self._child._reveal = v

    def start(self, delay: int = 0):
        eff = QGraphicsOpacityEffect(self._child)
        eff.setOpacity(0.0)
        self._child.setGraphicsEffect(eff)
        self._lay.setContentsMargins(0, self._dist, 0, 0)
        self._sync_reveal(0.0)

        def run():
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(int(self._dur * _MOTION))
            anim.setEasingCurve(QEasingCurve.OutCubic)

            def tick(v):
                v = float(v)
                if self._child.graphicsEffect() is eff:
                    eff.setOpacity(v)
                self._lay.setContentsMargins(0, int(self._dist * (1 - v)), 0, 0)
                self._sync_reveal(v)

            def done():
                if self._child.graphicsEffect() is eff:
                    self._child.setGraphicsEffect(None)
                self._lay.setContentsMargins(0, 0, 0, 0)
                self._sync_reveal(1.0)

            anim.valueChanged.connect(tick)
            anim.finished.connect(done)
            self._anim = anim
            anim.start()

        if delay > 0:
            QTimer.singleShot(delay, run)
        else:
            run()


class _FocusInLabel(QWidget):
    """focus-in-contract 텍스트 — 자간 수축 + blur(12px→0) + 페이드인.
    CSS 레퍼런스: cubic-bezier(.25,.46,.45,.94) ≈ OutQuad, 0.7s.
    블러는 텍스트를 이미지로 그린 뒤 축소→확대 리샘플로 구현한다."""

    def __init__(self, text: str, *, px: int = 21, weight: int = 800,
                 color_key: str = "text", start_ls: float = None,
                 duration: int = 700, max_w: int = 560, parent=None):
        super().__init__(parent)
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setWeight(QFont.Weight(weight))
        self._font = f
        fm = QFontMetrics(f)
        self._text = fm.elidedText(text, Qt.ElideMiddle, max_w)
        self._color_key = color_key
        self._start_ls = px * 1.0 if start_ls is None else start_ls
        self._dur = duration
        self.text_w = fm.horizontalAdvance(self._text)
        self.setFixedSize(self.text_w + 8, fm.height() + 8)
        self._p = 1.0
        self._anim = None

    def start(self, delay: int = 0):
        self._p = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(self._dur * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutQuad)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, p):
        self._p = float(p)
        self.update()

    def paintEvent(self, _e):
        p = self._p
        if p <= 0.0:
            return
        pal = current_palette()
        fm = QFontMetrics(self._font)
        base = fm.ascent() + 3
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)

        if p >= 1.0:                       # 정적/종료 — 선명하게 직접 그린다
            painter.setFont(self._font)
            painter.setPen(QColor(pal[self._color_key]))
            painter.drawText(QPointF(2, base), self._text)
            return

        ls = self._start_ls * (1 - p)
        blur = 12.0 * (1 - p)
        w, h = self.width(), self.height()
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        ip = QPainter(img)
        ip.setRenderHint(QPainter.TextAntialiasing)
        ip.setFont(self._font)
        ip.setPen(QColor(pal[self._color_key]))
        x = 2.0
        for ch in self._text:
            ip.drawText(QPointF(x, base), ch)
            x += fm.horizontalAdvance(ch) + ls
        ip.end()
        if blur > 0.4:                     # 축소→확대 리샘플 = 가우시안 근사
            f = 1.0 + blur * 0.45
            sw, sh = max(1, int(w / f)), max(1, int(h / f))
            img = img.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
                     .scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter.setOpacity(p)
        painter.drawImage(0, 0, img)


# ══════════════════════════════════════════════════════════════
# 애니메이션 프리미티브 (숫자/차트)
# ══════════════════════════════════════════════════════════════

class AnimatedNumberLabel(QLabel):
    """카운트업 숫자 라벨 — OutExpo 이징(급가속 후 감속 착지) + 착지 팝.

    기본은 최종값 정적 표시(테마 재렌더용). start(delay)를 부르면 0부터
    카운트업한다. 최종 문자열 폭을 예약(setMinimumWidth)해 카운트 중
    주변 레이아웃이 출렁이지 않는다.
    """

    def __init__(self, value: float, *, decimals: int = 0, px: int = 38,
                 weight: int = 800, color: str = None, duration: int = 1500,
                 parent=None):
        super().__init__(parent)
        self._value = float(value)
        self._decimals = decimals
        self._px = px
        self._weight = weight
        self._color = color or current_palette()["text"]
        self._duration = duration
        self._anim = None
        self._pop = None
        self._apply_style(1.0)
        # 최종 폭 + 팝 확대분(7%) 예약 — 전역 QSS와 같은 Pretendard 기준 측정.
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setWeight(QFont.Weight(weight) if weight <= 1000 else QFont.Bold)
        fm = QFontMetrics(f)
        final = _fmt_num(self._value, decimals)
        self.setMinimumWidth(int(fm.horizontalAdvance(final) * 1.07) + 2)
        self.setFixedHeight(int(fm.height() * 1.08))
        self.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self.setText(final)

    def _apply_style(self, scale: float):
        self.setStyleSheet(
            f"color:{self._color}; font-size:{max(1, round(self._px * scale))}px; "
            f"font-weight:{self._weight}; background:transparent; border:none;")

    def start(self, delay: int = 0):
        self.setText(_fmt_num(0, self._decimals))
        if delay > 0:
            QTimer.singleShot(delay, self._run)
        else:
            self._run()

    def _run(self):
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(self._duration * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutExpo)
        anim.valueChanged.connect(
            lambda p: self.setText(_fmt_num(self._value * float(p), self._decimals)))
        anim.finished.connect(self._land)
        self._anim = anim
        anim.start()

    def _land(self):
        """착지 — 최종값 고정 (애니메이션 없음)."""
        self.setText(_fmt_num(self._value, self._decimals))
        self._apply_style(1.0)


def _paint_tooltip_bubble(painter, pal, bounds_w, bounds_h, ax, ay, bx, by, text, color):
    """리더 라인 + 말풍선 1개(항상 표출되는 데이터 라벨). (ax,ay)=차트 표면의 시작점,
    (bx,by)=버블 중심 희망 위치 — bounds_w/h(캔버스 크기) 안쪽으로 클램프한다.
    color는 QColor 또는 팔레트 hex 문자열. DonutChartWidget·RadialGauge 공용."""
    font = QFont(painter.font())
    font.setPixelSize(12)
    font.setWeight(QFont.Weight(600))
    fm = QFontMetrics(font)
    pad_x, pad_y, dot_d, gap = 9, 6, 7, 6
    tw = fm.horizontalAdvance(text)
    box_w = pad_x * 2 + dot_d + gap + tw
    box_h = pad_y * 2 + fm.height()
    bx = max(box_w / 2 + 2, min(bounds_w - box_w / 2 - 2, bx))
    by = max(box_h / 2 + 2, min(bounds_h - box_h / 2 - 2, by))
    box = QRectF(bx - box_w / 2, by - box_h / 2, box_w, box_h)

    # 리더 라인 — 옅은 중립색은 유리 카드 위에서 거의 안 보였다(사용자 보고).
    # 데이터 색을 그대로 써서 어느 배경에서도 뚜렷하게 보이게 하고, 차트 표면
    # 쪽 끝에 작은 앵커 점을 찍어 "이 지점을 가리킨다"는 걸 분명히 한다.
    line_col = QColor(color)
    painter.setPen(QPen(line_col, 1.6))
    painter.drawLine(QPointF(ax, ay), QPointF(bx, by))
    painter.setPen(Qt.NoPen)
    painter.setBrush(line_col)
    painter.drawEllipse(QPointF(ax, ay), 2.6, 2.6)

    painter.setPen(QPen(QColor(pal["border"]), 1))
    painter.setBrush(QColor(pal["surface"]))
    painter.drawRoundedRect(box, 9, 9)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(box.left() + pad_x, by - dot_d / 2, dot_d, dot_d))

    painter.setPen(QColor(pal["text"]))
    painter.setFont(font)
    tx = box.left() + pad_x + dot_d + gap
    painter.drawText(QRectF(tx, box.top(), box.right() - tx, box.height()),
                     Qt.AlignVCenter | Qt.AlignLeft, text)


class DonutChartWidget(QWidget):
    """세그먼트 도넛 — 중앙에 합계 카운트업. segments=[(값, 팔레트키)].
    labels를 주면 범례 목록 대신 **항상 고정된 위치**(5시/11시)에서 리더 라인으로
    링과 이어진 툴팁 버블로 라벨을 표시한다(호버 불필요, 값이 0이어도 "0건"으로 표시).
    ⚠ 버블 "위치"는 고정이지만 리더 라인의 링쪽 끝(앵커)은 해당 세그먼트의 실제
    중앙각을 가리킨다 — 버블만 고정, 화살표 시작점은 데이터를 따라간다.
    값 0도 목록에서 빼면 안 되므로 segments를 값>0으로 필터링하지 않는다
    (라벨과 1:1 zip 유지)."""

    _BUBBLE_ANGLES = (60, 240)   # 고정 위치: index0=5시(우하), index1=11시(좌상).

    def __init__(self, segments, *, unit: str = "건", caption: str = None,
                 labels: list = None, size: int = 158,
                 canvas_w: int = None, canvas_h: int = None, parent=None):
        super().__init__(parent)
        self._segments = [(float(v), key) for v, key in segments]
        self._labels = labels or []
        self._total = sum(v for v, _ in self._segments)
        self._unit = unit
        self._caption = caption
        self._ring_size = size          # 링 지름(캔버스가 커져도 링 자체 크기는 고정)
        self._p = 1.0
        self._anim = None
        self.setFixedSize(canvas_w or size, canvas_h or size)

    def start(self, delay: int = 0):
        self._p = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(1300 * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, p):
        self._p = float(p)
        self.update()

    def paintEvent(self, _e):
        pal = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        size = self._ring_size - 18     # 캔버스가 버블용으로 커져도 링 지름은 고정.
        rect = QRectF((w - size) / 2, (h - size) / 2, size, size)

        pen = QPen(_track_color(), 13, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        # 각 세그먼트: 최종 시작각은 고정, 길이만 p에 비례해 자란다.
        cum = 0.0
        for value, key in self._segments:
            frac = value / self._total if self._total else 0
            start_angle = int((90 - cum * 360) * 16)
            span = -int(frac * 360 * self._p * 16)
            if span:
                c_val = key if key.startswith("#") else pal[key]
                pen.setColor(QColor(c_val))
                painter.setPen(pen)
                painter.drawArc(rect, start_angle, span)
            cum += frac

        # 중앙: 합계 카운트업 + 단위 + 캡션
        big = _fmt_num(self._total * self._p)
        font = painter.font()
        font.setPixelSize(31)
        font.setBold(True)
        fm_big = QFontMetrics(font)
        font_s = QFont(font)
        font_s.setPixelSize(14)
        font_s.setBold(False)
        fm_s = QFontMetrics(font_s)

        total_w = fm_big.horizontalAdvance(big) + 2 + fm_s.horizontalAdvance(self._unit)
        x = (w - total_w) / 2
        base = h / 2 + fm_big.ascent() / 2 - 7
        painter.setFont(font)
        painter.setPen(QColor(pal["text"]))
        painter.drawText(QRectF(x, base - fm_big.ascent(), fm_big.horizontalAdvance(big) + 2,
                                fm_big.height()), Qt.AlignLeft | Qt.AlignBottom, big)
        painter.setFont(font_s)
        painter.setPen(QColor(pal["text_sub"]))
        painter.drawText(QRectF(x + fm_big.horizontalAdvance(big) + 2, base - fm_s.ascent(),
                                fm_s.horizontalAdvance(self._unit) + 2, fm_s.height()),
                         Qt.AlignLeft | Qt.AlignBottom, self._unit)

        font_c = QFont(font_s)
        font_c.setPixelSize(13)
        painter.setFont(font_c)
        painter.setPen(QColor(pal["text_muted"]))
        painter.drawText(QRectF(0, base + 5, w, 18), Qt.AlignHCenter | Qt.AlignTop, self._caption)

        # 세그먼트 라벨 — 범례 목록 대신 **항상 고정된 위치**(4~5시/10~11시)에서
        # 말풍선을 그린다(호버 불필요, 값이 0인 세그먼트도 "0건"으로 표시). 버블
        # "위치"만 고정이고, 리더 라인의 링쪽 끝(앵커)은 그 세그먼트의 실제 중앙각을
        # 가리켜 — 버블은 안 움직이되 화살표는 데이터를 따라간다.
        if self._labels:
            cx, cy = w / 2, h / 2
            radius = size / 2
            cum = 0.0
            for i, ((value, key), label) in enumerate(zip(self._segments, self._labels)):
                frac = value / self._total if self._total else 0
                mid = cum + frac / 2
                cum += frac
                if i >= len(self._BUBBLE_ANGLES):
                    continue
                anchor_ang = math.radians(-90 + mid * 360)
                ax = cx + (radius + 6.5) * math.cos(anchor_ang)
                ay = cy + (radius + 6.5) * math.sin(anchor_ang)
                bubble_ang = math.radians(self._BUBBLE_ANGLES[i])
                bx = cx + (radius + 6.5 + 55) * math.cos(bubble_ang)
                by = cy + (radius + 6.5 + 55) * math.sin(bubble_ang)
                c_val = key if key.startswith("#") else pal[key]
                _paint_tooltip_bubble(painter, pal, w, h, ax, ay, bx, by, label, c_val)


class RadialGauge(QWidget):
    """단일 값 게이지 — 중앙에 % 카운트업(또는 고정 텍스트) + 캡션.
    bubble_label(단일) 또는 bubble_labels=(수락 텍스트, 거절 텍스트)를 주면
    외부 범례 텍스트 대신 링에서 뻗어나온 **항상 표출되는 툴팁 버블**
    (DonutChartWidget과 동일 부품)을 그린다. bubble_labels는 **항상 고정된 위치**
    (수락=5시, 거절=11시)에서 말풍선 2개를 띄워 도넛과 동일한 2항목 구분을
    낸다 — 값이 0이어도 표시(과거처럼 fraction 0/1 경계에서 숨기지 않음). 버블
    "위치"만 고정이고 리더 라인의 링쪽 끝(앵커)은 실제 아크 중앙각을 가리킨다."""

    _BUBBLE_ANGLE_ACCEPT = 60     # 고정 위치 5시(우하)
    _BUBBLE_ANGLE_REJECT = 240    # 고정 위치 11시(좌상)

    def __init__(self, fraction: float, *, caption: str = None,
                 center_text: str = None, bubble_label: str = None,
                 bubble_labels: tuple = None, fill_color: str = None,
                 reject_color: str = None,
                 size: int = 158, canvas_w: int = None, canvas_h: int = None,
                 parent=None):
        super().__init__(parent)
        self._fraction = max(0.0, min(1.0, fraction))
        self._caption = caption
        self._center_text = center_text     # 지정 시 % 대신 고정 텍스트
        self._bubble_label = bubble_label
        self._fill_color = fill_color       # 채워진 아크/수락 버블 색(미지정 시 pal["accent"])
        self._reject_color = reject_color   # 남은 트랙/거절 버블 색(미지정 시 기존 회색 트랙)
        self._bubble_labels = bubble_labels  # (수락 텍스트, 거절 텍스트)
        self._ring_size = size              # 링 지름(캔버스가 커져도 링 자체 크기는 고정)
        self._p = 1.0
        self._anim = None
        self.setFixedSize(canvas_w or size, canvas_h or size)

    def start(self, delay: int = 0):
        self._p = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(1300 * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, p):
        self._p = float(p)
        self.update()

    def paintEvent(self, _e):
        pal = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        size = self._ring_size - 18     # 캔버스가 버블용으로 커져도 링 지름은 고정.
        rect = QRectF((w - size) / 2, (h - size) / 2, size, size)

        reject = self._reject_color or _track_color()
        pen = QPen(QColor(reject), 13, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        fill = self._fill_color or pal["accent"]
        span = -int(self._fraction * self._p * 360 * 16)
        if span:
            pen.setColor(QColor(fill))
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, span)

        font = painter.font()
        font.setBold(True)
        painter.setPen(QColor(pal["text"]))
        if self._center_text is not None:
            font.setPixelSize(23)
            painter.setFont(font)
            painter.drawText(QRectF(0, h / 2 - 24, w, 30), Qt.AlignCenter, self._center_text)
        else:
            big = _fmt_num(self._fraction * 100 * self._p, 1)
            font.setPixelSize(29)
            fm_big = QFontMetrics(font)
            font_s = QFont(font)
            font_s.setPixelSize(14)
            font_s.setBold(False)
            fm_s = QFontMetrics(font_s)
            total_w = fm_big.horizontalAdvance(big) + 1 + fm_s.horizontalAdvance("%")
            x = (w - total_w) / 2
            base = h / 2 + fm_big.ascent() / 2 - 7
            painter.setFont(font)
            painter.drawText(QRectF(x, base - fm_big.ascent(), fm_big.horizontalAdvance(big) + 2,
                                    fm_big.height()), Qt.AlignLeft | Qt.AlignBottom, big)
            painter.setFont(font_s)
            painter.setPen(QColor(pal["text_sub"]))
            painter.drawText(QRectF(x + fm_big.horizontalAdvance(big) + 1, base - fm_s.ascent(),
                                    fm_s.horizontalAdvance("%") + 2, fm_s.height()),
                             Qt.AlignLeft | Qt.AlignBottom, "%")

        if self._caption:
            font_c = painter.font()
            font_c.setPixelSize(13)
            font_c.setBold(False)
            painter.setFont(font_c)
            painter.setPen(QColor(pal["text_muted"]))
            painter.drawText(QRectF(0, h / 2 + 11, w, 18),
                             Qt.AlignHCenter | Qt.AlignTop, self._caption)

        # 외부 범례 텍스트 대신 링에서 뻗어나온 말풍선을 **항상 고정된 위치**에서
        # 그린다(DonutChartWidget과 동일 원리) — 값이 0이어도 숨기지 않고 "0건" 표시.
        # 버블 "위치"만 고정이고, 리더 라인의 링쪽 끝(앵커)은 해당 구간의 실제
        # 중앙각을 가리킨다(버블은 안 움직이되 화살표는 데이터를 따라간다).
        cx, cy = w / 2, h / 2
        radius = size / 2

        def _bubble_at(anchor_mid_t, bubble_angle_deg, text, color):
            anchor_ang = math.radians(-90 + anchor_mid_t * 360)
            ax = cx + (radius + 6.5) * math.cos(anchor_ang)
            ay = cy + (radius + 6.5) * math.sin(anchor_ang)
            bubble_ang = math.radians(bubble_angle_deg)
            bx = cx + (radius + 6.5 + 55) * math.cos(bubble_ang)
            by = cy + (radius + 6.5 + 55) * math.sin(bubble_ang)
            _paint_tooltip_bubble(painter, pal, w, h, ax, ay, bx, by, text, color)

        if self._bubble_labels:
            accepted_text, rejected_text = self._bubble_labels
            if accepted_text:
                _bubble_at(self._fraction / 2, self._BUBBLE_ANGLE_ACCEPT, accepted_text, fill)
            if rejected_text:
                _bubble_at((1.0 + self._fraction) / 2, self._BUBBLE_ANGLE_REJECT, rejected_text, reject)
        elif self._bubble_label:
            _bubble_at(self._fraction / 2, self._BUBBLE_ANGLE_ACCEPT, self._bubble_label, fill)


class BarListChart(QWidget):
    """가로 바 목록 — 행별 스태거로 차오르고 수치도 함께 카운트업."""

    ROW_H = 47

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._items = [(str(k), int(v)) for k, v in items][:5]
        self._max = max([v for _, v in self._items] or [1])
        self._p = 1.0
        self._anim = None
        self.setMinimumWidth(160)
        self.setFixedHeight(self.ROW_H * len(self._items))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def start(self, delay: int = 0):
        self._p = 0.0
        self.update()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(1400 * _MOTION))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._tick)
        self._anim = anim
        if delay > 0:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()

    def _tick(self, p):
        self._p = float(p)
        self.update()

    def paintEvent(self, _e):
        pal = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()

        font_l = painter.font()
        font_l.setPixelSize(14)
        font_n = QFont(font_l)
        font_n.setPixelSize(15)
        font_n.setBold(True)

        for i, (name, count) in enumerate(self._items):
            # 행 스태거 — 위 행부터 순차적으로 차오른다.
            # p=1.0에서 마지막 행(i=4)도 정확히 1.0에 도달해야 최종 수치가 맞는다.
            rp = 1.0 if self._p >= 1.0 else \
                max(0.0, min(1.0, self._p * 1.5 - i * 0.12))
            y = i * self.ROW_H
            painter.setFont(font_l)
            painter.setPen(QColor(pal["text_sub"]))
            painter.drawText(QRectF(0, y, w - 72, 20), Qt.AlignLeft | Qt.AlignVCenter, name)
            painter.setFont(font_n)
            painter.setPen(QColor(pal["text"]))
            painter.drawText(QRectF(w - 96, y, 96, 20), Qt.AlignRight | Qt.AlignVCenter,
                             f"{_fmt_num(count * rp)}건")
            # 트랙 + 채움(accent 그라디언트)
            track_y = y + 26
            painter.setPen(Qt.NoPen)
            painter.setBrush(_track_color())
            painter.drawRoundedRect(QRectF(0, track_y, w, 8), 4, 4)
            fill_w = w * (count / self._max) * rp
            if fill_w > 1:
                grad = QLinearGradient(0, 0, w, 0)
                grad.setColorAt(0.0, QColor(pal["accent"]))
                grad.setColorAt(1.0, QColor(_MESH_CYAN))
                painter.setBrush(QBrush(grad))
                painter.drawRoundedRect(QRectF(0, track_y, fill_w, 8), 4, 4)


# ══════════════════════════════════════════════════════════════
# 결과 패널
# ══════════════════════════════════════════════════════════════

class ResultPanel(QWidget):
    # 유리 뒤 배경 샘플용 저해상도 버퍼 폭 — 리샘플이 곧 블러이므로 작을수록 프로스트감↑.
    _MESH_W = 160
    # 패널 배경(책 이미지 합성)의 중간 해상도 폭 — 이 폭을 넘으면 다운스케일해 성능 확보.
    _BG_W = 900
    # 중앙 콘텐츠 컬럼 최대폭 — 좌우 여백으로 시선을 가운데 모은다.
    _COL_MAX_W = 1080

    # 등장 스케줄(ms) — 카드가 한 장씩 천천히 떠오른다.
    _T_CARD1, _T_CARD2, _T_CARD3 = 350, 700, 1050
    _T_CH1, _T_CH2, _T_CH3 = 1450, 1700, 1950
    _T_BOT1, _T_BOT2 = 2250, 2500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = {}
        self._log = []
        self._corrections = None
        self._char_count = None
        self._page_count = None
        self._file_name = ""
        self._completed_at = ""
        self._animate = False
        self._glass_cards = []
        self._anims = []
        # 배경 — 위상 드리프트 타이머(표시 중에만 동작)
        self._t0 = time.time()
        self._bg_cache = None              # ((bw, bh), QImage) — 패널 배경(책 합성)
        self._mesh_cache = None            # (True, QImage) — 유리 뒤 샘플용 저해상 블러
        self._mesh_cache = None            # (True, QImage) — 유리 뒤 샘플용 저해상 블러
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._build_scaffold()

    # ── 스캐폴드 ─────────────────────────────────
    def _build_scaffold(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QWidget#ResultContent { background: transparent; } "
            "QWidget#ResultColumn { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)

        # 중앙 최대폭 컬럼 — 좌우 스트레치가 여백을 만든다.
        self._content = QWidget()
        self._content.setObjectName("ResultContent")
        outer = QHBoxLayout(self._content)
        outer.setContentsMargins(36, 30, 36, 40)
        outer.setSpacing(0)
        holder = QWidget()
        holder.setObjectName("ResultColumn")
        holder.setMaximumWidth(self._COL_MAX_W)
        holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._col = QVBoxLayout(holder)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(30)
        outer.addStretch(1)
        outer.addWidget(holder, 8)
        outer.addStretch(1)
        scroll.setWidget(self._content)

        from ui.widgets.components import SmoothScrollFilter
        self._smooth = SmoothScrollFilter(scroll.verticalScrollBar(), self)
        scroll.viewport().installEventFilter(self._smooth)
        # 스크롤 시 글래스 카드가 '유리 뒤 배경'을 다시 샘플링하도록 갱신
        scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        root.addWidget(scroll)

    # ── 메시 배경(정적 그라디언트) ─────────
    def showEvent(self, e):
        super().showEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)

    def _on_scroll(self, *_):
        # 배경은 그대로(캐시 유지) — 카드만 새 위치로 유리 뒤를 다시 샘플링.
        self.update()
        self._update_glass()

    def _update_glass(self):
        for c in self._glass_cards:
            try:
                c.update()
            except RuntimeError:      # 재렌더로 이미 소멸한 카드
                pass

    def _bg_image(self) -> QImage:
        """패널 배경 — 오로라 그라디언트 렌더링.
        중간 해상도(_BG_W)로 캐시하고 paintEvent가 패널 크기로 살짝 업스케일한다."""
        w = max(1, self.width())
        h = max(1, self.height())
        scale = min(1.0, self._BG_W / w)
        bw = max(1, int(w * scale))
        bh = max(1, int(h * scale))
        if self._bg_cache is not None and self._bg_cache[0] == (bw, bh):
            return self._bg_cache[1]
        # 애니메이션 대신 가장 예쁘게 배치되는 특정 시점(t=42.0)으로 고정합니다.
        fixed_t = 42.0
        img = self._compose_bg(bw, bh, fixed_t)
        self._bg_cache = ((bw, bh), img)
        return img

    def _mesh_image(self) -> QImage:
        """유리 카드가 샘플링하는 '유리 뒤 배경' — 배경을 저해상도로 리샘플(=블러).
        블러된 책 디테일이 프로스트 유리 너머로 은은히 비쳐 유리감을 살린다."""
        if self._mesh_cache is not None:
            return self._mesh_cache[1]
        bg = self._bg_image()
        sw = self._MESH_W
        sh = max(60, int(sw * bg.height() / max(1, bg.width())))
        small = bg.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self._mesh_cache = (True, small)
        return small

    @staticmethod
    def _compose_bg(bw: int, bh: int, t: float) -> QImage:
        """오로라 그라디언트 배경 — 3개 레이어(딥 블롭 + 대각선 오로라 커튼 +
        밝은 악센트)를 합성해 역동적이고 깊이감 있는 배경을 만든다.
        각 요소가 서로 다른 주파수·위상으로 움직여 유기적 오로라를 연출한다."""
        dark = current_mode() == "dark"
        img = QImage(bw, bh, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("#080B18") if dark else QColor("#EAF0FF"))
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        s, c_ = math.sin, math.cos

        # ── 유틸: 원형 블롭 ────────────────────────
        def blob(color, cx, cy, rad, alpha):
            c = QColor(color); c.setAlpha(alpha)
            g = QRadialGradient(cx * bw, cy * bh, rad * bw)
            g.setColorAt(0.0, c)
            c0 = QColor(c); c0.setAlpha(0)
            g.setColorAt(1.0, c0)
            p.fillRect(0, 0, bw, bh, QBrush(g))

        # ── 유틸: 대각선 오로라 커튼(리본) ─────────────
        #    angle(라디안)으로 회전한 선형 그라디언트 — 길쭉한 빛의 띠.
        #    pos(0~1)는 커튼의 수직 위치, width는 커튼 폭(0~1).
        def curtain(color, angle, pos, width, alpha):
            co = QColor(color)
            # 그라디언트 시작/끝 — 회전된 축
            dx = c_(angle) * bw
            dy = s(angle) * bh
            cx, cy = bw * 0.5, bh * pos
            g = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
            c1 = QColor(co); c1.setAlpha(0)
            c2 = QColor(co); c2.setAlpha(alpha)
            c3 = QColor(co); c3.setAlpha(0)
            hw = width * 0.5
            g.setColorAt(max(0.0, 0.5 - hw), c1)
            g.setColorAt(0.5, c2)
            g.setColorAt(min(1.0, 0.5 + hw), c3)
            p.fillRect(0, 0, bw, bh, QBrush(g))

        if dark:
            # ═══ 1. 매크로 배경 컬러 (세로 기둥형 오로라 베이스) ═══
            # 왼쪽은 시안/블루, 중앙은 레드/오렌지 빛줄기, 오른쪽은 다크 네이비/블랙
            lg = QLinearGradient(0, 0, bw, 0)
            lg.setColorAt(0.0, QColor("#054568"))
            lg.setColorAt(0.3, QColor("#033250"))
            lg.setColorAt(0.42, QColor("#610D27")) # 중앙 레드 시작
            lg.setColorAt(0.50, QColor("#7B3411")) # 중앙 오렌지 (더욱 어둡게)
            lg.setColorAt(0.55, QColor("#2E0E12")) # 오렌지 페이드 아웃
            lg.setColorAt(0.70, QColor("#020B1A")) # 우측 다크 네이비
            lg.setColorAt(1.0, QColor("#000105"))  # 우측 끝 블랙
            p.fillRect(0, 0, bw, bh, QBrush(lg))

            # ═══ 2. 세로 띠 (Pleats / Striations) 오버레이 ═══
            # 수십 개의 얇은 세로 그림자를 그려 주름진 커튼(Pillars) 느낌 연출
            p.setCompositionMode(QPainter.CompositionMode_Overlay)
            num_pillars = 40
            for i in range(num_pillars):
                nx = i / num_pillars
                # 주름 너비와 밝기 불규칙성
                w_mod = s(nx * 25.0 + t * 0.5) * 0.5 + 0.5
                width = (0.015 + w_mod * 0.02) * bw
                pos_shift = c_(nx * 15.0 + t * 0.3) * 0.01
                
                x_pix = (nx + pos_shift) * bw
                
                # 음영 띠 (양옆이 어둡고 가운데가 밝은 입체감)
                pleat = QLinearGradient(x_pix - width/2, 0, x_pix + width/2, 0)
                dark_alpha = int(40 + w_mod * 90)
                light_alpha = int(5 + w_mod * 15)
                pleat.setColorAt(0.0, QColor(0, 0, 0, dark_alpha))
                pleat.setColorAt(0.5, QColor(255, 255, 255, light_alpha))
                pleat.setColorAt(1.0, QColor(0, 0, 0, dark_alpha))
                
                p.fillRect(int(x_pix - width/2), 0, int(width), bh, QBrush(pleat))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # ═══ 3. 중앙 기둥 위/아래 색상 틴트 (상단 레드, 하단 옐로우/오렌지) ═══
            # 중앙 빔의 위쪽을 마젠타/레드로, 아래쪽을 골드/오렌지로 물들임
            p.setCompositionMode(QPainter.CompositionMode_Screen)
            blob("#FF0055", 0.5, 0.1, 0.4, 50)  # 상단 레드 글로우 (더욱 차분하게)
            blob("#DDAA00", 0.5, 0.9, 0.4, 25)  # 하단 옐로우 글로우 (더욱 차분하게)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # ═══ 4. 비네트 및 우측 다크 영역 강조 ═══
            vg = QRadialGradient(bw * 0.5, bh * 0.5, max(bw, bh) * 0.8)
            vg.setColorAt(0.0, QColor(0, 0, 0, 0))
            vg.setColorAt(1.0, QColor(0, 2, 8, 220))
            p.fillRect(0, 0, bw, bh, QBrush(vg))
            
            # 우측 심연(Abyss) 덮기
            right_shadow = QLinearGradient(bw * 0.65, 0, bw, 0)
            right_shadow.setColorAt(0.0, QColor(0, 0, 0, 0))
            right_shadow.setColorAt(1.0, QColor(0, 1, 5, 240))
            p.fillRect(int(bw*0.65), 0, int(bw*0.35), bh, QBrush(right_shadow))

        else:
            # ═══ 1. 매크로 배경 컬러 (세로 기둥형 오로라 베이스 - 라이트 모드) ═══
            # 왼쪽은 화사한 시안, 중앙은 코랄/피치 빛줄기, 오른쪽은 부드러운 블루그레이/화이트
            lg = QLinearGradient(0, 0, bw, 0)
            lg.setColorAt(0.0, QColor("#B0E5EB")) # 밝은 시안
            lg.setColorAt(0.3, QColor("#89C9D9")) # 부드러운 틸(Teal)
            lg.setColorAt(0.42, QColor("#EBA1B8")) # 코랄 핑크 시작
            lg.setColorAt(0.50, QColor("#F0B888")) # 피치 오렌지 (핵심 빔)
            lg.setColorAt(0.55, QColor("#E89EA8")) # 핑크 페이드 아웃
            lg.setColorAt(0.70, QColor("#D1DAE8")) # 우측 소프트 블루그레이
            lg.setColorAt(1.0, QColor("#ECF0F7"))  # 우측 끝 거의 화이트
            p.fillRect(0, 0, bw, bh, QBrush(lg))

            # ═══ 2. 세로 띠 (Pleats / Striations) 오버레이 ═══
            p.setCompositionMode(QPainter.CompositionMode_Overlay)
            num_pillars = 40
            for i in range(num_pillars):
                nx = i / num_pillars
                # 주름 너비와 밝기 불규칙성
                w_mod = s(nx * 25.0 + t * 0.5) * 0.5 + 0.5
                width = (0.015 + w_mod * 0.02) * bw
                pos_shift = c_(nx * 15.0 + t * 0.3) * 0.01
                
                x_pix = (nx + pos_shift) * bw
                
                # 음영 띠 (라이트 모드는 그림자를 옅게, 하이라이트를 화사하게)
                pleat = QLinearGradient(x_pix - width/2, 0, x_pix + width/2, 0)
                dark_alpha = int(10 + w_mod * 20)
                light_alpha = int(30 + w_mod * 50)
                pleat.setColorAt(0.0, QColor(0, 0, 0, dark_alpha))
                pleat.setColorAt(0.5, QColor(255, 255, 255, light_alpha))
                pleat.setColorAt(1.0, QColor(0, 0, 0, dark_alpha))
                
                p.fillRect(int(x_pix - width/2), 0, int(width), bh, QBrush(pleat))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # ═══ 3. 중앙 기둥 위/아래 색상 틴트 ═══
            # 화사한 배경이므로 투명도를 주어 부드럽게 색을 더함
            blob("#FF3377", 0.5, 0.1, 0.4, 40)  # 상단 핑크/레드 글로우
            blob("#FF9900", 0.5, 0.9, 0.4, 30)  # 하단 옐로우/오렌지 글로우

            # ═══ 4. 비네트 및 우측 그늘(Shade) 강조 ═══
            vg = QRadialGradient(bw * 0.5, bh * 0.5, max(bw, bh) * 0.8)
            vg.setColorAt(0.0, QColor(0, 0, 0, 0))
            vg.setColorAt(1.0, QColor(10, 20, 50, 40)) # 아주 옅은 외곽 그림자
            p.fillRect(0, 0, bw, bh, QBrush(vg))
            
            # 우측의 부드러운 그늘
            right_shadow = QLinearGradient(bw * 0.65, 0, bw, 0)
            right_shadow.setColorAt(0.0, QColor(0, 0, 0, 0))
            right_shadow.setColorAt(1.0, QColor(20, 30, 60, 50))
            p.fillRect(int(bw*0.65), 0, int(bw*0.35), bh, QBrush(right_shadow))

        p.end()
        return img

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self.rect(), self._bg_image())
        self._draw_card_shadows(p)

    def _draw_card_shadows(self, p: QPainter):
        """글래스 카드 뒤 가벼운 소프트 섀도우 — 우하단으로 살짝 오프셋."""
        dark = current_mode() == "dark"
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        sc = (0, 0, 0) if dark else (60, 70, 120)
        for c in self._glass_cards:
            try:
                if not c.isVisible():
                    continue
                rv = getattr(c, "_reveal", 1.0)
                if rv <= 0.05:
                    continue
                pos = c.mapTo(self, QPoint(0, 0))
                cw, ch = c.width(), c.height()
            except RuntimeError:
                continue
            peak = (8 if dark else 5) * rv
            layers = 8
            for i in range(layers):
                t = i / (layers - 1)
                a = int(peak * (1 - t) ** 1.5)
                if a <= 0:
                    continue
                p.setBrush(QColor(sc[0], sc[1], sc[2], a))
                grow = i * 2.8
                off = 4 + i * 1.2      # 우하단으로 살짝 오프셋
                rad = GlassCard.RADIUS + grow * 0.5
                p.drawRoundedRect(
                    QRectF(pos.x() - grow * 0.3 + off * 0.5,
                           pos.y() - grow * 0.3 + off * 0.7,
                           cw + grow * 0.6, ch + grow * 0.6), rad, rad)



    # ── 공개 API ─────────────────────────────────
    def show_result(self, result: dict, log_entries=None, corrections=None,
                    char_count=None, page_count=None, file_name: str = ""):
        fresh = result is not self._result       # 정오표 재생성 시 같은 dict → 시각 유지
        self._result = result or {}
        self._log = log_entries or []
        if corrections is not None:
            self._corrections = list(corrections)
        if char_count is not None:
            self._char_count = char_count
        if page_count is not None:
            self._page_count = page_count
        if file_name:
            self._file_name = file_name
        if fresh:
            self._completed_at = self._now_str()
        self._animate = True
        self._render()

    def refresh_theme(self):
        self._bg_cache = None
        self._mesh_cache = None
        self._animate = False
        self._render()

    # ── 렌더 ─────────────────────────────────────
    def _render(self):
        self._clear(self._col)
        self._anims = []      # (위젯, 지연 ms) — start(delay=…)를 가진 위젯
        self._glass_cards = []

        r = self._result
        applied = r.get("applied", 0)
        occ = r.get("occurrences", 0)
        failed = r.get("failed", 0)
        consumed = r.get("consumed", 0)
        flagged = r.get("flagged", 0)
        review_mode = flagged > 0 and applied == 0

        cors = self._corrections or []
        n_ai = sum(1 for c in cors if str(c.get("source", "")).startswith("ai_"))
        n_rule = len(cors) - n_ai
        n_prop = len(cors) if cors else (flagged if review_mode
                                         else applied + failed + consumed)
        rate = (applied / n_prop * 100.0) if (n_prop and not review_mode) else 0.0

        self._col.addWidget(self._build_header(review_mode, flagged))
        self._col.addWidget(self._build_pipeline(
            review_mode, applied, occ, failed, consumed, flagged,
            n_prop, n_rule, n_ai, rate))
        self._col.addWidget(self._build_charts(
            review_mode, applied, flagged, n_prop, n_rule, n_ai, rate, cors))

        bottom = QHBoxLayout()
        bottom.setSpacing(22)
        log = self._build_log()
        fails = self._build_fails()
        if log is not None:
            riser = _Riser(log)
            self._anims.append((riser, self._T_BOT1))
            bottom.addWidget(riser, 3)
        if fails is not None:
            riser = _Riser(fails)
            self._anims.append((riser, self._T_BOT2))
            bottom.addWidget(riser, 2)
        if log is not None or fails is not None:
            wrap = _twidget()
            wrap.setLayout(bottom)
            self._col.addWidget(wrap)

        # 스크롤 영역이 뷰포트를 채우려고(setWidgetResizable) holder를 실제 내용보다
        # 키울 때, 그 여분 세로 공간이 여기 하나로만 몰리게 한다 — 없으면 각 행/카드가
        # 나눠 늘어나며(내부 addStretch가 흡수) 창을 최대화할수록 카드가 밑으로 계속
        # 자라 보인다(사용자 보고).
        self._col.addStretch(1)

        if self._animate:
            # 등장 지연도 _MOTION배(모든 delay가 여기 한 곳을 통과 — 스케줄/카드/숫자·
            #   차트 애니 전부). setDuration의 _MOTION 스케일과 합쳐 전체가 2배 느려진다.
            for widget, delay in self._anims:
                widget.start(int(delay * _MOTION))
        self._animate = False

    def _num(self, value, *, decimals=0, px=38, color=None, duration=1500,
             delay=0) -> AnimatedNumberLabel:
        """AnimatedNumberLabel 생성 + 애니메이션 예약(현재 렌더가 animate일 때만)."""
        n = AnimatedNumberLabel(value, decimals=decimals, px=px,
                                color=color, duration=duration)
        self._anims.append((n, delay))
        return n

    def _glass(self, tint: str = "default", shine_delay: int = 0) -> GlassCard:
        """GlassCard 등록 — 메시/그림자 갱신 대상 + shine sweep 예약."""
        g = GlassCard(tint)
        self._glass_cards.append(g)
        self._anims.append((g, shine_delay))
        return g

    def _rise(self, widget: QWidget, delay: int) -> _Riser:
        """등장 래퍼 등록 — 카드가 한 장씩 천천히 떠오른다."""
        riser = _Riser(widget)
        self._anims.append((riser, delay))
        return riser

    # ── 헤더 ─────────────────────────────────────
    def _build_header(self, review_mode, flagged) -> QWidget:
        pal = current_palette()
        w = _twidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(4, 6, 4, 0)
        row.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(8)
        name = _FocusInLabel(self._file_name or "문서", px=27, weight=800,
                             color_key="text", start_ls=13.0, duration=760, max_w=640)
        self._anims.append((name, 0))
        left.addWidget(name)

        # 절약 시간 = 페이지 수 기반 수작업 예상(1인 8시간=100쪽) − 로그 시작~종료
        # 실제 소요(occ*30초 같은 어림 가정 아님 — 실측 로그 기반).
        saved_hm = self._saved_time_hm()
        if saved_hm:
            sh, sm = saved_hm
            if sh and sm:
                saved_txt = f"약 {sh}시간 {sm}분"
            elif sh:
                saved_txt = f"약 {sh}시간"
            elif sm:
                saved_txt = f"약 {sm}분"
            else:
                saved_txt = ""
        else:
            saved_txt = ""
        saved = f" · 수작업 대비 {saved_txt} 절약" if saved_txt else ""
        if review_mode:
            detail = f"검수 완료 · 정오표 {flagged}건 기록 (HWP 미수정)"
        else:
            detail = f"완료"
        ts = QLabel(f'<b style="color:{pal["text"]};">{html.escape(self._completed_at)}</b>'
                    f'<span style="color:{pal["text_sub"]};"> {html.escape(detail)}</span>')
        ts.setStyleSheet("font-size:14px; background:transparent; border:none;")
        ts_riser = _Riser(ts, dist=10, duration=520)
        self._anims.append((ts_riser, 180))
        left.addWidget(ts_riser)
        row.addLayout(left, 1)

        # 헤더 우측 빈 영역 — 절약 시간을 레트로 디지털시계로 강조(사용자 요구).
        if saved_hm:
            clock = _SavedTimeClock(*saved_hm)
            clock_riser = _Riser(clock, dist=10, duration=560)
            self._anims.append((clock_riser, 260))
            row.addWidget(clock_riser, 0, Qt.AlignVCenter)
        return w

    # ── 파이프라인 히어로 ─────────────────────────
    def _stage_card(self, num: str, title: str, sub: str, *,
                    tint: str = "default", shine_delay: int = 0):
        f = self._glass(tint, shine_delay)
        f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(f)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(5)
        # ⚠ text_dim은 불투명 표면 전제 토큰이라 반투명 글래스 카드(책 배경이
        #   비치는 가변 명도) 위에서는 거의 사라진다(사용자 보고) — 부제와 같은
        #   text_sub로 올려 카드 어디서나 읽히게 한다.
        num_lbl = _ctext(num, px=13, color="text_sub", weight=700, ls=1.5)
        v.addWidget(num_lbl)
        v.addWidget(_ctext(title, px=20, color="text", weight=800))
        v.addWidget(_ctext(sub, px=15, color="text_sub", weight=500))
        v.addSpacing(16)
        return f, v

    def _unit_row(self, num_widget, unit: str, *, unit_px: int = 17) -> QHBoxLayout:
        """숫자(큰 볼드체) + 단위 텍스트 한 줄. 두 폰트 크기가 크게 달라
        descent(베이스라인 아래 여백)도 달라진다 — 단순 AlignBottom은 각 위젯의
        '바닥'만 맞출 뿐 글자 베이스라인은 맞추지 못해 시각적으로 어긋난다.
        두 폰트의 descent 차이를 계산해 단위 라벨에 그만큼 아래 여백을 더하면
        두 베이스라인이 픽셀 단위로 일치한다."""
        h = QHBoxLayout()
        h.setSpacing(5)
        h.addWidget(num_widget)

        big_font = QFont(num_widget.font())
        big_font.setPixelSize(num_widget._px)
        bw = num_widget._weight
        big_font.setWeight(QFont.Weight(bw) if bw <= 1000 else QFont.Bold)
        small_font = QFont(num_widget.font())
        small_font.setPixelSize(unit_px)
        small_font.setWeight(QFont.Weight(600))
        diff = max(0, QFontMetrics(big_font).descent() - QFontMetrics(small_font).descent())

        u = _ctext(unit, px=unit_px, color="text_sub", weight=600)
        u.setFixedHeight(QFontMetrics(small_font).height())
        u.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        u_wrap = _twidget()
        uv = QVBoxLayout(u_wrap)
        uv.setContentsMargins(0, 0, 0, diff)
        uv.setSpacing(0)
        uv.addWidget(u)
        h.addWidget(u_wrap, 0, Qt.AlignBottom)
        h.addStretch()
        return h

    def _card_meta(self, v_layout, text: str):
        """카드 하단 메타 설명 — 구분선 아래 표기(3카드 공통 패턴)."""
        v_layout.addSpacing(10)
        v_layout.addWidget(_hline())
        v_layout.addSpacing(8)
        v_layout.addWidget(_ctext(text, px=15, color="text_sub", weight=500, wrap=True))

    def _connector(self, delay: int) -> QWidget:
        """진행 표시(미니멀 쉐브런) — 자체 페이드인(start)이 등장 효과라 _rise로
        감싸지 않고 스케줄에 직접 등록한다."""
        c = _MinimalChevron()
        self._anims.append((c, delay))
        return c

    def _build_pipeline(self, review_mode, applied, occ, failed, consumed,
                        flagged, n_prop, n_rule, n_ai, rate) -> QWidget:
        pal = current_palette()
        w = _twidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        # 01 분석 — 페이지 수가 대표 수치(교정 현장 단위: 하루 80~100쪽),
        #   추출 글자 수는 서브로. 페이지 수를 못 구하면(hwpx direct 등) 글자 수 폴백.
        f1, v1 = self._stage_card("01", "파일 분석", "문서 텍스트 추출",
                                  shine_delay=self._T_CARD1 + 520)
        if self._page_count:
            v1.addLayout(self._unit_row(
                self._num(self._page_count, px=58, delay=self._T_CARD1 + 330,
                          duration=1500), "페이지", unit_px=18))
            if self._char_count is not None:
                self._card_meta(v1, f"추출 글자 : {self._char_count:,}자")
        elif self._char_count is not None:
            v1.addLayout(self._unit_row(
                self._num(self._char_count, px=48, delay=self._T_CARD1 + 330,
                          duration=1500), "자"))
        else:
            dash = _ctext("—", px=44, color="text", weight=800)
            v1.addWidget(dash)
        v1.addStretch()
        row.addWidget(self._rise(f1, self._T_CARD1), 1)
        row.addWidget(self._connector(self._T_CARD2))

        # 02 교정 제안 — 총 건수 히어로(01·03과 동일 형식) + 소스 분해는 메타로.
        f2, v2 = self._stage_card("02", "교정 제안", "표준국어대사전 + AI 교정",
                                  shine_delay=self._T_CARD2 + 520)
        v2.addLayout(self._unit_row(
            self._num(n_prop, px=58, delay=self._T_CARD2 + 330,
                      duration=1500), "건", unit_px=18))
        if self._corrections is not None:
            self._card_meta(v2, f"사전 : {n_rule:,}건  |  AI : {n_ai:,}건")
        v2.addStretch()
        row.addWidget(self._rise(f2, self._T_CARD2), 1)
        row.addWidget(self._connector(self._T_CARD3))

        # 03 적용(또는 검수) — 히어로 숫자(강조색 + accent/시안 글로우 글래스)
        if review_mode:
            f3, v3 = self._stage_card("03", "검수", "정오표 기록 · HWP 미수정",
                                      tint="accent", shine_delay=self._T_CARD3 + 560)
            v3.addLayout(self._unit_row(
                self._num(flagged, px=58, color=pal["accent"],
                          delay=self._T_CARD3 + 380, duration=1700), "건", unit_px=18))
        else:
            f3, v3 = self._stage_card("03", "적용", "최종 교정 반영",
                                      tint="accent", shine_delay=self._T_CARD3 + 560)
            v3.addLayout(self._unit_row(
                self._num(applied, px=58, color=_success_color(),
                          delay=self._T_CARD3 + 380, duration=1700), "건", unit_px=18))
            meta = f"{occ:,}항목 치환 성공"
            extra = []
            if failed:
                extra.append(f"실패 : {failed}")
            if consumed:
                extra.append(f"제외 : {consumed}")
            if extra:
                meta += "  |  " + "  |  ".join(extra)
            self._card_meta(v3, meta)
        v3.addStretch()
        row.addWidget(self._rise(f3, self._T_CARD3), 1)
        return w

    def _section(self, title: str, icon: str = "", *, icon_role: str = "text_sub",
                 shine_delay: int = 0, hero_title: bool = False):
        """글래스 섹션 카드 등록 헬퍼. 반환: (frame, body_layout).
        hero_title=True면 아이콘 없이 파이프라인 히어로 카드(_stage_card)와
        동일한 제목 스타일(px=20, weight=800)을 쓴다(차트 카드 3장 전용)."""
        frame = self._glass("default", shine_delay)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(13)
        hdr = QHBoxLayout()
        hdr.setSpacing(9)
        if icon:
            hdr.addWidget(IconLabel(icon, role=icon_role, size=18))
        if hero_title:
            hdr.addWidget(_ctext(title, px=20, color="text", weight=800))
        else:
            hdr.addWidget(_ctext(title, px=16, color="text", weight=700))
        hdr.addStretch()
        lay.addLayout(hdr)
        return frame, lay

    # ── 상세 분석(차트) ───────────────────────────
    def _build_charts(self, review_mode, applied, flagged, n_prop,
                      n_rule, n_ai, rate, cors) -> QWidget:
        w = _twidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)

        # 파이프라인 히어로 행과 동일한 리듬(카드=stretch 1 · 사이에 미니멀 쉐브런,
        # spacing도 6으로 맞춤) — 카드가 조건부(cors 없으면 바/도넛 생략)라 먼저
        # 목록에 쌓은 뒤 실제 존재하는 카드끼리만 커넥터로 잇는다.
        row = QHBoxLayout()
        row.setSpacing(6)
        entries = []   # (등장 래퍼 위젯, 등장 지연ms)
        # 도넛/게이지는 캔버스(232px 고정폭) 때문에 카드 최소폭이 232+56(좌우 패딩)로
        # 강제된다. "오류 유형" 카드(BarListChart, 최소폭 160)는 그보다 훨씬 작게
        # 줄어들 수 있어서 — 창을 좁히면 이 카드만 먼저/더 작아져 3카드 비율이
        # 깨졌다(사용자 보고, 파이프라인 히어로 행은 셋 다 유연한 텍스트뿐이라 안 깨짐).
        # 3카드 모두 같은 최소폭을 강제해 항상 함께(비율 유지) 줄어들게 한다.
        _CHART_CARD_MIN_W = 232 + 56

        # 1) 오류 유형 바 (차트 카드도 한 장씩: T_CH1 → 2 → 3)
        if cors:
            from collections import Counter
            cnt = Counter()
            for c in cors:
                if c.get("source") == "dict_flag" or c.get("confidence") == "low":
                    # "검수 필요"는 오류 유형이라 보기 어려운 뭉뚱그린 버킷이라, 사유
                    # 문구로 실제 유형을 추정할 수 있으면 세분화한다(그 외엔 폴백 유지).
                    reason = c.get("reason", "") or ""
                    if "띄어쓰기" in reason:
                        cat = "띄어쓰기"
                    elif "어느 사전에도 없음" in reason:
                        cat = "사전 미등재"
                    else:
                        cat = "검수 필요"
                else:
                    cat = c.get("category") or _SOURCE_LABEL.get(c.get("source", "dict"), "교정")
                cnt[cat] += 1
            top = cnt.most_common(5)
            if top:
                fr3, l3 = self._section("오류 유형", shine_delay=self._T_CH1 + 560,
                                        hero_title=True)
                fr3.setMinimumWidth(_CHART_CARD_MIN_W)
                bars = BarListChart(top)
                self._anims.append((bars, self._T_CH1 + 350))
                l3.addWidget(bars)
                l3.addStretch()
                entries.append((self._rise(fr3, self._T_CH1), self._T_CH1))

        # 2) 제안 소스 도넛
        if cors:
            fr1, l1 = self._section("제안 소스", shine_delay=self._T_CH2 + 560,
                                    hero_title=True)
            fr1.setMinimumWidth(_CHART_CARD_MIN_W)
            # 범례 목록 대신 세그먼트 표면에서 뻗어나온 툴팁 버블로 라벨 표시(항상 표출,
            # 호버 불필요) — 버블이 밖으로 나갈 공간을 확보하려 캔버스를 링보다 넓힌다.
            dict_color = "#FF5500" if current_mode() == "dark" else "#D97706"
            ai_color = "#60A5FA" if current_mode() == "dark" else "#2563EB"
            donut = DonutChartWidget([(n_rule, dict_color), (n_ai, ai_color)],
                                     labels=[f"사전 : {n_rule:,}건", f"AI : {n_ai:,}건"],
                                     canvas_w=232, canvas_h=262)
            self._anims.append((donut, self._T_CH2 + 350))
            l1.addWidget(donut, 0, Qt.AlignHCenter)
            l1.addStretch()
            entries.append((self._rise(fr1, self._T_CH2), self._T_CH2))

        # 3) 적용률 게이지
        fr2, l2 = self._section("교정 적용률", shine_delay=self._T_CH3 + 560,
                                hero_title=True)
        fr2.setMinimumWidth(_CHART_CARD_MIN_W)
        # 외부 캡션 라벨 대신 제안 소스 도넛과 동일한 항상-표출 툴팁 버블로 대체.
        if review_mode:
            gauge = RadialGauge(1.0, caption="검수 완료", center_text="검수",
                                bubble_label=f"검수 {flagged:,}건", canvas_w=232, canvas_h=262)
        else:
            rejected = max(0, n_prop - applied)
            gauge = RadialGauge(rate / 100.0,
                                bubble_labels=(f"수락 : {applied:,}건", f"거절 : {rejected:,}건"),
                                fill_color=_success_color(), reject_color=_ACCEPT_COLOR,
                                canvas_w=232, canvas_h=262)
        self._anims.append((gauge, self._T_CH3 + 350))
        l2.addWidget(gauge, 0, Qt.AlignHCenter)
        l2.addStretch()
        entries.append((self._rise(fr2, self._T_CH3), self._T_CH3))

        for i, (widget, delay) in enumerate(entries):
            if i > 0:
                row.addWidget(self._connector(delay))
            row.addWidget(widget, 1)

        col.addLayout(row)
        return w

    # ── 실패 항목 ─────────────────────────────────
    def _build_fails(self):
        samples = self._result.get("fail_samples", [])
        if not samples:
            return None
        frame, lay = self._section("실패 항목", "triangle-alert", icon_role="warning",
                                   shine_delay=self._T_BOT2 + 560)
        pal = current_palette()
        dark = current_mode() == "dark"
        ins_bg = _rgba("#000000", 0.24) if dark else _rgba("#0F172A", 0.05)
        ins_bd = _rgba("#FFFFFF", 0.07) if dark else _rgba("#FFFFFF", 0.65)
        for s in samples:
            item = QFrame()
            # QLabel도 QFrame 서브클래스라 'QFrame' 선택자가 자식 라벨까지 물들인다
            # — objectName으로 이 프레임에만 스코프를 한정한다.
            item.setObjectName("failInset")
            item.setStyleSheet(
                f"QFrame#failInset {{ background:{ins_bg}; border:1px solid {ins_bd}; "
                "border-radius:12px; }}")
            il = QVBoxLayout(item)
            il.setContentsMargins(16, 12, 16, 12)
            il.setSpacing(4)
            orig = html.escape(s.get("original", ""))
            corr = html.escape(s.get("corrected", ""))
            line = QLabel(f'<span style="color:{pal["error"]};text-decoration:line-through;">{orig}</span>'
                          f'  →  <span style="color:{pal["success"]};font-weight:600;">{corr}</span>')
            line.setStyleSheet("font-size:14px; background:transparent; border:none;")
            line.setTextInteractionFlags(Qt.TextSelectableByMouse)
            line.setWordWrap(True)
            il.addWidget(line)
            il.addWidget(_ctext(f"사유: {s.get('error', '(상세 없음)')}", px=13,
                                color="text_muted", weight=500, wrap=True))
            lay.addWidget(item)
        lay.addStretch()
        return frame

    # ── 진행 로그(요약) ────────────────────────────
    def _build_log(self):
        # 활동 패널의 표시본을 **그대로** 쓴다(단일 출처 — 파일 상단 주석 참조).
        #   여기선 시간·본문 2열 고정 정렬만 담당한다.
        curated = self._log
        if not curated:
            return None
        frame, lay = self._section("교정 진행 요약", "clipboard-check",
                                   shine_delay=self._T_BOT1 + 560)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setMinimumHeight(230)
        pal = current_palette()
        dark = current_mode() == "dark"
        log_bg = _rgba("#000000", 0.24) if dark else _rgba("#0F172A", 0.04)
        log_bd = _rgba("#FFFFFF", 0.07) if dark else _rgba("#FFFFFF", 0.65)
        view.setStyleSheet(
            f"QTextEdit {{ background:{log_bg}; border:1px solid {log_bd}; "
            f"border-radius:12px; padding:12px 16px; font-size:13px; "
            f"color:{pal['text_sub']}; }}")

        # 시간(고정폭 monospace) | 내용 2열 테이블 — 시간·본문 정렬을 보장한다.
        #   레벨→색은 활동 패널과 **같은 표**를 쓴다(단일 출처). 'start'는 단계
        #   시작 마커 전용 색이라 여기 빠지면 완료만 초록이고 시작은 회색이 된다.
        from ui.widgets.activity_panel import _level_color
        rows = []
        for ts, lvl, msg in curated:
            color = _level_color(pal, lvl)
            weight = "600" if lvl in ("ok", "start", "err") else "400"
            safe = html.escape(msg)
            rows.append(
                f'<tr>'
                f'<td style="color:{pal["text_muted"]};'
                f'font-size:12px;white-space:nowrap;padding:3px 16px 3px 0;'
                f'vertical-align:top;">{ts}</td>'
                f'<td style="color:{color};font-size:13px;font-weight:{weight};'
                f'padding:3px 0;line-height:1.5;">{safe}</td>'
                f'</tr>')
        view.setHtml(
            '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
            + "".join(rows) + "</table>")
        lay.addWidget(view)
        return frame

    def _saved_time_hm(self):
        """수작업 대비 절약 시간(시, 분) — 페이지 수 기반 수작업 예상 시간(1인 8시간
        =100쪽 기준)에서 로그 시작~종료 실제 소요 시간을 뺀 값. 페이지 수/로그가
        없으면 계산 불가라 None(헤더 시계 위젯은 이때 숨김)."""
        if not self._page_count or not self._log:
            return None
        try:
            start = datetime.datetime.strptime(self._log[0][0], "%H:%M:%S")
            end = datetime.datetime.strptime(self._log[-1][0], "%H:%M:%S")
        except (ValueError, IndexError, TypeError):
            return None
        actual_sec = (end - start).total_seconds()
        if actual_sec < 0:
            actual_sec += 86400          # 자정을 넘긴 경우 보정
        manual_sec = self._page_count / 100.0 * 8 * 3600
        saved_sec = max(0, manual_sec - actual_sec)
        return int(saved_sec // 3600), int((saved_sec % 3600) // 60)

    # ── 유틸 ─────────────────────────────────────
    @staticmethod
    def _now_str() -> str:
        now = datetime.datetime.now()
        ampm = "오전" if now.hour < 12 else "오후"
        h12 = now.hour % 12 or 12
        return f"{now.year}년 {now.month}월 {now.day}일 {ampm} {h12}:{now.minute:02d}"

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() 먼저 — setParent(None)이 만든 임시 최상위 창이 직후의
                #   무거운 재렌더 프리즈 동안 빈 유령 창으로 화면에 남는 것 방지.
                w.hide()
                w.setParent(None)
                w.deleteLater()
