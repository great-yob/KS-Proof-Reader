"""
ui/widgets/book_progress.py — 진행률 '책' 그래픽 (원형 ProgressRing 대체)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
플랫 2D 픽토그램 — 사용자가 제공한 book-01.svg 실루엣을 그대로 이식.
펼친 책: 좌/우 앞 페이지(front)가 스파인에서 만나고, 그 뒤로 살짝 큰
뒤 페이지(back)가 바깥·아래로 삐져나와 책 두께를 만든다. 위/아래 모두
완만한 곡선(스파인 쪽이 살짝 처지는 얕은 밸리), 바깥 세로 모서리는 직선.

  · 실루엣 = book-01.svg 4개 패스(back_l/back_r/front_l/front_r)를 QPainterPath로
    옮겨 위젯 좌표로 스케일·정렬. 장식 효과 없음(그림자/부유/글로우 무).
  · 페이지 플립 = 고전적 2D — 앞 페이지를 스파인 기준 수평 스케일(cosθ)로 접는다.
  · 진행률 = 왼쪽 페이지 교정 마크가 진행률 따라 증가, 100% 완료 시
    오른쪽 페이지에도 일괄. 아래 큰 % 텍스트가 주인공.
  · 교정 마크 종류: 밑줄(ul) / 띄어쓰기 쐐기 ∨(v) / 체크 ✓(chk).

리페인트 정책(ProgressRing과 동일 원칙):
  상시 타이머 금지 — set_animating(True)일 때만 16ms 타이머가 돌고,
  멈추면 마지막 위상에서 정지한다. 진행률 전환은 QVariantAnimation이 담당.
"""

import math
import time

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QBrush, QTransform
from PySide6.QtWidgets import QWidget

from ui.styles.theme import current_palette


# ── 위젯/배치 상수 ────────────────────────────────────────────
_W, _H = 264, 150          # 위젯 크기 (책 30% 축소분에 맞춰 세로도 축소)
_TOP = 20                  # 책 상단 y (SVG y_min을 여기로 매핑)
_BOOK_W = 182              # 책 표시 폭(px) — 스케일 기준

# book-01.svg 바운딩 박스(뒤 페이지 포함)
_SVG_X0, _SVG_X1 = 5.238, 166.168
_SVG_Y0, _SVG_Y1 = 4.71, 101.91
_SVG_CX = (_SVG_X0 + _SVG_X1) / 2.0      # 책 수평 중심(≈스파인)
_SVG_SPINE = 85.98                        # 앞 페이지 스파인 x

_OUTLINE_W = 1.0           # 픽토그램 윤곽선
_BOOK_SCALE = 0.7          # 책 그래픽(패스·행·마크·%) 전체 균일 축소 배율

_FLIP_DUR = 1.15           # 페이지 한 장 넘기는 시간(s)
_FLIP_PAUSE = 0.55         # 넘김 사이 쉬는 시간(s)

# 본문 행 — 페이지 6행 (스파인 기준 상대 배치)
_LINE_FRACS = (0.95, 0.80, 0.90, 0.70, 0.88, 0.55)
_LINE_Y0 = 20              # 첫 행 y (_TOP 기준)
_LINE_GAP = 11.0           # 행 간격
_LINE_X0 = 16             # 행 시작 x (스파인 기준)
_LINE_SPAN = 58           # 행 최대 길이

# 교정 마크 — (행, 위치 비율, 길이 비율(ul만), 종류 "ul"|"v"|"chk").
_PROOF_MARKS_L = [
    (1, 0.15, 0.30, "ul"),
    (4, 0.62, 0.00, "v"),
    (5, 0.10, 0.26, "ul"),
    (0, 0.55, 0.00, "chk"),
]
_PROOF_MARKS_R = [
    (1, 0.35, 0.00, "v"),
    (2, 0.18, 0.28, "ul"),
    (4, 0.70, 0.00, "chk"),
]


def _flat_style(pal: dict) -> dict:
    """픽토그램 스타일 토큰 — 전부 팔레트에서 (테마 자동 대응)."""
    return dict(
        page=QColor(pal["surface"]),              # 앞 페이지 면
        back=QColor(pal["surface_alt"]),          # 뒤 페이지(두께) 면
        flip_front=QColor(pal["surface_alt"]),    # 넘어가는 페이지 앞면
        flip_back=QColor(pal["surface_hover"]),   # 넘어가는 페이지 뒷면
        outline=QColor(pal["text"]),              # 윤곽선 (픽토그램)
        line=QColor(pal["text_muted"]),           # 본문 행
        mark=QColor(pal["error"]),                # 교정 마크
    )


