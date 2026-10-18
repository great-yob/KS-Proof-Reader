"""
core/correction_merger.py — 교정 목록 병합·정규화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI 결과를 중복 제거하고 적용 순서를 최적화.
"""


class CorrectionMerger:
    """교정 결과 병합·정규화"""

    @staticmethod
    def merge(corrections: list) -> list:
        """
        병합 규칙:
        1. 중복 제거 (original 기준 — 먼저 나온 항목 유지)
        2. 긴 표현 우선 정렬 — 적용 시 부분 문자열 오염 방지
        """
        merged: dict = {}
        for item in corrections:
            if item.original not in merged:
                merged[item.original] = item

        valid = [c for c in merged.values() if c.is_valid()]
        valid.sort(key=lambda c: len(c.original), reverse=True)

        return valid
