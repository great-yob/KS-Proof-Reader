"""
core/josa_rules.py — 받침 호응 조사 교정 (결정론·맞춤법)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
조사 '은/는·이/가·을/를·과/와·으로/로'는 앞 체언의 **받침 유무**로 형태가 정해진다.
괄호 설명이 끼어들면(영상(15초, 30초)는) 조사가 괄호 앞 체언(영상)이 아니라 괄호
안 말미에 이끌려 틀리기 쉽다. 실무 교정에서는 **괄호 앞 체언에 호응**시킨다:
  영상(15초, 30초)는 → 영상(15초, 30초)은   ('영상'은 받침 ㅇ → '은')

받침 규칙은 기계적(결정론)이라 AI 없이 high-confidence로 교정한다. 글자 한 자(조사)만
바꾸므로 과교정 위험이 낮다. GUI-agnostic(core/ 규칙).
"""

import re

# 조사 → (받침형, 비받침형). 받침 있으면 [0], 없으면 [1].
_FORM = {
    "은": ("은", "는"), "는": ("은", "는"),
    "이": ("이", "가"), "가": ("이", "가"),
    "을": ("을", "를"), "를": ("을", "를"),
    "과": ("과", "와"), "와": ("과", "와"),
}

# 괄호 앞 체언 + (설명) + 조사. 조사 뒤에 한글이 더 이어지면(=긴 단어) 조사로 보지 않음.
#   ⚠ 여는 괄호 앞 공백 1칸을 허용한다('이행 (강화CRC → AEAD)를') — 띄어쓰면 조사가 괄호 앞
#     체언에 더 잘못 호응하기 쉽다. 교정문은 그 공백을 제거해 괄호를 체언에 붙인다(관용 표기).
_PAREN_JOSA_RE = re.compile(
    r"([가-힣]+)[ ]?\(([^()]{0,60})\)(으로|로|은|는|이|가|을|를|과|와)(?=$|[^가-힣])"
)


def _jongseong(syll: str):
    """음절의 종성(받침) 인덱스. 한글 음절이 아니면 None. 0이면 받침 없음, 8이면 ㄹ받침."""
    if not ("가" <= syll <= "힣"):
        return None
    return (ord(syll) - 0xAC00) % 28


def reconcile_josa(stem: str, josa: str) -> str:
    """새 어간 `stem`의 받침에 맞춰 뒤따르는 조사 `josa`의 형태를 교정해 돌려준다.

    규범표기/맞춤법 교정으로 어간 받침이 바뀌면(스윕[ㅂ받침]→스위프[받침없음]) 뒤 조사도
    호응해야 한다: '스윕과'→'스위프와', '스윕이'→'스위프가', '스윕으로'→'스위프로'.
    받침 민감 **선두 토큰**(은/는·이/가·을/를·과/와·으로/로)만 교체하고 나머지(보조사 등)는
    보존한다. ⚠ '이'는 서술격조사('이다' 활용: 이라/이야/이란…)와 헷갈리므로 **단독일 때만**
    주격 이/가로 본다(스윕이라고→스위프가라고 같은 오교정 방지). 모호하면 원형 유지(무해).

    반환: 호응 교정된 조사 문자열(변경 없으면 입력 그대로).
    """
    if not stem or not josa:
        return josa
    jong = _jongseong(stem[-1])
    if jong is None:
        return josa
    has_batchim = jong != 0
    # 1) 으로/로 (도구·자격·방향) — 받침 없거나 ㄹ받침(8)이면 '로'
    if josa.startswith("으로"):
        return ("으로" if (has_batchim and jong != 8) else "로") + josa[2:]
    #    bare '로' 선두는 도구격 여부가 모호 → 손대지 않음(무해).
    # 2) 단음절 받침 조사 선두 — 과/와·은/는·을/를는 항상 격/보조사라 보조사가 뒤따라도 안전
    head, rest = josa[0], josa[1:]
    if head in ("과", "와", "은", "는", "을", "를"):
        return _FORM[head][0 if has_batchim else 1] + rest
    # 3) 이/가는 서술격조사 회피 위해 **단독**일 때만 교정
    if head in ("이", "가") and rest == "":
        return _FORM[head][0 if has_batchim else 1]
    return josa


