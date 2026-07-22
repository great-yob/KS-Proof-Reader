"""
build_stdict_part.py — 표준국어대사전 부분 DB 빌더 (CI 전용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`spellcheck-ko/korean-dict-nikl` 의 **stdict/** 폴더만 받아 `stdict_part.db`를 만든다.
우리말샘(opendict)은 **건드리지 않는다** — 그쪽은 공식 '전체 내려받기' export를
사람이 받아 `update_opendict.py`로 반영하는 수동 경로다(로그인 필요 → 무인 자동화 불가).

  CI(반기)          : 이 스크립트 → stdict_part.db → GitHub Release 자산
  운영자(수시·수동) : update_stdict.py 로 내려받아 로컬 stdict.db의 stdict 부분만 교체
                      + update_opendict.py 로 우리말샘 부분 교체

실행:
    python build_stdict_part.py                 # stdict_part.db 생성
    python build_stdict_part.py --check-only    # 상류 변경 여부만 확인(종료코드 0=변경있음)

⚠ 왜 부분 DB인가 — 완성본 stdict.db를 CI가 만들려면 우리말샘 1.8GB export가 필요한데
  그건 로그인 뒤에 있다. 그래서 CI는 자기가 만들 수 있는 조각만 만들고, 합치는 일은
  로컬이 한다(update_stdict.py).
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dist" / "stdict_part.db"
STAMP = ROOT / "dist" / "stdict_part.json"     # 상류 커밋 SHA 기록(변경 감지용)
_UA = {"User-Agent": "KS-Proof Reader/1.0"}


def upstream_sha() -> str:
    """stdict/ 경로를 건드린 **최신 커밋 SHA** — 이게 바뀌었을 때만 재빌드한다."""
    from setup_dict import REPO_NIKL
    url = (f"https://api.github.com/repos/{REPO_NIKL}/commits"
           f"?path=stdict&per_page=1")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit("상류 커밋 정보를 읽지 못했습니다.")
    return data[0]["sha"]


def previous_sha() -> str:
    try:
        return json.loads(STAMP.read_text(encoding="utf-8")).get("sha", "")
    except Exception:
        return ""


def build(sha: str) -> int:
    """stdict/ XML을 받아 stdict_part.db(words 테이블, source='stdict')를 만든다."""
    from setup_dict import REPO_NIKL, stream_github

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(str(OUT))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE words (word TEXT NOT NULL, pos TEXT, word_type TEXT, "
                 "register TEXT, source TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    failures: list = []
    count, batch = 0, []
    for row in stream_github(REPO_NIKL, source="stdict", folder="stdict/", failures=failures):
        batch.append(row)
        count += 1
        if len(batch) >= 5000:
            conn.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)

    if failures:
        # ⚠ 조용한 청크 누락은 사전에 구멍을 내고도 앱이 정상으로 보이게 한다 — 실패로 끝낸다.
        conn.close()
        OUT.unlink(missing_ok=True)
        print(f"\n✗ 다운로드/파싱 실패 {len(failures)}건 — 불완전한 DB를 배포하지 않습니다:")
        for f in failures[:10]:
            print(f"    {f}")
        return 1

    conn.execute("CREATE INDEX idx_word ON words(word)")
    conn.execute("CREATE INDEX idx_word_source ON words(word, source)")
    for k, v in (("entry_count", str(count)), ("built_at", time.strftime("%Y-%m-%d")),
                 ("data_version", time.strftime("%Y.%m")), ("upstream_sha", sha)):
        conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    # 회귀 가드 — 표준국어대사전은 40만 표제어 규모다. 크게 모자라면 상류가 깨진 것.
    if count < 300_000:
        print(f"\n✗ 표제어 {count:,}개 — 기대치(≥300,000)에 크게 못 미칩니다. 배포 중단.")
        OUT.unlink(missing_ok=True)
        return 1

    STAMP.write_text(json.dumps({"sha": sha, "count": count,
                                 "built_at": time.strftime("%Y-%m-%d")},
                                ensure_ascii=False), encoding="utf-8")
    size = OUT.stat().st_size / 1_048_576
    print(f"\n✔ stdict_part.db 생성: {count:,} 표제어 · {size:.1f} MB · sha {sha[:8]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true",
                    help="상류 변경 여부만 확인(변경 있으면 종료코드 0, 없으면 1)")
    ap.add_argument("--force", action="store_true", help="변경 없어도 빌드")
    args = ap.parse_args()

    sha = upstream_sha()
    prev = previous_sha()
    changed = (sha != prev)
    print(f"상류 stdict/ 최신 커밋: {sha[:8]}  (이전: {prev[:8] or '없음'})  "
          f"→ {'변경됨' if changed else '변경 없음'}")

    if args.check_only:
        return 0 if changed else 1
    if not changed and not args.force:
        print("변경이 없어 빌드를 건너뜁니다(--force로 강제 가능).")
        return 1
    return build(sha)


if __name__ == "__main__":
    sys.exit(main())
