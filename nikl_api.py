"""
nikl_api.py — 우리말샘 사전 OpenAPI 캐싱 폴백
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
로컬 stdict.db(spellcheck-ko 스냅샷)가 놓친 단어를 **라이브 우리말샘**으로
실시간 확인한다. 스냅샷엔 없지만 실재하는 단어(예: 대행사·돌봄)가 거짓
'검수 필요'로 뜨는 false positive를 줄이는 보강 레이어.

설계 원칙:
  · **폴백 전용** — 로컬+형태소가 '미등재'로 판정한 의심어에만 호출(전 어휘 X).
  · **영구 캐시** — 한 단어는 평생 1회만 조회. data/api_cache.db에 결과(있음/없음)
    저장 → 다음 실행/문서에선 네트워크 0. 사실상 자동 성장하는 보강 사전.
  · **graceful** — 키 없음/오프라인/오류 시 None 반환 → 호출 측은 기존 동작 유지.
  · GUI-agnostic (PySide6 미사용) — core/ 규칙과 동일 선상.

우리말샘 OpenAPI: https://opendict.korean.go.kr/  (무료 인증키 발급)
  GET /api/search?key=KEY&q=단어&req_type=json&advanced=y&method=exact
  응답 JSON: {"channel": {"total": N, "item": [{"word": "..."}, ...]}}
"""

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

_API_URL = "https://opendict.korean.go.kr/api/search"
_TIMEOUT = 4.0          # 단어당 네트워크 타임아웃(초) — 스크리닝 지연 억제
_MIN_INTERVAL = 0.05    # 연속 호출 최소 간격(초) — API 예의

_lock = threading.Lock()
_mem: dict = {}         # 세션 인메모리 캐시 {word: bool}
_conn: Optional[sqlite3.Connection] = None
_last_call = 0.0
_key_cache: Optional[str] = None
_disabled = False       # 키 없음/requests 없음/API 오류 응답 → 이 실행 동안 비활성(재시도 안 함)
_last_error = ""        # 마지막 API 오류(코드·메시지) — 호출 측 로그용

# 캐시 형식 버전. 2 = '오류 응답을 미등재로 영구 캐시'하던 결함(아래 _parse_response)을
#   고친 판. 이전 판이 쌓은 **미등재(found=0)** 항목은 오류 응답이 섞였을 수 있어 한 번
#   비운다 — 등재(found=1)는 실제 표제어가 일치해야만 기록되므로 그대로 둔다.
_CACHE_VER = 2


def _norm(w: str) -> str:
    """비교용 정규화 — 한글만, 우리말샘 음절경계 마커(-, ^) 제거."""
    return re.sub(r"[^가-힣]", "", (w or "").replace("-", "").replace("^", ""))


def _cache_path() -> Path:
    """api_cache.db 경로 — 쓰기 가능한 사용자 데이터 폴더.

    ⚠ 과거엔 stdict.db 옆(= 번들 내부)을 썼는데, 빌드본에선 그 경로가 읽기 전용이라
      캐시 저장이 조용히 실패한다. onterm_api와 **같은 파일**을 공유한다(테이블만 분리).
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
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(word TEXT PRIMARY KEY, found INTEGER, ts TEXT)")
            _migrate_cache(_conn)
            _conn.commit()
        except Exception:
            _conn = None
    return _conn


def _migrate_cache(conn) -> None:
    """캐시 형식 버전이 낮으면 미등재 항목을 비운다(_CACHE_VER 주석).

    ⚠ 테이블 이름은 `nikl_cache_meta`다 — 같은 api_cache.db를 onterm_api가 함께 쓰므로
      범용 이름(meta 등)을 피하고, 온용어 캐시(onterm_cache)는 건드리지 않는다.
    """
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS nikl_cache_meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute(
            "SELECT value FROM nikl_cache_meta WHERE key='cache_ver'").fetchone()
        if row is None or int(row[0] or 0) < _CACHE_VER:
            conn.execute("DELETE FROM cache WHERE found=0")
            conn.execute("INSERT OR REPLACE INTO nikl_cache_meta VALUES('cache_ver', ?)",
                         (str(_CACHE_VER),))
    except Exception:
        pass


def last_error() -> str:
    """이번 실행에서 우리말샘 API가 돌려준 오류(없으면 ""). 오류가 나면 조회가 멈춘다."""
    return _last_error


def _get_key() -> str:
    global _key_cache
    if _key_cache is None:
        try:
            from core import ConfigLoader
            _key_cache = ConfigLoader().get_nikl_key() or ""
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
        row = conn.execute("SELECT found FROM cache WHERE word=?", (word,)).fetchone()
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
        conn.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
                     (word, 1 if found else 0, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception:
        pass


def available() -> bool:
    """폴백 사용 가능 여부(키 + requests 존재)."""
    if _disabled:
        return False
    if not _get_key():
        return False
    try:
        import requests  # noqa: F401
        return True
    except Exception:
        return False


def _api_error(text: str) -> str:
    """응답이 **API 오류 응답**이면 '코드 메시지', 아니면 "".

    실측(2026-10-16, 무효 키): HTTP 200 + `<error><error_code>020</error_code>
    <message>Unregistered key</message></error>` — req_type=json을 줘도 XML이 온다.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            import json
            d = json.loads(text)
            e = d.get("error") if isinstance(d, dict) else None
            if e:
                if isinstance(e, dict):
                    return f"{e.get('error_code', '')} {e.get('message', '')}".strip() or "error"
                return str(e)
            return ""
        except Exception:
            pass
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        err = root if root.tag.lower() == "error" else root.find(".//error")
        if err is not None:
            code = (err.findtext("error_code") or "").strip()
            msg = (err.findtext("message") or "").strip()
            return f"{code} {msg}".strip() or "error"
    except Exception:
        pass
    return ""


