"""
core/auth.py — Supabase Auth 세션(사내 계정 로그인) **= 앱 실행 게이트**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works(사내 근태앱)와 **동일한 Supabase Auth**(email/password)로 로그인한다. 같은
프로젝트·같은 직원 계정이므로 로그인하면 그 신원으로 공유 용어 뇌(동기화·큐레이터)가
활성된다.

⚠ **로그인은 이제 선택이 아니라 게이트다(2026-08-05).** 저장소·릴리스가 퍼블릭이라
누구나 앱을 받을 수 있는데 Gemini/사전 API는 무료 한도(RPD)를 공유하므로, 사내 계정
확인 전에는 앱 자체를 열지 않는다(ui/widgets/login_dialog.require_login → main.py).
게이트가 막는 건 '앱을 받아서 그냥 쓰는' 비사용자다 — 내장 키 자체는 릴리스에서
추출 가능하므로(빌드 문서 참조) 키 유출까지 막으려면 서버 프록시가 필요하다.

ks-works 규칙 미러(src/contexts/AuthContext.tsx):
  · 입력에 '@'가 없으면 사번/프리픽스로 보고 '@kyungsungmedia.com'을 붙인다.
  · supabase.auth.signInWithPassword 와 동일한 password grant.
  · 로그인 후 employees.terminated_at(퇴사자 차단)·role(admin/employee) 확인.

**오프라인 유예(GRACE_DAYS=7)** — 게이트가 회사 전체를 잠그는 단일 장애점이 되지
않도록, 서버 검증 *실패의 종류*를 구분한다:
  · 서버가 명시적으로 거부(400/401/403 = 토큰 취소·비번 변경, 또는 퇴사·미등록)
    → 세션 폐기, 재로그인 강제.
  · 네트워크/서버 오류(연결 불가·5xx·타임아웃) → 마지막 **성공 검증 시각**이 7일
    이내면 저장된 신원으로 통과(offline 모드). 넘으면 차단.
  offline 모드에는 유효한 access_token이 없으므로 동기화/큐레이션은 자동 no-op이고,
  교정 파이프라인은 로컬 사전만 쓰므로 그대로 동작한다.

보안:
  · access_token은 메모리에만. **refresh_token + 프로필 + 검증시각**만 디스크에
    저장하되 Windows **DPAPI**(현재 사용자·이 PC 한정 복호화)로 암호화한다. DPAPI
    불가(비Windows) 시 **저장하지 않음**(세션은 실행 중에만 유지) — 평문 토큰을
    디스크에 남기지 않는다(그 경우 매 실행 로그인).
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

# 오프라인 유예 — 서버에 **닿지 못했을 때**만 적용되는 마지막 성공 검증 이후 허용 기간.
#   서버가 명시적으로 거부하면(400/401/403·퇴사·미등록) 유예와 무관하게 즉시 차단된다.
GRACE_DAYS = 7
_GRACE_SECONDS = GRACE_DAYS * 24 * 3600

_lock = threading.Lock()
_state = {
    "loaded": False,          # 디스크 복원 시도 여부
    "access_token": None,
    "exp": 0.0,
    "refresh_token": None,
    "user": None,             # {uid, employee_id, name, email, role}
    "verified_at": 0.0,       # 마지막으로 서버가 신원을 확인해 준 시각(epoch) — 유예 기준
    "offline": False,         # 이번 세션이 오프라인 유예로 통과했는가
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
    """refresh_token + user + 검증시각을 DPAPI로 암호화 저장. DPAPI 불가 시 저장 생략(평문 금지)."""
    rt, user = _state.get("refresh_token"), _state.get("user")
    path = _session_path()
    if not rt or not user:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return
    raw = json.dumps({"refresh_token": rt, "user": user,
                      "verified_at": _state.get("verified_at") or 0.0}).encode("utf-8")
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
        try:
            _state["verified_at"] = float(data.get("verified_at") or 0.0)
        except (TypeError, ValueError):
            _state["verified_at"] = 0.0
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


def _fetch_profile(c: dict, access_token: str, uid: str) -> tuple:
    """(status, profile|None) 반환. status!=200 은 **네트워크/서버 실패**이지 '미등록'이
    아니다 — 둘을 뭉뚱그리면 회선 장애가 퇴사 처리와 같은 취급을 받아 세션이 날아간다."""
    status, js = _http(
        "GET",
        f"{c['url']}/rest/v1/employees"
        f"?select=id,name,role,terminated_at&auth_user_id=eq.{uid}",
        {"apikey": c["anon_key"], "Authorization": f"Bearer {access_token}",
         "Accept": "application/json"})
    if status == 200 and isinstance(js, list):
        return 200, (js[0] if js else None)
    return status, None


def _apply_token_response(c: dict, js: dict) -> tuple:
    """token 그랜트 응답 → 세션 반영 + 프로필 확인.

    반환 (outcome, user|None):
      · "ok"       — 검증 완료. verified_at 갱신(오프라인 유예의 기준점).
      · "rejected" — 서버가 신원을 거부(미등록·퇴사·응답 형식 이상). 호출측이 세션 폐기.
      · "net"      — 토큰은 받았으나 프로필 조회가 네트워크로 실패. **세션 유지**.
    ⚠ 프로필 조회보다 **토큰 반영을 먼저** 한다 — 회전된 refresh_token을 잃으면 구 토큰이
      서버에서 곧 무효화돼 다음 실행에 로그인이 강제된다(유예의 의미가 사라진다).
    """
    at = js.get("access_token")
    rt = js.get("refresh_token")
    uid = (js.get("user") or {}).get("id")
    if not at or not uid:
        return "rejected", None
    _state["access_token"] = at
    _state["exp"] = time.time() + int(js.get("expires_in", 3600))
    if rt:
        _state["refresh_token"] = rt
    status, prof = _fetch_profile(c, at, uid)
    if status != 200:
        return "net", None
    if prof is None:                    # 200인데 행이 없음 = employees 미등록
        return "rejected", None
    if prof.get("terminated_at"):       # 퇴사자 차단(ks-works와 동일)
        return "rejected", None
    user = {
        "uid": uid,
        "employee_id": prof.get("id"),
        "name": prof.get("name"),
        "email": (js.get("user") or {}).get("email"),
        "role": prof.get("role") or "employee",
    }
    _state["user"] = user
    _state["verified_at"] = time.time()
    _state["offline"] = False
    return "ok", user


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
    if status == 0:
        return {"ok": False, "error": "서버에 연결할 수 없습니다(네트워크 확인)."}
    if status != 200 or not isinstance(js, dict) or not js.get("access_token"):
        msg = (js or {}).get("error_description") or (js or {}).get("msg") \
            or "이메일 또는 비밀번호를 확인하세요."
        return {"ok": False, "error": msg}
    with _lock:
        outcome, user = _apply_token_response(c, js)
        if outcome == "net":
            _clear()
            return {"ok": False,
                    "error": "계정 정보를 확인하지 못했습니다(네트워크 확인 후 다시 시도)."}
        if outcome != "ok" or user is None:
            _clear()
            return {"ok": False, "error": "계정 정보를 확인할 수 없습니다(퇴사 처리 또는 미등록)."}
        _state["loaded"] = True
        _persist()
    return {"ok": True, "user": user}


def restore_session() -> dict:
    """저장된 세션을 서버 검증해 복원한다 — **게이트가 쓰는 상세 결과**.

    반환 {ok, user?, mode?, reason?, grace_days_left?}
      · ok=True  mode="verified" — 서버가 지금 신원을 확인해 줌.
      · ok=True  mode="offline"  — 서버에 못 닿았지만 마지막 검증이 GRACE_DAYS 이내.
      · ok=False reason="no_session" | "no_config" | "rejected" | "offline_expired".
    세션 폐기는 **rejected에서만** 일어난다(회선 장애로 로그인이 풀리지 않게).
    """
    c = _cfg()
    with _lock:
        _load_persisted()
        rt = _state.get("refresh_token")
        saved_user = _state.get("user")
        verified_at = float(_state.get("verified_at") or 0.0)
    if not rt or not saved_user:
        return {"ok": False, "reason": "no_session"}
    if not c.get("url") or not c.get("anon_key"):
        return {"ok": False, "reason": "no_config"}
    status, js = _http(
        "POST", f"{c['url']}/auth/v1/token?grant_type=refresh_token",
        {"apikey": c["anon_key"], "Content-Type": "application/json"},
        {"refresh_token": rt})
    with _lock:
        if status == 200 and isinstance(js, dict) and js.get("access_token"):
            outcome, user = _apply_token_response(c, js)
            if outcome == "ok" and user is not None:
                _persist()
                return {"ok": True, "user": user, "mode": "verified"}
            if outcome == "rejected":
                _clear()
                return {"ok": False, "reason": "rejected"}
            _persist()          # "net" — 회전된 refresh_token만 보존하고 유예 판정으로
        elif status in (400, 401, 403):
            # 서버가 명시적으로 거부 — 토큰 취소·비밀번호 변경·세션 만료. 유예 없음.
            _clear()
            return {"ok": False, "reason": "rejected"}
        # 여기 도달 = 네트워크/서버 오류(0·5xx·429) → 오프라인 유예 판정
        left = _GRACE_SECONDS - (time.time() - verified_at)
        if verified_at > 0 and left > 0:
            _state["offline"] = True
            return {"ok": True, "user": dict(saved_user), "mode": "offline",
                    "grace_days_left": max(1, int(left // 86400) + 1)}
        return {"ok": False, "reason": "offline_expired"}


def restore() -> Optional[dict]:
    """저장된 세션 복원 — 성공 시 user, 실패 시 None(restore_session의 얇은 래퍼)."""
    res = restore_session()
    return res.get("user") if res.get("ok") else None


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
                outcome, _u = _apply_token_response(c, js)
                if outcome == "ok":
                    _persist()
                    return _state["access_token"]
                if outcome == "net":
                    _persist()      # 회전된 토큰 보존 — 세션은 유지(오프라인 유예)
                    return None
                _clear()            # rejected
            elif status in (400, 401, 403):
                _clear()            # 서버가 명시적으로 거부 → 재로그인 필요
            else:
                return None         # 네트워크/서버 오류 — 세션 유지, 이번만 실패
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


def is_offline_session() -> bool:
    """이번 세션이 오프라인 유예로 통과했는가(동기화·큐레이션 불가 상태)."""
    with _lock:
        return bool(_state.get("offline")) and _state.get("user") is not None


def grace_days_left() -> int:
    """오프라인 유예 잔여 일수(올림). 저장된 검증 이력이 없으면 0."""
    with _lock:
        _load_persisted()
        verified_at = float(_state.get("verified_at") or 0.0)
    if verified_at <= 0:
        return 0
    left = _GRACE_SECONDS - (time.time() - verified_at)
    return max(0, int(left // 86400) + 1) if left > 0 else 0


def logout():
    with _lock:
        _clear()


def _clear():
    _state["access_token"] = None
    _state["exp"] = 0.0
    _state["refresh_token"] = None
    _state["user"] = None
    _state["verified_at"] = 0.0
    _state["offline"] = False
    try:
        _session_path().unlink(missing_ok=True)
    except Exception:
        pass


def status() -> dict:
    u = current_user()
    return {"logged_in": u is not None,
            "user": u, "session_file": str(_session_path()),
            "offline": is_offline_session(),
            "grace_days_left": grace_days_left(),
            "dpapi": sys.platform == "win32"}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("auth 상태:", status())
