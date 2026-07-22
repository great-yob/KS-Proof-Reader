"""
core/consistency_pass.py — 문서 전체 일관성 강화 후처리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI 청크별 호출의 비결정성을 보정한다.

2가지 변형을 자동 탐지·통일:
  1. 조사 변형 (Case A)    — "키메시지를" 결정 시 "키메시지가", "키메시지에"도 동일 처리
  2. 부분 매칭 전파 (Case 4) — "메세지"→"메시지" 결정 시 "키메세지"→"키메시지" 자동 전파
                              (후보가 사전 표제어이면 가드로 차단)

  · 1은 high confidence (조사 차이만)
  · 2는 low confidence (사용자 검토 권장)

  · 윤문(ai_polish) 항목은 문장 단위라 후처리 대상에서 제외

⚠ 과거의 'Case B'(편집거리 1 통째 치환)는 제거됨. canon의 목표어를 "1글자
  닮은" 무관한 단어에 통째로 덮어써 치명적 오교정("겪고"→"묻고", "고독사"→
  "고지서")을 냈고, 사전(표제어만 등재, 활용형 없음)으로도 막을 수 없었다.
  본래 의도였던 표기 혼재 통일은 Case 4가 이미 처리한다. 자세한 사유는
  enforce_consistency() 내부 주석 참조.
"""

import re
import unicodedata
from collections import OrderedDict
from dataclasses import replace
from functools import lru_cache

from .models import Correction
from .josa_rules import reconcile_josa


# 한국어 어절 끝 조사 — 격조사·보조사·접속조사·서술격조사(이다) 활용형·인용·복합조사.
# 어절에서 base(체언)를 얻기 위해 끝 조사를 제거한다.
#   · `$` 앵커 + 최좌단 매칭이라 같은 끝에서 가장 긴 조사가 자동 선택된다(아래 순서는
#     가독성용이지 정확성을 좌우하지 않는다 — "에서의"가 "에"보다 뒤에 와도 안전).
#   · 서술격조사 '이다'의 활용형(인·인데·이야·이라고…)을 포함한다. 과거엔 '인'을 조사로
#     인식하지 못해 '키메세지인'이 조사 변형(Case A, 高신뢰)이 아닌 저신뢰 부분매칭(Case 4)
#     으로 빠졌다. 관형형 '인'(예: "메시지인 것")까지 base로 환원한다.
#   · ⚠ 단음절 조사('인'·'들'·'요'·'가' 등)는 등재 명사의 끝음절('외국인'·'디자인'·'전문가')
#     과 겹쳐 과제거 위험이 있다. Case A는 사전 표제어 가드(known_surfaces)로, Case 4는
#     known_bases 가드로 이를 차단한다(사전 없으면 기존 동작).
#   · 동사 어미와 모호한 조사('하고'·'고'·'다'·'라'·'치고'·'따라' 등 bare형)는 '생각하고'
#     같은 활용형을 오분해하므로 의도적으로 제외했다(서술격 '이X' 형태만 채택).
_JOSA_RE = re.compile(
    r"("
    # ── 복합 격조사 (긴 표현 먼저) ──
    r"으로부터|로부터|으로서|로서|으로써|로써|으로의|로의|"
    r"에게서|에게로|에게|에서의|에서|에다가|에다|"
    r"한테서|한테|께서|께로|께|와의|과의|"
    # ── 인용·서술 '(이)라' 계열 ──
    r"이라든가|라든가|이라든지|라든지|이라야|라야|이래야|"
    r"이라는|이라고|이라며|이라면|이라서|이라도|"
    r"라는|라고|라며|라면|라서|라도|"
    # ── 서술격조사 '이다' 활용 + 관형형 '인' ──
    r"이야말로|야말로|이지만|이든지|이든가|이든|이나마|이거나|"
    r"이며|이고|이면|이나|이다|이라|이야|이오|이여|이시여|"
    r"인데|인지|인가|인들|인즉|인|"
    # ── 격조사 ──
    r"을|를|이|가|은|는|의|에|으로|로|와|과|"
    # ── 보조사 ──
    r"까지|부터|조차|마저|마다|만큼|밖에|뿐만|뿐|"
    r"처럼|같이|보다|은커녕|는커녕|커녕|깨나|들|"
    r"나마|든지|든가|든|야|나|도|만|요|란|이랑|랑"
    r")$"
)


