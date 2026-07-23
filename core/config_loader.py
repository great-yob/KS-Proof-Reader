"""
core/config_loader.py — API 키 및 설정 로더
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
config.ini 읽기/쓰기, 암호화된 조직 키 복호화, 사용자 키 관리
"""

import configparser
import os
import sys
from pathlib import Path
from typing import Optional


def _get_base_dir() -> Path:
    """EXE(Nuitka/PyInstaller) 또는 스크립트 기준 경로 반환"""
    if getattr(sys, "frozen", False):
        # Nuitka standalone 또는 PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).parent.parent  # core/ 상위 = 프로젝트 루트


# ── 암호화된 조직 키 (빌드 시 내장) ──────────────────
# 배포본은 config.ini를 **동봉하지 않는다**(키 평문 노출 방지). 대신 build_dist.py가
# `core/_org_keys.py`를 생성해 번들에 넣고, 여기서 복호화해 쓴다.
#
#   개발 PC : _org_keys.py 없음 → config.ini/환경변수로 동작(기존 워크플로 그대로)
#   배포본  : _org_keys.py 있음 → 사용자 PC에 키가 없어도 3종 API 전부 동작
#
# ⚠ 완전한 보안은 아니다 — PyInstaller 아카이브에서 추출 가능하다. 사내 배포 전제이며,
#   외부 배포로 성격이 바뀌면 서버 프록시 방식으로 재설계할 것.
# ⚠ _org_keys.py는 .gitignore 대상이다. 절대 커밋하지 말 것.

def _load_org_keys() -> dict:
    """빌드 시 내장된 암호화 조직 키 복호화. 반환: {"GEMINI": "...", ...} 또는 {}."""
    try:
        from . import _org_keys as _ok       # 빌드본에만 존재
    except Exception:
        return {}
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_ok.FERNET_KEY)
        return {k: f.decrypt(v).decode("utf-8")
                for k, v in getattr(_ok, "ENCRYPTED", {}).items()}
    except Exception:
        return {}


_ORG_KEYS = _load_org_keys()


def _decrypt_org_key(name: str = "GEMINI") -> str:
    """내장 조직 키 조회(없으면 빈 문자열)."""
    return _ORG_KEYS.get(name, "")


def user_data_dir() -> Path:
    """런타임에 **쓰기**가 일어나는 파일을 둘 폴더.

    ⚠ 빌드본에서 번들 내부(`_MEIPASS` = `_internal/`)는 물론이고 설치 폴더도 쓰기 불가일
      수 있다(Program Files 등). API 캐시(api_cache.db)를 거기에 두면 저장이 조용히 실패해
      **매 실행마다 전 어휘를 재조회**하게 된다(온용어 폴백이 0.0s → 수 초로 퇴행).
      그래서 빌드본에서는 %LOCALAPPDATA%\\KS-AI Editor 를 쓴다(datapaths.APP_DIR_NAME).
      개발 환경에서는 기존대로 레포의 data/ 를 쓴다(캐시 공유·디버깅 편의).
    """
    try:
        from datapaths import cache_dir
        return cache_dir()          # 단일 출처 — datapaths가 관리
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        d = Path(base) / "KS-AI Editor"
    else:
        d = _get_base_dir() / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


