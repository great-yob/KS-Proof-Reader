"""
core/spacing_rules.py — 규칙 기반 문장부호·스크립트 경계 띄어쓰기 탐지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
형태소 분석(kiwi)이 구조적으로 못 잡는 **영문/문장부호 띄어쓰기 누락**을 보수적으로
탐지한다. 탐지 전용 — 자동수정이 아니라 저신뢰 '검수 카드'로 노출(사람 검토)한다.

보수적 원칙(오탐 최소화):
  · 문장부호(.?!) 뒤에 곧바로 대문자/한글이 오고, 부호 앞이 '여러 글자 단어'일 때만.
    (소수점 3.14 / 약어 U.S.A·a.m. / node.js·config.py / URL·이메일 / 줄임표 … 제외)
  · 한글↔라틴이 따옴표를 사이에 두고 붙은 **교차-스크립트 경계**만(가"Say → 가 "Say).
    영어 축약(don't)은 직선 아포스트로피라 대상에서 제외한다.

공백만 삽입하고 글자는 바꾸지 않는다(환각 0). 검수 카드 특성상 약간의 노이즈는
사람 검토로 흡수된다.
"""
import re

# 큰따옴표(직선/굽은)만. ⚠ 작은따옴표(' ' ')는 스크립트 경계 규칙에서 **제외**한다 —
# 영어 아포스트로피·소유격·인용이 혼재해 "영어'+한글조사"(Age'의·Connect'를·Challenge'는)를
# 조사 앞에서 오분리한다. 큰따옴표 경계(캐나가"Say → 캐나가 "Say)만 보수적으로 다룬다.
_QUOTES = "\"“”"

# URL/이메일/파일명처럼 보이는 토큰은 통째로 건너뛴다.
_SKIP_TOKEN = re.compile(
    r"(https?://|www\.|@|\.(?:com|org|net|io|kr|co|gov|edu|html?|py|js|json|md|txt|csv)\b)",
    re.I,
)

# 점이 약어 구분자로 보이는 다음절 약어(점 뒤 공백 제안을 생략 — 보수적).
_ABBREV = {"etc", "vs", "cf", "al", "ed", "eds", "no", "vol", "pp", "fig", "ie", "eg"}


def _left_latin_run(tok: str, i: int) -> str:
    """tok[i] 바로 앞의 라틴 글자 런(없으면 '')."""
    j, run = i - 1, ""
    while j >= 0 and tok[j].isascii() and tok[j].isalpha():
        run = tok[j] + run
        j -= 1
    return run


def _is_abbrev_dot(tok: str, i: int) -> bool:
    """tok[i]=='.' 가 **문장 종결이 아닌** 점인가? (약칭·고유명사·도메인 구분자)

    판정 셋 — 앞 라틴 런이
      ① 1글자('U.S.'·'n.d.a')  ② 알려진 약어(_ABBREV: etc·vs·cf…)
      ③ ★**대문자를 품고 있음** — 'Ph.D'·'OECD.AI'·'WSO2 Inc.이'
    ③이 핵심이다(2026-08-03 사용자 결정). **예외를 열거하지 않는 판정**으로,
    도메인(.ai/.app…)·학위(Ph.D·M.D)·기관 약칭(OECD.AI)·법인(Inc.·Corp.)이
    새로 나와도 코드를 고칠 필요가 없다. 근거: 영어 문장이 끝날 때 마지막 낱말은
    보통 소문자('…of life.')이고, **대문자로 끝나면 거의 언제나 식별자**다.
    ⚠ 왜 목록을 안 늘렸나 — `_ABBREV` 12개 + `_SKIP_TOKEN` TLD 14개를 이미 갖고도
      위 셋을 못 막았다. 도메인·브랜드·약칭은 **닫히지 않는 집합**이라 열거로는 끝이
      없고, 목록을 늘릴 때마다 정문 발화를 재측정해야 한다. 목록형 예외는 유한하고
      안정된 집합(조사·의존명사)에만 쓴다.
    실측(실원고 5종 774K자): 오탐 3건(Ph.D 3회·OECD.AI 14회·Inc.이 1회) → **0건**,
    정탐 6건(전부 한글 뒤 마침표) **전량 보존**, 문서화 사례 'life.Let' 보존
    (소문자로 끝남), 중의성 정문 2만 문장 발화 5→5 무변화.
    수용한 미탐: 대문자 고유명사로 끝난 영문 문장 뒤 새 문장('…in Korea.The results').
      실원고 5종에 0건 — 억제 방향이라 과교정 0 원칙에 부합.
    ⚠ 한글 뒤 마침표는 이 함수를 아예 타지 않는다(호출 측 `prev.isascii()` 가드).
    """
    run = _left_latin_run(tok, i)
    if len(run) <= 1:
        return True
    if run.lower() in _ABBREV:
        return True
    return not run.islower()


