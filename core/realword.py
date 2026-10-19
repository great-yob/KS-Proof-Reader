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

**후보 경로는 둘이다** — 둘 다 같은 불변식(①~④) 아래에서 AI 검증에 함께 실린다.
  A. 빈출어 치환(위 설계) — 희소 어절을 문서 빈출어로 바꿔 문맥 점수가 오르는가.
  B. **오타 채널(잡음 채널)** — kiwi에 출판 원고의 타자 오류 모양(models.REALWORD_TYPO_PAIRS —
     두벌식 이웃 키·소리 혼동 자모 쌍)을 꽂아 원고를 다시 읽고(TypoTransformer), 오타로 고쳐 읽는
     편이 그대로 읽는 것보다 훨씬 자연스러운 자리를 후보로 낸다(Kernighan·Church·Gale 1990의
     잡음 채널 교정 — P(의도한 문장)·P(실제 친 글자 | 의도)). A와 달리 **문서에 정답형이 없어도**
     후보가 나오고, 교정 방향을 언어모델이 정한다 — A는 '영육'을 문서 빈출어 '교육'으로 짚어 AI가
     기각했는데(방향 오답) B는 '양육'을 낸다. 블록 읽기는 core.doc_analysis 공유 분석을 쓴다.
     같은 길이 치환·희소성·어절당 하나 — A의 설계 근거(위)를 그대로 따른다.
     실측(2026-10-19, 20원고 269만 자): 다른 계층이 다룬 어절을 뺀 B 후보 9건 중 실제 오타 5건
     ('협의자'→'혐의자'·'제제를'→'제재를' 등 — 전부 현행 파이프라인이 놓치던 것), AI 검증이 정확히
     그 5건만 통과. 상세는 docs/pipeline-reference.md 6.4절.
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
    REALWORD_TYPO_PAIRS,
    REALWORD_TYPO_COST,
    REALWORD_TYPO_COST_MAX,
    REALWORD_TYPO_GAP_MIN,
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
    picked = []
    for r in rows:
        if seen[r["original"]] >= REALWORD_TOP_PER_WORD:
            continue
        seen[r["original"]] += 1
        picked.append(r)

    # ── 5. 오타 채널(경로 B) — 경로 A의 상위 N 경쟁에 넣지 않는다 ────────────
    #   두 경로의 gap은 눈금이 다르다(B는 오타 비용이 점수에 들어 있다). 섞어 줄 세우면 A의 오답
    #   ('영육→교육')이 B의 정답('양육')을 자리에서 밀어낸다. B는 어절당 하나라 따로 붙인다.
    have = {(r["original"], r["corrected"]) for r in picked}
    for r in _typo_channel_rows(text, surf, stop_event, log):
        if (r["original"], r["corrected"]) not in have:
            have.add((r["original"], r["corrected"]))
            picked.append(r)
    if stop_event is not None and stop_event.is_set():
        return []

    out = picked[:REALWORD_MAX_CANDIDATES]
    if len(picked) > REALWORD_MAX_CANDIDATES:
        log(f"  [실단어] 후보 상한 {REALWORD_MAX_CANDIDATES}건 도달 — 이후 절단")
    return out


# ── 오타 채널(경로 B) ───────────────────────────────────────────────────
_TYPO_TF = None
# 개조식 '-ㅁ/-음' 어미의 kiwi 형태(받침 ㅁ은 종성 자모로 나온다).
_NOMINAL_ENDINGS = frozenset({"ᆷ", "음"})


def _typo_transformer():
    """models.REALWORD_TYPO_PAIRS로 만든 kiwi TypoTransformer(양방향, 1회 생성 후 재사용)."""
    global _TYPO_TF
    if _TYPO_TF is None:
        from kiwipiepy import TypoTransformer, TypoDefinition
        defs = []
        for a, b, where in REALWORD_TYPO_PAIRS:
            # 받침은 '\'로 이스케이프해야 종성으로 본다(kiwipiepy TypoDefinition 규약).
            x, y = ("\\" + a, "\\" + b) if where == "coda" else (a, b)
            defs += [TypoDefinition([x], [y], REALWORD_TYPO_COST),
                     TypoDefinition([y], [x], REALWORD_TYPO_COST)]
        _TYPO_TF = TypoTransformer(defs)
    return _TYPO_TF


