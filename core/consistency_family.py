"""
core/consistency_family.py — 복합명사 '머리낱말 가족' 표기 정합
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`find_compound_spacing_consistency`는 **낱말 하나하나**를 저자의 문서 내 다수 표기로
통일한다. 그런데 그 판정은 낱말별로 독립이라, **같은 부류의 복합명사가 서로 반대**로
끝날 수 있다(사용자 보고 2026-08-03):

    수익모델  2:1 → '수익 모델'(띄움)
    사업모델  2:1 → '사업모델'(붙임)

둘 다 수락하면 교정본에 '수익 모델'과 '사업모델'이 공존한다 — 교정 신뢰도가 떨어진다.

⚠ **자동 통일은 답이 아니다**(실측으로 닫힌 결론). 같은 데이터에
    산학협력 51:10 붙임(산학협력법 용어)  ↔  국제 협력 3:1 띄움
    개인정보 50:2  붙임(개인정보 보호법)   ↔  결제 정보 1:1
같은 **정당한 구분**이 섞여 있다. 가족을 강제로 통일하면 '산학 협력'·'개인 정보'가 된다.
사전도 못 가른다 — '복합모델·사업모델·수익모델'은 셋 다 등재어인데 방향이 갈린다
(우리말샘 전문용어가 웬만한 복합어를 다 갖고 있어 판별력이 없다).
같은 결론이 이미 [core/junction_pass.py](junction_pass.py) 헤더에도 있다(출산 전후 44:4 ↔ 출산전후휴가 3:1).

→ 그래서 이 모듈은 **판정하지 않는다**. 수락 결과에서 방향이 갈리는 가족을 찾아
   **사람이 한 번에 결정할 수 있게 묶어 줄 뿐**이다(검수 패널의 '표기 일관성 제안' 단계).

실측(실원고 5종 774K자): 일관성 카드 605장 · 머리낱말 가족 392개 ·
**방향 충돌 가족 42개**(층 1 '가족 우선 타이브레이크' 적용 후. 적용 전엔 57개).

⚠ 이 모듈은 GUI를 모른다(core 규약). 입력은 검수 패널이 들고 있는 교정 dict 목록이고,
  출력은 순수 데이터다 — 실제 뒤집기는 패널의 `_apply_flip`이 수행한다.
"""


def _spaced_form(orig: str, corr: str) -> str:
    """일관성 교정 쌍에서 **띄어 쓴 쪽** 표면형(둘 중 공백이 있는 것)."""
    return orig if " " in orig else corr


def head_of(orig: str, corr: str) -> str:
    """이 교정이 속한 **가족(머리낱말)** — 복합명사의 마지막 성분('수익 모델' → '모델')."""
    return _spaced_form(orig, corr).split(" ")[-1]


def evidence_of(minority: str, majority: str, n_min: int, n_maj: int) -> tuple:
    """`find_compound_spacing_consistency`의 (소수/다수) 축을 **(붙임/띄어쓴)** 축으로 바꾼다.

    `Correction.spacing_family` 값 = (머리낱말, 붙임 등장 수, 띄어쓴 등장 수).
    ⚠ 이 변환을 호출부에서 직접 쓰지 말 것 — 소수/다수와 붙임/띄어쓴은 **직교**라
      한 번 뒤집으면 기본 제안 방향이 통째로 반대가 된다(구현 중 실제로 겪었다).
    """
    spaced_is_minority = " " in minority
    return (head_of(minority, majority),
            n_maj if spaced_is_minority else n_min,     # 붙임
            n_min if spaced_is_minority else n_maj)     # 띄어쓴


def direction_of(corrected: str) -> str:
    """이 교정을 수락했을 때 문서가 갖게 되는 방향 — 'spaced'(띄움) / 'joined'(붙임)."""
    return "spaced" if " " in corrected else "joined"


def is_family_card(c: dict) -> bool:
    """복합명사 일관성 카드인가? (가족 정합의 대상)

    워커 [7] `find_compound_spacing_consistency` 소비부가 켠 `consistency_flip` +
    **글자 불변(공백만 가감)** 인 것만. 규범표기·맞춤법 카드는 대상이 아니다.
    """
    if not c.get("consistency_flip"):
        return False
    o, r = c.get("original") or "", c.get("corrected") or ""
    if not o or not r or o == r:
        return False
    if o.replace(" ", "") != r.replace(" ", ""):
        return False
    return (" " in o) != (" " in r)      # 한쪽만 공백 = 붙임/띄움 쌍


MIXED = "mixed"      # 그 낱말이 문서에 **두 표기 다** 남는 상태(카드를 거절했을 때)


