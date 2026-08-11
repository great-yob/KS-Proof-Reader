"""
output/annot_text.py — 메모·PDF 주석 본문 문구 (단일 출처)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
한/글 메모(`ui/workers/apply_worker._memo_text`)와 PDF 주석
(`output/pdf_annotator._memo_body`)은 **같은 말**을 해야 한다. 두 곳에서 따로
조립하던 동안 같은 교정이 산출물마다 다르게 읽혔다 — 문구를 여기 한 곳에 둔다.

서식(두 줄):
    ‘원문’ → ‘교정문’
    [교정 유형] 사유

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
_WS_RE = re.compile(r"\s+")

# 낱낱에 적을 이유가 없는 표지 — 산출물 전체가 이미 그 뜻이다.
_DROP_TAGS = ("검수",)

# 사유 길이 상한 — 메모 말풍선·주석 팝업 모두 좁다.
_REASON_MAX = 300


def clean_reason(reason: str, category: str = "") -> str:
    """사유에서 앞머리 중복 표지를 떼고 한 줄로 만든다."""
    text = _WS_RE.sub(" ", reason or "").strip()
    cat = (category or "").strip()
    while True:
        m = _LEAD_TAG_RE.match(text)
        if not m:
            break
        tag = m.group(1).strip()
        if tag in _DROP_TAGS or (cat and tag == cat):
            text = text[m.end():]
            continue
        break
    return text[:_REASON_MAX].strip()


def annotation_body(original: str, corrected: str, category: str = "",
                    reason: str = "", extra: str = "") -> str:
    """메모 한 장 / PDF 주석 하나의 본문.

    ⚠ 화면 로그 규약(개별 예시 금지)은 여기 적용되지 않는다 — 이건 로그가 아니라
      **산출물**이고, 편집자가 그 자리에서 무엇을 무엇으로 고칠지 읽어야 한다.
    """
    orig = (original or "").strip()
    corr = (corrected or "").strip()
    lines = [f"‘{orig}’ → ‘{corr}’" if corr and corr != orig
             else f"‘{orig}’ 확인 필요"]

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
