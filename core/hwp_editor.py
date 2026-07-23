"""
core/hwp_editor.py — HWP 문서 편집기 (32비트 브리지 클라이언트)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
64비트 PySide6 앱에서 32비트 Python 서브프로세스를 통해
HWP COM 자동화를 실행합니다.

아키텍처:
  [개발]   main.py/PySide6 ─→ HwpEditor ─→ subprocess(32bit python) ─→ 워커 ─→ COM
  [배포본] KS-AI Editor.exe ─→ HwpEditor ─→ bridge32/hwp_bridge_worker.exe ─→ COM

⚠ **배포본은 32비트 파이썬을 동봉한 브리지 EXE로 실행한다**(`bridge32/`).
  과거엔 배포본도 `_find_python32()` + `.py` 스크립트 경로를 썼는데, 두 가지가 동시에
  깨져 있었다: ① PyInstaller가 `hwp_bridge_worker.py`를 번들에 넣지 않아 스크립트
  자체가 없었고, ② 있었더라도 사용자 PC에 32비트 Python+pywin32가 설치돼 있어야 했다
  (후보 경로 1순위가 개발 PC 경로였다). 그래서 배포본에서는 HWP 교정이 통째로 죽었다.
  → `build_dist.py`가 32비트 파이썬으로 워커를 따로 빌드해 `bridge32/`에 동봉하고,
    아래 `_bridge_command()`가 그것을 **1순위**로 쓴다. 파이썬 스크립트 경로는 개발용 폴백.
"""

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
import time

from .models import Correction

# 32비트 Python 경로 자동 탐지
_PYTHON32_CANDIDATES = [
    r"C:\Users\user9\AppData\Local\Programs\Python\Python311-32\python.exe",
    r"C:\Python311-32\python.exe",
    r"C:\Python310-32\python.exe",
    r"C:\Python39-32\python.exe",
]

_BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "hwp_bridge_worker.py")

# 배포본에 동봉되는 32비트 브리지 — EXE 옆 `bridge32/hwp_bridge_worker.exe`.
#   data/ 와 같은 계열의 '옆에 두는 자산'이지만 **코드**라서 앱 패키지에 들어간다
#   (data는 사전 갱신 주기, 이건 코드 수정 주기를 따른다 — datapaths.py 헤더 참조).
_BRIDGE_DIR_NAME = "bridge32"
_BRIDGE_EXE_NAME = "hwp_bridge_worker.exe"


def _bundled_bridge_exe():
    """동봉된 32비트 브리지 실행 파일 경로. 없으면 None."""
    try:
        from datapaths import app_dir      # 최상위·무의존 모듈
        p = app_dir() / _BRIDGE_DIR_NAME / _BRIDGE_EXE_NAME
        return str(p) if p.is_file() else None
    except Exception:
        return None


def _bridge_command() -> list:
    """브리지 실행 명령. 동봉 EXE가 있으면 그것, 없으면 32비트 파이썬 + 스크립트."""
    exe = _bundled_bridge_exe()
    if exe:
        return [exe]

    if not os.path.isfile(_BRIDGE_SCRIPT):
        # 배포본인데 bridge32/ 가 없는 경우 — 앱 패키지가 불완전하다.
        raise RuntimeError(
            "HWP 브리지를 찾을 수 없습니다.\n"
            f"동봉 브리지({_BRIDGE_DIR_NAME}/{_BRIDGE_EXE_NAME})도, "
            "브리지 스크립트도 없습니다.\n"
            "설치 파일로 다시 설치하거나 배포 패키지를 최신본으로 갱신하세요."
        )
    return [_find_python32(), _BRIDGE_SCRIPT]


