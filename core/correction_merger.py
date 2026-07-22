"""
core/correction_merger.py — 교정 목록 병합·정규화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI 결과를 중복 제거하고 적용 순서를 최적화.
"""


class CorrectionMerger:
    """교정 결과 병합·정규화

    PNU 폐기 이후 단일 소스(AI)만 입력되지만, v4 호환을 위해 두 인자 시그니처는 유지.
    """

    @staticmethod
    def merge(legacy_list: list, ai_list: list) -> list:
        """
        병합 규칙:
        1. 중복 제거 (original 기준)
        2. 긴 표현 우선 정렬 — 적용 시 부분 문자열 오염 방지

        legacy_list: v4 호환용. 비어있는 리스트를 전달하면 됨.
        ai_list:     AI(Gemini) 교정 결과
        """
        merged: dict = {}

        # legacy 항목(있다면) 먼저 등록
        for item in legacy_list:
            if item.original not in merged:
                merged[item.original] = item

        # AI는 같은 original이 없을 때만 추가
        for item in ai_list:
            if item.original not in merged:
                merged[item.original] = item

        valid = [c for c in merged.values() if c.is_valid()]
        valid.sort(key=lambda c: len(c.original), reverse=True)

        return valid

    @staticmethod
    def find_subset_relations(corrections: list) -> dict:
        """긴 원문에 포함된 짧은 원문 관계 식별.

        Returns:
            dict: {짧은_original: 그것을_포함하는_긴_original}
        """
        relations = {}
        originals = [c.original for c in corrections]
        for shorter in originals:
            for longer in originals:
                if shorter != longer and shorter in longer:
                    relations[shorter] = longer
                    break
        return relations
