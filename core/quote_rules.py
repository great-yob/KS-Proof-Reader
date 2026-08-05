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
    ⚠ 예외는 `find_reversed_quotes` 하나 — 여기서만 **따옴표 두 글자의 '방향'**을
    바꾼다(’…‘ → ‘…’). 안쪽 글자는 여전히 불변이고, 저신뢰 검수 카드다.
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
_CURLY_CLOSE_SHAPE = {"’", "”"}                     # 굽은 '닫는 모양'
_ORPHAN_OPENER_FOR = {"’": "‘", "”": "“"}           # 고아 닫는 따옴표 → 보완할 여는 짝
# 굽은따옴표의 올바른 방향 짝 — 역방향 사용(’…‘)을 바로잡을 때 쓴다.
_CORRECT_PAIR = {"s": ("‘", "’"), "d": ("“", "”")}

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
        #   ⚠ **두 자리 숫자 뒤에 한글 단위가 붙으면 연도가 아니다**(2026-08-05 실측 수정):
        #   기사 제목 "…쓸어간다...'10억' 러브콜에"의 `'10억'`이 연도로 오인돼 여는 따옴표가
        #   스택에서 빠졌고, 그 결과 뒤의 정상 닫는 따옴표가 고아가 돼 거짓 보완 카드
        #   (`10억'`→`'10억'`)가 났다. 연도 약물은 뒤가 공백·부호이거나 '년'(‘19년)이다.
        if (ch in ("'", "’") and not (_CONTENT_CH.match(prev) or prev in _BRACKET_CLOSE)
                and re.match(r"\d\d(?!\d)", line[i + 1:])
                and not re.match(r"\d\d(?!년)[가-힣]", line[i + 1:])):
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


def quote_roles_text(text: str) -> dict:
    """문서 전체 기준 **절대 인덱스** 역할표 — {abs_index: role}.

    판정 자체는 줄 단위(_analyze_line)로 하고 오프셋만 더한다. 문서 오프셋으로 도는
    규칙(spacing_rules.find_quote_spacing)이 '이 따옴표가 정말 여는 것인가'를 물을 때
    쓴다 — 글자 모양만 보는 쪽(‘…’ 정규식)은 역방향 원고에서 통째로 오판한다.
    """
    roles, off = {}, 0
    for line in text.split("\n"):
        if any(q in line for q in _ALL_QUOTES):
            for i, r in _analyze_line(line)[0].items():
                roles[off + i] = r
        off += len(line) + 1
    return roles


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
            # ⚠ 고아가 **여는 모양**(‘ “)이면 이 원고는 따옴표를 뒤집어 쓰고 있다
            #   (실파일: '쌍륜 구동(Double-Wheel Drive)‘에 따라'). 그대로 앞에 ‘를
            #   넣으면 ‘…‘ 라는 또 다른 역방향 짝이 완성될 뿐이므로, 짝을 채우면서
            #   **고아 쪽 방향도 함께 바로잡는다**(‘…’). 방향이 맞는 고아(’ ” )·곧은
            #   따옴표는 기존대로 앞에 짝만 넣는다(글자 불변).
            cls = _Q_CLASS[ch]
            if ch in _CURLY_OPEN_SHAPE:
                opener, closer = _CORRECT_PAIR[cls]
                why = (f"짝 없는 따옴표 {ch} — 여는 따옴표 추가 + 방향 교정"
                       f"(넣을 위치는 검토 필요)")
            else:
                opener, closer = _ORPHAN_OPENER_FOR.get(ch, ch), ch
                why = (f"닫는 따옴표 {ch} 의 짝 여는 따옴표가 없음 — 여는 따옴표 추가"
                       "(넣을 위치는 검토 필요)")
            original = run + ch + josa
            corrected = opener + run + closer + josa
            if original in seen:
                continue
            seen.add(original)
            out.append((original, corrected, why))
    return out


