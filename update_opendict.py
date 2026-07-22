"""
update_opendict.py — 우리말샘 '전체 내려받기'로 opendict 부분 최신화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
data/에 받아둔 우리말샘 전체 export(JSON 폴더)를 파싱해 stdict.db의 opendict
부분을 통째로 교체한다. **표준국어대사전(source='stdict') 부분은 보존**한다.

setup_dict.py의 spellcheck-ko 스냅샷(2025-12)보다 최신인 공식 export로 갱신해
'대행사'·'돌봄'처럼 스냅샷에 빠졌던 실재어를 DB에 직접 채운다(거짓 검수 근절).

  · 비교 리포트: export에만 있고 현재 DB에 없던 신규 단어 수 + 대행사/돌봄 확인
  · 안전: 교체 전 stdict.db.bak 백업, 임시 DB에 빌드 후 스왑
  · register: senseinfo.type(방언/북한어/옛말) → register, 그 외 '' (검수 정책 정합)

실행:  python update_opendict.py
"""

import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

DATA = Path(__file__).parent / "data"
DB = DATA / "stdict.db"
TMP = DATA / "stdict_new.db"
BATCH = 5000


def _norm(w: str) -> str:
    """표제어 정규화 — setup_dict와 동일(음절경계 -/^ 제거, 한자병기 괄호 제거)."""
    w = (w or "").replace("-", "").replace("^", "")
    return w.split("(")[0].strip()


def _find_json_dir() -> Path:
    cands = [p for p in DATA.iterdir()
             if p.is_dir() and "내려받기" in p.name and "json" in p.name and list(p.glob("*.json"))]
    if not cands:
        raise SystemExit("  data/에서 우리말샘 JSON export 폴더를 찾을 수 없습니다.")
    return sorted(cands)[-1]


def _iter_export(d: Path):
    """export JSON → (word, pos, word_type, register, 'opendict')."""
    files = sorted(d.glob("*.json"))
    for i, f in enumerate(files):
        print(f"  [{i+1}/{len(files)}] {f.name} 파싱…", flush=True)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"    [실패:{e}]")
            continue
        for it in data.get("channel", {}).get("item", []):
            wi = it.get("wordinfo", {}) or {}
            si = it.get("senseinfo", {}) or {}
            w = _norm(wi.get("word", ""))
            if not w:
                continue
            # register는 '' 로 통일한다(옛 DB 동작 유지). 방언/북한어/옛말 플래깅은
            # 휴면 상태였고, 활성화하면 표준어 겸용어(예: '부추'=일반어+방언)가 거짓
            # 플래그되는 동형이의어 문제가 있어 의도적으로 끈다. (export의 senseinfo.type에
            # 방언/북한어/옛말 정보가 있으므로, 향후 '' 우선 lookup과 함께 활성화 가능.)
            yield (w,
                   si.get("pos", "") or "",
                   wi.get("word_type", "") or "",
                   "",
                   "opendict")


