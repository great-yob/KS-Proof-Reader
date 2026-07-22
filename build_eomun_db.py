"""
build_eomun_db.py — 어문 규범 지식 레이어 빌더 (data/eomun.db)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
data/eomun/*.jsonl(부트스트랩 시드 + parse_haeseol_pdf.py / crawl_*.py 산출물)을
읽어 어문 규범 지식 DB를 만든다. 설계도: docs/eomun-rule-layer-architecture.md.

산출 테이블(§3):
  · rules        — 규칙 카드(KAGEC 컨텍스트, 역할 A)
  · triggers     — 오류 표면형 → rule_id 인버티드 인덱스(역할 A 검색)
  · eomun_pairs  — 결정론 규범 페어(역할 B). norm_map과 동일 형상.
  · meta         — built_at / rule_count / source_rev

eomun_pairs(B) 채택 가드(안전 우선 — build_norm_map.py 동형이의어 가드 상속):
  1. 데이터가 deterministic=true 이고 context_dependent != true (데이터 명시 동의).
  2. examples에서 추출한 (오류형, 정답형)이 단일 토큰·둘 다 한글·len>=2·공백 없음.
  3. ⚠ 동형이의어 가드 — 오류형이 stdict.db에 '독립 표준 표제어'로 존재하면 제외
     ('있다→이따' 류 재앙적 과교정 차단). stdict.db 없으면 경고 후 데이터 플래그만 신뢰.
  4. 정답형이 stdict.db에 등재(정답이 비표준이면 페어 폐기).

전 구간 멱등(DROP-재생성). JSONL은 빌드 전용이며 배포엔 eomun.db만 동봉한다.

실행:  .\\.venv64\\Scripts\\python.exe build_eomun_db.py
"""
import glob
import io
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Windows 콘솔(cp949)이 em대시·일부 기호를 인코딩 못 해 크래시하는 것을 막는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

DATA_DIR = Path(__file__).parent / "data"
SRC_DIR = DATA_DIR / "eomun"
DB_PATH = DATA_DIR / "eomun.db"
STDICT_PATH = DATA_DIR / "stdict.db"

_CATEGORIES = {"맞춤법", "띄어쓰기", "표준어", "외래어", "로마자"}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip())


def _is_single_hangul_token(w: str) -> bool:
    """공백 없는 단일 토큰이며 한글을 포함하고 2글자 이상인가."""
    return bool(w) and " " not in w and len(w) >= 2 and re.search(r"[가-힣]", w) is not None


def load_records() -> list:
    """data/eomun/*.jsonl 을 모두 읽어 레코드 리스트로 반환(중복 rule_id는 후순위 무시)."""
    files = sorted(glob.glob(str(SRC_DIR / "*.jsonl")))
    if not files:
        raise SystemExit(f"어문 규범 JSONL을 찾을 수 없습니다: {SRC_DIR}\n"
                         f"  (최소 eomun_seed.jsonl 이 있어야 합니다.)")
    records, seen_ids = [], set()
    for f in files:
        with io.open(f, encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"  [건너뜀] {Path(f).name}:{ln} JSON 오류: {e}")
                    continue
                rid = _nfc(rec.get("rule_id", ""))
                if not rid:
                    print(f"  [건너뜀] {Path(f).name}:{ln} rule_id 없음")
                    continue
                if rid in seen_ids:
                    continue   # 먼저 읽은 파일이 우선(시드 > 자동수집 덮어쓰기 방지)
                seen_ids.add(rid)
                records.append(rec)
    print(f"레코드 적재: {len(records):,}건 ({len(files)}개 파일)")
    return records


def _stdict_conn():
    if not STDICT_PATH.exists():
        print(f"  [경고] {STDICT_PATH} 없음 — 동형이의어 가드 비활성. "
              f"결정론 페어는 데이터 플래그(deterministic)만 신뢰합니다.")
        return None
    try:
        return sqlite3.connect(str(STDICT_PATH))
    except sqlite3.Error as e:
        print(f"  [경고] stdict.db 열기 실패: {e} — 동형이의어 가드 비활성")
        return None


def _is_standard_headword(conn, word: str) -> bool:
    """word 가 stdict.db에 '표준' 표제어(register 빈값/표준어/전문어/외래어)로 있는가.

    동형이의어 가드용 — 비표준 안내 등재(방언/북한어/옛말 등)는 표준 표제어가 아니다.
    번호 접미사(등장01) 형태도 함께 본다(nikl_dict.lookup_word와 동일 정신).
    """
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
        return True   # 검증 불가 → 통과(데이터 플래그 신뢰)
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


