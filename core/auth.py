"""
core/auth.py — Supabase Auth 세션(사내 계정 로그인) (선택적 로그인)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works(사내 근태앱)와 **동일한 Supabase Auth**(email/password)로 로그인한다. 같은
프로젝트·같은 직원 계정이므로 로그인하면 그 신원으로 공유 용어 뇌(동기화·큐레이터)가
활성된다. **교정 기능은 로그인과 무관하게 항상 동작**한다(로그인은 공유 뇌 전용·선택).

ks-works 규칙 미러(src/contexts/AuthContext.tsx):
  · 입력에 '@'가 없으면 사번/프리픽스로 보고 '@kyungsungmedia.com'을 붙인다.
  · supabase.auth.signInWithPassword 와 동일한 password grant.
  · 로그인 후 employees.terminated_at(퇴사자 차단)·role(admin/employee) 확인.

보안:
  · access_token은 메모리에만. **refresh_token + 프로필**만 디스크에 저장하되 Windows
    **DPAPI**(현재 사용자·이 PC 한정 복호화)로 암호화한다. DPAPI 불가(비Windows) 시
    **저장하지 않음**(세션은 실행 중에만 유지) — 평문 토큰을 디스크에 남기지 않는다.
  · access_token 만료 시 refresh_token 그랜트로 자동 갱신(로테이션 반영).
  · 평문 비밀번호는 저장하지 않는다(config [SUPABASE] EMAIL/PASSWORD는 *선택적 헤드리스
    폴백*일 뿐 — UI 로그인 권장).

규율: GUI-agnostic(PySide6 import 금지). graceful — 네트워크/키 부재 시 None/False, 예외 무전파.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_TIMEOUT = 12
_DEFAULT_DOMAIN = "kyungsungmedia.com"

_lock = threading.Lock()
_state = {
    "loaded": False,          # 디스크 복원 시도 여부
    "access_token": None,
    "exp": 0.0,
    "refresh_token": None,
    "user": None,             # {uid, employee_id, name, email, role}
}


# ══════════════════════════════════════════════════════
# ▌설정 / 저장 경로
# ══════════════════════════════════════════════════════

def _cfg() -> dict:
    try:
        from core.config_loader import ConfigLoader
        return ConfigLoader().get_supabase()
    except Exception:
        return {}


def _email_domain() -> str:
    try:
        from core.config_loader import ConfigLoader
        import os
        cl = ConfigLoader()
        v = (os.environ.get("SUPABASE_EMAIL_DOMAIN", "")
             or cl._cfg.get("SUPABASE", "EMAIL_DOMAIN", fallback="")).strip()
        return v or _DEFAULT_DOMAIN
    except Exception:
        return _DEFAULT_DOMAIN


def _session_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data" / ".ks_session"


def normalize_email(email_or_prefix: str) -> str:
    """ks-works와 동일 — '@' 없으면 사번/프리픽스로 보고 사내 도메인을 붙인다."""
    t = (email_or_prefix or "").strip()
    if not t:
        return ""
    return t if "@" in t else f"{t}@{_email_domain()}"


# ══════════════════════════════════════════════════════
# ▌DPAPI 보안 저장 (Windows 현재 사용자 한정)
# ══════════════════════════════════════════════════════

def _dpapi(data: bytes, protect: bool) -> Optional[bytes]:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    try:
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out
    except Exception:
        return None


def _persist():
    """refresh_token + user를 DPAPI로 암호화 저장. DPAPI 불가 시 저장 생략(평문 금지)."""
    rt, user = _state.get("refresh_token"), _state.get("user")
    path = _session_path()
    if not rt or not user:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return
    raw = json.dumps({"refresh_token": rt, "user": user}).encode("utf-8")
    enc = _dpapi(raw, protect=True)
    if enc is None:
        return   # 비Windows/실패 → 디스크에 남기지 않음(보안)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(enc)
    except OSError:
        pass


def _load_persisted():
    if _state["loaded"]:
        return
    _state["loaded"] = True
    path = _session_path()
    if not path.exists():
        return
    try:
        dec = _dpapi(path.read_bytes(), protect=False)
        if not dec:
            return
        data = json.loads(dec.decode("utf-8"))
        _state["refresh_token"] = data.get("refresh_token")
        _state["user"] = data.get("user")
    except Exception:
        pass


# ══════════════════════════════════════════════════════
# ▌HTTP
# ══════════════════════════════════════════════════════

def _http(method: str, url: str, headers: dict, body=None) -> tuple:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {}
        return e.code, detail
    except Exception as e:
        return 0, {"_error": str(e)}


def _fetch_profile(c: dict, access_token: str, uid: str) -> Optional[dict]:
    status, js = _http(
        "GET",
        f"{c['url']}/rest/v1/employees"
        f"?select=id,name,role,terminated_at&auth_user_id=eq.{uid}",
        {"apikey": c["anon_key"], "Authorization": f"Bearer {access_token}",
         "Accept": "application/json"})
    if status == 200 and isinstance(js, list) and js:
        return js[0]
    return None


def _apply_token_response(c: dict, js: dict) -> Optional[dict]:
    """token 그랜트 응답 → 세션 반영 + 프로필 확인. 성공 시 user dict, 실패 시 None."""
    at = js.get("access_token")
    rt = js.get("refresh_token")
    if not at:
        return None
    uid = (js.get("user") or {}).get("id")
    if not uid:
        return None
    prof = _fetch_profile(c, at, uid)
    if prof is None:
        return None
    if prof.get("terminated_at"):       # 퇴사자 차단(ks-works와 동일)
        return None
    user = {
        "uid": uid,
        "employee_id": prof.get("id"),
        "name": prof.get("name"),
        "email": (js.get("user") or {}).get("email"),
        "role": prof.get("role") or "employee",
    }
    _state["access_token"] = at
    _state["exp"] = time.time() + int(js.get("expires_in", 3600))
    _state["refresh_token"] = rt
    _state["user"] = user
    return user


# ══════════════════════════════════════════════════════
# ▌공개 API
# ══════════════════════════════════════════════════════

def login(email_or_prefix: str, password: str) -> dict:
    """사내 계정 로그인. 반환 {ok, user?|error}. 성공 시 세션 저장(DPAPI)."""
    c = _cfg()
    if not c.get("url") or not c.get("anon_key"):
        return {"ok": False, "error": "서버 설정이 없습니다(관리자 문의)."}
    email = normalize_email(email_or_prefix)
    if not email or not password:
        return {"ok": False, "error": "이메일(사번)과 비밀번호를 입력하세요."}
    status, js = _http(
        "POST", f"{c['url']}/auth/v1/token?grant_type=password",
        {"apikey": c["anon_key"], "Content-Type": "application/json"},
        {"email": email, "password": password})
    if status != 200 or not isinstance(js, dict) or not js.get("access_token"):
        msg = (js or {}).get("error_description") or (js or {}).get("msg") \
            or "이메일 또는 비밀번호를 확인하세요."
        return {"ok": False, "error": msg}
    with _lock:
        user = _apply_token_response(c, js)
        if user is None:
            _clear()
            return {"ok": False, "error": "계정 정보를 확인할 수 없습니다(퇴사 처리 또는 미등록)."}
        _state["loaded"] = True
        _persist()
    return {"ok": True, "user": user}


def restore() -> Optional[dict]:
    """저장된 refresh_token으로 세션 복원(앱 시작 시 백그라운드). 성공 시 user, 실패 시 None.

    refresh가 실패(만료·취소)하면 세션을 비운다. 프로필을 다시 받아 role/퇴사 변경을 반영.
    """
    c = _cfg()
    with _lock:
        _load_persisted()
        rt = _state.get("refresh_token")
    if not rt or not c.get("url"):
        return current_user()    # 폴백: config 헤드리스는 access_token()에서 처리
    status, js = _http(
        "POST", f"{c['url']}/auth/v1/token?grant_type=refresh_token",
        {"apikey": c["anon_key"], "Content-Type": "application/json"},
        {"refresh_token": rt})
    with _lock:
        if status == 200 and isinstance(js, dict) and js.get("access_token"):
            user = _apply_token_response(c, js)
            if user is not None:
                _persist()
                return user
        # 복원 실패 → 세션 폐기
        _clear()
        return None


def access_token() -> Optional[str]:
    """유효한 access_token 반환(필요 시 refresh). 세션 없으면 config 헤드리스 폴백. 없으면 None."""
    c = _cfg()
    if not c.get("url") or not c.get("anon_key"):
        return None
    with _lock:
        _load_persisted()
        now = time.time()
        if _state["access_token"] and _state["exp"] - 60 > now:
            return _state["access_token"]
        rt = _state.get("refresh_token")
    # 1) refresh_token 그랜트
    if rt:
        status, js = _http(
            "POST", f"{c['url']}/auth/v1/token?grant_type=refresh_token",
            {"apikey": c["anon_key"], "Content-Type": "application/json"},
            {"refresh_token": rt})
        with _lock:
            if status == 200 and isinstance(js, dict) and js.get("access_token"):
                if _apply_token_response(c, js) is not None:
                    _persist()
                    return _state["access_token"]
            _clear()
    # 2) config 헤드리스 폴백(이메일/비번이 설정돼 있으면)
    if c.get("email") and c.get("password"):
        res = login(c["email"], c["password"])
        if res.get("ok"):
            return _state["access_token"]
    return None


def current_user() -> Optional[dict]:
    """현재 로그인 사용자 {uid,employee_id,name,email,role} 또는 None(네트워크 미발생)."""
    with _lock:
        _load_persisted()
        return dict(_state["user"]) if _state.get("user") else None


def is_logged_in() -> bool:
    return current_user() is not None


def is_curator() -> bool:
    """UI 노출용 — 세션 role이 admin인가(실제 권한은 서버 RLS가 강제)."""
    u = current_user()
    return bool(u) and u.get("role") == "admin"


def logout():
    with _lock:
        _clear()


def _clear():
    _state["access_token"] = None
    _state["exp"] = 0.0
    _state["refresh_token"] = None
    _state["user"] = None
    try:
        _session_path().unlink(missing_ok=True)
    except Exception:
        pass


def status() -> dict:
    u = current_user()
    return {"logged_in": u is not None,
            "user": u, "session_file": str(_session_path()),
            "dpapi": sys.platform == "win32"}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("auth 상태:", status())
