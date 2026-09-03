"""
core/user_dict_file.py — 사용자 첨부 사전(Excel) : 모든 교정 로직의 **최상위 계층**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용자가 설정 화면에서 직접 첨부하는 2단 엑셀(원문 · 교정문)을 읽어
  ① **교정 제안** — 원문이 본문에 있으면 무조건 카드를 만든다(수락 상태로 등장).
  ② **보호막**    — 사전·규칙·AI 등 **다른 모든 계층이 그 결정을 뒤집지 못하게** 막는다.
두 가지를 함께 제공한다. 파일이 없으면 이 모듈은 전혀 관여하지 않는다(기존 동작 그대로).

★이 모듈과 `core/userdict.py`는 **다른 것**이다. 헷갈리면 사고가 난다:
  · core/userdict.py     = 서버에서 내려받는 **조직 공유 학습 사전**(userdict.db, 큐레이션).
  · core/user_dict_file  = 이번 실행에만 쓰는 **사용자 개인 첨부 파일**(첨부하면 최우선).
  우선순위는 사용자 첨부 > 국가 표준/사내 페어 > 나머지다. 사용자가 그 원고를 보고 직접
  적어 낸 목록이 가장 확실한 근거라는 판단(사용자 지시 2026-09-03).

━━ 행의 세 가지 뜻 — **앞뒤 공백이 의미를 가른다** ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ 셀 값을 `strip()` 하면 안 된다. 사용자가 앞뒤 공백으로 '띄어쓰기 통일'을 표현하기
  때문이다(사용자 지시 2026-09-03 · 샘플 `사용자사전-샘플(띄어쓰기통일).xlsx`).

  ┌ 판정(단일 출처: `classify_row`) ─────────────────────────────────────────┐
  │ 교정문이 비었거나 원문과 완전히 같다        → **보호 전용**(고치지 말 것)   │
  │ 앞뒤 공백을 떼면 **글자가 다르다**           → **낱말 페어**(경성문화시→경성문화사)│
  │ 앞뒤 공백을 떼면 **글자가 같다**(공백만 다름) → **경계 규칙**(띄어쓰기 통일)  │
  └──────────────────────────────────────────────────────────────────────┘

  '앞뒤 공백을 떼면 글자가 다르다'가 낱말 페어인 건 실수 방지책이기도 하다 —
  `'경성문화시 '`처럼 엑셀에서 딸려 온 **우연한 공백**은 글자가 다르므로 낱말 페어로
  떨어져 무해하다. 경계 규칙이 되려면 **양쪽 글자가 완전히 같아야** 한다.

  경계 규칙 예:  `' 기술' → '기술'`  = 앞의 공백을 없앤다 → `xx 기술` · `xx기술` 혼재를
                                      `xx기술`로 통일
                 `'정보 ' → '정보'`  = 뒤의 공백을 없앤다 → `정보 xx` · `정보xx` 혼재를
                                      `정보xx`로 통일
                 `'기술' → '기술 '`  = 반대 방향(붙은 것을 띄운다)도 같은 방식으로 동작

━━ 경계 규칙은 **어절 쌍으로 펼쳐서** 카드를 만든다 ━━━━━━━━━━━━━━━━━━━━━━━━━━
★`' 기술'`을 그대로 `Correction.original`로 쓰면 안 된다. 검수 카드가 "원본 기술 / 교정
  기술"로 보여 **차이가 눈에 보이지 않고**, 정오표·메모·PDF도 전부 같은 꼴이 되며, 교정값
  인라인 편집은 공백 의미를 잃는다. 그래서 문서에서 실제 매칭된 자리를 **양옆 어절 경계까지
  넓혀**('정보 기술을'→'정보기술을') 사람이 읽을 수 있는 문자열 쌍으로 펼친다. 이 앱의
  검수·산출물 기계 전체가 "original = 문서에서 찾을 수 있고 사람이 읽을 수 있는 문자열"을
  전제로 돌아가기 때문이다(memory `three-artifacts-same-spots`).
⚠ 한 어절 무리에 규칙이 여러 개 걸리면 **한 번에 모아 적용**한다('정보 통신 기술' +
  두 규칙 ⇒ '정보통신기술' 한 장). 규칙마다 카드를 따로 내면 서로 겹쳐 한쪽이 가려진다.
⚠ 넓힌 원문이 공백으로 시작·끝나면 버린다 — 줄 앞 들여쓰기처럼 **붙일 낱말이 없는 자리**다.

━━ 보호 판정(단일 출처: `violates()`) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"용어를 고친다"를 문자열로 정의한다. 보호 대상 t가 **다른 계층** 교정의 원문에 있을 때,

  · 교정문에도 t가 그대로 있다          ⇒ **허용**. 용어는 안 건드리고 주변만 손댄 교정
                                          (예: t='콘텐츠', '콘텐츠개발'→'콘텐츠 개발').
  · 교정문에 사용자가 정한 결과 u가 있다 ⇒ **허용**. 사용자 사전이 정한 방향과 같다.
  · 둘 다 아니다                        ⇒ **드롭**. 용어 자체가 사라지거나 변형됐다.
  · 원문 == 교정문(검수 필요 카드)       ⇒ **드롭**. 사용자가 이미 답을 적어 둔 낱말이다.

⚠ **사용자 사전 자신(`source == SOURCE`)은 검사 대상이 아니다.** 보호의 근거가 보호에 걸려
  죽는 자기모순을 막는다 — 사용자가 적은 항목은 어떤 경우에도 드롭되지 않는다.
⚠ 1글자 표제어는 **보호에서 제외**한다(교정은 한다). 1글자를 부분문자열로 보호하면 그 글자가
  든 교정이 전부 죽는다 — '사' 하나로 문서 절반이 막힌다. 실효가 아니라 사고다.
⚠ 경계 규칙의 **낱말 토큰**('기술'·'정보')은 보호 집합에 넣지 않는다. 사용자가 등록한 건
  낱말이 아니라 **띄어쓰기 결정**이고, 흔한 명사를 부분문자열로 보호하면 '기술울' 같은
  오탈자 검수 카드까지 조용히 사라진다. 대신 ① 펼쳐 낸 어절 쌍을 보호에 등록하고
  ② 다른 계층 교정의 **교정문에 경계 규칙을 적용해 어긋나면 그 자리를 맞춰 고쳐 준다**
  (`filter_conflicts`의 조율) — 드롭이 아니라 조율인 이유는, 드롭하면 '그는 긔술을'→
  '그는 기술을' 같은 **오탈자 교정까지 함께 잃기** 때문이다.

⚠ 이 판정은 **한 곳에만** 있어야 한다. 워커가 여러 지점에서 부르되(스크리닝 앞·병합 직후·
  직렬화 직전) 부르는 함수는 언제나 이 모듈의 것이다 — 같은 판정을 두 계층이 따로 구현해
  조용히 어긋난 전례가 이 저장소에 있다(memory `occurrence-skip-single-predicate`).

⚠ AI 프롬프트는 **건드리지 않는다**. 보호는 전부 결정론 후처리로 한다 — 프롬프트는 내용뿐
  아니라 순서 변경만으로 정밀도가 무너졌고(memory `gemini-call-hardening-and-prompts`),
  글로서리 지침은 반복 표기 문서에서 폭주(504)를 일으킨 전력이 있다(`glossary-runaway-504`).
  대신 사전 스크리닝 결과(suspicious_words)에서 보호 낱말을 빼 AI에 '미등재어'로 흘러가지
  않게 한다 — 목록에서 빼는 것이지 지침을 더하는 게 아니다.
"""

