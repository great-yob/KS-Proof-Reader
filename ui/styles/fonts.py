"""
ui/styles/fonts.py — 번들 폰트 로드
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
assets/fonts/ 의 otf/ttf를 전부 QFontDatabase에 등록한다.
시스템에 미설치여도 앱 안에서 쓸 수 있게 한다(라이선스는 그 폴더의 NOTICE.md).

두 패밀리를 쓰고, 쓰임이 다르다:
  · PRIMARY_FAMILY (Pretendard) — UI 전역.
  · SERIF_FAMILY   (본명조) — 검토 화면 **원문·교정문 본문에만**. 출판 원고 대부분이
    명조 계열 본문을 쓰므로, 미리보기가 원고와 같은 인상으로 읽히게 한다.
    ⚠ UI에는 쓰지 말 것 — 명조는 작은 글자·굵은 글자에서 가독성이 떨어진다.
"""

from PySide6.QtGui import QFontDatabase

from ui.styles.assets import asset_path

PRIMARY_FAMILY = "Pretendard"
# 가변 TTF 한 파일(NotoSerifKR-VF.ttf)이 ExtraLight~Black 7종을 제공한다 —
#   교정 하이라이트의 SemiBold/Bold도 합성이 아닌 진짜 자형으로 나온다(Qt 6.11 확인).
SERIF_FAMILY = "Noto Serif KR"


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
