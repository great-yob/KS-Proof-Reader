"""
core/ai_guards.py — AI(생성) 과교정 억제 가드 (단일 출처)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI(Gemini)가 내는 4종 과교정을 결정론적으로 제외한다. 과거엔 워커에 인라인돼 있었으나,
**eval(AI 골드셋)과 워커가 '같은 함수'를 쓰도록** 분리했다(검증 일관성 — 골드셋이 실제 가드를
테스트하고 드리프트가 없게). 전부 **억제 방향(과교정 0)**.

  ① 비한글 토큰 '잘라내기'   — Microseparometer,P → Microseparometer,  (유효 약어·지표 삭제)
  ② 괄호 구조 변경           — 불균형 스팬을 조기에 닫음/단어 보충
  ③ 영문 병기 추가           — 재머 → 재머(jammer)  (표기 스타일, 맞춤법 아님)
  ④ 표·그림 캡션 인라인 대량삭제 — 'Figure 1 … Proposed … 과 같은' → 'Figure 1과 같은'
  ⑤ 숫자 값 변경             — '관리 범주에서 11개' → '관리 범주에서 9개'  (수치=내용 편집)
  ⑥ 영문→한글 음역           — One-Stop → 원스톱  (의도된 영문 표기 존중)
  ⑦ 하이픈↔공백 치환         — 최소-후보자 → 최소 후보자  (하이픈 합성어 표기 존중)

가드 ①~⑦은 c.source/original/corrected만 본다(filter_overcorrections, 텍스트 불필요).
추가로 **문서 텍스트가 필요한** 가드(부분조각 확장)와 **사전이 필요한** 가드(외래어 순화 강등)는
별도 함수(filter_redundant_expansions·demote_loanword_paraphrase)로 둔다 — 워커가 차례로 호출한다.

상세 근거·실측: memory safety-net-overflag-guards ③·③-b·③-c·③-d, validation-delta-and-spacing-backstop.
GUI-agnostic (PySide6 미사용) — core/ 규칙. 전부 **억제 방향(과교정 0)**.
"""

import re

# ── ② 괄호 구조 ────────────────────────────────────────────
# 묶음표(괄호) 짝. AI는 청크 경계의 부분 스팬을 '미완 괄호'로 오인해 닫아버리거나(안정성(열산화
#   안정성 → 안정성(열산화안정성)) 의미 보완이라며 단어까지 덧붙이는 과교정을 한다. 괄호 짝
#   보정은 결정론 규칙(core.bracket_rules)이 전담하므로, 이미 불균형인 스팬의 괄호 '구조'를
#   바꾸는 AI 교정은 차단한다(진짜 미완 괄호는 그쪽이 검수 카드로 잡아 탐지 손실 없음).
_BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"), ("（", "）"), ("〔", "〕"),
                  ("「", "」"), ("『", "』"), ("【", "】"), ("《", "》"), ("〈", "〉"))


def bracket_imbalance(s: str) -> int:
    """문자열의 괄호 짝 불균형 총량(종류별 |여는 수 − 닫는 수|의 합). 0이면 균형."""
    return sum(abs(s.count(o) - s.count(c)) for o, c in _BRACKET_PAIRS)


# ── ③ 영문 병기 ────────────────────────────────────────────
# 영문/숫자/구두점만 담긴 (English) 형태의 괄호. 글자(한글) 교정이 아니라 '전문 용어 명확화'를
#   위해 AI가 덧붙이는 주석이다(재머→재머(jammer)). 편집·표기 스타일 결정이지 맞춤법 교정이
#   아니며, 같은 용어가 70번 나오면 카드도 70장 표출되어 과교정·소음.
_GLOSS_PAREN_RE = re.compile(r"\s*[(（][\sA-Za-z0-9.,/&%·\-]+[)）]")


def is_english_gloss_add(orig: str, corr: str) -> bool:
    """corr가 orig에 '영문 병기' 괄호만 덧붙인 것인지(원문 글자는 한 자도 안 바뀜).

    재머→재머(jammer)처럼 순한글 원문에 (English) 주석만 추가한 경우 True. 원문에 이미 영문이
    있거나 글자 자체가 바뀐(진짜 오타 수정) 교정은 False — 영문 오타 치환까지 막지 않는다.
    """
    if not orig or orig == corr or not _GLOSS_PAREN_RE.search(corr):
        return False
    stripped = _GLOSS_PAREN_RE.sub("", corr).strip()
    return (stripped == orig.strip()
            and bool(re.search(r"[A-Za-z]", corr))
            and not re.search(r"[A-Za-z]", orig))


# ── ④ 캡션 대량 삭제 ──────────────────────────────────────
# ai_typo(맞춤법) 교정이 이 글자 수 이상을 순삭제하면 진짜 오타가 아니라 표/그림 캡션·표셀이
#   본문에 인라인된 추출 잡탕을 'AI가 중복/불필요'라며 통째 지우는 과교정이다. 진짜 오탈자
#   교정은 1~5자 국소 변경이라 이 임계를 한참 밑돈다(캡션 길이 20~40자는 한참 웃돈다).
AI_BULK_DELETE_MIN = 10


# ── 가드 술어 (True = 과교정이므로 제외) ─────────────────────
def is_nonkr_trim(c) -> bool:
    """① 한글 0자 + 교정문이 원문의 접두(뒤 잘림). Microseparometer,P→Microseparometer,
    ⚠ 영문 '오타 치환'(Desitiy→Density: 접두 관계 아님)은 보존."""
    return (c.source == "ai_typo"
            and not re.search(r"[가-힣]", c.original)
            and len(c.corrected) < len(c.original)
            and c.original.startswith(c.corrected))


def is_bracket_change(c) -> bool:
    """② 이미 괄호 불균형(>0)인데 AI가 그 불균형을 바꿈."""
    return (c.source == "ai_typo"
            and bracket_imbalance(c.original) > 0
            and bracket_imbalance(c.original) != bracket_imbalance(c.corrected))


def is_english_gloss(c) -> bool:
    """③ 순한글 원문에 영문 괄호만 추가(ai_typo·ai_polish 둘 다)."""
    return c.source in ("ai_typo", "ai_polish") and is_english_gloss_add(c.original, c.corrected)


def is_bulk_delete(c) -> bool:
    """④ ai_typo 순삭제 ≥ 임계(캡션 통째 삭제)."""
    return c.source == "ai_typo" and len(c.original) - len(c.corrected) >= AI_BULK_DELETE_MIN


# ── ⑤ 숫자 값 변경 ─────────────────────────────────────────
# AI가 '관리 범주에서 11개'→'관리 범주에서 9개'처럼 **숫자 값만** 바꾸는 것은 교정이 아니라
#   내용(사실) 편집이다(앞 문맥 일관성을 명분으로 수치를 임의 변경 — 실제론 원문 11개가 맞음).
#   교정 도구는 저자가 쓴 수치를 바꾸면 안 된다. '숫자열을 뺀 나머지'가 동일하고 아라비아 숫자열만
#   다를 때 차단(공백·문장부호 변경은 대상 아님 — 나머지가 달라져 자연 제외).
def is_number_change(c) -> bool:
    """⑤ 원문/교정문이 아라비아 숫자열만 다르고 나머지 글자는 동일(수치 임의 변경)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    if not re.search(r"\d", c.original):
        return False
    return (re.sub(r"\d+", "", c.original) == re.sub(r"\d+", "", c.corrected)
            and re.findall(r"\d+", c.original) != re.findall(r"\d+", c.corrected))


# ── ⑥ 영문 → 한글 음역(표기 통일) ──────────────────────────
# 'One-Stop'→'원스톱'처럼 **저자가 의도적으로 쓴 영문**을 한글 음역으로 바꾸는 것은 표기 스타일
#   결정이지 맞춤법 교정이 아니다. 원문이 라틴문자뿐(한글 0)이고 교정문이 한글뿐(라틴 0)일 때 차단.
#   ⚠ 영문 오타치환(Desitiy→Density: 교정문에 라틴 잔존)은 보존된다.
def is_latin_to_hangul(c) -> bool:
    """⑥ 원문=영문(한글 0) → 교정문=한글(영문 0) 음역(표기 통일)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o, cr = c.original.strip(), c.corrected.strip()
    return (bool(re.search(r"[A-Za-z]", o)) and not re.search(r"[가-힣]", o)
            and bool(re.search(r"[가-힣]", cr)) and not re.search(r"[A-Za-z]", cr))


# ── ⑦ 하이픈 ↔ 공백 치환 ──────────────────────────────────
# '최소-후보자'→'최소 후보자'처럼 하이픈을 공백으로(또는 제거) 바꾸는 것은 표기 스타일 판단이다
#   (하이픈 합성어 '비용-효과'·'최소-후보자'는 의도된 표기인 경우가 많음). 하이픈/공백을 뺀 글자가
#   동일하고 원문에 하이픈이 있을 때 차단(글자 자체를 고치는 교정은 나머지가 달라져 자연 보존).
def is_hyphen_space_swap(c) -> bool:
    """⑦ 하이픈↔공백/하이픈 제거만 다른 표기 변경(글자 불변)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    if "-" not in c.original and "‑" not in c.original and "–" not in c.original:
        return False
    norm = lambda s: re.sub(r"[\-‑–\s]", "", s)
    return norm(c.original) == norm(c.corrected)


# ── ⑧ 붙임표(-) → 가운뎃점(·) ─────────────────────────────
# '식별–유치–채용–육성–유지'→'식별·유치·채용·육성·유지'처럼 저자가 **붙임표(-, –)로 연결한
#   낱말의 나열**을 국립국어원 문장부호 규정을 명분으로 가운뎃점(·)으로 바꾸는 AI 교정.
#   저자는 붙임표로 '과정·순환·연계·루트(경로)'의 의미(A-B-C 진행/변환)를 담는데, 가운뎃점은
#   대등 열거라 **저자 의도를 파괴**한다(사용자 보고 2026-07-01: 30년 편집자 판단으로 오교정).
#   원문에 붙임표가 있고 교정문에 가운뎃점이 있으며, 붙임표·가운뎃점·공백을 뺀 나머지 글자가
#   완전히 동일할 때만 차단(글자를 실제로 고치는 교정은 나머지가 달라져 자연 보존).
_DASH_CHARS   = "-‐‑‒–—―−﹘﹣－"        # 하이픈-마이너스 + 대시 계열
_MIDDOT_CHARS = "·・‧⋅•∙"                # 가운뎃점 계열(U+00B7 등)
_DASH_MIDDOT_WS_RE = re.compile(f"[{re.escape(_DASH_CHARS + _MIDDOT_CHARS)}\\s]")


def is_dash_to_middot(c) -> bool:
    """⑧ 붙임표(-/–)를 가운뎃점(·)으로 바꾸는 표기 변경(글자 불변·저자 나열 의도 파괴)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o, cr = c.original, c.corrected
    if not any(ch in o for ch in _DASH_CHARS):
        return False
    if not any(ch in cr for ch in _MIDDOT_CHARS):
        return False
    # 붙임표·가운뎃점·공백을 지운 '알맹이'가 같으면 부호(및 공백) 차이뿐 → 저자 표기 존중.
    return _DASH_MIDDOT_WS_RE.sub("", o) == _DASH_MIDDOT_WS_RE.sub("", cr)


