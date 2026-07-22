"""
setup_dict.py — 표준국어대사전 SQLite DB 구축
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행: python setup_dict.py

NIKL XML 구조:
  <item>
    <word_info>          ← word/pos 는 여기 안에 있음
      <word>동재-하다</word>
      <pos>동사</pos>
    </word_info>
  </item>
"""

import argparse, sqlite3, sys, time, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# 출력 스트림을 UTF-8로 강제 — 진행바(░█)·✔·⚠·→ 등 비-cp949 문자가 백그라운드/
# 리다이렉트(콘솔 코드페이지 cp949) 시 UnicodeEncodeError로 빌드를 죽이는 것을 막는다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# ⚠ 소스 저장소 교정(2026-07-22) — 세 사전 모두 **통합 저장소 하나**에서 받는다.
#   과거엔 표준국어대사전을 단독 저장소(korean-dict-nikl-stdict)에서 받았는데,
#   그 저장소는 **2019-07-16 이후 갱신이 멈춰 있다**(7년 정지). 반면 통합 저장소
#   korean-dict-nikl은 stdict/·opendict/·krdict/ 를 모두 담고 활발히 갱신된다
#   (실측 2026-06-30 커밋: "표준국어대사전 업데이트 20260605").
#   ※ 실측상 이 교체의 표제어 커버리지 개선은 0건이었다 — 신선한 우리말샘(opendict)이
#     구형 stdict의 공백 391건을 전부 덮고 있었기 때문. 그래도 같은 저장소에서 최신을
#     받는 편이 옳으므로 교체한다(추가 비용 없음).
REPO_NIKL     = "spellcheck-ko/korean-dict-nikl"    # stdict/ + opendict/ + krdict/ 통합
REPO_KRDICT   = "spellcheck-ko/korean-dict-nikl-krdict"   # (구) 단독 — 2019 정지
REPO_OPENDICT = REPO_NIKL                                  # 하위 호환 별칭
REPO_STDICT   = REPO_NIKL                                  # 하위 호환 별칭
RAW_BASE      = "https://raw.githubusercontent.com"
API_TREE    = "https://api.github.com/repos/{repo}/git/trees/master?recursive=1"
HEADERS     = {"User-Agent": "KS-Proof Reader/1.0"}
DATA_DIR    = Path(__file__).parent / "data"
DB_PATH     = DATA_DIR / "stdict.db"
BATCH       = 5_000


# ══ XML 파싱 ════════════════════════════════════════

def parse_xml(xml_bytes: bytes, source: str = "stdict"):
    """
    NIKL 표준국어대사전 XML 파싱.
    <item> → <word_info> → <word> 구조 처리.
    CDATA 자동 처리 (ElementTree 기본 동작).
    음절 경계 하이픈(-) 제거: 동재-하다 → 동재하다
    """
    try:
        if xml_bytes.startswith(b"\xef\xbb\xbf"):
            xml_bytes = xml_bytes[3:]  # BOM 제거
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f" [ParseError:{e}]", end="")
        return

    for item in root.findall(".//item"):
        # word_info 하위에서 태그 탐색 (.// = 모든 자손 검색)
        word = _txt(item, "word")
        pos  = _txt(item, "pos")
        wt   = _txt(item, "word_type")
        register = _txt(item, "register")

        if not word:
            continue

        # NIKL 음절 경계 마커 제거: 동재-하다 → 동재하다
        # 한자 병기 제거: 사과(沙果) → 사과
        word = word.replace("-", "").replace("^", "")
        word = word.split("(")[0].strip()   # 괄호 이후 제거
        word = word.strip()

        if len(word) >= 1:
            yield word, pos, wt, register, source


def _txt(elem: ET.Element, tag: str) -> str:
    """요소의 모든 자손에서 태그를 찾아 텍스트 반환"""
    node = elem.find(f".//{tag}")
    return (node.text or "").strip() if node is not None else ""


# ══ GitHub 다운로드 ══════════════════════════════════

