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
  · 쉼표 뒤에 글자가 붙은 공백 누락(뒤가 조사·'등'이면 제외 — 쉼표 자체가 군더더기).

공백만 삽입하고 글자는 바꾸지 않는다(환각 0). 검수 카드 특성상 약간의 노이즈는
사람 검토로 흡수된다. ⚠ 예외 하나 — `find_stray_commas`는 군더더기 쉼표를 지우거나 공백으로 바꾼다
(그래서 워커가 띄어쓰기가 아니라 문장부호 카드로 낸다).
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


# 한 음절씩 마침표로 끊은 **한글 머리글자 약칭**(“가.나.다” 꼴 — 실원고에선 지자체 사업명이었다).
#   라틴 약어의 1글자 런 예외(U.S.A — _is_abbrev_dot ①)와 같은 판정을 한글에 적용한다.
#   ⚠ **세 음절 이상 사슬일 때만**: 두 음절('것.그 다음')은 문장 끝 1음절 어절 뒤에 띄어쓰기가
#     빠진 진짜 오류일 수 있다. 실측(2026-10-19): 이 꼴의 카드가 사용자에게 19회 거절됐다.
_HANGUL_ABBREV_RE = re.compile(r"(?<![가-힣])[가-힣](?:\.[가-힣]){2,}(?![가-힣])")


def _fix_token(tok: str, commas=()) -> str:
    """commas: 뒤를 띄울 쉼표의 토큰 내 위치(_tight_comma_positions가 문단 문맥으로 고른 것)."""
    inserts = [i + 1 for i in commas]   # 공백을 삽입할 위치(해당 인덱스 '앞')
    n = len(tok)
    _ko_abbr = [(m.start(), m.end()) for m in _HANGUL_ABBREV_RE.finditer(tok)]
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
            # 단, 한 음절씩 끊은 한글 약칭 사슬('가.나.다') 안의 점은 문장부호가 아니다.
            if c == "." and any(s < i < e for s, e in _ko_abbr):
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


# ── 쉼표 뒤 띄어쓰기 ────────────────────────────────────────────────────────────
# 쉼표 뒤는 띄어 쓴다 — 뒤에 글자(한글·라틴)가 바로 붙은 쉼표는 **전부** 후보다(사용자 보고 2026-10-22
#   '소재 (36건, 25.7%),센서 및 제어 …'). 표 칸·괄호 속 압축 나열('※전기,가스,수도 등'·'(보증금,월차임)')도
#   잡는다 — 규범상 오류이고 원고당 1~2장 수준이다(사용자 결정 2026-10-22: 처음엔 '같은 나열에 띄운 쉼표가
#   있을 때만'으로 좁혔으나 한 문단에 쉼표가 하나뿐인 본문 오류 '변수들로,이상'을 놓쳤다).
#   실측(20원고): 붙은 쉼표+글자 49곳 → 발화 47곳(본문 20 · 압축 나열 27).
# ⚠ 뒤가 조사·'등'이면 띄우지 않는다 — '묻다”,와'·'Gemini,등'은 쉼표 자체가 군더더기라 띄우면 오교정이다.
#   자릿점('1,000')·'1,2차'는 뒤가 숫자라 대상이 아니다.
_TIGHT_COMMA_RE = re.compile(r"(?<=\S),(?=[가-힣A-Za-z])")
# 열거를 닫는 '등' — 조사가 붙어도 같다('등의'·'등을'). '등급'·'등록'처럼 낱말이 이어지면 아니다.
_ENUM_END_RE = re.compile(
    r"(?:등등|등|따위)(?:의|을|를|이|은|는|과|와|에서|에게|에|으로|로|도|만|까지|이나|처럼)?(?=$|[^가-힣])")


def _tight_comma_positions(text: str) -> set:
    """뒤를 띄울 쉼표의 절대 위치 — 쉼표 자체가 군더더기인 자리(find_stray_commas)는 뺀다.

    뒤가 조사처럼 보여도 낱말로도 쓰이는 한 글자(이·가·나·도·만·라)는 앞이 닫는 부호가 아니면 낱말로
    본다 — '가,나,다'의 '나'·'A,이 경우'의 '이'. 닫는 부호 뒤('“A”,가')는 어느 쪽인지 못 가려 카드를 안 낸다.
    """
    out = set()
    for m in _TIGHT_COMMA_RE.finditer(text):
        rest = text[m.end():m.end() + 12]
        if _ENUM_END_RE.match(rest):
            continue
        jm = _JOSA_AFTER_QUOTE.match(rest)
        if jm and (text[m.start() - 1] in _CLOSE_SYM or jm.group(1) not in _AMBIG_JOSA):
            continue
        out.add(m.start())
    return out


# ── 군더더기 쉼표 — 위에서 '띄우지 않는다'로 뺀 두 자리를 바로잡는다 ─────────────────
#   (사용자 확인 2026-10-22) 이 둘은 띄어쓰기가 아니라 **쉼표 자체**가 틀렸다:
#     ⓐ 닫는 부호 + 쉼표 + 조사  '“…묻다”,와' → '“…묻다”와'   (조사는 인용·괄호에 붙는다 — 쉼표 삭제)
#     ⓑ 낱말 + 쉼표 + '등'        'Gemini,등'   → 'Gemini 등'   (열거를 닫는 '등' 앞엔 쉼표 대신 띄어 씀)
#   ⚠ 이 모듈에서 유일하게 **글자(쉼표)를 바꾸는** 규칙이다 → 워커가 문장부호(punct)·low 검수 카드로 낸다.
#   ⚠ ⓐ는 낱말로도 쓰이는 한 글자 조사(이·가·도·만·나·라)를 뺀다 — '(2024),이 연구는'의 '이'는 관형사라
#     쉼표를 지우면 오교정이다(그 자리는 띄움이 맞지만 어느 쪽인지 모양으로 못 가르므로 카드를 안 낸다).
_CLOSE_SYM = "”’\"')]」』》〉"
_AMBIG_JOSA = frozenset({"이", "가", "도", "만", "나", "라"})
_STRAY_JOSA_RE = re.compile(r"(?<=[" + re.escape(_CLOSE_SYM) + r"]),(?=[가-힣])")
_STRAY_ENUM_RE = re.compile(r"(?<=[0-9A-Za-z가-힣)\]]),(?=등|따위)")


