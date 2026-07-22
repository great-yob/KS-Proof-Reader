"""
core — KS-Proof Reader 코어 엔진 패키지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
교정 엔진 모듈 (사전검증, Gemini, HWP, Merger)
"""

import os

from .models import Correction, HL_DICT, HL_PNU, HL_TYPO, HL_POLISH, DOC_WARN_CHARS
from .config_loader import ConfigLoader
from .gemini_checker import GeminiChecker
from .correction_merger import CorrectionMerger
from .hwp_editor import HwpEditor as _HwpComEditor
from .hwpx_editor import HwpxEditor as _HwpxEditor


def HwpEditor(file_path: str, *args, **kwargs):
    """
    파일에 맞는 편집기를 반환하는 팩토리.

    기본 정책 — **모든 확장자를 COM 브리지로 처리**.
      이유: HwpxEditor는 OWPML XML 텍스트만 치환하고 글자색 속성을 다루지 않는다.
      교정 부분 빨강 표시 기능을 모든 파일에서 동일하게 보장하려면 COM 경유가 필수.

    환경변수 KS_HWP_BACKEND="direct" 로 강제 시에만 HwpxEditor 사용.
      (색상 표시 없이 텍스트만 변환 — 빠른 처리가 필요한 대용량 hwpx 일괄 처리용)

      .hwp / .hwpx          → HwpComEditor (32bit COM 브리지, 색상 적용 가능)
      KS_HWP_BACKEND=direct → HwpxEditor (ZIP 직접 편집, 색상 미적용)
    """
    backend = os.environ.get("KS_HWP_BACKEND", "").lower()
    ext = os.path.splitext(file_path)[1].lower()

    if backend == "direct" and ext == ".hwpx":
        try:
            with open(file_path, "rb") as f:
                head = f.read(4)
            if head.startswith(b"PK"):
                logger = kwargs.get("logger")
                if logger:
                    logger("  편집기: HwpxEditor (직접 XML, ⚠ 색상 미적용)")
                return _HwpxEditor(file_path, *args, **kwargs)
        except OSError:
            pass

    logger = kwargs.get("logger")
    if logger:
        logger(f"  편집기: HWP COM 브리지 ({ext})")
    return _HwpComEditor(file_path, *args, **kwargs)


__all__ = [
    "Correction",
    "HL_DICT", "HL_PNU", "HL_TYPO", "HL_POLISH", "DOC_WARN_CHARS",
    "ConfigLoader",
    "GeminiChecker",
    "CorrectionMerger",
    "HwpEditor",
]
