"""
datapaths.py — 데이터/캐시 경로 단일 해석기
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ **의존성 없는 최상위 모듈로 유지할 것**(version.py와 같은 규율). 사전 모듈
  (nikl_dict), 형태소 모듈(core.morph), 빌드 스크립트, 업데이터가 모두 이 파일을 본다.
  `core` 패키지를 import하면 core/__init__이 google.genai·HWP 브리지까지 끌어와
  사전 조회만 하려는 경로가 무거워진다.

배경 — **앱과 데이터를 따로 배포**한다:
    앱   패키지 ~202MB : 코드 + PySide6 + assets      (코드 수정마다, 자주)
    데이터 패키지 ~283MB : stdict.db + kiwipiepy_model  (사전 갱신 시, 드물게)
  이 분리 덕에 코드 한 줄 고치자고 283MB를 다시 내려받지 않는다. 대신 런타임이
  데이터를 **여러 후보 위치에서 찾아야** 하므로 그 규칙을 여기 한 곳에 모은다.

탐색 우선순위(먼저 찾은 것이 이김):
    1. 사용자 폴더  — 업데이터가 설치한 데이터
    2. EXE 옆 data/ — 배포 패키지에 동봉된 데이터
    3. 번들 내부    — 데이터까지 통째로 넣은 단일 빌드(개발/폴백)
  ★단, 사전(stdict.db)이 여러 곳에 있으면 **데이터 버전이 가장 높은 곳**이 이긴다
    (`data_dir`). 같으면 위 순서다.
"""

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "KS-AI Editor"        # %LOCALAPPDATA% 아래 폴더명(설정·캐시·업데이터 설치 데이터)
# ⚠ installer/KS-Proof-Reader.iss 의 [Code] UserDir 경로와 **반드시 일치**시킬 것 —
#   제거 시 사용자 데이터 폴더를 찾는 경로다. 어긋나면 잔재가 남거나 엉뚱한 폴더를 지운다.
_MARKER = "stdict.db"                # 데이터 폴더임을 식별하는 기준 파일


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """EXE가 있는 폴더(빌드본) 또는 레포 루트(개발)."""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def bundle_dir():
    """PyInstaller 번들 내부(_internal). 빌드본이 아니면 None."""
    mp = getattr(sys, "_MEIPASS", None)
    return Path(mp) if mp else None


def user_dir() -> Path:
    """쓰기 가능한 사용자 폴더.

    빌드본에서 설치 폴더(Program Files 등)와 번들 내부는 읽기 전용일 수 있으므로,
    런타임에 쓰거나 업데이터가 설치하는 것은 전부 여기로 간다.
    """
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / APP_DIR_NAME
    return Path(__file__).resolve().parent      # 개발: 레포 루트


def cache_dir() -> Path:
    """런타임 캐시(api_cache.db) 위치. 개발에선 기존대로 레포 data/."""
    d = user_dir() if is_frozen() else user_dir() / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _candidates() -> list:
    c = [user_dir() / "data", app_dir() / "data"]
    b = bundle_dir()
    if b:
        c.append(b / "data")
    return c


_ver_cache: dict = {}


