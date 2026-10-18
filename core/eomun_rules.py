"""
core/eomun_rules.py — 어문 규범 지식 레이어 런타임 조회 (data/eomun.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/eomun-rule-layer-architecture.md

역할(런타임에 쓰는 것은 B 하나다):
  B. 결정론 페어 — batch_lookup_eomun_pair()로 비표준→규범 매핑 조회(워커 [5.8], 국가 표준
                   대조용으로 [5.6]도 씀). pair_rule_id()는 정오표 근거 조항 인용용.
  ⚠ 역할 A(KAGEC — 청크에 걸린 규칙을 프롬프트에 주입)는 2026-06-22 **제거**됐다. AI 오탈자
    탐지를 분산시켜 회귀를 냈다(eomun.db를 끄면 복귀 — 인과 입증). 재도입은 정량 검증
    (precision/F0.5) 선결. eomun.db의 rules·triggers 테이블은 빌더가 여전히 만들지만
    런타임은 rules를 가용성 확인에만 쓰고 triggers는 읽지 않는다.

규율:
  · GUI-agnostic — PySide6 import 금지(core/ 규칙).
  · graceful — eomun.db 미존재/오류 시 빈 결과·None(사전·kiwi와 동일 degradation).
  · 스레드 로컬 커넥션(nikl_dict와 동일 패턴).
"""

import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════
# ▌DB 경로 결정 (사전 stdict.db와 같은 datapaths 규칙)
# ══════════════════════════════════════════════════════

def _resolve_db_path() -> Path:
    # ★사전(stdict.db)과 **같은 규칙**으로 찾는다(datapaths — 데이터 버전이 가장 높은 데이터
    #   폴더 우선). 예전엔 번들 내부·EXE 옆만 봐서, 데이터 패키지에 새 eomun.db가 들어 있어도
    #   인앱 데이터 업데이트(사용자 폴더 설치)가 **반영되지 않았다**(2026-10-17 감사).
    try:
        from datapaths import find_data
        p = find_data("eomun.db")
        if p is not None:
            return p
    except Exception:
        pass
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "data" / "eomun.db")            # type: ignore[attr-defined]
        candidates.append(Path(sys.executable).parent / "data" / "eomun.db")
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "data" / "eomun.db")
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


DB_PATH = _resolve_db_path()

_local = threading.local()


def _conn() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    if not hasattr(_local, "conn") or _local.conn is None:
        try:
            c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            c.execute("PRAGMA cache_size = -2048")
            _local.conn = c
        except sqlite3.Error:
            _local.conn = None
    return _local.conn


def available() -> bool:
    """eomun.db가 존재하고 rules 테이블이 있는가."""
    c = _conn()
    if c is None:
        return False
    try:
        c.execute("SELECT 1 FROM rules LIMIT 1")
        return True
    except sqlite3.Error:
        return False


# ══════════════════════════════════════════════════════
# ▌역할 B — 결정론 규범 페어 (nikl_dict.batch_lookup_norm과 동일 시그니처)
# ══════════════════════════════════════════════════════

def batch_lookup_eomun_pair(words: set, chunk_size: int = 500) -> dict:
    """비표준→규범 매핑 배치 조회. 반환: {nonstd: norm}. DB 없으면 {} (graceful)."""
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
                f"SELECT nonstd, norm FROM eomun_pairs WHERE nonstd IN ({placeholders})",
                chunk,
            ).fetchall()
        except sqlite3.Error:
            return out
        for r in rows:
            out[r[0]] = r[1]
    return out


def pair_rule_id(nonstd: str) -> Optional[str]:
    """결정론 페어의 근거 rule_id(정오표 인용용)."""
    c = _conn()
    if c is None:
        return None
    try:
        row = c.execute("SELECT rule_id FROM eomun_pairs WHERE nonstd=? LIMIT 1",
                        (re.sub(r"[^가-힣]", "", nonstd),)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def status() -> dict:
    """진단용 — DB 가용성과 카운트."""
    if not DB_PATH.exists():
        return {"available": False, "path": str(DB_PATH)}
    c = _conn()
    if c is None:
        return {"available": False, "path": str(DB_PATH)}
    try:
        rules = c.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        pairs = c.execute("SELECT COUNT(*) FROM eomun_pairs").fetchone()[0]
        trigs = c.execute("SELECT COUNT(*) FROM triggers").fetchone()[0]
        built = c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        return {"available": True, "path": str(DB_PATH), "rules": rules,
                "pairs": pairs, "triggers": trigs,
                "built_at": built[0] if built else "unknown"}
    except sqlite3.Error:
        return {"available": False, "path": str(DB_PATH)}


if __name__ == "__main__":
    print("어문 규범 DB 상태:")
    for k, v in status().items():
        print(f"  {k}: {v}")
    if available():
        print("결정론 페어 조회:", batch_lookup_eomun_pair({"컨텐츠", "갈수있다", "등교길"}))
