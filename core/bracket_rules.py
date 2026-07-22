"""
core/bracket_rules.py — 괄호(묶음표) 짝 맞추기 (문장부호 완결성)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
국립국어원 문장부호 규정상 소괄호 ( )·대괄호 [ ]·중괄호 { } 등 묶음표는 **반드시
짝(여는+닫는)을 이룬다.** 실제 원고에서는 한쪽이 누락되기 쉽다:
  · 여는 괄호만:  '리플렛(외로움안녕'      → '리플렛(외로움안녕)'   (닫는 괄호 추가)
  · 닫는 괄호만:  '…, 고립예방센터)'       → '…, (고립예방센터)'   (여는 괄호 추가)

⚠ 예외(매우 중요) — **홑 닫는 괄호는 글머리표(라벨)로 흔히 쓰인다.** 이때의 ')'는
   짝이 빠진 게 아니라 의도된 표기이므로 **건드리지 않는다**:
     '예)'  '답)'  '주)'  '1)'  '2)'  '가)'  'ㄱ)'  'a)'  …
   → 닫는 괄호 앞 '내용'이 한 글자·숫자·알려진 라벨어면 라벨로 보고 스킵한다.

설계 정합성:
  · 글자를 **치환하지 않고 괄호 한 짝만 삽입**한다(환각 0 — 띄어쓰기 백스톱과 동궤).
    누락된 짝을 '어디에' 넣을지는 휴리스틱이라(여는 괄호: 줄 끝/문장종결부호 직전,
    닫는 괄호: 직전 내용 어구 앞) **저신뢰 '검수 카드'** 로만 노출한다(자동수정 아님).
  · 줄(문단) 단위로 스택을 돌려 짝을 센다. 같은 줄에 균형 괄호와 홑 괄호가 섞여
    있어도('120(체크리스트 포함), 고립예방센터)') 균형분은 상쇄되고 홑분만 잡힌다.
  · GUI-agnostic (PySide6 미사용) — core/ 규칙. 사전·형태소 불필요(순수 규칙).
"""

import re

# 여는→닫는 괄호 짝. 소괄호(반각/전각)를 중심으로 흔한 묶음표를 포괄한다.
_PAIRS = {
    "(": ")", "（": "）", "[": "]", "{": "}",
    "〔": "〕", "「": "」", "『": "』", "【": "】", "《": "》", "〈": "〉",
}
_OPENERS = set(_PAIRS)
_CLOSERS = {close: open_ for open_, close in _PAIRS.items()}

# 괄호 안/밖 '내용'으로 보는 글자(이 글자들의 연속 = 한 어구).
_CONTENT = re.compile(r"[0-9A-Za-z가-힣]")
# 닫는 괄호를 넣을 자리를 끊는 문장 종결 부호(이 앞에 닫는 괄호를 넣어야 자연스럽다).
_TERMINATOR = ".!?…。！？"
# 홑 닫는 괄호가 '글머리표(라벨)'로 쓰이는 알려진 짧은 머리말 — 여는 괄호 추가 대상 제외.
_LABEL_WORDS = frozenset({
    "예", "답", "주", "참", "문", "비고", "정답", "출처", "예시", "보기", "주의", "단",
})
# 글머리표(불릿)로 시작하는 새 항목 줄 — 괄호 짝의 경계(이전 항목 미닫힘 괄호를 고아 확정).
#   '- ', '· ', '○ ' 등 기호 불릿만. 숫자 라벨('1)')은 _is_label_close와 얽혀 보수적으로 제외.
_BULLET_RE = re.compile(r"^\s*[-–—·•∙○◦●▪▫■□▷▶*※]\s")


def _is_label_close(run: str) -> bool:
    """닫는 괄호 앞 내용 run이 글머리표(라벨)로 보이는가? (= 짝 없는 게 정상)

    '예)·답)·1)·가)·a)'처럼 **한 글자, 숫자, 알려진 라벨어**면 의도된 라벨로 본다.
    '고립예방센터)'처럼 두 글자 이상 일반 어구면 라벨이 아니라 누락(여는 괄호 필요)으로 본다.
    """
    if not run:
        return True                      # 괄호 앞에 아무 내용 없음 → 판단 보류(스킵)
    if len(run) <= 1:
        return True                      # '가)·1)·a)' 한 글자 머리표
    if run.isdigit():
        return True                      # '12)' 순서 번호
    return run in _LABEL_WORDS           # '예)·답)·주)·비고)' 라벨어