def find_paren_josa(text: str) -> list:
    """괄호 설명 뒤 조사를 괄호 앞 체언의 받침에 호응하도록 교정한 후보.

    반환: [(original, corrected), ...] (중복 제거). 변경 없으면 제외.
    """
    out, seen = [], set()
    for m in _PAREN_JOSA_RE.finditer(text):
        noun, inner, josa = m.group(1), m.group(2), m.group(3)
        # ⚠ 괄호 안이 '순수 번호/라벨'(식 (4)·그림 (3)·표 (1))이면 조사는 괄호 앞 체언이
        #   아니라 **번호의 읽음**에 호응한다: '식 (4)와' = '식 사와'(4=사, 받침없음 → 와)로
        #   저자의 '와'가 옳다. 호응 대상이 모호하고(번호를 읽어 넘기는지/건너뛰는지) 저자가
        #   대개 맞게 쓰므로 **건드리지 않는다**(과교정 방지, 사용자 보고). '식'과 '(' 사이
        #   공백도 라벨 표기의 의도된 간격이라 제거하지 않는다(이 분기에서 통째로 스킵되므로).
        if re.fullmatch(r"\s*\d[\d\s.,·\-]*", inner):
            continue
        jong = _jongseong(noun[-1])
        if jong is None:
            continue
        has_batchim = jong != 0

        if josa in ("으로", "로"):
            # 받침 없거나 ㄹ받침(8) → '로', 그 외 받침 → '으로'
            correct = "로" if (not has_batchim or jong == 8) else "으로"
        else:
            correct = _FORM[josa][0 if has_batchim else 1]

        if correct == josa:
            continue
        original = m.group(0)
        corrected = f"{noun}({inner}){correct}"
        if original in seen:
            continue
        seen.add(original)
        out.append((original, corrected))
    return out


# 받침형 조사 ↔ 비받침형 조사 (단음절). 으로/로(ㄹ받침 예외)는 plain 어절에선 다루지 않음.
_BATCHIM_FORM = {"을": "를", "은": "는", "이": "가", "과": "와"}   # 받침형 → 비받침형
_NOBATCHIM_FORM = {"를": "을", "는": "은", "가": "이", "와": "과"}  # 비받침형 → 받침형