import unicodedata
from pathlib import Path

from core.models import Correction, HL_DICT

# ══════════════════════════════════════════════════════
# ▌식별자 — 다른 파일이 이 계층을 알아보는 유일한 이름
# ══════════════════════════════════════════════════════
SOURCE   = "user_dict"                  # Correction.source
CATEGORY = "사용자 사전"                  # Correction.category (카드 배지 · 정오표 '교정 유형')
REASON   = "사용자 사전에 등록된 항목"      # 사용자 지시 2026-09-03 — 문구 고정

# 첨부 UI와 로더가 같은 기준을 쓰도록 확장자를 여기 한 곳에 둔다.
EXTENSIONS = (".xlsx", ".xlsm")

# 보호 최소 길이(위 헤더 ⚠ 참조). 교정 제안에는 적용하지 않는다.
_MIN_PROTECT_LEN = 2

# 경계 규칙을 펼칠 때 원문의 최대 길이. 공백 없는 긴 덩어리(URL·표 셀 등)에 붙으면 카드가
#   읽을 수 없게 길어진다 — 넘으면 그 자리는 버린다(건수는 로그에 남긴다).
_BOUNDARY_SPAN_MAX = 40

# 제목줄 판정 — 첫 행이 이 낱말들이면 데이터가 아니라 헤더다(공백 제거 후 비교).
_HEADER_WORDS = {
    "원문", "원본", "수정전", "교정전", "before", "오류", "오탈자", "잘못된표기",
    "틀린표기", "찾을말", "검색어", "대상", "입력",
    "교정문", "교정", "수정후", "교정후", "after", "정정", "바른표기", "올바른표기",
    "바꿀말", "대체어", "결과",
}

