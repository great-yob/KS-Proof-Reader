"""
eval/corpus_harness/invariants.py — 계층 간 불변식 검사 (워커 최종 출력 전체에 기계 검사)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
골드셋은 문장 단위로 '그 규칙이 맞게 판정하는가'를 본다. 그런데 이 저장소의 결함은 대개 **계층 사이**
에서 났다 — 한 계층이 내린 판정을 뒤 계층이 조용히 뒤집거나(가드 강등이 신뢰도 통일에 되올려짐),
한 계층에만 있는 규칙이 다른 계층이 만든 카드에 닿지 않았다(안전망 빼기가 [7]에만 있어 [9.2] 카드와
같은 원문 카드가 두 장). 코퍼스 게이트는 '기준선과 다른가'만 보므로, 기준선에 처음부터 박힌 위반은
못 본다. 여기서는 **어떤 원고·어떤 실행에서도 성립해야 하는 성질**을 카드 출력 전체에 검사한다.

각 불변식의 근거는 CLAUDE.md·docs/pipeline-reference.md에 이미 적힌 규칙이다. 위반은 '기준선과
다름'(△)이 아니라 **실패**(✗)다 — 판정해서 서명할 대상이 아니라 고칠 대상이다.
새 불변식을 더할 때는 ① 문서에 근거가 있고 ② 현 코퍼스에서 위반 0(또는 위반을 고친 뒤 0)이어야 한다.

  run_corpus.py check NAME   — 한 실행(runs/NAME)을 검사
  run_corpus.py gate          — 두 방식(AI 켬·끔) 결과에 자동으로 돈다(위반이면 종료 코드 1)
"""
import re
from collections import Counter

_HANGUL = re.compile(r"[가-힣]+")
_WS = re.compile(r"\s")


def _letters(s: str) -> str:
    return _WS.sub("", s or "")


# (id, 설명, 근거) — 판정식은 check_doc 안에 같은 id로 있다.
INVARIANTS = (
    ("anchor", "원문이 원고에 있다",
     "[9.5] 적용 가능성 검증 — 문서에 없는 원문은 적용이 100% 실패한다"),
    ("review_low", "사유가 [검수]로 시작하면 low다",
     "가드 강등 표지 — 신뢰도 통일이 되올리면 '[검수]인데 자동 적용'이 된다(2026-08-03 사고)"),
    ("fixed_low", "실단어(realword)·사전 안전망(dict_flag) 카드는 low다",
     "realword 불변식 ② · 안전망은 확인 권장 카드"),
    ("flag_shape", "안전망 카드만 원문=교정문이다",
     "안전망은 글자를 바꾸지 않는 확인 권장, 그 밖의 카드는 실제 교정(Correction.is_valid)"),
    ("space_only", "띄어쓰기·일관성·이음매 카드는 글자를 바꾸지 않는다",
     "결정론 띄어쓰기는 공백만 가감 · junction_pass 불변식"),
    ("unique_original", "한 원고에 같은 원문의 카드는 한 장이다",
     "같은 원문 카드 두 장은 적용 등장 인덱스를 어긋나게 한다(워커 [7.5]·[8] 주석)"),
    ("confidence_domain", "신뢰도는 high 또는 low다",
     "AI medium은 가드 체인 끝에서 low로 내린다(demote_uncertain_confidence)"),
    ("flip_scope", "일관성 표지(consistency_flip)는 띄어쓰기 카드에만, 가족 근거는 일관성 카드에만",
     "규범 교정에 켜면 비표준 표기로의 통일을 조장한다(models.Correction 주석)"),
    ("realword_untaken", "실단어 카드의 원문은 다른 카드의 어절과 겹치지 않는다",
     "워커 [7.5] — 이미 다른 레이어가 손댄 어절은 제외(등장 인덱스 보호)"),
    ("user_dict_fixed", "사용자 첨부 사전 카드는 수락 상태다",
     "카드 표시값 고정(source=user_dict · 수락 · auto=False)"),
)


