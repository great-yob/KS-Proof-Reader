"""
core/consistency_family.py — 복합명사 계열 표기 정합(꼬리 축 + 머리 축)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`find_compound_spacing_consistency`는 **낱말 하나하나**를 저자의 문서 내 다수 표기로
통일한다. 그런데 그 판정은 낱말별로 독립이라, **같은 부류의 복합명사가 서로 반대**로
끝날 수 있다(사용자 보고 2026-08-03):

    수익모델  2:1 → '수익 모델'(띄움)
    사업모델  2:1 → '사업모델'(붙임)

둘 다 수락하면 교정본에 '수익 모델'과 '사업모델'이 공존한다 — 교정 신뢰도가 떨어진다.

**계열은 두 축으로 묶인다**(머리 축은 2026-08-03 사용자 지적으로 추가):

    꼬리 축 `…xx`  — 마지막 성분이 같다: 수익 모델 · 사업모델 · 복합모델   (가른 이음매 = 앞)
    머리 축 `xx…`  — 첫 성분이 같다:  기술 개발 · 기술격차 · 기술 역량     (가른 이음매 = 뒤)

⚠ 두 축은 **대체재가 아니라 보완재**다. 사용자 지적 파일 1종(26.8만 자) 실측 —
  꼬리 축 충돌 24계열(낱말 88) / 머리 축 충돌 26계열(낱말 120), 겹치는 낱말은 39뿐이라
  **81개 낱말이 머리 축에서만 갈렸다**('인재*' 16종 중 인재정책·인재지원사업만 붙임,
  '연구*' 12종 중 연구개발·연구노트·연구역량·연구환경만 붙임). 꼬리 축만 보던 동안
  이것들은 화면에 **한 번도 안 나왔다**.

⚠ **자동 통일은 답이 아니다**(실측으로 닫힌 결론 — 두 축 모두 해당). 같은 데이터에
    산학협력 51:10 붙임(산학협력법 용어)  ↔  국제 협력 3:1 띄움
    개인정보 50:2  붙임(개인정보 보호법)   ↔  결제 정보 1:1
같은 **정당한 구분**이 섞여 있다. 가족을 강제로 통일하면 '산학 협력'·'개인 정보'가 된다.
머리 축도 똑같다 — 위 실측의 '산학연계·산학프로그램(띄움) ↔ 산학협력(붙임)'이 그 예다.
사전도 못 가른다 — '복합모델·사업모델·수익모델'은 셋 다 등재어인데 방향이 갈린다
(우리말샘 전문용어가 웬만한 복합어를 다 갖고 있어 판별력이 없다).
같은 결론이 이미 [core/junction_pass.py](junction_pass.py) 헤더에도 있다(출산 전후 44:4 ↔ 출산전후휴가 3:1).

→ 그래서 이 모듈은 **판정하지 않는다**. 수락 결과에서 방향이 갈리는 가족을 찾아
   **사람이 한 번에 결정할 수 있게 묶어 줄 뿐**이다(검수 패널의 '표기 일관성 제안' 단계).

⚠⚠ **한 낱말은 두 계열에 동시에 속한다** — '지원정책'은 꼬리 계열 `…정책`이자 머리 계열
  `지원…`이다. 두 계열을 **반대 방향으로** 수락하면 나중 것이 앞의 것을 되돌려, 사용자가
  화면에서 고른 것과 다른 결과가 조용히 나간다. 어느 쪽이 옳은지는 이 모듈이 알 수 없다
  (그걸 알면 애초에 자동 통일이 됐다) → `resolve_jobs()`가 그런 낱말을 **아예 손대지 않고**
  `contested`로 돌려주고, 패널이 카드에 표시하고 호출부가 로그로 알린다. 임의로 한쪽을
  택하는 것(먼저 온 계열 우선 등)은 **금지** — 조용한 오통일이 된다.

실측(실원고 5종 774K자, 꼬리 축): 일관성 카드 605장 · 계열 392개 ·
**방향 충돌 가족 42개**(층 1 '가족 우선 타이브레이크' 적용 후. 적용 전엔 57개).

⚠ 이 모듈은 GUI를 모른다(core 규약). 입력은 검수 패널이 들고 있는 교정 dict 목록이고,
  출력은 순수 데이터다 — 실제 뒤집기는 패널의 `_apply_flip`이 수행한다.
"""

