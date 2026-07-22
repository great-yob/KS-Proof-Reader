"""
core/event_queue.py — 교정 결정 학습 이벤트 로컬 큐 (data/event_queue.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/userdict-layer-architecture.md (§2 데이터 모델 · §5 이벤트 캡처).
공유 용어 뇌(userdict)의 **수집 측** — 사용자가 검토 패널/auto_apply에서 내린
수락·거절 결정을 **용어 단위** 이벤트로 추출해 오프라인 로컬 큐(SQLite)에 적재한다.

역할(DO-3):
  · build_events()      — 교정 목록(상태 포함) → 용어 단위 이벤트 dict 목록(순수 함수, DB 무관).
  · record()/record_corrections() — 이벤트를 data/event_queue.db에 적재(synced=0).
  · pending()/mark_synced()/count_pending()/status() — DO-4(서버 push) 준비용 큐 조회.

프라이버시(설계 §2 확정):
  · **문맥 스니펫(context_before/after)·원문 문장은 저장하지 않는다.** 용어 단위만.
  · doc_type 등 비식별 거친 특징만 동반(현재 UI 미수집 → None 허용).
  · org_id/user_id는 DO-4(Supabase Auth)에서 채운다. DO-3에선 빈 값(로컬 누적).

규율:
  · GUI-agnostic — PySide6 import 금지(core/ 규칙).
  · graceful — kiwi/DB 부재·오류 시 정규화 생략·0 반환. **절대 예외를 올리지 않는다**
    (이벤트 기록은 교정 적용의 부수효과 — 실패해도 본 기능에 영향 0).
  · 스레드 로컬 커넥션. event_queue.db는 **런타임 생성**(빌드 산출물 아님) — 쓰기 가능
    위치(개발=레포 data/, 프로즌=exe 옆 data/)에 만든다. 멱등 스키마(IF NOT EXISTS).
"""

import sqlite3
import sys
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════
# ▌DB 경로 — 런타임 생성이므로 쓰기 가능 위치(읽기전용 _MEIPASS 회피)
# ══════════════════════════════════════════════════════

def _resolve_db_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent                       # exe 옆(쓰기 가능)
    else:
        base = Path(__file__).resolve().parent.parent            # 레포 루트
    return base / "data" / "event_queue.db"


DB_PATH = _resolve_db_path()