def _svg_book_paths():
    """book-01.svg의 4개 패스를 SVG 좌표계 QPainterPath로 빌드."""
    back_l = QPainterPath(QPointF(5.238, 11.945))
    back_l.lineTo(69.746, 11.945)
    back_l.cubicTo(78.559, 11.945, 85.703, 19.09, 85.703, 27.901)
    back_l.lineTo(85.703, 101.91)
    back_l.cubicTo(85.703, 101.91, 78.558, 95.184, 69.746, 95.184)
    back_l.lineTo(5.238, 95.184)
    back_l.lineTo(5.238, 11.945)
    back_l.closeSubpath()

    back_r = QPainterPath(QPointF(166.168, 11.945))
    back_r.lineTo(101.66, 11.945)
    back_r.cubicTo(92.847, 11.945, 85.703, 19.09, 85.703, 27.901)
    back_r.lineTo(85.703, 101.91)
    back_r.cubicTo(85.703, 101.91, 92.848, 95.184, 101.66, 95.184)
    back_r.lineTo(166.168, 95.184)
    back_r.lineTo(166.168, 11.945)
    back_r.closeSubpath()

    front_l = QPainterPath(QPointF(11.623, 4.71))
    front_l.lineTo(71.236, 4.71)
    front_l.cubicTo(79.38, 4.71, 85.98, 11.312, 85.98, 19.455)
    front_l.lineTo(85.98, 95.069)
    front_l.cubicTo(85.98, 95.069, 79.379, 88.853, 71.236, 88.853)
    front_l.lineTo(11.623, 88.853)
    front_l.lineTo(11.623, 4.71)
    front_l.closeSubpath()

    front_r = QPainterPath(QPointF(160.337, 4.71))
    front_r.lineTo(100.725, 4.71)
    front_r.cubicTo(92.58, 4.71, 85.98, 11.312, 85.98, 19.455)
    front_r.lineTo(85.98, 95.069)
    front_r.cubicTo(85.98, 95.069, 92.581, 88.853, 100.725, 88.853)
    front_r.lineTo(160.337, 88.853)
    front_r.lineTo(160.337, 4.71)
    front_r.closeSubpath()
    return back_l, back_r, front_l, front_r


