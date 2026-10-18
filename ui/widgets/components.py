"""
ui/widgets/components.py — 재사용 UI 프리미티브 팩토리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
화면마다 복붙되던 버튼/카드/뱃지/칩/구분선 QSS를 일원화한다.
정적 스타일은 theme.py 글로벌 QSS의 objectName/property 선택자에 의존하고,
아이콘은 icons.py(SVG)로 렌더해 테마 색상에 맞춰 refresh_theme()로 갱신한다.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPushButton, QLabel, QFrame, QVBoxLayout, QHBoxLayout, QWidget,
)
from PySide6.QtGui import QIcon

from ui.styles.icons import make_icon, pixmap, color_for, icon_size


# ══════════════════════════════════════════════════════════════
# 아이콘 위젯 (테마 색상 자동 갱신)
# ══════════════════════════════════════════════════════════════

class IconButton(QPushButton):
    """SVG 아이콘 버튼. refresh_theme()로 팔레트 색상 재적용."""

    def __init__(self, name: str, *, text: str = "", variant: str = "icon",
                 role: str = "text_sub", size: int = 18, tooltip: str = "",
                 on_click=None, parent=None):
        super().__init__(text, parent)
        self._icon_name = name
        self._icon_role = role
        self._icon_size = size
        self.setProperty("variant", variant)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        if on_click is not None:
            self.clicked.connect(on_click)
        self.refresh_theme()

    def set_icon_name(self, name: str):
        self._icon_name = name
        self.refresh_theme()

    def set_icon_role(self, role: str):
        self._icon_role = role
        self.refresh_theme()

    def refresh_theme(self):
        if self._icon_name:
            self.setIcon(make_icon(self._icon_name, color_for(self._icon_role), self._icon_size))
            self.setIconSize(icon_size(self._icon_size))
        else:
            self.setIcon(QIcon())


class IconLabel(QLabel):
    """SVG 아이콘 라벨. refresh_theme()로 팔레트 색상 재적용."""

    def __init__(self, name: str, *, role: str = "text_sub", size: int = 18,
                 stroke_width: float = 0, parent=None):
        super().__init__(parent)
        self._icon_name = name
        self._icon_role = role
        self._icon_size = size
        self._stroke_width = stroke_width
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.refresh_theme()

    def set_icon_name(self, name: str):
        self._icon_name = name
        self.refresh_theme()

    def set_icon_role(self, role: str):
        self._icon_role = role
        self.refresh_theme()

    def refresh_theme(self):
        self.setPixmap(pixmap(self._icon_name, color_for(self._icon_role),
                              self._icon_size, self._stroke_width))


# ══════════════════════════════════════════════════════════════
# 버튼
# ══════════════════════════════════════════════════════════════

def make_button(text: str = "", variant: str = "ghost", on_click=None,
                tooltip: str = "", icon: str = "", icon_role: str = None,
                icon_size_px: int = 16) -> QPushButton:
    """variant: primary | ghost | success | danger | icon | link
    icon: SVG 이름(선택). icon_role 미지정 시 variant로 색 추론."""
    if icon:
        role = icon_role or _icon_role_for_variant(variant)
        btn = IconButton(icon, text=("  " + text if text else ""),
                         variant=variant, role=role, size=icon_size_px,
                         tooltip=tooltip, on_click=on_click)
        return btn
    btn = QPushButton(text)
    btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def _icon_role_for_variant(variant: str) -> str:
    return {
        "primary": "accent_fg",
        "success": "success",
        "danger":  "error",
        "link":    "accent",
    }.get(variant, "text_sub")


def icon_button(name: str, tooltip: str = "", on_click=None, size: int = 18,
                role: str = "text_sub") -> IconButton:
    return IconButton(name, tooltip=tooltip, on_click=on_click, size=size, role=role)


# ══════════════════════════════════════════════════════════════
# 라벨
# ══════════════════════════════════════════════════════════════

def label(text: str, role: str = "", muted: bool = False,
          tone: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if muted:
        lbl.setProperty("muted", "true")
    if tone:
        lbl.setProperty("tone", tone)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


_ZWSP = "​"   # ZERO WIDTH SPACE


def soft_breakable(s: str) -> str:
    """표시 전용 라벨 텍스트에 ZWSP(폭 0 공백)를 끼워 넣어 어디서든 줄바꿈 가능하게.

    '나타난다.정보통신기획평가원(2025a)·…' 같은 무공백 장문이나 긴 파일명은 Qt의
    줄바꿈 단위가 수백 px가 돼, 줄바꿈을 켜도 접히지 않고 칸의 최소 폭을 밀어 올린다
    (실측 339px). 영문·숫자 연속 구간(2025a, OECD, 260730 등) 안에는 넣지 않아
    토큰 중간이 끊기지 않는다.
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


