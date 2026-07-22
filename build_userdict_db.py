"""
build_userdict_db.py — 사용자 용어 뇌(공유 학습 사전) 로컬 빌더 (data/userdict.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
서버(Supabase)에서 pull한 스냅샷 payload(JSON)를 읽어 클라이언트 로컬 조회용 SQLite를
만든다. 설계도: docs/userdict-layer-architecture.md. (형제 빌더: build_eomun_db.py)

스냅샷 JSON 형식(서버 published_snapshot.payload 또는 data/userdict/snapshot.json):
  {
    "version": 7,
    "pairs":      [{"nonstd","norm","rule_id","category","scope_doc_type"}, ...],
    "exceptions": [{"term","scope","rule_id","note"}, ...]
  }

산출 테이블:
  · userdict_pairs       — 결정론 사용자 페어(역할 P). norm_map/eomun_pairs와 동일 형상.
  · userdict_exceptions  — 조직 예외 무교정 화이트리스트(역할 E).
  · meta                 — built_at / snapshot_ver / pair_count / exception_count

페어(P) 채택 가드(안전 우선 — build_eomun_db.py 동형이의어 가드 상속):
  1. (nonstd, norm) 단일 토큰·둘 다 한글·len>=2·공백 없음·nonstd != norm.
  2. ⚠ 동형이의어 가드 — nonstd가 stdict.db에 '독립 표준 표제어'로 존재하면 제외
     ('있다→이따' 류 재앙적 과교정 차단). 표준→표준 사내 치환도 여기서 걸러진다(맥락의존이라
     결정론 부적합 — 예외(E)로 인코딩하는 게 안전).
  3. norm이 stdict.db에 등재(표준형이 비표준이면 페어 폐기).
  stdict.db가 없으면 경고 후 데이터를 그대로 신뢰(서버가 이미 가드 통과시킨 전제).

예외(E)는 교정을 *억제*만 하므로 무거운 가드 없이 dedup만 한다(과교정 안전 방향).

전 구간 멱등(DROP-재생성). 스냅샷이 없으면 빈(유효 스키마) DB를 만든다(로더 graceful 보장).

실행:
  .\\.venv64\\Scripts\\python.exe build_userdict_db.py
  .\\.venv64\\Scripts\\python.exe build_userdict_db.py --snapshot data/userdict/snapshot.example.json
"""
import argparse
import io
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Windows 콘솔(cp949)이 일부 기호를 인코딩 못 해 크래시하는 것을 막는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "userdict.db"
STDICT_PATH = DATA_DIR / "stdict.db"
DEFAULT_SNAPSHOT = DATA_DIR / "userdict" / "snapshot.json"

_SCOPES = {"all", "spacing"}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip())


def _is_single_hangul_token(w: str) -> bool:
    return bool(w) and " " not in w and len(w) >= 2 and re.search(r"[가-힣]", w) is not None


# ── 동형이의어 가드 (build_eomun_db.py와 동일) ─────────────────────────

def _stdict_conn():
    if not STDICT_PATH.exists():
        print(f"  [경고] {STDICT_PATH} 없음 — 동형이의어 가드 비활성. "
              f"스냅샷 데이터를 그대로 신뢰합니다(서버 가드 전제).")
        return None
    try:
        return sqlite3.connect(str(STDICT_PATH))
    except sqlite3.Error as e:
        print(f"  [경고] stdict.db 열기 실패: {e} — 동형이의어 가드 비활성")
        return None


def _is_standard_headword(conn, word: str) -> bool:
    if conn is None:
        return False
    clean = re.sub(r"[^가-힣]", "", word)
    if len(clean) < 2:
        return False
    try:
        rows = conn.execute(
            "SELECT register FROM words WHERE word=? OR word GLOB ? LIMIT 8",
            (clean, clean + "[0-9][0-9]"),
        ).fetchall()
    except sqlite3.Error:
        return False
    notable = {"방언", "북한어", "옛말", "일본어식", "비표준어"}
    for (reg,) in rows:
        reg = (reg or "").strip()
        if reg not in notable:   # 표준 용법이 하나라도 있으면 표준 표제어
            return True
    return False


def _exists_in_stdict(conn, word: str) -> bool:
    if conn is None:
        return True   # 검증 불가 → 통과(데이터 신뢰)
    clean = re.sub(r"[^가-힣]", "", word)
    if len(clean) < 2:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM words WHERE word=? OR word GLOB ? LIMIT 1",
            (clean, clean + "[0-9][0-9]"),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return True


# ── 스냅샷 적재 ───────────────────────────────────────────────────────

def load_snapshot(path: Path) -> dict:
    if not path.exists():
        print(f"  [안내] 스냅샷 없음: {path}\n"
              f"         빈(유효 스키마) DB를 만듭니다. 서버 pull(DO-4) 후 재실행하세요.")
        return {"version": 0, "pairs": [], "exceptions": []}
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"스냅샷 읽기 실패: {path} ({e})")
    data.setdefault("version", 0)
    data.setdefault("pairs", [])
    data.setdefault("exceptions", [])
    print(f"스냅샷 적재: v{data['version']} — 페어 {len(data['pairs'])} · "
          f"예외 {len(data['exceptions'])} ({path.name})")
    return data