def find_unbalanced_brackets(text: str) -> list:
    """짝이 맞지 않는 괄호 후보를 [(original, corrected, reason), …]로 반환.

    · 여는 괄호만 있는 줄 → 줄 끝(또는 첫 문장종결부호 직전)에 닫는 괄호 추가.
    · 닫는 괄호만 있고 라벨이 아닌 경우 → 직전 어구 앞에 여는 괄호 추가.
    공백/글자는 그대로 두고 괄호 한 짝만 더한다(환각 0). 중복 제거. 실패 시 [].

    ⚠ **괄호 짝은 줄바꿈을 넘나든다(중요)** — HWP 추출은 표 셀·자동 줄바꿈으로 한 묶음표를
       여러 줄로 쪼갠다(예: '[ex. …kg/m3,'(L1) / '(HEFA…)…kg/m3]'(L3), 사이 빈 줄). 줄 단위로
       짝을 세면 멀쩡한 '[ … ]'가 양쪽에서 홑괄호로 오인돼 거짓 검수 카드 2개가 난다(사용자
       보고). → **전체 텍스트를 하나의 스택으로** 짝을 센다(줄바꿈은 괄호 경계가 아님). 남은
       홑괄호만 진짜 누락으로 보고, '어디에 넣을지'(삽입 지점)만 그 줄 안에서 국소 계산한다.

    ⚠⚠ 단, **글머리표(불릿) 새 항목은 괄호 경계다(2026-07-02)** — 전체 스택만 쓰면 서로 다른
       항목의 **반대 방향 오류가 상쇄**된다: '리플렛(외로움안녕'(항목1, 미닫힘)과 '고립예방센터)'
       (항목2, 잉여 닫힘)가 짝으로 오인돼 **둘 다 미탐**(사용자 실측 test.hwp — '제공함([그림 3-2
       참고].'의 '('도 뒤쪽 잉여 ')'와 상쇄). → '- ·○' 등 **글머리표로 시작하는 줄을 만나면
       미닫힘 여는 괄호를 고아로 확정(flush)**한다. 표 셀 잘림 조각은 글머리표 없이 이어지므로
       기존 줄넘김 짝짓기가 유지된다(무회귀).
    """
    out, seen = [], set()
    lines = text.split("\n")
    stack = []          # [(line_idx, col, opener), …] — 아직 안 닫힌 여는 괄호(줄 넘나듦)
    orphan_open = []    # flush로 고아 확정된 여는 괄호
    orphan_close = []   # [(line_idx, col, closer), …] — 짝 없는 닫는 괄호
    for li, line in enumerate(lines):
        if li and stack and _BULLET_RE.match(line):
            orphan_open.extend(stack)    # 새 항목 시작 — 이전 항목의 미닫힘 괄호는 고아 확정
            stack.clear()
        for col, ch in enumerate(line):
            if ch in _OPENERS:
                stack.append((li, col, ch))
            elif ch in _CLOSERS:
                if stack and stack[-1][2] == _CLOSERS[ch]:
                    stack.pop()          # 짝 맞음 → 상쇄(줄이 달라도 OK)
                else:
                    orphan_close.append((li, col, ch))
    orphan_open.extend(stack)

    # ── 짝 없는 여는 괄호 → 닫는 괄호 추가 (삽입 지점은 해당 줄 안에서) ──
    for li, oi, opener in orphan_open:
        line = lines[li]
        closer = _PAIRS[opener]
        # 여는 괄호 앞 단어까지 포함(가독성·매칭 유일성).
        seg_start = oi
        while seg_start > 0 and _CONTENT.match(line[seg_start - 1]):
            seg_start -= 1
        # 닫는 괄호 삽입 지점: 여는 괄호 뒤 첫 문장종결부호 직전, 없으면 줄(우측 공백 제외) 끝.
        seg_end = len(line.rstrip())
        for j in range(oi + 1, seg_end):
            if line[j] in _TERMINATOR:
                seg_end = j
                break
        original = line[seg_start:seg_end].rstrip()
        if not original.strip() or original == closer:
            continue
        corrected = original + closer
        key = ("close", original)
        if key not in seen:
            seen.add(key)
            out.append((original, corrected,
                        f"여는 괄호 '{opener}'의 짝 '{closer}'가 없음 — 닫는 괄호 추가"))

    # ── 짝 없는 닫는 괄호 → (라벨이 아니면) 여는 괄호 추가 ──
    for li, ci, closer in orphan_close:
        line = lines[li]
        opener = _CLOSERS[closer]
        s = ci
        while s > 0 and _CONTENT.match(line[s - 1]):
            s -= 1
        run = line[s:ci]
        if _is_label_close(run):
            continue
        original = run + closer
        corrected = opener + original
        key = ("open", original)
        if key not in seen:
            seen.add(key)
            out.append((original, corrected,
                        f"닫는 괄호 '{closer}'의 짝 '{opener}'가 없음 — 여는 괄호 추가"))
    return out