class BookProgress(QWidget):
    """book-01.svg 픽토그램 책 페이지 플립. API는 ProgressRing 호환 + set_animating."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_W, _H)
        self._progress = 0.0
        self._target_progress = 0
        self._animating = False
        self._phase = 0.0
        self._t0 = time.time()
        self._ease = QEasingCurve(QEasingCurve.InOutCubic)

        # SVG → 위젯 변환(고정 크기라 1회 계산) + 패스 사전 변환/캐시
        s = _BOOK_W / (_SVG_X1 - _SVG_X0)
        cx = _W / 2.0
        tx = cx - s * _SVG_CX
        ty = _TOP - s * _SVG_Y0
        qt = QTransform()
        qt.translate(tx, ty)
        qt.scale(s, s)
        bl, br, fl, fr = _svg_book_paths()
        self._p_back_l = qt.map(bl)
        self._p_back_r = qt.map(br)
        self._p_front_l = qt.map(fl)
        self._p_front_r = qt.map(fr)
        self._cx = cx
        self._spine_x = tx + s * _SVG_SPINE       # 앞 페이지 스파인(위젯 x)
        self._text_y = ty + s * _SVG_Y1 + 10      # % 텍스트 y

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.valueChanged.connect(self._on_anim_value)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    # ── API ───────────────────────────────────────────────
    def set_progress(self, value: int):
        val = max(0, min(100, value))
        if val == self._target_progress:
            return
        self._target_progress = val
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(float(val))
        self._anim.start()

    def progress(self) -> int:
        return self._target_progress

    def set_animating(self, animating: bool):
        if self._animating == animating:
            return
        self._animating = animating
        if animating:
            self._t0 = time.time() - self._phase
            self._timer.start(16)
        else:
            self._timer.stop()
            self.update()

    def refresh_theme(self):
        self.update()

    def _on_anim_value(self, value: float):
        self._progress = value
        self.update()

    # ══════════════════════════════════════════════════════
    # 페인트
    # ══════════════════════════════════════════════════════
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 책 그래픽 전체를 균일 축소(패스·행·마크·% 함께). 기준점은 책 상단 중앙 —
        # 상단은 _TOP에 고정된 채 안쪽으로 줄어들어 아래 빈 공간을 남기지 않는다.
        # 선은 아래 코스메틱 펜이라 축소해도 두께는 유지된다.
        p.translate(_W / 2.0, _TOP)
        p.scale(_BOOK_SCALE, _BOOK_SCALE)
        p.translate(-_W / 2.0, -_TOP)

        pal = current_palette()
        st = _flat_style(pal)
        cx = self._spine_x

        if self._animating:
            self._phase = time.time() - self._t0
        ph = self._phase
        prog = self._progress / 100.0

        outline = QPen(st["outline"], _OUTLINE_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        outline.setCosmetic(True)   # 축소해도 윤곽선 두께 유지(디테일 선과 동일 굵기)

        # 1) 뒤 페이지(두께) — 앞 페이지 뒤에서 바깥·아래로 삐져나옴
        p.setPen(outline)
        p.setBrush(QBrush(st["back"]))
        p.drawPath(self._p_back_l)
        p.drawPath(self._p_back_r)

        # 2) 앞 페이지(좌/우) + 본문 행
        p.setPen(outline)
        p.setBrush(QBrush(st["page"]))
        p.drawPath(self._p_front_l)
        p.drawPath(self._p_front_r)
        self._draw_text_lines(p, cx, -1, st)
        self._draw_text_lines(p, cx, 1, st)

        # 3) 교정 마크 — 왼쪽: 진행률 따라 증가 / 오른쪽: 완료 시 일괄
        n_marks = min(len(_PROOF_MARKS_L), int(prog * len(_PROOF_MARKS_L) + 0.02))
        self._draw_marks(p, cx, -1, st, _PROOF_MARKS_L[:n_marks])
        if self._target_progress >= 100:
            self._draw_marks(p, cx, 1, st, _PROOF_MARKS_R)

        # 4) 넘어가는 페이지 (애니메이션 중 & 미완료)
        if self._animating and self._target_progress < 100:
            cyc = _FLIP_DUR + _FLIP_PAUSE
            tc = ph % cyc
            if tc < _FLIP_DUR:
                theta = self._ease.valueForProgress(tc / _FLIP_DUR) * math.pi
                self._draw_flip(p, cx, theta, st)

        # 5) 진행률 텍스트 — 중앙 정보의 주인공이라 크게
        p.setOpacity(1.0)
        p.setPen(QColor(pal.get("text", "#1A1D23")))
        f = self.font()
        f.setPixelSize(50)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, self._text_y, float(_W), 60), Qt.AlignCenter,
                   f"{int(self._progress)}%")

    # ── 본문 행 / 마크 (스파인 기준 상대 배치) ────────────
    def _line_span(self, cx, side_or_scale, i):
        s = side_or_scale
        y = _TOP + _LINE_Y0 + i * _LINE_GAP
        x0 = cx + s * _LINE_X0
        x1 = cx + s * (_LINE_X0 + _LINE_SPAN * _LINE_FRACS[i])
        return x0, x1, y

    def _draw_text_lines(self, p, cx, scale, st):
        pen = QPen(st["line"], 1.0, Qt.SolidLine, Qt.RoundCap)
        pen.setCosmetic(True)
        p.setPen(pen)
        for i in range(6):
            x0, x1, y = self._line_span(cx, scale, i)
            p.drawLine(QPointF(x0, y), QPointF(x1, y))

    def _draw_marks(self, p, cx, side, st, marks):
        """교정 마크: ul=밑줄, v=띄어쓰기 쐐기 ∨, chk=체크 ✓."""
        pen = QPen(st["mark"], 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for (li, f0, fl, kind) in marks:
            y = _TOP + _LINE_Y0 + li * _LINE_GAP
            x = cx + side * (_LINE_X0 + _LINE_SPAN * f0)
            if kind == "ul":
                x1 = cx + side * (_LINE_X0 + _LINE_SPAN * (f0 + fl))
                p.drawLine(QPointF(x, y + 4.0), QPointF(x1, y + 4.0))
            elif kind == "v":
                path = QPainterPath(QPointF(x - 4.2, y - 6))
                path.lineTo(QPointF(x, y - 0.5))
                path.lineTo(QPointF(x + 4.2, y - 6))
                p.strokePath(path, pen)
            else:  # "chk"
                path = QPainterPath(QPointF(x - 4.5, y - 2))
                path.lineTo(QPointF(x - 1.2, y + 2.5))
                path.lineTo(QPointF(x + 5.0, y - 6))
                p.strokePath(path, pen)

    def _draw_flip(self, p, cx, theta, st):
        """고전적 2D 플립 — 앞 오른쪽 페이지를 스파인 기준 x 스케일(cosθ)로 접는다."""
        ct = math.cos(theta)
        if abs(ct) < 0.02:
            return
        fade_from = math.pi * 0.90
        op = 1.0 if theta < fade_from else max(
            0.0, (math.pi - theta) / (math.pi - fade_from))
        if op <= 0.0:
            return

        p.save()
        p.setOpacity(op)
        # 스파인(cx) 기준 수평 스케일 — ct>0 오른쪽 페이지, ct<0 왼쪽으로 착지(미러)
        p.translate(cx, 0)
        p.scale(ct, 1.0)
        p.translate(-cx, 0)
        pen = QPen(st["outline"], _OUTLINE_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(QBrush(st["flip_front"] if ct >= 0 else st["flip_back"]))
        p.drawPath(self._p_front_r)
        if ct > 0.08:
            self._draw_text_lines(p, cx, 1, st)
        p.restore()
