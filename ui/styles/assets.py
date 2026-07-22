"""
ui/styles/assets.py — 번들 리소스 경로 해석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
폰트/아이콘 등 assets/ 하위 리소스의 절대 경로를 반환한다.
개발 실행과 Nuitka/PyInstaller 동결 실행 모두 대응.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    """assets/ 의 부모 폴더.

    ⚠ 동결 빌드에서 **EXE 옆이 아니다**. PyInstaller onedir은 `--add-data assets;assets`를
      `_internal/assets`(= `sys._MEIPASS`) 안에 넣는다. 예전엔 여기서 EXE 옆만 봤고,
      그래서 배포본에서 아이콘·로고·**폰트가 전부 조용히 사라졌다**(파일을 못 읽으면
      icons는 빈 픽스맵, fonts는 시스템 폴백이라 예외 없이 그냥 밋밋해진다).

      데이터(사전 DB·kiwi 모델)와 반대 규칙이라는 점에 주의 — 그쪽은 따로 교체돼야 해서
      일부러 EXE 옆에 둔다(datapaths.py). assets는 코드와 한 몸이라 번들 안이 맞다.
    """
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", None)          # PyInstaller
        if mp and (Path(mp) / "assets").is_dir():
            return Path(mp)
        return Path(sys.executable).parent           # Nuitka 등 EXE 옆에 두는 경우
    # ui/styles/assets.py → parents[2] = 프로젝트 루트
    return Path(__file__).resolve().parents[2]


def asset_path(*parts) -> Path:
    return base_dir().joinpath("assets", *parts)