def build():
    records = load_records()
    stdict = _stdict_conn()

    rules_rows, trigger_rows, pair_rows = [], [], []
    seen_pairs, seen_triggers = {}, set()
    pair_dropped_guard = 0

    for rec in records:
        rid = _nfc(rec["rule_id"])
        category = _nfc(rec.get("category", ""))
        if category and category not in _CATEGORIES:
            print(f"  [주의] {rid}: 알 수 없는 category '{category}'")
        ok = rec.get("examples", {}).get("correct", []) or []
        bad = rec.get("examples", {}).get("incorrect", []) or []

        rules_rows.append((
            rid,
            _nfc(rec.get("regulation", "")),
            _nfc(rec.get("chapter", "")),
            _nfc(rec.get("section") or ""),
            rec.get("article_no"),
            _nfc(rec.get("rule_text", "")),
            _nfc(rec.get("gloss", "")),
            json.dumps([_nfc(x) for x in ok], ensure_ascii=False),
            json.dumps([_nfc(x) for x in bad], ensure_ascii=False),
            category,
            int(rec.get("priority", 2) or 2),
            _nfc(rec.get("source_url", "")),
        ))

        # ── triggers(A) — 명시 triggers ∪ examples.incorrect 의 단일 한글 토큰 ──
        trig_src = (rec.get("triggers") or []) + bad
        for t in trig_src:
            t = _nfc(t)
            # 괄호 주석('년도(단어 첫머리)') 제거 후 단일 토큰만 색인
            t = t.split("(")[0].strip()
            if not _is_single_hangul_token(t):
                continue
            key = (t, rid)
            if key not in seen_triggers:
                seen_triggers.add(key)
                trigger_rows.append(key)

        # ── eomun_pairs(B) — 강가드 통과분만 ──
        deterministic = bool(rec.get("deterministic", False))
        context_dep = bool(rec.get("context_dependent", False))
        if not deterministic or context_dep:
            continue   # 데이터가 결정론 동의 안 함 → 페어 미생성(컨텍스트 A로만)

        # 단일 쌍 가정: correct/incorrect 각각의 첫 단일 토큰을 (정답, 오류)로
        norm = next((_nfc(x).split("(")[0].strip() for x in ok
                     if _is_single_hangul_token(_nfc(x).split("(")[0].strip())), None)
        for b in bad:
            nonstd = _nfc(b).split("(")[0].strip()
            if not _is_single_hangul_token(nonstd) or not norm or nonstd == norm:
                continue
            # 가드 3: 오류형이 표준 표제어이면 제외(동형이의어 재앙 차단)
            if _is_standard_headword(stdict, nonstd):
                pair_dropped_guard += 1
                print(f"  [가드] '{nonstd}' 표준 표제어 — 결정론 페어 제외 ({rid})")
                continue
            # 가드 4: 정답형이 사전에 없으면 폐기
            if not _exists_in_stdict(stdict, norm):
                pair_dropped_guard += 1
                print(f"  [가드] 정답형 '{norm}' 사전 미등재 — 페어 폐기 ({rid})")
                continue
            if nonstd in seen_pairs and seen_pairs[nonstd] != norm:
                print(f"  [충돌] '{nonstd}'→'{seen_pairs[nonstd]}' vs '{norm}' — 먼저 채택분 유지")
                continue
            if nonstd not in seen_pairs:
                seen_pairs[nonstd] = norm
                pair_rows.append((nonstd, norm, rid))

    if stdict is not None:
        stdict.close()

    _write_db(rules_rows, trigger_rows, pair_rows)
    print(f"\n완료 — 규칙 {len(rules_rows):,} · 트리거 {len(trigger_rows):,} · "
          f"결정론 페어 {len(pair_rows):,} (가드 제외 {pair_dropped_guard})")


def _write_db(rules_rows, trigger_rows, pair_rows):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript("""
            DROP TABLE IF EXISTS rules;
            DROP TABLE IF EXISTS triggers;
            DROP TABLE IF EXISTS eomun_pairs;
            DROP TABLE IF EXISTS meta;
            CREATE TABLE rules (
              rule_id TEXT PRIMARY KEY, regulation TEXT, chapter TEXT, section TEXT,
              article_no INTEGER, rule_text TEXT, gloss TEXT,
              examples_ok TEXT, examples_bad TEXT, category TEXT,
              priority INTEGER, source_url TEXT
            );
            CREATE TABLE triggers (surface TEXT, rule_id TEXT, PRIMARY KEY(surface, rule_id));
            CREATE INDEX idx_triggers_surface ON triggers(surface);
            CREATE TABLE eomun_pairs (nonstd TEXT PRIMARY KEY, norm TEXT NOT NULL, rule_id TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.executemany(
            "INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rules_rows)
        conn.executemany("INSERT OR IGNORE INTO triggers VALUES (?,?)", trigger_rows)
        conn.executemany("INSERT OR IGNORE INTO eomun_pairs VALUES (?,?,?)", pair_rows)
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ("rule_count", str(len(rules_rows))),
            ("pair_count", str(len(pair_rows))),
            ("source_rev", "seed-v1"),
        ])
        conn.commit()
        print(f"적재 완료: {DB_PATH}")
        print("결정론 페어 샘플:")
        for r in conn.execute("SELECT nonstd, norm, rule_id FROM eomun_pairs LIMIT 12"):
            print(f"  {r[0]} → {r[1]}  ({r[2]})")
    finally:
        conn.close()


if __name__ == "__main__":
    build()