def list_files(repo: str, folder: str = "") -> list:
    """GitHub API로 XML 파일 목록 반환 (실패 시 폴백)"""
    try:
        r = requests.get(API_TREE.format(repo=repo), headers=HEADERS, timeout=20)
        if r.status_code == 200:
            tree = r.json().get("tree", [])
            files = sorted(x["path"] for x in tree
                           if x["path"].endswith(".xml") and x["type"] == "blob"
                           and (not folder or x["path"].startswith(folder)))
            if files:
                return files
        print(f"  [API 폴백] 파일 탐색 모드… (status: {r.status_code})")
    except Exception as e:
        print(f"  [API 오류: {e}] 폴백 모드…")

    # 폴백: 알려진 범위로 직접 탐색 (GitHub API 실패 시에만)
    #   ⚠ 통합 저장소는 폴더별로 파일명 자릿수가 다르다(실측):
    #     opendict/0050000.xml (7자리) · stdict/005000.xml (6자리) · krdict/001.xml (3자리)
    found = []
    if folder == "opendict/":
        steps = range(50000, 1200000 + 50000, 50000)
        fmt = lambda n: f"{folder}{n:07d}.xml"
    elif folder == "stdict/":
        steps = range(5000, 500000 + 5000, 5000)
        fmt = lambda n: f"{folder}{n:06d}.xml"
    elif folder == "krdict/":
        steps = range(1, 12)
        fmt = lambda n: f"{folder}{n:03d}.xml"
    else:
        steps = range(5000, 70000 + 5000, 5000)
        fmt = lambda n: f"{n}.xml"

    for n in steps:
        fname = fmt(n)
        url = f"{RAW_BASE}/{repo}/master/{fname}"
        try:
            if requests.head(url, headers=HEADERS, timeout=5).status_code == 200:
                found.append(fname)
                print(f"  ✔ {fname}", end="  ", flush=True)
        except Exception:
            pass
        time.sleep(0.1)
    print()
    return found


def download(repo: str, fname: str, retries: int = 3) -> bytes:
    """파일 1개 다운로드 — 실패 시 지수 백오프로 재시도.

    opendict 청크는 ~79MB로 크고, 네트워크 블립/GitHub 일시 오류로 1개만
    실패해도 ~5만 단어가 통째로 빠진다(대행사·돌봄 누락의 추정 원인).
    HTTP 4xx/5xx도 raise_for_status로 잡아 재시도 후 실패면 예외를 던진다.
    """
    url = f"{RAW_BASE}/{repo}/master/{fname}"
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))   # 1.5s, 3s 백오프
    raise last_exc


def stream_github(repo: str, source: str = "stdict", folder: str = "", failures: list = None):
    files = list_files(repo, folder)
    if not files:
        print("  ⚠ 파일 목록 없음")
        if failures is not None:
            failures.append(f"{repo}/{folder} (파일 목록 0개)")
        return
    print(f"  {len(files)}개 파일 다운로드 시작…")
    for i, fname in enumerate(files):
        print(f"\r  [{i+1}/{len(files)}] {fname}…", end="", flush=True)
        try:
            data    = download(repo, fname)
            entries = list(parse_xml(data, source=source))
            yield from entries
        except Exception as e:
            # 조용히 넘기지 않는다 — 실패 파일을 수집해 빌드 종료 후 명시 보고.
            print(f" [실패:{e}]", end="")
            if failures is not None:
                failures.append(f"{repo}/{folder}{fname}: {e}")
        time.sleep(0.05)
    print()


# ══ ZIP 처리 (수동 다운로드) ════════════════════════

def parse_zip(zip_path: Path, source: str = "stdict"):
    with zipfile.ZipFile(zip_path) as zf:
        xmls = sorted(n for n in zf.namelist() if n.endswith(".xml"))
        print(f"  ZIP 내 XML: {len(xmls)}개")
        for name in xmls:
            with zf.open(name) as f:
                yield from parse_xml(f.read(), source=source)


# ══ SQLite 구축 ══════════════════════════════════════

