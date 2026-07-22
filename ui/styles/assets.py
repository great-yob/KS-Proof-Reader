"""
ui/styles/assets.py — 번들 리소스 경로 해석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
폰트/아이콘 등 assets/ 하위 리소스의 절대 경로를 반환한다.
개발 실행과 Nuitka/PyInstaller 동결 실행 모두 대응.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # ui/styles/assets.py → parents[2] = 프로젝트 루트
    return Path(__file__).resolve().parents[2]


def asset_path(*parts) -> Path:
    return base_dir().joinpath("assets", *parts)