@lru_cache(maxsize=50000)
def _strip_josa(word: str) -> str:
    """어절 끝 조사 제거 — 단어의 base form 추출.

    1순위 — **형태소 분석(kiwipiepy)**: 끝의 조사(J*)·계사(VCP/VCN+어미)만 정확히
            절단한다. 정규식이 못 가르는 단음절 조사/명사 끝음절 모호성을 해소:
            '외국인'·'국가'는 명사로 보존하고 '키메세지인'·'조사가'만 조사를 떼며,
            '키 메시지를'의 내부 공백도 보존한다(앱에서 kiwipiepy는 탐지·검증에 이미
            상시 동작 — 추가 비용은 lru_cache로 흡수).
    2순위 — **_JOSA_RE 정규식 폴백**: kiwipiepy 미설치/분석 실패 시. 계사 '이다' 활용형
            (인·인데…)까지 보강돼 있으나 단음절 조사 과제거는 호출 측 사전 가드로 방어.

    대량 어절을 반복 조회하므로 lru_cache로 형태소 분석 비용을 1회로 줄인다(순수 함수).
    """
    try:
        from core import morph as _morph
        if _morph.available():
            base = _morph.strip_josa(word)
            if base is not None:
                return base
    except Exception:
        pass
    return _JOSA_RE.sub("", word).strip()


def _canonical_base_correction(canon: Correction) -> tuple[str, str]:
    """canon의 (base_original, base_corrected)를 추출.

    3b 수정: original/corrected가 서로 다른 조사로 끝나는 경우(예: 을/를 교정)
    이전 로직은 corrected의 strip에 실패해 잘못된 base를 반환하고,
    "애니메이션" → "애니메이션을" 같은 황당한 변형 전파가 발생했음.
    이제 **양쪽 모두 _strip_josa를 적용**해 정확한 base를 얻는다.

    예시:
      ("키메시지를", "키 메시지를")   → ("키메시지",   "키 메시지")
      ("애니메이션를", "애니메이션을") → ("애니메이션", "애니메이션")  ← 조사만 다른 fix
      ("케릭터", "캐릭터")             → ("케릭터",     "캐릭터")

    ⚠ 견고화(2026-07-01): corrected가 original과 **같은 조사**로 끝나면(=base만 바뀐 교정)
      그 조사를 corrected에서 직접 떼어 base를 얻는다. _strip_josa(형태소/정규식)가 **공백이
      섞인 재띄어쓰기 교정**('웹 빌더라는')이나 복합 인용조사('라는')에서 절단에 실패해 base에
      조사가 남는 것을 막는다. 실패 시 base_corrected='웹 빌더라는'이 되어 '웹빌더를'이
      '웹 빌더라는을'(라는 잔존 + 조사 중복)로 오교정됐다(사용자 보고). 조사가 바뀐 교정
      (애니메이션를→애니메이션을)은 corrected가 original 조사로 안 끝나 이 경로를 안 타 무회귀.
    """
    base_o = _strip_josa(canon.original)
    base_c = _strip_josa(canon.corrected)
    josa_o = canon.original[len(base_o):]
    if josa_o and canon.corrected.endswith(josa_o):
        cand = canon.corrected[:-len(josa_o)].rstrip()
        if len(cand.replace(" ", "")) >= 2:
            base_c = cand
    return base_o, base_c