def find_stray_commas(text: str) -> list:
    """군더더기 쉼표 후보 [(원문, 교정문, 사유), …](등장 순·중복 제거).

    원문은 앞 낱말(과 닫는 부호)부터 조사·'등'까지다. 같은 원문이 이 규칙이 고치지 않는 자리
    ('Gemini,등급' 속)에도 있으면 그 원문은 내지 않는다(그 자리까지 바뀌지 않게).
    """
    cand = []                                   # (원문 시작, 원문, 교정문, 사유)
    for m in _STRAY_JOSA_RE.finditer(text):
        jm = _JOSA_AFTER_QUOTE.match(text, m.end())
        if not jm or jm.group(1) in _AMBIG_JOSA:
            continue
        s = m.start() - 1                       # 닫는 부호
        while s > 0 and (text[s - 1].isalnum() or text[s - 1] in _CLOSE_SYM) and m.start() - s < 20:
            s -= 1
        orig = text[s:jm.end()]
        cand.append((s, orig, orig.replace(",", "", 1) if orig.count(",") == 1 else None,
                     "닫는 부호 뒤 조사 앞의 쉼표 — 조사는 앞말에 붙여 씀"))
    for m in _STRAY_ENUM_RE.finditer(text):
        s = m.start()
        while s > 0 and text[s - 1].isalnum() and m.start() - s < 20:
            s -= 1
        em = _ENUM_END_RE.match(text, m.end())
        if not em:
            continue                            # '등급'·'등록' — 열거의 '등'이 아니다
        orig = text[s:em.end()]
        cand.append((s, orig, orig.replace(",", " ", 1) if orig.count(",") == 1 else None,
                     "열거를 닫는 '등' 앞에는 쉼표 대신 띄어 씀"))
    starts = {}
    for s, orig, fixed, _w in cand:
        starts.setdefault(orig, set()).add(s)
    out, seen = [], set()
    for s, orig, fixed, why in sorted(cand):
        if fixed is None or orig in seen:
            continue
        i, ok = text.find(orig), True
        while i != -1:
            if i not in starts[orig]:
                ok = False
                break
            i = text.find(orig, i + 1)
        if ok:
            seen.add(orig)
            out.append((orig, fixed, why))
    return out


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

    ⚠⚠ **모양으로 찾고 역할로 거른다**(2026-08-05 사용자 보고). 위 정규식은 글자 모양
    (‘…’)으로만 짝을 잡는데, 굽은따옴표를 **뒤집어 쓴 원고**(’성과‘가 아니라 ’메커니즘‘을)
    에서는 그게 통째로 어긋난다 — 한 인용의 **닫는** 따옴표와 다음 인용의 **여는** 따옴표를
    한 쌍으로 오인해 그 사이(성과‘가 아니라 ’)에 공백을 넣으라는 카드를 냈다. 원고에서
    실제로 틀린 건 따옴표 **방향**이지 띄어쓰기가 아니다(→ `quote_rules.find_reversed_quotes`).
    그래서 `quote_rules`의 **줄 문맥 스택**이 판정한 역할과 대조해, 여는 자리가 정말
    `open`이고 닫는 자리가 정말 `close`일 때만 후보로 삼는다. 역할표를 못 얻으면
    (임포트 실패) 기존 동작으로 되돌아간다 — 억제 방향이라 안전하다.

    반환: [(original, corrected), ...] (중복 제거). 미설치/실패 시 [].
    """
    try:
        from core import quote_rules as _qr
        roles = _qr.quote_roles_text(text)
    except Exception:
        roles = None
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
            # 줄 문맥이 판정한 역할과 어긋나면(역방향 원고·스택 오염) 후보 아님
            if roles is not None and not (roles.get(s) == "open"
                                          and roles.get(e - 1) == "close"):
                continue

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

    fix_fn(토큰, 토큰의 절대 offset) → 토큰 교정문.
    반환: [(원문, 교정문), ...] (문서 등장 순, 중복 제거). 글자 불변만 통과.
    """
    cand = []          # (앵커 절대 offset, 토큰, 토큰 교정문, 앵커, 앵커 교정문)
    for m in re.finditer(r"\S+", text):
        tok = m.group()
        if len(tok) < min_len or (skip_fn and skip_fn(tok)):
            continue
        fixed = fix_fn(tok, m.start())
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
    쉼표 뒤 띄움은 뒤 문맥(조사·'등')을 토큰 밖까지 봐야 해 먼저 골라 두고(_tight_comma_positions)
    같은 토큰 교정에 합친다 — 한 토큰에 두 규칙이 겹쳐도 카드는 한 장이다.
    """
    commas = _tight_comma_positions(text)

    def _fix(tok: str, start: int) -> str:
        if not commas:
            return _fix_token(tok)
        return _fix_token(tok, [i for i in range(len(tok)) if start + i in commas])

    return _collect_token_fixes(
        text, _fix,
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
        lambda t, _s: _UNIT_WON_RE.sub(r"\1 \2", t),
        skip_fn=lambda t: bool(re.search(r"\d,\d", t)),   # 자릿점/오식('12,9') — AI 영역
    )
