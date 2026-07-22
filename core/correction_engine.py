"""
core/correction_engine.py — 교정 생성 엔진
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
생성(교정안 만들기) 책임을 엔진 어댑터 뒤에 둔다. 현재 생성 엔진은 Gemini 하나다.

  · GeminiEngine — 기존 GeminiChecker 래핑.

build_engine(api_key)가 엔진을 만든다(키 없으면 None). GUI-agnostic — PySide6 미사용.

⚠ KoGEC(NLLB 기반 오프라인 GEC) 엔진과 앙상블 교차검증은 2026-06-17 **제거됨.**
  실측에서 어절 단위 환각·과교정(예: '사회문제에'→'사회문제제에서', '홍보전략을'→
  '전략을', '위한'→'홍보')이 심했고, 앙상블 합치율도 1/39 수준이라 검증 도구로서
  노이즈가 압도적이었다. 출판 교정은 정확성·신뢰도가 최우선 → 미검증 생성모델은
  파이프라인에서 뺀다. 검증된 대체 모델(출판 도메인 파인튜닝 + 정량 검증) 전엔
  재도입 금지. (설계도 docs/proofreading-architecture.md §6 — 역사적 기록)
"""


class GeminiEngine:
    """기존 GeminiChecker 어댑터."""

    def __init__(self, api_key: str):
        from .gemini_checker import GeminiChecker
        self._checker = GeminiChecker(api_key)

    def check_scope(self, text, suspicious_words=None, *, scope_typo=True,
                    scope_spacing=True, scope_polish=False, logger=None, stop_event=None):
        return self._checker.check_scope(
            text, suspicious_words or [],
            scope_typo=scope_typo, scope_spacing=scope_spacing,
            scope_polish=scope_polish, logger=logger, stop_event=stop_event)

    @property
    def last_call_stats(self) -> dict:
        """직전 check_scope의 청크 호출 집계 — {"failed": n, "total": m}.

        워커가 '전체 실패(침묵 성공 방지)'/'부분 실패'를 구분해 로그로 표출한다.
        """
        return {"failed": getattr(self._checker, "last_failed_chunks", 0),
                "total":  getattr(self._checker, "last_total_chunks", 0)}


def build_engine(api_key: str = "", *, logger=None):
    """생성 엔진 생성. API 키가 없으면 None.

    현재 엔진은 Gemini 단독이다.
    """
    log = logger or (lambda *_: None)
    if api_key:
        return GeminiEngine(api_key)
    log("  [엔진] Gemini API 키 없음")
    return None