# 시각적으로 안 보이거나 정상 텍스트 흐름을 방해하는 코드포인트.
# 한글 자모 채움 문자(U+115F, U+1160, U+3164, U+FFA0)도 포함.
_INVISIBLE_RE = re.compile(
    "["
    "­͏؜ᅟᅠ឴឵᠎"
    "​-‏‪-‮"
    "⁠-⁯"
    "ㅤ︀-️﻿ﾠ"
    "]"
)


def _nfc(s: str) -> str:
    """NFC 정규화 + 불가시 문자 제거.

    NFD/NFC 혼재 + ZWSP·BOM 같은 시각적 무영향 코드포인트가 섞이면
    "원문 == 교정"이 시각상 같아 보이지만 코드포인트로 다르게 비교됨.
    이 함수가 양쪽을 진짜로 같은 형태로 만들어준다.
    """
    if not s:
        return s
    s = unicodedata.normalize("NFC", s)
    s = _INVISIBLE_RE.sub("", s)
    return s.strip()


def _load_dict_module():
    """nikl_dict(표준국어대사전 로컬 SQLite)를 지연 임포트.

    core/는 GUI-agnostic이지만 nikl_dict는 PySide6 의존이 없는 순수
    sqlite 모듈이라 import 규칙에 위배되지 않는다. 루트 경로가 sys.path에
    없을 수 있어 1차 실패 시 프로젝트 루트를 추가해 재시도한다.

    반환: nikl_dict 모듈 또는 None(임포트 실패 시).
    """
    try:
        import nikl_dict as nd
        return nd
    except Exception:
        pass
    try:
        import os
        import sys
        import importlib
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        return importlib.import_module("nikl_dict")
    except Exception:
        return None


def _build_known_words(doc_words: set):
    """문서 어절 중 사전 등재 어휘를 (조사 제거 base / 표면형 그대로) 두 갈래로 반환.

    반환: (dict_available: bool, known_bases: set[str], known_surfaces: set[str])
      · dict_available=False  → DB 미존재/임포트 실패. 표제어 검증 불가.
      · known_bases    → 어절의 조사 제거 base가 사전에 존재 — Case 4(부분매칭) 가드.
                         멀쩡한 표제어(예: "고독사")를 1글자 닮은 단어로 훼손하는 것 차단.
      · known_surfaces → 어절 표면형 자체가 사전에 존재 — Case A(조사 변형) 가드.
                         '외국인'·'디자인'·'온라인'처럼 끝음절이 조사가 아니라 단어의
                         일부인 등재어를, 조사 변형 전파가 덮어쓰지 않게 막는다.

    base/표면형을 한 번의 배치 조회로 함께 확인해 DB 왕복을 늘리지 않는다.
    """
    nd = _load_dict_module()
    if nd is None:
        return False, set(), set()
    try:
        if not nd.DB_PATH.exists():
            return False, set(), set()
    except Exception:
        return False, set(), set()

    surfaces = {w for w in doc_words if len(w) >= 2}
    bases = {b for b in (_strip_josa(w) for w in doc_words) if len(b) >= 2}
    query = bases | surfaces
    if not query:
        return True, set(), set()
    try:
        found = nd.batch_lookup_existence(query)
        exist = {w for w, r in found.items() if r.get("exists")}
        return True, exist & bases, exist & surfaces
    except Exception:
        # 조회 실패 시 검증 불가로 간주 (가드 보수적으로 적용)
        return False, set(), set()


