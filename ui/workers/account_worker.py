"""
ui/workers/account_worker.py — 계정 작업(비밀번호 변경·회원 관리) 비동기 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
core.auth / core.member_admin 의 네트워크 호출을 UI 스레드 밖에서 실행한다. 호출할
함수는 `"모듈.함수"` 문자열로 받는다(core import는 워커 스레드 안에서). 결과는 언제나
dict 하나로 `done`에 실린다 — 예외도 {ok: False, error}로 바꿔 전파하지 않는다.

⚠ login_worker.py와 같은 규칙: 단명 다이얼로그가 소유하면 결과 처리 전에 wait() 할 것
  ("QThread: Destroyed while running" — 2026-06-23 실사고).
"""

from PySide6.QtCore import QThread, Signal


class AccountWorker(QThread):
    done = Signal(dict)

    def __init__(self, target: str, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._target = target          # 예: "auth.change_password", "member_admin.list_members"
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            import importlib
            mod_name, fn_name = self._target.rsplit(".", 1)
            fn = getattr(importlib.import_module(f"core.{mod_name}"), fn_name)
            res = fn(*self._args, **self._kwargs)
            self.done.emit(res if isinstance(res, dict) else {"ok": bool(res)})
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e)})