def find_reversed_quotes(text: str) -> list:
    """**따옴표 방향 오류**(’성과‘ → ‘성과’)를 [(original, corrected, reason), …]로.

    사용자 보고 2026-08-05(실파일: 中國科學院 보고서 — 문서 전체가 굽은따옴표를
    뒤집어 쓴다: `중국의 ’성과‘가 아니라 ’메커니즘‘을`, 18곳). 이런 원고에서는
    **여는 자리에 닫는 모양(’ ”)** 이, **닫는 자리에 여는 모양(‘ “)** 이 온다.
    이건 띄어쓰기 문제가 아니라 부호 자체의 오류인데, 모양만 보는 규칙
    (spacing_rules.find_quote_spacing의 ‘…’ 정규식)은 **한 인용의 닫는 따옴표와
    다음 인용의 여는 따옴표**를 짝으로 오인해 그 사이에 공백을 넣으라는 카드를 냈다
    ('성과‘가 아니라 ’' → '성과 ‘가 아니라 ’'). 원인을 고치는 쪽이 이 규칙이다.

    판정 = **문맥 스택이 확정한 짝**(_analyze_line)에서 여는 자리 글자가 굽은
    '닫는 모양'인 경우. 글자 모양이 아니라 역할로 보기 때문에 정상 원고·혼용 원고
    (‘…' 곧은/굽은 섞임)에는 발동하지 않는다.

    가드(보수 — 짝 판정이 흔들릴 수 있는 자리는 전부 뺀다):
      · 여는 따옴표 **바로 뒤가 내용 글자**여야 한다(따옴표는 인용문에 붙는다).
        문장 사이에 홀로 떠 있는 따옴표가 우연히 짝지어진 경우를 배제한다.
      · 닫는 따옴표 **바로 앞이 공백이 아니어야** 한다(같은 이유. 인용이 부호로
        끝나는 '…했다.’ 는 허용).
      · 인용 내용은 1~60자, 탭 없음(탭이 든 원문은 본문 탐색이 불안정), 내용
        글자를 하나 이상 포함.
    바꾸는 것은 **따옴표 두 글자의 방향**과, 아래 조사 붙임의 **공백 하나**뿐이다 —
    안쪽 글자는 건드리지 않는다.

    ⚠⚠ **닫는 따옴표 뒤 조사는 이 카드가 함께 붙인다**(2026-08-05 사용자 보고):
    `’백인계획/천인계획/만인계획‘ 을` 처럼 방향 오류 + 조사 띄움이 겹친 자리에서
    조사 붙임이 통째로 **미탐**됐다. 그 일을 하던 `spacing_rules.find_quote_spacing`은
    **글자 모양**(‘…’)으로 짝을 찾으므로 뒤집힌 짝을 아예 보지 못한다.
    ★ 그렇다고 그쪽을 방향 인식으로 고치면 **같은 자리에 카드가 둘** 생기고, 겹침
    해소(`_resolve_overlaps`: 최장 승)가 둘 중 하나를 조용히 가린다 — 방금 그
    증상으로 방향 카드가 사라진 전례가 있다(morph.find_symbol_noun_spacing 건).
    → **한 자리는 한 카드**가 원칙이므로, 방향 오류 자리의 조사 붙임은 이 규칙이
    떠맡아 `’…‘ 을` → `‘…’을` 한 장으로 낸다.
    ⚠ 공백은 **정확히 한 칸**만 흡수한다(카드 원문이 브리지 RepeatFind의 리터럴
    탐색 대상이라, 여러 칸·탭을 넣으면 본문에서 못 찾는 카드가 된다).
    ⚠ **여는 따옴표 앞 띄움**(`낱말’인용‘` → `낱말 ’인용‘`)은 다루지 않는다 —
    실측 16짝 중 0건이고, 앵커를 앞 낱말까지 늘리면 다른 규칙과 겹칠 위험만 커진다.
    """
    out, seen = [], set()
    for line in text.split("\n"):
        if not any(q in line for q in _ALL_QUOTES):
            continue
        roles, pairs = _analyze_line(line)
        for oi, role in roles.items():
            if role != "open" or oi not in pairs:
                continue
            och = line[oi]
            if och not in _CURLY_CLOSE_SHAPE:        # 여는 자리가 정상 모양 → 무관
                continue
            ci = pairs[oi]
            if ci <= oi:
                continue
            inner = line[oi + 1:ci]
            if not (1 <= len(inner) <= 60) or "\t" in inner:
                continue
            if not _CONTENT_CH.search(inner):
                continue
            if not (_CONTENT_CH.match(inner[0]) or inner[0] in "([{（「『【《〈"):
                continue                              # 여는 따옴표 뒤 공백/부호 → 스킵
            if inner[-1].isspace():
                continue                              # 닫는 따옴표 앞 공백 → 스킵
            opener, closer = _CORRECT_PAIR[_Q_CLASS[och]]
            # 닫는 따옴표 + 공백 한 칸 + 조사 → 조사를 붙인다(위 ⚠⚠ 참조).
            rest, josa, tail = line[ci + 1:], "", ""
            if rest.startswith(" ") and not rest[1:2].isspace():
                jm = _JOSA_RE.match(rest[1:])
                if jm:
                    josa = jm.group(1)
                    tail = " " + josa
            original = line[oi:ci + 1] + tail
            corrected = opener + inner + closer + josa
            if original == corrected or original in seen:
                continue
            # 불변식 — 따옴표와 공백을 뺀 알맹이가 같아야 한다(글자 변경 0).
            _kernel = lambda s: "".join(c for c in s if c not in _ALL_QUOTES and c != " ")
            if _kernel(original) != _kernel(corrected):
                continue
            seen.add(original)
            why = (f"따옴표 방향 오류 수정")
            if josa:
                why += f" (닫는 따옴표 뒤 조사 '{josa}'도 붙임)"
            out.append((original, corrected, why))
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
        # 방향 오류 — 여닫이를 뒤집어 쓴 원고(2026-08-05 실보고)
        "중국의 ’성과‘가 아니라 ’메커니즘‘을 보는 접근이 필요함",
        "그는 ”준비되면“ 이라고 말했다.",
        "혁신: ’백인계획/천인계획/만인계획‘ 을 통한 영입",   # 방향 + 조사 붙임 한 카드
        "’표준‘ 을지로에서 만났다.",                        # '을지로'는 조사 아님 → 붙임 없음
        "그는 ‘성과’가 아니라 ‘메커니즘’을 보았다.",   # 방향 정상 → 무변경
        "투자금 '10억' 유치에 성공했다.",             # 연도 약물 아님 → 고아 카드 없음
        "'19년 대비 '20년 실적",                     # 연도 약물 → 무변경
    ]
    for t in tests:
        print(f"  {t!r}")
        for o, c, why in find_unpaired_quotes(t):
            print(f"      짝: {o!r} ⇒ {c!r}   [{why}]")
        for o, c, why in find_reversed_quotes(t):
            print(f"      방향: {o!r} ⇒ {c!r}")
        for o, c in find_quote_punct_spacing(t):
            print(f"      띄어쓰기: {o!r} ⇒ {c!r}")
    print()
    for t in ["Hampole, et al.(2025),“ Artificial Intelligence and jobs”, NBER."]:
        print(f"  {t!r}")
        for o, c in find_quote_punct_spacing(t):
            print(f"      띄어쓰기: {o!r} ⇒ {c!r}")