# 행 분류 결과
ROW_PROTECT  = "protect"    # 보호 전용 — 아무도 고치지 말 것
ROW_PAIR     = "pair"       # 낱말 페어 — 원문을 교정문으로
ROW_BOUNDARY = "boundary"   # 경계 규칙 — 띄어쓰기 통일(앞뒤 공백만 다름)


def _nfc(v) -> str:
    """셀 값 → 비교 가능한 문자열. **앞뒤 공백은 그대로 둔다**(헤더의 세 가지 뜻 참조).

    추출 텍스트가 파이프라인 진입점에서 NFC로 통일되므로 사전 쪽도 NFC로 맞춘다. 눈에
    안 보이는 문자(ZWSP·BOM)는 원고에서 절대 매칭되지 않으므로 제거하고, NBSP는 보통
    공백으로 편다 — 엑셀 붙여넣기로 딸려 오는 대표적인 쓰레기 문자다.
    """
    if v is None:
        return ""
    s = str(v)
    s = s.replace("​", "").replace("﻿", "").replace("\xa0", " ")
    return unicodedata.normalize("NFC", s)


def _squash(s: str) -> str:
    """제목줄 비교용 — 공백류를 전부 없앤 형태."""
    return "".join(s.split())


def classify_row(a: str, b: str) -> tuple:
    """엑셀 한 행 → (종류, 원문, 교정문). 쓸 수 없는 행이면 (None, "", "").

    ★행의 뜻을 정하는 **단일 출처**다(모듈 헤더의 표가 근거). 호출부에서 다시 해석하지 말 것.
    """
    ka, kb = a.strip(), b.strip()
    if not ka:
        return (None, "", "")
    if not kb or a == b:
        return (ROW_PROTECT, ka, "")
    if ka != kb:
        # 글자가 다르다 → 낱말 페어. 앞뒤 공백은 우연히 딸려 온 것으로 보고 떼어 낸다.
        return (ROW_PAIR, ka, kb)
    # 글자는 같고 앞뒤 공백만 다르다 → 띄어쓰기 통일 규칙(공백을 살려 둔다)
    return (ROW_BOUNDARY, a, b)


def _urlish(s: str) -> bool:
    """URL·경로·이메일이면 띄어쓰기를 건드리지 않는다(core.morph와 같은 원칙, graceful)."""
    try:
        from core import morph
        return any(morph.is_urlish(w) for w in s.split())
    except Exception:
        return "://" in s or "@" in s


