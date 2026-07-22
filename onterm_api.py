"""
onterm_api.py — 온용어(국립국어원 전문 분야 용어지식 플랫폼) OpenAPI 캐싱 폴백
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/onterm-integration-design.md · 형제 모듈: nikl_api.py(우리말샘)

로컬 stdict.db(표준국어대사전+우리말샘)에 없는 **기관 전문용어**(법령·정보통신·의학·
전력·국방·건설…)를 온용어로 확인해, 거짓 '검수 필요' 카드를 줄이는 보강 레이어.
온용어는 36개 국가·공공기관의 53개 용어집 약 120만 용어를 통합한 자료다.

설계 원칙(nikl_api.py와 동일):
  · **폴백 전용** — 로컬+형태소+우리말샘이 모두 '미등재'로 본 의심어에만 호출(전 어휘 X).
  · **영구 캐시** — 한 단어 평생 1회 조회. data/api_cache.db의 `onterm_cache` 테이블에
    저장(우리말샘 결과 `cache`와 **분리**). 쓸수록 자라는 로컬 전문용어 사전이 된다.
  · **graceful** — 키 없음/오프라인/오류 시 None → 호출 측은 기존 동작 유지.
  · GUI-agnostic (PySide6 미사용).

★ 실측 효과(2026-07-22, 실문서 3건 504K자): 검수 대상 153건 중 12건 구제(7.8%),
  진짜 오탈자 오구제 0건. 도메인 편차 큼(IT·법 17.6% / 사회복지 6.7% / 스타트업 4.1%).

⚠ 이 API는 **공식 문서와 실제 동작이 다르다**. 아래는 전부 실측으로 확인한 것이며,
  하나라도 어기면 조용히 오작동한다(설계도 §2에 전체 표):

  1. `start`/`num`을 생략하면 JSON이 아니라 **HTML 페이지**가 온다 → 항상 명시.
  2. 성공 시 `return_object`는 **list**, 오류·무결과 시 **str** → isinstance 분기 필수.
  3. 성공 `returnCode`는 **정수 1**, 오류는 **문자열 "100"** → str()로 감싸 비교.
  4. 결과 0건 → `return_object == "검색 결과가 없습니다."` = **확정 미등재**(오류 아님).
  5. 무효 키도 `100 "시스템 에러"`로 온다(020 아님) → 키 오류/요청 오류 구별 불가.
  6. `method=exact`가 **없고** 검색이 **부분 일치**다('전력'→'열기전력'도 매칭)
     → 응답 word를 정규화해 **직접 정확일치 판정 필수**. 기본 정렬 wt는 정확일치가 1위.
  7. 표제어에 구 경계 마커 `^`가 붙는다('스마트^그리드') → 정규화로 제거.
  8. 조사가 붙으면 0건('스마트 그리드를') → base로 조회. 공백은 무시되므로 제거 무해.

온용어 OpenAPI: https://kli.korean.go.kr/term/bbs/indexOpenApiInfo.do (회원가입 후 인증키)
  GET /term/api/search.do?key=KEY&apiSearchWord=단어&start=1&num=30
  일일 제한 5만 건(공식) — 문서당 수십 건이므로 사실상 무제한.
"""

import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional

_API_URL = "https://kli.korean.go.kr/term/api/search.do"
_TIMEOUT = 4.0          # 단어당 네트워크 타임아웃(초) — 스크리닝 지연 억제
_MIN_INTERVAL = 0.05    # 연속 호출 최소 간격(초) — API 예의
_NUM = "30"             # 부분일치 응답 중 정확일치를 찾기 위한 표본 크기(정렬 wt=정확도순)

_lock = threading.Lock()
_net_lock = threading.Lock()   # _last_call 보호 — 병렬 조회(워커 ThreadPool) 대비
_mem: dict = {}         # 세션 인메모리 캐시 {word: bool}
_conn: Optional[sqlite3.Connection] = None
_last_call = 0.0
_key_cache: Optional[str] = None
_disabled = False       # 키 없음/requests 없음/일일한도 초과 → 영구 비활성(재시도 안 함)
_quota_hit = False      # 일일 제한(022) 도달 여부 — 진단·로그용


def _norm(w: str) -> str:
    """비교용 정규화 — NFC 후 한글만, NIKL 음절/구 경계 마커(-, ^) 제거.

    ⚠ `^` 제거가 핵심이다. 온용어 표제어는 구 경계를 '스마트^그리드'로 표기하므로
      그대로 비교하면 영원히 매칭되지 않는다.
    """
    w = unicodedata.normalize("NFC", (w or "")).replace("-", "").replace("^", "")
    return re.sub(r"[^가-힣]", "", w)


def _cache_path() -> Path:
    """api_cache.db 경로 — nikl_api와 **같은 파일**을 쓰되 테이블만 분리한다.

    ⚠ 빌드본에선 쓰기 가능한 %LOCALAPPDATA% 아래로 간다(core.config_loader.user_data_dir).
      번들/설치 폴더는 쓰기 불가일 수 있어 캐시가 조용히 죽는다.
    """
    try:
        from core.config_loader import user_data_dir
        return user_data_dir() / "api_cache.db"
    except Exception:
        return Path(__file__).parent / "data" / "api_cache.db"


def _get_conn() -> Optional[sqlite3.Connection]:
    global _conn
    if _conn is None:
        try:
            p = _cache_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(p), check_same_thread=False)
            # ⚠ 우리말샘 결과(`cache`)와 **절대 섞지 않는다** — 두 사전의 등재 범위가
            #   다르므로 한쪽 결과를 다른 쪽으로 오독하면 폴백이 조용히 망가진다.
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS onterm_cache "
                "(word TEXT PRIMARY KEY, found INTEGER, ts TEXT)")
            _conn.commit()
        except Exception:
            _conn = None
    return _conn