def _parse_response(text: str):
    """응답(JSON 또는 XML)에서 (total, [word, ...]) 추출. 실패 시 (None, []).

    우리말샘 API는 req_type=json을 줘도 인증오류·설정에 따라 XML로 응답하기도 한다.
    두 포맷을 모두 파싱해 폴백이 조용히 무력화되지 않게 한다.

    ★★(None, [])은 '확인 불가'(캐시 안 함), (0, [])은 '미등재 확정'(영구 캐시)이다 —
      둘을 섞으면 안 된다. 예전엔 오류 응답(루트가 `<error>`인 XML — `.//error`는 루트
      자신을 못 찾는다 — 또는 `{"error": …}` JSON)이 total 0으로 읽혀 **조회한 낱말이
      전부 '미등재'로 영구 캐시**됐다. 키를 교체·회수하는 순간 모든 사용자 PC의 캐시가
      오염되는 경로였다(2026-10-16 감사, 무효 키로 실측). 그래서 이제 **결과 형식이
      확인될 때만**(channel / total 요소가 있을 때만) 숫자를 돌려준다.
    """
    text = (text or "").strip()
    if not text or _api_error(text):
        return None, []
    if text[0] in "{[":
        try:
            import json
            d = json.loads(text)
            ch = d.get("channel") if isinstance(d, dict) else None
            if not isinstance(ch, dict) or "total" not in ch:
                return None, []                 # 알 수 없는 형식 — 확인 불가
            total = int(ch.get("total") or 0)
            items = ch.get("item", []) or []
            if isinstance(items, dict):
                items = [items]
            return total, [str(it.get("word", "")) for it in items]
        except Exception:
            pass
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        tot_el = root.find(".//total")
        if tot_el is None or not (tot_el.text or "").strip():
            return None, []                     # 알 수 없는 형식 — 확인 불가
        total = int(tot_el.text)
        words = [(w.text or "") for w in root.findall(".//item/word")]
        return total, words
    except Exception:
        return None, []


def _query_api(word: str) -> Optional[bool]:
    """라이브 우리말샘 조회. True/False, 오류 시 None(캐시 안 함)."""
    global _last_call, _disabled, _last_error
    key = _get_key()
    if not key:
        return None
    try:
        import requests
    except Exception:
        return None
    # 예의 간격
    gap = _MIN_INTERVAL - (time.time() - _last_call)
    if gap > 0:
        time.sleep(gap)
    params = {"key": key, "q": word, "req_type": "json",
              "advanced": "y", "method": "exact"}
    try:
        r = requests.get(_API_URL, params=params, timeout=_TIMEOUT)
        _last_call = time.time()
        if r.status_code != 200:
            return None
        err = _api_error(r.text)
        if err:
            # 키 무효·회수·한도 초과 등 — 같은 응답이 낱말마다 반복되므로 이 실행 동안은
            #   더 묻지 않는다(호출 측은 last_error()로 사유를 로그에 남긴다).
            _disabled = True
            _last_error = err
            return None
        total, words = _parse_response(r.text)
    except Exception:
        return None
    if total is None:          # 파싱 실패/오류 응답(키 오류 등) → 미확인
        return None
    if total <= 0:
        return False
    # method=exact여도 정규화 비교로 한 번 더 확인(과매칭/페이지네이션 방지).
    target = _norm(word)
    for w in words:
        if _norm(w) == target:
            return True
    return False


def exists_online(word: str) -> Optional[bool]:
    """우리말샘에 word가 등재되어 있는가? (캐싱 폴백)

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
        _disabled = True   # 키/requests 없음 → 이후 호출 즉시 None(불필요한 시도 차단)
        return None
    with _lock:
        cached = _cache_get(w)
        if cached is not None:
            return cached
        result = _query_api(w)
        if result is not None:
            _cache_put(w, result)
        return result


# ── 독립 실행 테스트:  python nikl_api.py 대행사 돌봄 상담채녈 ──
if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    words = sys.argv[1:] or ["대행사", "돌봄", "상담채녈", "사과"]
    print(f"키 설정됨: {bool(_get_key())} · 폴백 사용가능: {available()}")
    print(f"캐시 DB: {_cache_path()}")
    for w in words:
        print(f"  {w}: exists_online = {exists_online(w)}")