def check_doc(cards: list, text: str) -> list:
    """한 원고의 카드 출력(dict 목록)과 원고 텍스트 → [(불변식 id, 설명 문자열), …]."""
    out = []

    def bad(inv, c, why=""):
        out.append((inv, f"{c.get('source')}/{c.get('confidence')} "
                         f"{c.get('original', '')[:40]!r} → {c.get('corrected', '')[:40]!r}"
                         + (f" · {why}" if why else "")))

    origs = Counter(c.get("original", "") for c in cards)
    taken = set()
    for c in cards:
        if c.get("source") not in ("realword", "ai_polish"):
            taken.update(_HANGUL.findall(c.get("original", "")))

    for c in cards:
        o, k = c.get("original", ""), c.get("corrected", "")
        src, conf = c.get("source", ""), c.get("confidence", "")
        why = (c.get("reason") or "").lstrip()
        if o not in text:
            bad("anchor", c)
        if why.startswith("[검수]") and conf != "low":
            bad("review_low", c)
        if src in ("realword", "dict_flag") and conf != "low":
            bad("fixed_low", c)
        if (src == "dict_flag") != (o == k):
            bad("flag_shape", c)
        if ((src == "spacing" or c.get("consistency_flip") or c.get("junction_group"))
                and _letters(o) != _letters(k)):
            bad("space_only", c)
        if origs[o] > 1:
            bad("unique_original", c, f"{origs[o]}장")
        if conf not in ("high", "low"):
            bad("confidence_domain", c)
        if c.get("consistency_flip") and src != "spacing":
            bad("flip_scope", c, "일관성 표지가 띄어쓰기 카드 밖")
        if c.get("spacing_family") and not c.get("consistency_flip"):
            bad("flip_scope", c, "가족 근거가 일관성 카드 밖")
        if src == "realword" and o in taken:
            bad("realword_untaken", c)
        if src == "user_dict" and c.get("status") != "accepted":
            bad("user_dict_fixed", c, f"상태 {c.get('status')}")
    return out


def selftest() -> list:
    """검사기가 살아 있는가 — 불변식마다 일부러 어긴 카드를 넣어 **반드시 걸리는지** 본다.

    코퍼스에서 위반 0이 계속되면 판정식이 죽어 있어도 모른다(이 저장소의 '죽어 있던 정상 복합어
    판정'과 같은 부류). 걸리지 않은 불변식 id 목록을 돌려준다(빈 목록 = 정상).
    """
    text = "원고에 있는 낱말 띄어쓰기 실단어 오타"
    ok = {"original": "낱말", "corrected": "낱 말", "source": "spacing", "confidence": "low",
          "reason": "", "status": "pending", "consistency_flip": False, "spacing_family": [],
          "junction_group": ""}
    cases = {
        "anchor": [dict(ok, original="원고에 없는 말", corrected="원고에 없는 말!", source="ai_typo")],
        "review_low": [dict(ok, reason="[검수] 확인", confidence="high")],
        "fixed_low": [dict(ok, original="오타", corrected="오자", source="realword", confidence="high")],
        "flag_shape": [dict(ok, original="낱말", corrected="낱말", source="ai_typo")],
        "space_only": [dict(ok, corrected="낱알")],
        "unique_original": [dict(ok), dict(ok, corrected="낱 말 ")],
        "confidence_domain": [dict(ok, confidence="medium")],
        "flip_scope": [dict(ok, original="낱말", corrected="낱알", source="dict", consistency_flip=True)],
        "realword_untaken": [dict(ok, original="실단어", corrected="실 단어"),
                             dict(ok, original="실단어", corrected="실단아", source="realword")],
        "user_dict_fixed": [dict(ok, source="user_dict")],
    }
    dead = []
    for inv, cards in cases.items():
        if inv not in {i for i, _m in check_doc(cards, text)}:
            dead.append(inv)
    clean = [dict(ok), dict(ok, original="오타", corrected="오타", source="dict_flag")]
    if check_doc(clean, text):
        dead.append("clean(정상 카드가 걸림)")
    return dead + [i for i, _d, _r in INVARIANTS if i not in cases]


def check_run(docs: dict) -> dict:
    """{태그: (카드 목록, 원고 텍스트)} → {태그: 위반 목록}(위반 없는 원고는 빠진다)."""
    res = {}
    for tag, (cards, text) in sorted(docs.items()):
        v = check_doc(cards, text)
        if v:
            res[tag] = v
    return res


def summarize(res: dict, show: int = 20) -> list:
    """위반을 사람이 읽을 줄로 — 불변식별 건수 + 앞쪽 사례."""
    by = Counter(inv for v in res.values() for inv, _m in v)
    desc = {i: d for i, d, _r in INVARIANTS}
    lines = [f"{inv} — {desc.get(inv, '')}: {n}건" for inv, n in by.most_common()]
    shown = 0
    for tag, v in res.items():
        for inv, msg in v:
            if shown >= show:
                break
            lines.append(f"   {tag} [{inv}] {msg}")
            shown += 1
    return lines