def _data_version_key(d: Path) -> tuple:
    """폴더 d의 stdict.db 데이터 버전(YYYY.MM)을 비교용 튜플로. 못 읽으면 ().

    `nikl_dict.data_version()`과 같은 규칙(meta.data_version → 없으면 built_at)이다.
    ⚠ 읽기 전용(`mode=ro`)으로 열고 **곧바로 닫는다** — 연결을 붙들고 있으면 그 폴더를
      업데이터가 갈아 끼울 수 없다(아래 `apply_pending_data` 주석).
    """
    f = d / _MARKER
    try:
        st = f.stat()
    except OSError:
        return ()
    ck = (str(f), st.st_mtime_ns, st.st_size)
    if ck in _ver_cache:
        return _ver_cache[ck]
    ver = ""
    try:
        import sqlite3
        con = sqlite3.connect(f.as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
            if row and row[0]:
                ver = str(row[0])
            else:
                row = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
                if row and row[0] and len(str(row[0])) >= 7:
                    ver = str(row[0])[:7].replace("-", ".")
        finally:
            con.close()
    except Exception:
        ver = ""
    try:
        key = tuple(int(x) for x in ver.split(".")) if ver else ()
    except ValueError:
        key = ()
    _ver_cache[ck] = key
    return key


def data_dir() -> Path:
    """사전 데이터 폴더 — stdict.db가 실제로 있는 후보 중 **데이터 버전이 가장 높은** 폴더
    (같으면 후보 순서가 앞선 쪽).

    ★왜 버전을 보는가(2026-10-16 감사): 예전엔 '있는 첫 폴더'였는데, 사용자 폴더는
      업데이터가 한 번 설치하면 **영원히 남는다**. 그 뒤 사용자가 더 새 데이터가 든
      설치 파일로 앱을 다시 깔면, EXE 옆의 새 사전이 사용자 폴더의 **옛 사전에 가려졌다**
      — 설치했는데 사전이 그대로인, 아무 표시 없는 실패다.

    하나도 없으면 마지막 후보를 반환한다(호출 측이 '없음'을 자연스럽게 처리하도록).
    """
    cands = _candidates()
    found = [p for p in cands if (p / _MARKER).exists()]
    if not found:
        return cands[-1]
    best, best_v = found[0], _data_version_key(found[0])
    for p in found[1:]:
        v = _data_version_key(p)
        if v > best_v:
            best, best_v = p, v
    return best


def _ordered_candidates() -> list:
    """`data_dir()`가 고른 폴더를 맨 앞에 — 데이터 패키지의 자산(모델 등)이 사전과 같은
    패키지에서 오게 한다."""
    first = data_dir()
    return [first] + [p for p in _candidates() if p != first]


def find_data(name: str):
    """데이터 파일 하나를 후보 폴더에서 찾는다. 없으면 None."""
    for p in _ordered_candidates():
        f = p / name
        if f.exists():
            return f
    return None


def userdict_runtime_path() -> Path:
    """서버 동기화(공유 용어 뇌)가 **빌드해 쓰는** userdict.db 위치 — 빌더와 로더의 단일 출처.

    ★빌드본에서는 `cache_dir()`(%LOCALAPPDATA%\\KS-AI Editor)다. 예전엔 빌더가 자기 파일 옆
      `data/`에 썼는데, 빌드본에서 그건 번들 내부(`_internal/data`)라 ① 런타임에 번들 안에
      파일을 만들고 ② 데이터 업데이트와 무관한 곳에 쌓였다. `data/` 아래(업데이터 데이터)도
      안 된다 — 데이터 업데이트가 그 폴더를 통째로 갈아 끼운다(`apply_pending_data`).
    개발 환경에서는 기존대로 레포 `data/userdict.db`.
    """
    if is_frozen():
        return cache_dir() / "userdict.db"
    return app_dir() / "data" / "userdict.db"


_PENDING_READY = ".ks_ready"     # 검증을 마친 대기 데이터임을 뜻하는 표식(data.new 안)


def apply_pending_data() -> str:
    """업데이터가 검증해 둔 새 사전(`data.new`)을 **앱 시작 직후, 사전을 열기 전에** 제자리로.

    ★왜 시작 때 하는가(2026-10-16 감사 실측): 실행 중인 앱은 사전 DB 연결을 끝까지 열어
      둔다(`nikl_dict._get_conn`). 그래서 이미 사용자 폴더에 데이터가 있는 상태(= 두 번째
      업데이트부터)에서 폴더를 바꾸려 하면 Windows가 `WinError 5`로 거부해, **두 번째 인앱
      데이터 업데이트는 항상 실패**했고 `data.new`만 남았다. 데이터는 어차피 재시작해야
      적용되므로, 교체를 아무것도 열리지 않은 다음 시작 시점으로 옮긴다.
    ⚠ 빌드본에서만 동작한다 — 개발 환경에서 user_dir()은 레포 루트라 `data/`(우리말샘
      export 1.8GB 포함)를 건드리게 된다(`updater.install_data`의 개발 환경 거부와 같은 이유).
    반환: "" (할 일 없음) · "applied" · "failed: …"(대기본은 남겨 다음 시작에 다시 시도).
    """
    if not is_frozen():
        return ""
    root = user_dir()
    new, cur, old = root / "data.new", root / "data", root / "data.old"
    try:
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)       # 지난번에 못 지운 잔재
        if not new.is_dir():
            return ""
        if not (new / _PENDING_READY).exists():
            shutil.rmtree(new, ignore_errors=True)       # 설치 도중 끊긴 미완성본
            return ""
        if cur.exists():
            cur.rename(old)
        try:
            new.rename(cur)
        except OSError:
            if old.exists() and not cur.exists():
                old.rename(cur)                          # 되돌림 — 옛 사전으로라도 돈다
            raise
        try:
            (cur / _PENDING_READY).unlink()
        except OSError:
            pass
        shutil.rmtree(old, ignore_errors=True)
        _ver_cache.clear()
        return "applied"
    except OSError as e:
        return f"failed: {e}"