def enforce_consistency(corrections: list, document_text: str, logger=None) -> list:
    """문서 전체 일관성 강화.

    원본 corrections는 그대로 보존하고, 변형 단어에 대한
    추가 Correction을 만들어 returned 리스트에 합쳐 반환.

    NFC 정규화를 모든 문자열에 일괄 적용해, "애니메이션(NFC) vs 애니메이션(NFD)"
    같은 시각적 동일·실제 다름 케이스의 거짓 변형 전파를 차단한다.

    logger: 선택적 콜러블. 사전 가드로 건너뛴 표제어 수 등 진단을 흘려보낸다.
    """
    if not corrections or not document_text:
        return corrections

    # 문서 텍스트도 NFC로 정규화 — doc_words 추출 시 코드포인트 통일
    document_text = _nfc(document_text)

    # 입력 corrections의 original/corrected도 NFC로 통일.
    # AI 결과는 _tag에서 이미 NFC지만, 다른 경로(사전검증, 수동) 진입에도 안전.
    nfc_corrections = []
    for c in corrections:
        nfc_corrections.append(replace(
            c,
            original=_nfc(c.original),
            corrected=_nfc(c.corrected),
        ))
    corrections = nfc_corrections

    # 1. base → canonical Correction (high confidence 우선)
    base_canonical: OrderedDict[str, Correction] = OrderedDict()
    for c in corrections:
        if c.source == "ai_polish":
            continue   # 윤문 항목은 후처리에서 제외
        if not c.original:
            continue
        base = _strip_josa(c.original)
        if len(base) < 2:
            continue
        existing = base_canonical.get(base)
        if existing is None:
            base_canonical[base] = c
        elif existing.confidence != "high" and c.confidence == "high":
            base_canonical[base] = c

    # 2. 문서에서 모든 한글 어절 추출 (이미 NFC 정규화됨)
    doc_words = set(re.findall(r"[가-힣]+", document_text))

    # 2.5 사전 표제어 가드 — 멀쩡한 등재어를 부분매칭 전파로 훼손하지 않게 차단.
    #     · 명사/표제어: _build_known_words (사전 정확 일치)
    #     · 활용형(겪고=겪다): core.morph 형태소 분석으로 보호 (사전엔 표제어만 있어
    #       표면형 조회로는 못 잡음). kiwipiepy 미설치 시 무영향.
    dict_available, known_bases, known_surfaces = _build_known_words(doc_words)
    guard_skips = 0
    _nd = _load_dict_module()
    try:
        from core import morph as _morph
        _morph = _morph if (_morph.available() and _nd is not None) else None
    except Exception:
        _morph = None
    _morph_exists = (lambda w: _nd.lookup_word(w)["exists"]) if _nd is not None else None

    existing_originals = {c.original for c in corrections}
    new_corrections: list = []
    # Case A에서 '본문에 단독 토큰으로 존재하는 조사형'을 만들어낸 base 집합.
    #   AI가 bare형(예: '상담채녈')만 냈는데 본문엔 '상담채녈을'만 있을 때,
    #   조사형 교정으로 대체했으므로 bare 교정은 중복 카드가 된다(아래에서 제거).
    subsumed_bases: set = set()

    def _add(new_c: Correction):
        # 모든 비교 전 한 번 더 NFC 정규화 — 안전망
        new_orig = _nfc(new_c.original)
        new_corr = _nfc(new_c.corrected)
        if new_orig in existing_originals or new_orig == new_corr:
            return
        new_corrections.append(replace(new_c, original=new_orig, corrected=new_corr))
        existing_originals.add(new_orig)

    # 3. 조사 변형 탐지 (Case A)
    #
    #    ⚠ 과거의 'Case B'(편집거리 1 통째 치환)는 제거되었다. 이유:
    #      · canon의 corrected(목표어)를 "1글자 닮은" **다른 단어**에 통째로
    #        덮어써, 문맥상 무관한 단어를 망가뜨리는 치명적 오교정을 냈다.
    #        예) "붇고→묻고"(정상) 확정 후 "겪고"를 "묻고"로,
    #            "고지사→고지서" 확정 후 "고독사"를 "고지서"로 둔갑.
    #      · 사전 표제어 가드로도 막을 수 없다. 사전은 표제어(겪다)만 담고
    #        활용형(겪고)은 없어, 멀쩡한 활용형을 비표제어로 오인한다.
    #      · 본래 의도였던 표기 혼재(키메세지↔키메시지)는 Case 4(부분 매칭)가
    #        이미 처리하므로 기능 손실도 없다.
    for base, canon in base_canonical.items():
        _, canon_corrected_base = _canonical_base_correction(canon)
        # canon이 단순 조사 교체(을/를)인 경우 base만으로는 전파 의미 없음.
        # 다만 같은 조사 형태가 doc에 존재하면 그것은 canon.original 자체이므로
        # 이미 existing_originals에 있어 skip됨. 그래서 그냥 진행해도 안전.
        for word in doc_words:
            if word in existing_originals:
                continue
            word_base = _strip_josa(word)
            if len(word_base) < 2:
                continue

            # 케이스 A: 조사 변형 (정확한 base 일치) — 같은 단어의 조사 차이만
            # 통일하므로 다른 단어로 둔갑할 위험이 없다.
            if word_base == base:
                # 사전 표제어 가드: 후보 어절 자체가 등재어이면(예: '외국인'·'디자인'·
                # '온라인'·'전문가') 끝음절은 조사가 아니라 단어의 일부다. 확장된 단음절
                # 조사('인'·'가' 등)가 등재 명사를 base로 잘못 환원해 조사 변형으로
                # 덮어쓰는 과교정을 차단한다. (사전 없으면 known_surfaces가 비어 무영향)
                if word in known_surfaces:
                    guard_skips += 1
                    continue
                # canon.original과 다른 표면형(조사형)이 본문에 단독 토큰으로 존재함
                # → canon이 bare형이면 이 조사형이 대체하므로 중복으로 표시.
                if word != canon.original:
                    subsumed_bases.add(base)
                # 교정으로 base의 받침이 바뀌면 뒤 조사도 호응시킨다(스윕과→스위프와).
                josa = reconcile_josa(canon_corrected_base, word[len(base):])
                _add(replace(
                    canon,
                    original=word,
                    corrected=canon_corrected_base + josa,
                    reason=f"[일관성] '{canon.original}' → '{canon.corrected}' 와 같은 단어 (조사 변형)",
                    consistency_flip=False,   # 방향 뒤집기는 base 카드에서만(변형 미전파)
                ))
                continue

    # 4. 부분 매칭 전파 — 짧은 교정 패턴이 긴 단어에 포함된 경우
    #    예: "메세지" → "메시지" 결정이 있으면 "키메세지" 같은 합성어에도 적용
    for canon in list(corrections):
        if canon.source == "ai_polish":
            continue
        canon_base, canon_corrected_base = _canonical_base_correction(canon)
        # 너무 짧으면 오탐 위험, 너무 길면 substring 매칭 의미 없음
        if not (2 <= len(canon_base) <= 4):
            continue
        if canon_base == canon_corrected_base:
            continue   # 변환 패턴이 없음 (조사만 차이) → 전파 무의미
        for word in doc_words:
            if word in existing_originals:
                continue
            if canon_base not in word:
                continue
            # ⚠ 이미 교정형(canon_corrected_base)을 포함한 단어엔 전파 금지(치명적 — '키메시지지').
            #   '메시'→'메시지' 패턴을 '키메시지'(이미 '메시지' 포함)에 substring 치환하면 글자가
            #   겹쳐 '키메시지지' 중복이 생긴다. 반면 '메시를'→'메시지를'·'메시전달'→'메시지전달'
            #   (교정형 미포함)은 정상 전파한다. ⚠ 과거 '확장형(메시⊂메시지) 전면 금지'는 '메시를'
            #   같은 정상 교정까지 막아 미탐을 냈다(사용자 보고) → 이 단어 단위 가드로 좁힌다.
            #   ⚠ 비교는 공백 제거형으로(2026-07-15 실측): 교정형이 띄어 쓴 '미생성 코드'면 붙여
            #   쓴 문서 어절 '미생성코드'와 그대로는 매칭이 안 돼 이 가드가 무력화되고,
            #   '미생성코드'→'미생성 코드코드' 중복 합성이 만들어졌다. word는 한글 런이라 공백이
            #   없으므로 교정형만 공백을 벗겨 비교한다(무공백 교정형은 동작 불변).
            if canon_corrected_base.replace(" ", "") in word:
                continue
            # 사전 가드: 후보 어절이 표제어이면(= 멀쩡한 단어) 부분 매칭 전파를
            # 하지 않는다. 예: "사고"→"사거" 패턴이 "사고력"(표제어)을
            # "사거력"으로 망가뜨리는 것을 막는다. (사전 없으면 무영향: 기존 동작)
            if _strip_josa(word) in known_bases:
                guard_skips += 1
                continue
            # 활용형 가드: 표제어 용언의 활용형(겪고=겪다)이면 보호한다.
            # 사전엔 표제어만 있어 위 정확 일치로는 못 잡는다.
            if _morph is not None and _morph.has_known_inflection(word, _morph_exists):
                guard_skips += 1
                continue
            # 단순 substring 치환
            new_word = word.replace(canon_base, canon_corrected_base, 1)
            if new_word == word:
                continue
            _add(replace(
                canon,
                original=word,
                corrected=new_word,
                reason=f"[일관성] '{canon_base}' → '{canon_corrected_base}' 패턴을 포함 단어에 전파",
                confidence="low",
                consistency_flip=False,   # 방향 뒤집기는 base 카드에서만(전파 카드 제외)
            ))

    if logger and guard_skips:
        logger(f"  → 일관성 가드: 사전 표제어 {guard_skips}건 전파 차단")

    # 5. bare형 중복 제거 — AI가 조사 없는 형태(예: '상담채녈')를 냈지만 본문엔
    #    단독 토큰으로 없고(항상 조사가 붙음) Case A가 조사형('상담채녈을')을 만든
    #    경우, bare 교정은 같은 위치를 가리키는 중복 카드다(본문 부분문자열로만
    #    매칭돼 앵커 경합에서 밀려 클릭 시 최상단으로 튐). 조사형으로 대체한다.
    kept = []
    dropped = 0
    for c in corrections:
        if (c.source != "ai_polish"
                and c.original
                and " " not in c.original
                and re.fullmatch(r"[가-힣]+", c.original)
                and c.original not in doc_words            # 본문에 단독 토큰으로 없음
                and _strip_josa(c.original) in subsumed_bases):  # 조사형으로 대체됨
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 일관성: 조사형으로 대체된 bare 교정 {dropped}건 중복 제거")

    return kept + new_corrections