def _fix_token(tok: str) -> str:
    inserts = []   # 공백을 삽입할 위치(해당 인덱스 '앞')
    n = len(tok)
    for i in range(1, n - 1):
        c = tok[i]
        prev, nxt = tok[i - 1], tok[i + 1]

        # 규칙 1: 문장부호 .?! + 대문자/한글 (앞은 여러 글자 단어)
        if c in ".?!":
            prev_letter = prev.isalpha() or ("가" <= prev <= "힣")
            next_start = ("A" <= nxt <= "Z") or ("가" <= nxt <= "힣")
            if not (prev_letter and next_start):
                continue
            if prev in ".?!" or nxt in ".?!":       # 줄임표/연속 부호
                continue
            # 약어(U.S.·etc.)·소수점 예외는 라틴 문맥에서만 — 한글 뒤 마침표는 문장부호다.
            if c == "." and prev.isascii() and _is_abbrev_dot(tok, i):
                continue
            inserts.append(i + 1)                    # 부호 '뒤'에 공백
            continue

        # 규칙 2: 한글↔라틴 따옴표 경계 (가"Say / Say"가)
        if c in _QUOTES:
            left_ko = "가" <= prev <= "힣"
            right_ko = "가" <= nxt <= "힣"
            left_lat = prev.isascii() and prev.isalpha()
            right_lat = nxt.isascii() and nxt.isalpha()
            if left_ko and right_lat:
                inserts.append(i)        # 따옴표 '앞'(한글 뒤)에 공백
            elif left_lat and right_ko:
                # 닫는 따옴표 뒤 한글이 **조사**면 붙여 쓴다 — 기호 뒤 조사는 앞말(인용어)에
                #   붙는다(사용자 보고 2026-07-01: '"AI"를'→'"AI" 를'은 오분리). '를·의·는' 등
                #   조사는 _JOSA_AFTER_QUOTE로 판정('의미'의 '의'처럼 더 긴 단어면 조사 아님 → 분리).
                if _JOSA_AFTER_QUOTE.match(tok[i + 1:]):
                    continue
                inserts.append(i + 1)    # 따옴표 '뒤'(한글 앞)에 공백
            continue

    if not inserts:
        return tok
    chars = list(tok)
    for pos in sorted(set(inserts), reverse=True):
        chars.insert(pos, " ")
    return "".join(chars)


# ── 한국어 인용부호 띄어쓰기 정규화 ──────────────────────────────────
# 인용부호 쌍(여는, 닫는). 직선은 같은 글자가 쌍. 같은 굽은 글자 반복(’…’)은
# 아포스트로피 오매칭 위험이 커 쌍으로 인정하지 않는다(보수적).
_QUOTE_PAIRS = (("‘", "’"), ("“", "”"), ("'", "'"), ('"', '"'))

# 닫는 따옴표 뒤에 붙어야 하는 조사(긴 것부터). 뒤가 한글이면(=더 긴 단어) 조사 아님.
_JOSA_AFTER_QUOTE = re.compile(
    r"(으로서|으로써|에게서|이라고|이라는|으로|에서|에게|이라|이며|이고|라고|라는|"
    r"처럼|보다|마다|조차|밖에|부터|까지|이나|은|는|이|가|을|를|에|의|와|과|도|만|로|나|라)"
    r"(?=$|[^가-힣])"
)