_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id     TEXT PRIMARY KEY,
  org_id       TEXT,                       -- DO-4(Supabase)에서 채움
  user_id      TEXT,                       -- DO-4에서 채움
  original     TEXT NOT NULL,              -- 용어 표면형(morph.strip_josa로 lemma 정규화)
  corrected    TEXT NOT NULL,              -- 제안된 교정형
  action       TEXT NOT NULL,              -- accept | reject | edit_accept
  suggest_src  TEXT,                       -- ai | dict | spacing | userdict
  category     TEXT,
  doc_type     TEXT,                       -- 비식별 거친 문맥(현재 None 허용)
  snapshot_ver INTEGER,                    -- 적용 당시 userdict 스냅샷 버전
  ts           TEXT NOT NULL,              -- ISO8601 UTC
  synced       INTEGER NOT NULL DEFAULT 0  -- 0=로컬 대기, 1=서버 전송 완료(DO-4)
);
CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced);
"""


def _conn() -> Optional[sqlite3.Connection]:
    if not hasattr(_local, "conn") or _local.conn is None:
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            c.executescript(_SCHEMA)        # 멱등 — 없으면 생성
            c.commit()
            _local.conn = c
        except sqlite3.Error:
            _local.conn = None
        except OSError:
            _local.conn = None
    return _local.conn


def available() -> bool:
    """이벤트 큐를 쓸 수 있는가(DB 생성/스키마 OK)."""
    return _conn() is not None


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip())


def _get_morph():
    """core.morph 반환(사용 가능할 때만). 미설치/실패 시 None — 정규화 생략."""
    try:
        from core import morph
        return morph if morph.available() else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════
# ▌이벤트 추출 (용어 단위 · 순수 함수 · 문맥 미저장)
# ══════════════════════════════════════════════════════

# 용어 페어가 아닌 항목 — 학습 이벤트에서 제외.
#   dict_flag : original==corrected 인 '검수 필요' 카드(치환 아님).
#   ai_polish : 문장 단위 윤문(재사용 가능한 용어 페어 아님).
#   punct     : 괄호 짝 맞추기 등 위치 의존 문장부호 교정(재사용 가능한 용어 페어 아님).
_SKIP_SOURCES = {"dict_flag", "ai_polish", "punct"}


def _suggest_src(c: dict) -> str:
    """Correction.source(+reason) → 이벤트 suggest_src(ai|dict|spacing|userdict)."""
    reason = c.get("reason") or c.get("description") or ""
    if reason.startswith("[사내 용어]"):
        return "userdict"        # [5.6] 사내 페어 — source는 'dict'지만 출처는 userdict
    src = c.get("source") or ""
    if src.startswith("ai"):
        return "ai"
    if src == "spacing":
        return "spacing"
    return "dict"


def _lemma_pair(original: str, corrected: str, morph) -> tuple:
    """어절 끝 조사를 떼어 용어 페어를 정규화한다(설계 §2: morph.strip_josa 적용).

    '훗가이도현의'→'홋카이도현의' 를 '훗가이도현'→'홋카이도현' 으로 모아 같은 용어의
    표가 흩어지지 않게 한다. 단일 어절(공백 없음)에만 적용하고, original에서 떼어낸
    조사가 corrected 끝에도 동일하게 있을 때만 양쪽을 함께 자른다(정합 보장). kiwi
    미설치/실패 시 표면형 그대로(graceful).
    """
    if morph is None or " " in original:
        return original, corrected
    try:
        lemma = morph.strip_josa(original)
    except Exception:
        return original, corrected
    if (lemma and lemma != original and len(lemma) >= 2
            and original.startswith(lemma)):
        josa = original[len(lemma):]
        if josa and corrected.endswith(josa) and len(corrected) > len(josa):
            return lemma, corrected[:-len(josa)]
    return original, corrected


def build_events(corrections, *, doc_type: Optional[str] = None,
                 snapshot_ver: Optional[int] = None) -> list:
    """교정 목록(검토/auto_apply로 status가 정해진) → 용어 단위 이벤트 목록.

    · status가 accepted/rejected 인 항목만(미결정 pending은 표가 아님 → 제외).
    · dict_flag/ai_polish, original==corrected(무변경)는 제외.
    · original/corrected는 lemma 정규화. 문맥 스니펫은 일절 포함하지 않는다.
    실패는 조용히 건너뛴다(부수효과 — 절대 예외 전파 금지).
    """
    morph = _get_morph()
    events = []
    for c in corrections or []:
        try:
            status = (c.get("status") or "pending")
            if status not in ("accepted", "rejected"):
                continue
            if (c.get("source") or "") in _SKIP_SOURCES:
                continue
            original = _nfc(c.get("original"))
            corrected = _nfc(c.get("corrected"))
            if not original or not corrected or original == corrected:
                continue
            orig_term, corr_term = _lemma_pair(original, corrected, morph)
            if not orig_term or not corr_term or orig_term == corr_term:
                continue
            if status == "accepted":
                action = "edit_accept" if c.get("_edited") else "accept"
            else:
                action = "reject"
            events.append({
                "original":     orig_term,
                "corrected":    corr_term,
                "action":       action,
                "suggest_src":  _suggest_src(c),
                "category":     c.get("category") or "",
                "doc_type":     doc_type,
                "snapshot_ver": snapshot_ver,
            })
        except Exception:
            continue   # 단일 항목 오류가 전체 수집을 막지 않게
    return events


# ══════════════════════════════════════════════════════
# ▌적재 / 큐 조회
# ══════════════════════════════════════════════════════

def record(events) -> int:
    """이벤트 목록을 큐에 적재(synced=0). 적재 건수 반환. 실패/빈 입력이면 0 (graceful)."""
    if not events:
        return 0
    c = _conn()
    if c is None:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for e in events:
        try:
            rows.append((
                str(uuid.uuid4()),
                e.get("org_id") or "", e.get("user_id") or "",
                e["original"], e["corrected"], e["action"],
                e.get("suggest_src") or "", e.get("category") or "",
                e.get("doc_type"), e.get("snapshot_ver"),
                now, 0,
            ))
        except (KeyError, TypeError):
            continue
    if not rows:
        return 0
    try:
        c.executemany(
            "INSERT INTO events (event_id, org_id, user_id, original, corrected, "
            "action, suggest_src, category, doc_type, snapshot_ver, ts, synced) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
        return len(rows)
    except sqlite3.Error:
        return 0


def record_corrections(corrections, *, doc_type: Optional[str] = None) -> int:
    """편의 함수 — 교정 목록에서 이벤트를 추출해 바로 적재. 적재 건수 반환.

    적용 당시 userdict 스냅샷 버전을 함께 기록한다(가용 시). 전 구간 graceful.
    """
    snap = None
    try:
        from core import userdict
        snap = userdict.snapshot_version()
    except Exception:
        snap = None
    return record(build_events(corrections, doc_type=doc_type, snapshot_ver=snap))


def pending(limit: int = 500) -> list:
    """서버로 보낼(synced=0) 이벤트를 dict 목록으로 반환(DO-4 push용). 없으면 []."""
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute(
            "SELECT event_id, org_id, user_id, original, corrected, action, "
            "suggest_src, category, doc_type, snapshot_ver, ts "
            "FROM events WHERE synced=0 ORDER BY ts LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        return []
    cols = ("event_id", "org_id", "user_id", "original", "corrected", "action",
            "suggest_src", "category", "doc_type", "snapshot_ver", "ts")
    return [dict(zip(cols, r)) for r in rows]


def mark_synced(event_ids) -> int:
    """주어진 event_id들을 synced=1로 표시(DO-4 push 성공 후). 갱신 건수 반환."""
    ids = [i for i in (event_ids or []) if i]
    if not ids:
        return 0
    c = _conn()
    if c is None:
        return 0
    try:
        ph = ",".join("?" * len(ids))
        cur = c.execute(f"UPDATE events SET synced=1 WHERE event_id IN ({ph})", ids)
        c.commit()
        return cur.rowcount
    except sqlite3.Error:
        return 0


def count_pending() -> int:
    c = _conn()
    if c is None:
        return 0
    try:
        return c.execute("SELECT COUNT(*) FROM events WHERE synced=0").fetchone()[0]
    except sqlite3.Error:
        return 0


def status() -> dict:
    """진단용 — DB 가용성과 카운트."""
    c = _conn()
    if c is None:
        return {"available": False, "path": str(DB_PATH)}
    try:
        total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        pend = c.execute("SELECT COUNT(*) FROM events WHERE synced=0").fetchone()[0]
        return {"available": True, "path": str(DB_PATH),
                "total": total, "pending": pend, "synced": total - pend}
    except sqlite3.Error:
        return {"available": False, "path": str(DB_PATH)}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("이벤트 큐 상태:", status())
    sample = [
        {"original": "훗가이도현의", "corrected": "홋카이도현의", "status": "accepted",
         "source": "ai_typo", "category": "외래어", "reason": ""},
        {"original": "플랫홈을", "corrected": "플랫폼을", "status": "accepted",
         "source": "dict", "category": "외래어", "reason": "[사내 용어] '플랫홈'…"},
        {"original": "있다", "corrected": "있다", "status": "accepted",
         "source": "dict_flag", "category": "검수 필요", "reason": ""},
        {"original": "오탈자", "corrected": "오타", "status": "rejected",
         "source": "ai_typo", "category": "맞춤법", "reason": ""},
    ]
    evs = build_events(sample, doc_type="보고서", snapshot_ver=0)
    print("추출된 이벤트:")
    for e in evs:
        print("  ", e)
