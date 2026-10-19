# -*- coding: utf-8 -*-
"""
core/doc_analysis.py — 문서 단위 공유 형태소 분석 (문맥 읽기의 단일 출처)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
원고를 **블록**(문장을 이어 붙인 120자 이하 덩어리)으로 나눠 블록마다 한 번만 분석하고, 문맥이
필요한 모든 계층이 그 읽기를 함께 쓴다. 예전엔 계층마다 등장 앞뒤 ±48자 창을 따로 잘라 분석했다 —
같은 어절을 계층마다 다르게 읽을 수 있었고(창 경계가 낱말 한가운데를 자른다), 같은 문장을 여러 번
분석했다.

  · ★블록은 **앞뒤로 48자 이상 문맥을 덧붙여**(공백 경계까지 넓혀) 읽는다. 블록만 떼어 읽으면 안 된다 —
    kiwi는 입력의 처음·끝에 붙은 낱말을 다르게 읽는다(2026-10-19 코퍼스: 줄 끝 '갈월동'→갈/VV+월동,
    줄 머리 인용 저자명(성+이름 3음절)→성/NNP+이름 음절 쪼갬, 한 줄짜리 표 칸 '②가사간병방문지원'→가/JKS+사/VV…로 읽혀
    띄어쓰기 오탐 6장이 새로 났다). 예전 창은 줄을 넘어 이웃 글자를 문맥으로 줬기에 맞게 읽었다.
  · 문맥 읽기(정본) = 덧붙인 범위의 `top_n=1` 분석. ⚠ `top_n=5`의 1순위는 `top_n=1`과 **다를 수 있다**
    (탐색 폭이 달라 실측 3.4% 단위에서 갈림 — 2026-10-19, 4원고 12,842단위). 그래서 읽기는 항상
    `top_n=1`로 정하고, N-best는 아래 '근접 대안' 확인에만 쓴다.
  · 근접 대안(N-best) = 1순위와 점수 차가 `TIE_DELTA` 이내인 다른 분석. kiwi가 거의 같은 확신으로
    다르게 읽을 수 있다는 뜻이라, **읽기에 기대는 high 판정**은 근접 대안에서도 같은 판정이 날 때만
    high로 둔다(morph의 `*_near_tie` 판정들 — 판정이 갈리면 지우지 않고 low로 내린다).
  · 오타 채널 분석 = 같은 단위를 출판 원고용 오타 모델로 다시 읽은 것(core/realword.py).

**무엇이 여기를 쓰나** — 원고 속 '그 자리'를 문맥으로 읽는 판정만이다:
  morph._context_failing_cuts(띄어쓰기 finder의 문맥 재확인) · verb_inflection_lemma ·
  noun_josa_split(규범표기 동형이의 가드) · 보조용언 '-어야 하다' 문맥 확인 · realword 오타 채널.
⚠ 어절 단독 분석(strip_josa·is_known_form·finder의 1차 제안 등)은 여기로 옮기지 않는다 — 그 함수들은
  '이 문자열은 무엇인가'를 묻고, 원고에 없는 문자열(교정문·조사 뗀 base)에도 불린다.

GUI-agnostic · kiwi 미가용이면 `get()`이 None(호출부는 기존처럼 graceful).
"""
from __future__ import annotations

import bisect
import re
import threading
from collections import OrderedDict

# 블록(읽기를 공유하는 범위) 최대 길이(글자). 문장이 이보다 길면 공백에서 거의 같은 길이로 나누고,
#   짧은 문장·줄은 이 길이까지 이어 붙인다. 너무 길면 N-best 순위를 블록 다른 곳의 모호성이 차지해
#   그 자리의 근접 대안이 목록 밖으로 밀려난다.
UNIT_MAX = 120
# 블록 앞뒤로 덧붙여 읽는 문맥 폭 — 예전 ±48자 창과 같다(위 ★). 공백 경계까지 넓히되 공백이 없는
#   글(중국어 원문 등)에선 _SNAP_MAX자에서 멈춘다.
PAD = 48
_SNAP_MAX = 20
# 근접 대안을 찾을 때 뽑는 분석 수.
NBEST = 5
# 근접 대안으로 인정하는 점수 차 상한. morph._INFLECT_GAP(1.5)와 같은 눈금이다 — '세울'(세/MM+울 vs
#   세우/VV+ㄹ 0.58)·'본가구'(2.46)를 가르던 실측 경계.
TIE_DELTA = 1.5

_LINE = re.compile(r"[^\n]+")
# 문장 끝 — 종결 부호 **뒤에 공백·줄끝**이 올 때만(소수점 '2.5'·약어 'U.S.' 안쪽은 자르지 않는다).
_SENT_END = re.compile(r"[.!?。](?=\s|$)")
_SPACE = re.compile(r"\s+")