# 붙임 판정 전에 **바깥쪽**에서만 떼는 부호. 안쪽 부호('정책,'의 쉼표)는 남긴다 —
#   그게 "이 둘은 나열이지 복합어가 아니다"라는 증거이기 때문이다(_join_ok 참조).
_OUTER_L = "‘“'\"([{<「『《〈"
_OUTER_R = "’”'\")]}>」』》〉.,;:!?…·"


def _joinable(word: str, *, left: bool) -> bool:
    """붙여 써도 되는 어절인가 — 앞말/뒷말 기준이 다르다(core.morph 위임 · graceful).

    ⚠ kiwi가 없거나 실패하면 False, 즉 **붙이지 않는다**. 사용자 사전 규칙이 통째로
      잠드는 셈이지만, 그 반대(형태소 판정 없이 문자열대로 붙이기)는 '한다. 기술' 같은
      자리를 수락 상태로 쏟아 낸다 — 조용한 오적용보다 조용한 미적용이 낫다.
    """
    try:
        from core import morph
        return morph.is_joinable_left(word) if left else morph.is_joinable_right(word)
    except Exception:
        return False


def _split_ok(run: str, offset: int) -> bool:
    """어절 `run`의 `offset` 자리에서 띄어도 되는가(core.morph 위임 · graceful)."""
    try:
        from core import morph
        return morph.is_word_split_point(run, offset)
    except Exception:
        return False