def main():
    if not DB.exists():
        raise SystemExit("  stdict.db 없음 — 먼저 setup_dict.py로 구축하세요.")
    d = _find_json_dir()
    print(f"export 폴더: {d.name}")

    # ── 현재 DB 적재 ──
    conn = sqlite3.connect(DB)
    cur_words = {r[0] for r in conn.execute("SELECT DISTINCT word FROM words")}
    cur_open = conn.execute("SELECT COUNT(*) FROM words WHERE source='opendict'").fetchone()[0]
    stdict_rows = conn.execute(
        "SELECT word,pos,word_type,register,source FROM words WHERE source='stdict'").fetchall()
    conn.close()
    print(f"현재 DB: 유니크 {len(cur_words):,} 단어 "
          f"(opendict {cur_open:,} rows · stdict {len(stdict_rows):,} rows 보존)")

    # ── 임시 DB 빌드: stdict 보존 + opendict 신규 ──
    if TMP.exists():
        TMP.unlink()
    t = sqlite3.connect(TMP)
    t.execute("CREATE TABLE words (word TEXT, pos TEXT, word_type TEXT, register TEXT, source TEXT)")
    t.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    t.executemany("INSERT INTO words VALUES(?,?,?,?,?)", stdict_rows)
    t.commit()

    print("\nexport 파싱 + opendict 적재 중 (~수분)…")
    export_words = set()
    reg_dist = {}
    batch = []
    n_open = 0
    for row in _iter_export(d):
        export_words.add(row[0])
        reg_dist[row[3]] = reg_dist.get(row[3], 0) + 1
        batch.append(row)
        n_open += 1
        if len(batch) >= BATCH:
            t.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)
            t.commit()
            batch.clear()
    if batch:
        t.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)
    t.execute("CREATE INDEX idx_word ON words(word)")
    t.execute("CREATE INDEX idx_word_source ON words(word, source)")
    total = len(stdict_rows) + n_open
    t.execute("INSERT OR REPLACE INTO meta VALUES('entry_count', ?)", (str(total),))
    t.execute("INSERT OR REPLACE INTO meta VALUES('built_at', ?)", (time.strftime("%Y-%m-%d"),))
    t.execute("INSERT OR REPLACE INTO meta VALUES('opendict_source', ?)", (d.name,))
    # 데이터 버전(YYYY.MM) — 앱 버전과 독립. 업데이터·UI가 이 값을 기준으로 판단한다.
    #   우리말샘 export 폴더명 끝의 YYYYMMDD를 우선 사용하고(무엇을 넣었는지가 진실),
    #   못 읽으면 빌드 시각으로 대체한다.
    m = re.search(r"(\d{4})(\d{2})\d{2}\s*$", d.name)
    t.execute("INSERT OR REPLACE INTO meta VALUES('data_version', ?)",
              (f"{m.group(1)}.{m.group(2)}" if m else time.strftime("%Y.%m"),))
    t.commit()
    t.execute("VACUUM")
    t.close()

    # ── 비교 리포트 ──
    gap = export_words - cur_words
    print(f"\n{'='*52}")
    print(f"  opendict 신규 적재: {n_open:,} rows · 유니크 {len(export_words):,} 단어")
    print(f"  register 분포: {reg_dist}")
    print(f"  ▶ 현재 DB에 없던 신규 단어: {len(gap):,}개")
    for w in ("대행사", "돌봄"):
        print(f"     '{w}' export에 있음? {w in export_words}  (현재 DB엔? {w in cur_words})")
    print(f"     신규 단어 샘플: {sorted(gap)[:25]}")
    print(f"{'='*52}")

    # ── 백업 + 스왑 ──
    bak = DATA / "stdict.db.bak"
    shutil.copy(DB, bak)
    for ext in ("-wal", "-shm"):
        p = Path(str(DB) + ext)
        if p.exists():
            p.unlink()
    DB.unlink()
    TMP.rename(DB)
    print(f"\n백업: {bak.name}  ·  교체 완료: {DB.name} (총 {total:,} rows)")

    # ── ⚠ 보조 테이블 복구 — 이 단계를 빼면 규범표기 교정이 통째로 죽는다 ──
    #   이 스크립트는 임시 DB를 **새로 만들어** 스왑하므로 words/meta 외의 테이블
    #   (norm_map 등)은 그대로 사라진다. 2026-07-22 실측: 우리말샘 갱신 한 번에
    #   norm_map 12,730건이 소멸 → '컨텐츠→콘텐츠' 류 결정론 교정이 전부 무력화됐고,
    #   앱은 아무 오류 없이 조용히 동작해 발견이 어려웠다(골드셋이 잡음).
    #   norm_map은 **이번에 적용한 바로 그 export**에서 파생되므로 복사가 아니라 재생성한다.
    print("\n보조 테이블 재생성: norm_map (규범표기 정규화 사전)")
    try:
        import build_norm_map
        build_norm_map.build(export_dir=d)
    except Exception as e:
        print(f"  ✗ norm_map 재생성 실패: {e}")
        print(f"  ⚠ 수동 복구:  .\\.venv64\\Scripts\\python.exe build_norm_map.py")
    print("검증을 위해:  python nikl_dict.py  또는  아래 자동 검증")

    # ── 검증 ──
    c = sqlite3.connect(DB)
    print("\n=== 검증 ===")
    for w in ("대행사", "돌봄", "상담", "채널", "고독사"):
        r = c.execute("SELECT word,source,register FROM words WHERE word=? LIMIT 1", (w,)).fetchone()
        print(f"  {w}: {r if r else '미등재'}")
    for s, cnt in c.execute("SELECT source, COUNT(*) FROM words GROUP BY source"):
        print(f"  source {s}: {cnt:,}")

    # ⚠ 불변식 게이트 — 백업 대비 테이블이 사라지지 않았는지 확인한다.
    #   조용한 소실이 이 스크립트의 유일한 치명 실패 모드이므로 여기서 크게 실패시킨다.
    ok = True
    b = sqlite3.connect(bak)
    old_tabs = {r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    new_tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    b.close()
    lost = old_tabs - new_tabs
    if lost:
        ok = False
        print(f"  ✗ 테이블 소실: {sorted(lost)} — 백업({bak.name})에서 복구 필요")
    nm = c.execute("SELECT COUNT(*) FROM norm_map").fetchone()[0] if "norm_map" in new_tabs else 0
    print(f"  norm_map: {nm:,}건 {'✔' if nm > 1000 else '✗ (비정상 — 규범표기 교정이 죽습니다)'}")
    if nm <= 1000:
        ok = False
    c.close()

    print("\n" + ("  ✔ 갱신 완료 — 골드셋 회귀를 돌려 확인하세요:\n"
                  "      .\\.venv64\\Scripts\\python.exe eval\\ai_goldset\\run_goldset.py"
                  if ok else "  ✗ 검증 실패 — 위 항목을 확인하세요."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
