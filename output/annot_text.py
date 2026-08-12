"""
output/annot_text.py — 메모·PDF 주석 본문 문구 (단일 출처)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
한/글 메모(`ui/workers/apply_worker._memo_text`)와 PDF 주석
(`output/pdf_annotator._memo_body`)은 **같은 말**을 해야 한다. 두 곳에서 따로
조립하던 동안 같은 교정이 산출물마다 다르게 읽혔다 — 문구를 여기 한 곳에 둔다.

서식:
    원문 → 교정문
    [교정 유형] 사유
    — 부연·근거

⚠ **교정 전후 표기에 따옴표를 두르지 않는다**(사용자 지정 2026-08-12). 사유 안에는
  원고에서 인용한 따옴표가 이미 들어 있어(`'···해야 한다/된다'는 띄어 씀이 원칙`),
  첫 줄까지 따옴표를 두르면 말풍선 한 장에 따옴표가 네 겹으로 겹쳐 보인다.
  `→`가 이미 '무엇을 무엇으로'를 말하므로 따옴표는 정보를 더하지 않는다.

⚠ **사유의 줄바꿈은 살린다**(사용자 지정 2026-08-12). 파이프라인의 사유는 `판정`과
  `— 근거`를 이미 두 줄로 만들어 보내는데(`띄어쓰기 일관성 → 다수 표기로 통일\n—
  국방분야(24) : 국방 분야(2)`), 예전 `clean_reason`이 `\s+`로 **줄바꿈까지 뭉개어**
  한 줄로 이어 붙였다. 좁은 말풍선에서 판정과 근거가 붙어 읽혔다. 줄바꿈이 없는
  사유(`보조용언 띄어쓰기 — '···' 원칙`)도 같은 모양이 되도록 `— ` 앞에서 끊는다.
  ⚠ 다만 **괄호 안의 `—`는 끊지 않는다** — `(AI 판단 모호 — 검토 필요)`처럼 부연이
    괄호로 이미 묶여 있으면 끊는 순간 여는 괄호와 닫는 괄호가 다른 줄로 갈라진다.

⚠ **`[검수]` 표지는 붙이지 않는다**(사용자 지정 2026-08-11). 메모본과 주석본은
  그 자체가 '사람이 보고 판단할 것'을 모아 둔 산출물이라, 낱낱에 '검수'라고 적는 것은
  모든 줄에 같은 말을 반복하는 것과 같다. 정오표는 시트(적용/교정/확인 필요)로 이미
  구분하므로 그쪽에는 영향이 없다.

⚠ **같은 말을 두 번 적지 않는다.** 파이프라인이 만든 사유는 앞에 자기 유형을 다시
  달고 오는 경우가 많아(`[규범표기] 규범표기인 '슈퍼마켓' 권장`), 유형 칸까지 더하면
  한 줄에 '규범표기'가 **세 번** 나왔다(사용자 보고 2026-08-11). 아래 두 단계로 줄인다:
    ① 사유 앞머리의 `[검수]`·`[같은 유형]` 표지를 떼고,
    ② 그러고도 사유가 그 유형 이름으로 **시작하면** 유형 표지를 아예 생략한다.
  결과: `규범표기인 '슈퍼마켓' 권장` / `[맞춤법] 중복 조사 삭제 …`
"""

import re

_LEAD_TAG_RE = re.compile(r"^\s*\[([^\]]{1,12})\]\s*")
# ⚠ 가로 공백만 뭉친다 — `\s+`로 하면 사유가 이미 들고 온 줄바꿈까지 사라진다(위 주석).
_WS_RE = re.compile(r"[^\S\n]+")
_BLANK_RE = re.compile(r"\n{2,}")
# 부연·근거를 여는 구분자. 앞뒤 공백이 있는 `—`만 본다(글자에 붙은 것은 문장부호다).
_DASH_RE = re.compile(r"[^\S\n]*—[^\S\n]+")
_OPEN_PARENS = "([{（〔［｛"
_CLOSE_PARENS = ")]}）〕］｝"

# 낱낱에 적을 이유가 없는 표지 — 산출물 전체가 이미 그 뜻이다.
_DROP_TAGS = ("검수",)

# 사유 길이 상한 — 메모 말풍선·주석 팝업 모두 좁다.
_REASON_MAX = 300


def _break_before_dash(text: str) -> str:
    """`— 부연` 앞에서 줄을 바꾼다. **괄호 안의 `—`는 건드리지 않는다.**

    괄호 깊이를 세면서 훑는 이유는 `(AI 판단 모호 — 검토 필요)` 같은 사유 때문이다 —
    이미 괄호가 부연을 묶고 있는데 거기서 끊으면 괄호 짝이 두 줄로 갈라진다.
    """
    out, depth, i = [], 0, 0
    for m in _DASH_RE.finditer(text):
        seg = text[i:m.start()]
        depth += sum(1 for ch in seg if ch in _OPEN_PARENS)
        depth -= sum(1 for ch in seg if ch in _CLOSE_PARENS)
        depth = max(depth, 0)
        out.append(seg)
        # 줄 첫머리에 이미 있는 `—`는 그대로 둔다(사유가 스스로 줄을 바꿔 보낸 경우).
        before = text[:m.start()]
        at_line_head = (not before) or before.endswith("\n")
        out.append(m.group(0) if (depth or at_line_head) else "\n— ")
        i = m.end()
    out.append(text[i:])
    return "".join(out)


def clean_reason(reason: str, category: str = "") -> str:
    """사유에서 앞머리 중복 표지를 떼고 `판정 / — 근거` 두 줄 꼴로 다듬는다."""
    text = _WS_RE.sub(" ", (reason or "").replace("\r\n", "\n").replace("\r", "\n"))
    cat = (category or "").strip()
    while True:
        m = _LEAD_TAG_RE.match(text.strip())
        if not m:
            break
        tag = m.group(1).strip()
        if tag in _DROP_TAGS or (cat and tag == cat):
            text = text.strip()[m.end():]
            continue
        break
    text = _break_before_dash(text.strip())
    # 줄마다 가장자리 공백을 떼고 빈 줄을 없앤다(말풍선이 좁다).
    text = "\n".join(ln.strip() for ln in text.split("\n"))
    return _BLANK_RE.sub("\n", text)[:_REASON_MAX].strip()


def annotation_body(original: str, corrected: str, category: str = "",
                    reason: str = "", extra: str = "") -> str:
    """메모 한 장 / PDF 주석 하나의 본문.

    ⚠ 화면 로그 규약(개별 예시 금지)은 여기 적용되지 않는다 — 이건 로그가 아니라
      **산출물**이고, 편집자가 그 자리에서 무엇을 무엇으로 고칠지 읽어야 한다.
    """
    orig = (original or "").strip()
    corr = (corrected or "").strip()
    # ⚠ 따옴표를 두르지 않는다(파일 머리말 참조) — `→`가 이미 방향을 말한다.
    lines = [f"{orig} → {corr}" if corr and corr != orig
             else f"{orig} 확인 필요"]

    cat = (category or "").strip()
    body = clean_reason(reason, cat)
    # ② 사유가 이미 그 유형 이름으로 시작하면 표지를 생략한다.
    if cat and body.startswith(cat):
        second = body
    elif cat and body:
        second = f"[{cat}] {body}"
    else:
        second = f"[{cat}]" if cat else body
    if second:
        lines.append(second)
    if extra:
        lines.append(extra)
    return "\n".join(lines)
