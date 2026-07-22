"""
nikl_dict.py — 표준국어대사전 로컬 SQLite 검증 모듈
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
setup_dict.py 로 구축한 data/stdict.db 를 직접 쿼리합니다.

특징:
  - Deno/API 키/네트워크 완전 불필요
  - 배치 조회로 대량 단어 검사 시 10배 이상 빠름
  - EXE 번들 완전 지원 (PyInstaller frozen 경로 처리)
  - DB 없으면 검증 건너뜀 (앱 중단 없음)
  - 외래어/전문어 등 작가 의도 어휘는 보존 (의심 처리 안 함)
"""

import re
import sqlite3
import sys
import threading
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════
# ▌DB 경로 결정
# ══════════════════════════════════════════════════════

def _resolve_db_path() -> Path:
    """stdict.db 위치 — 탐색 규칙은 datapaths가 단일 출처로 관리한다.

    앱/데이터를 분리 배포하므로(설계: datapaths.py 헤더) 사용자 폴더(업데이터 설치본) →
    EXE 옆 → 번들 내부 순으로 찾는다. datapaths를 못 읽으면 기존 규칙으로 폴백한다.
    """
    try:
        from datapaths import data_dir
        return data_dir() / "stdict.db"
    except Exception:
        pass
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS)          / "data" / "stdict.db")
        candidates.append(Path(sys.executable).parent / "data" / "stdict.db")
    else:
        candidates.append(Path(__file__).parent / "data" / "stdict.db")

    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


DB_PATH = _resolve_db_path()

_JOSA_RE = re.compile(
    r"(을|를|이|가|은|는|의|에|에서|에게|으로|로|와|과|도|만|까지|부터|"
    r"이다|이라|으로서|로서|이며|이고|이지만|이나|이든지|이면|"
    r"습니다|ㅂ니다|었습니다|았습니다|겠습니다|었다|았다|겠다|"
    r"아요|어요|았어요|었어요|세요|십시오)$"
)

HL_UNVERIFIED = 0x0055FF   # BGR 주황색

# S5: register 화이트리스트 — 작가 의도일 가능성이 높은 어휘는 invalid로 분류하지 않는다.
#     "외래어"가 명시적으로 포함되어 "키메시지" 같은 사전 미등재 외래어가
#     강제로 confidence="low"로 떨어지지 않도록 한다.
_ALLOWED_REGISTERS = {"표준어", "외래어", "전문어", "신어", "신조어"}

# 의심 처리 시 사용자에게 보여줄 register 라벨
_NOTABLE_REGISTERS = {"방언", "북한어", "옛말", "일본어식", "비표준어"}


# ══════════════════════════════════════════════════════
# ▌커넥션 (스레드 로컬)
# ══════════════════════════════════════════════════════

_local = threading.local()


def _get_conn() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    if not hasattr(_local, "conn") or _local.conn is None:
        try:
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            conn.execute("PRAGMA cache_size = -8192")
            conn.execute("PRAGMA mmap_size = 67108864")
            _local.conn = conn
        except Exception:
            _local.conn = None
    return _local.conn


# ══════════════════════════════════════════════════════
# ▌단어 조회 — 개별 (stem/prefix fallback 포함)
# ══════════════════════════════════════════════════════

def lookup_word(word: str) -> dict:
    """
    단어가 사전에 등재되어 있는지 확인.
    반환: {"exists": bool, "source": str, "register": str}
    """
    conn = _get_conn()
    if conn is None:
        return {"exists": True, "source": "stdict", "register": ""}   # DB 없으면 모두 유효 처리

    clean = re.sub(r"[^가-힣]", "", word).strip()
    if len(clean) < 2:
        return {"exists": True, "source": "stdict", "register": ""}

    try:
        row = conn.execute("SELECT source, register FROM words WHERE word=? LIMIT 1", (clean,)).fetchone()
        if row: return {"exists": True, "source": row[0], "register": row[1] or ""}

        # 동형이의어 번호 접미사 처리 — 표제어가 번호와 함께만 저장된 경우.
        #   예: '등장'은 bare로 없고 '등장01'(登場)·'등장02'(等張)로만 등재됨.
        #   GLOB '[0-9][0-9]'는 접두 'clean'으로 인덱스를 타며 숫자 2자리만 허용해
        #   '등장국'(다른 단어) 같은 오매칭을 피한다.
        row = conn.execute(
            "SELECT source, register FROM words WHERE word GLOB ? LIMIT 1",
            (clean + "[0-9][0-9]",)).fetchone()
        if row: return {"exists": True, "source": row[0], "register": row[1] or ""}

        stem = _JOSA_RE.sub("", clean).strip()
        if stem and stem != clean:
            row = conn.execute("SELECT source, register FROM words WHERE word=? LIMIT 1", (stem,)).fetchone()
            if row: return {"exists": True, "source": row[0], "register": row[1] or ""}

        # ⚠ 3글자 접두 LIKE 폴백 제거(2026-06-16): `WHERE word LIKE clean[:3]+'%'`는
        #   "앞 3글자가 같은 사전 단어가 하나라도 있으면 등재"로 처리해, **4번째 글자
        #   이후의 오타를 통째로 마스킹**했다(예: '상담채녈'→'상담채%'에 걸려 미탐,
        #   '채널링크'→'채널링킄'도 '채널링%'에 흡수). 치명적 미탐의 구조적 원인이라
        #   삭제. 등재어의 정당한 활용형/복합형 인식은 형태소 분석(core.morph의
        #   is_known_form/has_known_inflection)이 정밀하게 담당하므로 기능 손실 없음.
        return {"exists": False}
    except sqlite3.Error:
        return {"exists": True, "source": "stdict", "register": ""}


