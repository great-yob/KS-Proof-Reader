"""
ui/workers/login_worker.py — 로그인/세션 복원 비동기 워커 (선택적 로그인)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
core.auth 의 로그인/세션 복원을 UI 스레드 밖에서 실행. 결과는 시그널로.
어떤 실패도 예외로 전파하지 않는다.
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


class RestoreWorker(QThread):
    """앱 시작 시 저장된 세션(DPAPI)을 복원·검증(refresh). 결과 user|None."""
    done = Signal(object)          # user dict | None

    def run(self):
        try:
            from core import auth
            self.done.emit(auth.restore())
        except Exception:
            self.done.emit(None)
