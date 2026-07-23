"""
ui/workers/update_worker.py — 자동 업데이트 QThread 워커 (확인 · 다운로드)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`core/updater.py`는 GUI-agnostic이라 PySide6를 모른다(설계 규율). 네트워크
I/O를 GUI 스레드에서 돌리면 앱이 얼어붙으므로 여기서 QThread로 감싸고,
updater가 콜백으로 주는 진행률을 Signal로 바꿔 UI에 전달한다.

⚠ **확인은 자동, 설치는 사용자가 누를 때만**(updater 헤더의 규율). 이 모듈은
  확인·다운로드까지만 하고 설치는 하지 않는다 — 교정 도중 앱이 멋대로
  재시작되면 사용자의 작업이 날아간다. 설치 호출은 MainWindow가 사용자
  클릭을 받은 뒤에 한다.
"""

import threading

from PySide6.QtCore import QThread, Signal


class UpdateCheckWorker(QThread):
    """두 채널(앱·데이터)의 새 릴리스를 백그라운드로 확인한다.

    네트워크 없음·저장소 없음·비공개 전환 등은 updater가 전부 조용히 삼켜
    None을 주므로(graceful), 이 워커는 실패해도 앱 동작에 영향을 주지 않는다.
    """

    done = Signal(dict)      # {"app": info|None, "data": info|None}

    def run(self):
        try:
            from core import updater
            self.done.emit(updater.check_all())
        except Exception:
            # 확인 실패는 사용자에게 알릴 사건이 아니다(업데이트는 부가 기능).
            self.done.emit({"app": None, "data": None})


class UpdateDownloadWorker(QThread):
    """선택된 채널의 zip을 내려받는다. 취소 가능(stop_event)."""

    progress = Signal(int, int)      # (받은 바이트, 전체 바이트)
    done     = Signal(object)        # pathlib.Path | None(실패·취소)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self._info = info
        self._stop = threading.Event()

    def request_stop(self):
        self._stop.set()

    def run(self):
        try:
            from core import updater
            path = updater.download(
                self._info,
                progress=lambda done, total: self.progress.emit(done, total),
                stop_event=self._stop,
            )
        except Exception:
            path = None
        self.done.emit(path)
