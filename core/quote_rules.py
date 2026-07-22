"""
core/quote_rules.py — 따옴표 짝·방향 판정과 따옴표 관련 결정론 규칙 (문장부호 완결성)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
따옴표는 괄호와 달리 **방향이 글자에 고정되지 않는다** — 곧은따옴표(' ")는 여닫이가 같은
글자이고, 굽은따옴표(‘ ’ “ ”)도 뒤집어 쓰는 문서가 실재한다(”준비되면“ — 실측 보고).
그래서 글자 모양이 아니라 **줄 단위 문맥 스택**으로 역할을 판정한다:

  · 같은 클래스('끼리, "끼리, ‘’쌍, “”쌍)의 스택을 줄 안에서 돌린다.
  · 스택에 열린 짝이 있으면 → 닫는 따옴표(close). 짝 위치를 기록한다.
  · 스택이 비었는데 앞이 내용 글자(한글·한자·라틴·닫는괄호)면 → 닫는 문맥:
      - 뒤에 같은 클래스 따옴표가 더 있고 **그 따옴표도 닫는 문맥**이면, 지금 것은
        앞말에 붙은 여는 따옴표다(국립국어원'맞춤법규칙' — 여는 것으로 취급).
      - 아니면 **짝 없는 닫는 따옴표(close_orphan)** — 여는 따옴표 누락(사용자 보고:
        '중국은 과거부터 천인계획(千人計劃)'과' → ''천인계획(千人計劃)'과').
  · 라틴 문자 사이의 '·’는 아포스트로피(don't·It’s)로 보고 제외한다.
  · 줄 끝까지 안 닫힌 여는 따옴표는 open_orphan — 단, 인용이 줄(문단)을 넘는 경우가
    실재하므로 **닫는 따옴표 추가 카드는 만들지 않는다**(과교정 0 원칙 — 미탐 허용).

설계 정합성(괄호 짝 bracket_rules와 동궤):
  · 글자를 치환하지 않고 **따옴표 한 짝만 삽입**하거나 **공백만 가감**한다(환각 0).
  · 탐지 전용 — 저신뢰 '검수 카드'로만 노출(자동수정 아님).
  · GUI-agnostic (PySide6 미사용) — core/ 규칙. 사전·형태소 불필요(순수 규칙).
"""

import re

# 따옴표 클래스 — 같은 클래스끼리 짝을 이룬다.
#   ⚠ 곧은따옴표와 굽은따옴표를 **같은 클래스로 병합**한다(2026-07-03 실측 수정) — 실제
#   원고는 여닫이를 혼용한다: '“요즘 어때? 진짜로 궁금해서"'(여는 굽은 “ + 닫는 곧은 ").
#   클래스를 나누면 곧은 "가 홀로 남아 짝 없는 닫는 따옴표로 오인 → '"궁금해서"' 같은
#   거짓 보완 카드가 났다(사용자 보고 3건). 병합하면 “…" 혼용이 스택에서 정상 짝지어진다.
_Q_CLASS = {"'": "s", "‘": "s", "’": "s", '"': "d", "“": "d", "”": "d"}
_ALL_QUOTES = set(_Q_CLASS)
_CURLY_OPEN_SHAPE = {"‘", "“"}                      # 굽은 '여는 모양'(방향 신뢰 가능)
_ORPHAN_OPENER_FOR = {"’": "‘", "”": "“"}           # 고아 닫는 따옴표 → 보완할 여는 짝

# 내용 글자(한글·한자·라틴·숫자) — 닫는 문맥/어구 런 판정용.
_CONTENT_CH = re.compile(r"[0-9A-Za-z가-힣㐀-鿿]")
# 닫는 괄호 → 여는 괄호 (어구 런 스캔 시 균형 괄호 그룹을 통째로 포함)
_BRACKET_CLOSE = {")": "(", "]": "[", "}": "{", "）": "（", "］": "［",
                  "」": "「", "』": "『", "】": "【", "》": "《", "〉": "〈"}

