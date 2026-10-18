"""
core/userdict.py — 사용자 용어 뇌(공유 학습 사전) 런타임 로더/조회 (data/userdict.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/userdict-layer-architecture.md (형제: core/eomun_rules.py)

역할:
  P. 결정론 사용자 페어 — batch_lookup_pair()로 사내 비표준→표준 매핑 조회(워커 [5.6]).
                          (nikl_dict.batch_lookup_norm·eomun_rules.batch_lookup_eomun_pair와
                          동일 시그니처). 동형이의어 가드는 빌드타임(build_userdict_db.py)에서 적용.
  E. 조직 예외(무교정) — is_exception()/exception_set()으로 조직 승인 표기를 조회.
                          재검증②/안전망⑤/띄어쓰기 백스톱⑦에서 교정·플래그를 *억제*하는 데 쓴다.

규율:
  · GUI-agnostic — PySide6 import 금지(core/ 규칙).
  · graceful — userdict.db 미존재/오류 시 빈 결과·None·False(사전·kiwi와 동일 degradation).
  · 스레드 로컬 커넥션(nikl_dict·eomun_rules와 동일 패턴).

배포: 서버(Supabase) 스냅샷을 pull해 build_userdict_db.py가 data/userdict.db를 만든다(DO-4).
      이 모듈은 그 산출물만 읽는다 — 네트워크·서버 의존 없음.
"""

import re
import sqlite3
import sys
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════
# ▌DB 경로 결정 — 후보 중 스냅샷 버전이 가장 높은 파일(_resolve_db_path 독스트링)
# ══════════════════════════════════════════════════════

def _snapshot_ver_of(p: Path) -> int:
    """파일 p의 meta.snapshot_ver(못 읽으면 -1). 읽기 전용으로 열고 곧바로 닫는다."""
    try:
        con = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='snapshot_ver'").fetchone()
        finally:
            con.close()
        return int(row[0]) if row and str(row[0]).isdigit() else -1
    except Exception:
        return -1


def _resolve_db_path() -> Path:
    """userdict.db 위치 — 후보 중 **스냅샷 버전이 가장 높은** 파일(같으면 앞 순서).

    후보: ① 서버 동기화가 빌드한 것(`datapaths.userdict_runtime_path`) ② 데이터 패키지에
    든 것(사전과 같은 탐색 순서 — 사용자 폴더·EXE 옆·번들 내부). 번들 내부는 예전 빌드본이
    동기화 결과를 잘못 쓰던 곳이라, 이미 받아 둔 스냅샷을 잃지 않으려고 후보에 남긴다.
    ★예전엔 번들 내부·EXE 옆만 봐서, 데이터 패키지의 새 userdict.db가 인앱 데이터
      업데이트(사용자 폴더 설치)로 들어와도 **반영되지 않았다**(2026-10-17 감사).
    없으면 빌더가 쓸 자리를 돌려준다 — 동기화가 처음 빌드하면 `reload()`가 그대로 집는다.
    """
    cands = []
    try:
        import datapaths as _dp
        cands.append(_dp.userdict_runtime_path())
        cands += [d / "userdict.db" for d in _dp._ordered_candidates()]
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        cands.append(Path(sys._MEIPASS) / "data" / "userdict.db")          # type: ignore[attr-defined]
        cands.append(Path(sys.executable).parent / "data" / "userdict.db")
    else:
        cands.append(Path(__file__).resolve().parent.parent / "data" / "userdict.db")
    cands = list(dict.fromkeys(cands))
    existing = [p for p in cands if p.exists()]
    if not existing:
        return cands[0]
    return max(existing, key=lambda p: (_snapshot_ver_of(p), -existing.index(p)))


DB_PATH = _resolve_db_path()

_local = threading.local()
_gen = 0          # reload()마다 증가 — 스레드마다 들고 있는 연결을 새 파일로 바꾸게 한다


def reload() -> Path:
    """서버 동기화가 새로 빌드한 뒤 — 위치를 다시 고르고 캐시를 비운다(재시작 없이 반영)."""
    global DB_PATH, _gen
    DB_PATH = _resolve_db_path()
    _gen += 1
    exception_set.cache_clear()
    return DB_PATH


def _conn() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "gen", None) != _gen:
        try:
            c.close()
        except Exception:
            pass
        c = _local.conn = None
    if c is None:
        try:
            c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            c.execute("PRAGMA cache_size = -2048")
            _local.conn = c
            _local.gen = _gen
        except sqlite3.Error:
            _local.conn = None
    return _local.conn


def available() -> bool:
    """userdict.db가 존재하고 스키마(userdict_pairs 테이블)가 있는가.

    페어가 0건이어도 스키마만 있으면 True(예외만 있는 스냅샷도 유효). graceful.
    """
    c = _conn()
    if c is None:
        return False
    try:
        c.execute("SELECT 1 FROM userdict_pairs LIMIT 1")
        return True
    except sqlite3.Error:
        return False


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