# ── ⑨ (저자, 연도) 인용 표기 재배치 ───────────────────────
# '(Startup Genome, 2024;2025)'→'Startup Genome(2024; 2025)'처럼 저자의 **올바른 (저자, 연도)
#   인용/참고문헌 표기**를 AI가 '통일'을 명분으로 괄호 위치·쉼표·세미콜론·공백만 재배치하는 교정.
#   인용 양식은 저자·편집자·학회 스타일의 몫이지 맞춤법이 아니다(사용자 보고 2026-07-01).
#   ⚠ **글자·숫자(알맹이)가 완전히 동일**하고 부호·공백만 다를 때만 차단 → 실제 오탈자(글자/숫자
#   변경)는 나머지가 달라져 자연 보존. 라틴 저자명 + 4자리 연도 + 괄호가 모두 있어야(=저자-연도
#   인용 시그니처) 발동해 일반 영문 나열/제목 등 오발동을 막는다.
#   ⚠ 알맹이 = **영숫자·한글만 남긴 것**(문장부호·공백을 특정 집합으로 열거하지 않는다). 과거
#   열거식('(),;.:' 등)은 연도 사이 '/'('2024/2025'→'2024; 2025')·붙임표('2024-2025') 같은 다른
#   구분자로 재배치할 때 알맹이가 어긋나(/ 잔존) 놓쳤다(사용자 보고 2026-07-01: OECD, 2024/2025 →
#   OECD(2024; 2025) 미차단). 영숫자·한글만 비교하면 **어떤 부호 재배치든** 포착된다.
_CITATION_KERNEL_RE = re.compile(r"[^0-9A-Za-z가-힣]")   # 영숫자·한글 외(부호·공백) 전부 제거


def is_citation_reformat(c) -> bool:
    """⑨ (저자, 연도) 인용 표기의 괄호·부호·공백만 재배치하는 AI 교정(알맹이 불변)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o, cr = c.original, c.corrected
    if not re.search(r"[A-Za-z]", o):          # 라틴 저자/출처명
        return False
    if not re.search(r"\d{4}", o):             # 연도(4자리)
        return False
    if "(" not in (o + cr) and ")" not in (o + cr):  # 괄호 인용
        return False
    # 영숫자·한글(알맹이)만 남겨 같으면 부호·공백 재배치일 뿐 → 인용 양식 변경, 차단.
    return _CITATION_KERNEL_RE.sub("", o) == _CITATION_KERNEL_RE.sub("", cr)


# ── ⑩ 쌍점(:) → 가운뎃점(·) ───────────────────────────────
# '국내:해외'→'국내·해외'처럼 저자가 **쌍점(:)으로 표현한 비율·대비**를 '문서 내 일관성'을 명분으로
#   가운뎃점(·)으로 바꾸는 AI 교정. 쌍점은 비율(6:4)·점수(2:1)·시각(3:30)·대비(국내:해외)를 담는
#   부호라 가운뎃점(대등 열거)으로 바꾸면 **저자 의도를 파괴**한다(사용자 보고 2026-07-01: 뒤에
#   '6:4' 비율이 이어져 '국내:해외'가 대비/비율임이 확정 — high 자동적용 카드였음).
#   ⚠ 쌍점은 열거 부호를 잘못 쓴 것이 아니라 별개 용법이므로 '쌍점→가운뎃점'은 정당한 교정이 될 수
#   없다 → ⑧과 동일하게 완전 드롭(억제 방향). 원문에 쌍점, 교정문에 가운뎃점이 있고 쌍점·가운뎃점·
#   공백을 뺀 나머지 글자가 완전히 동일할 때만 발동(실제 글자 교정은 나머지가 달라져 자연 보존).
_COLON_CHARS = ":：∶꞉"                    # ASCII/전각 쌍점 + 비율(RATIO)·수식자 쌍점
_COLON_MIDDOT_WS_RE = re.compile(f"[{re.escape(_COLON_CHARS + _MIDDOT_CHARS)}\\s]")


def is_colon_to_middot(c) -> bool:
    """⑩ 쌍점(:)을 가운뎃점(·)으로 바꾸는 표기 변경(글자 불변·저자 비율/대비 의도 파괴)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o, cr = c.original, c.corrected
    if not any(ch in o for ch in _COLON_CHARS):
        return False
    if not any(ch in cr for ch in _MIDDOT_CHARS):
        return False
    # 쌍점·가운뎃점·공백을 지운 '알맹이'가 같으면 부호(및 공백) 차이뿐 → 저자 표기 존중.
    return _COLON_MIDDOT_WS_RE.sub("", o) == _COLON_MIDDOT_WS_RE.sub("", cr)


# ── ⑪ 복합명사 분리(순수 재띄어쓰기) → 결정론 다수결에 위임 ─────────────────
# '인재전략'→'인재 전략'처럼 AI가 **한글 복합명사를 공백으로 쪼개는 분리**를 '문서 내 일관성'
#   ('글로서리 통일' 포함 — 앞 청크 결정을 뒤 청크에 전파하는 AI 교정)을 명분으로 제안하는 교정.
#   문제: AI는 (a) 다수/소수를 세지 않아 **소수형으로 통일**할 수 있고, (b) '다른 부분에 있다'는
#   주장을 **검증 없이** 하며(환각 가능), (c) 카드 문구가 결정론 다수결 카드('성장단계'→'성장 단계',
#   N회 우세)와 **달라 혼란**을 준다(사용자 결정 2026-07-01: "띄어쓰기 일관성은 다수표기 통일이
#   원칙"). → **드롭**하고 `morph.find_compound_spacing_consistency`(문서 실제 빈도로 다수 방향
#   결정)에 일원화한다. 두 형태가 다 있으면 결정론이 다수로 통일하고, AI가 근거 없이 주장한
#   경우(다른 형태 미존재)는 카드 미표출(억제·안전). ※청크 스킵(타임아웃)이 있어도 결정론 다수결은
#   **문서 전체 텍스트**를 세므로 판정이 오염되지 않는다(오히려 결정론이 AI 누락 구간을 메운다).
#   ⚠ **분리(공백 추가) 방향 + 쪼개지는 어절이 순수 한글**일 때만 발동: 조사 붙이기(join,
#   '분야 보다도'→'분야보다도')는 반대 방향이라 제외하고, 외래어/숫자 병기 띄어쓰기('A모델'→'A 모델',
#   경계가 라틴↔한글)는 설계상 AI 담당이라 건드리지 않는다([[eomun/⑩]] 설계). 라틴/숫자가 **별도
#   어절**로 붙어도('AI 인재확보'→'AI 인재 확보', 쪼개지는 어절 '인재확보'가 순수 한글) 발동한다.
#   ⚠⚠ **어절 단위 판정이 핵심**: 결정론 다수결은 '어절→조사제거→비한글제거' base(순수 한글,
#   len≥4)만 받는다. 그래서 **숫자/라틴이 어절 안에 융합**된 '2차전지'→'2차 전지'는 드롭하면
#   결정론이 base='차전지'(3글자)로 **스킵**해 카드가 통째로 사라진다(회귀). → 쪼개지는 어절이 순수
#   한글일 때만 위임하고, 융합 어절은 **보존**(2026-07-01 후속6, '2차전지' 회귀 수정).
def is_compound_split_respacing(c) -> bool:
    """⑪ 한글 복합명사 어절을 공백으로 쪼개는 분리(글자 불변) → 결정론 다수결에 위임(드롭)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o, cr = c.original, c.corrected
    if o.replace(" ", "") != cr.replace(" ", ""):      # 순수 재띄어쓰기(글자 불변)만
        return False
    o_toks, c_toks = o.split(), cr.split()
    if len(c_toks) <= len(o_toks):                     # 분리(어절 수 증가) 방향만 — join/재배치 제외
        return False
    # 원문 각 어절이 교정문 어절들로 어떻게 쪼개졌는지 순차 매칭(글자 순서 보존).
    #   순수 분리라면 원문 어절 경계는 교정문 어절 경계의 부분집합 → 어절별로 소비된다.
    j, fired = 0, False
    for ot in o_toks:
        buf = ""
        while j < len(c_toks) and len(buf) < len(ot):
            buf += c_toks[j]
            j += 1
            if buf == ot and len(buf) >= len(ot):
                break
        if buf != ot:                                  # 어절 경계 어긋남(재배치) → 대상 아님
            return False
        # 이 원문 어절이 2개 이상으로 쪼개졌다면, 그 어절은 **순수 한글**이어야 위임(드롭).
        #   융합 어절('2차전지'·'A모델')은 결정론이 못 받으므로 보존.
        if buf != c_toks[j - 1]:                       # 마지막 소비 어절과 다르면 = 여러 어절로 분리됨
            if not all("가" <= ch <= "힣" for ch in ot):
                return False
            fired = True
    return fired and j == len(c_toks)


# ── ⑫ 쉼표(,) 가감 → 저자 문장부호 존중(드롭) ─────────────────────────────
# '…적용되지 않아, 초기…'→'…적용되지 않아 초기…'처럼 AI가 **저자의 쉼표(,)만 넣거나 빼는** 교정을
#   '불필요한 쉼표 삭제' 명분으로 제안하는 것. 쉼표는 절·열거의 호흡을 정하는 **저자·편집자 문장부호
#   판단**이지 오탈자·띄어쓰기가 아니다(사용자 보고 2026-07-01: 오탈자/띄어쓰기 교정에서 쉼표 삭제는
#   과교정). → 글자(알맹이)가 완전히 같고 **쉼표 개수만 다를 때** 완전 드롭(양방향: 삭제·추가 모두).
#   ⚠ **자릿수 구분 쉼표(3,000)는 제외** — 그건 숫자 표기라 ⑤ is_number_change 영역(숫자값 동일이면
#   보존). 실제 글자 교정(오탈자)이 섞이면 알맹이가 달라져 자연 보존.
_COMMA_CHARS = ",，、"
_DIGIT_COMMA_RE = re.compile(r"(?<=\d)[,，](?=\d)")   # 자릿수 구분 쉼표(1,000)만 — 숫자 영역
_COMMA_WS_RE = re.compile(r"[,，、\s]")


def is_comma_edit(c) -> bool:
    """⑫ 저자 쉼표(,)만 가감하는 문장부호 교정(글자 불변) → 저자 문장부호 존중(드롭)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    o = _DIGIT_COMMA_RE.sub("", c.original)     # 자릿수 쉼표 제거 후 절·열거 쉼표만 비교
    cr = _DIGIT_COMMA_RE.sub("", c.corrected)
    oc = sum(o.count(ch) for ch in _COMMA_CHARS)
    cc = sum(cr.count(ch) for ch in _COMMA_CHARS)
    if oc == cc:                                # 절 쉼표 개수 변화가 없으면 대상 아님
        return False
    # 쉼표·공백을 뺀 알맹이가 같으면 쉼표(및 공백) 가감뿐 → 저자 문장부호 존중.
    return _COMMA_WS_RE.sub("", o) == _COMMA_WS_RE.sub("", cr)