class ConfigLoader:
    """API 키 및 앱 설정 관리"""

    def __init__(self):
        self._base_dir = _get_base_dir()
        self._cfg = configparser.ConfigParser()

        # 읽기는 두 곳을 **병합**한다(뒤가 우선):
        #   ① EXE 옆 config.ini      — 배포자가 넣어 주는 조직 설정/키 override(선택)
        #   ② 사용자 폴더 config.ini — 앱이 저장하는 개인 설정(테마·창 크기)
        # 쓰기는 항상 ②. 설치 폴더가 읽기 전용(Program Files 등)이어도 설정이 저장된다.
        side = self._base_dir / "config.ini"
        if getattr(sys, "frozen", False):
            self._cfg_path = user_data_dir() / "config.ini"
        else:
            self._cfg_path = side          # 개발 환경은 기존 그대로 레포 루트
        paths = [p for p in dict.fromkeys([side, self._cfg_path]) if p.exists()]
        if paths:
            self._cfg.read(paths, encoding="utf-8")

    # ── Gemini API 키 ─────────────────────────────────

    def get_gemini_key(self) -> str:
        """
        우선순위:
          1. 환경변수 GEMINI_API_KEY
          2. config.ini [API] GEMINI_API_KEY
          3. 암호화된 조직 키 (빌드 시 내장)
          4. 빈 문자열 (미설정)
        """
        # 1) 환경변수
        env_key = os.environ.get("GEMINI_API_KEY", "")
        if env_key and env_key != "YOUR_API_KEY_HERE":
            return env_key

        # 2) config.ini
        ini_key = self._cfg.get("API", "GEMINI_API_KEY", fallback="")
        if ini_key and ini_key != "YOUR_API_KEY_HERE":
            return ini_key

        # 3) 빌드 내장 조직 키
        org_key = _decrypt_org_key("GEMINI")
        if org_key:
            return org_key

        return ""

    def set_gemini_key(self, key: str):
        """사용자가 직접 입력한 키를 config.ini에 저장"""
        if not self._cfg.has_section("API"):
            self._cfg.add_section("API")
        self._cfg.set("API", "GEMINI_API_KEY", key)
        self._save()

    def has_valid_gemini_key(self) -> bool:
        key = self.get_gemini_key()
        return bool(key) and key != "YOUR_API_KEY_HERE"

    # ── NIKL 사전 API 키 (우리말샘 OpenAPI) ───────────
    #   로컬 stdict.db가 놓친 단어를 라이브 우리말샘으로 확인하는 캐싱 폴백에 사용.
    #   우선순위: 환경변수 NIKL_API_KEY → config.ini [API] NIKL_API_KEY → 빈 문자열.
    #   키가 없으면 폴백이 비활성(오프라인 동작 그대로).

    def get_nikl_key(self) -> str:
        for key in (os.environ.get("NIKL_API_KEY", ""),
                    self._cfg.get("API", "NIKL_API_KEY", fallback=""),
                    _decrypt_org_key("NIKL")):
            key = (key or "").strip()
            # 'YOUR_NIKL_KEY_HERE' 등 placeholder는 키 없음으로 취급(불필요한 실패 호출 차단).
            if key and not key.upper().startswith("YOUR"):
                return key
        return ""

    # ── 온용어 API 키 (국립국어원 전문 분야 용어지식 플랫폼) ──
    #   stdict.db(표준국어대사전+우리말샘)에 없는 **기관 전문용어**(법령·정보통신·의학·
    #   전력·국방…)를 확인해 거짓 '검수 필요'를 줄이는 2단 폴백에 사용.
    #   우선순위: 환경변수 ONTERM_API_KEY → config.ini [API] ONTERM_API_KEY → 빈 문자열.
    #   ⚠ 우리말샘 키(NIKL_API_KEY)와 **별개 키**다 — 서비스가 달라 재사용 불가(실측 확인).
    #      유효기간 2년이라 만료 시 재발급 필요. 없으면 폴백 비활성(기존 동작 그대로).

    def get_onterm_key(self) -> str:
        for key in (os.environ.get("ONTERM_API_KEY", ""),
                    self._cfg.get("API", "ONTERM_API_KEY", fallback=""),
                    _decrypt_org_key("ONTERM")):
            key = (key or "").strip()
            if key and not key.upper().startswith("YOUR"):
                return key
        return ""

    # ── Supabase (공유 용어 뇌 동기화) ────────────────
    #   url/anon_key는 배포본에 내장(공개 키 — 데이터는 RLS가 보호). 사용자별 비밀은
    #   email/password(사내 계정). 넷 중 하나라도 비면 동기화 비활성(graceful no-op).
    #   우선순위: 환경변수 → config.ini [SUPABASE] → 내장 기본값.
    _SUPABASE_URL_DEFAULT  = "https://ogcwpfkrimzdjsjledtv.supabase.co"
    _SUPABASE_ANON_DEFAULT = "sb_publishable_tMVLXbBJQc_1cyg2NUbNmA_zdOUfjAS"

    def get_supabase(self) -> dict:
        def pick(env: str, key: str, default: str = "") -> str:
            for v in (os.environ.get(env, ""),
                      self._cfg.get("SUPABASE", key, fallback=""),
                      default):
                v = (v or "").strip()
                if v and not v.upper().startswith("YOUR"):
                    return v
            return ""
        return {
            "url":      pick("SUPABASE_URL",      "URL",      self._SUPABASE_URL_DEFAULT),
            "anon_key": pick("SUPABASE_ANON_KEY", "ANON_KEY", self._SUPABASE_ANON_DEFAULT),
            "email":    pick("SUPABASE_EMAIL",    "EMAIL"),
            "password": pick("SUPABASE_PASSWORD", "PASSWORD"),
        }

    # ── 서버/앱 설정 ──────────────────────────────────

    def get_server_port(self) -> int:
        return self._cfg.getint("SERVER", "PORT", fallback=8765)

    def get_window_size(self) -> tuple:
        w = self._cfg.getint("APP", "WIDTH", fallback=1400)
        h = self._cfg.getint("APP", "HEIGHT", fallback=860)
        return w, h

    # ── 테마 (light / dark) ───────────────────────────

    def get_theme(self) -> str:
        val = self._cfg.get("APP", "THEME", fallback="light").strip().lower()
        return "dark" if val == "dark" else "light"

    def set_theme(self, mode: str):
        if not self._cfg.has_section("APP"):
            self._cfg.add_section("APP")
        self._cfg.set("APP", "THEME", "dark" if str(mode).lower() == "dark" else "light")
        self._save()

    # ── 내부 ──────────────────────────────────────────

    def _save(self):
        """설정 저장 — 실패해도 **절대 예외를 올리지 않는다**.

        ⚠ 호출부가 테마 토글 같은 UI 이벤트라, 읽기 전용 폴더에서 예외가 나면 앱이
          죽는다. 저장 실패는 '설정이 유지되지 않는' 정도의 열화로 끝내야 한다.
        """
        try:
            self._cfg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cfg_path, "w", encoding="utf-8") as f:
                self._cfg.write(f)
        except OSError:
            pass
