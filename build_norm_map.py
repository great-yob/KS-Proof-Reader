"""
build_norm_map.py — 우리말샘 '규범 표기' 정규화 사전(보조 테이블) 빌더
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
우리말샘 JSON 뜻풀이에 박혀 있는 `⇒규범 표기는 'X'이다` 패턴에서
(비표준형 → 규범형) 매핑을 추출해 `data/stdict.db`에 **norm_map** 테이블로 적재한다.

비표준 외래어·표기(컨텐츠→콘텐츠, 수퍼마켓→슈퍼마켓, 초콜렛→초콜릿, 로보트→로봇 …)를
AI 없이 **결정론적·고신뢰**로 교정하기 위한 인프라다(설계: 사전=항상-on 인프라).
norm_map은 stdict.db 안에 함께 적재되므로 빌드 후엔 JSON 없이도 동작한다(배포엔 DB만 필요).

채택 규칙(안전 우선):
  · 단일 토큰(공백 없음) 1:1 매핑만 — 다어절/띄어쓰기 혼합 매핑은 위험해 제외.
  · 음절 경계 마커(^)·하이픈(-)·한자 병기(괄호)는 setup_dict.py와 동일하게 정리.
  · 양쪽 NFC 정규화. nonstd==norm, 한글 미포함, 2글자 미만은 제외.

실행:  .\\.venv64\\Scripts\\python.exe build_norm_map.py
"""
import glob
import io
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "stdict.db"

# 뜻풀이의 "규범 표기는 '슈퍼마켓'이다." — 직선/굽은 따옴표 모두 허용.
_NORM_RE = re.compile(r"규범\s*표기는\s*[‘’'\"]([^‘’'\"]+)[‘’'\"]\s*이다")

# '변형 지시' 뜻 — 실제 의미를 풀이하지 않고 "다른 표제어를 가리키는" 뜻.
#   "규범 표기는 'X'이다" / "'X'의 북한어·방언·옛말·준말·본말·비표준어·잘못" 형태.
#   이런 뜻만 가진 표제어는 안전하게 정규화 가능. 하나라도 '실뜻'(아래 미매칭)을
#   가지면 동형이의어이므로 매핑에서 제외한다(예: 동사 '있다').
_VARIANT_SENSE = re.compile(
    r"규범\s*표기는"
    r"|[‘'][^’']+[’']\s*의\s*(?:북한어|방언|옛말|예전\s*말|준말|본말|비표준어|잘못)"
)


def _clean(w: str) -> str:
    w = unicodedata.normalize("NFC", w or "")
    w = w.replace("-", "").replace("^", "")   # 음절 경계 마커 제거
    w = w.split("(")[0].strip()               # 한자/원어 병기 제거
    return w


def latest_export_dir() -> Path:
    """가장 최신 우리말샘 export 폴더 — update_opendict._find_json_dir와 동일 규칙.

    ⚠ 과거엔 `data/전체*json*/*.json`을 통째로 글롭해 **여러 export를 합쳐** 읽었다.
      export를 새로 받아도 옛 폴더가 남아 있으면 낡은 페어가 계속 섞여 들어온다
      (규범표기가 개정돼 사라진 매핑이 부활할 수 있음). 최신 하나만 본다.
    """
    cands = [p for p in DATA_DIR.iterdir()
             if p.is_dir() and "내려받기" in p.name and "json" in p.name
             and list(p.glob("*.json"))]
    if not cands:
        raise SystemExit(f"우리말샘 JSON export 폴더를 찾을 수 없습니다: {DATA_DIR}")
    return sorted(cands)[-1]


def extract_pairs(export_dir: Path = None) -> dict:
    d = export_dir or latest_export_dir()
    files = sorted(glob.glob(str(d / "*.json")))
    if not files:
        raise SystemExit(f"우리말샘 JSON을 찾을 수 없습니다: {d}")
    print(f"export 폴더: {d.name} ({len(files)}개 파일)")

    # ⚠ 동형이의어 가드 — 같은 표기가 '규범 표기 안내' 뜻 외에 **표준 뜻**도 가지면
    #   제외한다. 예: '있다'는 시간부사 '이따'의 비표준 뜻이 있지만 동사 '있다'가
    #   표준이므로, 매핑하면 모든 '있다'를 '이따'로 망가뜨린다(재앙적 과교정).
    #   비표준형이 **오직 규범표기 안내 뜻만** 가질 때(예: '컨텐츠'='콘텐츠'의 안내)만 채택.
    candidates: dict = {}     # nonstd -> norm  (규범표기 안내 뜻에서 추출)
    has_standard: set = set()  # 규범표기 안내가 아닌 '실뜻'을 가진 표기
    scanned = 0
    for f in files:
        try:
            data = json.load(io.open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  [건너뜀] {f}: {e}")
            continue
        for it in data.get("channel", {}).get("item", []):
            scanned += 1
            word = _clean(it.get("wordinfo", {}).get("word", ""))
            if not word:
                continue
            d = it.get("senseinfo", {}).get("definition", "")
            m = _NORM_RE.search(d) if "규범 표기" in d else None
            if m:
                norm = _clean(m.group(1))
                if (norm and word != norm and " " not in word and " " not in norm
                        and len(word) >= 2
                        and re.search(r"[가-힣]", word) and re.search(r"[가-힣]", norm)):
                    candidates.setdefault(word, norm)
            # 변형-지시 뜻이 아니면 '실뜻'(표준 용법) → 동형이의어로 보고 매핑 제외 대상.
            if not _VARIANT_SENSE.search(d):
                has_standard.add(word)

    pairs = {w: n for w, n in candidates.items() if w not in has_standard}
    dropped = len(candidates) - len(pairs)
    print(f"스캔 항목: {scanned:,} · 규범표기 후보: {len(candidates):,} · "
          f"동형이의어 가드 제외: {dropped:,} · 최종 매핑: {len(pairs):,}건")
    return pairs


def build(export_dir: Path = None):
    if not DB_PATH.exists():
        raise SystemExit(f"stdict.db 없음: {DB_PATH} (먼저 setup_dict.py 실행)")
    pairs = extract_pairs(export_dir)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("DROP TABLE IF EXISTS norm_map")
        conn.execute("CREATE TABLE norm_map (nonstd TEXT PRIMARY KEY, norm TEXT NOT NULL)")
        conn.executemany("INSERT OR IGNORE INTO norm_map VALUES(?,?)", list(pairs.items()))
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM norm_map").fetchone()[0]
        print(f"norm_map 적재 완료: {n:,}건")
        print("샘플:")
        for r in conn.execute("SELECT nonstd, norm FROM norm_map LIMIT 10"):
            print(f"  {r[0]} → {r[1]}")
    finally:
        conn.close()


if __name__ == "__main__":
    build()