def build(snapshot_path: Path):
    snap = load_snapshot(snapshot_path)
    stdict = _stdict_conn()

    pair_rows, seen_pairs = [], {}
    pair_dropped_guard = 0
    for rec in snap["pairs"]:
        nonstd = _nfc(rec.get("nonstd", "")).split("(")[0].strip()
        norm = _nfc(rec.get("norm", "")).split("(")[0].strip()
        if not _is_single_hangul_token(nonstd) or not _is_single_hangul_token(norm):
            continue
        if nonstd == norm:
            continue
        # 가드 2: nonstd가 표준 표제어이면 제외(동형이의어/표준치환 차단)
        if _is_standard_headword(stdict, nonstd):
            pair_dropped_guard += 1
            print(f"  [가드] '{nonstd}' 표준 표제어 — 페어 제외 ({rec.get('rule_id','')})")
            continue
        # 가드 3: norm이 사전에 없으면 폐기
        if not _exists_in_stdict(stdict, norm):
            pair_dropped_guard += 1
            print(f"  [가드] 표준형 '{norm}' 사전 미등재 — 페어 폐기 ({rec.get('rule_id','')})")
            continue
        if nonstd in seen_pairs:
            if seen_pairs[nonstd] != norm:
                print(f"  [충돌] '{nonstd}'→'{seen_pairs[nonstd]}' vs '{norm}' — 먼저 채택분 유지")
            continue
        seen_pairs[nonstd] = norm
        pair_rows.append((
            nonstd, norm,
            _nfc(rec.get("rule_id", "")),
            _nfc(rec.get("category", "")),
            _nfc(rec.get("scope_doc_type") or "*"),
        ))

    exc_rows, seen_exc = [], set()
    for rec in snap["exceptions"]:
        term = _nfc(rec.get("term", ""))
        if not term or len(term) < 2:
            continue
        scope = _nfc(rec.get("scope") or "all") or "all"
        if scope not in _SCOPES:
            print(f"  [주의] 알 수 없는 scope '{scope}' — 'all'로 처리 ({term})")
            scope = "all"
        key = (term, scope)
        if key in seen_exc:
            continue
        seen_exc.add(key)
        exc_rows.append((term, scope, _nfc(rec.get("rule_id", "")), _nfc(rec.get("note", ""))))

    if stdict is not None:
        stdict.close()

    _write_db(snap["version"], pair_rows, exc_rows)
    print(f"\n완료 — 페어 {len(pair_rows):,} (가드 제외 {pair_dropped_guard}) · "
          f"예외 {len(exc_rows):,} · 스냅샷 v{snap['version']}")


def _write_db(version, pair_rows, exc_rows):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript("""
            DROP TABLE IF EXISTS userdict_pairs;
            DROP TABLE IF EXISTS userdict_exceptions;
            DROP TABLE IF EXISTS meta;
            CREATE TABLE userdict_pairs (
              nonstd TEXT PRIMARY KEY, norm TEXT NOT NULL,
              rule_id TEXT, category TEXT, scope_doc_type TEXT
            );
            CREATE TABLE userdict_exceptions (
              term TEXT, scope TEXT, rule_id TEXT, note TEXT,
              PRIMARY KEY (term, scope)
            );
            CREATE INDEX idx_exc_term ON userdict_exceptions(term);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.executemany(
            "INSERT OR REPLACE INTO userdict_pairs VALUES (?,?,?,?,?)", pair_rows)
        conn.executemany(
            "INSERT OR IGNORE INTO userdict_exceptions VALUES (?,?,?,?)", exc_rows)
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ("snapshot_ver", str(version)),
            ("pair_count", str(len(pair_rows))),
            ("exception_count", str(len(exc_rows))),
        ])
        conn.commit()
        print(f"적재 완료: {DB_PATH}")
        if pair_rows:
            print("페어 샘플:")
            for r in conn.execute(
                    "SELECT nonstd, norm, rule_id FROM userdict_pairs LIMIT 8"):
                print(f"  {r[0]} → {r[1]}  ({r[2]})")
        if exc_rows:
            print("예외 샘플:")
            for r in conn.execute(
                    "SELECT term, scope, note FROM userdict_exceptions LIMIT 8"):
                print(f"  {r[0]} [{r[1]}]  {r[2]}")
    finally:
        conn.close()


def guard_check_many(pairs):
    """페어 후보 일괄 동형이의어/등재 가드 검사 (큐레이터 패널용 — stdict 1회 연결).

    pairs: [(nonstd, norm), ...] → {(nonstd, norm): (ok: bool, reason: str)}.
    빌드타임 가드(build())와 동일 기준: nonstd가 표준 표제어면 제외, norm 미등재면 폐기.
    stdict.db가 없으면 모두 (True, '사전 미검증')로 통과(graceful).
    """
    conn = _stdict_conn()
    out = {}
    try:
        for nonstd, norm in pairs:
            n, m = _nfc(nonstd), _nfc(norm)
            if conn is None:
                out[(nonstd, norm)] = (True, "사전 미검증(통과)")
            elif _is_standard_headword(conn, n):
                out[(nonstd, norm)] = (False, f"'{n}'는 표준 표제어 — 결정론 치환 위험")
            elif not _exists_in_stdict(conn, m):
                out[(nonstd, norm)] = (False, f"표준형 '{m}' 사전 미등재")
            else:
                out[(nonstd, norm)] = (True, "가드 통과")
    finally:
        if conn is not None:
            conn.close()
    return out


def main():
    ap = argparse.ArgumentParser(description="사용자 용어 뇌 로컬 빌더 (data/userdict.db)")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                    help="스냅샷 JSON 경로(기본: data/userdict/snapshot.json)")
    args = ap.parse_args()
    build(Path(args.snapshot))


if __name__ == "__main__":
    main()