def find_batchim_josa(text: str) -> list:
    """괄호 없는 일반 어절의 조사를 앞 체언 받침에 호응하도록 교정한 후보.

    '필드을'→'필드를'(필드 받침 없음 → 를). kiwi 형태소로 **마지막 형태소가 조사형 1글자이고,
    그 앞이 체언(N*)이며, 마지막 형태소가 어미(E*)가 아닐 때만** 받침을 대조한다. 이 가드가
    핵심 — 그렇지 않으면 관형형 어미('있는'·'어려운'의 -는/-은)와 단어 일부('가을'·'마을')를
    조사로 오인해 과교정한다(실문서로 확인된 함정). 받침 불일치 시에만 조사 한 글자를 교정한다.
    받침 규칙은 결정론이라 high-confidence. kiwipiepy 미설치/실패 시 [].

    반환: [(original, corrected), …] (중복 제거).
    """
    try:
        from core import morph as _morph
        kiwi = _morph._get_kiwi()
    except Exception:
        kiwi = None
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for w in text.split():
        if w in seen or len(re.sub(r"[^가-힣]", "", w)) < 2:
            continue
        seen.add(w)
        try:
            toks = kiwi.analyze(w)[0][0]
        except Exception:
            continue
        if len(toks) < 2:
            continue
        last, prev = toks[-1], toks[-2]
        if (last.start == 0 or len(last.form) != 1
                or (last.form not in _BATCHIM_FORM and last.form not in _NOBATCHIM_FORM)
                or last.tag.startswith("E")          # 관형형/연결 어미(-는/-은/-을) 제외
                or last.tag == "XSN"                  # 명사파생접미사(제미나'이'·높'이') 제외 — 조사 아님
                or prev.tag == "NNP"                  # 고유명사+끝음절은 이름의 일부('제미나이') — 조사 호응 금지
                or not prev.tag.startswith("N")       # 조사 앞은 체언이어야
                or not (0 < last.start <= len(w))):
            continue
        # 표면 글자 ↔ 조사 형태 일치 가드(치명적) — kiwi는 준말 '그게'(그것이)를 그것/NP + 이/JKS로
        #   분석하는데, 이/JKS의 표면 글자는 '게'(거+이 축약)다. 표면이 '게'인데 조사 형태가 '이'라고
        #   '가'로 바꾸면 '그게'→'그가'가 된다(사용자 보고). 마지막 형태소의 표면 글자가 조사 형태와
        #   정확히 같을 때만(필드'을'='을') 교정한다. 축약·이형은 건드리지 않는다.
        if w[last.start] != last.form:
            continue
        jong = _jongseong(w[last.start - 1])
        if jong is None:
            continue
        has_batchim = jong != 0
        f = last.form
        if f in _BATCHIM_FORM and not has_batchim:
            corr = _BATCHIM_FORM[f]
        elif f in _NOBATCHIM_FORM and has_batchim:
            corr = _NOBATCHIM_FORM[f]
        else:
            continue
        fixed = w[:last.start] + corr + w[last.start + 1:]
        if fixed != w:
            out.append((w, fixed))
    return out


# 단독 토큰으로 떨어져 나온 '고아 조사' — 앞 체언과 붙여 써야 한다('수요 를'→'수요를').
#   ⚠ 일반 조사 JOIN(공백 제거)은 거짓양성이 커서 보류된 영역이다([[validation-delta-and-spacing
#   -backstop]] ⑩). 그래서 **단독 단어로는 절대 쓰이지 않는 조사만** 다룬다 —
#   '을'·'를'(목적격)과 '으로'(부사격)는 그 자체로 명사·동사가 될 수 없어(은=銀, 가=邊 같은
#   동형이의 위험 없음) 한 어절로 떨어져 있으면 100% 앞 체언에 붙어야 할 조사다.
#   '으로'는 붙일 때 앞 체언 받침에 호응시킨다(세종 으로→세종으로 / 학교 으로→학교로 —
#   받침 없거나 ㄹ받침이면 '로'). 글자 불변 또는 받침 결정론, 문법상 확정 → high.
#   (사용자 보고 2026-07-03: '세종 으로' 미탐 — 30.hwp)
_ORPHAN_JOSA = frozenset({"을", "를", "으로"})
_QUOTE_CHARS = "\"'“”‘’"


def find_orphan_josa(text: str) -> list:
    """앞 체언과 떨어져 단독 토큰이 된 조사('수요 를'·'세종 으로')를 붙인 후보를 만든다.

    인접 두 어절 A B가 **정확히 공백 1칸**으로 나뉘고, B가 통째로 '을'/'를'/'으로'이며, A가
    한글로 끝나면 'A B'→'AB'로 붙인다('으로'는 A 받침에 호응해 으로/로 선택). 메타언어
    인용(조사 '를'…)을 피하려 양옆에 따옴표가 붙은 경우는 제외한다.
    반환 [(original, joined), …] (등장 순, 중복 제거).
    """
    out, seen = [], set()
    for line in text.split("\n"):
        toks = list(re.finditer(r"\S+", line))
        for i in range(len(toks) - 1):
            a, b = toks[i], toks[i + 1]
            if b.start() - a.end() != 1:                # 정확히 공백 1칸
                continue
            A, B = a.group(), b.group()
            if B not in _ORPHAN_JOSA:
                continue
            if not A or not ("가" <= A[-1] <= "힣"):     # 앞 어절이 한글로 끝나야(체언)
                continue
            # 메타언어 인용('를'을 단어로 논하는 경우)·따옴표 인접은 제외
            if A[-1] in _QUOTE_CHARS:
                continue
            be = b.end()
            if be < len(line) and line[be] in _QUOTE_CHARS:
                continue
            orig = A + " " + B
            if orig in seen:
                continue
            seen.add(orig)
            # '으로'는 앞 체언 받침에 호응(받침 없음·ㄹ받침 → '로') — reconcile_josa 재사용
            joined = A + (reconcile_josa(A, B) if B == "으로" else B)
            out.append((orig, joined))
    return out


