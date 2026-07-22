"""
ui/widgets/ring_particles.py — 링 오로라 배경 (오로라/불길 리본, 오로라 대체)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
구글 Antigravity 링 파티클에서 출발했으나, "점의 집합"이 아니라 오로라·불길이
일렁이는 "면(surface)의 집합"으로 표현한다.

  · 링 띠를 도는 흐르는 빛의 리본(오로라 커튼) 여러 겹. 각 리본은 반지름 중심선과
    두께가 여러 사인파의 합으로 시간에 따라 일렁이는 '채워진 면'이다.
  · 두께가 군데군데 0으로 오므라들며(pinch) 갈라진 불꽃 혓바닥(tongue)을 만들고,
    바깥 가장자리에 고주파 flicker를 얹어 불길처럼 너울거린다.
  · 색: 링 한 바퀴를 도는 0~360° 무지개는 QConicalGradient 한 장으로 처리 —
    리본을 그 그라데이션으로 채우면 각도별 색이 자동으로 입혀진다.
  · 글로우: 넓고 옅은 헤일로 → 좁고 밝은 코어의 다중 패스로 부드러운 발광.
  · 합성 — 다크=가산(Plus, 네온 오로라), 라이트=곱셈(Multiply, 흐르는 잉크).
  · 링 전체가 아주 천천히 회전하고(_ROT_SPEED), 은은히 숨쉰다(breathe).

성능/질감: 리본은 **저해상 오프스크린 버퍼**(_SUPERSAMPLE)에 그린 뒤 카드에
업스케일해 얹는다 — 채움 픽셀이 1/4로 줄어 빠르고, 업스케일 리샘플이 그대로
몽환적 블러가 된다(result 패널 유리 배경과 동일 기법).

AnimatedGradientBorder(components.py)가 paint 시점에 draw()를 호출한다.
phase(초)는 호출 측이 관리 — 멈추면 마지막 위상에서 동결되는 기존 관례 유지.
클래스명 RingParticles는 호출부 호환을 위해 유지(내용은 리본 필드).
"""

import math
import random

from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import (
    QPainter, QColor, QConicalGradient, QPainterPath, QImage,
)

_TWO_PI = math.pi * 2

# ── 링 영역 (기준 짧은 변 기준 비율) — 리본이 일렁이는 반지름 띠 ─────
# 안쪽 반경은 중앙의 책·제목·상세 텍스트 클리어런스(카드에서 콘텐츠 비중이 큼).
_INNER_RATIO = 0.1
_OUTER_RATIO = 0.17
_LAYERS = 10                # 겹치는 오로라 리본 수
_POINTS = 200             # 리본 둘레 샘플 수 (곡선 매끄러움)

# ⚠ 오로라 크기는 창(카드) 크기와 **완전히 무관한 고정 절대 크기**다.
# 중앙 원판(components.py의 center_disc)이 고정 px이므로 링도 고정이어야 둘의 비율이
# 항상 같다. rect에 비례시키면 작은 창에선 원판에 가려지고 최대화하면 과하게 커진다.
# rect은 버퍼(그릴 영역) 크기 산정에만 쓰고, 링 반경은 이 기준값으로만 계산한다.
# (링이 카드보다 크면 카드 경계에서 잘릴 뿐 — 크기 자체는 그대로 유지된다.)
_RING_REF_SIDE = 800      # 링 크기 기준 짧은 변(px, display) — 창 크기와 무관하게 고정

# 저해상 버퍼 배율(0.45≈1/5 픽셀). 작을수록 빠르고 더 흐릿(몽환).
_SUPERSAMPLE = 0.45

# ── 흐름/일렁임 ─────────────────────────────────────────────
_FLOW_SPEED = 0.7        # 사인파가 시간에 따라 흐르는 기준 속도
_FLICKER_FREQ = 10.0      # 불길 가장자리 고주파 flicker
_FLICKER_SPEED = 1.0
_FLICKER_AMT = 0.5

# ── 완속 회전 & 숨쉬기 ───────────────────────────────────────
_ROT_SPEED = 0.2         # rad/s
_BREATHE_SPEED = 0.2
_BREATHE_AMT = 0.08