# kiwipiepy 모델 폴더가 '온전한가'를 판정할 필수 파일 목록(실측 0.23.x 기준).
#   ⚠ 이 검사는 **장식이 아니다**. 모델 경로가 잘못되거나 파일이 빠진 채로
#     `Kiwi(model_path=…)`를 호출하면 파이썬 예외가 난 뒤 **네이티브 힙 손상으로
#     프로세스가 죽는다**(실측: exit 0xC0000374, try/except로 못 막음).
#     그래서 네이티브 코드에 넘기기 **전에** 여기서 걸러야 한다.
_KIWI_REQUIRED = ("extract.mdl", "cong.mdl", "sj.morph", "default.dict", "combiningRule.txt")


def kiwi_model_version(d):
    """모델 폴더의 버전 문자열('0.23.0'). 못 읽으면 None."""
    try:
        import re
        txt = (d / "_version.py").read_text(encoding="utf-8")
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", txt)
        return m.group(1) if m else None
    except OSError:
        return None


def kiwi_model_ok(d) -> bool:
    """모델 폴더가 **쓸 수 있는지** — 파일 온전성 + 설치된 kiwipiepy와의 버전 호환성.

    ⚠ 파일만 검사하면 부족하다. 앱만 업데이트하고(새 kiwipiepy) 데이터 패키지는 옛것인
      경우, 파일은 다 있는데 **모델 포맷이 안 맞아** 네이티브에서 죽을 수 있다.
      모델은 kiwipiepy의 **마이너 버전**에 묶여 배포되므로(실측: lib 0.23.2 ↔ model 0.23.0,
      lib 0.22.2 ↔ model 0.22.1) 마이너까지 일치할 때만 쓴다. 불일치면 안 쓰는 쪽이 안전하다
      (형태소 분석만 비활성 = graceful, 크래시보다 훨씬 낫다).
    """
    try:
        if not all((d / f).is_file() and (d / f).stat().st_size > 0
                   for f in _KIWI_REQUIRED):
            return False
    except OSError:
        return False
    mv = kiwi_model_version(d)
    if mv is None:
        return True          # 버전 못 읽으면 파일 온전성만 믿는다(구 패키지 호환)
    try:
        import kiwipiepy
        lib = kiwipiepy.__version__
    except Exception:
        return True
    return mv.split(".")[:2] == lib.split(".")[:2]      # 마이너까지 일치


def kiwi_model_dir():
    """동봉된 kiwipiepy 모델 폴더. 없거나 **불완전하면** None(= pip 기본 위치 사용).

    데이터 패키지에 `data/kiwipiepy_model/`로 들어간다. 모델은 kiwipiepy **버전에 묶인**
    자산이라 사전과 함께 '드물게 바뀌는 큰 것'으로 묶어 배포한다.
    """
    for p in _ordered_candidates():
        d = p / "kiwipiepy_model"
        if d.is_dir() and kiwi_model_ok(d):
            return d
    return None


def status() -> dict:
    """진단용 — 어떤 경로가 선택됐는지 한눈에."""
    return {
        "frozen": is_frozen(),
        "app_dir": str(app_dir()),
        "user_dir": str(user_dir()),
        "cache_dir": str(cache_dir()),
        "data_dir": str(data_dir()),
        "stdict": str(find_data(_MARKER) or "없음"),
        "kiwi_model": str(kiwi_model_dir() or "(pip 기본)"),
    }


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    for k, v in status().items():
        print(f"  {k:12} {v}")