# ══════════════════════════════════════════════════════
# ▌배치 조회 — S4 성능 개선
# ══════════════════════════════════════════════════════

def batch_lookup_existence(words: set, chunk_size: int = 500) -> dict:
    """단어 집합을 한 번에 조회 (정확 일치만).

    반환: {word: {"exists": bool, "source": str, "register": str}}
    개별 lookup_word()는 stem/prefix까지 시도하지만, 배치는 정확 일치만 수행한다.
    배치에서 못 찾은 단어는 호출 측에서 lookup_word()로 재시도하면 된다.
    """
    conn = _get_conn()
    if conn is None:
        return {w: {"exists": True, "source": "stdict", "register": ""} for w in words}

    result = {}
    word_list = list(words)
    for i in range(0, len(word_list), chunk_size):
        chunk = word_list[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"SELECT word, source, register FROM words WHERE word IN ({placeholders})",
                chunk
            ).fetchall()
            # SQL은 같은 word에 여러 row를 반환할 수 있음 — 첫 번째만 채택
            found = {}
            for row in rows:
                if row[0] not in found:
                    found[row[0]] = {"source": row[1], "register": row[2] or ""}
            for w in chunk:
                if w in found:
                    result[w] = {"exists": True, **found[w]}
                # 미발견은 결과에 포함하지 않음 → 호출 측에서 fallback 결정
        except sqlite3.Error:
            # 쿼리 실패 시 안전하게 모두 "존재" 처리 (false-positive 방지)
            for w in chunk:
                result[w] = {"exists": True, "source": "stdict", "register": ""}
    return result


