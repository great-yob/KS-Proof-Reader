"""
core/citation_spans.py — 인용 구간(남의 저작물 제목·참고문헌) 찾기
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
띄어쓰기 **일관성**은 저자가 직접 쓴 본문의 표기를 저자 다수에 맞추는 일이다. 인용한 남의 제목·법령명·
참고문헌은 원 출처와 글자 하나까지 같아야 하므로 그 대상이 아니다 — 고치면 인용이 틀려진다.

2026-10-20 코퍼스(20원고) 실측: 일관성 카드 2,892장 중 바꿀 등장이 **전부** 제목 괄호·참고문헌 줄 안인
카드가 high만 82장이었다(「주요국 AI 인재 양성 및 유치 정책 연구」→'정책연구', 「…기본조례」→'기본 조례',
참고문헌 “…저출생 결정 요인에 대한 분석”→'결정요인'). 게다가 그 등장이 다수결 표까지 던져 저자 본문의
통일 방향을 흔들 수 있었다.

쓰는 곳(셋이 같은 구간을 봐야 한다):
  · 워커 [7] 복합명사 일관성 · [9.2] 이음매 변이표 — `mask()`한 텍스트로 센다(인용은 표를 던지지 않는다)
  · 검수 패널 `_mark_stem_boundary_skips` — 일관성 카드의 인용 구간 속 등장은 excluded(바꾸지 않는다)
  · `bracket_rules.find_paren_attach`의 저자 증거 경로 — 인용 속 괄호는 저자 다수에도 후보에도 들지 않는다
⚠ 일관성 카드 전용이다. 오탈자·문법 규칙 카드는 인용 속이라도 그대로 둔다(제목 속 오타는 편집자가 본다).
⚠ 의존성 없는 모듈로 둔다(PySide6·kiwi 금지) — 패널과 core 양쪽에서 import한다.
"""
import bisect
import re

# 제목·명칭 괄호(같은 줄 안, 300자 이하). U+F0854/F0855는 한/글이 겹낫표 『』를 내보내는 사용자 정의 문자다.
#   ⚠ <…>·【…】은 넣지 않는다 — 원고에서 표 제목('<표 5-19>')·소제목 표지로 쓰인다(저자 본문).
_TITLE_PAIRS = {"「": "」", "｢": "｣", "『": "』", "《": "》", "〈": "〉", "≪": "≫",
                "\U000f0854": "\U000f0855"}
_TITLE_RE = re.compile("|".join(f"{re.escape(o)}[^{re.escape(o)}{re.escape(c)}\\n]{{1,300}}{re.escape(c)}"
                                for o, c in _TITLE_PAIRS.items()))
# 참고문헌·출처 줄 — '저자(연도).' · '저자. (연도).' · '매체(2025.10.19.),' · '자료:'·'출처:'로 시작.
#   연도 괄호 뒤에 마침표·쉼표가 와야 한다 — 본문의 '연구소(2022)의 분류체계'는 아니다.
_REF_LINE_RE = re.compile(
    r"^[ \t　]*(?:[-•∙·*][ \t]*)?(?:"   # ⚠ \s 금지 — 줄바꿈을 삼켜 앞 빈 줄에서 매치가 시작된다
    r"[^\n]{1,80}?[\(（]\s*(?:19|20)\d\d[a-z]?(?:[.\-~∼]\d{1,2}){0,2}\.?\s*[\)）]\s*[.,]"
    r"|(?:자료|출처|Source|Sources)\s*[:：])",
    re.MULTILINE)
_MAX_REF_LINE = 400


def citation_spans(text: str) -> list:
    """인용 구간 [(시작, 끝), …](정렬·겹침 병합)."""
    if not text:
        return []
    spans = [m.span() for m in _TITLE_RE.finditer(text)]
    for m in _REF_LINE_RE.finditer(text):
        s = m.start()
        e = text.find("\n", s)
        e = len(text) if e < 0 else e
        if e - s <= _MAX_REF_LINE:
            spans.append((s, e))
    spans.sort()
    out = []
    for s, e in spans:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def mask(text: str, spans=None) -> str:
    """인용 구간의 **한글만** 제어문자로 덮은 같은 길이의 텍스트(한글 런·띄어쓴 쌍에서 빠진다).

    ⚠ 괄호·부호·공백은 그대로 둔다 — 괄호까지 덮으면 '｣의 대응상황'의 '의'가 홀로 선 낱말처럼 읽혀
      이음매 패스가 '의 대응상황'→'의 대응 상황' 같은 엉뚱한 카드를 만들었다(2026-10-20 구현 중 실측).
    """
    spans = citation_spans(text) if spans is None else spans
    if not spans:
        return text
    buf = list(text)
    for s, e in spans:
        for i in range(s, e):
            if "가" <= buf[i] <= "힣":
                buf[i] = "\x00"
    return "".join(buf)


def covers(spans: list, start: int, end: int) -> bool:
    """[start, end) 가 어느 인용 구간 안에 통째로 드는가."""
    i = bisect.bisect_right(spans, (start, float("inf"))) - 1
    return i >= 0 and spans[i][0] <= start and end <= spans[i][1]