def title_label(text: str) -> QLabel:
    return label(text, role="title")


def sub_label(text: str, wrap: bool = False) -> QLabel:
    return label(text, role="sub", wrap=wrap)


# ══════════════════════════════════════════════════════════════
# 뱃지 / 칩
# ══════════════════════════════════════════════════════════════

def badge(text: str, tone: str = "") -> QLabel:
    return label(text, role="badge", tone=tone)


def chip(text: str, tone: str = "") -> QLabel:
    return label(text, role="chip", tone=tone)


# ══════════════════════════════════════════════════════════════
# 컨테이너
# ══════════════════════════════════════════════════════════════

def card(role: str = "card") -> QFrame:
    frame = QFrame()
    frame.setProperty("role", role)
    return frame


def section_card(title: str = "", icon: str = "", role: str = "section",
                 icon_role: str = "text_sub"):
    """제목/SVG 아이콘 헤더가 있는 섹션 카드.
    반환: (frame, body_layout)"""
    frame = card(role)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(27, 21, 27, 21)
    lay.setSpacing(12)

    if title:
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        if icon:
            hdr.addWidget(IconLabel(icon, role=icon_role, size=16))
        hdr.addWidget(title_label(title))
        hdr.addStretch()
        lay.addLayout(hdr)

    return frame, lay


def divider(horizontal: bool = True) -> QFrame:
    d = QFrame()
    d.setProperty("role", "divider")
    if horizontal:
        d.setFixedHeight(1)
    else:
        d.setFixedWidth(1)
    return d

# ══════════════════════════════════════════════════════════════
# 진행 화면 카드 — 회전 그라디언트 테두리 + 링 배경 + 글자 파티클
# ══════════════════════════════════════════════════════════════

from PySide6.QtCore import QTimer
import time

# ── 떠다니며 연결되는 글자 파티클(뉴런 스캔에서 이식) ──────────────
import math as _math
import random as _random

# 교정/교열 분위기의 한글 글자 풀(장식용 — 의미보다 분위기).
_PARTICLE_CHARS = list(
    '가나다라마바사아자차카타파하경성문화사편집본부김대경부본부장'
    'ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣ')

# 티어별 색(다크=네온, 라이트=흰 배경에서도 보이는 진한 채도). 인덱스=파티클 tier.
_PARTICLE_RGB_DARK = [(0, 232, 200), (139, 116, 255), (255, 45, 160)]
_PARTICLE_RGB_LIGHT = [(13, 124, 112), (74, 84, 188), (176, 24, 120)]


class _GlyphParticle:
    """카드 안을 천천히 떠다니는 글자 한 개. 수명에 따라 페이드 인/아웃 후 재생성."""
    __slots__ = ("x", "y", "vx", "vy", "ch", "sz", "max_a", "life", "max_life",
                 "a", "tier")

    def __init__(self, rect):
        self.respawn(rect, seeded=True)

    def respawn(self, rect, seeded=False):
        x0, y0, w, h = rect
        self.x = x0 + _random.random() * w
        self.y = y0 + _random.random() * h
        ang = _random.random() * _math.pi * 2
        spd = _random.random() * 0.42 + 0.06
        self.vx = _math.cos(ang) * spd
        self.vy = _math.sin(ang) * spd
        self.ch = _random.choice(_PARTICLE_CHARS)
        self.sz = _random.random() * 9 + 8          # 8~17px
        self.max_a = _random.random() * 0.27 + 0.07  # 0.07~0.34
        self.life = _random.randint(0, 600) if seeded else 0
        self.max_life = _random.random() * 500 + 220
        self.a = 0.0
        r = _random.random()
        self.tier = 0 if r < 0.55 else (1 if r < 0.82 else 2)

    def update(self, rect):
        x0, y0, w, h = rect
        self.x += self.vx
        self.y += self.vy
        self.life += 1
        t = self.life / self.max_life
        if t < 0.12:
            self.a = (t / 0.12) * self.max_a
        elif t > 0.82:
            self.a = ((1 - t) / 0.18) * self.max_a
        else:
            self.a = self.max_a
        m = 40
        if (self.life >= self.max_life or self.x < x0 - m or self.x > x0 + w + m
                or self.y < y0 - m or self.y > y0 + h + m):
            self.respawn(rect, seeded=False)