class UserDictFile:
    """읽어 들인 사용자 사전 한 벌.

    Attributes:
        path          : 원본 엑셀 경로
        pairs         : {원문: 교정문} — 낱말 페어(부분문자열 치환)
        boundary      : ((원문, 교정문), …) — 경계 규칙(띄어쓰기 통일, 앞뒤 공백 유지)
        protected     : frozenset — 다른 계층이 뒤집으면 안 되는 문자열
        rows          : 읽어 낸 데이터 행 수(헤더 제외)
        skipped       : 원문 칸이 비어 쓸 수 없던 행 수
        dropped_short : 1글자라 보호에서만 빠진 표제어 수
    """

    __slots__ = ("path", "pairs", "boundary", "protected", "rows", "skipped",
                 "dropped_short", "expanded", "boundary_skipped", "boundary_blocked")

    def __init__(self, path: str, pairs: dict, boundary: tuple, protected: frozenset,
                 rows: int, skipped: int, dropped_short: int):
        self.path = path
        self.pairs = pairs
        self.boundary = boundary
        self.protected = protected
        self.rows = rows
        self.skipped = skipped
        self.dropped_short = dropped_short
        # 경계 규칙을 문서에 펼쳐 얻은 {원문: 교정문} — build_corrections가 채운다.
        #   보호 판정이 "이 자리를 되돌리는 교정"을 알아보게 하는 근거다.
        self.expanded = {}
        self.boundary_skipped = 0
        self.boundary_blocked = 0

    def __bool__(self) -> bool:
        return bool(self.pairs or self.boundary or self.protected)

    @property
    def name(self) -> str:
        return Path(self.path).name if self.path else ""

    # ══════════════════════════════════════════════
    # ① 교정 제안
    # ══════════════════════════════════════════════
    def build_corrections(self, text: str, logger=None) -> list:
        """본문에 실제로 등장하는 항목만 교정 카드로 만든다.

        ⚠ 낱말 페어는 어절이 아니라 **부분문자열**로 찾는다. 사용자는 '김대깅'만 적지
          '김대깅은'까지 적지 않는데 문서에는 조사가 붙어 나온다. 부분문자열이면 조사형이
          저절로 맞고, 브리지 RepeatFind가 보는 좌표계(원문 문자열을 그대로 찾는다)와도
          같다(memory `three-artifacts-same-spots`).
        """
        if not text:
            return []
        out = [self._card(o, c) for o, c in self.pairs.items() if o in text]

        exp = self._expand_boundary(text)
        if exp:
            # 펼친 쌍을 보호 근거로 등록 — 다른 계층이 이 자리를 되돌리면 violates가 잡는다.
            self.expanded.update(exp)
            self.protected = frozenset(self.protected
                                       | set(exp) | set(exp.values()))
            out.extend(self._card(o, c) for o, c in exp.items())
        if logger and self.boundary:
            logger(f"      · 띄어쓰기 통일 규칙 {len(self.boundary)}건 → 문서 어절 쌍 "
                   f"{len(exp)}건으로 전개 · 복합명사가 아닌 자리 "
                   f"{self.boundary_blocked}곳 제외"
                   + (f" · 붙일 낱말이 없거나 너무 긴 자리 {self.boundary_skipped}곳 제외"
                      if self.boundary_skipped else ""))
        return out

    @staticmethod
    def _card(original: str, corrected: str) -> Correction:
        return Correction(
            original=original, corrected=corrected,
            reason=REASON,
            source=SOURCE, color=HL_DICT,
            category=CATEGORY, confidence="high",
        )

    def _join_ok(self, text: str, s: int, k: str, v: str) -> bool:
        """이 자리에서 공백을 지워도 되는가 — **양옆이 둘 다 순수 체언일 때만** 붙인다.

        ★규칙은 `' 기술' → '기술'`처럼 "공백 하나를 지워라"인데, 문자열 그대로 문서 전체에
          적용하면 복합명사가 아닌 자리까지 붙는다. 실측(2026-09-03 · 실파일H · 규칙 2개
          → 174곳)에서 '한다. 기술'·'정책, 기술'·'하는 기술을'·'및 기술과'·'각 기술'처럼
          **명백히 틀린 자리가 절반을 넘었다**. 그 카드들이 전부 수락 상태로 뜨므로 그대로
          두면 사용자가 100장 가까이 거절해야 한다.
        ⚠ 판정은 `core.morph.is_joinable_left`/`is_joinable_right` 두 함수로만 하고(앞말은
          조사·어미가 붙으면 False, 뒷말은 조사 허용), kiwi가 없으면 **붙이지 않는다** —
          '붙일지 말지'를 묻는 판정이라 모르면 안 붙이는 쪽이 안전하다.
        ⚠ 공백을 **넣는** 방향(띄어 쓰라는 규칙)에는 걸지 않는다. 글자를 붙이는 것과 달리
          띄우는 쪽은 낱말을 망가뜨리지 않고, 사용자가 명시한 자리에서만 일어난다.
        """
        n = len(text)
        if len(v) > len(k):
            # ── 띄우는 방향 — **형태소 경계에서만** 띄운다.
            #    실측: `'기술' → '기술 '` 규칙이 '기술이'를 '기술 이'로 쪼갰다(조사 분리).
            adds = []
            if v[:1].isspace() and not k[:1].isspace():
                adds.append(s)
            if v[-1:].isspace() and not k[-1:].isspace():
                adds.append(s + len(k))
            for q in adds:
                a, b = q, q
                while a > 0 and not text[a - 1].isspace():
                    a -= 1
                while b < n and not text[b].isspace():
                    b += 1
                if not _split_ok(text[a:b], q - a):
                    return False
            return True
        if len(v) == len(k):
            return True                     # 공백 수가 그대로 — 가드 대상 아님
        for p in (s + i for i, ch in enumerate(k) if ch.isspace()):
            a = p                           # 지워질 공백 p의 좌우 어절을 문서에서 떠 온다
            while a > 0 and not text[a - 1].isspace():
                a -= 1
            b = p + 1
            while b < n and not text[b].isspace():
                b += 1
            # ⚠ 바깥쪽 부호만 뗀다. 안쪽 부호('정책,'의 쉼표)는 **남겨서** 판정에 쓴다
            #   — 그 쉼표가 바로 "이 둘은 나열이지 복합어가 아니다"라는 증거다.
            left = text[a:p].lstrip(_OUTER_L)
            right = text[p + 1:b].rstrip(_OUTER_R)
            if not left or not right:
                return False                # 줄 앞 들여쓰기 등 — 붙일 낱말이 없다
            if not (_joinable(left, left=True) and _joinable(right, left=False)):
                return False
        return True

    def _expand_boundary(self, text: str) -> dict:
        """경계 규칙 → 문서에 실재하는 {어절 원문: 어절 교정문}(모듈 헤더 참조)."""
        self.boundary_skipped = 0
        self.boundary_blocked = 0
        if not self.boundary or not text:
            return {}

        # ① 규칙별 매치 수집 → ② 겹치면 왼쪽·긴 것 우선으로 하나만 남긴다.
        hits = []
        for k, v in self.boundary:
            start = 0
            while True:
                i = text.find(k, start)
                if i < 0:
                    break
                if self._join_ok(text, i, k, v):
                    hits.append((i, i + len(k), v))
                else:
                    self.boundary_blocked += 1
                start = i + 1
        if not hits:
            return {}
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        picked, last_end = [], -1
        for h in hits:
            if h[0] < last_end:
                continue
            picked.append(h)
            last_end = h[1]

        # ③ 어절 경계로 넓히고, 겹치거나 맞닿는 것끼리 한 무리로 합친다.
        #    ⚠ 합치지 않으면 '정보 통신 기술'이 '정보 통신'·'통신 기술' 두 카드로 갈려
        #      서로 겹치고, 검수 패널의 겹침 해소가 한쪽을 가려 버린다.
        n = len(text)
        groups = []
        for s, e, v in picked:
            a, b = s, e
            while a > 0 and not text[a - 1].isspace():
                a -= 1
            while b < n and not text[b].isspace():
                b += 1
            if groups and a <= groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], b)
                groups[-1][2].append((s, e, v))
            else:
                groups.append([a, b, [(s, e, v)]])

        # ④ 무리 → (원문, 교정문). 같은 쌍은 한 카드로 모인다(등장은 검수 패널이 센다).
        out = {}
        for a, b, ms in groups:
            original = text[a:b]
            if (not original or original[0].isspace() or original[-1].isspace()
                    or len(original) > _BOUNDARY_SPAN_MAX or _urlish(original)):
                self.boundary_skipped += 1      # 붙일 낱말이 없는 자리 / 너무 긴 덩어리
                continue
            buf, cur = [], a
            for s, e, v in ms:
                buf.append(text[cur:s])
                buf.append(v)
                cur = e
            buf.append(text[cur:b])
            corrected = "".join(buf)
            if corrected == original or "\n" in original or "\n" in corrected:
                self.boundary_skipped += 1
                continue
            out.setdefault(original, corrected)
        return out

    # ══════════════════════════════════════════════
    # ② 보호막
    # ══════════════════════════════════════════════
    def hits(self, s: str) -> list:
        """문자열 s 안에 들어 있는 보호 낱말 목록(없으면 빈 리스트)."""
        if not s or not self.protected:
            return []
        return [t for t in self.protected if t in s]

    def covers(self, word: str) -> bool:
        """이 낱말이 사용자 사전의 관할인가 — 사전 스크리닝 플래그 억제에 쓴다."""
        return bool(self.hits(word))

    def target_of(self, term: str) -> str:
        """사용자가 그 표제어에 대해 정한 결과(없으면 "")."""
        return self.pairs.get(term) or self.expanded.get(term) or ""

    def violates(self, original: str, corrected: str) -> str:
        """이 교정이 보호 낱말을 건드리는가. 건드리면 그 낱말을, 아니면 ""를 돌려준다.

        ★판정식은 모듈 헤더의 표가 전부다 — 호출부에서 다시 해석하지 말 것.
        """
        hits = self.hits(original)
        if not hits:
            return ""
        if original == corrected:
            return hits[0]          # 검수 필요 카드 — 이미 사용자가 답을 적어 둔 낱말
        for t in hits:
            if t in corrected:
                continue                        # 용어는 그대로 — 주변만 손댄 교정
            u = self.target_of(t)
            if u and u in corrected:
                continue                        # 사용자 사전이 정한 결과와 같은 방향
            return t
        return ""

    def harmonize(self, corrected: str) -> str:
        """교정문에 경계 규칙을 적용한 결과 — 다른 계층 교정의 띄어쓰기를 사전에 맞춘다."""
        if not self.boundary or not corrected:
            return corrected
        for k, v in self.boundary:
            corrected = corrected.replace(k, v)
        return corrected

    def filter_conflicts(self, corrections: list, logger=None) -> tuple:
        """다른 계층의 교정을 사용자 사전에 맞춘다 — 어긋난 띄어쓰기는 고치고, 용어를
        건드리는 교정은 걷어낸다.

        Returns: (남은 목록, 드롭된 목록)

        ⚠ `source == SOURCE`(사용자 사전 자신)는 검사 대상이 아니다 — 사용자가 적은
          항목은 어떤 경우에도 여기서 드롭되지 않는다.
        """
        if not corrections or not (self.protected or self.boundary):
            return corrections, []
        kept, dropped, tuned = [], [], 0
        for c in corrections:
            if getattr(c, "source", "") == SOURCE:
                kept.append(c)
                continue
            orig = getattr(c, "original", "") or ""
            corr = getattr(c, "corrected", "") or ""

            # ① 띄어쓰기 조율 — 교정문이 사전의 경계 결정과 어긋나면 그쪽으로 맞춘다.
            #    ⚠ 드롭이 아니라 조율이다. 드롭하면 '그는 긔술을'→'그는 기술을' 같은
            #      오탈자 교정까지 함께 잃는다(모듈 헤더 참조). 원문은 절대 손대지 않는다
            #      — 문서에서 찾을 수 있는 문자열이어야 한다는 불변식.
            #    ⚠ 단, 조율 결과가 원문 그대로면 그 교정은 **사전 결정을 되돌리는 것**
            #      뿐이다('정보기술'→'정보 기술'). 조율할 게 없으므로 드롭한다 — 이게
            #      없으면 이미 붙어 있던 자리를 다른 계층이 도로 띄운다(실측 확인).
            if corr and orig != corr:
                fixed = self.harmonize(corr)
                if fixed != corr:
                    if fixed == orig:
                        dropped.append((c, "띄어쓰기 통일"))
                        continue
                    c.corrected = fixed
                    corr = fixed
                    tuned += 1

            term = self.violates(orig, corr)
            if term:
                dropped.append((c, term))
            else:
                kept.append(c)

        if logger:
            # 화면 로그 규약: 표시되는 줄은 `[태그]` + `n건`, 개별 예시는 `· ` 불릿으로만.
            if tuned:
                logger(f"  [사용자 사전] 다른 교정 {tuned}건의 띄어쓰기를 사전 결정에 맞춤")
            if dropped:
                logger(f"  [사용자 사전] 등록 용어를 건드리는 다른 교정 {len(dropped)}건 제외 "
                       "(사용자 사전이 최우선)")
                for c, term in dropped[:20]:
                    logger(f"      · '{term}' 보호 — '{c.original}' → '{c.corrected}' "
                           f"({getattr(c, 'source', '')}) 제외")
        return kept, [c for c, _t in dropped]


