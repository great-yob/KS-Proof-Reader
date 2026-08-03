# -*- coding: utf-8 -*-
"""이음매(junction) 단위 띄어쓰기 정합 — 워커 [9.6].

**무엇을 고치는가.** 띄어쓰기 카드는 낱말 하나에 대한 이진(수락/거절) 판정이다.
그런데 낱말이 서로 중첩되면('수당수급자' ⊂ '수당수급자확인서') 같은 이음매를 두
카드가 **독립적으로 반대로** 결정할 수 있고, 앞 카드의 결정이 뒤 카드에 배선되지
않는다(사용자 보고 2026-07-30, 복지지갑 연구 .hwp).

실측(보고서 4종·62.9만 자, 글자불변 띄어쓰기 카드 630건):
  · 중첩 이음매 충돌 **22쌍**, 그중 **20쌍이 자동적용(high) 카드 관여** — 즉 대부분은
    사용자가 카드조차 보지 못한 채 표기가 갈렸다.
  · '수급자확인서'는 문서가 10:1로 띄어 쓰는데, 기호뒤 finder가 만든
    '급여(현금)수급자확인서' 카드는 그 근거를 보지 못해 붙임을 유지한다.

**왜 자동 해결이 안 되는가(실측).** 충돌의 절반은 결함이 아니라 **정당한 구분**이다 —
'출산 전후'(44:4 띄움) vs '출산전후휴가'(3:1 붙임, 고용보험법 용어), '개인정보보호'
(12:2) vs '개인정보 보호법'(3:2, 법률 공식 명칭), '판매 종사자'(5:3) vs
'서비스판매종사자'(13:2, 한국표준직업분류). 한글 맞춤법 제49·50항이 고유명사·전문
용어의 붙여 쓰기를 명시 허용한다. 그리고 이 둘을 가를 자동 신호가 없다:
  · **사전 등재 여부 실패** — '출산전후휴가'·'서비스판매종사자'·'아파트매매가격지수'
    전부 미등재로, 결함 쪽('수급자확인서'·'중복관리체계')과 구별되지 않는다.
  · **이음매 빈도 합산 실패** — 출산|전후는 띄움 45:7이라 '출산전후휴가'를
    '출산 전후 휴가'로 만든다(오답).

**그래서 우선순위 계층을 둔다(사용자 결정 2026-07-30).**

    1순위  낱말 자체의 문서 내 다수 표기   ← 저자가 확립한 용어. **이음매보다 강하다**
    2순위  이음매의 문서 내 다수 표기      ← 낱말 자체 근거가 없을 때만
    3순위  규범 기본값(미등재 → 띄어쓰기 원칙)

이는 이 앱의 원래 지침("복합명사는 옳고 그름을 따지지 말고 저자가 더 자주 쓴 표기로
통일만")을 이음매 계층까지 확장한 것이다. 충돌 판정은 이 계층에서 자동으로 나온다:

    양쪽 낱말 근거가 다 명확   → **정당한 구분**. 손대지 않는다(카드 보존).
    한쪽만 명확(상대는 근거 0) → **자동 정합**. 근거 있는 쪽 이음매로 corrected 재작성.
    그 밖(약함·동률)          → **이음매 그룹**. 사용자가 1회 결정하고 그룹 전체 전파.

⚠ **불변식 3종(깨면 적용이 실패하거나 문서가 오염된다).**
  (1) `original`은 절대 바꾸지 않는다 — 문서에 실재하는 문자열이어야 하고, 워커 [9.5]
      문서 대조 게이트가 이미 그것을 보장했다. 이 패스는 `corrected`만 만진다.
  (2) 글자를 바꾸지 않는다 — 공백만 가감(환각 0). 그래서 규범 교정 역전
      ('콘텐츠'→'컨텐츠') 위험이 구조적으로 없고, `consistency_flip` 게이트에 묶지
      않아도 안전하다(기호뒤·분리 finder 카드까지 커버해야 하므로 중요).
  (3) 자동 정합은 **상대 낱말에 자체 근거가 전혀 없을 때만** 한다. 근소차라도 근거가
      갈리면 사용자에게 넘긴다 — '개인정보 보호법'(3:2)을 빈도로 눌러선 안 된다.

**낱말 근거는 '한글 런' 기준이다** — 붙임형(런), 띄어쓴형(단일 공백 + 어절 경계),
검수 패널 등장 필터가 모두 같은 기준을 쓴다([[occurrence-count-stem-boundary]] 확장⑤).
하나를 바꾸면 셋을 함께 볼 것.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache

from core.models import Correction, HL_TYPO

# 낱말 후보 길이 — morph.find_compound_spacing_consistency와 같은 창을 쓴다.
MIN_LEN = 4
MAX_LEN = 14
# 띄어쓴 변이를 몇 조각까지 관찰하는가. 2조각은 기존 finder가 이미 보고, 3~4조각이
#   이 패스가 새로 보는 영역이다('기초연금 수급자 확인서' 9회 — 실측 미탐).
MAX_PARTS = 4

_HANGUL_RUN = re.compile(r"[가-힣]+")


@lru_cache(maxsize=8192)
def _josa_tail_at(eojeol: str) -> int:
    """어절에서 **조사 꼬리가 시작하는 문자 인덱스**(조사가 없으면 len)."""
    from core import morph as _morph
    base = _morph.strip_josa(eojeol)
    return len(base) if base else 0


def _starts_word(text: str, s: int, e: int) -> bool:
    """이 한글 런이 **낱말의 머리**로 쓰일 수 있는가 — 띄어쓴 변이 시퀀스의 시작 자격.

    ⚠ **한글 런은 어절이 아니다.** 앞에 공백이 없으면 그 런은 더 큰 어절의 **조각**이고,
    조각을 낱말의 머리로 삼으면 **문서에 없는 낱말**이 만들어진다(사용자 보고 2026-08-03:
    'EU AI Act의 주요내용'의 런은 ['의','주요내용'] → 키 `의주요내용` → 검수 카드
    '의 주요내용' → '의 주요 내용'. '의 위험기반'·'의 대응상황'도 같은 원인).

    다만 **모든 조각을 막으면 정당한 근거가 함께 날아간다** — 이 저장소는 괄호·가운뎃점을
    낱말 경계로 세는 것이 원칙이고(morph 확장⑤: 공백 토큰으로 세면 '보조금·핵심인재'가
    한 낱말이 돼 과소 카운트), '급여(현금)수급자 확인서'의 '수급자'는 멀쩡한 낱말이다.
    실측(실파일D 18.2만 자, 앞을 공백으로만 제한): 잡음 5건과 함께 **정당한 카드 6건이
    소실**됐다('복지민원서비스'→'복지 민원 서비스' high 포함). 그래서 조각의 정체를 가른다:

      · 앞이 **영문·숫자·한자**(isalnum)     → 항상 거부. 그 어절의 몸통이 비한글이므로
        한글 조각은 조사('Act의')든 단위 의존명사('3대'·'2024년')든 낱말의 머리가 아니다.
      · 앞이 **부호**(괄호·가운뎃점·따옴표)  → 그 런이 어절의 **조사 꼬리**일 때만 거부.
        '｢AI 추진법｣의 주요 내용'의 '의'는 거부, '(현금)수급자'의 '수급자'는 통과.
      · 앞이 **공백·문두**                    → 통과.

    실측 결과 잡음 5건이 정확히 제거되고 정당한 카드 손실은 0이었다.
    ⚠ 런의 **뒤쪽**은 일부러 보지 않는다 — 꼬리 조각은 없는 낱말을 만들지 못하고
    ('주요 내용AI'가 세는 표기도 어차피 '주요 내용') 등장 수만 스칠 뿐인데, 각주 번호가
    붙은 '주요 내용1)' 같은 정당한 근거까지 함께 날아간다.
    """
    if not s:
        return True
    prev = text[s - 1]
    if prev.isspace():
        return True
    if prev.isalnum():          # 런이 최대 매칭이라 앞 글자는 한글이 아니다
        return False
    js = s
    while js and not text[js - 1].isspace():
        js -= 1
    #   어절 끝의 부호(쉼표 등)가 strip_josa를 방해하지 않도록 런 끝까지만 본다.
    return s - js < _josa_tail_at(text[js:e])


# ── 낱말 변이 표 ───────────────────────────────────────────────────────
def build_variants(text: str, *, min_len: int = MIN_LEN, max_len: int = MAX_LEN,
                   max_parts: int = MAX_PARTS) -> dict:
    """낱말(공백 제거 키) → Counter{실제 표기: 등장 수}.

    붙임형은 **한글 런**의 조사 제거 base로 센다(공백 split 토큰으로 세면 가운뎃점·
    괄호가 낱말을 이어붙여 과소 카운트된다 — morph 확장⑤와 같은 이유).
    띄어쓴형은 **단일 공백으로 이어진 연속 한글 어절** 2~max_parts개를 이어 본다.
    마지막 조각의 조사는 strip_josa로 떼며, 이 함수는 내부 공백을 보존한다(실측).
    ⚠ 시퀀스의 **첫 런은 `_starts_word`를 통과해야 한다** — 한글 런은 어절이 아니라서
    'Act의'의 '의' 같은 꼬리 조각이 낱말의 머리가 되면 없는 낱말이 만들어진다.
    """
    from core import morph as _morph
    if not text or not _morph.available():
        return {}
    strip = _morph.strip_josa

    out: dict = defaultdict(Counter)

    # 1) 붙임형 — 유니크 런만 형태소 분석(성능).
    for run, n in Counter(_HANGUL_RUN.findall(text)).items():
        base = strip(run) or run
        if min_len <= len(base) <= max_len:
            out[base][base] += n

    # 2) 띄어쓴형 — 단일 공백으로 이어진 연속 한글 어절 시퀀스에서 부분 시퀀스 추출.
    toks = [(m.group(), m.start(), m.end()) for m in _HANGUL_RUN.finditer(text)]
    for i in range(len(toks)):
        if not _starts_word(text, toks[i][1], toks[i][2]):
            continue
        for k in range(2, max_parts + 1):
            if i + k > len(toks):
                break
            seq = toks[i:i + k]
            # 정확히 공백 1칸으로만 이어졌는가(개행·다중 공백·부호 개입 배제).
            #   ⚠ 카드 원문/브리지 RepeatFind가 'a b'(한 칸) 리터럴로 탐색·치환하므로,
            #   개행 등장을 세면 보이지도 치환되지도 않는 유령 근거가 된다.
            if any(seq[j + 1][1] - seq[j][2] != 1 or text[seq[j][2]] != " "
                   for j in range(k - 1)):
                break
            form = " ".join(t[0] for t in seq)
            base = strip(form) or form
            key = base.replace(" ", "")
            if min_len <= len(key) <= max_len and " " in base:
                out[key][base] += 1
    return dict(out)


def verdict(counter: Counter | None):
    """낱말 자체 근거 판정 → (strength, winner, top_n, second_n).

    strength: "none"  변이가 하나뿐 = 저자의 선택을 알 수 없음(이음매에 양보)
              "weak"  동률 또는 근소차 = 진짜 혼재(사용자 결정)
              "clear" 명확한 다수 = 저자가 확립한 표기(이음매보다 우선)

    ⚠ **'clear'는 격차 2 이상을 요구한다** — 워커 [7]의 카드 신뢰도 규칙
    (`close = tie or (diff <= 1 and n_min >= 2)`)보다 **일부러 더 엄격하다**. 이
    함수의 카운트와 `find_compound_spacing_consistency`의 근거 수치는 꼬리 처리
    방식이 달라 ±1~2 어긋난다(실측: finder는 마지막 어절의 조사만 검사해
    '교육 수준별'의 접미사 '별'을 정당하게 거부하지만, 여기선 형태 전체를
    strip_josa한다). 그 오차가 판정을 뒤집지 않도록 여유를 두며, 어긋나는 쪽은
    항상 'weak'(=사용자 결정)로 떨어진다 — 'clear'가 트리거하는 동작이 정당한 구분
    **보존**과 **자동 정합**이라 과신하면 조용히 오교정이 되기 때문이다.
    실측 4문서에서 보존·정합·보완에 필요한 케이스는 전부 격차 2 이상이었다
    (출산전후휴가 3:1 · 아파트전세가격지수 3:1 · 기초연금수급자확인서 9:1 등).
    """
    if not counter:
        return "none", None, 0, 0
    ranked = counter.most_common()
    if len(ranked) == 1:
        return "none", ranked[0][0], ranked[0][1], 0
    (w, n_maj), (_, n_min) = ranked[0], ranked[1]
    return ("clear" if n_maj - n_min >= 2 else "weak"), w, n_maj, n_min


# ── 이음매 좌표 ────────────────────────────────────────────────────────
def spaces_of(form: str) -> frozenset:
    """공백 제거 좌표에서 '이 인덱스 앞에 공백이 있다'의 집합."""
    out, j = set(), 0
    for ch in form:
        if ch == " ":
            if j:
                out.add(j)
        else:
            j += 1
    return frozenset(out)


def apply_spaces(word: str, spaces) -> str:
    """공백 제거 낱말 + 공백 인덱스 집합 → 표기 문자열."""
    buf = []
    for j, ch in enumerate(word):
        if j and j in spaces:
            buf.append(" ")
        buf.append(ch)
    return "".join(buf)


def _eligible(c) -> bool:
    """이 패스가 다루는 카드 — 글자 불변(공백만 가감) 결정론 띄어쓰기/부호 카드.

    AI 카드(ai_typo)는 제외한다. 실측 22쌍이 전부 결정론 finder 산출이었고, AI
    카드까지 열면 공백 외 판단이 섞인 교정을 이 패스가 재작성하게 된다.
    """
    from core import morph as _morph
    o, cr = c.original or "", c.corrected or ""
    return bool(o and cr and o != cr
                and o.replace(" ", "") == cr.replace(" ", "")
                and getattr(c, "source", "") in ("spacing", "punct")
                # URL·경로 어절은 공백 삽입 자체가 값을 파괴한다 — finder 쪽에서도
                #   막지만(morph.is_urlish), 다른 경로로 들어온 카드까지 여기서 차단한다.
                and not _morph.is_urlish(o)
                # 제로폭 문자가 섞인 어절은 kiwi가 유령 명사로 읽어 이음매 좌표 자체가
                #   어긋난다 — 같은 이유로 여기서도 2중 차단(morph.has_zero_width 주석).
                and not _morph.has_zero_width(o))


def _pure_noun(word: str) -> bool:
    """순수 명사 덩어리인가(NNG/NNP만) — 기존 finder와 같은 게이트."""
    from core import morph as _morph
    kiwi = _morph._get_kiwi()
    if kiwi is None:
        return False
    try:
        toks = kiwi.analyze(word)[0][0]
    except Exception:
        return False
    return bool(toks) and all(t.tag in ("NNG", "NNP") for t in toks)


def _rewrites(x, y) -> bool:
    """x의 교정이 y의 **교정 결과를 다시 잡아** 되돌리는가.

    브리지 apply는 원문이 긴 항목부터 문서 전체를 부분문자열로 훑는다. 그래서 x의
    `original`이 y의 `corrected` 안에 남아 있으면 x가 y의 결과를 덮어쓴다.

    ⚠ 단, **원문에 공백이 없는 '분리' 교정**은 적용 단계에서 어절 경계 필터를 받는다
    (`review_panel._mark_stem_boundary_skips` — 그 함수는 `" " in orig`이면 대상에서
    빼므로 **공백 없는 원문에만** 걸린다). 그래서 '출산전후'는 '출산전후휴가' 속
    조각으로는 치환되지 않는다 → 간섭이 아니다. 반대로 원문에 공백이 있으면 그 필터가
    없어 부분문자열이 그대로 치환된다 → 실제 간섭이다.
    이 구분이 '출산 전후 ↔ 출산전후휴가'(정당한 구분)와
    '전자문서 지갑 ↔ 전자문서지갑 기능'(상호 무효화)을 가른다.
    """
    xo, yc = x["c"].original or "", y["c"].corrected or ""
    if not xo or xo not in yc:
        return False
    if " " in xo:
        return True                     # 경계 필터 없음 → 부분문자열 치환이 그대로 발생
    for m in re.finditer(re.escape(xo), yc):
        prev = yc[m.start() - 1] if m.start() else ""
        nxt = yc[m.end()] if m.end() < len(yc) else ""
        if not ("가" <= prev <= "힣") and not ("가" <= nxt <= "힣"):
            return True                 # 독립 어절로 등장 → 실제로 치환된다
    return False


def _interferes(a, b) -> bool:
    """두 카드가 **적용 후 서로의 결과를 덮어쓰는가** — '정당한 구분'의 전제 조건.

    실측(사용자 보고 2026-07-31): '전자문서 지갑'→'전자문서지갑'(43:33, 자동 적용)과
    '전자문서지갑 기능'→'전자문서 지갑 기능'(7:5, 검수 카드)이 '정당한 구분'으로 보존됐는데,
    적용하면 `전자문서지갑 기능 → 전자문서 지갑 기능 → 전자문서지갑 기능`으로 되돌아가
    **사용자가 수락한 교정이 12곳 전량 무효화**됐다.
    """
    return _rewrites(a, b) or _rewrites(b, a)


def _pure_noun_form(form: str) -> bool:
    """이 표기가 **어절마다** 순수 명사인가."""
    parts = [p for p in (form or "").split() if p]
    return bool(parts) and all(_pure_noun(p) for p in parts)


def _noun_group(counter) -> bool:
    """이 낱말 무리가 명사 복합어인가 — **저자의 표기 중 하나라도** 순수 명사면 참.

    ⚠ 붙인 문자열 하나만 `_pure_noun`에 넣으면 **kiwi 오분석에 무리 전체가 걸린다** —
    실측 '이상거래탐지'·'이상거래' 둘 다 `이/MM + 상거래/NNG`로 오독된다(관형사 '이' +
    '상거래'). 그 한 번의 오분석 때문에
    `이상거래탐지(1) · 이상거래 탐지(10) · 이상 거래 탐지(3)` 전체가 게이트에서 탈락해
    **소수 표기 3회가 카드도 교정도 없이 미탐**으로 남았다(사용자 보고 2026-07-31).
    같은 오분석이 2분할 finder의 엉뚱한 '이 상거래탐지' 분할도 만들었다.
    저자가 **띄어 쓴 형태**는 경계가 드러나 정상 분석된다('이상 거래 탐지' = 이상+거래+탐지,
    전부 NNG). 즉 한 표기라도 순수 명사로 읽히면 그 무리는 명사 복합어로 본다.

    ⚠ 이 완화가 '구(句)를 낱말로 취급'하는 ⑩ 294 노이즈로 번지지 않는 이유: 호출부가
    **승자가 반드시 띄어쓴 형태**임을 따로 요구한다(`" " in winner`). 금지된 부류
    ('학교 밖 청소년 지원'→'학교밖청소년지원')는 승자가 붙임형이라 거기서 막힌다.
    """
    return any(_pure_noun_form(f) for f in (counter or {}))


# ── 본 패스 ───────────────────────────────────────────────────────────
def resolve(corrections: list, text: str, *, logger=None,
            exception_set=frozenset()) -> dict:
    """이음매 정합 패스. corrections를 **제자리에서** 손보고 진단 dict를 돌려준다.

    반환 키: preserved(정당한 구분 쌍) / harmonized(자동 정합 카드) /
             grouped(이음매 그룹 수) / recovered(미탐 보완 카드) / conflicts(총 충돌 쌍) /
             redirected(방향 정정 카드) / dropped(방향 오판으로 제거한 카드) /
             mutual(상호 무효화 쌍 — 전부 이음매 그룹으로 넘어가므로 grouped의 부분집합.
                    워커 요약 n에 **따로 더하지 말 것**: 같은 판정을 두 번 세게 된다)
    """
    log = logger or (lambda *_a, **_k: None)
    diag = {"conflicts": 0, "preserved": 0, "harmonized": 0,
            "grouped": 0, "recovered": 0, "redirected": 0, "dropped": 0,
            "mutual": 0}
    from core import morph as _morph
    if not text or not _morph.available():
        return diag

    variants = build_variants(text)

    # ① 미탐 보완 — 문서가 3조각 이상으로 띄어 쓰는 낱말은 기존 2분할 finder의
    #    출력 공간 밖이라 카드가 **아예 생성되지 않았다**(실측: '기초연금수급자확인서'
    #    붙임 1 : '기초연금 수급자 확인서' 9 → best_sp=None으로 그냥 지나감).
    #
    #    ⚠ **원문은 반드시 공백 없는 단일 한글 런으로 제한한다.** 즉 이 보완은
    #    '붙은 낱말을 문서 다수 표기대로 띄운다'만 하고, 그 반대(띄어 쓴 구를 붙임)는
    #    절대 하지 않는다. 초기 구현이 이 제한 없이 max_parts=4로 돌자 '방지 관리 체계
    #    개선방안'·'출산 후 경제활동 참가' 같은 **구(句)** 가 낱말로 취급되고,
    #    '학교 밖 청소년 지원'→'학교밖청소년지원'(2:1)처럼 법률 용어를 붙이는 high
    #    카드까지 나왔다(실측). 이는 이 저장소가 실패로 기록한 '복합명사 결합 자동화'
    #    (⑩ 294건 노이즈 클래스) 부류이므로 재도입 금지.
    #    ⚠⚠ **패자가 여럿이면 전부 카드로 낸다**(사용자 보고 2026-07-31). 초기 구현은
    #    붙임형(key) 하나만 냈고, 그나마 'key에 카드가 있으면' 낱말 전체를 건너뛰었다.
    #    그래서 3형태 혼재 문서에서 **소수 표기 하나가 조용히 미탐**으로 남았다:
    #    `이상거래탐지(1) · 이상거래 탐지(10) · 이상 거래 탐지(3)` 에서 1회짜리는
    #    카드가 생겼는데 **3회짜리 '이상 거래 탐지'는 카드도 교정도 없이** 방치됐다.
    #    2분할 finder는 '붙임형 base'에서만 후보를 만들므로 띄어 쓴 소수형을 원리적으로
    #    못 본다 — 여기서 메우지 않으면 아무도 못 본다.
    #    금지선은 그대로다: **승자가 반드시 띄어쓴 형태**여야 하므로(`" " in winner`)
    #    '띄어 쓴 구를 붙여 한 낱말로 만드는' ⑩ 294 노이즈 부류는 여전히 생성되지 않는다.
    #    여기서 하는 일은 **같은 낱말 안에서 공백 위치를 다수형에 맞추는 재배치**뿐이다.
    have = {(c.original or "") for c in corrections if _eligible(c)}
    for key, counter in sorted(variants.items()):
        if len(counter) < 2:
            continue
        st, winner, n_maj, n_min = verdict(counter)
        if st != "clear" or not winner or " " not in winner:
            continue          # 약함·동률은 보류 / 승자가 붙임형이면 결합이라 대상 아님
        if key in exception_set or not _noun_group(counter):
            continue
        # 2분할 변이가 있으면 기존 finder의 출력 공간 안이다(그쪽이 카드를 냈을 수도,
        #   kiwi 오분석으로 못 냈을 수도 있다 — 카드는 내되 자동 적용은 하지 않는다).
        _finder_visible = any(f.count(" ") == 1 for f in counter)
        for form, n in sorted(counter.items()):
            if form == winner or not n:
                continue
            if form in have:      # 이미 그 표기의 카드가 있으면 중복 생성 금지
                continue
            # ⚠ **자동 적용(high)은 기존에 검증된 모양에만 준다.** 이번 확장의 목적은
            #   '미탐 제거'이지 자동 적용 확대가 아니다(사용자 지적 2026-07-31).
            #   검증된 부류 = 원문이 **공백 없는 붙임형**이고 그 무리에 **2분할 변이가
            #   없어** 기존 finder가 원리적으로 못 보던 경우(예: 기초연금수급자확인서 9:1).
            #   그 밖은 전부 low — 띄어 쓴 표기의 공백 재배치도, finder가 볼 수 있었던
            #   무리도, 사람이 카드로 한 번 보고 정한다.
            joined_src = " " not in form and not _finder_visible
            corrections.append(Correction(
                original=form, corrected=winner,
                reason=(("" if joined_src else "[검수] ")
                        + f"띄어쓰기 일관성 → 다수 표기로 통일\n"
                        f"— {winner}({n_maj}) : {form}({n})"),
                source="spacing", color=HL_TYPO,
                category="띄어쓰기",
                confidence="high" if joined_src else "low",
                consistency_flip=True,
            ))
            have.add(form)
            diag["recovered"] += 1
            log(f"      · 미탐 보완 '{form}' → '{winner}' ({n_maj}:{n})")

    # ①b 방향 정정 — 카드의 교정 방향이 **그 낱말 자체의 문서 전체 분포**와 어긋나면
    #     바로잡는다. 2분할 finder는 낱말을 한 지점에서만 쪼개 보므로 3조각 다수형을
    #     못 보고 엉뚱한 방향을 낼 수 있다(실측 doc06: '중복수급관리대상' 카드는
    #     1:1 동률이라며 '중복수급관리 대상'을 제안했지만, 문서는 '중복수급 관리 대상'을
    #     **15회** 쓴다). 이 정정이 없으면 그 카드가 '정당한 구분'으로 오분류돼
    #     실제 결함이 보존된다. 1순위 계층(낱말 근거)의 단일 카드 적용이다.
    dead_ids = set()
    for c in corrections:
        if not _eligible(c):
            continue
        word = (c.original or "").replace(" ", "")
        st, winner, n_maj, n_min = verdict(variants.get(word))
        if st != "clear" or not winner or winner == c.corrected:
            continue
        if winner == c.original:
            # 문서 다수 표기가 곧 원문 — 이 카드는 방향이 거꾸로다. 고칠 것이 없다.
            dead_ids.add(id(c))
            diag["dropped"] += 1
            log(f"      · 방향 오판 카드 제거 '{c.original}'→'{c.corrected}' "
                f"(문서 다수 표기가 원문 그대로 {n_maj}회)")
            continue
        old = c.corrected
        c.corrected = winner
        # ⚠ 근거 문구에 **'그 외' 같은 뭉뚱그린 말을 쓰지 말 것**(사용자 보고 2026-07-31:
        #   '이상거래 탐지(10) : 그 외(3)'). `verdict`의 n_min은 '나머지 전부'가 아니라
        #   **2위 표기 하나의 개수**라 실제 문자열이 존재한다. 게다가 사용자가 보는 건
        #   이 카드의 원문 표기이므로, 그 표기와 그 표기의 실제 등장 수를 보여주는 게
        #   가장 정확하다(다른 일관성 카드의 '소수(n) : 다수(n)' 형식과도 일치).
        _vc = variants.get(word) or Counter()
        _loser_n = _vc.get(c.original, 0)
        if _loser_n:
            _loser, _loser_cnt = c.original, _loser_n
        else:                       # 원문 표기가 분포에 없으면 2위 표기로 폴백
            _ranked = _vc.most_common()
            _loser, _loser_cnt = (_ranked[1] if len(_ranked) > 1 else (c.original, n_min))
        c.reason = (f"띄어쓰기 일관성 → 다수 표기로 통일\n"
                    f"— {winner}({n_maj}) : {_loser}({_loser_cnt})")
        diag["redirected"] += 1
        log(f"      · 방향 정정 '{c.original}' → '{winner}' (기존 '{old}', "
            f"문서 분포 {n_maj}:{n_min})")
    if dead_ids:
        corrections[:] = [c for c in corrections if id(c) not in dead_ids]

    # ② 대상 카드 목록 + 낱말 근거 판정.
    items = []
    for c in corrections:
        if not _eligible(c):
            continue
        word = (c.original or "").replace(" ", "")
        st, winner, n_maj, n_min = verdict(variants.get(word))
        items.append({"c": c, "word": word, "sp": spaces_of(c.corrected),
                      "st": st, "winner": winner})

    # ③ 중첩 이음매 충돌 탐지 — 짧은 낱말이 긴 낱말 안에 들어가고, 겹치는 구간
    #    **내부**의 공백 결정이 다르면 두 카드를 함께 적용했을 때 같은 이음매가 갈린다.
    pairs = []
    for a in items:
        for b in items:
            if a is b or len(a["word"]) >= len(b["word"]):
                continue
            d = b["word"].find(a["word"])
            if d < 0:
                continue
            a_in = {i for i in a["sp"] if 0 < i < len(a["word"])}
            b_in = {i - d for i in b["sp"] if d < i < d + len(a["word"])}
            if a_in != b_in:
                pairs.append((a, b, d))
    diag["conflicts"] = len(pairs)
    if not pairs:
        return diag

    # ④ 계층 적용.
    adj = defaultdict(set)          # 이음매 그룹(연결 요소)용 인접 리스트
    for a, b, d in pairs:
        sa, sb = a["st"], b["st"]
        if sa == "clear" and sb == "clear":
            # ⚠ **'정당한 구분'은 두 카드가 서로를 덮어쓰지 않을 때만 성립한다.**
            #   한쪽의 원문이 다른 쪽의 **교정문 안에** 들어 있으면, 적용 단계에서
            #   나중에 도는 카드가 앞 카드의 결과를 되돌린다(브리지는 원문이 긴 것부터
            #   문서 전체를 훑는다). 실측(사용자 보고 2026-07-31):
            #     '전자문서지갑 기능'→'전자문서 지갑 기능'(7:5, 검수 카드)
            #     '전자문서 지갑'→'전자문서지갑'(43:33, 자동 적용)
            #   → 사용자가 검수 카드를 수락해도 `전자문서지갑 기능 → 전자문서 지갑 기능
            #     → 전자문서지갑 기능`으로 **완전히 되돌아간다**(12곳 전부).
            #   '출산 전후' ↔ '출산전후휴가'가 정당한 구분인 건 짧은 쪽 원문(띄어쓴 형태)이
            #   긴 쪽 표기(붙임형) 안에 없어 **서로 간섭하지 않기** 때문이다. 그 조건이
            #   깨지면 '구분'이 아니라 그냥 충돌이므로 사용자 결정으로 넘긴다.
            if not _interferes(a, b):
                diag["preserved"] += 1
                log(f"      · 정당한 구분 유지 '{a['c'].original}' ↔ "
                    f"'{b['c'].original}' (양쪽 다 문서 내 다수 표기)")
                continue
            diag["mutual"] += 1
            log(f"      · 상호 무효화 쌍 → 사용자 결정 '{a['c'].original}' ↔ "
                f"'{b['c'].original}' (한쪽이 다른 쪽 교정 결과를 되돌림)")
            adj[id(a["c"])].add(id(b["c"]))
            adj[id(b["c"])].add(id(a["c"]))
            continue
        if sa == "clear" and sb == "none":
            _harmonize(b, a, d, log, diag)
            continue
        if sb == "clear" and sa == "none":
            _harmonize_into_short(a, b, d, log, diag)
            continue
        # 근거가 약하거나 갈린다 → 사용자 결정(이음매 그룹).
        adj[id(a["c"])].add(id(b["c"]))
        adj[id(b["c"])].add(id(a["c"]))

    # ⑤ 연결 요소 → junction_group 부여 + 자동적용 방지(low 강등).
    by_id = {id(it["c"]): it for it in items}
    seen, gid = set(), 0
    for k in sorted(adj, key=lambda x: by_id[x]["word"]):
        if k in seen:
            continue
        gid += 1
        stack, members = [k], []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            members.append(cur)
            stack.extend(adj[cur] - seen)
        name = f"J{gid}"
        for m in members:
            c = by_id[m]["c"]
            c.junction_group = name
            if c.confidence != "low":
                # ⚠ 자동 적용을 막는 것이 이 강등의 목적이다. 그대로 두면 사용자가
                #   카드조차 보지 못한 채 문서 안에서 같은 이음매가 갈린다(실측 20/22쌍).
                c.confidence = "low"
            if "[검수]" not in c.reason:
                c.reason = "[검수] " + c.reason
        diag["grouped"] += 1
        log(f"      · 이음매 그룹 {name}: "
            + " · ".join(f"'{by_id[m]['c'].original}'" for m in members))

    # ⑥ 무의미해진 카드 제거(정합 결과가 원문과 같아진 것).
    dead = {id(it["c"]) for it in items if it.get("dead")}
    if dead:
        corrections[:] = [c for c in corrections if id(c) not in dead]
    return diag


def _harmonize(weak: dict, strong: dict, d: int, log, diag):
    """근거 없는 **긴** 카드(weak)의 corrected를 근거 있는 짧은 카드(strong) 이음매로."""
    keep = {i for i in weak["sp"] if not (d < i < d + len(strong["word"]))}
    add = {i + d for i in strong["sp"] if 0 < i < len(strong["word"])}
    _rewrite(weak, keep | add, strong, log, diag)


def _harmonize_into_short(weak: dict, strong: dict, d: int, log, diag):
    """근거 없는 **짧은** 카드(weak)의 corrected를 근거 있는 긴 카드(strong) 이음매로."""
    add = {i - d for i in strong["sp"] if d < i < d + len(weak["word"])}
    _rewrite(weak, add, strong, log, diag)


_HARMONIZED_MARK = "— 이음매 정합"


def _rewrite(weak: dict, spaces: set, strong: dict, log, diag):
    new = apply_spaces(weak["word"], spaces)
    c = weak["c"]
    if new == c.corrected:
        return
    # ⚠ **정합 후 `weak["sp"]`를 반드시 갱신한다.** 한 카드가 여러 강한 카드와 겹칠
    #   수 있고(실측 '일가정양립정책의'는 3건과 겹침), 갱신하지 않으면 두 번째
    #   정합이 낡은 공백 집합에서 다시 계산해 **앞 정합을 되돌린다**(실측: 정합이
    #   3회 돌며 결과가 매번 뒤집혔고 최종값은 순서 운에 달렸다).
    weak["sp"] = frozenset(spaces)
    if new == c.original:
        # 정합 결과가 원문과 같아지면 교정할 것이 없다 — 무의미 카드로 표시하고
        #   resolve가 목록에서 제거한다(패널에 '원문=교정' 카드를 띄우지 않기 위함).
        log(f"      · 정합 결과가 원문과 동일 → 무의미 카드 제거 '{c.original}'")
        weak["dead"] = True
        diag["harmonized"] += 1
        return
    old = c.corrected
    c.corrected = new
    if _HARMONIZED_MARK not in c.reason:
        c.reason = (c.reason + f"\n{_HARMONIZED_MARK}: 겹치는 이음매를 문서 다수 "
                               f"표기('{strong['c'].corrected}')에 맞춤").strip()
    diag["harmonized"] += 1
    log(f"      · 이음매 정합 '{c.original}' → '{new}' (기존 '{old}')")


# ── 검수 패널 전파용 헬퍼(GUI 무관) ────────────────────────────────────
def propagate(decided_word: str, decided_spaces, target_word: str,
              target_spaces):
    """결정된 이음매를 다른 낱말 좌표로 옮겨 새 공백 집합을 만든다.

    decided_*  사용자가 방금 결정한 낱말과 그 공백 집합
    target_*   같은 그룹의 다른 낱말과 현재 공백 집합
    반환: 겹치는 구간만 결정으로 덮은 새 공백 집합(겹치지 않으면 원래 집합 그대로)

    ⚠ 낱말은 **양방향 포함**을 다 본다 — 그룹 안에서 결정 카드가 짧을 수도 길 수도 있다.
    """
    d = target_word.find(decided_word)
    if d >= 0:
        keep = {i for i in target_spaces
                if not (d < i < d + len(decided_word))}
        add = {i + d for i in decided_spaces if 0 < i < len(decided_word)}
        return keep | add
    d = decided_word.find(target_word)
    if d >= 0:
        return {i - d for i in decided_spaces
                if d < i < d + len(target_word)}
    return set(target_spaces)


def governed(decided_word: str, target_word: str) -> set:
    """결정 낱말이 대상 낱말의 **어느 이음매까지 지배하는가**(대상 좌표).

    검수 패널이 이걸로 '이 결정만으로 대상 카드가 확정되는지'를 판단한다. 지배하지
    않는 이음매가 남아 있으면 그 카드는 **pending으로 남겨** 사용자가 따로 결정한다 —
    한 이음매 결정으로 다른 이음매까지 자동 수락하면, 사용자가 판단하지 않은 띄어쓰기가
    조용히 적용된다(실측 시뮬레이션 C: '수당수급자' 거절이 '수당수급자확인서'의
    수급자|확인서 분리까지 수락해 버렸다).
    """
    d = target_word.find(decided_word)
    if d >= 0:
        return set(range(d + 1, d + len(decided_word)))
    if decided_word.find(target_word) >= 0:
        return set(range(1, len(target_word)))   # 대상이 결정 낱말에 통째로 포함
    return set()