class AnimatedGradientBorder(QWidget):
    def __init__(self, inner_widget: QWidget, border_width: int = 3, radius: int = 14,
                 parent=None, particles: bool = False, center_disc: int = 0):
        super().__init__(parent)
        self.inner_widget = inner_widget
        self.border_width = border_width
        self.radius = radius
        self.angle = 0
        self.phase = 0.0
        self._is_animating = False
        self._start_time = time.time()
        # 카드 배경에 떠다니며 연결되는 글자 파티클(opt-in — 내부 위젯이 투명할 때만 보임).
        self._particles_enabled = particles
        self._particles = None
        # 링 파티클 리플 배경 (오로라 대체 — ui/widgets/ring_particles.py)
        self._ring_bg = None
        # 중앙 불투명 원판 반경(px, 0=off) — 중앙 콘텐츠(책+%+제목) 뒤에서
        # 배경 리본/글자 파티클을 가려 가독성을 확보한다(opt-in).
        self._center_disc = center_disc
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(border_width, border_width, border_width, border_width)
        layout.addWidget(inner_widget)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)

    def set_animating(self, animating: bool):
        if self._is_animating == animating:
            return
            
        self._is_animating = animating
        if animating:
            # 다시 시작할 때 기존 phase 위치에서 부드럽게 이어서 시작 (점프 방지)
            self._start_time = time.time() - self.phase
            self.timer.start(16) # ~60 fps
        else:
            self.timer.stop()
            self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPainterPath, QConicalGradient, QColor
        from PySide6.QtCore import QRectF, Qt
        
        if self._is_animating:
            elapsed = time.time() - self._start_time
            self.angle = (elapsed * 180) % 360  # 2초에 한바퀴
            self.phase = elapsed
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 테두리를 포함한 전체 영역에 회전하는 원뿔형 그라데이션 그리기
        grad = QConicalGradient(self.rect().center(), self.angle)
        grad.setColorAt(0.0, QColor("#3b82f6")) # Blue
        grad.setColorAt(0.25, QColor("#ec4899")) # Pink
        grad.setColorAt(0.5, QColor("#f97316")) # Orange
        grad.setColorAt(0.75, QColor("#eab308")) # Yellow
        grad.setColorAt(1.0, QColor("#3b82f6")) # Blue
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(self.rect(), self.radius, self.radius)
        
        # 2. 내부 영역(inner_rect) 계산
        inner_rect = QRectF(self.rect()).adjusted(self.border_width, self.border_width, -self.border_width, -self.border_width)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner_rect, self.radius - self.border_width, self.radius - self.border_width)
        
        # 3. 내부 영역 클리핑 및 배경색 채우기
        painter.setClipPath(inner_path)
        from ui.styles.theme import current_mode
        is_dark = (current_mode() == "dark")
        
        if is_dark:
            painter.fillRect(inner_rect, QColor("#1a1d23"))
        else:
            painter.fillRect(inner_rect, QColor("#ffffff"))

        # 4. 링 파티클 리플 배경(ui/widgets/ring_particles.py — 옛 오로라 배경 대체).
        #    멈추면 마지막 위상에서 정지한다.
        if self._ring_bg is None:
            from ui.widgets.ring_particles import RingParticles
            self._ring_bg = RingParticles()
        self._ring_bg.draw(painter, inner_rect, self.phase, is_dark)

        # 5. 떠다니며 연결되는 글자 파티클 (inner_path 클립 안에 그려 둥근 모서리 유지)
        if self._particles_enabled:
            self._draw_particles(painter, inner_rect, is_dark)

        # 6. 중앙 불투명 원판(opt-in) — 리본 배경/글자 파티클을 중앙에서 가림.
        #    카드 배경색 단색 하드 엣지.
        if self._center_disc > 0:
            r = float(self._center_disc)
            bg = QColor("#1a1d23") if is_dark else QColor("#ffffff")
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawEllipse(inner_rect.center(), r, r)

    def _draw_particles(self, painter, inner_rect, is_dark):
        """카드 안을 떠다니는 글자 + 가까운 글자끼리 잇는 가는 선.

        painter는 이미 inner_path(둥근 사각형)로 클립돼 있어 모서리를 넘지 않는다.
        위치 갱신은 애니메이션 중일 때만 — 멈추면 마지막 프레임에서 정지(링 배경과 동일).
        """
        from PySide6.QtGui import QColor, QFont, QPen
        from PySide6.QtCore import QPointF

        rect = (inner_rect.x(), inner_rect.y(), inner_rect.width(), inner_rect.height())
        if self._particles is None:
            n = int(max(16, min(40, (rect[2] * rect[3]) / 11000)))
            self._particles = [_GlyphParticle(rect) for _ in range(n)]
        pts = self._particles

        if self._is_animating:
            for p in pts:
                p.update(rect)

        rgb = _PARTICLE_RGB_DARK if is_dark else _PARTICLE_RGB_LIGHT
        # 라이트(흰 배경)는 글자가 묻혀 잘 안 보이므로 진한 색 + 불투명도 상향.
        a_scale = 1.0 if is_dark else 1.9

        # ── 연결선: 가까운 글자끼리(거리가 가까울수록 진하게) ──
        max_d = max(90.0, min(inner_rect.width(), inner_rect.height()) * 0.42)
        max_d2 = max_d * max_d
        line_factor = 0.5 if is_dark else 0.85
        lr, lg, lb = rgb[0]
        pen = QPen()
        pen.setWidthF(0.7)
        for i in range(len(pts)):
            pi = pts[i]
            if pi.a <= 0.004:
                continue
            for j in range(i + 1, len(pts)):
                pj = pts[j]
                dx = pi.x - pj.x
                dy = pi.y - pj.y
                d2 = dx * dx + dy * dy
                if d2 >= max_d2:
                    continue
                a = (1 - _math.sqrt(d2) / max_d) * min(pi.a, pj.a) * line_factor
                if a <= 0.004:
                    continue
                col = QColor(lr, lg, lb)
                col.setAlphaF(min(1.0, a))
                pen.setColor(col)
                painter.setPen(pen)
                painter.drawLine(QPointF(pi.x, pi.y), QPointF(pj.x, pj.y))

        # ── 글자 ──
        font = QFont(painter.font())
        for p in pts:
            if p.a <= 0.004:
                continue
            cr, cg, cb = rgb[p.tier]
            col = QColor(cr, cg, cb)
            col.setAlphaF(min(1.0, p.a * a_scale))
            painter.setPen(col)
            font.setPixelSize(max(8, int(p.sz)))
            painter.setFont(font)
            painter.drawText(QPointF(p.x, p.y), p.ch)

