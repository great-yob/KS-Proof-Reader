"""
core/updater.py — GitHub Releases 자동 업데이트 (앱 · 데이터 2채널)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계: docs 없음 — 이 헤더가 설계도. 형제 프로젝트 KS-Works-Utility는 Electron이라
electron-updater를 쓰지만, 이 앱은 PySide6라 같은 일을 파이썬으로 한다.

**2채널인 이유** — 코드와 데이터의 변경 빈도가 10배 이상 다르다:
    앱   채널: 태그 `v1.0.0`        · zip 93MB  · 코드 수정마다(잦음)
    데이터 채널: 태그 `data-2026.07` · zip 131MB · 사전 갱신 시(반기~월간)
  한 덩어리로 묶으면 코드 한 줄 고칠 때마다 223MB를 재배포하게 된다.

**설치 위치**(datapaths 규칙과 일치):
    앱   → EXE 폴더를 통째로 교체해야 하므로 **헬퍼 배치 파일**이 앱 종료 후 수행.
    데이터 → `user_dir()/data` 에 풀어 넣기만 하면 된다. datapaths.data_dir()의
             탐색 1순위라 다음 실행부터 자동으로 새 데이터가 쓰인다. **앱 재시작만 필요.**

  ⚠ 데이터 채널이 앱 채널보다 훨씬 안전하다(실행 중인 파일을 안 건드림).
    그래서 데이터는 앱 안에서 바로 적용하고, 앱 교체만 헬퍼로 미룬다.

규율:
  · GUI-agnostic — PySide6 import 금지. 진행률은 콜백으로 넘긴다(UI가 감싼다).
  · graceful — 네트워크 없음/저장소 없음/권한 없음 전부 조용히 실패(앱 동작 불변).
  · **자동 설치는 하지 않는다** — 확인은 자동, 적용은 사용자가 누를 때만.
    출판 교정 도중 앱이 멋대로 재시작되면 작업이 날아간다.
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

_API = "https://api.github.com/repos/{owner}/{repo}/releases"
_TIMEOUT = 10.0
_UA = {"User-Agent": "KS-Proof-Reader-Updater"}

APP_CHANNEL = "app"
DATA_CHANNEL = "data"


# ══════════════════════════════════════════════════════
# ▌버전 비교
# ══════════════════════════════════════════════════════

def _parse_app(v: str) -> tuple:
    """'1.2.3' → (1,2,3). 비교 불가 조각은 0으로."""
    out = []
    for part in str(v or "").strip().lstrip("v").split(".")[:3]:
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _parse_data(v: str) -> tuple:
    """'2026.07' → (2026,7)."""
    out = []
    for part in str(v or "").strip().replace("data-", "").split(".")[:2]:
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 2:
        out.append(0)
    return tuple(out)


def is_newer(channel: str, remote: str, local: str) -> bool:
    f = _parse_data if channel == DATA_CHANNEL else _parse_app
    return f(remote) > f(local)


# ══════════════════════════════════════════════════════
# ▌릴리스 조회
# ══════════════════════════════════════════════════════

def _get_json(url: str):
    try:
        req = urllib.request.Request(url, headers=_UA)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _current(channel: str) -> str:
    if channel == DATA_CHANNEL:
        try:
            from nikl_dict import data_version
            v = data_version()
            if v:
                return v
        except Exception:
            pass
        try:
            from version import DATA_VERSION
            return DATA_VERSION
        except Exception:
            return "0000.00"
    try:
        from version import APP_VERSION
        return APP_VERSION
    except Exception:
        return "0.0.0"


def check(channel: str) -> Optional[dict]:
    """해당 채널에 더 새로운 릴리스가 있으면 정보를, 없으면 None.

    반환: {"version", "tag", "url"(zip 자산), "size", "notes", "current"}
    """
    try:
        from version import GITHUB_OWNER, GITHUB_REPO
    except Exception:
        return None
    data = _get_json(_API.format(owner=GITHUB_OWNER, repo=GITHUB_REPO) + "?per_page=30")
    if not isinstance(data, list):
        return None       # 저장소 없음/비공개/네트워크 오류 → 조용히 비활성

    prefix = "data-" if channel == DATA_CHANNEL else "v"
    cur = _current(channel)
    best = None
    for rel in data:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name", ""))
        if not tag.startswith(prefix):
            continue
        # 'v1.0.0' vs 'data-2026.07' — 접두사가 겹치지 않게 data를 먼저 걸러낸다.
        if channel == APP_CHANNEL and tag.startswith("data-"):
            continue
        ver = tag[len(prefix):]
        if not is_newer(channel, ver, cur):
            continue
        asset = _pick_asset(rel, channel)
        if not asset:
            continue
        cand = {"version": ver, "tag": tag, "url": asset["browser_download_url"],
                "size": asset.get("size", 0), "notes": rel.get("body", "") or "",
                "current": cur}
        if best is None or is_newer(channel, ver, best["version"]):
            best = cand
    return best


def _pick_asset(rel: dict, channel: str):
    """채널에 맞는 zip 자산 선택. 앱은 '-app.zip'(없으면 '-full.zip')."""
    assets = [a for a in (rel.get("assets") or [])
              if str(a.get("name", "")).lower().endswith(".zip")]
    if not assets:
        return None
    if channel == DATA_CHANNEL:
        for a in assets:
            if "data-" in a["name"]:
                return a
        return None
    for key in ("-app.zip", "-full.zip"):
        for a in assets:
            if a["name"].lower().endswith(key):
                return a
    return assets[0]


# ══════════════════════════════════════════════════════
# ▌다운로드
# ══════════════════════════════════════════════════════

def download(info: dict, progress: Optional[Callable[[int, int], None]] = None,
             stop_event=None) -> Optional[Path]:
    """zip을 임시 폴더로 내려받는다. 취소/실패 시 None(부분 파일은 지운다)."""
    try:
        req = urllib.request.Request(info["url"], headers=_UA)
        ctx = ssl.create_default_context()
        tmp = Path(tempfile.mkdtemp(prefix="ksproof-upd-")) / "package.zip"
        with urllib.request.urlopen(req, timeout=_TIMEOUT * 3, context=ctx) as r:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise InterruptedError("취소됨")
                    chunk = r.read(1 << 20)      # 1MB
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        # 온전한 zip인지 확인 — 끊긴 다운로드를 설치하면 앱이 망가진다.
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None:
                raise zipfile.BadZipFile("손상된 zip")
        return tmp
    except Exception:
        try:
            shutil.rmtree(tmp.parent, ignore_errors=True)   # noqa: F821
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════
# ▌설치
# ══════════════════════════════════════════════════════

def install_data(zip_path: Path, logger: Optional[Callable[[str], None]] = None,
                 dest: Optional[Path] = None) -> bool:
    """데이터 패키지를 user_dir()/data 에 설치한다. 앱 재시작만 필요(교체 아님).

    임시 폴더에 먼저 풀고 검증한 뒤 교체한다 — 도중에 실패해도 기존 데이터가 남는다.

    ⚠ 개발 환경에서는 `dest`를 명시하지 않으면 **거부**한다. 개발 PC에서 user_dir()은
      레포 루트라, 그냥 두면 레포의 data/(우리말샘 export 1.8GB·캐시·백업 포함)를
      배포용 data로 통째로 덮어쓴다. dest는 테스트용 탈출구다.
    """
    log = logger or (lambda *_: None)
    try:
        from datapaths import user_dir, is_frozen
        if dest is None:
            if not is_frozen():
                log("  ✗ 개발 환경에서는 데이터 자동 설치를 수행하지 않습니다"
                    "(레포 data/ 손상 방지). 테스트하려면 dest를 지정하세요.")
                return False
            dest = user_dir() / "data"
        dest = Path(dest)
        staging = dest.parent / "data.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)
        # 최소 무결성 — stdict.db가 있고 열리는가.
        db = staging / "stdict.db"
        if not db.exists():
            log("  ✗ 데이터 패키지에 stdict.db가 없습니다 — 설치 취소")
            shutil.rmtree(staging, ignore_errors=True)
            return False
        import sqlite3
        c = sqlite3.connect(str(db))
        n = c.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        if n < 100_000 or "norm_map" not in tabs:
            log(f"  ✗ 데이터 무결성 실패(단어 {n:,} · 테이블 {sorted(tabs)}) — 설치 취소")
            shutil.rmtree(staging, ignore_errors=True)
            return False
        old = dest.parent / "data.old"
        shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            dest.rename(old)
        staging.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
        log(f"  ✔ 데이터 설치 완료({n:,} 단어) — 앱을 다시 시작하면 적용됩니다.")
        return True
    except Exception as e:
        log(f"  ✗ 데이터 설치 실패: {e}")
        return False


_HELPER = r"""@echo off
chcp 65001 >nul
rem KS-Proof Reader 앱 업데이트 헬퍼 — 앱 종료를 기다렸다가 폴더를 교체하고 재실행한다.
rem (실행 중인 EXE는 자기 자신을 덮어쓸 수 없어 외부 프로세스가 필요하다)
echo 업데이트를 적용하는 중입니다. 창을 닫지 마세요...
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
robocopy "{src}" "{dst}" /E /MOVE /NFL /NDL /NJH /NJS /NC /NS >nul
start "" "{exe}"
rmdir /S /Q "{tmp}" 2>nul
"""


def install_app(zip_path: Path, logger: Optional[Callable[[str], None]] = None) -> bool:
    """앱 패키지를 설치한다 — 압축을 풀고 **헬퍼가 앱 종료 후** 폴더를 교체·재실행.

    호출 측은 True를 받으면 곧바로 앱을 종료해야 한다(헬퍼가 기다리고 있다).
    ⚠ 데이터 폴더는 건드리지 않는다(app.zip에 data/가 없고, /MOVE는 원본에 있는 것만 옮긴다).
    """
    log = logger or (lambda *_: None)
    try:
        from datapaths import app_dir, is_frozen
        if not is_frozen():
            log("  ✗ 개발 환경에서는 앱 자동 업데이트를 수행하지 않습니다.")
            return False
        dst = app_dir()
        staging = Path(tempfile.mkdtemp(prefix="ksproof-app-"))
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)
        exe = dst / f"{Path(sys.executable).name}"
        if not (staging / exe.name).exists():
            log("  ✗ 앱 패키지에 실행 파일이 없습니다 — 설치 취소")
            shutil.rmtree(staging, ignore_errors=True)
            return False
        bat = staging.parent / "ks_update.bat"
        bat.write_text(_HELPER.format(pid=os.getpid(), src=str(staging), dst=str(dst),
                                      exe=str(exe), tmp=str(staging.parent)),
                       encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        log("  ✔ 업데이트 준비 완료 — 앱을 종료하면 자동으로 교체 후 재시작됩니다.")
        return True
    except Exception as e:
        log(f"  ✗ 앱 업데이트 실패: {e}")
        return False


def check_all() -> dict:
    """두 채널을 한 번에 확인 — {"app": info|None, "data": info|None}."""
    return {APP_CHANNEL: check(APP_CHANNEL), DATA_CHANNEL: check(DATA_CHANNEL)}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("현재 버전:")
    print(f"   앱     {_current(APP_CHANNEL)}")
    print(f"   데이터  {_current(DATA_CHANNEL)}")
    print("\n릴리스 확인:")
    for ch, info in check_all().items():
        print(f"   {ch:5} → {info if info else '최신(또는 저장소/네트워크 없음)'}")