# ── 색상 — 링 한 바퀴 무지개 스펙트럼 (conical) ──────────────
_HUE_STOPS = 13           # 0..1 둘레 색 스톱 수

# 글로우 다중 패스: (두께 배율, 알파 배율) — 넓고 옅게 → 좁고 밝게.
# (업스케일 리샘플이 이미 가장자리를 흐리므로 2패스로 충분.)
_PASSES = ((2.0, 0.3), (1.0, 1.0))

_SEED = 20260715

# 테마 파라미터 (키=is_dark)
#   buf_mode = 버퍼 내부 리본 축적 합성(다크=가산 코어, 라이트=색 겹침)
#   blit_mode = 버퍼를 카드에 얹을 합성(다크=가산 발광, 라이트=곱셈 잉크)
_THEMES = {
    True: dict(saturation=0.85, lightness=0.60, layer_alpha=0.22,
               buf_mode=QPainter.CompositionMode_Plus,
               blit_mode=QPainter.CompositionMode_Plus),
    False: dict(saturation=0.64, lightness=0.54, layer_alpha=0.30,
                buf_mode=QPainter.CompositionMode_SourceOver,
                blit_mode=QPainter.CompositionMode_Multiply),
}


class RingParticles:
    """카드 배경에 그리는 링 오로라 리본 필드. 인스턴스는 위젯별로 하나."""

    def __init__(self):
        rnd = random.Random(_SEED)
        self._layers = []
        for i in range(_LAYERS):
            self._layers.append(dict(
                mid_frac=(i + 0.5) / _LAYERS + (rnd.random() - 0.5) * 0.06,
                amp=0.10 + rnd.random() * 0.09,        # 중심선 방황(띠폭 비율)
                base_h=0.22 + rnd.random() * 0.13,     # 기본 두께(띠폭 비율)
                freqs=(rnd.choice((2, 3)), rnd.choice((3, 4, 5)),
                       rnd.choice((5, 6, 7))),
                speeds=((rnd.random() * 0.6 + 0.7) * (1 if i % 2 else -1),
                        (rnd.random() * 0.5 + 0.5) * (-1 if i % 2 else 1),
                        rnd.random() * 0.8 + 0.4),
                phase=rnd.random() * _TWO_PI,
                pinch_freq=rnd.choice((3, 4, 5, 6)),
                pinch_speed=(rnd.random() * 0.5 + 0.5) * (1 if i % 2 else -1),
                pinch_phase=rnd.random() * _TWO_PI,
                alpha=0.80 + rnd.random() * 0.20,      # 리본별 밝기 편차
            ))
        self._grad = None            # 무지개 conical (테마별 캐시)
        self._grad_dark = None
        self._buf = None             # 저해상 오프스크린 버퍼
        self._buf_key = None         # (bw, bh)

    # ── 무지개 원뿔 그라데이션 (테마 바뀔 때만 재생성) ────────
    def _build_grad(self, dark: bool):
        th = _THEMES[dark]
        s, l = th["saturation"], th["lightness"]
        g = QConicalGradient(QPointF(0, 0), 0.0)
        for i in range(_HUE_STOPS):
            pos = i / (_HUE_STOPS - 1)
            g.setColorAt(pos, QColor.fromHslF(pos % 1.0, s, l, 1.0))
        self._grad = g
        self._grad_dark = dark

    # ── 리본 각도별 기하 (레이어당 프레임 1회 — 삼각함수 캐시) ─
    def _ribbon_geom(self, layer, mid, band, t):
        """(ct, st, rc, h1, flick) 배열. h1=thick_scale 1 기준 반두께."""
        f1, f2, f3 = layer["freqs"]
        s1, s2, s3 = layer["speeds"]
        ph = layer["phase"]
        amp = layer["amp"] * band
        base_h = layer["base_h"] * band
        pf, ps, pph = layer["pinch_freq"], layer["pinch_speed"], layer["pinch_phase"]
        sin, cos = math.sin, math.cos
        cts, sts, rcs, h1s, flicks = [], [], [], [], []
        for k in range(_POINTS + 1):
            a = k * _TWO_PI / _POINTS
            # 중심선 반지름 — 세 사인파의 합으로 유기적으로 일렁임
            flow = (0.6 * sin(a * f1 + t * s1 + ph)
                    + 0.3 * sin(a * f2 + t * s2 + ph * 1.7)
                    + 0.2 * sin(a * f3 + t * s3 + ph * 0.5))
            # 두께 pinch — 0으로 오므라들며 갈라진 불꽃 혓바닥
            pinch = 0.30 + 0.70 * sin(a * pf + t * ps + pph)
            h = base_h * pinch
            cts.append(cos(a))
            sts.append(sin(a))
            rcs.append(mid + amp * flow)
            h1s.append(h if h > 0.0 else 0.0)
            # 불길 flicker — 바깥 가장자리만 고주파로 너울
            flicks.append(1.0 + _FLICKER_AMT
                          * sin(a * _FLICKER_FREQ + t * _FLICKER_SPEED + ph))
        return cts, sts, rcs, h1s, flicks

    @staticmethod
    def _ribbon_path(geom, thick_scale) -> QPainterPath:
        """캐시된 기하에서 한 패스의 채워진 면 경로(삼각함수 없이)."""
        cts, sts, rcs, h1s, flicks = geom
        n = len(cts)
        path = QPainterPath()
        # 바깥 경계 정방향
        for k in range(n):
            h = h1s[k] * thick_scale
            ro = rcs[k] + h * flicks[k]
            x, y = cts[k] * ro, sts[k] * ro
            if k == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        # 안쪽 경계 역방향
        for k in range(n - 1, -1, -1):
            ri = rcs[k] - h1s[k] * thick_scale
            path.lineTo(cts[k] * ri, sts[k] * ri)
        path.closeSubpath()
        return path

    # ── 프레임 드로잉 ─────────────────────────────────────
    def draw(self, p: QPainter, rect: QRectF, phase: float, dark: bool):
        if self._grad is None or self._grad_dark != dark:
            self._build_grad(dark)

        th = _THEMES[dark]
        bw = max(2, int(rect.width() * _SUPERSAMPLE))
        bh = max(2, int(rect.height() * _SUPERSAMPLE))
        if self._buf is None or self._buf_key != (bw, bh):
            self._buf = QImage(bw, bh, QImage.Format_ARGB32_Premultiplied)
            self._buf_key = (bw, bh)
        self._buf.fill(Qt.transparent)

        # 링 반경은 rect(창)와 무관한 고정값 — 창을 키우든 줄이든 항상 같은 크기.
        # 버퍼는 rect를 supersample 배로 축소한 크기 → 표시 반경 R을 얻으려면
        # 버퍼 좌표에선 R*supersample. 여기 inner/band는 이미 버퍼 좌표(고정).
        ref = _RING_REF_SIDE * _SUPERSAMPLE
        inner = ref * _INNER_RATIO
        band = ref * (_OUTER_RATIO - _INNER_RATIO)
        t = phase * _FLOW_SPEED
        rotation_deg = math.degrees(phase * _ROT_SPEED)
        breathe = 1 + math.sin(phase * _BREATHE_SPEED * _TWO_PI) * _BREATHE_AMT
        base_alpha = th["layer_alpha"]

        # 1) 저해상 버퍼에 리본 면 축적
        bp = QPainter(self._buf)
        bp.setRenderHint(QPainter.Antialiasing)
        bp.translate(bw / 2.0, bh / 2.0)
        bp.rotate(rotation_deg)
        bp.scale(breathe, breathe)
        bp.setPen(Qt.NoPen)
        bp.setBrush(self._grad)
        bp.setCompositionMode(th["buf_mode"])
        for layer in self._layers:
            mid = inner + band * layer["mid_frac"]
            geom = self._ribbon_geom(layer, mid, band, t)   # 삼각함수 1회
            for thick_scale, a_scale in _PASSES:
                path = self._ribbon_path(geom, thick_scale)
                bp.setOpacity(base_alpha * layer["alpha"] * a_scale)
                bp.drawPath(path)
        bp.end()

        # 2) 카드에 업스케일 블릿(리샘플=블러) + 테마 합성
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setCompositionMode(th["blit_mode"])
        p.drawImage(rect, self._buf)
        p.restore()