# 닫는 따옴표 뒤에 붙는 조사(긴 것부터) — spacing_rules._JOSA_AFTER_QUOTE와 같은 취지의
#   자체 사본(순수 re, 모듈 간 비공개 이름 결합 회피). 뒤에 한글이 더 이어지면 조사 아님.
_JOSA_RE = re.compile(
    r"(으로서|으로써|에게서|이라고|이라는|으로|에서|에게|이라|이며|이고|라고|라는|"
    r"처럼|보다|마다|조차|밖에|부터|까지|이나|은|는|이|가|을|를|에|의|와|과|도|만|로|나|라)"
    r"(?=$|[^가-힣])"
)

# 여는 따옴표 앞에서 띄어쓰기가 필요한 문장부호(있다.'천인계획 → 있다. '천인계획).
_PUNCT_BEFORE_QUOTE = ".,;:!?"


def _analyze_line(line: str):
    """줄 안 모든 따옴표의 역할과 짝을 판정한다.

    반환: (roles, pairs)
      roles: {index: "open" | "close" | "close_orphan" | "open_orphan" | "apostrophe"}
      pairs: {open_index: close_index, close_index: open_index} (짝이 확정된 것만)
    """
    roles, pairs = {}, {}
    stacks = {"s": [], "d": []}

    def _closing_ctx(i: int) -> bool:
        prev = line[i - 1] if i > 0 else ""
        return bool(prev) and (bool(_CONTENT_CH.match(prev)) or prev in _BRACKET_CLOSE)

    for i, ch in enumerate(line):
        cls = _Q_CLASS.get(ch)
        if not cls:
            continue
        prev = line[i - 1] if i > 0 else ""
        nxt = line[i + 1] if i + 1 < len(line) else ""
        # 아포스트로피 — 라틴 문자 '사이'(don't·It’s)는 인용부호가 아니다.
        if (ch in ("'", "’") and prev.isascii() and prev.isalnum()
                and nxt.isascii() and nxt.isalnum()):
            roles[i] = "apostrophe"
            continue
        # 연도 약물('19·'20·'99) — 홑따옴표(곧은/오른굽은) + **두 자리 숫자**(뒤가 3번째
        #   숫자 아님)는 인용부호가 아니라 연도 생략 표기다. 짝 없이 스택에 남아 뒤의 정상
        #   여는 따옴표를 '닫는'으로 오판시키는 오염원 → apostrophe로 빼 스택에서 제외한다
        #   (사용자 보고 2026-07-21: '건강보험료' 뒤 '19년이 스택을 오염시켜 다음 문장의
        #   여는 따옴표가 close로 오판됨). 앞이 비내용(공백/문두/부호)일 때만 — 4자리 연도
        #   '2024'·'1인 가구'(숫자 1자리)·측정 5'는 대상 아님.
        if (ch in ("'", "’") and not (_CONTENT_CH.match(prev) or prev in _BRACKET_CLOSE)
                and re.match(r"\d\d(?!\d)", line[i + 1:])):
            roles[i] = "apostrophe"
            continue
        st = stacks[cls]
        if st:
            # ⚠ 중첩 인용(2026-07-15 실측 보고): 스택 톱과 현재 글자가 **둘 다 여는
            #   모양**(“·‘)이면 닫힘이 아니라 **중첩의 시작**이다 — 법령 인용문 안의
            #   재인용(바깥 “…” 안 “부정수급자”)에서 무조건 pop하면 “1↔“2가 짝지어져
            #   진짜 닫는 ”3·”4가 고아로 밀리고, 정상 문장에 거짓 보완 카드('있다”라고')
            #   + 거짓 기호 뒤 띄어쓰기 카드('“ 부정수급자')가 났다. 역방향 문서
            #   (”준비되면“)는 스택 톱이 닫는 모양(”)이라 이 분기를 안 타 기존 동작
            #   유지. 곧은따옴표(")는 방향 정보가 없어 계속 닫는 것으로 취급한다
            #   (“A "B" ” 같은 이종 중첩은 판별 불가 — 기존 한계 그대로).
            if ch in _CURLY_OPEN_SHAPE and line[st[-1]] in _CURLY_OPEN_SHAPE:
                roles[i] = "open"
                st.append(i)
                continue
            oi = st.pop()
            roles[i] = "close"
            pairs[oi], pairs[i] = i, oi
            continue
        # 스택 빈 상태에서 닫는 문맥(앞이 내용 글자)인 따옴표 — **뒤 문맥이 판별자**:
        #   · 뒤가 공백/부호/줄끝이거나 **조사 런**이면 인용이 끝나는 자리 →
        #     짝 없는 닫는 따옴표(천인계획(千人計劃)'과 / 원 패스'를).
        #   · 뒤에 내용 글자가 바로 이어지면 앞말에 붙은 **여는** 따옴표
        #     (국립국어원'맞춤법규칙'에 / 캐나가“Say — 실측 오탐 수정 2026-07-03).
        if _closing_ctx(i):
            rest = line[i + 1:]
            terminal = True
            if rest and _CONTENT_CH.match(rest[0]):
                terminal = bool("가" <= rest[0] <= "힣" and _JOSA_RE.match(rest))
            if terminal:
                roles[i] = "close_orphan"
                continue
        roles[i] = "open"
        st.append(i)

    for st in stacks.values():
        for i in st:
            roles[i] = "open_orphan"
    return roles, pairs


