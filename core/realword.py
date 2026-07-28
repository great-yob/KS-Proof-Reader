# -*- coding: utf-8 -*-
"""
core/realword.py — 실단어 오류(real-word error) 후보 탐지 (결정론)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
오타가 하필 **사전 등재어**에 떨어져 사전 스크리닝이 원리적으로 못 잡는 부류를 찾는다.
  '목적 외 사용으로 부고 있다'  → 보고   ('부고'·'보고' 둘 다 등재어)
  '유형정비안 재시'            → 제시
  '확용하는 방향을 제시한다'     → 활용하는

**신호는 kiwipiepy 내장 언어모델의 문맥 점수**다(추가 의존성·네트워크 0, 결정론).
    gap = score(치환문) − score(원문)
치환하는 편이 문맥상 훨씬 자연스러우면 gap이 크다.

⚠⚠ **이 모듈은 '거리 기반 추측 치환'이 아니다 — 그 선을 넘지 말 것.**
    [[det-layer-phase-d-and-bareun]]의 BK-tree 추측 치환과 과거 consistency Case B는
    **영구 금지**다. 둘과 이 모듈을 가르는 것은 오직 다음 네 가지이며, 전부 불변식이다:
      ① 자동 적용 절대 금지          ② confidence는 **항상 low**
      ③ 검수 카드로만 노출            ④ AI 검증을 통과한 것만 내보냄
    ②를 어기는 순간(=high를 달거나 auto_apply 경로에 태우는 순간) 이 모듈은 금지된 그것이
    된다. 신뢰도를 올리자는 제안은 반드시 거절할 것.

**적용 범위는 '1회성' 실단어 오류로 한정한다(실측 근거).** 실파일 6쌍(원본↔교정본) 대조에서
현행이 고친 '같은 길이·거리1' 오류 129건을 분해하니 **59%가 반복 오타**였다(키메세지 18회,
리플렛 12회…). 반복 오타는 이 모듈의 희소성 전제를 위반하지만 **현행 파이프라인이 이미 전부
잡고 있어** 여기서 다룰 이유가 없다. 나머지는 미등재어(17%, 사전 몫)·조사/외래어 규범(19%,
각 전용 레이어 몫)이다. 즉 이 모듈은 **대체재가 아니라 보완재**다.

**실측(2026-07-28, 실파일 12건·178만자, 후보 130건 수동 판정 + 교정본 6쌍 대조)**
  · 문서당 카드 1.6건 · 정밀도 95%(초고 원고 100%) — AI 검증 결합 기준
  · 검증 없이는 정밀도 36%라 **AI 검증은 선택이 아니라 필수 구성요소**다
  · 현행 전체 경로(사전+AI)를 통과하고 교정본에까지 살아남은 오타 5건을 이 경로가 잡았다
    ('부고'는 청커 격자 10설정 전부 미탐이 독립 확인된 건)

**설계 확정 근거(전부 실측으로 걸러낸 것 — 되돌리지 말 것)**
  · **같은 길이 치환 편집만.** 삭제/삽입을 허용하면 오탐이 전량 유입된다(업무량→업무,
    입국월→입국, 미연동→연동…). kiwi가 등재 표제어를 선호해 희소 합성어는 구조적으로
    점수가 낮기 때문이다.
  · **빈출 후보는 '형태소 표제어' 빈도로 센다.** 표면 어절로 세면 '제시'가 1회로 보여
    ('제시하였다'·'제시된'이 따로 세어짐) 정답이 후보에 들어오지 못한다.
  · **'희소어 기괴분석'(내용어 없이 기능형태소로만 분해) 신호는 기각.** 진짜 오류도
    정상 분석되는 경우가 많아 오히려 정답을 걸러낸다.
  · **온용어(전문용어 120만) 제외 가드도 기각.** 오탐 65%를 거르지만 진짜 오류의 33%
    ('부고'·'재시'·'일련')도 함께 거른다 — 표적을 잃는다.
"""
from __future__ import annotations

import re
import collections
from typing import Callable, Optional

from .models import (
    REALWORD_GAP_MIN,
    REALWORD_RARE_MAX,
    REALWORD_COMMON_MIN,
    REALWORD_TOP_PER_WORD,
    REALWORD_MAX_CANDIDATES,
    REALWORD_MIN_LEN,
)

# 한글 런 어절 — 저장소 전역 관례(붙임형/띄어쓴형/검수 패널이 공유하는 기준).
#   [[occurrence-count-stem-boundary]]: 이 셋은 같은 기준을 써야 한다.
_HANGUL = re.compile(r"[가-힣]+")
# 문맥 창 — 문장 부호/줄바꿈으로 끊는다. 너무 짧으면 언어모델이 판단할 근거가 없고,
#   너무 길면 점수차가 희석돼 신호가 죽는다(실측 4~200자가 적정).
_SENT = re.compile(r"[^\n.!?。]{4,200}")
# 빈도를 셀 때 인정할 내용어 품사 — 조사·어미까지 세면 신호가 묻힌다.
_CONTENT = ("NNG", "NNP", "VV", "VA", "XR", "SL")