def find_quote_spacing(text: str) -> list:
    """한국어 인용부호 띄어쓰기 정규화 — 여는 따옴표 '앞' 띄움 + 닫는 따옴표 '뒤' 조사 붙임.

    예) 국립국어원'맞춤법규칙' 에  →  국립국어원 '맞춤법규칙'에
       (좌: 단어'여는 → 단어 '여는,  우: 닫는' 에 → 닫는'에)

    · **짝이 맞는 인용**(여는+닫는)만 처리 → 홑따옴표 아포스트로피(Age'의·don't)는 짝이
      없어 자연 제외(오교정 방지). 인용 내용에 한글이 있어야(영문 약물·코드 오탐 방지).
    · 공백을 넣거나(여는 앞) 빼므로(닫는 뒤 조사) 글자 불변(환각 0). 닫는 따옴표 뒤 조사는
      '한글이 더 안 이어질 때'만 조사로 인정('의미'의 '의'는 조사 아님). 탐지 전용 검수 카드.

    반환: [(original, corrected), ...] (중복 제거). 미설치/실패 시 [].
    """
    out, seen = [], set()
    for opn, cls in _QUOTE_PAIRS:
        if opn == cls:
            inner = "[^" + re.escape(opn) + "\\n]"
        else:
            inner = "[^" + re.escape(opn) + re.escape(cls) + "\\n]"
        pat = re.compile(re.escape(opn) + "(" + inner + "{1,40})" + re.escape(cls))
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if not re.search(r"[가-힣]", m.group(1)):
                continue                                   # 인용 내용에 한글 없음 → 스킵

            # 좌: 단어 + 여는따옴표(공백 없음) → 앞 단어 전체를 잡아 공백 삽입
            left_fix = s > 0 and text[s - 1].isalnum()
            if left_fix:
                o_start = s - 1
                while o_start > 0 and text[o_start - 1].isalnum():
                    o_start -= 1
                prefix = text[o_start:s] + " "
            else:
                o_start, prefix = s, ""

            # 우: 닫는따옴표 + 공백 + 조사 → 공백 제거(조사 붙임)
            right_fix, josa, sp_len = False, "", 0
            sp = re.match(r"[ \t]+", text[e:])
            if sp:
                jm = _JOSA_AFTER_QUOTE.match(text[e + sp.end():])
                if jm:
                    right_fix, josa, sp_len = True, jm.group(1), sp.end()

            if not (left_fix or right_fix):
                continue
            o_end = e + sp_len + len(josa) if right_fix else e
            original = text[o_start:o_end]
            corrected = prefix + text[s:e] + (josa if right_fix else "")
            if original == corrected or original.replace(" ", "") != corrected.replace(" ", ""):
                continue
            if original in seen:
                continue
            seen.add(original)
            out.append((original, corrected))
    return out


# ── 앵커 최소화 — 같은 교정이 카드 N장으로 흩어지는 것을 막는다 ────────────────
# 이 모듈의 토큰 규칙은 **공백으로 자른 토큰 전체**를 원문으로 삼았다. 그래서 같은 자리를
#   고치는 교정이 주변 글자에 따라 서로 다른 카드가 됐다 — 실측(실파일E, 268K자): 'OECD.AI'
#   하나가 'OECD.AI,'·'추진한다(OECD.AI,'·'OECD.AI(n.d.c)'·'OECD.AI.(n.d.c),' 등
#   **카드 12장**으로 갈렸다(그 문서 문장부호 카드 20장 중 12장). 사용자 보고 2026-08-03:
#   "카드 하나만 등장하고 반복으로 표시하면 카드 수도 줄고 사용성이 좋을 텐데".
#   → 삽입 지점의 **양옆 문맥에서 낱말 문자 런까지만** 앵커로 잡는다. 그러면 위 12장이
#   전부 'OECD.AI' 한 장(반복 14)이 되고, 검수 패널의 반복 일괄 처리가 그대로 먹는다.
#
# ⚠ 앵커를 좁히면 그 문자열이 **규칙이 발동하지 않는 자리**(URL 안 'www.OECD.AI' 등)에도
#   매칭될 수 있다. 그래서 좁힌 앵커는 **문서 안 모든 등장이 이 규칙이 고치는 자리일 때만**
#   채택하고, 하나라도 어긋나면 토큰 앵커로 되돌린다(_collect_token_fixes). 억제 방향이라
#   되돌아가면 기존 동작 그대로다.
_ANCHOR_EXTRA = "._-/"      # 낱말 내부로 취급할 부호(소수점 13.6억원·도메인 OECD.AI)


def _is_anchor_char(ch: str) -> bool:
    return ch.isalnum() or ch in _ANCHOR_EXTRA