# ── ⑬ 아라비아 숫자 → 한글 수사(한자어 읽기) 표기 변환 → 저자 숫자 표기 존중(드롭) ──────
# '2차전지'→'이차전지', '3세대'→'삼세대'처럼 AI가 **저자의 아라비아 숫자를 한글 수사로 바꾸는**
#   교정('국립국어원 표기 관례' 명분)을 제안하는 것. 숫자를 한글로 풀어 쓸지는 **표기 스타일 결정**
#   이지 오탈자·맞춤법 교정이 아니다(사용자 결정 2026-07-01: 저자 숫자 표기 존중 — ⑤ 숫자값·⑥ 영문
#   음역과 동일 철학). ⚠ ⑤(is_number_change)는 숫자'값'이 바뀔 때(11→9)만 발동하고 여기선 값은
#   같고 표기(2↔이)만 바뀐다. → **원문의 각 아라비아 숫자열을 한자어 수사로 읽은 결과가 교정문과
#   정확히 일치할 때만** 드롭(정밀 — 오탈자 치환 '2세대차량→차세대차량'은 읽기 불일치라 자연 보존).
#   ※한자어(Sino) 읽기만 처리 — 고유어('2개'→'두 개')는 대상 아님(다음절·거짓양성 위험). 지원 범위
#   밖 큰 수는 불일치로 보존(안전). 값 자체가 다르면(⑤ 영역) 역시 불일치라 보존.
_SINO_DIGITS = "영일이삼사오육칠팔구"
_SINO_SMALL_UNITS = ("", "십", "백", "천")
_SINO_BIG_UNITS = ("", "만", "억", "조", "경")


def _sino_read4(n: int) -> str:
    """0 <= n < 10000 → 한자어 수사(예: 21→'이십일', 100→'백')."""
    out = ""
    s = str(n)
    L = len(s)
    for i, ch in enumerate(s):
        d = int(ch)
        pos = L - 1 - i                       # 자릿수(0=일, 1=십, 2=백, 3=천)
        if d == 0:
            continue
        if d == 1 and pos > 0:                # 십·백·천은 '일' 생략(일십→십)
            out += _SINO_SMALL_UNITS[pos]
        else:
            out += _SINO_DIGITS[d] + _SINO_SMALL_UNITS[pos]
    return out


def _sino_read(num_str: str) -> str:
    """아라비아 숫자열 → 한자어 수사. 지원 범위(경 미만) 밖이면 원본 반환(→ 숫자 잔존으로 보존)."""
    try:
        n = int(num_str)
    except ValueError:
        return num_str
    if n == 0:
        return "영"
    groups = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    if len(groups) > len(_SINO_BIG_UNITS):    # 경 초과 → 미지원(원본 유지 → 안전 보존)
        return num_str
    out = ""
    for i in range(len(groups) - 1, -1, -1):
        if groups[i] == 0:
            continue
        out += _sino_read4(groups[i]) + _SINO_BIG_UNITS[i]
    return out