def quote_roles(line: str) -> dict:
    """줄 안 따옴표의 역할만 반환 — {index: role}. (morph 기호 뒤 명사 띄어쓰기 등 공용)"""
    return _analyze_line(line)[0]


def _run_start_before(line: str, i: int) -> int:
    """i(따옴표 위치) 앞의 '어구 런' 시작 인덱스 — 내용 글자·가운뎃점과 균형 괄호 그룹 포함.

    '천인계획(千人計劃)' 처럼 어구 안의 균형 괄호는 통째로 포함하고, 공백·기타 부호에서 멈춘다.
    """
    j = i
    while j > 0:
        c = line[j - 1]
        if _CONTENT_CH.match(c) or c == "·":
            j -= 1
            continue
        if c in _BRACKET_CLOSE:
            opener, depth, k = _BRACKET_CLOSE[c], 1, j - 2
            while k >= 0 and depth:
                if line[k] == c:
                    depth += 1
                elif line[k] == opener:
                    depth -= 1
                k -= 1
            if depth:            # 균형 안 맞음 → 런 종료
                break
            j = k + 1
            continue
        break
    return j


def find_unpaired_quotes(text: str) -> list:
    """짝 없는 닫는 따옴표에 여는 짝을 보완할 후보를 [(original, corrected, reason), …]로.

    '중국은 과거부터 천인계획(千人計劃)'과 같은' → ''천인계획(千人計劃)'과 같은' 방향만
    다룬다(여는 따옴표 추가). 열린 채 안 닫힌 따옴표(open_orphan)는 인용이 줄을 넘는
    경우가 실재해 **다루지 않는다**(과교정 0 — 미탐 허용).

    가드(보수):
      · 따옴표 앞 어구 런에 한글/한자가 있어야(라틴 소유격 Jones' 제외).
      · 따옴표 뒤가 공백/부호/줄끝이거나, 한글이면 **조사 런**이어야(그 외 한글이 이어지면
        모호 → 스킵).
    """
    out, seen = [], set()
    for line in text.split("\n"):
        if not any(q in line for q in _ALL_QUOTES):
            continue
        roles, _pairs = _analyze_line(line)
        for i, role in roles.items():
            if role != "close_orphan":
                continue
            ch = line[i]
            j = _run_start_before(line, i)
            run = line[j:i]
            if len(run) < 2 or not re.search(r"[가-힣㐀-鿿]", run):
                continue
            rest = line[i + 1:]
            josa = ""
            if rest and "가" <= rest[0] <= "힣":
                jm = _JOSA_RE.match(rest)
                if not jm:
                    continue                  # 뒤 한글이 조사가 아님 → 모호, 스킵
                # 뒤 조사를 원문에 포함 — 같은 어구가 정상 짝으로도 등장할 때('테크 패스'및
                #   vs 원 패스'를) 적용 검색이 정상 쪽을 오염시키지 않게 유일성을 높인다.
                josa = jm.group(1)
            opener = _ORPHAN_OPENER_FOR.get(ch, ch)
            original = run + ch + josa
            corrected = opener + original
            if original in seen:
                continue
            seen.add(original)
            out.append((original, corrected,
                        f"닫는 따옴표 {ch} 의 짝 여는 따옴표가 없음 — 여는 따옴표 추가"
                        "(넣을 위치는 검토 필요)"))
    return out


