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
    1. 사용자 폴더  — 업데이터가 설치한 **최신** 데이터
    2. EXE 옆 data/ — 배포 패키지에 동봉된 데이터
    3. 번들 내부    — 데이터까지 통째로 넣은 단일 빌드(개발/폴백)
"""

import os
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


def data_dir() -> Path:
    """사전 데이터 폴더 — 후보 중 stdict.db가 **실제로 있는** 첫 폴더.

    하나도 없으면 마지막 후보를 반환한다(호출 측이 '없음'을 자연스럽게 처리하도록).
    """
    cands = _candidates()
    for p in cands:
        if (p / _MARKER).exists():
            return p
    return cands[-1]


def find_data(name: str):
    """데이터 파일 하나를 후보 폴더에서 찾는다. 없으면 None."""
    for p in _candidates():
        f = p / name
        if f.exists():
            return f
    return None


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
    for p in _candidates():
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
