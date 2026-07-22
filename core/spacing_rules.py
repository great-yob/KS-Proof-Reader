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


def _is_abbrev_dot(tok: str, i: int) -> bool:
    """tok[i]=='.' 가 약어 구분자로 보이는가? (앞 글자 run이 1글자이거나 알려진 약어)"""
    j = i - 1
    run = ""
    while j >= 0 and tok[j].isascii() and tok[j].isalpha():
        run = tok[j] + run
        j -= 1
    if len(run) <= 1:
        return True
    return run.lower() in _ABBREV


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


def find_punct_spacing(text: str) -> list:
    """문장부호/스크립트 경계 띄어쓰기 누락 후보.

    반환: [(원토큰, 띄어쓴토큰), ...] (등장 순, 중복 제거). 공백만 삽입된 것만.
    """
    out = []
    seen = set()
    for tok in text.split():
        if len(tok) < 3 or tok in seen:
            continue
        seen.add(tok)
        if _SKIP_TOKEN.search(tok):
            continue
        fixed = _fix_token(tok)
        # 글자(공백 제외) 동일 보장 — 환각 0
        if fixed != tok and fixed.replace(" ", "") == tok.replace(" ", ""):
            out.append((tok, fixed))
    return out


# ── 숫자 큰수단위 뒤 통화 단위 '원' 띄어쓰기 ────────────────────────────────
# '13.6억원'→'13.6억 원'처럼 **숫자에 붙은 큰수단위(만/억/조/경) 뒤의 통화 '원'**을 띄운다
#   (한글 맞춤법 제43항 — 단위 명사는 띄어 쓴다). AI가 청크별로 일부만 잡고 나머지를 놓치는
#   대표 유형이라 결정론 규칙으로 **모든 등장**을 잡는다(공백만 삽입 — 환각 0, 저신뢰 검수 카드).
#   ⚠ 앞이 '숫자'일 때만(=수 문맥) 발동해 '만원버스(滿員)'·인명('억원') 오발동을 막는다.
#   ⚠ 숫자 사이 쉼표('12,9억원')는 표기 오류(소수점/자릿점)라 AI 영역 → 중복·충돌 방지로 제외.
#   순수 '5000원'(큰수단위 없음)은 붙여쓰기 허용이라 건드리지 않는다.
_UNIT_WON_RE = re.compile(r"(?<=\d)([만억조경])원")


def find_unit_spacing(text: str) -> list:
    """숫자+큰수단위(만/억/조/경) 뒤 통화 '원' 띄어쓰기 후보.

    반환: [(원토큰, 띄어쓴토큰), ...] (등장 순, 중복 제거). 공백만 삽입된 것만.
    """
    out = []
    seen = set()
    for tok in text.split():
        if tok in seen:
            continue
        seen.add(tok)
        if re.search(r"\d,\d", tok):        # 자릿점/오식('12,9') — AI 영역, 제외
            continue
        fixed = _UNIT_WON_RE.sub(r"\1 원", tok)
        if fixed != tok and fixed.replace(" ", "") == tok.replace(" ", ""):
            out.append((tok, fixed))
    return out