def _insert_positions(tok: str, fixed: str):
    """fixed가 tok에 **공백만 삽입**한 결과일 때 삽입 위치(tok 기준)를 반환. 아니면 None."""
    pos, i = [], 0
    for ch in fixed:
        if i < len(tok) and ch == tok[i]:
            i += 1
        elif ch == " ":
            pos.append(i)
        else:
            return None
    return pos if i == len(tok) else None


def _anchor_of(tok: str, fixed: str):
    """(앵커, 앵커 교정문, 토큰 내 시작 offset) — 좁힐 수 없으면 토큰 그대로."""
    ins = _insert_positions(tok, fixed)
    if not ins:
        return tok, fixed, 0
    lo, hi = ins[0] - 1, ins[-1] + 1        # 삽입 지점 양옆 = 규칙이 본 최소 문맥
    if lo < 0 or hi > len(tok):
        return tok, fixed, 0
    while lo > 0 and _is_anchor_char(tok[lo - 1]):
        lo -= 1
    while hi < len(tok) and _is_anchor_char(tok[hi]):
        hi += 1
    # 확장이 삼킨 **바깥쪽** 부호는 되돌린다('OECD.AI.(n.d.c),'의 끝 '.'). 최소 문맥은 침범 금지.
    while lo < ins[0] - 1 and not tok[lo].isalnum():
        lo += 1
    while hi > ins[-1] + 1 and not tok[hi - 1].isalnum():
        hi -= 1
    if lo == 0 and hi == len(tok):
        return tok, fixed, 0
    chars = list(tok[lo:hi])
    for p in sorted(ins, reverse=True):
        chars.insert(p - lo, " ")
    return tok[lo:hi], "".join(chars), lo


def _collect_token_fixes(text: str, fix_fn, skip_fn=None, min_len: int = 1) -> list:
    """공백 토큰 규칙의 공통 수집기 — 앵커를 좁히고 중복을 제거한다.

    반환: [(원문, 교정문), ...] (문서 등장 순, 중복 제거). 글자 불변만 통과.
    """
    cand = []          # (앵커 절대 offset, 토큰, 토큰 교정문, 앵커, 앵커 교정문)
    for m in re.finditer(r"\S+", text):
        tok = m.group()
        if len(tok) < min_len or (skip_fn and skip_fn(tok)):
            continue
        fixed = fix_fn(tok)
        if fixed == tok or fixed.replace(" ", "") != tok.replace(" ", ""):
            continue                                   # 글자 변경 — 환각 0 불변식
        a, af, off = _anchor_of(tok, fixed)
        cand.append((m.start() + off, tok, fixed, a, af))

    fixable = {}       # 앵커 → 이 규칙이 고치는 절대 위치 집합
    for start, _t, _f, a, _af in cand:
        fixable.setdefault(a, set()).add(start)
    verdict = {}       # 앵커 → 좁혀도 되는가(문서 전 등장이 고칠 자리인가)

    def _safe(a: str) -> bool:
        if a not in verdict:
            all_pos, i = set(), text.find(a)
            while i != -1:
                all_pos.add(i)
                i = text.find(a, i + 1)
            verdict[a] = all_pos == fixable[a]
        return verdict[a]

    out, seen = [], set()
    for _s, tok, fixed, a, af in cand:
        if a != tok and not _safe(a):
            a, af = tok, fixed                         # 규칙 밖 등장이 있다 → 토큰 앵커로 복귀
        if a in seen:
            continue
        seen.add(a)
        out.append((a, af))
    return out


def find_punct_spacing(text: str) -> list:
    """문장부호/스크립트 경계 띄어쓰기 누락 후보.

    반환: [(원문, 띄어쓴 원문), ...] (등장 순, 중복 제거). 공백만 삽입된 것만.
    원문은 공백 토큰 전체가 아니라 **교정 지점을 감싼 최소 앵커**다(_collect_token_fixes).
    """
    return _collect_token_fixes(
        text, _fix_token,
        skip_fn=lambda t: bool(_SKIP_TOKEN.search(t)),
        min_len=3,
    )