def _sub1(a: str, b: str) -> bool:
    """같은 길이 · 정확히 한 글자만 다른가(치환 편집 1)."""
    return len(a) == len(b) and sum(1 for x, y in zip(a, b) if x != y) == 1


def available() -> bool:
    """kiwi 언어모델을 쓸 수 있는가. 없으면 이 기능은 조용히 꺼진다."""
    try:
        from . import morph
        return morph.available()
    except Exception:
        return False


def find_candidates(text: str,
                    stop_event=None,
                    logger: Optional[Callable[[str], None]] = None) -> list:
    """실단어 오류 후보를 찾는다.

    Returns:
        [{"original": 오타 어절, "corrected": 제안, "gap": 점수차, "context": 문맥 문장}, …]
        gap 내림차순. **AI 검증 전이라 이대로 카드로 내보내면 안 된다**(정밀도 36%).

    kiwi가 없으면 빈 리스트(graceful no-op) — 형태소 분석 없이는 신호 자체가 없다.
    """
    def log(msg):
        if logger:
            logger(msg)

    if not text or not available():
        return []
    try:
        from .morph import _get_kiwi
        kiwi = _get_kiwi()
    except Exception:
        return []
    if kiwi is None:
        return []

    # ── 1. 빈도 집계 ────────────────────────────────────────────────
    surf = collections.Counter(e for e in _HANGUL.findall(text)
                               if len(e) >= REALWORD_MIN_LEN)
    if not surf:
        return []
    lemma = collections.Counter()
    try:
        for tok in kiwi.tokenize(text):
            if tok.tag in _CONTENT and len(tok.form) >= REALWORD_MIN_LEN:
                lemma[tok.form] += 1
    except Exception as e:
        log(f"  [실단어] 형태소 빈도 집계 실패 — 스킵: {e}")
        return []
    if stop_event is not None and stop_event.is_set():
        return []

    # ── 2. 후보 어절의 문맥 확보(첫 등장) ───────────────────────────
    ctx = {}
    for m in _SENT.finditer(text):
        s = m.group(0).strip()
        if len(s) < 6:
            continue
        for e in _HANGUL.findall(s):
            ctx.setdefault(e, s)

    # 빈출 집합 — 표면·표제어 중 하나라도 자주 나오면 '정상 표기'로 본다.
    common = ({w for w, n in lemma.items() if n >= REALWORD_COMMON_MIN} |
              {w for w, n in surf.items() if n >= REALWORD_COMMON_MIN})
    by_len = collections.defaultdict(list)
    for w in common:
        by_len[len(w)].append(w)

    # ── 3. 문맥 점수차 계산 ────────────────────────────────────────
    cache: dict = {}

    def score(s: str) -> float:
        if s not in cache:
            try:
                r = kiwi.analyze(s, top_n=1)
                cache[s] = r[0][1] if r else float("-inf")
            except Exception:
                cache[s] = float("-inf")
        return cache[s]

    rows = []
    for w, n in surf.items():
        if n > REALWORD_RARE_MAX or w not in ctx:
            continue
        if stop_event is not None and stop_event.is_set():
            return []
        s = ctx[w]
        base = score(s)
        if base == float("-inf"):
            continue
        for c in by_len.get(len(w), ()):
            if c == w or not _sub1(w, c):
                continue
            # 어절 경계를 지켜 치환 — 부분 문자열을 건드리면 엉뚱한 문장이 된다.
            s2 = re.sub(rf"(?<![가-힣]){re.escape(w)}(?![가-힣])", c, s, count=1)
            if s2 == s:
                continue
            gap = score(s2) - base
            if gap >= REALWORD_GAP_MIN:
                rows.append({"original": w, "corrected": c,
                             "gap": round(gap, 1), "context": s})

    rows.sort(key=lambda r: -r["gap"])

    # ── 4. 희소어당 상위 N개만 ─────────────────────────────────────
    #   1개로 줄이면 정답이 2등인 사례를 잃는다(실측: '재시'의 1등은 오답 '역시',
    #   정답 '제시'는 2등). 3개 이상은 AI 검증 부담만 늘고 이득이 없었다.
    seen = collections.Counter()
    out = []
    for r in rows:
        if seen[r["original"]] >= REALWORD_TOP_PER_WORD:
            continue
        seen[r["original"]] += 1
        out.append(r)
        if len(out) >= REALWORD_MAX_CANDIDATES:
            log(f"  [실단어] 후보 상한 {REALWORD_MAX_CANDIDATES}건 도달 — 이후 절단")
            break
    return out