def _typo_channel_rows(text: str, surf, stop_event, log) -> list:
    """오타 채널 후보 — 블록마다 오타 모델 읽기와 그대로 읽기의 점수차가 문턱 이상인 자리.

    후보 = 오타로 고쳐 읽힌 형태소가 든 **희소 한글 런** 하나. 교정형은 그 런의 형태소를 공백 없이
    다시 붙인 것(kiwi.join — '대응학'→대응/하/ᆯ→'대응할'). 경로 A와 같은 설계 가드:
      · 같은 길이 치환만(삽입·삭제 금지 — 실측 오탐 전량) · 바뀐 글자는 오타 교정 형태소 자리 안에만
      · 희소(REALWORD_RARE_MAX 이하 등장) · 최소 길이 · 순한글
    실패·kiwi 없음이면 [](graceful).
    """
    try:
        from . import doc_analysis
        doc = doc_analysis.get(text)
        typos = _typo_transformer()
    except Exception as e:
        log(f"  [실단어] 오타 채널 준비 실패 — 스킵: {e}")
        return []
    if doc is None:
        return []
    out = []
    for i, (bs, be) in enumerate(doc.blocks):
        if stop_event is not None and stop_event.is_set():
            return []
        runs = [m for m in _HANGUL.finditer(text, bs, be)
                if len(m.group()) >= REALWORD_MIN_LEN and surf.get(m.group(), 0) <= REALWORD_RARE_MAX]
        if not runs:
            continue
        tr = doc.block_analysis(i, typos=typos, cost_threshold=REALWORD_TYPO_COST_MAX)
        if not tr or not tr[2]:
            continue
        toks, score, fixed = tr
        base = doc.block_analysis(i)
        if not base:
            continue
        gap = score - base[1]
        if gap < REALWORD_TYPO_GAP_MIN:
            continue
        for m in runs:
            rs, re_ = m.span()
            hit = [k for k in fixed if rs <= k[2] < re_]
            if not hit:
                continue
            w = m.group()
            rtoks = [t for t in toks if rs <= t.start < re_]
            if not rtoks or rtoks[0].start != rs or rtoks[-1].end != re_:
                continue                   # 런 경계와 형태소 경계가 어긋남 — 다시 붙일 수 없다
            cand = doc.join(rtoks)
            if (len(cand) != len(w) or cand == w or not _HANGUL.fullmatch(cand)):
                continue
            allowed = set()
            for _f, _t, s, n in hit:
                allowed.update(range(s - rs, s - rs + n))
            changed = {p for p, (a, b) in enumerate(zip(w, cand)) if a != b}
            if changed - allowed:
                continue
            # 그대로 읽은 결과로 거른다 — 2026-10-19 코퍼스(20원고)에서 오탐이 몰린 두 모양:
            btoks = [t for t in base[0] if rs <= t.start < re_]
            #   ① 바뀐 글자가 **'-ㅁ/-음' 어미** 자리 — 개조식 문체의 '-함/-됨/-음'('고려함'·'작성함'·
            #      '개최됨'·'쉬움')을 받침 ㅁ↔ㄴ 규칙이 관형형('-한')으로 끌어당긴다. 점수차 3.5~5 구간 후보
            #      9건 중 8건이 이 모양이었다. kiwi는 이 어미를 문장 끝이면 EF, 아니면 ETN으로 읽어 둘 다 본다.
            #      수용한 미탐: 진짜 '-ㅁ/-ㄴ' 오타('살펴봄 결과'→'살펴본').
            if any(t.tag in ("ETN", "EF") and t.form in _NOMINAL_ENDINGS
                   and (t.start - rs) in changed for t in btoks):
                continue
            #   ② 런의 내용 형태소가 **전부 고유명사** — 인명(인용 저자 — 가명 '조보영과'=조보영/NNP+과)·음역어('양탄일성
            #      (两弹一星)'). kiwi가 고유명사로 확신하는 낱말을 흔한 낱말로 끌어당긴 것이다. 일부만
            #      고유명사인 오타('괌심'=괌/NNP+심/NNG → 관심)는 남는다.
            content = [t for t in btoks if t.tag[0] in "NVX" and not t.tag.startswith("XS")]
            if content and all(t.tag == "NNP" for t in content):
                continue
            out.append({"original": w, "corrected": cand, "gap": round(gap, 1),
                        "context": text[bs:be]})
    out.sort(key=lambda r: -r["gap"])
    return out
