"""
update_stdict.py — 표준국어대사전 부분만 교체 (update_opendict.py의 거울상)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CI(반기)가 만든 `stdict_part.db`를 받아 로컬 `data/stdict.db`의 **source='stdict'
행만** 교체한다. 우리말샘(source='opendict')과 norm_map은 **그대로 보존**한다.

  update_opendict.py : 우리말샘 부분 교체(수동 export 기준) — stdict 보존
  update_stdict.py   : 표준국어대사전 부분 교체(CI 산출물)  — opendict·norm_map 보존

실행:
    python update_stdict.py                       # 최신 릴리스에서 자동 다운로드
    python update_stdict.py --file dist/stdict_part.db   # 로컬 파일로

⚠ 이 스크립트의 유일한 치명 실패 모드는 **보조 테이블 조용한 소실**이다
  (2026-07-22 update_opendict.py에서 실제 발생 — norm_map 12,730건 증발).
  그래서 여기서는 새 DB를 만들지 않고 **기존 DB에 트랜잭션으로 덮어쓴다**.
"""

import argparse
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "stdict.db"
_UA = {"User-Agent": "KS-Proof-Reader"}


def fetch_latest() -> Path:
    """GitHub Releases에서 최신 stdict_part.db 자산을 내려받는다."""
    import json
    from version import GITHUB_OWNER, GITHUB_REPO
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=30"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        rels = json.loads(r.read().decode("utf-8"))
    for rel in rels if isinstance(rels, list) else []:
        if not str(rel.get("tag_name", "")).startswith("stdict-"):
            continue
        for a in rel.get("assets") or []:
            if a["name"].endswith(".db"):
                tmp = Path(tempfile.mkdtemp(prefix="ks-stdict-")) / a["name"]
                print(f"  내려받는 중: {rel['tag_name']} / {a['name']} "
                      f"({a.get('size', 0)/1048576:.0f}MB)")
                urllib.request.urlretrieve(a["browser_download_url"], tmp)
                return tmp
    raise SystemExit("  stdict- 태그의 릴리스 자산을 찾지 못했습니다.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="로컬 stdict_part.db 경로(생략 시 최신 릴리스)")
    args = ap.parse_args()

    if not DB.exists():
        raise SystemExit(f"  stdict.db 없음: {DB}")
    part = Path(args.file) if args.file else fetch_latest()
    if not part.exists():
        raise SystemExit(f"  부분 DB 없음: {part}")

    # ── 검증 먼저 ──
    p = sqlite3.connect(str(part))
    n_new = p.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    meta_new = dict(p.execute("SELECT key,value FROM meta"))
    if n_new < 300_000:
        p.close()
        raise SystemExit(f"  ✗ 부분 DB 표제어 {n_new:,}개 — 비정상. 중단합니다.")
    rows = p.execute("SELECT word,pos,word_type,register,source FROM words").fetchall()
    p.close()

    c = sqlite3.connect(str(DB))
    before = dict(c.execute("SELECT source, COUNT(*) FROM words GROUP BY source"))
    tabs_before = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    print(f"현재 DB: {before} · 테이블 {sorted(tabs_before)}")
    print(f"새 stdict 부분: {n_new:,} rows (상류 sha {meta_new.get('upstream_sha','?')[:8]})")

    bak = DB.with_suffix(".db.bak")
    c.close()
    shutil.copy(DB, bak)
    print(f"백업: {bak.name}")

    # ── 트랜잭션 교체 — 새 DB를 만들지 않으므로 보조 테이블이 사라질 수 없다 ──
    c = sqlite3.connect(str(DB))
    try:
        c.execute("BEGIN")
        c.execute("DELETE FROM words WHERE source='stdict'")
        c.executemany("INSERT INTO words VALUES(?,?,?,?,?)", rows)
        for k in ("data_version", "built_at"):
            if k in meta_new:
                c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, meta_new[k]))
        c.execute("INSERT OR REPLACE INTO meta VALUES('stdict_upstream_sha',?)",
                  (meta_new.get("upstream_sha", ""),))
        c.execute("INSERT OR REPLACE INTO meta VALUES('entry_count',?)",
                  (str(c.execute("SELECT COUNT(*) FROM words").fetchone()[0]),))
        c.commit()
    except Exception as e:
        c.rollback()
        c.close()
        shutil.copy(bak, DB)
        raise SystemExit(f"  ✗ 교체 실패, 백업 복원함: {e}")

    # ── 불변식 게이트 ──
    after = dict(c.execute("SELECT source, COUNT(*) FROM words GROUP BY source"))
    tabs_after = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    nm = c.execute("SELECT COUNT(*) FROM norm_map").fetchone()[0] if "norm_map" in tabs_after else 0
    c.close()

    ok = True
    print(f"\n교체 후: {after}")
    if tabs_before - tabs_after:
        ok = False
        print(f"  ✗ 테이블 소실: {sorted(tabs_before - tabs_after)}")
    if after.get("opendict", 0) != before.get("opendict", -1):
        ok = False
        print("  ✗ 우리말샘(opendict) 행 수가 변했습니다 — 보존 실패")
    print(f"  norm_map: {nm:,}건 {'✔' if nm > 1000 else '✗ 비정상'}")
    if nm <= 1000:
        ok = False
    print("\n" + ("  ✔ 완료 — 골드셋 회귀를 돌려 확인하세요:\n"
                  "      .\\.venv64\\Scripts\\python.exe eval\\ai_goldset\\run_goldset.py"
                  if ok else "  ✗ 검증 실패 — 백업(stdict.db.bak)에서 복원을 검토하세요."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