# ══════════════════════════════════════════════════════════════
# 페이드 화면 전환 컨테이너 (스무스 뷰 트랜지션)
# ══════════════════════════════════════════════════════════════

from PySide6.QtWidgets import QStackedWidget, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve

class FadingStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fade_anim = None

    def setCurrentIndex(self, index: int):
        if self.currentIndex() == index:
            return
            
        super().setCurrentIndex(index)
        
        new_widget = self.currentWidget()
        if new_widget:
            effect = QGraphicsOpacityEffect(new_widget)
            new_widget.setGraphicsEffect(effect)
            
            self._fade_anim = QPropertyAnimation(effect, b"opacity")
            self._fade_anim.setDuration(300)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
            
            # 애니메이션 후 이펙트 제거 (성능)
            def on_finished():
                if new_widget.graphicsEffect() == effect:
                    new_widget.setGraphicsEffect(None)
                    
            self._fade_anim.finished.connect(on_finished)
            self._fade_anim.start()

# ══════════════════════════════════════════════════════════════
# 스무스 스크롤 필터 (휠 모션 등)
# ══════════════════════════════════════════════════════════════

from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QScrollBar

class SmoothScrollFilter(QObject):
    """
    QScrollArea나 QTextBrowser의 viewport()에 설치하여 
    마우스 휠 스크롤링을 부드럽게(Easing) 만들어줍니다.
    """
    def __init__(self, scroll_bar: QScrollBar, parent=None):
        super().__init__(parent)
        self.scroll_bar = scroll_bar
        self.anim = QPropertyAnimation(self.scroll_bar, b"value")
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setDuration(300)
        self._target_value = self.scroll_bar.value()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            if event.angleDelta().y() != 0:
                delta = event.angleDelta().y()
                # 휠 한 번 굴림 보통 120 -> 3줄 정도 이동, 부드럽게 하기 위해 약간 보정
                step = self.scroll_bar.singleStep() * (delta / 120) * 3
                
                # 애니메이션이 실행 중이 아닐 때는 항상 현재 실제 스크롤 값에서 시작해야 함
                # (사용자가 드래그했거나 스크롤 높이가 변경된 경우를 위함)
                if self.anim.state() != QPropertyAnimation.Running:
                    self._target_value = self.scroll_bar.value()
                
                new_val = self._target_value - step
                max_val = self.scroll_bar.maximum()
                min_val = self.scroll_bar.minimum()
                new_val = max(min_val, min(max_val, new_val))
                
                if new_val != self._target_value:
                    self._target_value = new_val
                    self.anim.stop()
                    self.anim.setStartValue(self.scroll_bar.value())
                    self.anim.setEndValue(int(self._target_value))
                    self.anim.start()
                
                return True # 이벤트 가로채기 (기본 스크롤 동작 방지)
        return super().eventFilter(obj, event)