def drop_redundant_josa_variants(corrections: list, logger=None) -> list:
    """접두(prefix) 부분문자열로 이미 처리되는 '조사 변형' 교정을 제거한다.

    같은 단어가 여러 소스(norm_map·eomun·Case A·받침 조사)에서 bare형과 조사형
    두 교정으로 동시에 잡히는 일이 흔하다(예: '뱃지'→'배지' + '뱃지를'→'배지를').
    조사형은 bare형의 부분문자열 치환으로 **정확히 동일한 결과**가 나오므로 잉여다.

    ⚠ 그런데 이 둘이 함께 적용 단계로 가면 등장(occurrence) 인덱스 정합성이 깨진다:
      · 검수 패널은 '뱃지를' 안의 '뱃지' 등장을 shadowed 처리하면서도 그 인덱스를
        skip_occurrences에 넣는다(_resolve_overlaps/_derive).
      · 브리지는 긴 교정('뱃지를')을 **먼저** 치환한다. 수락된 '뱃지를'는 '배지를'로
        바뀌어 이후 RepeatFind('뱃지')가 그 자리를 못 보는데, skip 인덱스는 그대로라
        뒤 인덱스가 한 칸씩 밀린다 → **수락한 등장이 안 바뀌고 거절한 등장이 바뀜**.
      · 거절된 '뱃지를'는 '뱃지를'로 남아 RepeatFind('뱃지')가 부분매칭 → 거절 등장이
        '배지를'로 오적용(실측 보고).

    조사형을 제거하고 bare 교정 하나로 통일하면, 검수 패널 등장 집합과 브리지
    RepeatFind 집합이 일치해 부분 거절까지 정확히 동작한다(bare 교정이 부분문자열
    치환으로 본문의 'bare+조사' 등장까지 함께 교정·색상 적용).

    제거 조건(엄격) — V를 버리려면 다른 교정 B가 존재하고:
      · len(B.original) >= 2  (위험한 1글자 base 차단)
      · V.original == B.original + 접미   (B.original이 V.original의 **진접두**)
      · V.corrected == B.corrected + 같은 접미
      · B.original != B.corrected         (B가 실제 치환을 수행)
    이때 V는 B의 접두 치환으로 정확히 재현되므로 안전하게 버린다.

    ⚠ 중간 삽입(infix) 부분매칭(Case 4)은 생성 시점에만 사전 가드가 걸려 있어
       무차별 부분치환으로 일반화하면 가드를 우회한다 → **접두에 한정**한다.
       윤문(ai_polish)·검수 플래그(original==corrected)는 대상이 아니다.
    """
    if not corrections:
        return corrections

    # B 후보: 실제 치환을 하는(original != corrected) 비-윤문 교정.
    bases = []
    for c in corrections:
        if c.source == "ai_polish":
            continue
        bo, bc = _nfc(c.original), _nfc(c.corrected)
        if len(bo) >= 2 and bo != bc:
            bases.append((bo, bc))

    def _redundant(v) -> bool:
        if v.source == "ai_polish" or not v.original:
            return False
        vo, vc = _nfc(v.original), _nfc(v.corrected)
        if vo == vc:
            return False
        for bo, bc in bases:
            if len(bo) >= len(vo) or not vo.startswith(bo):
                continue
            suffix = vo[len(bo):]
            if vc == bc + suffix:
                return True
        return False

    kept, dropped = [], 0
    for c in corrections:
        if _redundant(c):
            dropped += 1
            continue
        kept.append(c)
    if logger and dropped:
        logger(f"  → 적용 정합성: 조사 변형 교정 {dropped}건 제거"
               f" (접두 교정이 부분문자열로 함께 처리 — 등장 인덱스 어긋남 방지)")
    return kept