# ── 종결어미 뒤 인용·보충 괄호 붙임 ─────────────────────────────────────────
# 국립국어원 문장 부호 해설: 괄호(주석·보충)는 **앞말에 붙여 쓴다**. 실제 원고에서는
#   문장 종결 뒤 출처 괄호를 띄어 쓰는 오류가 잦다(사용자 보고 2026-07-03, 30.hwp):
#     '있다 (경향신문' → '있다(경향신문' / '것이다 (Korea-EU' → '것이다(Korea-EU'
# ⚠ 좁게 간다(과교정 0): **앞 어절의 마지막 형태소가 종결어미(EF)** 일 때만 —
#   표 머리 'A기업 (데이터…)'·'연구소 (2025a)'·'기업 수 (개)' 같은 체언+괄호 간격은
#   저자 레이아웃일 수 있어 건드리지 않는다. 괄호 안이 순수 번호('(4)'·'(032)')면
#   라벨/전화번호라 스킵(find_paren_josa의 번호 라벨 예외와 동일 철학).
#   공백만 제거(글자 불변·환각 0) — 탐지 전용 저신뢰 '검수 카드'.
_ATTACH_RE = re.compile(r"([가-힣]{2,}) ([(（])")


def find_paren_attach(text: str) -> list:
    """종결어미로 끝난 어절과 여는 괄호 사이의 공백 제거 후보 [(original, corrected), …].

    kiwipiepy로 앞 어절의 마지막 형태소가 EF(종결어미)인지 검사한다('있다·것이다·한다').
    kiwi 미설치/실패 시 [] (graceful).
    """
    try:
        from core import morph as _morph
        kiwi = _morph._get_kiwi()
    except Exception:
        kiwi = None
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    ef_cache = {}
    for line in text.split("\n"):
        for m in _ATTACH_RE.finditer(line):
            word, opener = m.group(1), m.group(2)
            # 앞 어절이 문장 종결형인가 (kiwi EF) — 어절 단위 캐시
            is_ef = ef_cache.get(word)
            if is_ef is None:
                try:
                    last = kiwi.analyze(word)[0][0][-1]
                    is_ef = last.tag.startswith("EF")
                except Exception:
                    is_ef = False
                ef_cache[word] = is_ef
            if not is_ef:
                continue
            # 괄호 안 내용 미리보기 — 순수 번호/라벨('(4)'·'(032)')이면 스킵
            rest = line[m.end():m.end() + 40]
            inner = rest.split(")", 1)[0] if ")" in rest else rest
            if re.fullmatch(r"\s*\d[\d\s.,·\-]*", inner or ""):
                continue
            # 괄호 뒤 내용 런 포함(검색 유일성) — 공백/닫는괄호/쉼표 전까지 최대 15자
            run = re.match(r"[0-9A-Za-z가-힣·\-]{1,15}", rest)
            if not run:
                continue
            original = f"{word} {opener}{run.group()}"
            corrected = f"{word}{opener}{run.group()}"
            if original in seen:
                continue
            seen.add(original)
            out.append((original, corrected))
    return out


if __name__ == "__main__":
    tests = [
        "리플렛(외로움안녕",                          # 닫는 괄호 추가
        "120(체크리스트 포함), 고립예방센터)",         # 여는 괄호 추가 (고립예방센터)
        "예)",                                        # 라벨 → 무변경
        "1) 첫째 2) 둘째",                             # 숫자 라벨 → 무변경
        "가) 항목 나) 항목",                           # 한 글자 라벨 → 무변경
        "정상 괄호(설명)이다",                          # 균형 → 무변경
        "(주)경성미디어",                              # 균형 → 무변경
        "그는 말했다(중요 이것이 핵심이다.",            # 문장종결부호 직전 닫는 괄호
    ]
    for t in tests:
        print(f"  {t!r}")
        for o, c, why in find_unbalanced_brackets(t):
            print(f"      → {o!r} ⇒ {c!r}   [{why}]")
    print()
    attach_tests = [
        "유도하고 있다 (경향신문, 2025; 동아사이언스, 2025).",   # EF+괄호 → 붙임
        "반영한 것이다 (Korea-EU Research Centre, 2022).",      # EF+괄호 → 붙임
        "A기업 (데이터·바이오 예측)",                            # 체언 → 무변경
        "업력별 AI 기업 수 (개)",                                # 체언(수) → 무변경
        "정책연구소 (2025a)",                                    # 체언 → 무변경
        "혁신성을 중심으로 심사한다 (콘텐츠 산업동향, 2025)",     # EF → 붙임
        "전화 문의 (032) 674-7335",                              # 순수 번호 → 무변경
    ]
    for t in attach_tests:
        print(f"  {t!r}")
        for o, c in find_paren_attach(t):
            print(f"      → {o!r} ⇒ {c!r}")