TAIL = "tail"        # 꼬리 축 — 마지막 성분이 같은 계열('…모델')
LEAD = "lead"        # 머리 축 — 첫 성분이 같은 계열('기술…')
AXES = (TAIL, LEAD)


def _spaced_form(orig: str, corr: str) -> str:
    """일관성 교정 쌍에서 **띄어 쓴 쪽** 표면형(둘 중 공백이 있는 것)."""
    return orig if " " in orig else corr


def head_of(orig: str, corr: str) -> str:
    """이 교정이 속한 **꼬리 계열** — 복합명사의 마지막 성분('수익 모델' → '모델').

    ⚠ 이름의 '머리(head)'는 국어 복합명사의 **핵**, 즉 마지막 성분이라는 뜻이다.
      문자열 위치로는 **꼬리**다(`…모델`). `Correction.spacing_family[0]`이 이 값이라
      이름을 유지한다 — 새 코드는 `key_of(o, c, TAIL)`을 쓸 것.
    """
    return _spaced_form(orig, corr).split(" ")[-1]


def lead_of(orig: str, corr: str) -> str:
    """이 교정이 속한 **머리 계열** — 복합명사의 첫 성분('기술 개발' → '기술')."""
    return _spaced_form(orig, corr).split(" ")[0]


def key_of(orig: str, corr: str, axis: str = TAIL) -> str:
    """`axis` 계열의 키 낱말."""
    return lead_of(orig, corr) if axis == LEAD else head_of(orig, corr)


def family_label(family: dict) -> str:
    """계열을 사람에게 보여 줄 표기 — 꼬리 `…정책` / 머리 `지원…`.

    ⚠ 같은 낱말이 두 축의 키가 될 수 있다('지원정책'의 `…정책`과 '정부지원'의 `지원…`).
      축 표시 없이 키만 쓰면 두 계열이 화면에서 구분되지 않는다.
    """
    key = family.get("head", "")
    return f"{key}…" if family.get("axis") == LEAD else f"…{key}"


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