# ══════════════════════════════════════════════════════
# ▌역할 P — 결정론 사용자 페어 (nikl_dict.batch_lookup_norm과 동일 시그니처)
# ══════════════════════════════════════════════════════

def batch_lookup_pair(words: set, chunk_size: int = 500) -> dict:
    """사내 비표준→표준 매핑 배치 조회. 반환: {nonstd: norm}. DB 없으면 {} (graceful)."""
    c = _conn()
    if c is None:
        return {}
    out: dict = {}
    wl = [w for w in words if isinstance(w, str) and len(w) >= 2]
    for i in range(0, len(wl), chunk_size):
        chunk = wl[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        try:
            rows = c.execute(
                f"SELECT nonstd, norm FROM userdict_pairs WHERE nonstd IN ({placeholders})",
                chunk,
            ).fetchall()
        except sqlite3.Error:
            return out
        for r in rows:
            out[r[0]] = r[1]
    return out


def pair_info(nonstd: str) -> Optional[dict]:
    """페어의 부가정보(정오표 reason 인용용) — norm/rule_id/category. 없으면 None."""
    c = _conn()
    if c is None:
        return None
    clean = re.sub(r"[^가-힣]", "", nonstd).strip()
    if len(clean) < 2:
        return None
    try:
        row = c.execute(
            "SELECT norm, rule_id, category FROM userdict_pairs WHERE nonstd=? LIMIT 1",
            (clean,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {"norm": row[0], "rule_id": row[1], "category": row[2]}


# ══════════════════════════════════════════════════════
# ▌역할 E — 조직 예외(무교정 화이트리스트)
# ══════════════════════════════════════════════════════

@lru_cache(maxsize=8)
def exception_set(scope: Optional[str] = None) -> frozenset:
    """주어진 scope에서 무교정 처리할 표제어 집합(캐시).

    · scope=None      — 저장된 모든 예외 표제어.
    · scope='spacing' — scope가 'spacing' 또는 'all'인 표제어(상위 scope 'all'이 하위를 포함).
    · scope='all'     — scope가 'all'인 표제어만(전역 무교정).
    DB 없으면 frozenset() (graceful).
    """
    c = _conn()
    if c is None:
        return frozenset()
    try:
        if scope is None:
            rows = c.execute("SELECT term FROM userdict_exceptions").fetchall()
        else:
            rows = c.execute(
                "SELECT term FROM userdict_exceptions WHERE scope=? OR scope='all'",
                (scope,)).fetchall()
        return frozenset(r[0] for r in rows)
    except sqlite3.Error:
        return frozenset()


def is_exception(term: str, scope: str = "all") -> bool:
    """term이 주어진 scope에서 조직 승인 무교정 표제어인가. DB 없으면 False (graceful)."""
    if not term:
        return False
    return _nfc(term) in exception_set(scope)


# ══════════════════════════════════════════════════════
# ▌진단
# ══════════════════════════════════════════════════════

def snapshot_version() -> Optional[int]:
    """현재 로드된 스냅샷 버전(meta.snapshot_ver). 동기화 비교용(DO-4). 없으면 None."""
    c = _conn()
    if c is None:
        return None
    try:
        row = c.execute("SELECT value FROM meta WHERE key='snapshot_ver'").fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else None
    except (sqlite3.Error, ValueError):
        return None


def status() -> dict:
    """진단용 — DB 가용성과 카운트."""
    if not DB_PATH.exists():
        return {"available": False, "path": str(DB_PATH)}
    c = _conn()
    if c is None:
        return {"available": False, "path": str(DB_PATH)}
    try:
        pairs = c.execute("SELECT COUNT(*) FROM userdict_pairs").fetchone()[0]
        exc = c.execute("SELECT COUNT(*) FROM userdict_exceptions").fetchone()[0]
        built = c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        return {"available": True, "path": str(DB_PATH), "pairs": pairs,
                "exceptions": exc, "snapshot_ver": snapshot_version(),
                "built_at": built[0] if built else "unknown"}
    except sqlite3.Error:
        return {"available": False, "path": str(DB_PATH)}


if __name__ == "__main__":
    # CLI 자가테스트 전용 — Windows 콘솔(cp949)이 일부 기호를 못 내보내 크래시하는 것을 막는다.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("사용자 용어 뇌 DB 상태:")
    for k, v in status().items():
        print(f"  {k}: {v}")
    if available():
        print("\n페어 조회 테스트:", batch_lookup_pair({"플랫홈", "있다", "콘텐츠"}))
        print("예외(spacing) 테스트, 매출액:", is_exception("매출액", "spacing"))
        print("예외(all) 테스트, 표준국어대사전:", is_exception("표준국어대사전", "all"))
        print("예외 집합(spacing):", set(exception_set("spacing")))
