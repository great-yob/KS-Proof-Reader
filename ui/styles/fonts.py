"""
ui/styles/fonts.py — 번들 폰트(Pretendard) 로드
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
assets/fonts/ 의 Pretendard otf/ttf를 QFontDatabase에 등록한다.
시스템에 미설치여도 앱 내에서 Pretendard를 사용 가능하게 한다.
"""

from PySide6.QtGui import QFontDatabase

from ui.styles.assets import asset_path

PRIMARY_FAMILY = "Pretendard"


def load_fonts() -> str:
    """번들 폰트를 등록하고 기본 패밀리명을 반환."""
    font_dir = asset_path("fonts")
    if not font_dir.exists():
        return PRIMARY_FAMILY
    loaded = set()
    for path in sorted(font_dir.glob("*.otf")) + sorted(font_dir.glob("*.ttf")):
        idx = QFontDatabase.addApplicationFont(str(path))
        if idx != -1:
            loaded.update(QFontDatabase.applicationFontFamilies(idx))
    if PRIMARY_FAMILY in loaded:
        return PRIMARY_FAMILY
    # 등록 실패 시 첫 패밀리 또는 기본명
    return next(iter(loaded), PRIMARY_FAMILY)