class Tok:
    """문서 좌표로 옮긴 형태소(kiwi Token의 필요한 속성만). `start`는 원고 전체 기준 위치."""
    __slots__ = ("form", "tag", "start", "len", "oov")

    def __init__(self, form, tag, start, length, oov=False):
        self.form = form
        self.tag = tag
        self.start = start
        self.len = length
        self.oov = oov

    @property
    def end(self):
        return self.start + self.len

    def key(self):
        return (self.form, self.tag, self.start, self.len)

    def __repr__(self):
        return f"{self.form}/{self.tag}@{self.start}"


def _trim(text: str, s: int, e: int):
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e) if s < e else None


def _split_long(text: str, s: int, e: int) -> list:
    """UNIT_MAX보다 긴 단위를 공백에서 거의 같은 길이로 나눈다(낱말 한가운데는 자르지 않는다)."""
    n = e - s
    k = -(-n // UNIT_MAX)
    spaces = [m.start() for m in _SPACE.finditer(text, s, e)]
    cuts = []
    for i in range(1, k):
        target = s + n * i // k
        cand = [p for p in spaces if not cuts or p > cuts[-1]]
        if not cand:
            break
        cuts.append(min(cand, key=lambda p: abs(p - target)))
    out = []
    for a, b in zip([s] + cuts, cuts + [e]):
        t = _trim(text, a, b)
        if t:
            out.append(t)
    return out


def segment(text: str) -> list:
    """원고를 문장 단위 [(start, end), …]로 나눈다(줄 → 문장 → 긴 문장은 공백에서 등분)."""
    units = []
    for lm in _LINE.finditer(text):
        ls, le = lm.start(), lm.end()
        cut = ls
        for m in _SENT_END.finditer(text, ls, le):
            piece = _trim(text, cut, m.end())
            cut = m.end()
            if piece:
                units.extend(_split_long(text, *piece) if piece[1] - piece[0] > UNIT_MAX else [piece])
        piece = _trim(text, cut, le)
        if piece:
            units.extend(_split_long(text, *piece) if piece[1] - piece[0] > UNIT_MAX else [piece])
    return units


def blocks(text: str) -> list:
    """문장 단위를 UNIT_MAX 안에서 이어 붙인 블록 [(start, end), …] — 읽기를 공유하는 범위."""
    out = []
    for s, e in segment(text):
        if out and e - out[-1][0] <= UNIT_MAX:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def padded(text: str, s: int, e: int):
    """블록 [s, e)를 앞뒤 PAD자 이상 넓힌 분석 범위 — 낱말 한가운데를 자르지 않게 공백까지 넓힌다."""
    lo = max(0, s - PAD)
    stop = max(0, lo - _SNAP_MAX)
    while lo > stop and not text[lo - 1].isspace():
        lo -= 1
    hi = min(len(text), e + PAD)
    stop = min(len(text), hi + _SNAP_MAX)
    while hi < stop and not text[hi].isspace():
        hi += 1
    return lo, hi


def _convert(toks, base: int) -> tuple:
    return tuple(Tok(t.form, t.tag, t.start + base, t.len, bool(getattr(t, "oov", False)))
                 for t in toks)


def _span(toks, start: int, end: int) -> list:
    """[start, end) 안에서 **시작하는** 형태소 — 예전 창 분석의 스팬 기준과 같다."""
    return [t for t in toks if start <= t.start < end]


class DocAnalysis:
    """한 원고의 블록과 그 분석 캐시. `get(text)`로 얻는다(직접 만들지 말 것)."""

    _CACHE_MAX = 8192

    def __init__(self, text: str, kiwi):
        self.text = text
        self._kiwi = kiwi
        self.blocks = blocks(text)
        self.spans = [padded(text, s, e) for s, e in self.blocks]
        self._starts = [b[0] for b in self.blocks]
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    # ── 캐시 ─────────────────────────────────────────────
    def _cached(self, key, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        val = fn()
        with self._lock:
            self._cache[key] = val
            if len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)
        return val

    # ── 블록 ─────────────────────────────────────────────
    def block_index(self, start: int, end: int):
        """[start, end)를 통째로 담는 블록의 번호(없으면 None — 블록 경계를 가로지른다)."""
        i = bisect.bisect_right(self._starts, start) - 1
        if i >= 0 and self.blocks[i][1] >= end:
            return i
        return None

    # ── 분석 ─────────────────────────────────────────────
    def _run(self, lo: int, hi: int, **kw):
        """원고 [lo, hi)를 분석해 [(Tok 튜플, 점수), …]로(실패하면 [])."""
        try:
            res = self._kiwi.analyze(self.text[lo:hi], **kw)
        except Exception:
            return []
        return [(_convert(toks, lo), score) for toks, score in res]

    def _results(self, start: int, end: int, top_n: int = 1, blocklist=None):
        """[start, end)가 든 블록의 (덧붙인 범위) 분석. 블록을 가로지르면 그 자리 중심의 창으로."""
        i = self.block_index(start, end)
        if i is not None:
            lo, hi = self.spans[i]
        else:
            lo, hi = padded(self.text, start, end)
        key = (lo, hi, top_n, tuple(blocklist or ()))
        kw = {"top_n": top_n}
        if blocklist:
            kw["blocklist"] = list(blocklist)
        return self._cached(key, lambda: self._run(lo, hi, **kw))

    def context_tokens(self, start: int, end: int, blocklist=None):
        """[start, end)를 담은 문맥의 1순위 형태소 전체(문서 좌표). 실패하면 None."""
        res = self._results(start, end, 1, blocklist)
        return list(res[0][0]) if res else None

    def reading(self, start: int, end: int, blocklist=None):
        """[start, end) 자리의 1순위 읽기(그 안에서 시작하는 형태소들). 실패하면 None."""
        toks = self.context_tokens(start, end, blocklist)
        return None if toks is None else _span(toks, start, end)

    def readings(self, start: int, end: int, delta: float = TIE_DELTA) -> list:
        """[start, end) 자리의 읽기들 — 1순위 + 점수 차 `delta` 이내 근접 대안(서로 다른 것만).

        첫 원소는 항상 1순위 읽기(`reading`과 같다). 근접 대안의 기준점은 1순위와 N-best 중
        더 높은 점수다(탐색 폭이 넓은 N-best가 1순위보다 높은 점수를 찾기도 한다 — 그것도 대안이다).
        """
        best = self._results(start, end, 1)
        if not best:
            return []
        first = _span(best[0][0], start, end)
        out, seen = [first], {tuple(t.key() for t in first)}
        alts = self._results(start, end, NBEST)
        ref = max([best[0][1]] + [sc for _t, sc in alts])
        for toks, sc in alts:
            if ref - sc > delta:
                continue
            sp = _span(toks, start, end)
            k = tuple(t.key() for t in sp)
            if k not in seen:
                seen.add(k)
                out.append(sp)
        return out

    def block_analysis(self, i: int, typos=None, cost_threshold: float = 2.5):
        """블록 i(덧붙인 범위)의 1순위 (Tok 튜플, 점수, 오타 교정 형태소 key 집합). 실패하면 None.

        typos를 주면 그 오타 모델로 읽는다(core/realword.py 오타 채널). 오타 교정 형태소 = kiwi가
        typo_cost > 0으로 바꿔 읽은 형태소 — Tok에는 typo_cost를 싣지 않으므로(다른 소비자는 필요
        없다) key 집합으로 따로 돌려준다. typos=None이면 집합은 비고 결과는 reading과 같은 분석이다.
        """
        lo, hi = self.spans[i]
        if typos is None:
            res = self._results(*self.blocks[i], 1)
            return (res[0][0], res[0][1], frozenset()) if res else None
        key = ("typo", lo, hi, id(typos), cost_threshold)

        def run():
            try:
                res = self._kiwi.analyze(self.text[lo:hi], top_n=1, typos=typos,
                                         typo_cost_threshold=cost_threshold)
            except Exception:
                return None
            if not res:
                return None
            toks, score = res[0]
            conv = _convert(toks, lo)
            fixed = frozenset(c.key() for c, t in zip(conv, toks)
                              if getattr(t, "typo_cost", 0) > 0)
            return conv, score, fixed
        return self._cached(key, run)

    def join(self, toks) -> str:
        """한 어절 안의 형태소들을 **공백 없이** 표면 문자열로 되돌린다(kiwi.join). 실패하면 ''.

        ⚠ (형태, 품사)만 넘기면 kiwi가 띄어쓰기 규칙으로 공백을 넣는다('복지/NNG 재정/NNG' →
          '복지 재정'). 셋째 값 False(왼쪽 띄어쓰기 없음)로 붙인다.
        """
        try:
            return self._kiwi.join([(t.form, t.tag, False) for t in toks])
        except Exception:
            return ""


# ── 원고별 등록소 ─────────────────────────────────────────
#   워커는 한 원고(text)를 여러 계층에 넘긴다 — 같은 문자열이면 같은 분석을 돌려준다. 골드셋처럼
#   문장마다 다른 text로 부르는 경우를 위해 최근 몇 개만 기억한다.
_REG: "OrderedDict[str, DocAnalysis]" = OrderedDict()
_REG_MAX = 4
_REG_LOCK = threading.Lock()


def get(text: str):
    """text의 공유 분석(DocAnalysis). kiwi 미가용·빈 문자열이면 None."""
    if not text:
        return None
    from . import morph
    kiwi = morph._get_kiwi()
    if kiwi is None:
        return None
    with _REG_LOCK:
        da = _REG.get(text)
        if da is not None:
            _REG.move_to_end(text)
            return da
    da = DocAnalysis(text, kiwi)
    with _REG_LOCK:
        _REG[text] = da
        _REG.move_to_end(text)
        while len(_REG) > _REG_MAX:
            _REG.popitem(last=False)
    return da