def find_quote_punct_spacing(text: str) -> list:
    """문장부호↔여는 따옴표 띄어쓰기 후보를 [(original, corrected), …]로 반환.

    (a) 문장부호 뒤 여는 따옴표 붙음  — '있다.'천인계획'   → '있다. '천인계획''
    (b) 여는 따옴표 뒤 공백          — ',“ Artificial'    → ', “Artificial'
    두 오류가 붙어 있으면(et al.(2025),“ Artificial) 한 후보로 합쳐 낸다.

    가드(보수 — 과교정 0):
      · **여는 역할(open)** 따옴표만, 그리고 (굽은 여는 모양이거나 줄 안에 닫는 짝이
        확정된 경우)만 다룬다 — 인용이 줄을 넘어 닫는 따옴표가 여는 것으로 오인되는
        경우(…했다." 그러나)를 차단.
      · (a)는 부호 앞이 내용 글자(한글/라틴/닫는괄호)일 때만(소수점 3.14·약어 제외).
    공백만 가감(글자 불변·환각 0). 탐지 전용 저신뢰 검수 카드용.
    """
    out, seen = [], set()
    for line in text.split("\n"):
        if not any(q in line for q in _ALL_QUOTES):
            continue
        roles, pairs = _analyze_line(line)
        for i, role in roles.items():
            if role != "open":
                continue
            ch = line[i]
            if ch not in _CURLY_OPEN_SHAPE and i not in pairs:
                continue                       # 방향 신뢰 불가(줄 넘김 인용 가능성) → 스킵
            pa = line[i - 1] if i > 0 else ""
            pb = line[i - 2] if i > 1 else ""
            fix_a = (pa in _PUNCT_BEFORE_QUOTE and bool(pb) and not pb.isdigit()
                     and (bool(_CONTENT_CH.match(pb)) or pb in _BRACKET_CLOSE))
            m_sp = re.match(r" {1,2}", line[i + 1:])
            after = i + 1 + (m_sp.end() if m_sp else 0)
            fix_b = bool(m_sp) and after < len(line) and bool(_CONTENT_CH.match(line[after]))
            if not (fix_a or fix_b):
                continue
            # 원문 구간 — 앞뒤 어절 꼬리를 포함해 검색 유일성 확보(공백 전까지, 최대 12자)
            start = i - 1 if fix_a else i
            j = start
            while j > 0 and line[j - 1] not in " \t" and (start - j) < 12:
                j -= 1
            k = after
            while k < len(line) and line[k] not in " \t" and (k - after) < 12:
                k += 1
            if k == after:                     # 뒤에 붙일 내용이 없음
                continue
            original = line[j:k]
            corrected = (line[j:i] + (" " if fix_a else "") + ch + line[after:k])
            if (original == corrected
                    or original.replace(" ", "") != corrected.replace(" ", "")):
                continue
            if original in seen:
                continue
            seen.add(original)
            out.append((original, corrected))
    return out


if __name__ == "__main__":
    tests = [
        # close_orphan — 여는 따옴표 누락
        "중국은 과거부터 천인계획(千人計劃)'과 같은 국가 주도 프로그램을 통해",
        # 정상 짝 — 무변경이어야
        "인센티브를 제공하고 있다.'천인계획' 등 해외 인재 유치 프로그램이 있다.",
        "국립국어원'맞춤법규칙'에 따르면",         # 붙은 여는 따옴표(짝 있음) → 고아 아님
        "Jones' 이론과 '가설' 검증",               # 라틴 소유격 → 고아 카드 없음
        "don't stop, it's fine",                   # 아포스트로피 → 무변경
    ]
    for t in tests:
        print(f"  {t!r}")
        for o, c, why in find_unpaired_quotes(t):
            print(f"      짝: {o!r} ⇒ {c!r}   [{why}]")
        for o, c in find_quote_punct_spacing(t):
            print(f"      띄어쓰기: {o!r} ⇒ {c!r}")
    print()
    for t in ["Hampole, et al.(2025),“ Artificial Intelligence and jobs”, NBER."]:
        print(f"  {t!r}")
        for o, c in find_quote_punct_spacing(t):
            print(f"      띄어쓰기: {o!r} ⇒ {c!r}")