def reconcile_variant_confidence(corrections: list, logger=None) -> list:
    """같은 단어의 조사 변형들이 **일관된 confidence**를 갖도록 통일.

    validate()는 항목마다 corrected를 형태소 분석해 신뢰도를 매기는데, kiwi가
    '홋카이도현'(통째 미등재 명사)과 '홋카이도현의'(홋카이도+현)를 다르게 분석해
    같은 단어인데 bare=low, 조사형=high로 갈리는 문제가 있다. → 자동 일괄 적용 시
    조사형만 교정되고 bare는 저신뢰로 거절돼 문서가 불일치해진다(사용자 보고).

    base(조사 제거한 corrected)가 같은 그룹에서 하나라도 high면 모두 high로 통일한다.
    조사만 다른 같은 단어이므로 confidence·color는 단어 단위 속성이어야 한다.
    윤문·사전 검수 플래그는 대상에서 제외.
    """
    if not corrections:
        return corrections
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for i, c in enumerate(corrections):
        if c.source in ("ai_polish", "dict_flag"):
            continue
        base = _strip_josa(_nfc(c.corrected))
        if len(base) >= 2:
            groups[base].append(i)

    out = list(corrections)
    promoted = 0
    for base, idxs in groups.items():
        if len(idxs) < 2:
            continue
        highs = [i for i in idxs if out[i].confidence == "high"]
        if not highs:
            continue
        ref_color = out[highs[0]].color
        for i in idxs:
            if out[i].confidence != "high":
                out[i] = replace(out[i], confidence="high", color=ref_color)
                promoted += 1
    if logger and promoted:
        logger(f"  → 일관성: 조사 변형 신뢰도 통일 {promoted}건 (bare/조사형 불일치 보정)")
    return out
