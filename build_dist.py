"""
build_dist.py — 배포본 빌드 (PyInstaller)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행:  .\.venv64\Scripts\python.exe build_dist.py
옵션:  --clean          기존 build/·dist/ 삭제 후 빌드
       --no-keys        조직 키 주입 없이 빌드(사용자가 config.ini를 직접 넣는 배포)
       --console        콘솔 창 표시(디버깅용)
       --skip-bridge    32비트 브리지 재빌드 생략(직전 build/bridge32 재사용 — 빠른 반복)
       --no-installer   설치 파일(setup.exe) 생성 생략
       --no-zip         zip 패키징 생략

하는 일:
  1. 키 수집 — config.ini / 환경변수에서 3종(GEMINI·NIKL·ONTERM)을 읽어
     Fernet으로 암호화한 `core/_org_keys.py`를 **생성**한다.
     → 사용자 PC에 config.ini가 없어도 3종 API가 전부 동작한다.
  2. PyInstaller 실행(64비트) — assets/·QtSvg 플러그인을 번들.
  3. **32비트 HWP 브리지 빌드** — 한/글 COM은 32비트 전용이라 워커를 32비트 파이썬으로
     따로 얼려 `bridge32/`에 동봉한다. 이게 없으면 사용자 PC에 32비트 Python과
     pywin32가 설치돼 있어야 하고, 실제로 그래서 배포본의 HWP 교정이 죽어 있었다.
  4. 빌드 후 `core/_org_keys.py`를 **반드시 삭제**한다(레포에 평문 잔존 방지).
  5. 패키징 — 설치 파일(Inno Setup) + 업데이트용 zip 2종.

⚠ 절대 번들하면 안 되는 것 (아래 _EXCLUDE_PATTERNS로 차단):
    key.txt              — 우리말샘 계정 ID·비밀번호·키 평문
    config.ini           — 개발자 개인 키 (배포본은 _org_keys.py를 쓴다)
    교정샘플/            — 고객 원고(개인정보·저작물)
    korean-ambiguity-data/ — CC BY-NC 평가 자산(상업 배포 시 라이선스 위반)
    data/api_cache.db    — 개발 PC 조회 캐시(불필요·프라이버시)
    data/event_queue.db  — 로컬 이벤트 큐
  ⚠ PyInstaller에 레포 루트를 통째로 include 하는 규칙을 추가하지 말 것.

산출물: dist/KS-AI Editor/  (onedir — data/stdict.db가 160MB라 onefile은
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
# ⚠ 제품 표시 이름 = EXE 파일명 + dist 폴더명 + 설치 폴더명 + 릴리스 자산 접두사의
#   **단일 출처**. 여기만 바꾸면 빌드 산출물 전체가 따라온다.
#   ⚠ GitHub 저장소명(great-yob/KS-Proof-Reader)·릴리스 태그(v1.0.1)는 **별개**다 —
#     그건 업데이터가 폴링하는 URL이라 version.py에 그대로 두고 여기서 바꾸지 않는다.
#   ⚠ install_app()은 실행 중 EXE 이름과 app.zip 속 EXE 이름이 **같아야** 업데이트를
#     적용한다. 이 값을 바꾸면 EXE 이름이 바뀌므로, 이미 배포된 구(舊)이름 설치본은
#     이 이름의 새 빌드로 **자동 업데이트되지 않는다**(설치 파일로 1회 재설치 필요).
APP_NAME = "KS-AI Editor"
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


# ══════════════════════════════════════════════════════
# ▌2-b. 32비트 HWP 브리지 (한/글 COM은 32비트 전용)
# ══════════════════════════════════════════════════════
#
# 앱은 64비트, 한/글 COM 서버는 32비트라 워커를 별도 프로세스로 띄운다(core/hwp_editor.py).
# 개발 PC에선 32비트 Python이 깔려 있어 `python32 hwp_bridge_worker.py`로 됐지만,
# **배포본에서는 그게 성립하지 않는다** — 사용자 PC에 32비트 Python+pywin32가 있을 리 없고,
# 애초에 PyInstaller가 .py 스크립트를 번들에 넣지도 않았다(=배포본 HWP 교정 전면 불능).
# → 워커를 32비트 파이썬으로 따로 얼려 `bridge32/`에 동봉한다.

BRIDGE_DIR_NAME = "bridge32"
BRIDGE_EXE_NAME = "hwp_bridge_worker.exe"
_BRIDGE_BUILD = ROOT / "build" / "bridge32"          # PyInstaller distpath
_BRIDGE_OUT = _BRIDGE_BUILD / "hwp_bridge_worker"    # onedir 결과 폴더

_PYTHON32_CANDIDATES = (
    r"C:\Users\user9\AppData\Local\Programs\Python\Python311-32\python.exe",
    r"C:\Python311-32\python.exe",
    r"C:\Python310-32\python.exe",
)


def _pe_bits(exe: Path):
    """PE 헤더에서 아키텍처를 읽는다 → 32 / 64 / None.

    ⚠ 이 검사가 있어야 '실수로 64비트 파이썬으로 브리지를 빌드'한 배포본을 잡는다.
      그런 배포본은 설치는 되지만 한/글 COM 생성에서 사용자 PC에서만 실패한다.
    """
    try:
        with open(exe, "rb") as f:
            f.seek(0x3C)
            off = int.from_bytes(f.read(4), "little")
            f.seek(off)
            if f.read(4) != b"PE\0\0":
                return None
            machine = int.from_bytes(f.read(2), "little")
        return {0x014C: 32, 0x8664: 64}.get(machine)
    except OSError:
        return None


def find_python32():
    """32비트 Python 실행 파일. 환경변수 PYTHON32_PATH → 알려진 경로 순. 없으면 None."""
    cands = [os.environ.get("PYTHON32_PATH", "")] + list(_PYTHON32_CANDIDATES)
    for c in cands:
        if c and Path(c).is_file() and _pe_bits(Path(c)) == 32:
            return Path(c)
    return None


def build_bridge32(skip: bool = False, clean: bool = False) -> bool:
    """32비트 파이썬으로 hwp_bridge_worker를 onedir 빌드한다."""
    exe = _BRIDGE_OUT / BRIDGE_EXE_NAME
    if skip and exe.is_file():
        print(f"  · 재사용(--skip-bridge): {exe}")
        return True

    py32 = find_python32()
    if py32 is None:
        print("  ✗ 32비트 Python을 찾을 수 없습니다.")
        print("     환경변수 PYTHON32_PATH를 지정하거나 32비트 Python 3.11을 설치한 뒤")
        print("     `pip install pywin32 pyinstaller` 하세요.")
        return False
    print(f"  · 32비트 Python: {py32}")

    cmd = [
        str(py32), "-m", "PyInstaller", "--noconfirm", "--onedir",
        # ⚠ --console 이어야 한다. --windowed(noconsole) 빌드는 stdout/stderr가 막혀
        #   JSON 라인 프로토콜이 통째로 죽는다. 창은 부모가 CREATE_NO_WINDOW로 숨긴다.
        "--console",
        "--name", "hwp_bridge_worker",
        "--distpath", str(_BRIDGE_BUILD),
        "--workpath", str(ROOT / "build" / "bridge32-work"),
        "--specpath", str(ROOT / "build" / "bridge32-spec"),
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "win32timezone",
        # 32비트 파이썬엔 무거운 패키지가 잔뜩 깔려 있다 — 워커가 안 쓰는 건 차단.
        # ⚠ pyhwpx 제외가 핵심이다. 워커의 `import pyhwpx`(보안 DLL 탐색용, try 안)를
        #   PyInstaller가 정적 분석으로 집어 PyQt5까지 끌어와 63MB가 붙는다(실측 85MB).
        #   우리가 원하는 건 pyhwpx 패키지가 아니라 그 안의 DLL 한 개뿐이고, 그건
        #   아래에서 파일로 복사한다. 제외하면 워커는 동봉 DLL 경로를 먼저 보므로 무해.
        "--exclude-module", "pyhwpx",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PySide6",
        "--exclude-module", "tkinter",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
    ]
    if clean:
        cmd.append("--clean")
    cmd.append(str(ROOT / "core" / "hwp_bridge_worker.py"))

    rc = subprocess.call(cmd, cwd=str(ROOT))
    if rc != 0 or not exe.is_file():
        print(f"  ✗ 32비트 브리지 빌드 실패 (exit {rc})")
        return False

    # 보안 모듈 DLL 동봉 — 한/글 '파일 접근 허용' 팝업을 우회한다(있으면 좋은 것).
    #   ⚠ RegisterModule은 등록된 COM ProgID를 받으므로 DLL을 복사한다고 자동 등록되진
    #     않는다. 없으면 워커가 기본 SecurityModule로 graceful 폴백한다.
    try:
        out = subprocess.run(
            [str(py32), "-c",
             "import pyhwpx,os;print(os.path.dirname(pyhwpx.__file__))"],
            capture_output=True, text=True, timeout=30)
        d = Path(out.stdout.strip())
        dll = d / "FilePathCheckerModule.dll"
        if dll.is_file():
            shutil.copy2(dll, _BRIDGE_OUT / dll.name)
            print(f"  · 보안 모듈 DLL 동봉: {dll.name}")
    except Exception:
        pass
    return True


def stage_bridge32(outdir: Path) -> bool:
    """빌드된 브리지를 EXE 옆 `bridge32/`로 배치한다."""
    if not (_BRIDGE_OUT / BRIDGE_EXE_NAME).is_file():
        return False
    dst = outdir / BRIDGE_DIR_NAME
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(_BRIDGE_OUT, dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return True


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


_ISCC_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe",
    r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe",
    r"%ProgramFiles%\Inno Setup 6\ISCC.exe",
)


def find_iscc():
    """Inno Setup 컴파일러(ISCC.exe). 환경변수 ISCC_PATH → 알려진 경로 → PATH."""
    for c in (os.environ.get("ISCC_PATH", ""), *_ISCC_CANDIDATES):
        if c:
            p = Path(os.path.expandvars(c))
            if p.is_file():
                return p
    w = shutil.which("ISCC")
    return Path(w) if w else None


def build_installer(app_ver: str):
    """설치 파일(setup.exe)을 만든다. Inno Setup이 없으면 None(빌드는 계속)."""
    iscc = find_iscc()
    iss = ROOT / "installer" / "KS-Proof-Reader.iss"
    if iscc is None:
        print("  ⚠ Inno Setup(ISCC.exe)을 찾을 수 없어 설치 파일을 건너뜁니다.")
        print("     설치:  winget install --id JRSoftware.InnoSetup -e")
        return None
    if not iss.is_file():
        print(f"  ⚠ 설치 스크립트 없음: {iss}")
        return None

    # 경로는 .iss가 자기 위치 기준으로 계산한다 — 공백 있는 경로를 /D로 넘기지 않는다.
    rc = subprocess.call([str(iscc), f"/DAppVersion={app_ver}", str(iss)],
                         cwd=str(ROOT))
    # ⚠ .iss의 OutputBaseFilename({#AppName}-Setup-{ver})과 반드시 일치해야 한다.
    out = ROOT / "dist" / "release" / f"{APP_NAME}-Setup-{app_ver}.exe"
    if rc != 0 or not out.is_file():
        print(f"  ✗ 설치 파일 생성 실패 (exit {rc})")
        return None
    return out


def package(outdir: Path, app_ver: str, data_ver: str, installer: bool = True) -> list:
    """릴리스 산출물을 만든다.

      setup.exe : 최초 설치용(앱+데이터)   — 사용자가 받는 것
      app.zip   : 코드 업데이트용(데이터 제외) — updater 앱 채널
      data.zip  : 사전 업데이트용(데이터만)    — updater 데이터 채널

    ⚠ full.zip은 setup.exe로 대체됐다(2026-07-22). 업데이터의 `_pick_asset`이
      '-app.zip' 없을 때 '-full.zip'을 찾는 폴백을 갖고 있으나, 앱 릴리스엔 항상
      app.zip을 올리므로 문제되지 않는다.
    """
    rel = ROOT / "dist" / "release"
    made = []
    print("\n[5/5] 릴리스 패키징")
    if installer:
        print("  · 설치 파일 컴파일 (Inno Setup lzma2/max — 수 분)")
        setup = build_installer(app_ver)
        if setup:
            made.append(setup)
            print(f"  ✔ {'최초 설치':9} {setup.name:44} "
                  f"{setup.stat().st_size/1048576:6.0f} MB")
    print("  · 업데이트용 zip 압축 (수 분)")
    specs = [
        (f"{APP_NAME}-{app_ver}-app.zip",  outdir, {"data"},     "앱 업데이트"),
        (f"{APP_NAME}-data-{data_ver}.zip", outdir / "data", set(), "데이터 업데이트"),
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
    # UI 자산 — `ui/styles/assets.base_dir()`가 동결 시 `_MEIPASS`(=_internal)를 본다.
    #   여기 없으면 아이콘·로고·폰트가 **예외 없이 조용히** 사라진다(빈 픽스맵/시스템 폰트).
    #   과거 배포본이 실제로 그랬다 — 그래서 눈으로 볼 필요 없이 빌드가 잡도록 둔다.
    for sub in ("icons", "fonts", "logo"):
        p = outdir / "_internal" / "assets" / sub
        if not p.is_dir() or not any(p.iterdir()):
            ok = False
            print(f"  ✗ _internal/assets/{sub} 없음/빈 폴더 — UI 자산이 사라집니다")
        else:
            print(f"  ✔ _internal/assets/{sub} ({len(list(p.iterdir()))}개)")

    # 32비트 HWP 브리지 — 없으면 배포본에서 한/글 교정이 통째로 죽는다(과거 실제 사고).
    bexe = outdir / BRIDGE_DIR_NAME / BRIDGE_EXE_NAME
    if not bexe.is_file():
        ok = False
        print(f"  ✗ {BRIDGE_DIR_NAME}/{BRIDGE_EXE_NAME} 없음 — HWP 교정이 죽습니다")
    else:
        bits = _pe_bits(bexe)
        if bits != 32:
            ok = False
            print(f"  ✗ 브리지가 {bits}비트입니다 — 한/글 COM은 32비트 전용입니다")
        else:
            print(f"  ✔ {BRIDGE_DIR_NAME}/{BRIDGE_EXE_NAME} (32비트)")

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
    ap.add_argument("--no-zip", action="store_true", help="릴리스 패키징 생략(빠른 반복)")
    ap.add_argument("--skip-bridge", action="store_true",
                    help="32비트 브리지 재빌드 생략(직전 결과 재사용)")
    ap.add_argument("--no-installer", action="store_true",
                    help="설치 파일(setup.exe) 생성 생략")
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
        print("\n[1/5] 조직 키 주입 생략(--no-keys) — 사용자가 config.ini를 넣어야 합니다.")
        cleanup_org_keys()
    else:
        keys = collect_keys()
        if not keys:
            print("\n  ✗ 주입할 키가 없습니다. config.ini [API]에 키를 넣거나 --no-keys 를 쓰세요.")
            return 1
        missing = {"GEMINI", "NIKL", "ONTERM"} - set(keys)
        write_org_keys(keys)
        print(f"\n[1/5] 조직 키 주입: {', '.join(sorted(keys))} → core/_org_keys.py")
        if "GEMINI" not in keys:
            print("  ⚠ GEMINI 키가 없습니다 — 배포본에서 AI 교정이 동작하지 않습니다.")
        if missing:
            print(f"  · 미주입(해당 기능만 graceful 비활성): {', '.join(sorted(missing))}")

    # ── 2) 빌드 ──
    print("\n[2/5] PyInstaller 빌드 중… (수 분 소요)")
    try:
        rc = build(console=args.console, clean=args.clean)
    finally:
        # ⚠ 성공/실패/예외 무관하게 평문 키 모듈을 지운다.
        cleanup_org_keys()
        print("  · core/_org_keys.py 삭제 완료(레포에 평문 잔존 없음)")
    if rc != 0:
        print(f"\n  ✗ 빌드 실패 (exit {rc})")
        return rc

    # ── 3) 32비트 HWP 브리지 ──
    print("\n[3/5] 32비트 HWP 브리지 빌드")
    if not build_bridge32(skip=args.skip_bridge, clean=args.clean):
        print("\n  ✗ 브리지 없이는 배포본에서 HWP 교정이 불가능합니다 — 중단합니다.")
        return 1

    outdir = ROOT / "dist" / APP_NAME
    print("\n[4/5] 자산 배치 (EXE 옆 data/ · bridge32/)")
    stage_data(outdir)
    stage_bridge32(outdir)
    ok = verify(outdir)
    if not ok:
        print("\n  ✗ 검증 실패 — 위 항목을 해결한 뒤 재빌드하세요.")
        return 1

    # ── 5) 패키징 ──
    if args.no_zip:
        print("\n[5/5] 패키징 생략(--no-zip)")
    else:
        package(outdir, APP_VERSION, DATA_VERSION,
                installer=not args.no_installer)

    print("\n" + "=" * 58)
    print(f"  ✔ 완료 → {outdir}")
    print(f"  릴리스 태그:  앱 v{APP_VERSION}  ·  데이터 data-{DATA_VERSION}")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
