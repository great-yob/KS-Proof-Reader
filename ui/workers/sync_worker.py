"""
ui/workers/sync_worker.py — 공유 용어 뇌 동기화 백그라운드 워커 (DO-4d)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
core.userdict_sync 의 push/pull/sync 를 UI 스레드 밖에서 실행한다. 전적으로
부수효과 — 미설정([SUPABASE] 비밀 없음)·오프라인·오류 시 조용히 no-op. 교정
흐름과 완전히 독립이며 어떤 실패도 앱에 영향을 주지 않는다.
"""

from PySide6.QtCore import QThread, Signal


class SyncWorker(QThread):
    log_message = Signal(str)
    done        = Signal(dict)

    def __init__(self, mode: str = "sync", parent=None):
        super().__init__(parent)
        self._mode = mode

    def run(self):
        try:
            from core import userdict_sync as us
            if not us.available():
                return                      # 미설정 → 즉시 종료(무영향)
            log = self.log_message.emit
            if self._mode == "push":
                res = {"pushed": us.push(logger=log)}
            elif self._mode == "pull":
                res = us.pull(logger=log)
            else:
                res = us.sync(logger=log)
            self.done.emit(res or {})
        except Exception:
            pass                            # 동기화 실패는 무시(로컬 큐에 보존)
