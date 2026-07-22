"""
build_dist.py — 배포본 빌드 (PyInstaller)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행:  .\.venv64\Scripts\python.exe build_dist.py
옵션:  --clean       기존 build/·dist/ 삭제 후 빌드
       --no-keys     조직 키 주입 없이 빌드(사용자가 config.ini를 직접 넣는 배포)
       --console     콘솔 창 표시(디버깅용)

하는 일:
  1. 키 수집 — config.ini / 환경변수에서 3종(GEMINI·NIKL·ONTERM)을 읽어
     Fernet으로 암호화한 `core/_org_keys.py`를 **생성**한다.
     → 사용자 PC에 config.ini가 없어도 3종 API가 전부 동작한다.
  2. PyInstaller 실행 — assets/·data/·kiwipiepy 모델·QtSvg 플러그인을 번들.
  3. 빌드 후 `core/_org_keys.py`를 **반드시 삭제**한다(레포에 평문 잔존 방지).

⚠ 절대 번들하면 안 되는 것 (아래 _EXCLUDE_PATTERNS로 차단):
    key.txt              — 우리말샘 계정 ID·비밀번호·키 평문
    config.ini           — 개발자 개인 키 (배포본은 _org_keys.py를 쓴다)
    교정샘플/            — 고객 원고(개인정보·저작물)
    korean-ambiguity-data/ — CC BY-NC 평가 자산(상업 배포 시 라이선스 위반)
    data/api_cache.db    — 개발 PC 조회 캐시(불필요·프라이버시)
    data/event_queue.db  — 로컬 이벤트 큐
  ⚠ PyInstaller에 레포 루트를 통째로 include 하는 규칙을 추가하지 말 것.

산출물: dist/KS-Proof Reader/  (onedir — data/stdict.db가 160MB라 onefile은
        실행 때마다 임시폴더로 압축 해제해 기동이 매우 느려진다)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
APP_NAME = "KS-Proof Reader"
ORG_KEYS_PATH = ROOT / "core" / "_org_keys.py"

# 번들에서 제외할 경로 조각 — PyInstaller datas 수집 시 필터로 쓴다.
_EXCLUDE_PATTERNS = (
    "key.txt", "config.ini", "교정샘플", "korean-ambiguity-data",
    "api_cache.db", "event_queue.db", "__pycache__", ".venv",
    "stdict.db.bak", "userdict/snapshot.json",
)


def _excluded(p: Path) -> bool:
    s = str(p).replace("\\", "/")
    return any(pat in s for pat in _EXCLUDE_PATTERNS)


# ══════════════════════════════════════════════════════
# ▌1. 조직 키 주입
# ══════════════════════════════════════════════════════

def collect_keys() -> dict:
    """config.ini/환경변수에서 3종 키를 모은다. 없는 키는 빠진다(부분 주입 허용)."""
    import configparser
    cfg = configparser.ConfigParser()
    ini = ROOT / "config.ini"
    if ini.exists():
        cfg.read(ini, encoding="utf-8")

    def pick(env: str, ini_key: str) -> str:
        for v in (os.environ.get(env, ""),
                  cfg.get("API", ini_key, fallback="")):
            v = (v or "").strip()
            if v and not v.upper().startswith("YOUR"):
                return v
        return ""

    return {k: v for k, v in {
        "GEMINI": pick("GEMINI_API_KEY", "GEMINI_API_KEY"),
        "NIKL":   pick("NIKL_API_KEY",   "NIKL_API_KEY"),
        "ONTERM": pick("ONTERM_API_KEY", "ONTERM_API_KEY"),
    }.items() if v}


def write_org_keys(keys: dict) -> None:
    """core/_org_keys.py 생성 — Fernet 암호화된 키 + 복호화 키를 함께 담는다.

    ⚠ 같은 파일에 복호화 키가 있으니 '난독화'이지 '암호화 보안'이 아니다.
      평문 grep을 막는 수준이며, 사내 배포 전제다(config_loader 주석 참조).
    """
    from cryptography.fernet import Fernet
    fkey = Fernet.generate_key()
    f = Fernet(fkey)
    enc = {name: f.encrypt(val.encode("utf-8")) for name, val in keys.items()}
    body = ",\n".join(f"    {name!r}: {blob!r}" for name, blob in enc.items())
    ORG_KEYS_PATH.write_text(
        "# 자동 생성 파일 — build_dist.py가 만들고 빌드 후 삭제한다.\n"
        "# ⚠ 커밋 금지(.gitignore 등록됨). 손으로 편집하지 말 것.\n"
        f"FERNET_KEY = {fkey!r}\n"
        f"ENCRYPTED = {{\n{body}\n}}\n",
        encoding="utf-8")


def cleanup_org_keys() -> None:
    if ORG_KEYS_PATH.exists():
        ORG_KEYS_PATH.unlink()
    pyc = ORG_KEYS_PATH.parent / "__pycache__"
    for f in pyc.glob("_org_keys.*"):
        try:
            f.unlink()
        except OSError:
            pass


# ══════════════════════════════════════════════════════
# ▌2. PyInstaller 인자 구성
# ══════════════════════════════════════════════════════

def data_args() -> list:
    """--add-data 인자 목록.

    ⚠ 사전 DB·kiwipiepy 모델은 **번들에 넣지 않는다** — 빌드 후 EXE 옆 `data/`로
      복사해 앱 패키지와 데이터 패키지를 따로 배포한다(datapaths.py 헤더 참조).
      번들 내부(_internal)에 넣으면 데이터만 따로 교체할 수 없다.
      여기서 넣는 건 코드와 함께 움직이는 것(assets/)뿐이다.
    """
    args = []
    if (ROOT / "assets").is_dir():
        args += ["--add-data", f"{ROOT / 'assets'}{os.pathsep}assets"]
    return args


def stage_data(outdir: Path) -> Path:
    """EXE 옆 `data/`에 데이터 자산을 배치한다 → datapaths.app_dir()/data 로 발견된다."""
    dst = outdir / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted((ROOT / "data").glob("*.db")):
        if not _excluded(p):
            shutil.copy2(p, dst / p.name)
    for sub in ("eomun", "userdict"):
        d = ROOT / "data" / sub
        if d.is_dir() and not _excluded(d):
            shutil.copytree(d, dst / sub, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("snapshot.json", "__pycache__"))
    # kiwipiepy 모델(~105MB) — 코드가 아니라 자산이고 kiwipiepy 버전에 묶여 드물게 바뀐다.
    try:
        import kiwipiepy_model
        src = Path(kiwipiepy_model.__file__).parent
        shutil.copytree(src, dst / "kiwipiepy_model", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    except Exception as e:
        print(f"  ⚠ kiwipiepy_model 복사 실패: {e} — 형태소 분석이 비활성화될 수 있습니다.")
    return dst


def _zip_dir(src: Path, zpath: Path, skip_top: set = frozenset()) -> int:
    """폴더를 zip으로. skip_top에 있는 최상위 항목은 제외. 반환: 바이트 크기."""
    import zipfile
    zpath.parent.mkdir(parents=True, exist_ok=True)
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            if rel.parts and rel.parts[0] in skip_top:
                continue
            z.write(p, rel.as_posix())
    return zpath.stat().st_size


def package(outdir: Path, app_ver: str, data_ver: str) -> list:
    """세 개의 릴리스 산출물을 만든다.

      full : 최초 설치용(앱+데이터)        — 크다, 드물게
      app  : 코드 업데이트용(데이터 제외)  — 자주
      data : 사전 업데이트용(데이터만)     — 드물게
    """
    rel = ROOT / "dist" / "release"
    made = []
    print("\n[4/4] 릴리스 패키징 (zip 압축 — 수 분)")
    specs = [
        (f"KS-Proof-Reader-{app_ver}-full.zip", outdir, set(),        "최초 설치"),
        (f"KS-Proof-Reader-{app_ver}-app.zip",  outdir, {"data"},     "앱 업데이트"),
        (f"KS-Proof-Reader-data-{data_ver}.zip", outdir / "data", set(), "데이터 업데이트"),
    ]
    for name, src, skip, label in specs:
        if not src.exists():
            continue
        size = _zip_dir(src, rel / name, skip)
        made.append(rel / name)
        print(f"  ✔ {label:9} {name:44} {size/1048576:6.0f} MB")
    return made


def build(console: bool, clean: bool) -> int:
    icon = ROOT / "assets" / "icon.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",                      # ⚠ onefile 금지 — 160MB DB 압축해제로 기동 지연
        "--name", APP_NAME,
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # kiwipiepy는 **코드만** 번들한다(_kiwipiepy.pyd). 모델(~105MB)은 자산이라
        #   stage_data()가 EXE 옆 data/kiwipiepy_model/ 로 따로 배치한다(분리 배포).
        #   ⚠ --collect-data kiwipiepy_model 을 되살리면 모델이 번들에도 들어가 중복 105MB.
        "--collect-submodules", "kiwipiepy",
        "--exclude-module", "kiwipiepy_model",
        # SVG 아이콘 렌더링에 필요(CLAUDE.md: QtSvg + qsvg 이미지 플러그인).
        "--hidden-import", "PySide6.QtSvg",
        # COM 브리지가 지연 import하는 모듈들(32비트 워커 쪽과 별개로 64비트에서도 필요).
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "win32timezone",
        # 앱이 쓰지 않는 무거운 패키지 제외 — 배포 크기 절감.
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pdfplumber",
        # ⚠ torch/transformers 계열은 **제거된 KoGEC(NLLB GEC) 엔진의 잔재**다.
        #   (KoGEC는 2026-06-17 전면 제거 — CLAUDE.md 참조. 재도입 금지)
        #   .venv64엔 아직 남아 있어 PyInstaller가 끌어오는데, 소스 어디서도 import하지
        #   않는다. 빼면 배포본이 약 960MB → 520MB로 줄고 자동 업데이트 다운로드도 그만큼
        #   가벼워진다. ⚠ 나중에 검증된 로컬 모델을 도입한다면 이 제외부터 풀 것.
        "--exclude-module", "torch",
        "--exclude-module", "transformers",
        "--exclude-module", "tokenizers",
        "--exclude-module", "huggingface_hub",
        "--exclude-module", "hf_xet",
        "--exclude-module", "safetensors",
    ]
    if clean:
        cmd.append("--clean")
    cmd.append("--console" if console else "--windowed")
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd += data_args()
    cmd.append(str(ROOT / "main.py"))

    print("  실행:", " ".join(cmd[:8]), "…")
    return subprocess.call(cmd, cwd=str(ROOT))


# ══════════════════════════════════════════════════════
# ▌3. 빌드 후 검증
# ══════════════════════════════════════════════════════

def verify(outdir: Path) -> bool:
    """유출 금지 파일이 산출물에 섞이지 않았는지 확인 — 실패 시 빌드를 무효로 본다."""
    ok = True
    leaked = []
    for p in outdir.rglob("*"):
        if p.is_file() and p.name in ("key.txt", "config.ini"):
            leaked.append(p)
    if leaked:
        ok = False
        print("  ✗ 유출 금지 파일이 배포본에 포함됨:")
        for p in leaked:
            print(f"      {p}")
    for sub in ("교정샘플", "korean-ambiguity-data"):
        hits = list(outdir.rglob(sub))
        if hits:
            ok = False
            print(f"  ✗ '{sub}' 가 배포본에 포함됨: {hits[0]}")
    exe = outdir / f"{APP_NAME}.exe"
    if not exe.exists():
        ok = False
        print(f"  ✗ 실행 파일 없음: {exe}")
    else:
        print(f"  ✔ 실행 파일: {exe}")
    # 데이터는 EXE 옆 data/ 에 있어야 한다(번들 내부가 아니라) — 그래야 따로 교체된다.
    for rel, why in (("data/stdict.db", "사전 기능"),
                     ("data/kiwipiepy_model", "형태소 분석")):
        p = outdir / rel
        if not p.exists():
            ok = False
            print(f"  ✗ {rel} 없음 — {why}이 죽습니다")
        else:
            print(f"  ✔ {rel}")
    # 번들 내부에 데이터가 중복으로 들어가지 않았는지(분리 배포가 무의미해짐)
    dup = outdir / "_internal" / "data" / "stdict.db"
    if dup.exists():
        ok = False
        print("  ✗ 번들 내부에도 stdict.db가 있습니다 — 분리 배포가 깨집니다")
    # ⚠ 동봉한 kiwi 모델이 **실제로 쓸 수 있는지**(파일 온전 + 버전 호환) 확인한다.
    #   못 쓰는 모델을 배포하면 형태소 분석이 통째로 죽는다(가드 덕에 크래시는 면하지만
    #   활용형 복원·띄어쓰기 백스톱·인명 가드가 전부 사라져 교정 품질이 조용히 무너진다).
    try:
        from datapaths import kiwi_model_ok, kiwi_model_version
        md = outdir / "data" / "kiwipiepy_model"
        if md.is_dir():
            import kiwipiepy
            good = kiwi_model_ok(md)
            print(f"  {'✔' if good else '✗'} kiwi 모델 호환: "
                  f"model {kiwi_model_version(md)} ↔ kiwipiepy {kiwipiepy.__version__}")
            if not good:
                ok = False
                print("     → 마이너 버전이 어긋났습니다. kiwipiepy와 kiwipiepy_model을 "
                      "같은 마이너로 맞춘 뒤 재빌드하세요.")
    except Exception as e:
        print(f"  ⚠ kiwi 모델 검증 생략: {e}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="build/·dist/ 삭제 후 빌드")
    ap.add_argument("--no-keys", action="store_true", help="조직 키 주입 없이 빌드")
    ap.add_argument("--console", action="store_true", help="콘솔 창 표시(디버깅)")
    ap.add_argument("--no-zip", action="store_true", help="릴리스 zip 패키징 생략(빠른 반복)")
    args = ap.parse_args()

    try:
        from version import APP_VERSION, DATA_VERSION
    except Exception:
        APP_VERSION, DATA_VERSION = "0.0.0", "0000.00"
    # 데이터 버전은 실제 DB의 meta 값을 우선한다(패키징 값과 어긋나면 그게 진실).
    try:
        import sqlite3
        _c = sqlite3.connect(str(ROOT / "data" / "stdict.db"))
        _v = _c.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
        _c.close()
        if _v and _v[0]:
            if _v[0] != DATA_VERSION:
                print(f"  ⚠ version.py DATA_VERSION({DATA_VERSION}) ≠ "
                      f"DB meta({_v[0]}) — DB 값을 씁니다. version.py도 맞춰 주세요.")
            DATA_VERSION = str(_v[0])
    except Exception:
        pass
    print("=" * 58)
    print(f"  {APP_NAME} 배포본 빌드   앱 v{APP_VERSION} · 데이터 {DATA_VERSION}")
    print("=" * 58)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("  ✗ PyInstaller 미설치. 먼저 설치하세요:")
        print("      .\\.venv64\\Scripts\\python.exe -m pip install pyinstaller")
        return 1

    if args.clean:
        for d in ("build", "dist"):
            shutil.rmtree(ROOT / d, ignore_errors=True)
        print("  기존 build/·dist/ 삭제")

    # ── 1) 키 주입 ──
    if args.no_keys:
        print("\n[1/3] 조직 키 주입 생략(--no-keys) — 사용자가 config.ini를 넣어야 합니다.")
        cleanup_org_keys()
    else:
        keys = collect_keys()
        if not keys:
            print("\n  ✗ 주입할 키가 없습니다. config.ini [API]에 키를 넣거나 --no-keys 를 쓰세요.")
            return 1
        missing = {"GEMINI", "NIKL", "ONTERM"} - set(keys)
        write_org_keys(keys)
        print(f"\n[1/3] 조직 키 주입: {', '.join(sorted(keys))} → core/_org_keys.py")
        if "GEMINI" not in keys:
            print("  ⚠ GEMINI 키가 없습니다 — 배포본에서 AI 교정이 동작하지 않습니다.")
        if missing:
            print(f"  · 미주입(해당 기능만 graceful 비활성): {', '.join(sorted(missing))}")

    # ── 2) 빌드 ──
    print("\n[2/4] PyInstaller 빌드 중… (수 분 소요)")
    try:
        rc = build(console=args.console, clean=args.clean)
    finally:
        # ⚠ 성공/실패/예외 무관하게 평문 키 모듈을 지운다.
        cleanup_org_keys()
        print("  · core/_org_keys.py 삭제 완료(레포에 평문 잔존 없음)")
    if rc != 0:
        print(f"\n  ✗ 빌드 실패 (exit {rc})")
        return rc

    outdir = ROOT / "dist" / APP_NAME
    print("\n[3/4] 데이터 배치 (EXE 옆 data/)")
    stage_data(outdir)
    ok = verify(outdir)
    if not ok:
        print("\n  ✗ 검증 실패 — 위 항목을 해결한 뒤 재빌드하세요.")
        return 1

    # ── 4) 패키징 ──
    if args.no_zip:
        print("\n[4/4] 패키징 생략(--no-zip)")
    else:
        package(outdir, APP_VERSION, DATA_VERSION)

    print("\n" + "=" * 58)
    print(f"  ✔ 완료 → {outdir}")
    print(f"  릴리스 태그:  앱 v{APP_VERSION}  ·  데이터 data-{DATA_VERSION}")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
