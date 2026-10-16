"""
core/member_admin.py — 앱 전용 회원 관리(관리자) — Edge Function `ksai-admin-members` 클라이언트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works 직원으로 등록하지 않고 이 앱만 쓰는 사용자를 만든다(2026-10-16 분리 —
core/auth.py 머리 설명 참조). 계정 생성·초기화에는 서비스 키가 필요하므로 전부
서버 함수가 하고, 이 모듈은 사용자 JWT로 그 함수를 부를 뿐이다.

  · 다루는 대상은 **ksai_members뿐**이다. ks-works 직원 계정은 생성·초기화·중지
    어느 것도 하지 않는다(서버가 404로 거부).
  · 새 계정의 초기 비밀번호는 서버 상수(ks2026)이며, 첫 로그인 때 변경이 강제된다.
  · ks-works **퇴사자**는 같은 로그인 ID로 앱 회원 전환이 가능하다(create_member 참조).
  · 권한 판정은 서버가 한다(userdict_is_curator) — 여기서의 관리자 여부는 UI 노출용.

규율: GUI-agnostic(PySide6 import 금지). 예외 무전파 — 모든 함수가 {ok, error?, …}를 돌려준다.
"""

from core import auth

_FN = "ksai-admin-members"

# 초기 비밀번호 — **표시용**. 실제 값은 서버 Edge Function 상수가 정하고(ksai-admin-members·
#   ksai-change-password), 생성·초기화 응답의 initial_password가 그 값을 돌려준다.
#   서버 값을 바꾸면 여기도 맞출 것.
INITIAL_PASSWORD = "ks2026"


def _call(action: str, **payload) -> dict:
    try:
        status, js = auth.call_function(_FN, {"action": action, **payload})
    except Exception as e:                       # 방어 — 호출측 UI를 죽이지 않는다
        return {"ok": False, "error": str(e)}
    if status == 200 and isinstance(js, dict) and js.get("ok"):
        return js
    out = {"ok": False, "error": auth._error_text(status, js, "요청을 처리하지 못했습니다.")}
    if isinstance(js, dict) and js.get("code"):
        out["code"] = js["code"]          # 예: former_employee — UI가 확인 후 재요청
    return out


def list_members() -> dict:
    """{ok, people:[{login_email, name, kind, brain_access, department?, position?,
    auth_user_id?, role?, must_change_password?, disabled_at?}]}

    kind: member(앱 계정 있음) · roster(명단에만 — 계정 없음) · employee(ks-works 재직
    직원 — 기여자로 지정돼 목록에 나온다) · former_employee · unknown.
    전화번호·성별은 서버가 내보내지 않는다(개인정보는 DB에만).
    """
    res = _call("list")
    if res.get("ok"):
        res["people"] = res.get("people") or []
    return res


def provision_roster() -> dict:
    """명단에서 계정이 없는 사람을 일괄 생성. {ok, created, converted, skipped_employee,
    failed:[{login_email, name, error}], initial_password}

    퇴사자는 같은 ID의 계정을 전환하고(ks-works는 계속 차단), 재직 직원은 건너뛴다.
    """
    return _call("provision_roster")


def set_brain_access(login_email: str, enabled: bool) -> dict:
    """공유 뇌 이벤트 기여 허용/해제. 관리자는 규칙으로 항상 포함이라 지정할 필요 없다."""
    return _call("set_brain_access", login_email=login_email, enabled=bool(enabled))


def create_member(login: str, name: str, admin: bool = False,
                  allow_former_employee: bool = False) -> dict:
    """{ok, member, initial_password, converted?} — login은 사내 ID(도메인 자동) 또는 전체 이메일.

    같은 ID의 **퇴사** 직원이 있으면 서버가 code="former_employee"로 먼저 거절한다.
    확인 후 allow_former_employee=True로 다시 부르면 그 계정을 앱 회원으로 전환한다
    (auth 차단 해제 + 초기 비밀번호 — ks-works는 퇴사 기록 때문에 계속 막힌다).
    """
    login, name = (login or "").strip(), (name or "").strip()
    if not login or not name:
        return {"ok": False, "error": "로그인 ID와 이름을 입력하세요."}
    return _call("create", login=login, name=name, role="admin" if admin else "member",
                 allow_former_employee=bool(allow_former_employee))


def reset_password(auth_user_id: str) -> dict:
    """초기 비밀번호로 되돌리고 다음 로그인 때 변경을 강제한다. {ok, initial_password}"""
    return _call("reset_password", auth_user_id=auth_user_id)


def set_disabled(auth_user_id: str, disabled: bool) -> dict:
    """사용 중지(로그인 차단)/재개. 본인 계정은 중지할 수 없다."""
    return _call("set_disabled", auth_user_id=auth_user_id, disabled=bool(disabled))