def _get_key() -> str:
    global _key_cache
    if _key_cache is None:
        try:
            from core import ConfigLoader
            _key_cache = ConfigLoader().get_onterm_key() or ""
        except Exception:
            _key_cache = ""
    return _key_cache


def _cache_get(word: str) -> Optional[bool]:
    if word in _mem:
        return _mem[word]
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT found FROM onterm_cache WHERE word=?", (word,)).fetchone()
        if row is not None:
            val = bool(row[0])
            _mem[word] = val
            return val
    except Exception:
        pass
    return None


def _cache_put(word: str, found: bool):
    _mem[word] = found
    conn = _get_conn()
    if conn is None:
        return
    try:
        conn.execute("INSERT OR REPLACE INTO onterm_cache VALUES(?,?,?)",
                     (word, 1 if found else 0, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception:
        pass


def available() -> bool:
    """폴백 사용 가능 여부(키 + requests 존재, 일일한도 미도달)."""
    if _disabled:
        return False
    if not _get_key():
        return False
    try:
        import requests  # noqa: F401
        return True
    except Exception:
        return False


def quota_exceeded() -> bool:
    """일일 호출 제한(022)에 도달했는가 — 호출 측 로그용."""
    return _quota_hit


def _parse_response(data) -> Optional[list]:
    """응답 JSON에서 resultlist 추출.

    반환: list(정상 — 빈 리스트면 결과 없음) · None(오류 = 확인 불가).

    ⚠ 이 함수가 이 모듈의 핵심 함정 처리부다:
      · return_object가 **str**이면 오류 또는 무결과. "검색 결과가 없습니다."만
        **확정 미등재(빈 리스트)**로 보고, 나머지(시스템 에러·키 오류)는 None.
      · returnCode는 성공 시 정수 1, 오류 시 문자열 → str()로 감싸 비교.
    """
    global _quota_hit
    if not isinstance(data, dict):
        return None
    ch = data.get("channel")
    if not isinstance(ch, dict):
        return None
    ro = ch.get("return_object")

    if isinstance(ro, str):
        if "검색 결과가 없" in ro:
            return []          # 확정 미등재 — 캐시해도 되는 정상 응답
        if "일일" in ro or "Daily" in ro:
            _quota_hit = True
        return None            # 시스템 에러·키 오류 등 → 확인 불가

    if not isinstance(ro, list) or not ro:
        return None
    first = ro[0]
    if not isinstance(first, dict):
        return None

    code = str(first.get("returnCode", "")).strip()
    if code != "1":
        if code == "022":
            _quota_hit = True
        return None
    items = first.get("resultlist")
    return items if isinstance(items, list) else []


def _query_api(word: str) -> Optional[bool]:
    """라이브 온용어 조회. True/False, 오류 시 None(캐시 안 함)."""
    global _last_call
    key = _get_key()
    if not key:
        return None
    try:
        import requests
    except Exception:
        return None

    # 예의 간격 — 병렬 호출에서도 전체 호출률이 유지되도록 락으로 보호.
    with _net_lock:
        gap = _MIN_INTERVAL - (time.time() - _last_call)
        if gap > 0:
            time.sleep(gap)
        _last_call = time.time()

    # ⚠ start/num은 문서상 '선택'이지만 생략하면 HTML이 돌아온다(실측) — 항상 명시.
    params = {"key": key, "apiSearchWord": word, "start": "1", "num": _NUM}
    try:
        r = requests.get(_API_URL, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        items = _parse_response(r.json())
    except Exception:
        return None

    if items is None:          # 오류 응답/파싱 실패 → 미확인
        return None
    if not items:
        return False           # "검색 결과가 없습니다." = 확정 미등재

    # ⚠ 온용어 검색은 **부분 일치**이고 method=exact가 없다('전력'→'열기전력'도 매칭).
    #   정규화 정확일치만 등재로 인정하지 않으면 조각 단어가 전부 통과한다.
    target = _norm(word)
    for it in items:
        if isinstance(it, dict) and _norm(it.get("word")) == target:
            return True
    return False


def exists_online(word: str) -> Optional[bool]:
    """word가 온용어에 표제어로 등재되어 있는가? (캐싱 폴백)

    반환: True(등재) · False(미등재 확인) · None(확인 불가 — 키없음/오프라인/오류).
    None은 캐시하지 않으므로 다음 기회에 재시도된다.
    """
    global _disabled
    w = _norm(word)
    if len(w) < 2:
        return None
    if _disabled:
        return None
    if not available():
        _disabled = True   # 키/requests 없음 → 이후 호출 즉시 None
        return None

    # 캐시 조회/기록만 _lock으로 감싼다(네트워크는 밖 — 병렬 조회를 직렬화하지 않기 위해).
    with _lock:
        cached = _cache_get(w)
    if cached is not None:
        return cached

    result = _query_api(w)
    if result is not None:
        with _lock:
            _cache_put(w, result)
    elif _quota_hit:
        _disabled = True   # 일일 한도 초과 → 남은 호출 전부 스킵(재시도 루프 금지)
    return result


# ── 독립 실행 테스트:  python onterm_api.py 스마트그리드 제로트러스트 상담채녈 ──
if __name__ == "__main__":
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    words = sys.argv[1:] or ["스마트그리드", "제로트러스트", "하이퍼파라미터",
                             "상담채녈", "지역사회보장협의체"]
    print(f"키 설정됨: {bool(_get_key())} · 폴백 사용가능: {available()}")
    print(f"캐시 DB: {_cache_path()} (테이블 onterm_cache)")
    for w in words:
        t0 = time.time()
        print(f"  {w}: exists_online = {exists_online(w)}  ({time.time()-t0:.2f}s)")