def find_duplicate_comitative_josa(text: str) -> list:
    """앞 어절이 이미 공동격 조사(과/와)로 끝나는데 **단독 '와'/'과' 어절이 또** 뒤따르는
    조사 중복을 찾아 삭제 후보를 만든다('모즈얀(もずやん)과 와 지역'→'모즈얀(もずやん)과 지역').

    AI가 비결정적으로 잡던 유형(청크 따라 미탐)의 결정론 백스톱(사용자 보고 2026-07-02).
    가드(보수 — 과교정 0):
      · 앞 어절의 **마지막 kiwi 형태소가 1글자 조사(J*) '과/와'이고 표면 글자와 일치**할 때만
        ('결과·물리학과'는 NNG 통낱말이라 자연 제외 — kiwi 실측).
      · 뒤 어절이 통째로 '와' 또는 '과'(단독 어절).
      · 그다음 어절이 존재하고 한글로 시작(문말 감탄 '와!' 제외).
      · 따옴표 인접(메타언어 인용) 제외. 삭제 교정이라 호출부는 저신뢰 '검수 카드'로 노출.
    반환: [(original, corrected), …] — original='A 와', corrected='A'.
    """
    try:
        from core import morph as _morph
        kiwi = _morph._get_kiwi()
    except Exception:
        kiwi = None
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for line in text.split("\n"):
        toks = list(re.finditer(r"\S+", line))
        for i in range(len(toks) - 2):
            a, b, c = toks[i], toks[i + 1], toks[i + 2]
            A, B, C = a.group(), b.group(), c.group()
            if B not in ("와", "과") or b.start() - a.end() != 1:
                continue
            if not C or not ("가" <= C[0] <= "힣"):     # 뒤에 한글 어절이 이어져야(감탄 '와' 제외)
                continue
            if not A or A[-1] not in ("과", "와"):
                continue
            if A[-1] in _QUOTE_CHARS or line[b.end():b.end() + 1] in ("'", '"', "’", "”"):
                continue
            try:
                last = kiwi.analyze(A)[0][0][-1]
            except Exception:
                continue
            # 앞 어절 끝이 '조사' 과/와일 때만(결과=NNG 등 낱말 끝음절 제외) — 표면 일치 가드 포함.
            if (not last.tag.startswith("J") or len(last.form) != 1
                    or last.form not in ("과", "와") or A[last.start:last.start + 1] != last.form):
                continue
            orig = A + " " + B
            if orig in seen:
                continue
            seen.add(orig)
            out.append((orig, A))
    return out


if __name__ == "__main__":
    tests = [
        "홍보 영상(15초, 30초)는 좋다",
        "이 책(개정판)를 봤다",
        "결과(표 1)가 나왔다",          # 결과 받침없음 → 가 (정상, 무변경)
        "서울(특별시)으로 간다",          # 서울 ㄹ받침 → 로
        "사업(올해)와 함께",             # 사업 ㅂ받침 → 과
        # 순수 번호/라벨 → 스킵(괄호 앞 체언이 아니라 번호 읽음에 호응, 저자 표기 존중)
        "식 (4)와 같이 정의",            # '식 사와' → 와 정상, 무변경(과거엔 '식(4)과' 오교정)
        "그림 (3)을 보라",               # 무변경(스킵)
        "정책(2024)과 비교",             # 2024=숫자 → 스킵(과거엔 '정책'ㄱ받침에 호응)
    ]
    for t in tests:
        print(f"  {t!r} → {find_paren_josa(t)}")