def _find_python32() -> str:
    """32비트 Python 실행 파일 경로 탐색"""
    # 1. 환경 변수
    env_path = os.environ.get("PYTHON32_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. 시스템 python이 32비트인지 확인
    import struct
    system_python = sys.executable
    # 현재 프로세스가 32비트면 시스템 python 사용
    if struct.calcsize("P") * 8 == 32:
        return system_python

    # 3. 알려진 경로 후보
    for path in _PYTHON32_CANDIDATES:
        if os.path.isfile(path):
            return path

    # 4. PATH에서 python.exe 검색
    import shutil
    py = shutil.which("python")
    if py:
        return py

    raise RuntimeError(
        "32비트 Python을 찾을 수 없습니다.\n"
        "한/글이 32비트이므로 32비트 Python이 필요합니다.\n"
        "환경 변수 PYTHON32_PATH를 설정하거나 Python 32비트를 설치하세요."
    )


class HwpEditor:
    """HWP 문서 편집기 — 32비트 브리지 클라이언트"""

    _active_instances = []

    def __init__(self, file_path: str, logger=None, visible: bool = False):
        self.file_path = file_path
        self.logger    = logger
        self.visible   = visible
        self._proc     = None
        # S1: stderr 진행률/로그 큐 — 워커 lifetime 전체에서 계속 drain
        self._stderr_queue: "queue.Queue[dict]" = queue.Queue()
        self._stderr_thread: threading.Thread = None
        self._stderr_stop = threading.Event()
        # S8: stdout 응답도 drain 스레드가 큐로 받는다 — _send_cmd가 readline에
        #   직접 블록되지 않아 '유휴 타임아웃'(아래)을 실제로 동작시킬 수 있다.
        self._stdout_queue: "queue.Queue" = queue.Queue()
        self._stdout_thread: threading.Thread = None
        # 유휴 타임아웃 기준 — 워커가 stdout/stderr로 마지막 출력을 낸 시각.
        #   (apply/verify는 진행률을 주기적으로 쏘므로 '총 시간'이 아닌 '무응답 시간'으로
        #    재야 대용량 문서에서 거짓 타임아웃이 나지 않는다.)
        self._last_activity = time.time()

    def open(self):
        """32비트 서브프로세스를 시작하고 HWP 파일을 엽니다."""
        cmd = _bridge_command()

        if self.logger:
            self.logger(f"  HWP 브리지 시작: {os.path.basename(cmd[0])}")

        # 32비트 Python의 stdio 기본 인코딩은 한국 Windows에서 CP949이므로
        # UTF-8로 강제 (워커도 startup 시 reconfigure 함)
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.abspath(self.file_path)),
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=child_env,
        )

        # S1: stderr drain 스레드를 워커 lifetime 동안 영구 가동
        # — 워커가 traceback을 쏟아도 OS 파이프 버퍼가 차지 않게 함
        self._stderr_thread = threading.Thread(
            target=self._stderr_drain_loop, daemon=True
        )
        self._stderr_thread.start()

        # S8: stdout drain 스레드 — 응답 라인을 큐로 중계(타임아웃 가능한 대기)
        self._stdout_thread = threading.Thread(
            target=self._stdout_drain_loop, daemon=True
        )
        self._stdout_thread.start()

        # 파일 열기
        result = self._send_cmd({
            "cmd": "open",
            "file_path": os.path.abspath(self.file_path),
            "visible": self.visible,
        })

        if not result.get("ok"):
            raise RuntimeError(
                f"HWP 파일 열기 실패:\n{result.get('error', '알 수 없는 오류')}"
            )

        HwpEditor._active_instances.append(self)

    def get_text(self) -> str:
        """전체 텍스트 추출. 부가로 문서 총 페이지 수를 last_page_count에 보관
        (브리지가 못 구하면 None — 호출 측은 getattr 폴백으로 읽는다)."""
        result = self._send_cmd({"cmd": "get_text"})
        if not result.get("ok"):
            raise RuntimeError(f"텍스트 추출 실패: {result.get('error')}")
        self.last_page_count = result.get("page_count")
        return result.get("text", "")

    # 취소 반응성을 위한 배치 크기 — 브리지 apply는 단일 명령이 끝날 때까지
    #   중단할 수 없으므로, 배치 사이에서 stop_event를 확인한다.
    _APPLY_BATCH = 10

    def apply_corrections(self, corrections: list,
                          progress_cb=None,
                          stop_event: threading.Event = None) -> tuple:
        """
        교정 목록을 HWP 문서에 적용.

        배치(기본 10건) 단위로 브리지에 보내고 배치 사이에서 stop_event를 확인한다
        — 적용 단계 '취소'가 실제로 동작한다(중단 시 나머지 항목 미적용, 저장은
        호출 측 책임이라 원본은 무변경). ⚠ '긴 원문 우선' 불변식은 배치 전에
        전역 정렬로 확정한다 — 브리지의 배치 내 재정렬은 동일 순서를 유지한다.

        Returns:
            tuple: (stats dict, detail list)
        """
        # Correction 객체 → dict 변환
        corr_data = []
        for c in corrections:
            if isinstance(c, Correction):
                corr_data.append({
                    "original":  c.original,
                    "corrected": c.corrected,
                    "reason":    c.reason,
                    "source":    c.source,
                    "color":     c.color,
                    "skip_occurrences": list(getattr(c, "skip_occurrences", []) or []),
                })
            elif isinstance(c, dict):
                corr_data.append(c)

        # 전역 불변식: 긴 원문 우선(부분문자열 오염 방지) — 배치로 쪼개도 유지되도록
        #   여기서 한 번 정렬한다(브리지도 배치 내에서 같은 키로 재정렬 → 순서 동일).
        corr_data.sort(key=lambda c: len(c.get("original") or ""), reverse=True)

        stats_total  = {"dict": 0, "ai_typo": 0, "ai_polish": 0, "fail": 0}
        detail_total = []
        total = len(corr_data)
        done  = 0

        for i in range(0, total, self._APPLY_BATCH):
            if stop_event is not None and stop_event.is_set():
                break   # 취소 — 처리한 배치까지의 stats/detail만 반환
            batch = corr_data[i:i + self._APPLY_BATCH]

            batch_cb = None
            if progress_cb is not None:
                def batch_cb(pct, _t, _done=done, _blen=len(batch)):
                    # 브리지 진행률(배치 내 0~100%) → 전체 '처리 항목 수'로 환산
                    frac = min(max(pct, 0), 100) / 100.0
                    progress_cb(_done + _blen * frac, total)

            result = self._send_cmd({
                "cmd": "apply",
                "corrections": batch,
            }, progress_cb=batch_cb,
               total=total)

            if not result.get("ok"):
                raise RuntimeError(f"교정 적용 실패: {result.get('error')}")

            for k, v in (result.get("stats") or {}).items():
                stats_total[k] = stats_total.get(k, 0) + v
            detail_total.extend(result.get("detail") or [])
            done += len(batch)
            if progress_cb is not None:
                progress_cb(done, total)

        return stats_total, detail_total

    def verify_originals(self, originals: list) -> dict:
        """각 원문 문자열이 문서 '찾기'로 도달 가능한지 검증 (치환 없음 — 문서 무변경).

        추출 텍스트에는 있으나 문서에는 연속으로 존재하지 않는 원문(각주 앵커·책갈피 등
        보이지 않는 제어문자가 낀 경우)을 분석 단계에서 걸러내기 위한 것.

        Returns:
            dict: {original: bool(문서에서 찾음)}. 브리지 실패 시 예외.
        """
        result = self._send_cmd({"cmd": "verify", "originals": list(originals)})
        if not result.get("ok"):
            raise RuntimeError(f"문서 대조 검증 실패: {result.get('error')}")
        return result.get("found", {})

    def save_as(self, output_path: str):
        result = self._send_cmd({
            "cmd": "save_as",
            "output_path": os.path.abspath(output_path),
        })
        if not result.get("ok"):
            raise RuntimeError(f"저장 실패: {result.get('error')}")

    def close(self):
        """HWP 종료 + 서브프로세스 정리"""
        if self in HwpEditor._active_instances:
            HwpEditor._active_instances.remove(self)

        if self._proc and self._proc.poll() is None:
            # HWP Quit은 대용량 문서에서 수 초 걸릴 수 있어 close는 여유를 둔다.
            try:
                self._send_cmd({"cmd": "close"}, timeout=15)
            except Exception:
                pass
            try:
                self._send_cmd({"cmd": "quit"}, timeout=5)
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

        # I6: stderr/stdout drain 스레드 정리
        self._stderr_stop.set()
        for th_attr in ("_stderr_thread", "_stdout_thread"):
            th = getattr(self, th_attr, None)
            if th and th.is_alive():
                th.join(timeout=2)
            setattr(self, th_attr, None)
        self._proc = None

    # ── 내부 통신 ────────────────────────────────

    def _stderr_drain_loop(self):
        """워커의 stderr를 항상 비워주는 영구 루프.

        S1: 워커가 진행률 JSON뿐 아니라 win32com 경고/traceback을 stderr로 쏟을 수 있는데,
            그때 OS 파이프 버퍼가 차면 워커가 stderr.write에서 블록되어 메인앱이 멈춤.
            이 스레드가 항상 비워주므로 데드락 없음.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, ""):
                if self._stderr_stop.is_set():
                    break
                self._last_activity = time.time()   # 유휴 타임아웃 기준 갱신
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                    # progress 메시지면 큐에 넣어 _send_cmd가 소비
                    if isinstance(p, dict) and "progress" in p:
                        self._stderr_queue.put(p)
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass
                # JSON이 아닌 일반 메시지는 로거로 흘려보냄
                if self.logger:
                    self.logger(f"  [Worker stderr] {line}")
        except Exception:
            pass

    def _stdout_drain_loop(self):
        """워커의 stdout(응답 채널)을 큐로 중계하는 영구 루프.

        S8: _send_cmd가 readline에 직접 블록되면 타임아웃을 걸 수 없어, 워커가
            행업(HWP 모달·COM 데드락)하면 호출 스레드가 영원히 멈췄다. 이 스레드가
            라인을 큐에 넣고 _send_cmd는 큐를 '타임아웃 있는 대기'로 소비한다.
            EOF(프로세스 종료) 시 None 센티널을 넣어 대기 측을 깨운다.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                self._last_activity = time.time()
                self._stdout_queue.put(line)
        except Exception:
            pass
        self._stdout_queue.put(None)   # EOF 센티널

    def _send_cmd(self, cmd_dict: dict, timeout: float = 120,
                  progress_cb=None, total: int = 0) -> dict:
        """32비트 워커에 JSON 명령 전송 + 응답 수신.

        progress_cb가 주어지면 stderr 큐에서 진행률을 폴링해 UI에 중계한다.
        timeout은 '유휴 타임아웃'(초) — 워커가 stdout/stderr로 아무 출력도 내지
        않은 채 timeout을 넘기면 행업으로 판단, 프로세스를 강제 종료하고 오류를
        반환한다(진행률을 내는 장시간 apply/verify는 정상 계속).
        """
        if not self._proc or self._proc.poll() is not None:
            return {"ok": False, "error": "HWP 브리지 프로세스가 종료되었습니다."}

        try:
            # 명령 전송 — 유휴 기준 시각을 전송 시점으로 리셋
            self._last_activity = time.time()
            line = json.dumps(cmd_dict, ensure_ascii=False) + "\n"
            self._proc.stdin.write(line)
            self._proc.stdin.flush()

            # stdout 큐에서 응답 대기 — 진행률(stderr 큐)은 그 사이 중계
            while True:
                if progress_cb is not None:
                    try:
                        while True:
                            p = self._stderr_queue.get_nowait()
                            progress_cb(p.get("progress", 0), total)
                    except queue.Empty:
                        pass

                try:
                    response_line = self._stdout_queue.get(timeout=0.25)
                except queue.Empty:
                    # 출력 없음 — 유휴 시간 초과면 행업으로 판단하고 강제 종료
                    if timeout and (time.time() - self._last_activity) > timeout:
                        try:
                            self._proc.kill()
                        except Exception:
                            pass
                        return {"ok": False, "error":
                                f"HWP 브리지 응답 없음 — {timeout:.0f}초간 아무 출력이 없어 "
                                f"중단했습니다 (한/글 행업 추정). 다시 시도해 주세요."}
                    continue

                if response_line is None:   # EOF 센티널 — 프로세스 종료
                    return {"ok": False, "error": "브리지에서 응답 없음 (프로세스 종료됨)"}

                response_line = response_line.strip()
                if not response_line:
                    continue

                try:
                    result = json.loads(response_line)
                    # 응답 수신 후, 큐에 남은 마지막 진행률을 비움 (100% 보장)
                    if progress_cb is not None:
                        try:
                            while True:
                                p = self._stderr_queue.get_nowait()
                                progress_cb(p.get("progress", 0), total)
                        except queue.Empty:
                            pass
                    return result
                except json.JSONDecodeError:
                    # JSON이 아닌 일반 문자열은 로그로 흘려보내고 다음 줄 대기
                    if self.logger:
                        self.logger(f"  [Worker stdout] {response_line}")
                    continue

        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ── atexit 핸들러 ────────────────────────────────
@atexit.register
def _cleanup_hwp_instances():
    for editor in list(HwpEditor._active_instances):
        try:
            editor.close()
        except Exception:
            pass