def is_numeral_to_hangul(c) -> bool:
    """⑬ 아라비아 숫자를 한자어 수사 표기로 바꾸는 AI 교정(값 동일·표기만) → 저자 표기 존중(드롭)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    if not re.search(r"\d", c.original):
        return False
    # 원문의 각 아라비아 숫자열을 한자어 수사로 치환 → 교정문과 (공백 무시) 정확히 일치해야 발동.
    converted = re.sub(r"\d+", lambda m: _sino_read(m.group()), c.original)
    if re.search(r"\d", converted):           # 변환 못한 숫자 잔존(지원 범위 밖) → 보존
        return False
    return converted.replace(" ", "") == c.corrected.replace(" ", "")


# ── ⑭ 라틴 대소문자만 변경 → 저자 표기 존중(드롭) ─────────────────────────────
# '유지(Retention)'→'유지(retention)'처럼 AI가 **영문의 대소문자만** 바꾸는 교정을 "괄호 안의
#   외래어는 소문자로 표기하는 것이 일반적인 출판 관례" 같은 **실존하지 않는 규범(환각)**을 명분으로
#   제안하는 것(사용자 보고 2026-07-02). 영문 대소문자(고유명사 첫대문자, 두문자어 AI/GDP, 병기
#   Retention)는 **저자·원저작물의 표기 스타일**이지 맞춤법이 아니다 — ⑤값·⑥음역·⑬수사와 동일 철학.
#   → 원문에 라틴 문자가 있고 **casefold하면 완전 동일**(=대소문자 외 아무것도 안 바뀜)이면 드롭.
#   ⚠ 실제 영문 오타 교정(Desity→Density)은 글자가 달라 casefold 불일치 → 자연 보존. 한글은
#   대소문자가 없어 casefold 무영향이라 한글 교정도 전부 보존. 양방향(소문자화·대문자화 모두) 차단.
_LATIN_RE = re.compile(r"[A-Za-z]")


def is_latin_case_change(c) -> bool:
    """⑭ 라틴 대소문자만 다른 AI 교정(그 외 글자 불변) → 저자 영문 표기 존중(드롭)."""
    if c.source not in ("ai_typo", "ai_polish") or c.original == c.corrected:
        return False
    if not _LATIN_RE.search(c.original):
        return False
    return c.original.casefold() == c.corrected.casefold()


# ── ⑮ 문장 재구성(어간 보존 조사·어미 일괄 변형) → 저자 문장 존중(드롭) ──────────
# '사전 구직 활동이 허용된다.'→'사전 구직 활동을 허용한다.'처럼 **오탈자 스코프(ai_typo)**의 AI가
#   '문맥상 어색함'을 이유로 멀쩡한 문장의 **태·문형을 통째로 바꾸는** 교정(이→을 + 된다→한다,
#   피동→능동). 문장 개입은 윤문(ai_polish) 스코프의 권한이지 오탈자·띄어쓰기가 아니다(사용자 결정
#   2026-07-02: "오탈자 띄어쓰기 교정에서 문장 자체의 변형을 엄격히 금지"). 결정론 시그니처:
#   **어간(공통 접두)은 보존한 채 꼬리(조사·어미)만 바뀐 어절이 2곳 이상**, 서로 다른 변형으로
#   협응(coordinated) — 이는 문법 재구성이지 철자 교정이 아니다.
#   ⚠ 보존되는 것(사용자 명시 carve-out):
#   · **단일 어절** 조사 교정(빠진 조사 추가·중복 조사 제거·받침 불일치 을/를) → 변형 1곳이라 미발동.
#   · 실제 오타 수정(몇일→며칠: 첫 글자부터 달라 공통 접두<2 → 문법형 아님 → 전체 보존).
#   · 같은 오타의 반복 수정('있읍니다 있읍니다'→'있습니다 있습니다': 동일 변형이라 distinct<2 → 보존).
#   · 윤문(ai_polish)은 대상 아님 — 문장 다듬기가 그 스코프의 본분.
_EDGE_NONHANGUL_RE = re.compile(r"^[^가-힣]+|[^가-힣]+$")
_HANGUL_TOKEN_RE = re.compile(r"[가-힣]+")


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def is_sentence_grammar_rewrite(c) -> bool:
    """⑮ 어간 보존·조사/어미만 2곳+ 일괄 변형(문장 재구성, ai_typo만) → 저자 문장 존중(드롭)."""
    if c.source != "ai_typo" or c.original == c.corrected:
        return False
    o_toks, c_toks = c.original.split(), c.corrected.split()
    if len(o_toks) != len(c_toks) or len(o_toks) < 2:   # 어절 수 불변인 문장형만(띄어쓰기 무관)
        return False
    edits = []
    for a, b in zip(o_toks, c_toks):
        if a == b:
            continue
        a2 = _EDGE_NONHANGUL_RE.sub("", a)              # 가장자리 문장부호 제거 후 비교
        b2 = _EDGE_NONHANGUL_RE.sub("", b)
        if (not a2 or not b2 or a2 == b2
                or not _HANGUL_TOKEN_RE.fullmatch(a2)
                or not _HANGUL_TOKEN_RE.fullmatch(b2)):
            return False                                # 비한글/부호만 변경 어절 섞임 → 보존(보수)
        p = _common_prefix_len(a2, b2)
        if p < 2:                                       # 어간 공유 없음(몇일→며칠 오타) → 보존
            return False
        ta, tb = a2[p:], b2[p:]
        if len(ta) > 4 or len(tb) > 4:                  # 꼬리가 길면 단어 교체(다른 가드 영역) → 보존
            return False
        edits.append((ta, tb))
    # 서로 다른 조사/어미 변형이 2곳 이상 협응할 때만 = 문장 재구성.
    return len(edits) >= 2 and len(set(edits)) >= 2


# ── ⑯ 한글 음절 재배열(멀티셋 동일·순서만 변경) → 저자 어순·명칭 존중(드롭) ──────────
# '과학혁신기술부'(영국 DSIT의 확립된 번역명)→'과학기술혁신부'처럼 AI가 "대한민국 부처 명칭
#   관례"를 이유로 **같은 음절들을 재배열**한 교정(2026-07-14 보고 — category=표준어·high로
#   노출된 치명 오교정). 음절 멀티셋이 같고 순서만 다른 변경은 철자 교정이 아니라 **명칭·어순의
#   내용 편집**이다 — 오탈자는 글자를 더하고/빼고/바꾸지, 같은 글자들을 섞지 않는다. 외국 기관명
#   번역·고유 복합어의 어순은 저자(원문) 표기가 기준(⑤값·⑥음역·⑭케이스와 동일 철학).
#   ⚠ 3차 사전 재검증이 구조적으로 못 잡는 부류: 두 형태 모두 등재 성분(과학+혁신+기술+부)으로
#   분해돼 is_known_form=True — 이 결정론 가드가 유일한 방어선.
#   보존: 진짜 오타(멀티셋 상이 — 됬→됐·과확→과학), 띄어쓰기·부호만 변경(한글 시퀀스 동일 →
#   미발동), 윤문(ai_polish)의 어순 다듬기는 그 스코프의 권한이라 대상 아님(⑮와 동일 경계).
_HANGUL_CHAR_RE = re.compile(r"[가-힣]")


def is_syllable_reorder(c) -> bool:
    """⑯ 한글 음절 멀티셋 동일·순서만 다른 AI 교정(ai_typo) → 저자 어순·명칭 존중(드롭)."""
    if c.source != "ai_typo" or c.original == c.corrected:
        return False
    ho = _HANGUL_CHAR_RE.findall(c.original)
    hc = _HANGUL_CHAR_RE.findall(c.corrected)
    if len(ho) < 2 or ho == hc:
        return False
    return sorted(ho) == sorted(hc)


# ── ⑰ 인명(성명) 붙이기 → 고유명사 서식은 교정 대상 아님(드롭) ─────────────────
# '이 우 식'→'이우식'·'이 영 글'→'이영글'처럼 AI가 **음절마다 띄운 인명을 붙이며** "인명은 붙여
#   쓰는 것이 원칙"을 사유로 대는 과교정(사용자 보고 2026-07-21, 연구진 명단). 성명 붙여쓰기(한글
#   맞춤법 제48항)는 실재하나, **음절마다 벌린 '이 우 식'은 명백한 의도적 서식(자간)** 이고 교정
#   도구가 저자의 고유명사 표기·서식을 임의로 재조정하는 것은 과교정이다(코드베이스도 finder의
#   '인명 오분리 가드'로 인명을 안 건드리는 철학 — 붙이는 쪽 방어가 없어 뚫렸다). 발동:
#   순수 재띄어쓰기 JOIN(글자 불변) + 교정문이 인명 꼴(morph.looks_like_korean_name)이고, 다음 중
#   하나 — (구조) 원문이 **단음절 한글 어절 3개 이상**('이 우 식'), 또는 (사유) reason이 '인명…붙여'.
#   ⚠ 인명 꼴 게이트가 핵심: '이 사회'→'이사회'(실단어, 어절 '사회'가 2음절→구조 미충족)·'인재
#   전략'→'인재전략'(looks_like_name=False)은 미발동. 복합명사 결합/조사 붙이기는 인명 꼴이 아니라 보존.
_NAME_WORD_RE = re.compile(r"인명|성명|성함|성과 이름|이름")
_JOIN_WORD_RE = re.compile(r"붙여|붙이|붙임")


def _looks_like_name(word: str) -> bool:
    """교정문이 한국인 성명 꼴인가(morph.looks_like_korean_name). morph 미가용 시 False(보수)."""
    try:
        from core import morph
        return morph.available() and morph.looks_like_korean_name(word)
    except Exception:
        return False


def is_name_join(c) -> bool:
    """⑰ 음절마다 띄운 인명을 붙이는 AI 과교정('이 우 식'→'이우식') → 드롭."""
    if c.source not in ("ai_typo", "ai_polish") or not c.original or c.original == c.corrected:
        return False
    o, cr = c.original.strip(), c.corrected.strip()
    if o.replace(" ", "") != cr.replace(" ", ""):     # 순수 재띄어쓰기(글자 불변)만
        return False
    o_toks = o.split()
    if len(o_toks) < 2 or " " in cr:                  # 원문 다어절 → 교정문 완전 결합(공백 없음)
        return False
    if not _looks_like_name(cr):                      # 교정문이 인명 꼴일 때만(실단어 결합 배제)
        return False
    if all(len(t) == 1 and "가" <= t <= "힣" for t in o_toks) and len(o_toks) >= 3:
        return True                                   # 구조: 3음절+ 음절마다 띄운 인명
    return bool(c.reason and _NAME_WORD_RE.search(c.reason) and _JOIN_WORD_RE.search(c.reason))


# (이름, 술어, 제외 로그 메시지) — ①~④ 순서·문구는 과거 워커 인라인과 동일(무회귀). ⑤~⑰은 추가.
_GUARDS = (
    ("nonkr_trim", is_nonkr_trim,
     "비한글 토큰 잘라내기 교정 {n}건 제외 (영문·표 전문용어 과교정 방지)"),
    ("bracket_change", is_bracket_change,
     "괄호 구조 변경 AI 교정 {n}건 제외 (괄호 조기 닫기·단어 보충 과교정 방지)"),
    ("english_gloss", is_english_gloss,
     "영문 병기 추가 AI 교정 {n}건 제외 (글자 불변·주석 추가 과교정 방지)"),
    ("bulk_delete", is_bulk_delete,
     "대량 삭제 AI 교정 {n}건 제외 (표·그림 캡션 인라인 추출 잡탕 정리 과교정 방지)"),
    ("number_change", is_number_change,
     "숫자 값 변경 AI 교정 {n}건 제외 (수치는 저자 표기 존중 — 내용 편집 방지)"),
    ("latin_to_hangul", is_latin_to_hangul,
     "영문→한글 음역 AI 교정 {n}건 제외 (의도된 영문 표기 존중)"),
    ("hyphen_space", is_hyphen_space_swap,
     "하이픈↔공백 치환 AI 교정 {n}건 제외 (하이픈 합성어 표기 존중)"),
    ("dash_to_middot", is_dash_to_middot,
     "붙임표→가운뎃점 치환 AI 교정 {n}건 제외 (저자의 나열·과정·경로 의도 존중)"),
    ("citation_reformat", is_citation_reformat,
     "(저자, 연도) 인용 표기 재배치 AI 교정 {n}건 제외 (인용 양식은 저자·학회 스타일 존중)"),
    ("colon_to_middot", is_colon_to_middot,
     "쌍점→가운뎃점 치환 AI 교정 {n}건 제외 (비율·대비·점수·시각 등 쌍점 의도 존중)"),
    ("compound_split_respacing", is_compound_split_respacing,
     "복합명사 분리 AI 교정 {n}건 제외 (띄어쓰기 일관성은 결정론 다수결이 담당)"),
    ("comma_edit", is_comma_edit,
     "쉼표 가감 AI 교정 {n}건 제외 (쉼표는 저자·편집자 문장부호 판단 — 오탈자·띄어쓰기 아님)"),
    ("numeral_to_hangul", is_numeral_to_hangul,
     "아라비아 숫자→한글 수사 표기 변환 AI 교정 {n}건 제외 (저자 숫자 표기 존중 — 표기 스타일 결정)"),
    ("latin_case_change", is_latin_case_change,
     "영문 대소문자 변경 AI 교정 {n}건 제외 (저자·원저작물 영문 표기 존중 — 규범 아님)"),
    ("sentence_grammar_rewrite", is_sentence_grammar_rewrite,
     "문장 재구성 AI 교정 {n}건 제외 (조사·어미 일괄 변형은 오탈자 범위 밖 — 저자 문장 존중)"),
    ("syllable_reorder", is_syllable_reorder,
     "한글 음절 재배열 AI 교정 {n}건 제외 (기관명·복합어 어순은 저자 표기 존중 — 철자 교정 아님)"),
    ("name_join", is_name_join,
     "인명 붙이기 AI 교정 {n}건 제외 (음절마다 띄운 인명 '이 우 식'→'이우식' — 고유명사 서식 존중)"),
)


def filter_overcorrections(ai_list: list, logger=None):
    """AI 교정 목록에서 4종 과교정을 제외한다(과거 워커 인라인 4필터를 1함수로).

    각 항목은 첫 매칭 가드에서 제외(순서: 비한글→괄호→영문병기→대량삭제). 워커와 동일한
    제외 로그를 가드별로 남긴다(logger 주어질 때). 반환: (kept_list, dropped:{guard:건수}).
    """
    kept, dropped = [], {name: 0 for name, _, _ in _GUARDS}
    for c in ai_list:
        hit = next((name for name, fn, _ in _GUARDS if fn(c)), None)
        if hit:
            dropped[hit] += 1
        else:
            kept.append(c)
    if logger:
        for name, _, msg in _GUARDS:
            if dropped[name]:
                logger("  → " + msg.format(n=dropped[name]))
    return kept, dropped


# ══════════════════════════════════════════════════════════
# ▌문서 텍스트가 필요한 가드 — 부분조각 확장
# ══════════════════════════════════════════════════════════
def _norm_ws(s: str) -> str:
    """공백(줄바꿈 포함) 런을 단일 공백으로 — 추출 줄바꿈('소프트\\n스킬')과 카드 공백을 동일 비교."""
    return re.sub(r"\s+", " ", s or "").strip()


# 어절 끝 조사 런 — 뒤 한글 런 '전체'가 조사(연쇄 포함: 에서는·까지도)일 때만 매칭(긴 것부터).
_JOSA_ALT = (
    "으로서|으로써|에게서|에서부터|이라고|이라는|으로|에서|에게|이라|이며|이고|라고|라는|"
    "처럼|보다|마다|조차|밖에|부터|까지|이나|이란|은|는|이|가|을|를|에|의|와|과|도|만|로|나|란"
)
_JOSA_RUN_RE = re.compile(f"(?:{_JOSA_ALT})+")


def _appears_standalone(needle: str, hay: str) -> bool:
    """needle이 hay 안에 **한글 단어에 붙지 않은(독립) 형태**로 한 번이라도 등장하는가?

    즉 매칭 앞뒤 글자가 한글이 아닌(=어절/구 경계) 자리. '소프트웨어 개발'의 '소프트웨어'는 독립
    등장(앞=공백/시작, 뒤=공백)이라 True. 반면 '키메시지' 속 '메시지'는 앞 글자가 '키'(한글)라
    독립 등장이 아니므로(다른 합성어의 일부) False. 더 긴 합성어의 조각인지 가르는 핵심 판정.

    ⚠ **뒤에 붙은 조사 런은 독립 사용이다(2026-07-03)** — '선임·책임연구원으로'의 '책임연구원'은
    조사 '으로'만 붙은 정상 사용인데, 과거 '뒤 한글 = 비독립' 판정 탓에 corrected('책임연구원')가
    문서에 조사형으로만 있으면 AI 조각 확장 '책임연'→'책임연구원'을 못 걸렀다(사용자 보고 —
    적용 시 '책임연구원구원' 오염 위험). 뒤 한글 **런 전체가 조사 연쇄**로 소진될 때만 독립
    인정('개발도상국'의 '도상국'은 조사가 아니라 비독립 유지 — 합성어 조각 보호 불변).
    """
    if not needle:
        return False
    n = len(needle)
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        before = hay[i - 1] if i > 0 else ""
        after = hay[i + n] if i + n < len(hay) else ""
        if not ("가" <= before <= "힣"):
            if not ("가" <= after <= "힣"):
                return True
            # 뒤 한글 런 전체가 조사면 독립 사용(책임연구원+으로)
            k = i + n
            while k < len(hay) and "가" <= hay[k] <= "힣":
                k += 1
            if _JOSA_RUN_RE.fullmatch(hay[i + n:k]):
                return True
        start = i + 1


def filter_redundant_expansions(ai_list: list, document_text: str, logger=None):
    """원문 조각을 '문서에 **독립 단어로** 이미 있는 더 긴 표기'로 부풀리는 AI 과교정을 제외한다.

    예) '소프트'→'소프트웨어'(문서에 '소프트웨어'가 독립 단어로 존재), '소규모·저'→'소규모·저매출',
        '문제 해결력·소프트'→'문제 해결력·소프트 스킬'(문서에 그 구가 독립으로 존재). 모두
        **original이 corrected의 부분문자열**이고, corrected가 문서에 **독립 단어/구로** 등장한다
        → AI가 멀쩡한 더 긴 표기의 '조각'을 잡아 확장한 것이다.

    ⚠ **독립 등장**으로 좁히는 게 핵심(사용자 보고): 단순 부분문자열 검사는 '메시'→'메시지'를
       '메시지'가 '키메시지' 속에 있다는 이유로 잘못 제외해, 정작 오탈자 '메시를'→'메시지를'(일관성
       Case A가 이 canon에서 파생)까지 죽였다. '메시지'는 '키메시지'에 **붙어서만** 나오고 독립
       단어로는 없으므로, 독립 등장 판정이면 '메시'→'메시지'는 보존된다(진짜 오탈자 교정).

    ⚠ **조사형 확장 + 공백 무시 판정(2026-07-15 실측 '미생성이'→'미생성 코드가')**: AI가 조사를
       갈아끼우며 확장하면 original이 corrected의 부분문자열이 아니라 기존 판정을 벗어났고,
       교정문은 띄어 쓴 '미생성 코드'인데 문서 표기는 붙여 쓴 '미생성코드'라 독립 등장 검사도
       비껴갔다. 이 씨앗 하나를 일관성 Case A가 bare 카드('미생성'→'미생성 코드')로 문서 전체에
       전파해 '미생성코드' 524곳이 '미생성 코드코드' 오염 위기에 놓였다. → 양쪽 조사를 뗀 base로
       재판정하고, 독립 등장은 공백 제거형('미생성코드')도 함께 본다.

    이 과교정은 (a) 같은 조각이 본문에 수십~수백 번 나오면 검수 카드가 그만큼 폭증하고,
    (b) corrected ⊇ original이라 적용 시 자기재매칭으로 증식·오염된다. → 카드 생성 단계에서 제외.

    반환: 걸러낸 리스트(원본 미변경). 문서 텍스트 없으면 입력 그대로.
    """
    if not ai_list or not document_text:
        return ai_list
    doc = _norm_ws(document_text)

    def _standalone_any(s: str) -> bool:
        # 띄어 쓴 교정문('미생성 코드')과 붙여 쓴 문서 표기('미생성코드')는 같은 확장이다.
        n = _norm_ws(s)
        if _appears_standalone(n, doc):
            return True
        ds = n.replace(" ", "")
        return ds != n and _appears_standalone(ds, doc)

    kept, dropped = [], 0
    for c in ai_list:
        o, cr = (c.original or "").strip(), (c.corrected or "").strip()
        if c.source in ("ai_typo", "ai_polish") and o and o != cr:
            # (a) bare 확장 — original 그대로가 corrected의 진부분('소프트'→'소프트웨어').
            if o in cr and len(cr) > len(o) and _standalone_any(cr):
                dropped += 1
                continue
            # (b) 조사형 확장 — 조사를 떼면 base가 진부분('미생성[이]'→'미생성 코드[가]').
            #     kiwi 미가용 시 _strip_josa가 입력을 그대로 돌려줘 (a)와 동일 판정(무회귀).
            bo, bcr = _strip_josa(o), _strip_josa(cr)
            if (len(bo) >= 2 and bo != bcr and bo in bcr and len(bcr) > len(bo)
                    and _standalone_any(bcr)):
                dropped += 1
                continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 부분조각 확장 AI 교정 {dropped}건 제외 "
               f"(문서에 독립 단어로 있는 더 긴 표기의 조각 — 카드 폭증·자기증식 방지)")
    return kept


# ══════════════════════════════════════════════════════════
# ▌환각 '문서 내 일관성' 재띄어쓰기 드롭 — 문서 텍스트로 자체 검증
# ══════════════════════════════════════════════════════════
# '수급자_사망의심자 처리완료율'→'수급자_사망 의심자 처리완료율'(사유 "문서 내 일관성 유지")처럼
# AI가 **'문서 내 일관성'을 명분으로 복합명사를 분리**하지만, 정작 그 **띄어 쓴 형태('사망 의심자')가
# 문서 어디에도 없는** 경우(사용자 보고 2026-07-21, hwp 실측). 프롬프트 지침 "문서 내 일관성 강제"
# (core/prompts.py)를 AI가 사유로 되뇌며 근거 없이 분리한 **환각**이다. [M] is_compound_split_respacing
# (복합명사 분리 드롭→결정론 다수결 위임)이 못 잡는 이유: 원문 어절 '수급자_사망의심자'에 **밑줄(_)이
# 섞여** '순수 한글' 검사에서 탈락 → AI 카드가 그대로 high로 통과. 결정론 다수결도 밑줄 어절은
# 다루지 않아 사각. → **문서로 자체 검증**: AI가 새로 띄운 '한글런 A 공백 한글런 B' 구가 문서에
# (공백 정규화 후) 하나도 없으면 '일관성' 근거가 실재하지 않는 것 → 드롭.
#   ⚠ **사유 게이트 필수**: 문법 규칙 기반 분리(보조용언 '협력해야한다'→'협력해야 한다')는 띄어 쓴
#   형태가 문서에 없어도 정당하다 → **사유가 '일관성/문서/통일'을 근거로 들 때만** 발동해 규칙 교정을
#   보호한다. 실재하는 일관성(띄어 쓴 형태가 문서에 있음)은 발동하지 않아 정당한 통일은 보존된다.
_CONSISTENCY_CLAIM_RE = re.compile(r"일관성|문서 내|다수 표기|통일")


def _introduced_spaced_bigrams(o: str, cr: str):
    """순수 재띄어쓰기 '분리'에서 AI가 새로 만든 '한글런 공백 한글런' 구들을 반환.

    글자 불변(공백만 추가)이 아니거나 분리(어절 수 증가) 방향이 아니면 []. 원문 어절이 여러
    교정 어절로 쪼개진 경계마다, 좌 어절의 **끝 한글런** + 우 어절의 **앞 한글런**을 이어 붙인
    구를 만든다('수급자_사망'|'의심자' → '사망 의심자'). 정렬 실패(재배치)는 [].
    """
    if o.replace(" ", "") != cr.replace(" ", ""):
        return []
    o_toks, c_toks = o.split(), cr.split()
    if len(c_toks) <= len(o_toks):
        return []
    bigrams, j = [], 0
    for ot in o_toks:
        grp, buf = [], ""
        while j < len(c_toks) and len(buf) < len(ot):
            buf += c_toks[j]
            grp.append(c_toks[j])
            j += 1
            if buf == ot:
                break
        if buf != ot:
            return []                     # 어절 경계 어긋남(재배치) → 대상 아님
        if len(grp) >= 2:                 # 이 어절이 2개 이상으로 분리됨
            for a, b in zip(grp, grp[1:]):
                left = re.search(r"[가-힣]+$", a)
                right = re.search(r"^[가-힣]+", b)
                if left and right:
                    bigrams.append(left.group() + " " + right.group())
    if j != len(c_toks):
        return []
    return bigrams


def drop_hallucinated_consistency_respacing(ai_list: list, document_text: str, logger=None):
    """'문서 내 일관성'을 명분으로 분리했으나 **띄어 쓴 형태가 문서에 없는** AI 재띄어쓰기를 드롭.

    사유가 '일관성/문서 내/통일'을 근거로 들고(reason 게이트), 순수 재띄어쓰기 분리이며, AI가
    새로 띄운 '한글런 공백 한글런' 구가 문서(공백 정규화)에 하나도 없으면 근거 없는 환각 → 제외.
    실재하는 일관성(띄어 쓴 형태가 문서에 존재)·규칙 기반 분리(사유 게이트 밖)는 보존한다.
    문서 텍스트 없으면 입력 그대로.

    반환: 걸러낸 리스트(원본 미변경).
    """
    if not ai_list or not document_text:
        return ai_list
    doc = _norm_ws(document_text)
    kept, dropped = [], 0
    for c in ai_list:
        if (c.source in ("ai_typo", "ai_polish") and c.reason
                and _CONSISTENCY_CLAIM_RE.search(c.reason)):
            bigrams = _introduced_spaced_bigrams(
                (c.original or "").strip(), (c.corrected or "").strip())
            if bigrams and not any(bg in doc for bg in bigrams):
                dropped += 1
                continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 환각 '일관성' 재띄어쓰기 AI 교정 {dropped}건 제외 "
               f"(띄어 쓴 형태가 문서에 없음 — 근거 없는 분리)")
    return kept


# ══════════════════════════════════════════════════════════
# ▌⑰ 영문 병기(괄호) 앵커 명칭 치환 드롭 — 문서 텍스트 필요
# ══════════════════════════════════════════════════════════
# '과학산업자원부(Department of Industry, Science and Resources, DISR)'의 앞 낱말을
#   AI가 "호주 정부 부처명의 한국어 관용 표기 반영"이라며 **'산업통상자원부'(전혀 다른 —
#   대한민국 — 기관명)로 교체**한 치명 오교정(2026-07-14 보고, ⑯ 재배열 보고와 같은 부류의
#   두 번째 변종 — 이번엔 음절 멀티셋 자체가 바뀌어 ⑯ 미발동). 결정론 앵커:
#   **문서에서 그 낱말 바로 뒤에 라틴 원어 병기 괄호가 붙어 있으면** 그 한글은 저자가 원어에
#   대응시킨 번역·명칭이다 — 정체가 괄호로 고정된 낱말의 대규모 치환은 표기 교정이 아니라
#   **개체(entity) 개명**이므로 차단한다. [K] 단어교체 가드는 공유 음절 0을 요구해 이 부류
#   (산·업·자·원·부 5음절 공유)를 못 잡는다 — 규모(자모거리) + 병기 앵커로 가른다.
#   보존: 소규모 표기 교정(플랫홈→플랫폼·아키텍쳐→아키텍처, 자모거리 1~3)·조사 변경(가시성
#   (visibility)가→을, ㉒의 영역)·병기 없는 낱말(앵커 없음 — 미발동)·다어절 교정(윤문 스코프).
_GLOSS_ANCHOR_FMT = r"{w}\s?[(（][^)）]*[A-Za-z][^)）]*[)）]"
_HANGUL_RUN_RE = re.compile(r"[가-힣]+")


def _is_glossed_name_swap(o: str, cr: str, doc: str) -> bool:
    """단일 낱말 o→cr이 '원어 병기로 정체가 고정된 명칭'의 대규모 치환인가."""
    if not o or not cr or o == cr or " " in o or " " in cr:
        return False
    ho = "".join(_HANGUL_RUN_RE.findall(o))
    hc = "".join(_HANGUL_RUN_RE.findall(cr))
    if not ho or not hc or ho == hc:
        return False
    if _jamo_distance(ho, hc) < 4:               # 표기 교정·조사 변경(1~3) → 보존
        return False
    runs = _HANGUL_RUN_RE.findall(o)
    base = max(runs, key=len)
    stripped = _strip_josa(base)                  # 조사형 카드('…부는')도 병기 등장에 앵커
    cands = {base, stripped} if stripped else {base}
    for w in cands:
        if len(w) < 2:
            continue
        if re.search(_GLOSS_ANCHOR_FMT.format(w=re.escape(w)), doc):
            return True
    return False


def drop_glossed_name_substitution(ai_list: list, document_text: str, logger=None):
    """⑰ 원어 병기 괄호가 뒤따르는 명칭을 다른 명칭으로 통째 바꾸는 AI 교정을 제외한다.

    ai_typo·ai_polish 단일 낱말 카드만 대상. 반환: 걸러낸 리스트(원본 미변경).
    문서 텍스트 없으면 입력 그대로(graceful).
    """
    if not ai_list or not document_text:
        return ai_list
    kept, dropped = [], 0
    for c in ai_list:
        if (c.source in ("ai_typo", "ai_polish")
                and _is_glossed_name_swap((c.original or "").strip(),
                                          (c.corrected or "").strip(), document_text)):
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 원어 병기 명칭 치환 AI 교정 {dropped}건 제외 "
               f"(괄호 병기로 정체가 고정된 기관·용어 명칭은 저자 표기 존중)")
    return kept


# ══════════════════════════════════════════════════════════
# ▌괄호 뒤 조사 받침 호응 보정 — AI 조사 교정 fix-up (드롭 아님)
# ══════════════════════════════════════════════════════════
# AI가 닫는 괄호 바로 뒤 조사를 바꿀 때(격 판단은 옳음), 받침 호응을 **괄호 앞 체언**이
# 아니라 괄호 안 말미(영문 등)에 이끌려 틀리는 오교정(사용자 보고 2026-07-06):
#   '가시성(visibility)가 확보하고' → AI '…)를 확보하고'  (호응 대상 '가시성' 받침 ㅇ → '을')
# 게다가 AI 원문 앵커가 '(visibility)가…'처럼 괄호 중간부터 잘려 결정론 find_paren_josa
# 카드와 겹치면 검수 겹침 해소가 긴 AI 카드만 남긴다 → AI 형태를 결정론으로 보정하는 게 정답.
# josa_rules.find_paren_josa와 동일 원칙(호응 대상=괄호 앞 체언, 순수 번호/라벨 괄호는 제외),
# reconcile_josa 재사용. **조사 한 글자 형태만** 고치고 격 선택(주격→목적격)은 AI 판단 존중.
_CLOSE_TO_OPEN = {")": "(", "）": "（", "]": "[", "〕": "〔", "】": "【"}
_SWAP_JOSA = frozenset({"이", "가", "을", "를", "은", "는", "과", "와", "로", "으로"})
_LABEL_INNER_RE = re.compile(r"\s*\d[\d\s.,·\-]*")   # 순수 번호/라벨 (josa_rules ⑯과 동일)


def _single_diff(o: str, cr: str):
    """o→cr의 공통 접두/접미를 벗긴 단일 변경 구간 (prefix, old, new, suffix). 동일하면 None."""
    if o == cr:
        return None
    i, n_o, n_c = 0, len(o), len(cr)
    while i < n_o and i < n_c and o[i] == cr[i]:
        i += 1
    j = 0
    while j < n_o - i and j < n_c - i and o[n_o - 1 - j] == cr[n_c - 1 - j]:
        j += 1
    return o[:i], o[i:n_o - j], cr[i:n_c - j], o[n_o - j:]


def _paren_host_syllable(o: str, close_idx: int, doc: str):
    """o[close_idx](닫는 괄호)의 짝 여는 괄호를 찾아 그 앞 한글 음절과 괄호 안 내용을 반환.

    o 안에서 짝을 못 찾으면(AI 앵커가 괄호 중간부터 잘린 경우) 문서 문맥(doc)에서 o의 첫
    등장을 찾아 앞쪽으로 스캔한다. 여는 괄호 앞 공백 1칸 허용(find_paren_josa와 동일).
    반환 (host_syllable | None, inner | None) — host가 None이면 판정 불가(손대지 않음).
    """
    close = o[close_idx]
    open_ch = _CLOSE_TO_OPEN[close]

    def scan(s: str, ci: int):
        depth, k, floor = 1, ci - 1, max(0, ci - 80)
        while k >= floor:
            ch = s[k]
            if ch == close:
                depth += 1
            elif ch == open_ch:
                depth -= 1
                if depth == 0:
                    h = k - 1
                    if h >= 0 and s[h] == " ":
                        h -= 1
                    if h >= 0 and "가" <= s[h] <= "힣":
                        return s[h], s[k + 1:ci]
                    return None, s[k + 1:ci]
            k -= 1
        return None   # 짝 미발견(범위 초과·문자열 시작) — 문서 문맥 필요

    r = scan(o, close_idx)
    if r is None and doc:
        p = doc.find(o)
        if p >= 0:
            r = scan(doc, p + close_idx)
    return r if r is not None else (None, None)


def fix_paren_josa_agreement(ai_list: list, document_text: str, logger=None) -> list:
    """닫는 괄호 바로 뒤 조사를 바꾼 AI 교정의 받침 호응을 괄호 앞 체언으로 보정한다.

    발동(전부 충족일 때만 — 보수적):
      · 소스 ai_typo/ai_polish, 원문→교정문 차이가 **단일 구간**이고 양쪽 다 조사
        (이/가·을/를·은/는·과/와·로/으로)일 것(=순수 조사 교체).
      · 그 조사 바로 앞이 닫는 괄호류이고, 조사 뒤가 한글이 아닐 것(긴 단어 오인 방지).
      · 짝 여는 괄호 앞이 한글 체언일 것. 괄호 안이 순수 번호/라벨('식 (4)')이면 제외
        (호응 대상이 번호 읽음 — josa_rules ⑯과 동일 정책).
    보정 결과가 원문과 같아지면(AI가 받침 방향만 뒤집은 무의미 교체) 카드를 드롭한다.
    반환: 리스트(제자리 수정 + 드롭 반영). josa_rules.reconcile_josa 재사용(결정론).
    """
    if not ai_list:
        return ai_list
    from core.josa_rules import reconcile_josa
    out, fixed_n, dropped = [], 0, 0
    for c in ai_list:
        o, cr = c.original or "", c.corrected or ""
        if c.source not in ("ai_typo", "ai_polish") or not o or not cr:
            out.append(c)
            continue
        d = _single_diff(o, cr)
        if not d:
            out.append(c)
            continue
        prefix, old_j, new_j, suffix = d
        # 로↔으로 삽입/삭제는 공통 접미 탐욕이 '로'를 먹어 old/new가 ''/'으'로 갈라진다 — 재해석.
        if old_j == "" and new_j == "으" and suffix.startswith("로"):
            old_j, new_j, suffix = "로", "으로", suffix[1:]
        elif old_j == "으" and new_j == "" and suffix.startswith("로"):
            old_j, new_j, suffix = "으로", "로", suffix[1:]
        if (old_j not in _SWAP_JOSA or new_j not in _SWAP_JOSA
                or not prefix or prefix[-1] not in _CLOSE_TO_OPEN
                or (suffix and "가" <= suffix[0] <= "힣")):
            out.append(c)
            continue
        host, inner = _paren_host_syllable(o, len(prefix) - 1, document_text or "")
        if host is None or _LABEL_INNER_RE.fullmatch(inner or ""):
            out.append(c)
            continue
        jong = (ord(host) - 0xAC00) % 28
        if new_j == "로":       # bare '로'는 reconcile_josa가 모호 처리 — 괄호 문맥은 결정 가능
            correct = "로" if jong in (0, 8) else "으로"
        else:
            correct = reconcile_josa(host, new_j)
        if correct == new_j:
            out.append(c)
            continue
        new_cr = prefix + correct + suffix
        if new_cr == o:
            dropped += 1        # AI가 받침 방향만 잘못 뒤집음 → 교정 무의미
            continue
        c.corrected = new_cr
        c.reason = ((c.reason or "").rstrip()
                    + f" (받침 호응 보정 '{new_j}'→'{correct}' — 괄호 앞 체언 호응)")
        fixed_n += 1
        out.append(c)
    if logger and (fixed_n or dropped):
        logger(f"  → 괄호 뒤 조사 받침 호응 보정 {fixed_n}건"
               + (f" · 무의미 조사 교체 {dropped}건 제외" if dropped else "")
               + " (호응 대상=괄호 앞 체언, 결정론)")
    return out


# ══════════════════════════════════════════════════════════
# ▌모호(헤지) 사유 AI 교정 강등 — 확신 없는 판단은 자동 적용 금지
# ══════════════════════════════════════════════════════════
# AI가 스스로 확신하지 못하는 교정은 reason에 헤지 표현을 남긴다:
#   "문맥상 '책임연구원'의 줄임말로 **보이나**, 출판물에서는 명확한 표기를 **권장**함"
# 이런 카드가 high로 남으면 자동 적용 모드에서 그대로 반영된다(사용자 지시 2026-07-03:
# 결정론이 아닌 모호한 AI 교정은 **무조건 low 검수 카드**). 강등은 억제 방향(과교정 0) —
# low는 자동 적용에서 제외되고 검수 카드로만 노출되므로 부작용이 없다.
#   ⚠ AI 원시 출력(ai_list) 단계에서 1회 적용 — 일관성 Case A 변형은 canon의 confidence를
#   복사하므로 여기서 강등하면 전파분까지 low로 일관된다. 이후 reconcile_variant_confidence가
#   같은 단어의 '결정론(high) 형제'가 있을 때만 되올리는데, 그건 결정론 근거가 있는 경우라 정당.
_HEDGE_RE = re.compile(
    r"보이나|보이며|보이지만|보임|보인다|로 보이|추정|짐작|가능성|권장|"
    r"듯하|듯 하|일 수도|수도 있|불명확|모호|애매|확실하지 않|확신할 수"
)


def demote_hedged_corrections(corrections: list, logger=None) -> list:
    """reason에 헤지(추정·권장 등) 표현이 있는 AI 교정을 low로 강등한다(제자리 수정 후 반환)."""
    demoted = 0
    for c in corrections:
        if (c.source in ("ai_typo", "ai_polish") and c.confidence != "low"
                and c.reason and _HEDGE_RE.search(c.reason)):
            c.confidence = "low"
            if not c.reason.startswith("[검수]"):
                c.reason = "[검수] " + c.reason + " (AI 판단 모호 — 검토 필요)"
            demoted += 1
    if logger and demoted:
        logger(f"  → 모호 사유 AI 교정 {demoted}건 검수 카드로 강등 "
               "(추정·권장 등 확신 없는 판단 — 자동 적용 제외)")
    return corrections


# ══════════════════════════════════════════════════════════
# ▌'관용 표기·관례' 주장 사유 AI 교정 강등 — 근거가 규범이 아니면 자동 적용 금지
# ══════════════════════════════════════════════════════════
# 기관 명칭 개명 오교정 2건(⑯ 과학혁신기술부→과학기술혁신부, ⑰ 과학산업자원부→산업통상자원부,
# 2026-07-14)의 공통 사유 패턴: "…부처 명칭 **관례에 따른** 수정", "…한국어 **관용 표기** 반영".
# 실존 규범 조항이 아니라 AI가 지어낸 '관용/관례/관행'을 근거로 대는 교정은 표기 교정이 아닌
# 내용 편집일 위험이 크다. ⑯(재배열)·⑰(병기 앵커)이 못 잡는 변종 — 병기 괄호가 없고 음절도
# 공유하는 명칭 치환 — 의 마지막 그물(사용자 지시 2026-07-14). [S] 헤지 강등과 동일 메커니즘·
# 동일 단계(원시 ai_list 1회 → Case A 전파분까지 low 일관)·억제 방향(low=자동 적용 제외,
# 사전 사실·카드 노출은 유지 — 편집자가 판단). 정당한 근거 문구(맞춤법 제N항·표준 표기·사전
# 등재·외래어 표기법)는 이 낱말들을 쓰지 않아 무영향.
_CONVENTION_RE = re.compile(r"관용|관례|관행|관습|통용")


def demote_convention_claims(corrections: list, logger=None) -> list:
    """reason이 '관용 표기·관례·관행' 주장인 AI 교정을 low로 강등한다(제자리 수정 후 반환)."""
    demoted = 0
    for c in corrections:
        if (c.source in ("ai_typo", "ai_polish") and c.confidence != "low"
                and c.reason and _CONVENTION_RE.search(c.reason)):
            c.confidence = "low"
            if not c.reason.startswith("[검수]"):
                c.reason = ("[검수] " + c.reason
                            + " (근거가 규범 조항 아닌 '관용·관례' 주장 — 검토 필요)")
            demoted += 1
    if logger and demoted:
        logger(f"  → '관용·관례' 사유 AI 교정 {demoted}건 검수 카드로 강등 "
               "(규범 조항 아닌 관례 주장 — 자동 적용 제외)")
    return corrections


# ══════════════════════════════════════════════════════════
# ▌'문맥상' 판단 사유 AI 교정 강등 — 규범이 아닌 문맥적 편집 판단은 자동 적용 금지
# ══════════════════════════════════════════════════════════
# 'LH 주택공사의'→'LH의'("LH(한국토지주택공사)에 '주택공사'가 포함돼 중복 표현임. **문맥상**
# 'LH의'로 수정")처럼 AI가 **규범 조항이 아니라 '문맥/맥락'을 근거로 낱말을 지우거나 바꾸는**
# 교정(중복어 삭제·문맥적 낱말 선택 등, 사용자 보고 2026-07-21). 중복 표현 지적 자체는 맞아도
# 저자가 최초 등장·명확성 때문에 'LH 주택공사'를 의도했을 수 있어 **자동 적용(high)은 과교정** —
# 삭제·치환은 편집자 몫이다. reason에 문맥/맥락을 판단 근거로 든 표현이 있으면 low 검수 카드로
# 강등한다. [S] 헤지·[V] 관용 강등과 **동일 메커니즘·동일 단계**(원시 ai_list 1회 → Case A
# 전파분까지 low 일관)·억제 방향(low=자동 적용 제외, 사전 사실·카드 노출은 유지 — 편집자 판단).
#   ⚠ 규범 근거 문구(맞춤법 제N항·사전 등재·표준 표기·받침 호응·외래어 표기법)는 '문맥/맥락'이란
#   낱말을 쓰지 않아 무영향. 동형이의 문맥 판단('문맥상 가리키다')도 편집 판단이라 검수가 옳다.
_CONTEXT_JUDGMENT_RE = re.compile(r"문맥상|맥락상|문맥적|맥락적|문맥[을를에]|맥락[을를에]")


def demote_contextual_judgment(corrections: list, logger=None) -> list:
    """reason이 '문맥/맥락' 판단을 근거로 든 AI 교정을 low로 강등한다(제자리 수정 후 반환).

    중복어 삭제·문맥적 낱말 선택처럼 규범이 아닌 편집 판단은 자동 적용 대신 검수 카드로.
    """
    demoted = 0
    for c in corrections:
        if (c.source in ("ai_typo", "ai_polish") and c.confidence != "low"
                and c.reason and _CONTEXT_JUDGMENT_RE.search(c.reason)):
            c.confidence = "low"
            if not c.reason.startswith("[검수]"):
                c.reason = ("[검수] " + c.reason
                            + " (규범 아닌 문맥적 편집 판단 — 검토 필요)")
            demoted += 1
    if logger and demoted:
        logger(f"  → '문맥상' 판단 사유 AI 교정 {demoted}건 검수 카드로 강등 "
               "(규범 조항 아닌 문맥적 편집 판단 — 자동 적용 제외)")
    return corrections


# ══════════════════════════════════════════════════════════
# ▌기관·부처 명칭 치환 강등 — AI 학습 시점의 옛 명칭 되돌리기 방지
# ══════════════════════════════════════════════════════════
# '성평등가족부장관'→'여성가족부장관'("대한민국 정부 부처 명칭은 '여성가족부'가 정확합니다")처럼
# AI가 **현행 정식 기관명을 자기 학습 시점의 옛 명칭으로 되돌리는** 치명적 오교정(사용자 보고
# 2026-07-21). 성평등가족부는 여성가족부의 **개명 후 현재 명칭**인데 AI 지식 컷오프 탓에 거꾸로
# 고친다. 정부 부처·기관은 개편·개명이 잦아 **AI가 최신 명칭을 알 수 없다** — 기관명 치환은
# 표기 교정이 아니라 **사실(개체) 편집**이므로 자동 적용해선 안 된다.
#   기존 그물이 전부 사각이었다(실측): [K]단어교체는 공유 음절('가족부장관')이 있어 미발동,
#   [V]관용·관례는 사유에 그 낱말이 없어 미발동, [U]원어 병기 앵커는 라틴 병기 괄호가 없어
#   미발동, [T]음절 재배열은 음절 멀티셋이 달라 미발동 → high로 통과.
#   → **드롭이 아닌 low 강등**(사용자 지시): 문서가 실제로 옛 명칭을 쓰는 경우도 있어 방향
#   판단은 편집자 몫이다. low면 자동 적용에서 빠지고 검수 카드로만 노출된다.
# 기관·직위 접미사(긴 것 우선) — 원문·교정문이 **같은 접미사**로 끝나면 같은 개체 유형의 '개명'.
_ORG_SUFFIX = (
    "위원회", "부장관", "부총리", "위원장", "연구원", "진흥원", "정보원", "이사장", "본부장",
    "장관", "차관", "청장", "처장", "원장", "공사", "공단", "재단", "협회", "학회", "본부",
    "부", "처", "청", "원", "실", "단",
)
_NAME_CLAIM_RE = re.compile(r"명칭|부처명|기관명|부서명|정식 이름")
_CORRECT_CLAIM_RE = re.compile(r"정확|공식|정식|올바|옳|맞습니다")


def _is_org_name_substitution(c) -> bool:
    """c가 '기관·부처 명칭을 다른 명칭으로 통째 바꾸는' 개체 편집인가?

    발동(전부 충족 + 마지막 두 트리거 중 하나):
      · source가 AI, 원문/교정문이 각각 **공백 없는 순한글 단일 어절**(조사 제거 base, len≥4).
      · **자모거리 ≥ 3** — 오탈자 교정('여상가족부'→'여성가족부', 거리 1~2)은 보존한다.
      · (구조) 원문·교정문이 **같은 기관·직위 접미사**로 끝남(부장관·부·청·위원회…) = 개명, 또는
        (사유) reason이 '명칭이 정확/공식/정식'이라는 주장.
    """
    if c.source not in ("ai_typo", "ai_polish") or not c.original or c.original == c.corrected:
        return False
    o, cr = c.original.strip(), c.corrected.strip()
    if " " in o or " " in cr:
        return False
    ob, cb = _strip_josa(o), _strip_josa(cr)
    if not re.fullmatch(r"[가-힣]+", ob) or not re.fullmatch(r"[가-힣]+", cb):
        return False
    if len(ob) < 4 or len(cb) < 4 or ob == cb:
        return False
    if _jamo_distance(ob, cb) < 3:
        return False        # 근소 차이 = 오탈자 교정 → 보존
    if any(ob.endswith(s) and cb.endswith(s) for s in _ORG_SUFFIX):
        return True         # 구조: 같은 기관·직위 접미사 = 개체 개명
    return bool(c.reason and _NAME_CLAIM_RE.search(c.reason)
                and _CORRECT_CLAIM_RE.search(c.reason))


def demote_org_name_substitution(corrections: list, logger=None) -> list:
    """기관·부처 명칭을 다른 명칭으로 바꾸는 AI 교정을 low로 강등한다(제자리 수정 후 반환).

    AI는 학습 시점 이후의 개편·개명을 모른다('성평등가족부'→'여성가족부'로 되돌림). 기관명
    치환은 표기 교정이 아니라 사실 편집이므로 자동 적용을 막고 편집자 검수에 맡긴다.
    [S]헤지·[V]관용·문맥 강등과 동일 메커니즘·동일 단계(원시 ai_list 1회 → Case A 전파분까지 일관).
    """
    demoted = 0
    for c in corrections:
        if c.confidence != "low" and _is_org_name_substitution(c):
            c.confidence = "low"
            if not (c.reason or "").startswith("[검수]"):
                c.reason = ("[검수] " + (c.reason or "")
                            + " (기관·부처 명칭 치환 — 개편·개명 여부는 편집자 확인 필요)")
            demoted += 1
    if logger and demoted:
        logger(f"  → 기관·부처 명칭 치환 AI 교정 {demoted}건 검수 카드로 강등 "
               "(AI가 모르는 개편·개명 가능 — 자동 적용 제외)")
    return corrections


# ══════════════════════════════════════════════════════════
# ▌사전이 필요한 가드 — 외래어 순화(paraphrase) 강등
# ══════════════════════════════════════════════════════════
def _edit_distance(a: str, b: str) -> int:
    """표준 Levenshtein 편집거리(짧은 한글 단어용 — 비용 무시 가능)."""
    if a == b:
        return 0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[n]


def _shares_bigram(a: str, b: str) -> bool:
    """a·b가 길이 2 이상 공통 부분열(2-gram)을 하나라도 갖는가?"""
    ba = {a[i:i + 2] for i in range(len(a) - 1)}
    return any(b[i:i + 2] in ba for i in range(len(b) - 1))


def _strip_josa(word: str) -> str:
    """어절 끝 조사를 떼 base 반환(형태소). 미가용/실패 시 입력 그대로(graceful)."""
    try:
        from core import morph
        if morph.available():
            b = morph.strip_josa(word)
            if b:
                return b
    except Exception:
        pass
    return word


def _is_loanword_paraphrase(c, exists_fn) -> bool:
    """c가 '단어를 뜻이 비슷한 전혀 다른 단어로 통째 바꾸는 순화(paraphrase)'인가?

    '파라메터(가)'→'매개변수(가)'·'리스크'→'위험'·'데이터'→'자료'처럼 글자가 **완전히 달라**
    (공유 2-gram 없음 · 편집거리 ≥ 3) 맞춤법 교정이 아니라 순화/유의어 치환. 판정:
      · **교정문(목표어) base가 사전 등재 표제어**이면 충분 — 원문은 비표준 표기(파라메터)일 수 있다.
        철자 교정은 글자를 보존(2-gram 공유 또는 편집거리 1~2)하므로 이 조건에 안 걸린다.
      · 외래어 표기 정규화(파라메터→파라미터: 2-gram '파라' 공유)는 살아남는다 ← 보존돼야 할 진짜 교정.
    ⚠ AI는 흔히 조사까지 붙여 교정한다('파라메터가'→'매개변수가') → **조사를 떼고 base로** 검사.
    ⚠ 실단어 오류(결제↔결재·지향↔지양 등 최소대립쌍)는 편집거리 1·글자 공유라 False(영향 없음).
    """
    if c.source != "ai_typo":
        return False
    o, cr = (c.original or "").strip(), (c.corrected or "").strip()
    if o == cr or not re.fullmatch(r"[가-힣]+", o) or not re.fullmatch(r"[가-힣]+", cr):
        return False
    ob, cb = _strip_josa(o), _strip_josa(cr)   # 조사 제거 base로 비교
    if not (len(ob) >= 2 and len(cb) >= 2 and ob != cb):
        return False
    if _shares_bigram(ob, cb) or _edit_distance(ob, cb) < 3:
        return False
    # 교정문(목표어)만 등재 표제어면 충분 — 원문은 비표준 외래어 표기(파라메터)일 수 있다(사용자 보고).
    #   원문까지 등재를 요구하면 '파라메터가'(미등재)가 가드를 새 버린다.
    try:
        return exists_fn(cb)
    except Exception:
        return False


def drop_loanword_paraphrase(corrections: list, exists_fn, logger=None):
    """외래어/단어를 '뜻이 비슷한 전혀 다른 단어'로 통째 바꾸는 AI 순화를 **제외(드롭)** 한다.

    교정교열 도구는 저자가 쓴 외래어 표기를 임의로 순화하지 않는다('파라메터'→'매개변수'는
    표기/문체 판단이지 맞춤법 교정이 아님 — AI 스스로도 '저자 의도 존중'을 사유로 적으면서 정작
    교정문은 순화어를 내는 모순을 보였다, 사용자 보고). **교정문 base가 등재 표제어** + 글자 완전
    상이(공유 2-gram 없음 · 편집거리 ≥ 3)면 제외(원문은 비표준 표기여도 됨). 외래어 표기 정규화
    (파라메터→파라미터: 2-gram 공유)와 실단어 오류(최소대립쌍, 편집거리 1)는 글자가 가까워 보존된다.
    validate/일관성 통일 이후 호출. exists_fn(word)->bool.

    반환: 걸러낸 리스트(원본 미변경).
    """
    if not corrections:
        return corrections
    kept, dropped = [], 0
    for c in corrections:
        if _is_loanword_paraphrase(c, exists_fn):
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 외래어 순화 AI 교정 {dropped}건 제외 (저자 표기 존중 — 임의 순화 금지)")
    return kept


# ══════════════════════════════════════════════════════════
# ▌사전이 필요한 가드 — 문맥 윤문의 '단어 교체'(유의어 치환) 차단
# ══════════════════════════════════════════════════════════
def _jamo(s: str) -> str:
    """한글 음절을 자모(NFD)로 분해 — '하'→'ㅎㅏ'. 음운 유사도 비교용."""
    import unicodedata
    return unicodedata.normalize("NFD", s)


def _jamo_distance(a: str, b: str) -> int:
    """자모 단위 편집거리 — 오탈자(1~2)와 전혀 다른 단어(≥3)를 가른다."""
    return _edit_distance(_jamo(a), _jamo(b))


def _shares_syllable(a: str, b: str) -> bool:
    """a·b가 공통 음절(1글자)을 하나라도 갖는가(오탈자면 대개 겹침)."""
    return bool(set(a) & set(b))


def _is_word_swap(o_tok: str, c_tok: str, exists_fn) -> bool:
    """토큰 치환 (o_tok→c_tok)이 '오타 교정'이 아니라 '유효 단어의 유의어 치환'인가?

    '하에'→'아래'·'리스크'→'위험'처럼 저자가 쓴 **멀쩡한 단어**를 뜻이 비슷한 **전혀 다른 단어**로
    바꾸는 것(문맥 윤문)은 저자 고유 권한 침해 → 차단(사용자 보고 2026-07-01). 반면 조사 추가
    ('BMBF'→'BMBF의')·오탈자('몇일'→'며칠'·'역활'→'역할')는 보존해야 한다. 판정:
      · 순수 추가/삭제(한쪽이 다른 쪽의 부분문자열)면 조사·병기 → 교체 아님(False).
      · 조사 뗀 base가 같으면(조사만 다름) 교체 아님.
      · **자모 편집거리 ≥ 3 이고 공유 음절이 없어야**(전혀 다른 단어). 오탈자는 자모거리 1~2이거나
        음절을 공유하므로(몇일↔며칠=2, 역활↔역할=1·공유'역') 걸리지 않는다.
      · 원문·교정문 base가 **둘 다 등재어**여야(유효 단어 교체). 진짜 오타(미등재)→단어는 살린다.
    """
    o, cc = (o_tok or "").strip(), (c_tok or "").strip()
    if not o or not cc or o == cc:
        return False
    if o in cc or cc in o:                       # 조사/병기 추가·삭제
        return False
    if not re.search(r"[가-힣]", o) or not re.search(r"[가-힣]", cc):
        return False
    ob, cb = _strip_josa(o), _strip_josa(cc)
    if not ob or not cb or ob == cb:
        return False
    if _shares_syllable(ob, cb):                  # 음절 공유 → 오탈자/최소대립쌍
        return False
    if _jamo_distance(ob, cb) < 3:                # 자모거리 근소 → 오탈자
        return False
    try:
        return bool(exists_fn(ob) and exists_fn(cb))
    except Exception:
        return False


def drop_word_substitution_paraphrase(corrections: list, exists_fn, logger=None):
    """문맥 윤문에서 **유효 단어를 유의어로 통째 바꾸는** AI 교정을 **제외(드롭)** 한다.

    '…지원 하에'→'…지원 아래'(하에→아래), 여러 어절 구간의 부분 단어 치환도 잡는다: 원문/교정문을
    어절로 정렬(difflib)해 **치환된 토큰 쌍**만 `_is_word_swap`으로 검사한다. 조사 추가('BMBF'→
    'BMBF의')는 부분문자열이라 통과(보존), '하에'→'아래'는 유의어 치환이라 카드 전체를 제외한다.
    ⚠ 조사 추가는 살리고 단어 교체만 되돌리는 '부분 복원'은 오적용 위험이 커 하지 않는다 — 단어
    교체가 하나라도 섞이면 **그 카드를 통째로 드롭**(저자 표기 보존 우선, 조사 보완 미적용은 감수).
    ai_typo/ai_polish만 대상. validate/순화 가드 이후 호출. exists_fn(word)->bool.

    반환: 걸러낸 리스트(원본 미변경).
    """
    if not corrections:
        return corrections
    import difflib
    kept, dropped = [], 0
    for c in corrections:
        if c.source not in ("ai_typo", "ai_polish") or not c.original or not c.corrected:
            kept.append(c)
            continue
        o_toks, c_toks = c.original.split(), c.corrected.split()
        swap = False
        sm = difflib.SequenceMatcher(a=o_toks, b=c_toks, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "replace":
                continue
            ob, cb = o_toks[i1:i2], c_toks[j1:j2]
            for k in range(min(len(ob), len(cb))):
                if _is_word_swap(ob[k], cb[k], exists_fn):
                    swap = True
                    break
            if swap:
                break
        if swap:
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 문맥 윤문 '단어 교체' AI 교정 {dropped}건 제외 "
               f"(유효 단어의 유의어 치환 — 저자 고유 표기 존중)")
    return kept


# ══════════════════════════════════════════════════════════
# ▌사전이 필요한 가드 — 등재 표준형 → 미등재 근접변이(반표준화) 드롭
# ══════════════════════════════════════════════════════════
# '실젯값'(우리말샘 등재)→'실제값'(미등재)처럼 AI가 **사전 등재 표준형을 사전에 없는 근접
#   변이형**(사이시옷 제거·받침 교체 등)으로 바꾸면서, 사유로는 "표준국어대사전 등재어로
#   통일"을 내세우는 **자기모순 교정**(사용자 보고 2026-07-21). 교정 방향이 표준→비표준으로
#   거꾸로라 명백한 오교정인데도 **3차 재검증(nikl_dict.validate)이 놓친다**: 교정문 '실제값'은
#   whole-word로는 미등재지만 kiwi가 '실제(NNG)+값(NNG)' 등재 형태소 복합어로 봐
#   is_known_form=True → 저신뢰 강등을 escape한다('고지사=고+지사'와 동일한 형태소 복합어
#   사각지대). → validate가 쓰는 is_known_form을 **우회**하고 **직접 표제어 조회(exists_fn)** 로만
#   등재 방향 반전을 판정해 드롭한다. exists_fn은 반드시 lookup_word 계열 '직접 표제어' 조회여야
#   한다(is_valid_word/is_known_form을 주면 교정문이 통과해 무력화됨 — 워커가 lookup_word를 넘김).
def _is_destandardizing_variant(c, exists_fn) -> bool:
    """c가 '등재 표준형 → 미등재 근접변이'로 되돌리는 반(反)표준화 AI 교정인가?

    발동 4조건(전부 충족):
      · source == "ai_typo" — 낱말 표기 주장. 윤문/띄어쓰기 교정은 대상 아님.
      · 원문·교정문이 각각 **한글 런 정확히 1개**(공백 없는 단일 어절)이고 **한글 외 골격이 동일**.
        수식 부호 등 한글에 붙은 부호('실젯값-'/'실제값-')는 허용하되 그 부호 배치가 같아야 한다
        → 사이시옷·받침 미세변이 클래스에만 좁게 발동(다어절·부호 변경은 제외).
      · **등재 방향 반전**: 원문 한글 base는 직접 표제어(exists_fn=True), 교정문 base는 미등재
        (exists_fn=False). 표준형을 비표준형으로 '거꾸로' 바꿀 때만.
      · **근접 변이**: 두 base가 음절을 공유(_shares_syllable)하고 자모거리 ≤ 2(_jamo_distance).
        사이시옷 가감(거리 1)·받침 교체 수준만 — 전혀 다른 단어 치환은 유의어/순화 가드 소관이라
        여기선 제외(오발동 방지).

    ⚠ 올바른 방향(미등재 '실제값'→등재 '실젯값', 사이시옷 추가)은 원문이 미등재라 첫 조건에서
      자연 제외 → 정당한 사이시옷 교정은 보존된다.
    """
    if getattr(c, "source", None) != "ai_typo":
        return False
    o, cr = (c.original or "").strip(), (c.corrected or "").strip()
    if not o or o == cr or " " in o or " " in cr:
        return False   # 공백 포함 다어절 제외(단일 어절만)
    o_runs, cr_runs = re.findall(r"[가-힣]+", o), re.findall(r"[가-힣]+", cr)
    if len(o_runs) != 1 or len(cr_runs) != 1:
        return False   # 한글 런이 정확히 하나씩(단일 한글 낱말)일 때만
    if re.sub(r"[가-힣]+", "\x00", o) != re.sub(r"[가-힣]+", "\x00", cr):
        return False   # 한글 외 골격(부호 종류·위치)이 다르면 대상 아님 — '실젯값-'↔'실제값-'만 통과
    ob, cb = _strip_josa(o_runs[0]), _strip_josa(cr_runs[0])
    if not (len(ob) >= 2 and len(cb) >= 2 and ob != cb):
        return False
    if not _shares_syllable(ob, cb) or _jamo_distance(ob, cb) > 2:
        return False   # 근접 변이(사이시옷·받침)만 — 전혀 다른 단어 치환 제외
    try:
        return bool(exists_fn(ob)) and not bool(exists_fn(cb))   # 등재 → 미등재 반전
    except Exception:
        return False


def drop_destandardizing_variant(corrections: list, exists_fn, logger=None):
    """등재 표준형을 미등재 근접변이형으로 되돌리는 AI 교정을 **제외(드롭)** 한다.

    '실젯값'(등재)→'실제값'(미등재)처럼 사전 등재 표준형을 사전에 없는 사이시옷/받침 변이형으로
    바꾸는 자기모순 교정. 3차 재검증이 형태소 복합어(is_known_form) 사각으로 못 막는 부류라
    직접 표제어 조회로만 판정한다. validate/순화·유의어 가드 이후(최종 신뢰도 확정 후) 호출.
    exists_fn(word)->bool 은 **직접 표제어 조회**(lookup_word 계열)여야 한다.

    반환: 걸러낸 리스트(원본 미변경).
    """
    if not corrections:
        return corrections
    kept, dropped = [], 0
    for c in corrections:
        if _is_destandardizing_variant(c, exists_fn):
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 반표준화 AI 교정 {dropped}건 제외 "
               f"(등재 표준형→미등재 근접변이 — 사이시옷/받침 되돌림)")
    return kept