def lookup_norm(word: str) -> Optional[str]:
    """비표준 표기의 규범형을 반환(없으면 None).

    우리말샘 '규범 표기' 매핑(norm_map 테이블, build_norm_map.py로 적재)을 조회한다.
    예: '컨텐츠'→'콘텐츠', '수퍼마켓'→'슈퍼마켓'. 테이블이 없는 구버전 DB에선
    조용히 None을 반환(graceful).
    """
    conn = _get_conn()
    if conn is None:
        return None
    clean = re.sub(r"[^가-힣]", "", word).strip()
    if len(clean) < 2:
        return None
    try:
        row = conn.execute("SELECT norm FROM norm_map WHERE nonstd=? LIMIT 1", (clean,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None   # norm_map 테이블 부재/오류 → 기능 비활성


def batch_lookup_norm(words: set, chunk_size: int = 500) -> dict:
    """비표준→규범 매핑을 배치 조회. 반환: {nonstd: norm} (norm_map에 있는 것만).

    norm_map 테이블이 없으면 {} (graceful — 기존 동작 유지).
    """
    conn = _get_conn()
    if conn is None:
        return {}
    out: dict = {}
    wl = [w for w in words if len(w) >= 2]
    for i in range(0, len(wl), chunk_size):
        chunk = wl[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"SELECT nonstd, norm FROM norm_map WHERE nonstd IN ({placeholders})",
                chunk,
            ).fetchall()
        except sqlite3.Error:
            return out   # 테이블 부재 → 빈 결과로 비활성
        for r in rows:
            out[r[0]] = r[1]
    return out


# ── norm_map 동형이의어 가드 ───────────────────────────────────────────────
# norm_map은 우리말샘 '⇒규범 표기는 X이다'에서 자동 추출한다(build_norm_map.py). 빌드타임
#   _VARIANT_SENSE 가드는 표기가 '실뜻'을 가지면 제외하지만, **현대 고유명사/흔한 단어가 우리말샘
#   표제어로는 없는** 경우 빠져나간다 → 치명적 오교정 2부류(사용자 보고):
#   (1) 동형이의 고유명사: '구글'(Google) → 우리말샘 옛말 '귀글'(句글). 매핑이 'Google'을 '귀글'로.
#   (2) 흔한 단어 + 조사 = 희귀 방언 표제어: '동기와'(動機+와) → 새 이름 방언 '너새'.
#       '광주만'(光州+만="광주만") → 중국 만(灣) '광저우만', '한참에'(한참+에) → '한꺼번에' 등.
#   둘 다 빈출 강등(③ [5.7])으로 자동적용은 막지만 **검수 카드 자체가 오답**이라 노출도 부적합.
#   → 결정론 가드로 통째 차단. (1)은 큐레이션 블록리스트, (2)는 'base(=등재 표제어)+명확 조사' 구조.
_NORM_BLOCKLIST = frozenset({"구글"})   # 현대 고유명사인데 옛말 규범표기로 잘못 매핑되는 동형이의어
# 단독으로 단어가 될 수 없는 '명확한' 조사만(이/가/은 등 모호한 1글자 제외 — 곰방이·청실로 같은
#   진짜 방언 표제어를 살린다). 이 조사로 끝나고 base가 등재 표제어면 'X+조사' 오매칭으로 본다.
_NORM_UNAMBIG_JOSA = frozenset({
    "은", "는", "을", "를", "와", "과", "의", "에", "에서", "에게",
    "으로", "로", "도", "만", "까지", "부터", "와의", "과의",
})


def is_homograph_norm_key(key: str) -> bool:
    """norm_map 키 `key`가 '동형이의어 오매칭'이라 적용하면 안 되는가?

    True인 두 경우:
      · 블록리스트(구글 등) — 현대 고유명사가 옛말 규범표기로 잘못 매핑된 표기.
      · `key = [사전 등재 표제어 base] + [명확한 조사]` — 흔한 '단어+조사'가 우연히 희귀
        방언 표제어와 같은 표기라 오매칭되는 구조('동기와'=동기+와, '광주만'=광주+만).
    형태소(strip_josa)·사전(lookup_word) 미가용 시 보수적으로 False(기존 동작 유지·graceful).
    """
    if not key:
        return False
    if key in _NORM_BLOCKLIST:
        return True
    try:
        from core import morph as _morph
        base = _morph.strip_josa(key) if _morph.available() else None
    except Exception:
        base = None
    if not base or base == key or len(base) < 2:
        return False
    josa = key[len(base):]
    if josa not in _NORM_UNAMBIG_JOSA:
        return False
    try:
        return lookup_word(base)["exists"]
    except Exception:
        return False


def is_verb_inflection_homograph(key: str, eojeol: str, text: str) -> bool:
    """norm_map 키 `key`가 이 문서에서 '표준 용언의 활용형'과 동형이라 치환을 보류해야 하는가?

    '나올'("답변이 나올 수 있다" = 나오+ㄹ 관형형)이 명사 표제어 '나올(羅兀)→너울'로
    오매칭되는 부류의 문맥 가드(2026-07-14 사용자 보고). norm_map 12,730건 전수 스캔에서
    같은 위험군 **377건** 확인('짚고'→'집고'(문제를 짚고 넘어가다), '할래'→'까지' 등).
    기존 가드(_VARIANT_SENSE·is_homograph_norm_key·is_registered_compound_component)는
    전부 **표제어 수준**이라 활용형 동형이의는 사각이었다. 3조건 전부 충족 시 True:
      ① key가 어절 전체와 일치 — '찌게를'처럼 조사 딸린 매칭은 명사 사용 신호라 미적용
        (된장 찌게를→찌개를 카드 보존).
      ② 문맥 kiwi 분석에서 등장이 용언 활용형(V* + ETM/EF/EC/EP, 명사형 ETN 제외)으로 읽힘
        (core.morph.verb_inflection_lemma — 등장 하나라도 용언이면 일괄 치환이 훼손이므로 보류).
      ③ 복원 기본형이 norm_map에 **없음** — 기본형 자체가 비표준 용언(치루다·채이다·쳐지다)이면
        그 카드는 정당하므로 유지.
    억제 방향 트레이드오프(수용): 조사 없는 통어절 '된장 찌게'는 kiwi가 용언으로 읽어 미탐 —
    과교정 0 원칙 우선. 형태소 미가용 시 False(graceful — 기존 동작 유지).
    """
    if not key or key != eojeol:
        return False
    try:
        from core import morph as _morph
        lemma = _morph.verb_inflection_lemma(text, eojeol) if _morph.available() else None
    except Exception:
        lemma = None
    if not lemma:
        return False
    return lookup_norm(lemma) is None


def _is_exact_headword(word: str) -> bool:
    """`word`가 사전에 **정확히** 등재된 표제어인가(조사 stem 폴백 없음).

    lookup_word()는 조사형(컨텐츠'를')을 stem으로 되돌려 exists=True로 보므로 복합어
    성분 판정엔 부적합하다. 여기선 정확 일치 + 동형이의 번호접미(등장01) 변형만 본다.
    """
    conn = _get_conn()
    if conn is None:
        return False
    clean = re.sub(r"[^가-힣]", "", word).strip()
    if len(clean) < 2:
        return False
    try:
        if conn.execute("SELECT 1 FROM words WHERE word=? LIMIT 1", (clean,)).fetchone():
            return True
        if conn.execute("SELECT 1 FROM words WHERE word GLOB ? LIMIT 1",
                        (clean + "[0-9][0-9]",)).fetchone():
            return True
    except sqlite3.Error:
        return False
    return False


def is_exact_noun_headword(word: str) -> bool:
    """`word`가 pos='명사'/'대명사'로 **정확히** 등재된 표제어인가(조사 stem 폴백 없음).

    띄어쓰기 백스톱(core.morph.find_spacing_suggestions)이 kiwi 오분석으로 등재 명사
    '이중(二重)'을 관형사+의존명사(이/MM+중/NNB)로 보고 '이중과제'→'이 중 과제'로 쪼개는 것을
    막는 데 쓴다. '한개'(부사)·'그곳'(대명사는 kiwi가 NP로 통합해 무영향) 등 비명사는 제외한다.
    """
    conn = _get_conn()
    if conn is None:
        return False
    clean = re.sub(r"[^가-힣]", "", word).strip()
    if len(clean) < 2:
        return False
    try:
        if conn.execute(
                "SELECT 1 FROM words WHERE word=? AND pos IN ('명사','대명사') LIMIT 1",
                (clean,)).fetchone():
            return True
        if conn.execute(
                "SELECT 1 FROM words WHERE word GLOB ? AND pos IN ('명사','대명사') LIMIT 1",
                (clean + "[0-9][0-9]",)).fetchone():
            return True
    except sqlite3.Error:
        return False
    return False


def is_registered_compound_component(key: str, eojeols) -> bool:
    """`key`가 '이 문서에 등장하는 더 긴 등재 표제어'의 부분 성분인가?

    norm_map은 우리말샘 redirect 자동추출이라 **외래어 복합어의 성분**을 떼어 동형이의
    옛말/방언으로 오매핑하는 사각이 있다. 예) '티어'→'테어'(우리말샘 옛말)로 잡히지만,
    같은 문서에 **등재 복합어 '톱티어'(top-tier)**가 있으면 그 '티어'는 tier 성분이므로
    규범표기 치환을 보류해야 한다(사용자 보고 2026-07-01).

    is_homograph_norm_key(문맥 무관·norm_map 속성)와 달리 이 가드는 **문서 문맥**을 본다:
    문서의 통 한글런 집합 `eojeols`(re.findall r"[가-힣]+")에 `key`를 부분으로 포함하는
    **더 긴 등재 표제어**가 있으면 True. '로동청년→노동청년'(두음)·'세수그릇→세숫그릇'(사이
    시옷)류는 문서에 그것을 포함하는 상위 등재어가 없어 안전하다(억제 방향·정상 순화 보존).
    ⚠ 정확 표제어(`_is_exact_headword`)로만 판정 — lookup_word의 조사 stem 폴백을 쓰면
    '컨텐츠를'을 '컨텐츠'로 되돌려 정상 순화(컨텐츠→콘텐츠)까지 잘못 억제한다.
    사전/형태소 미가용 시 False(graceful).
    """
    if not key or len(key) < 2:
        return False
    try:
        from core import morph as _morph
        _strip = _morph.strip_josa if _morph.available() else None
    except Exception:
        _strip = None
    klen = len(key)
    for e in eojeols:
        if len(e) <= klen or key not in e:
            continue
        try:
            if _is_exact_headword(e):
                return True
            if _strip:
                b = _strip(e)
                if b and b != e and len(b) > klen and key in b and _is_exact_headword(b):
                    return True
        except Exception:
            continue
    return False


def db_status() -> dict:
    if not DB_PATH.exists():
        return {"available": False, "path": str(DB_PATH)}
    conn = _get_conn()
    if conn is None:
        return {"available": False, "path": str(DB_PATH)}
    try:
        count = conn.execute("SELECT value FROM meta WHERE key='entry_count'").fetchone()
        built = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        size  = DB_PATH.stat().st_size / 1_048_576
        return {
            "available":   True,
            "path":        str(DB_PATH),
            "entry_count": int(count[0]) if count else 0,
            "built_at":    built[0] if built else "unknown",
            "size_mb":     round(size, 1),
        }
    except Exception:
        return {"available": False, "path": str(DB_PATH)}


# ══════════════════════════════════════════════════════
# ▌형태소 분석 연동 (활용형 → 기본형 보정)
# ══════════════════════════════════════════════════════
# 사전은 표제어(겪다)만 담고 활용형(겪고)은 없다. 표면형을 그대로 조회하면
# 멀쩡한 활용형이 "미등재"로 잡힌다. core.morph(형태소 분석)로 기본형을
# 복원해 재대조하면 이 거짓 미등재를 제거할 수 있다. (kiwipiepy 미설치 시 무영향)

def data_version() -> Optional[str]:
    """실제로 로드된 사전 데이터의 버전(YYYY.MM). 없으면 None.

    ⚠ version.py의 DATA_VERSION은 '이 빌드가 패키징하려던' 값이고, 이 함수는
      '지금 디스크에 있는' 값이다. 데이터만 따로 업데이트되는 구조라 둘이 어긋날 수
      있으므로 UI·업데이터는 **이쪽을 우선**한다.
    """
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
        if row and row[0]:
            return str(row[0])
        # 구 DB 호환 — data_version이 없으면 built_at(YYYY-MM-DD)에서 YYYY.MM을 만든다.
        row = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        if row and row[0] and len(str(row[0])) >= 7:
            return str(row[0])[:7].replace("-", ".")
    except sqlite3.Error:
        pass
    return None


def _exists_for_morph(w: str) -> bool:
    """형태소 기본형 콜백 — 사전 등재 여부."""
    return lookup_word(w)["exists"]


def _get_morph():
    """core.morph 모듈 반환(사용 가능할 때만). 미설치/실패 시 None."""
    try:
        from core import morph
        return morph if morph.available() else None
    except Exception:
        return None


def _get_userdict():
    """core.userdict 모듈 반환(사용 가능할 때만). 미존재/실패 시 None.

    조직 예외(무교정 화이트리스트) 조회용 — 재검증 ②에서 조직 승인 표제어를
    confidence=low로 강등하지 않기 위함(설계: docs/userdict-layer-architecture.md §5 E).
    userdict.db가 없거나 빈 스냅샷이면 None/빈 결과로 graceful 비활성.
    """
    try:
        from core import userdict
        return userdict if userdict.available() else None
    except Exception:
        return None


def is_valid_word(word: str, morph_mod=None) -> bool:
    """word가 '등재 표제어' 또는 '등재어의 활용/복합형'이면 True.

    1차: 직접 사전 조회(표면형/어간/접두). 2차: 형태소 기본형 복원 후 재조회.
    """
    if lookup_word(word)["exists"]:
        return True
    m = morph_mod if morph_mod is not None else _get_morph()
    if m is not None and m.is_known_form(word, _exists_for_morph):
        return True
    return False


def is_likely_typo(word: str) -> bool:
    """미등재 표면형 word가 '오타로 보이는가' — 사전 안전망 노이즈 필터.

    탐지(extract_suspicious_words)가 잡은 미등재어 중 대부분은 고유명사·외래어·
    정상 복합어(오탐)다. AI가 안 고친 미등재어를 검수 카드로 띄울 때, 진짜 오타만
    남기기 위한 보수적 필터:

      · 순수 한글이 아니면 False — 영문/일본어/숫자/괄호/구두점 포함 시 외래어·코드·
        고유명사 결합·추출 잡음일 가능성이 높다(예: '콘텐츠(키메세지)', '1인가구', 'KOBACO').
      · 형태소 분석상 고유명사(NNP)이거나 등재 명사 2개 이상의 정상 복합어면 False
        (예: 인명·지명, '지역사회보장협의체'). → morph.looks_like_typo.
      · 그 외(미등재 단일 내용 명사 등)는 오타 가능성 → True (예: '상담채녈'·'애니메니션').

    형태소 미설치 시 한글 여부만으로 판단(관대 → 노출 유지, 안전 측).
    """
    if not word or re.search(r"[^가-힣]", word):
        return False
    m = _get_morph()
    if m is None:
        return True
    return m.looks_like_typo(word, _exists_for_morph)


def is_registered_online(word: str) -> bool:
    """로컬 DB가 놓친 word가 **라이브 우리말샘**에는 있는가? (캐싱 폴백).

    로컬 stdict.db는 spellcheck-ko 스냅샷이라 실재하는 단어(대행사·돌봄)가
    빠질 수 있다. 이 함수로 의심어를 라이브 사전에 한 번 더 대조해 거짓
    '검수 필요' 플래그를 줄인다. 어절 끝 조사를 떼고 base로 조회한다.

    반환: True(우리말샘 등재 확인) / False(미등재 또는 확인 불가).
    키 없음/오프라인/오류 시 False(=확인 불가 → 호출 측은 기존대로 플래그).
    """
    clean = re.sub(r"[^가-힣]", "", word or "")
    base = _JOSA_RE.sub("", clean).strip()
    if len(base) < 2:
        return False
    try:
        from nikl_api import exists_online
    except Exception:
        return False
    return exists_online(base) is True


# ── 온용어(전문용어) 폴백 ─────────────────────────────────────────────────
# 온용어(국립국어원)는 36개 기관 53개 용어집 약 120만 전문용어의 통합체다. 우리 stdict.db
# (표준국어대사전+우리말샘)엔 기관 전문용어가 거의 없어, 법령·정보통신·의학·전력 용어가
# 거짓 '미등재'로 잡혀 AI 과교정을 유발해 왔다. 이 폴백이 그 입력을 줄인다.
# 설계도: docs/onterm-integration-design.md

def _norm_pair_components(base: str) -> bool:
    """`base`가 규범표기 교정 대상을 **성분으로 품고** 있는가?

    ⚠ 온용어 화이트리스트의 **거부권 판정**이다. 온용어에는 비표준 표기가 등재돼 있다
    (실측: '컨텐츠' 조회 → '디지털^컨텐츠' 등재, 국방 과학 기술 용어 사전). '컨텐츠'는
    우리 norm_map 키지만 '디지털컨텐츠'는 아니라서, 온용어가 이 어절을 통째로 화이트리스트에
    넣으면 **비표준 표기를 정상으로 승인**하게 된다. 온용어(용어집 수록 사실)는 규범 판단을
    이길 수 없다.

    판정: base의 모든 부분문자열(2글자 이상)이 norm_map/userdict_pairs 키인지, 또는
    spelling_pairs의 비표준 어간을 포함하는지. 하나라도 걸리면 True(=화이트리스트 거부).

    과잉 판정은 **안전한 방향**이다 — 거부되면 그냥 현행 동작(검수 카드로 노출)으로
    돌아갈 뿐 새 오류를 만들지 않는다. 반대로 놓치면 비표준 표기가 조용히 승인된다.
    """
    if len(base) < 2:
        return False

    # ① spelling_pairs — 키 자체가 '부분문자열 비표준 어간'이라 직접 포함 검사.
    try:
        from core.spelling_pairs import _STEM_PAIRS
        for stem in _STEM_PAIRS:
            if stem in base:
                return True
    except Exception:
        pass

    # ② norm_map / userdict_pairs — 표제어 수준 키라 base의 부분문자열을 배치 조회.
    subs = {base[i:j]
            for i in range(len(base))
            for j in range(i + 2, len(base) + 1)}
    try:
        if batch_lookup_norm(subs):
            return True
    except Exception:
        pass
    try:
        from core import userdict
        if userdict.available() and userdict.batch_lookup_pair(subs):
            return True
    except Exception:
        pass
    return False


def is_registered_onterm(word: str) -> bool:
    """로컬 DB·우리말샘이 놓친 word가 **온용어**에 전문용어로 등재되어 있는가?

    반환: True(온용어 등재 확인 → 오타 아님) / False(미등재·확인 불가·거부권 발동).
    키 없음/오프라인/오류 시 False(=확인 불가 → 호출 측은 기존대로 플래그).
    """
    clean = re.sub(r"[^가-힣]", "", word or "")
    base = _JOSA_RE.sub("", clean).strip()
    if len(base) < 2:
        return False
    # ⚠ 규범표기 거부권 — 온용어 조회보다 **먼저** 본다(불필요한 네트워크 호출도 절약).
    if _norm_pair_components(base):
        return False
    try:
        from onterm_api import exists_online as _onterm_exists
    except Exception:
        return False
    return _onterm_exists(base) is True


# ══════════════════════════════════════════════════════
# ▌교정 검증기
# ══════════════════════════════════════════════════════

class KoreanDictValidator:
    """표준국어대사전 로컬 SQLite 기반 교정 제안 검증"""

    @property
    def available(self) -> bool:
        return DB_PATH.exists()

    def extract_suspicious_words(self, text: str, stop_event=None) -> list:
        """
        원문에서 띄어쓰기 기준으로 단어를 분리하여,
        사전에 없거나 비표준 등재인 의심 단어 목록을 추출합니다.

        S4: 단어 집합을 한 번에 조회해 성능을 10배 이상 개선.
        S5: 외래어/전문어/신어는 작가 의도이므로 의심 목록에서 제외.

        빈도 기반 필터는 제거됨 — 5번 반복되는 오탈자(예: "케릭터")가
        "작가 의도"로 잘못 분류되어 AI 검토에서 빠지는 부작용이 있었음.
        AI가 직접 판단하도록 모든 후보를 전달.
        """
        if not self.available:
            return []

        # NFC 정규화 — 일부 HWP 텍스트는 한글이 NFD(자모 분리)/혼합으로 들어와, 아래 clean
        #   추출(`[^가-힣]` 제거)이 결합 자모를 떨궈 등재어를 깨진 부분열로 만든다(거짓 미탐).
        #   호출부가 정규화하지 않아도 사전 스크리닝이 스스로 방어한다(설계: 사전=항상-on 인프라).
        text = unicodedata.normalize("NFC", text)

        # 본문에서 유니크 한글 어휘 집합 구축 (clean → actual_word) + 빈도 카운트(통계용)
        clean_to_actual: dict = {}
        freq: dict = {}
        for w in text.split():
            if stop_event and stop_event.is_set():
                return []
            # ⚠ 토큰 안의 **한글 런을 각각 독립 검사**한다. 과거엔 `re.sub(r"[^가-힣]","",w)`로
            #   토큰의 모든 한글을 **이어붙여** 검사했는데, 문서가 낱말을 문장부호(가운뎃점 ·,
            #   괄호, 붙임표 –, +, ∼ 등)로 **띄어쓰기 없이** 연결하면('비자·정착', '대구·광주·부산',
            #   '지자체(고베시') 사전에 없는 합성열('비자정착'…)이 만들어져 **등재 낱말을 거짓
            #   미등재로 플래그**했다(사용자 30.hwp 실측 2026-07-01: '비자·정착'·'트레이닝–채용–
            #   온보딩' 등 다수). 런 단위 검사로 각 낱말이 제 표제어로 검증돼 거짓 플래그가 사라진다.
            for m in re.finditer(r"[가-힣]+", w):
                clean = m.group()
                if len(clean) < 2:
                    continue
                # ⚠ **숫자에 인접한 한글런은 제외**한다. 문장부호는 낱말 '구분자'지만 숫자는
                #   낱말의 '일부'(수사 융합)라, '2차전지'의 런 '차전지'는 독립 낱말이 아닌 조각이다.
                #   이 조각을 의심 단어로 넘기면 AI가 '차전지'→'이차전지'로 오교정한다(사용자 보고
                #   2026-07-01: "'2'를 제외한 '차전지'를 교정대상으로 탐지"). 런 바로 앞/뒤 문자가
                #   숫자면 수사-융합 표현의 일부이므로 스킵('세대'처럼 조각이 등재어여도 무해).
                i0, i1 = m.start(), m.end()
                if (i0 > 0 and w[i0 - 1].isdigit()) or (i1 < len(w) and w[i1].isdigit()):
                    continue
                freq[clean] = freq.get(clean, 0) + 1
                if clean not in clean_to_actual:
                    clean_to_actual[clean] = clean

        if not clean_to_actual:
            return []

        # 진단용 통계 — 빈도 분포만 기록 (필터링은 안 함)
        self.last_stats = {
            "total_unique":     len(clean_to_actual),
            "high_freq_count":  sum(1 for cnt in freq.values() if cnt >= 3),
        }

        # 2. 배치 SQL — 정확 일치만 (가장 빠름)
        batch = batch_lookup_existence(set(clean_to_actual.keys()))

        # 3. 미발견 단어에 대해서만 stem/prefix fallback 수행
        suspicious: list = []
        seen: set = set()
        morph_mod = _get_morph()   # 활용형 거짓 미등재 제거용 (없으면 None)

        for clean, actual in clean_to_actual.items():
            if stop_event and stop_event.is_set():
                break
            res = batch.get(clean)
            if res is None:
                res = lookup_word(clean)  # stem/prefix까지 시도

            if not res["exists"]:
                # 형태소 분석: 등재어의 활용형/복합형이면 미등재가 아니다
                #   (예: "겪고"→"겪다", "먹었습니다"→"먹다"는 의심 대상에서 제외)
                if morph_mod is not None and morph_mod.is_known_form(clean, _exists_for_morph):
                    continue
                # 어느 사전에도 없음 — 오탈자 또는 신조어 외래어 가능성
                key = f"{actual}::missing"
                if key not in seen:
                    seen.add(key)
                    suspicious.append(f"{actual} (어느 사전에도 없음 — 오탈자 가능성)")
            else:
                # opendict 등재 — register가 _NOTABLE인 경우에만 의심 처리
                if res["source"] == "opendict":
                    reg = res.get("register", "")
                    if reg in _NOTABLE_REGISTERS:
                        key = f"{actual}::{reg}"
                        if key not in seen:
                            seen.add(key)
                            suspicious.append(f"{actual} (우리말샘에 [{reg}]으로 등재됨)")
                    # 외래어/전문어/표준어/신어/빈 register는 정상 처리 (의심 X)

        return suspicious

    def extract_flags(self, text: str, stop_event=None) -> list:
        """사전 전용 '검수 모드'용 — 미등재/비표준 어휘를 구조화해 반환.

        [{"word": 어휘, "reason": 사유}] 형태. extract_suspicious_words가 만든
        문자열("어휘 (사유)")을 분해해 (word, reason)로 돌려준다. 파싱 규칙을
        포맷 정의부(여기) 옆에 두어 호출부가 문자열 형식에 의존하지 않게 한다.
        """
        flags = []
        for s in self.extract_suspicious_words(text, stop_event=stop_event):
            if " (" in s:
                word, reason = s.split(" (", 1)
                flags.append({"word": word.strip(), "reason": reason.rstrip(")").strip()})
            else:
                flags.append({"word": s.strip(), "reason": "표제어 확인 필요"})
        return flags

    def validate(self, corrections: list, stop_event=None) -> list:
        """AI 교정 제안을 사전 기준으로 재검증.

        S5: 외래어/전문어 register는 invalid로 분류하지 않는다.
            "키 메시지", "콘텐츠" 같은 정상 외래어가 confidence="low"로
            잘못 격하되는 false positive를 방지한다.
        """
        if not self.available:
            return corrections

        result = []
        morph_mod = _get_morph()   # 활용형 교정을 거짓 저신뢰로 떨구지 않기 위함
        ud_mod = _get_userdict()   # 조직 예외(무교정 화이트리스트) — 예외 표제어는 강등 제외

        for item in corrections:
            if stop_event and stop_event.is_set():
                result.extend(corrections[len(result):])
                break

            if item.source == "ai_polish":   # 윤문은 문장 단위 → 건너뜀
                result.append(item)
                continue

            # 델타 기준 — 이 교정이 '새로 만들거나 바꾼' 어절만 검증한다.
            #   원문에 이미 있던 토큰(예: 미등재 외래어 '모즈얀')은 이 교정의 책임이
            #   아니므로 페널티에서 제외한다. 조사 중복 삭제·띄어쓰기처럼 인접 미등재어를
            #   건드리지 않는 순수 문법 교정이, 잔존 미등재 토큰 때문에 통째로 저신뢰가
            #   되는 오판을 막는다(설계 ②의 'AI 목표어가 비표준이면 low' 의도에 충실).
            orig_clean = {re.sub(r"[^가-힣]", "", w) for w in item.original.split()}
            orig_clean.discard("")

            invalid = []
            for w in item.corrected.split():
                clean = re.sub(r"[^가-힣]", "", w)
                if len(clean) < 2:
                    continue
                if clean in orig_clean:
                    continue   # 원문에 이미 있던 토큰 → 이 교정이 만든 게 아님 → 검증 제외
                # 조직 예외(무교정 화이트리스트) — 조직이 승인한 표기는 비표준·비등재라도
                #   저신뢰로 강등하지 않는다(설계 E: 사내 통일 용어). DB 부재 시 ud_mod=None.
                if ud_mod is not None and ud_mod.is_exception(clean, "all"):
                    continue
                res = lookup_word(clean)
                if not res["exists"]:
                    # 형태소 기본형이 등재어면 정상 활용형 → 의심 아님
                    #   (예: AI가 "먹었슺니다"→"먹었습니다"로 고친 경우 저신뢰 오판 방지)
                    if morph_mod is not None and morph_mod.is_known_form(clean, _exists_for_morph):
                        continue
                    # 어느 사전에도 없음 — 진짜 의심
                    invalid.append(w)
                elif res["source"] == "opendict":
                    reg = res.get("register", "")
                    # S5: 허용 register는 통과, 그 외(방언/북한어 등)만 invalidate
                    if reg and reg not in _ALLOWED_REGISTERS and reg in _NOTABLE_REGISTERS:
                        invalid.append(w)

            if invalid:
                item = replace(item, color=HL_UNVERIFIED, confidence="low")

            result.append(item)

        return result


# ══════════════════════════════════════════════════════
# ▌독립 실행 테스트
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    print("표준국어대사전 DB 상태:")
    s = db_status()
    for k, v in s.items():
        print(f"  {k}: {v}")

    if s["available"]:
        tests = ["사과", "나무", "교정교열", "띄어쓰기", "먹었습니다", "키메시지", "콘텐츠"]
        print("\n단어 검증 테스트:")
        for w in tests:
            res = lookup_word(w)
            if res["exists"]:
                print(f"  ✔ {w} (출처: {res['source']}, 속성: {res['register']})")
            else:
                print(f"  ✖ {w} (미등재)")