def find_conflicts(corrections: list) -> list:
    """복합명사 일관성 결정에서 **가족 방향 충돌**을 찾는다.

    ⚠ **단위는 카드가 아니라 낱말(base)이다.** 한 낱말에 카드가 둘일 수 있기 때문이다 —
      검수 중 '반대 표기로 통일'을 고르면 원 카드는 거절되고 **반대 교정이 합성·수락**된다
      (review_panel._apply_flip). 카드 단위로 세면 그 거절된 원 카드가 '혼재'로 잘못 잡혀,
      방금 정리한 낱말이 다시 충돌로 올라온다.

    ⚠ **거절 카드를 빼면 안 된다**(사용자 지적 2026-08-03). 거절 = 앱이 아무것도 바꾸지
      않는다 = **문서에 두 표기가 그대로 남는다**. 방향이 틀린 것보다 오히려 나쁜 상태이고,
      사용자가 잘못 거절했을 수도 있다. 그래서 낱말의 최종 상태를 이렇게 본다:
        · 그 낱말의 카드 중 **수락된 것이 있으면** → 그 교정문의 방향으로 확정
        · 하나도 없으면(전부 거절) → **MIXED**(두 방향 다 남음)
      MIXED는 두 방향을 다 차지하므로, 낱말이 2종 이상인 가족에 하나라도 있으면 충돌이다.

    반환(머리낱말 순, 가족별 1건):
        [{"head": "모델",
          "members": [{"base": "수익모델", "cards": [(ci, corrected, status), …],
                       "direction": "spaced" | MIXED,
                       "spaced": "수익 모델", "joined": "수익모델",
                       "n_joined": 1, "n_spaced": 2}, …],
          "proposal": "joined",          # 기본 제안 방향(사용자가 토글로 뒤집을 수 있다)
          "n_joined": 5, "n_spaced": 4}] # 가족 전체 등장 수 합(근거 표시용)
    """
    bases = {}
    for ci, c in enumerate(corrections):
        if not is_family_card(c):
            continue
        o, r = c["original"], c["corrected"]
        base = r.replace(" ", "")
        sf = c.get("spacing_family") or ()
        m = bases.setdefault(base, {
            "base": base, "cards": [],
            "spaced": o if " " in o else r,
            "joined": (o if " " in o else r).replace(" ", ""),
            "n_joined": int(sf[1]) if len(sf) >= 3 else 0,
            "n_spaced": int(sf[2]) if len(sf) >= 3 else 0,
            "head": head_of(o, r),
        })
        m["cards"].append((ci, o, r, c.get("status")))

    fam = {}
    for m in bases.values():
        acc = [r for _ci, _o, r, st in m["cards"] if st == "accepted"]
        m["direction"] = direction_of(acc[0]) if acc else MIXED
        fam.setdefault(m["head"], []).append(m)

    out = []
    for head, members in fam.items():
        if len(members) < 2:
            continue                       # 계열이 아니다(낱말 하나) — 그 카드에서 이미 결정했다
        dirs = set()
        for m in members:
            dirs |= ({"joined", "spaced"} if m["direction"] == MIXED else {m["direction"]})
        if len(dirs) < 2:
            continue                       # 전부 한 방향으로 확정 = 이미 정합
        # 기본 제안 = **확정된 낱말의 다수**(빈출 낱말 하나가 계열을 끌고 가지 않게).
        #   확정이 없거나 동수면 계열 전체 등장 수로 가른다.
        n_mem_j = sum(1 for m in members if m["direction"] == "joined")
        n_mem_s = sum(1 for m in members if m["direction"] == "spaced")
        n_j = sum(m["n_joined"] for m in members)
        n_s = sum(m["n_spaced"] for m in members)
        if n_mem_j != n_mem_s:
            proposal = "joined" if n_mem_j > n_mem_s else "spaced"
        else:
            proposal = "joined" if n_j > n_s else "spaced"
        members.sort(key=lambda m: m["base"])
        out.append({"head": head, "members": members, "proposal": proposal,
                    "n_joined": n_j, "n_spaced": n_s})
    out.sort(key=lambda f: f["head"])
    return out


def action_for(member: dict, direction: str):
    """이 낱말을 `direction`으로 확정하려면 무엇을 해야 하는가?

    반환: None(이미 그 방향) 또는 (동작, ci, original, corrected)
      · "accept" — **교정문이 그 방향인** 카드를 수락한다(거절해 둔 것을 되살림).
      · "flip"   — **원문이 그 방향인** 카드를 거절하고 반대 교정을 수락한다(_apply_flip).
    ⚠ (original, corrected)는 **그 카드에 실제로 들어 있는 쌍을 그대로** 돌려준다.
      표면형을 다시 조립해 넘기면 방향이 뒤집혀도 아무 오류 없이 조용히 반대로 통일된다
      (구현 중 실제로 겪었다 — 적용 후에도 계열이 그대로 갈려 있었다).
    """
    if member["direction"] == direction:
        return None
    for ci, o, r, _st in member["cards"]:
        if direction_of(r) == direction:            # 이 카드를 수락하면 그 방향이 된다
            return ("accept", ci, o, r)
    for ci, o, r, _st in member["cards"]:
        if direction_of(o) == direction:            # 이 카드를 뒤집으면 그 방향이 된다
            return ("flip", ci, o, r)
    return None


def targets_for(family: dict, direction: str) -> list:
    """`family`를 `direction`으로 통일할 때 **손대야 하는 낱말** 목록."""
    return [m for m in family["members"] if action_for(m, direction) is not None]


def current_label(member: dict) -> str:
    """카드에 보여 줄 '지금 결정대로라면 문서에 남는 표기'."""
    if member["direction"] == MIXED:
        return f"{member['spaced']} · {member['joined']} (혼재)"
    return member["spaced"] if member["direction"] == "spaced" else member["joined"]


def unified_form(member: dict, direction: str) -> str:
    """낱말을 `direction`으로 맞췄을 때의 표면형."""
    return member["spaced"] if direction == "spaced" else member["joined"]