# ══════════════════════════════════════════════════════
# ▌로더 — 실패는 예외가 아니라 사유를 남긴 None(graceful)
# ══════════════════════════════════════════════════════

def load(path: str, logger=None):
    """엑셀(원문 · 교정문 2단)을 읽어 UserDictFile을 만든다. 실패하면 None.

    관대하게 읽는다 — 사용자가 손으로 만든 목록이다:
      · 시트를 전부 훑는다(첫 시트만 보면 조용히 절반을 잃는다).
      · 첫 행이 '원문/교정문' 같은 제목줄이면 건너뛴다.
      · 앞쪽 두 칸만 본다(메모·비고 칸이 뒤에 있어도 무해).
      · 행의 뜻은 `classify_row`가 정한다(보호 전용 / 낱말 페어 / 경계 규칙).
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        if logger:
            logger(f"  [사용자 사전] 파일을 찾을 수 없습니다: {p.name}")
        return None
    try:
        import openpyxl
    except ImportError:
        if logger:
            logger("  [사용자 사전] openpyxl 미설치 — 사용자 사전을 읽지 못했습니다")
        return None

    try:
        wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    except Exception as e:
        if logger:
            logger(f"  [사용자 사전] 파일을 열 수 없습니다 ({e}) — "
                   "이번 실행은 사용자 사전 없이 진행합니다")
        return None

    pairs, boundary, protect_only = {}, [], set()
    rows = skipped = dups = 0
    try:
        for ws in wb.worksheets:
            first = True
            for raw in ws.iter_rows(values_only=True):
                if raw is None:
                    continue
                a = _nfc(raw[0] if len(raw) > 0 else "")
                b = _nfc(raw[1] if len(raw) > 1 else "")
                if first:
                    first = False
                    if (_squash(a) in _HEADER_WORDS
                            and (not b.strip() or _squash(b) in _HEADER_WORDS)):
                        continue            # 제목줄
                kind, o, c = classify_row(a, b)
                if kind is None:
                    if b.strip():
                        skipped += 1        # 교정문만 있는 행 — 무엇을 고칠지 알 수 없다
                    continue
                rows += 1
                if kind == ROW_PROTECT:
                    protect_only.add(o)
                elif kind == ROW_BOUNDARY:
                    if (o, c) not in boundary:
                        boundary.append((o, c))
                elif o in pairs and pairs[o] != c:
                    dups += 1               # 먼저 적힌 값을 살린다(위에서 아래로 읽는 순서)
                else:
                    pairs[o] = c
    except Exception as e:
        if logger:
            logger(f"  [사용자 사전] 읽는 중 오류 ({e}) — 읽어 낸 만큼만 사용합니다")
    finally:
        try:
            wb.close()
        except Exception:
            pass

    # 보호 집합 = 낱말 페어의 양쪽 + 보호 전용. 1글자는 제외(헤더 ⚠).
    #   ⚠ 경계 규칙의 낱말 토큰('기술'·'정보')은 **넣지 않는다** — 흔한 명사를 부분문자열로
    #     보호하면 그 글자가 든 오탈자 검수 카드까지 조용히 사라진다(헤더 ⚠ 참조).
    #     경계 보호는 `harmonize`와, build_corrections가 등록하는 `expanded`가 맡는다.
    cand = set(pairs) | set(pairs.values()) | protect_only
    protected = frozenset(t for t in cand if len(t) >= _MIN_PROTECT_LEN)
    dropped_short = len(cand) - len(protected)

    ud = UserDictFile(str(p), pairs, tuple(boundary), protected,
                      rows, skipped, dropped_short)

    if logger:
        logger(f"  [사용자 사전] '{p.name}' 로드 — 낱말 {len(pairs)}건 · "
               f"띄어쓰기 통일 {len(boundary)}건 · 보호 용어 {len(protected)}건")
        if protect_only:
            logger(f"      · 교정문이 비어 '고치지 말 것'으로만 등록된 표제어 "
                   f"{len(protect_only)}건")
        if dups:
            logger(f"      · 같은 원문에 다른 교정문이 적힌 행 {dups}건 — 먼저 적힌 값을 사용")
        if skipped:
            logger(f"      · 원문 칸이 비어 사용할 수 없는 행 {skipped}건")
        if dropped_short:
            logger(f"      · 1글자라 보호에서 제외된 표제어 {dropped_short}건 "
                   "(부분문자열 보호가 문서 전체를 막는 것을 방지)")
        nested = [k for k in pairs if any(k != o and o in k for o in pairs)]
        if nested:
            logger(f"      · 다른 표제어를 품고 있는 표제어 {len(nested)}건 "
                   "(겹치는 자리는 한 카드로 합성됩니다)")
    return ud