# ── 숫자 큰수단위 뒤 통화 단위 '원' 띄어쓰기 ────────────────────────────────
# '13.6억원'→'13.6억 원'처럼 **숫자에 붙은 큰수단위(만/억/조/경) 뒤의 통화 '원'**을 띄운다
#   (한글 맞춤법 제43항 — 단위 명사는 띄어 쓴다). AI가 청크별로 일부만 잡고 나머지를 놓치는
#   대표 유형이라 결정론 규칙으로 **모든 등장**을 잡는다(공백만 삽입 — 환각 0, 저신뢰 검수 카드).
#   ⚠ 앞이 '숫자'일 때만(=수 문맥) 발동해 '만원버스(滿員)'·인명('억원') 오발동을 막는다.
#   ⚠ 숫자 사이 쉼표('12,9억원')는 표기 오류(소수점/자릿점)라 AI 영역 → 중복·충돌 방지로 제외.
#   순수 '5000원'(큰수단위 없음)은 붙여쓰기 허용이라 건드리지 않는다(제43항 단서).
#
# ⚠ **이 규칙이 통화·단위를 직접 열거하는 이유**(2026-07-28, 실측 후 확정) — 형태소 백스톱
#   (morph.find_spacing_suggestions)도 같은 경계를 잡지만, 워커 [7]의 사전 가드가 그 카드를
#   다시 죽인다: `nikl_dict.lookup_word()`가 **숫자를 떼고** 조회하므로(nikl_dict.py:106)
#   '10만원'→'만원'(滿員)·'10만명'→'만명'·'10만개'→'만개'(滿開)가 전부 '등재어'로 판정된다.
#   즉 kiwi 경로는 수량+단위에서 구조적으로 막힌다. 사전 가드가 없는 **이 결정론 경로**가
#   수량 표기를 책임지는 유일한 길이라, 통화 외 단위까지 여기서 함께 다룬다.
#   단위 목록은 '숫자+큰수단위 뒤'라는 강한 수 문맥에서만 쓰이므로 오발동 여지가 좁다.
#   ⚠ **제외한 단위**(의도적 — 붙여 쓴 쪽이 다른 뜻이라 결정론으로 가를 수 없다):
#     · '대' — '억대 연봉'·'주가 10만대'의 '대(帶)'는 범위를 뜻하는 접미사라 붙여 쓴다.
#             '10만 대(臺)의 차량'과 표기가 같아 문맥 없이는 판정 불가.
#     · '분' — '10만분의 1'(분수)이 붙여 쓰는 정상 표기다. 띄우면 오히려 틀린다.
#     · '월/일/초' — '만일(萬一)' 등 동형어 대비 실익이 낮아 제외.
#   위 넷은 형태소 백스톱도 사전 가드에 막혀 잡지 않는다(= 현행 유지, 미탐).
_BIG_UNIT = "만억조경"
_COUNT_UNIT = (
    # 통화 — 원화 + 주요 외국통화
    "원", "달러", "엔", "유로", "위안", "파운드", "프랑", "루블", "루피", "링깃",
    # 수량 — 사람·개수·건수·집단
    "명", "개", "건", "곳", "가구", "세대", "부",
    # 계량·시간
    "톤", "년", "시간", "km", "kg", "평", "㎡",
)
_UNIT_WON_RE = re.compile(
    r"(?<=\d)([" + _BIG_UNIT + r"])(" + "|".join(sorted(_COUNT_UNIT, key=len, reverse=True)) + r")")


def find_unit_spacing(text: str) -> list:
    """숫자+큰수단위(만/억/조/경) 뒤 **단위 명사** 띄어쓰기 후보.

    '13.6억원'→'13.6억 원', '10만명'→'10만 명', '5만달러'→'5만 달러'
    (한글 맞춤법 제43항 — 단위를 나타내는 명사는 띄어 쓴다).

    반환: [(원문, 띄어쓴 원문), ...] (등장 순, 중복 제거). 공백만 삽입된 것만.
    원문은 부호가 붙은 토큰('13.6억원이고,')이 아니라 **최소 앵커**('13.6억원이고')다
    — 같은 수치가 문서 곳곳에서 각각 카드가 되던 문제를 막는다(find_punct_spacing과 공용).
    """
    return _collect_token_fixes(
        text,
        lambda t: _UNIT_WON_RE.sub(r"\1 \2", t),
        skip_fn=lambda t: bool(re.search(r"\d,\d", t)),   # 자릿점/오식('12,9') — AI 영역
    )
