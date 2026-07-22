"""
core/models.py — 데이터 모델 및 전역 상수
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
교정 항목 데이터 클래스, 하이라이트 색상 상수, API 설정 상수
"""

from dataclasses import dataclass, field


# ══════════════════════════════════════════════════════
# ▌HWP 배경 하이라이트 색상 (Windows COLORREF = 0x00BBGGRR)
# ══════════════════════════════════════════════════════

HL_DICT   = 0x00FFFF    # 노란색  ← 사전검증 기본 (표준국어대사전·우리말샘)
HL_TYPO   = 0x55FFAA    # 연두색  ← AI 오탈자 보완
HL_POLISH = 0xFFBBDD    # 연보라  ← AI 윤문 교정

# v4 호환 별칭 — 외부 코드가 import 하던 이름. 제거 예정.
HL_PNU = HL_DICT


# ══════════════════════════════════════════════════════
# ▌API / 엔진 설정 상수
# ══════════════════════════════════════════════════════

# Gemini AI
# 청크 크기를 키워 대부분 문서가 단일 호출로 처리되도록 유도.
# 청크 간 비결정성(같은 단어가 청크별로 다른 교정 받는 문제)을 원천 차단.
# Gemini 3.1 Flash Lite는 1M+ 컨텍스트, 64K 출력 토큰을 지원하므로 안전.
AI_CHUNK_TYPO   = 15000  # 오탈자 모드 — DOC_WARN_CHARS와 동일, 대부분 단일 청크
AI_CHUNK_POLISH = 8000   # 윤문 모드 — 문장 단위 분석 비용이 커서 더 작게
AI_CALL_DELAY   = 4.1    # 15 RPM (분당 15회) 제한 우회를 위해 4.1초로 설정
# 청크 호출 타임아웃(ms). 네트워크/API가 멈추면 워커가 영원히 블록(앱 프리즈)되는 것을
#   방지한다 — 정상 호출은 보통 6~10초라 120초는 매우 여유. 초과 시 해당 청크만 오류로
#   건너뛰고(_call_and_parse가 잡아 처리) 분석은 계속된다.
AI_REQUEST_TIMEOUT = 120_000

# 생성 출력 상한(토큰) — '글로서리 지침 × 반복 표기 문서'에서 greedy 디코딩(temp 0)이
#   동일 JSON 항목을 무한 반복 생성하다 서버 504(DEADLINE_EXCEEDED)로 죽는 폭주를 절단한다
#   (30.hwp 청크 3·9·17 재현 실측 2026-07-07: 상한 없으면 90초+ 504, 상한 걸면 수 초 내
#   MAX_TOKENS 반환). 절단돼도 _parse_json_response의 salvage가 온전한 항목을 회수하고
#   반복 항목은 dedup되므로 데이터 손실 없음. 정상 응답은 실측 0.4~2K자라 여유 충분.
#   ⚠ 디코딩 자체는 바꾸지 않음(절단만) — response_schema류 재현성 문제와 무관.
AI_MAX_OUT_TYPO   = 8192    # 오탈자 모드
AI_MAX_OUT_POLISH = 16384   # 윤문 모드 — 문장 단위 원문/교정 쌍이라 더 크게

# 문서 경고
DOC_WARN_CHARS = 15_000  # 이 글자 수 초과 시 소요 시간 경고


# ══════════════════════════════════════════════════════
# ▌Correction 데이터 클래스
# ══════════════════════════════════════════════════════

@dataclass
class Correction:
    """교정 항목 단일 데이터 구조.

    source 값:
      "ai_typo"   — AI 오탈자/띄어쓰기
      "ai_polish" — AI 윤문
      "dict"      — 사전검증 기본 (legacy/fallback)
    """
    original:   str
    corrected:  str
    reason:     str = ""
    source:     str = "dict"
    color:      int = HL_DICT
    category:   str = ""
    confidence: str = "high"
    # 띄어쓰기 일관성 '통일' 카드 마커 — 방향(소수→다수)이 옳고 그름이 아닌 편집
    #   선택이라, 검수 패널이 이 카드의 수락/거절을 '문서 전체를 어느 표기로 통일할지'
    #   로 해석한다(거절 = 반대 방향 교정을 즉시 합성·수락 = 원문 표기로 통일).
    #   생성처: 워커 [7] find_compound_spacing_consistency 소비부만. ⚠ 규범 교정
    #   (norm_map·eomun 등)에 켜지 말 것 — 비표준 표기로의 통일을 조장하게 된다.
    consistency_flip: bool = False
    # 적용에서 제외할 등장(occurrence) 인덱스(문서 등장 순, 0-based).
    # 비어 있으면 모든 등장을 치환(기존 동작). 일부만 채우면 부분 거절.
    skip_occurrences: list = field(default_factory=list)

    def is_valid(self) -> bool:
        return bool(self.original.strip()) and self.original != self.corrected