def _collect_bases(corrections: list) -> dict:
    """일관성 카드를 **낱말(base) 단위**로 접고 최종 방향을 매긴다.

    ⚠ **단위는 카드가 아니라 낱말이다.** 한 낱말에 카드가 둘일 수 있기 때문이다 —
      검수 중 '반대 표기로 통일'을 고르면 원 카드는 거절되고 **반대 교정이 합성·수락**된다
      (review_panel._apply_flip). 카드 단위로 세면 그 거절된 원 카드가 '혼재'로 잘못 잡혀,
      방금 정리한 낱말이 다시 충돌로 올라온다.

    ⚠ **거절 카드를 빼면 안 된다**(사용자 지적 2026-08-03). 거절 = 앱이 아무것도 바꾸지
      않는다 = **문서에 두 표기가 그대로 남는다**. 방향이 틀린 것보다 오히려 나쁜 상태이고,
      사용자가 잘못 거절했을 수도 있다. 그래서 낱말의 최종 상태를 이렇게 본다:
        · 그 낱말의 카드 중 **수락된 것이 있으면** → 그 교정문의 방향으로 확정
        · 하나도 없으면(전부 거절) → **MIXED**(두 방향 다 남음)

    ⚠ 낱말 dict는 **두 축이 공유하는 같은 객체**다(사본이 아니다). '지원정책'은 꼬리 계열
      `…정책`과 머리 계열 `지원…`에 동시에 들어가는데, 사본을 쓰면 한쪽에서 본 상태가
      다른 쪽과 어긋난다.
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
            "head": head_of(o, r),          # 꼬리 축 키(기존 이름 유지)
            "lead": lead_of(o, r),          # 머리 축 키
        })
        m["cards"].append((ci, o, r, c.get("status")))
    for m in bases.values():
        acc = [r for _ci, _o, r, st in m["cards"] if st == "accepted"]
        m["direction"] = direction_of(acc[0]) if acc else MIXED
    return bases


def _dirs_of(members: list, final: dict = None) -> set:
    """계열이 차지하는 방향 집합. MIXED는 **두 방향을 다** 차지한다(그래서 곧 충돌이다)."""
    out = set()
    for m in members:
        d = (final or {}).get(m["base"], m["direction"])
        out |= {"joined", "spaced"} if d == MIXED else {d}
    return out


def analyze(corrections: list) -> list:
    """낱말 2종 이상인 **모든 계열**(꼬리 축 먼저, 각 축 안에서 키 낱말 순).

    반환 원소:
        {"axis": "tail" | "lead",       # 어느 축의 계열인가
         "head": "모델",                 # 키 낱말(축과 함께 봐야 뜻이 통한다 → family_label)
         "split": True,                 # 방향이 갈렸는가(=검수 카드로 낼 계열인가)
         "members": [{"base": "수익모델", "cards": [(ci, original, corrected, status), …],
                      "direction": "spaced" | MIXED,
                      "spaced": "수익 모델", "joined": "수익모델",
                      "n_joined": 1, "n_spaced": 2}, …],
         "proposal": "joined",          # 기본 제안 방향(split일 때만. 사용자가 토글로 뒤집는다)
         "n_joined": 5, "n_spaced": 4}  # 계열 전체 등장 수 합(근거 표시용)

    ⚠ **갈리지 않은 계열도 함께 돌려준다.** 카드로 낼 것은 `split`인 것뿐이지만, 통일이
      그 **밖의 계열을 깨뜨리지 않는지** 검사하려면 갈리지 않은 계열이 필요하다
      (`plan()` — 축이 둘이 되면서 생긴 요구사항). 낱말 dict는 두 축이 **공유**한다.
    """
    bases = _collect_bases(corrections)
    out = []
    for axis in AXES:
        key_field = "lead" if axis == LEAD else "head"
        fam = {}
        for m in bases.values():
            fam.setdefault(m[key_field], []).append(m)
        for key, members in sorted(fam.items()):
            if len(members) < 2:
                continue                   # 계열이 아니다(낱말 하나) — 그 카드에서 이미 결정했다
            members = sorted(members, key=lambda m: m["base"])
            split = len(_dirs_of(members)) > 1
            n_j = sum(m["n_joined"] for m in members)
            n_s = sum(m["n_spaced"] for m in members)
            proposal, strong = None, False
            if split:
                # 기본 제안 = **확정된 낱말의 다수**(빈출 낱말 하나가 계열을 끌고 가지 않게).
                #   확정이 없거나 동수면 계열 전체 등장 수로 가른다.
                n_mem_j = sum(1 for m in members if m["direction"] == "joined")
                n_mem_s = sum(1 for m in members if m["direction"] == "spaced")
                strong = n_mem_j != n_mem_s          # 근거가 뚜렷 → align_proposals가 안 건드림
                if strong:
                    proposal = "joined" if n_mem_j > n_mem_s else "spaced"
                else:
                    proposal = "joined" if n_j > n_s else "spaced"
            out.append({"axis": axis, "head": key, "key": (axis, key),
                        "members": members, "split": split, "strong": strong,
                        "proposal": proposal, "n_joined": n_j, "n_spaced": n_s})
    return out


def align_proposals(families: list, all_families: list = None) -> int:
    """**두 축의 기본 제안을 함께 푼다** — 부딪히거나 남의 계열을 깨는 기본값을 미리 없앤다.

    한 낱말이 꼬리·머리 두 계열에 속하는데 두 계열의 기본 제안이 반대면, 사용자가 둘 다
    그대로 수락했을 때 그 낱말이 부딪힌다(→ 한쪽이 잠기고 다른 쪽 계열은 갈린 채 남는다).
    계열마다 **따로** 방향을 정하기 때문에 생기는 일이라, 기본값 단계에서 맞춰 둔다.

    비용 = ① 이웃 계열과 방향이 어긋나는 수 + ② 이 방향이면 **갈리게 되는 멀쩡한 계열** 수
    (②는 `all_families`를 줄 때만. 2차 재검토는 보호 가드가 풀려 있어 ②가 실제 피해가 된다 —
     기본값이 남의 계열을 깨는 쪽으로 잡혀 있으면 '전부 수락'이 갈림을 옮기기만 한다).

    ⚠ **자기 근거가 뚜렷한 계열(strong)은 절대 뒤집지 않는다** — '낱말 자신의 다수 >
      계열의 다수 > 규범'이라는 기존 계층에 한 층 더 얹는 것뿐이다. 뒤집는 건 낱말 수가
      동수라 등장 수로 겨우 가른 계열(weak)뿐이고, 그것도 **비용이 줄어들 때만**.
      전부 한 방향으로 모는 '강제 통일'이 아니다(그건 실측으로 닫힌 금지 사항).

    결정성: 계열 키 순으로 훑고, 한 번 훑어 아무것도 안 바뀌면 멈춘다(최대 `_ALIGN_ROUNDS`).
    반환: 뒤집은 계열 수.
    """
    fams = [f for f in families if f.get("split")]
    if len(fams) < 2:
        return 0
    by_base = {}
    for f in fams:
        for m in f["members"]:
            by_base.setdefault(m["base"], []).append(f)
    intact_by_base = {}
    for f in (all_families or ()):
        if f.get("split"):
            continue
        d0 = next(iter(_dirs_of(f["members"])))
        for m in f["members"]:
            intact_by_base.setdefault(m["base"], []).append((f, d0))

    def cost(f, d):
        n, hit = 0, set()
        for m in f["members"]:
            b = m["base"]
            for g in by_base.get(b, ()):
                if g is not f and (g.get("choice") or g["proposal"]) != d:
                    n += 1
            if m["direction"] != d:                  # 이 낱말이 실제로 움직일 때만
                for g, d0 in intact_by_base.get(b, ()):
                    if d0 != d and id(g) not in hit:
                        hit.add(id(g))
                        n += 1
        return n

    order = sorted(range(len(fams)), key=lambda i: fams[i]["key"])
    flipped = 0
    for _ in range(_ALIGN_ROUNDS):
        changed = 0
        for i in order:
            f = fams[i]
            if f.get("strong"):
                continue
            cur = f.get("choice") or f["proposal"]
            opp = "spaced" if cur == "joined" else "joined"
            if cost(f, opp) < cost(f, cur):
                f["proposal"] = opp
                if "choice" in f:
                    f["choice"] = opp
                changed += 1
        flipped += changed
        if not changed:
            break
    return flipped


_ALIGN_ROUNDS = 4    # 고정점까지 훑는 최대 회차(실측 2회차에 수렴 — 진동 방지용 상한)


def find_conflicts(corrections: list) -> list:
    """**방향이 갈린** 계열만(=검수 카드로 낼 것). `analyze()`의 얇은 필터."""
    return [f for f in analyze(corrections) if f["split"]]


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


def demands(families: list) -> dict:
    """수락된 계열들이 낱말마다 요구하는 방향 — `{base: {direction: [family, …]}}`.

    두 축이 같은 낱말을 각자 끌어당기므로, 카드 화면도 적용도 이 표를 봐야 한다.
    """
    out = {}
    for f in families:
        if f.get("status") != "accepted":
            continue
        for m in f["members"]:
            out.setdefault(m["base"], {}).setdefault(f["choice"], []).append(f)
    return out


def plan(families: list, all_families: list = None, overrides: dict = None,
         allow_break: bool = False) -> dict:
    """수락된 계열들의 결정을 **낱말 단위로 합쳐** 실행 계획을 만든다.

    반환 `{"want": {base: direction}, "owner": {base: family},
           "locked": {base: (owner, [뒤로 밀린 계열, …])},
           "blocked": {base: family}, "jobs": [action_for 결과, …]}`

    두 축이 생기면서 **한 낱말이 두 계열에 속하게** 됐고, 그래서 계열마다 따로 실행하면
    안 되는 이유가 둘이다:

    ① **반대 요구**(`locked`) — 꼬리 계열은 붙임, 머리 계열은 띄어쓰기를 요구한다. 어느
       쪽이 옳은지 앱은 알 수 없다(알면 애초에 자동 통일이 된다). → **사용자가 먼저
       결정한 계열이 그 낱말을 가져가고**(`f["seq"]` 오름차순. `overrides`가 있으면 그게
       우선), 나머지 계열의 카드에는 '잠김'으로 **보이게** 한다. 눌러서 되돌릴 수 있다.
       ⚠ 앱이 몰래 한쪽을 고르는 것과는 다르다 — 순서는 사용자가 만든 것이고, 화면에
         누가 가져갔는지 나오고, 되돌릴 수 있다. **이 세 가지가 빠지면 조용한 오통일이다.**
       (2026-08-04 이전 구현은 양쪽 다 손대지 않고 로그로만 알렸다 — 사용자 결정으로
        '두 단계 검토'가 들어오면서, 1단계에서 최대한 정리하는 이 규칙으로 바뀌었다.)

    ② ★**멀쩡한 계열 깨뜨리기**(`blocked`) — 이건 실측으로 발견했다(사용자 지적 파일):
       두 축을 그냥 나란히 놓고 기본 제안대로 전부 통일하면, 한 축의 통일이 **다른 축에서
       이미 한 방향으로 맞아 있던 계열**을 깨뜨려 **새로 갈린 계열 16건**이 생겼다
       (예: '현장…' 계열을 붙임으로 통일 → '현장기반'이 붙임이 되면서 '…기반' 계열의
       공급 기반·관계 기반·기술 기반·협력 기반 사이에 혼자 붙임으로 남는다).
       결과적으로 총 충돌이 28 → 30으로 **늘었다** — 축을 추가한 의미가 사라진다.
       그 계열은 갈리지 않았으니 카드로 나오지도 않아 사용자는 볼 기회조차 없다.
       → **불변식: 이 단계는 사용자 모르게 새 혼재를 만들지 않는다.** 멀쩡한 계열을
         깨뜨리는 이동은 되돌리고(`blocked`) 호출부가 알린다. 되돌림은 고정점까지 반복한다
         (되돌림이 또 다른 계열을 깨뜨릴 수 있다). 각 회차마다 blocked가 최소 1개 늘어나
         종료한다.

       ★`allow_break=True`(2차 재검토)면 되돌리지 **않는다**. 1차 뒤 남은 갈림은 대부분
       '다른 축의 계열이 그 낱말을 붙잡고 있어서'인데, 가드를 그대로 두면 2차에서 **아무것도
       못 움직인다**(실측: 3원고 모두 2차 조정 0~1건). 두 축을 동시에 만족시킬 수 없는
       자리라 어디를 갈라 둘지는 **사람만 정할 수 있다** → 2차에서는 `breaks`(이 결정으로
       갈리게 되는 계열)를 카드에 **보여 주고** 사용자가 고르게 한다. '모르게'가 빠질 뿐
       불변식은 유지된다.

    ⚠ `all_families`는 `analyze()`의 **전체 목록**(갈리지 않은 계열 포함)이어야 한다.
      `families`(=카드로 낸 갈린 계열)만 주면 ②를 검사할 수 없다 — 깨뜨릴 대상이 바로
      그 '갈리지 않은 계열'이기 때문이다.
    """
    fams_all = all_families if all_families is not None else list(families)
    members = {m["base"]: m for f in fams_all for m in f["members"]}
    for f in families:                       # 카드로만 있고 전체 목록에 없을 일은 없지만 방어
        for m in f["members"]:
            members.setdefault(m["base"], m)

    # ① 낱말마다 **주인 계열**을 정한다 — override > 사용자가 먼저 수락한 순서(seq) >
    #    계열 키(seq가 없을 때의 결정성 보장).
    ovr = overrides or {}
    owner, locked = {}, {}
    for b, dirs in demands(families).items():
        claims = [f for fs in dirs.values() for f in fs]
        pick = next((f for f in claims if f.get("key") == ovr.get(b)), None)
        if pick is None:
            pick = min(claims, key=lambda f: (f.get("seq", 1 << 30), str(f.get("key"))))
        owner[b] = pick
        others = [f for f in claims if f is not pick and f["choice"] != pick["choice"]]
        if others:
            locked[b] = (pick, others)
    want = {b: f["choice"] for b, f in owner.items()}
    intact = [(f, next(iter(_dirs_of(f["members"]))))
              for f in fams_all if not f["split"]]

    def _final(blocked):
        return {b: (m["direction"] if b in blocked or b not in want else want[b])
                for b, m in members.items()}

    def _breaks(blocked):
        """지금 계획대로면 **갈리게 되는** 멀쩡한 계열 — {base: 그 계열}."""
        final, out = _final(blocked), {}
        for f, d0 in intact:
            if len(_dirs_of(f["members"], final)) < 2:
                continue
            for m in f["members"]:
                b = m["base"]
                if b in want and b not in blocked and final[b] != d0:
                    out.setdefault(b, f)
        return out

    blocked, settled = {}, set()
    if not allow_break:
        while True:
            final = _final(blocked)
            hit = None
            for i, (f, d0) in enumerate(intact):
                if i not in settled and len(_dirs_of(f["members"], final)) > 1:
                    hit = (i, f, d0)
                    break
            if hit is None:
                break
            i, f, d0 = hit
            settled.add(i)                   # 이번 회차로 이 계열은 다뤘다(무한 루프 차단)
            for m in f["members"]:           # 원래 방향에서 벗어난 이동만 되돌린다
                b = m["base"]
                if b in want and b not in blocked and final[b] != d0:
                    blocked[b] = f

    jobs = []
    for b in sorted(want):
        if b in blocked:
            continue
        act = action_for(members[b], want[b])
        if act:
            jobs.append(act)
    return {"want": want, "owner": owner, "locked": locked,
            "blocked": blocked, "breaks": _breaks(blocked), "jobs": jobs}


def resolve_jobs(families: list, all_families: list = None, overrides: dict = None,
                 allow_break: bool = False):
    """`plan()`의 얇은 래퍼 — `(jobs, 계열 보호로 손대지 않은 낱말)`."""
    p = plan(families, all_families, overrides, allow_break)
    return p["jobs"], sorted(p["blocked"])


def current_label(member: dict) -> str:
    """카드에 보여 줄 '지금 결정대로라면 문서에 남는 표기'."""
    if member["direction"] == MIXED:
        return f"{member['spaced']} · {member['joined']} (혼재)"
    return member["spaced"] if member["direction"] == "spaced" else member["joined"]


def unified_form(member: dict, direction: str) -> str:
    """낱말을 `direction`으로 맞췄을 때의 표면형."""
    return member["spaced"] if direction == "spaced" else member["joined"]
