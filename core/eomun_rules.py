"""
core/eomun_rules.py — 어문 규범 지식 레이어 런타임 로더/검색 (data/eomun.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/eomun-rule-layer-architecture.md

역할:
  A. KAGEC 컨텍스트 — retrieve()로 청크에 등장한 규칙을 찾아 render()로 프롬프트 텍스트화.
  B. 결정론 페어   — lookup_eomun_pair()/batch_lookup_eomun_pair()로 비표준→규범 매핑 조회
                     (nikl_dict.lookup_norm/batch_lookup_norm과 동일 시그니처).

규율:
  · GUI-agnostic — PySide6 import 금지(core/ 규칙).
  · graceful — eomun.db 미존재/오류 시 빈 결과·None(사전·kiwi와 동일 degradation).
  · 휴면 — 이 모듈을 import해 호출하기 전까지 런타임에 아무 영향 없음.
  · 스레드 로컬 커넥션(nikl_dict와 동일 패턴).
"""

import json
import re
import sqlite3
import sys
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════
# ▌DB 경로 결정 (nikl_dict._resolve_db_path와 동일 우선순위)
# ══════════════════════════════════════════════════════

def _resolve_db_path() -> Path:
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


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


# ══════════════════════════════════════════════════════
# ▌역할 A — KAGEC 규칙 컨텍스트 검색/렌더
# ══════════════════════════════════════════════════════

@dataclass
class RuleCard:
    rule_id: str
    regulation: str
    chapter: str
    article_no: Optional[int]
    rule_text: str
    examples_ok: list
    examples_bad: list
    category: str
    priority: int

    def citation(self) -> str:
        """'한글 맞춤법 제42항' 형식의 근거 인용 문자열."""
        if self.article_no:
            return f"{self.regulation} 제{self.article_no}항"
        return self.regulation or "어문 규범"


@lru_cache(maxsize=1)
def _all_trigger_surfaces() -> tuple:
    """전체 트리거 표면형 집합(캐시). 검색 시 청크와 대조."""
    c = _conn()
    if c is None:
        return ()
    try:
        rows = c.execute("SELECT DISTINCT surface FROM triggers").fetchall()
        return tuple(r[0] for r in rows)
    except sqlite3.Error:
        return ()


def retrieve(chunk_text: str, *, limit: int = 12) -> list:
    """청크에 등장한 트리거 표면형으로 관련 규칙 카드를 반환한다.

    · 트리거(오류 표면형)가 청크의 부분문자열로 나타나면 그 규칙을 활성화한다
      (붙여쓴 형태 '갈수있다'·단어 '컨텐츠' 모두 부분문자열로 잡힘).
    · priority(1=핵심) → 트리거 매칭 수 순으로 정렬해 상한 limit만 반환(프롬프트 예산 보호).
    · DB 없으면 [] (graceful).
    """
    c = _conn()
    if c is None or not chunk_text:
        return []
    chunk = _nfc(chunk_text)
    hit_ids: dict = {}   # rule_id -> 매칭된 트리거 수
    for surface in _all_trigger_surfaces():
        if surface and surface in chunk:
            try:
                rows = c.execute(
                    "SELECT rule_id FROM triggers WHERE surface=?", (surface,)).fetchall()
            except sqlite3.Error:
                continue
            for (rid,) in rows:
                hit_ids[rid] = hit_ids.get(rid, 0) + 1
    if not hit_ids:
        return []

    try:
        placeholders = ",".join("?" * len(hit_ids))
        rows = c.execute(
            f"SELECT rule_id, regulation, chapter, article_no, rule_text, "
            f"examples_ok, examples_bad, category, priority "
            f"FROM rules WHERE rule_id IN ({placeholders})",
            list(hit_ids.keys()),
        ).fetchall()
    except sqlite3.Error:
        return []

    cards = []
    for r in rows:
        try:
            ok = json.loads(r[5]) if r[5] else []
            bad = json.loads(r[6]) if r[6] else []
        except json.JSONDecodeError:
            ok, bad = [], []
        cards.append(RuleCard(
            rule_id=r[0], regulation=r[1], chapter=r[2], article_no=r[3],
            rule_text=r[4], examples_ok=ok, examples_bad=bad,
            category=r[7], priority=r[8] or 2,
        ))
    # priority 오름차순(1 먼저), 동순위는 트리거 매칭 수 내림차순
    cards.sort(key=lambda c: (c.priority, -hit_ids.get(c.rule_id, 0)))
    return cards[:limit]


def render(cards: list) -> str:
    """규칙 카드 목록을 프롬프트용 간결 텍스트로 렌더한다(빈 목록이면 '')."""
    if not cards:
        return ""
    lines = []
    for c in cards:
        lines.append(f"- [{c.citation()}] {c.rule_text}")
        if c.examples_ok:
            lines.append(f"  · 바른 예: {' / '.join(c.examples_ok[:3])}")
        if c.examples_bad:
            lines.append(f"  · 틀린 예: {' / '.join(c.examples_bad[:3])}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# ▌역할 B — 결정론 규범 페어 (nikl_dict.lookup_norm과 동일 시그니처)
# ══════════════════════════════════════════════════════

def lookup_eomun_pair(word: str) -> Optional[str]:
    """비표준 표기의 규범형을 반환(없으면 None). 동형이의어 가드는 빌드타임 적용."""
    c = _conn()
    if c is None:
        return None
    clean = re.sub(r"[^가-힣]", "", word).strip()
    if len(clean) < 2:
        return None
    try:
        row = c.execute("SELECT norm FROM eomun_pairs WHERE nonstd=? LIMIT 1", (clean,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


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
        sample = "회의에서 컨텐츠 방향을 정했는데 갈수있다고 본다. 등교길 안전도 점검."
        print(f"\n검색 테스트 — 입력: {sample!r}")
        cards = retrieve(sample)
        print(render(cards) or "  (해당 규칙 없음)")
        print("\n결정론 페어 조회:", batch_lookup_eomun_pair({"컨텐츠", "갈수있다", "등교길"}))