def build_db(entries, db_path: Path, hint: int = 0) -> int:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS words (word TEXT NOT NULL, pos TEXT, word_type TEXT, register TEXT, source TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_word ON words(word)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_word_source ON words(word, source)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("DELETE FROM words")
    conn.commit()

    count, batch = 0, []
    for word, pos, wt, register, source in entries:
        batch.append((word, pos, wt, register, source))
        count += 1
        if len(batch) >= BATCH:
            conn.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)
            conn.commit(); batch.clear()
            if hint > 0:
                pct = min(count/hint*100, 100)
                bar = "█"*int(pct/5) + "░"*(20-int(pct/5))
                print(f"\r  [{bar}] {pct:4.0f}%  {count:,}", end="", flush=True)
            else:
                print(f"\r  {count:,}개 처리 중…", end="", flush=True)

    if batch:
        conn.executemany("INSERT INTO words VALUES(?,?,?,?,?)", batch)

    conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("entry_count", str(count)))
    conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("built_at", time.strftime("%Y-%m-%d")))
    # 데이터 버전(YYYY.MM) — 앱 버전과 독립. nikl_dict.data_version()이 읽는다.
    conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("data_version", time.strftime("%Y.%m")))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    size = db_path.stat().st_size / 1_048_576
    print(f"\n  완료: {count:,}개 어휘, {size:.1f} MB")
    return count


# ══ 진입점 ═══════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--krdict", action="store_true", help="한국어기초사전 (~6.5만)")
    ap.add_argument("--zip",    help="수동 다운로드 ZIP 경로")
    ap.add_argument("--check",  action="store_true", help="DB 상태 확인만")
    args = ap.parse_args()

    if args.check:
        if DB_PATH.exists():
            conn = sqlite3.connect(DB_PATH)
            c = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            m = conn.execute("SELECT key,value FROM meta").fetchall()
            conn.close()
            print(f"단어 수: {c:,}개")
            for k,v in m: print(f"  {k}: {v}")
        else:
            print("DB 없음")
        return

    print("=" * 50)
    repo  = REPO_KRDICT if args.krdict else REPO_STDICT
    label = "한국어기초사전" if args.krdict else "표준국어대사전"
    print(f"  {label} SQLite DB 구축")
    print("=" * 50)

    failures: list = []   # 다운로드/파싱 실패 파일 — 빌드 종료 후 명시 보고
    if args.zip:
        zp = Path(args.zip)
        if not zp.exists():
            print(f"파일 없음: {zp}"); return
        print(f"  ZIP: {zp.name}")
        entries, hint = parse_zip(zp, source="stdict"), 0
    elif args.krdict:
        print("  (한국어기초사전, ~6.5만 어휘, 약 5분 소요)")
        entries, hint = stream_github(REPO_KRDICT, source="krdict", failures=failures), 65_000
    else:
        import itertools
        print("  (표준국어대사전 + 우리말샘 전체 통합 구축, ~160만 어휘, 시간 다소 소요됨)")
        ans = input("  계속? (Y/n): ").strip().lower()
        if ans == "n": return
        # 두 제너레이터를 연결하여 순차 다운로드
        gen_stdict = stream_github(REPO_NIKL, source="stdict", folder="stdict/", failures=failures)
        gen_opendict = stream_github(REPO_NIKL, source="opendict", folder="opendict/", failures=failures)
        entries = itertools.chain(gen_stdict, gen_opendict)
        hint = 1_600_000

    # 기존 DB 삭제 후 재구축
    if DB_PATH.exists():
        DB_PATH.unlink()

    t0    = time.time()
    count = build_db(entries, DB_PATH, hint)
    elapsed = (time.time() - t0) / 60

    # ⚠ 누락 파일 보고 — 조용한 청크 누락(대행사·돌봄 미등재의 원인)을 드러낸다.
    if failures:
        print("\n" + "!" * 50)
        print(f"  ⚠ 다운로드/파싱 실패 {len(failures)}개 — DB가 불완전합니다:")
        for f in failures:
            print(f"    - {f}")
        print("  네트워크 확인 후 재실행하면 누락 청크를 복구합니다.")
        print("!" * 50)
    else:
        print("  ✔ 모든 소스 파일 정상 처리(누락 없음)")

    if count == 0:
        print()
        print("  ⚠ 데이터 0개. 조치:")
        print("    1) python setup_dict.py --krdict   (소규모 테스트)")
        print("    2) stdict.korean.go.kr 에서 직접 다운로드 후:")
        print("       python setup_dict.py --zip 파일명.zip")
    else:
        print(f"  소요: {elapsed:.1f}분 | 저장: {DB_PATH}")

if __name__ == "__main__":
    main()
