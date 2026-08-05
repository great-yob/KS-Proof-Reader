"""
ui/workers/login_worker.py — 로그인/세션 복원 비동기 워커 (실행 게이트)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
core.auth 의 로그인/세션 복원을 UI 스레드 밖에서 실행. 결과는 시그널로.
어떤 실패도 예외로 전파하지 않는다.

⚠ **워커를 단명 위젯(모달 다이얼로그)이 소유하면 finished 전에 파괴돼 앱이 죽는다**
  ("QThread: Destroyed while running", 2026-06-23 실사고). 소유자는 결과 처리 전에
  wait() 하거나 장수명 객체가 참조를 잡아야 한다.
"""

from PySide6.QtCore import QThread, Signal


class LoginWorker(QThread):
    done = Signal(dict)            # {"ok":bool, "user"?|"error"}

    def __init__(self, email_or_prefix: str, password: str, parent=None):
        super().__init__(parent)
        self._e = email_or_prefix
        self._p = password

    def run(self):
        try:
            from core import auth
            self.done.emit(auth.login(self._e, self._p))
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e)})


class SessionRestoreWorker(QThread):
    """저장된 세션(DPAPI)을 복원·검증(refresh)한다 — 게이트가 앱 시작 시 사용.

    복원 **결과의 종류**까지 돌려준다(오프라인 유예/명시적 거부 구분).

    결과 dict: {ok, user?, mode: verified|offline, reason?: no_session|no_config|
                rejected|offline_expired, grace_days_left?}
    """
    done = Signal(dict)

    def run(self):
        try:
            from core import auth
            self.done.emit(auth.restore_session() or {"ok": False, "reason": "no_session"})
        except Exception as e:
            self.done.emit({"ok": False, "reason": "no_session", "error": str(e)})
