"""
ui/styles/icons.py — SVG 라인 아이콘 렌더링 (Lucide, ISC)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
assets/icons/<name>.svg 의 stroke="currentColor"를 지정 색으로 치환해
고해상도 QPixmap/QIcon으로 렌더한다. 테마 색상에 맞춰 재생성 가능.
"""

from PySide6.QtCore import QByteArray, QRectF, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

from ui.styles.assets import asset_path
from ui.styles.theme import current_palette, current_mode

_DPR = 2.0
_text_cache = {}
_pixmap_cache = {}


def _svg_text(name: str) -> str:
    if name in _text_cache:
        return _text_cache[name]
    try:
        txt = asset_path("icons", f"{name}.svg").read_text(encoding="utf-8")
    except OSError:
        txt = ""
    _text_cache[name] = txt
    return txt


def color_for(role: str = "text_sub") -> str:
    pal = current_palette()
    return pal.get(role, pal["text_sub"])


def pixmap(name: str, color: str, size: int = 18,
           stroke_width: float = 0) -> QPixmap:
    key = (name, color, size, stroke_width)
    if key in _pixmap_cache:
        return _pixmap_cache[key]

    svg = _svg_text(name)
    phys = int(size * _DPR)
    pm = QPixmap(phys, phys)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(_DPR)
    if svg:
        svg = svg.replace("currentColor", color)
        if stroke_width > 0:
            import re
            svg = re.sub(r'stroke-width="[^"]*"',
                         f'stroke-width="{stroke_width}"', svg)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        p = QPainter(pm)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
    _pixmap_cache[key] = pm
    return pm


def make_icon(name: str, color: str, size: int = 18) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size))
    return ic


def app_icon() -> QIcon:
    """작업표시줄·Alt-Tab·창 아이콘용 앱 아이콘(assets/icon.ico, 다해상도).

    ⚠ **실행 중** 창의 작업표시줄 아이콘은 EXE 임베드 리소스가 아니라 Qt가
      `setWindowIcon`으로 지정한 아이콘을 쓴다. 이걸 설정하지 않으면 Qt 기본
      아이콘(깨진 듯 보이는 빈 아이콘)이 뜬다 — 고정한 바로가기 아이콘은 EXE
      리소스라 멀쩡한데 실행하면 깨져 보이는 원인이 바로 이 차이다.
    """
    p = asset_path("icon.ico")
    ic = QIcon(str(p))
    if ic.isNull():                     # 아주 드문 폴백 — 렌더 아이콘으로라도
        ic = make_icon("wand-sparkles", color_for("brand"), 32)
    return ic


def logo_pixmap(width: int = 0, height: int = 30) -> QPixmap:
    """Sidebar 상단에 들어갈 브랜드 로고 픽스맵.
    라이트=assets/logo/ci-01.png, 다크=ci-dark-01.png 로 분리되어 있어
    테마에 맞는 픽스맵을 반환한다."""
    mode = current_mode()
    fname = "ci-dark-01.png" if mode == "dark" else "ci-01.png"
    key = ("__logo__", mode, width, height)
    if key in _pixmap_cache:
        return _pixmap_cache[key]

    src = QPixmap(str(asset_path("logo", fname)))
    if not src.isNull():
        if width > 0:
            scaled = src.scaledToWidth(int(width * _DPR), Qt.SmoothTransformation)
        else:
            scaled = src.scaledToHeight(int(height * _DPR), Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(_DPR)
        src = scaled
    _pixmap_cache[key] = src
    return src


def icon_size(size: int) -> QSize:
    return QSize(size, size)
