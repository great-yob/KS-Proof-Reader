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
        # KS_HWP_VISIBLE=1 → 한/글 창을 숨기지 않는다. 배포 PC에서 '모달이 숨겨져
        #   멈춘' 상황을 눈으로 확인하기 위한 현장 진단 스위치.
        self.visible   = visible or os.environ.get("KS_HWP_VISIBLE") == "1"
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
        # 행업 진단용 — 워커가 마지막으로 낸 stderr 한 줄. 타임아웃 메시지에 실어
        #   보내면 '어느 단계에서 멈췄는지'가 화면 캡처만으로 드러난다(브리지 로그는
        #   activity_panel._DROP이 화면에서 감추므로 이 경로가 유일하다).
        self._last_worker_line = ""
        # 메모 재색인이 만드는 중간 저장본(아래 `_reindex_document`). 끝나면 지운다.
        self._stage_file = None
        self._reindexing = False

    def open(self):
        """32비트 서브프로세스를 시작하고 HWP 파일을 엽니다."""
        cmd = _bridge_command()
        # 재개(재색인) 시에도 drain 스레드가 돌아야 한다 — close가 세워 둔 정지
        #   플래그를 내려 두지 않으면 새 스레드가 첫 루프에서 바로 끝난다.
        self._stderr_stop.clear()
        # ⚠ 앞 프로세스가 남긴 큐 내용도 버린다. EOF 센티널(None)이 남아 있으면 새
        #   프로세스의 첫 명령이 그것을 먼저 읽고 **'브리지 응답 없음(프로세스
        #   종료됨)'**으로 실패한다(실측 2026-08-10: 첫 재색인만 실패했다).
        for q in (self._stdout_queue, self._stderr_queue):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

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
        (브리지가 못 구하면 None — 호출 측은 getattr 폴백으로 읽는다).

        `last_note_lines`에는 **각주·글상자에서 실려 온 라인 인덱스**가 담긴다 —
        추출 순서·오프셋은 그대로 두고 분류 정보만 덧붙인 것이다(왜 재배열하지
        않는지는 hwp_bridge_worker._classify_note_lines 주석). 못 구하면 빈 목록.

        ⚠ `last_note_lines`는 이름과 달리 **컨트롤 텍스트 전부**(각주·미주·글상자·표·
        목차·머리말)다. **실제 각주만** 필요한 곳(미리보기 `[각주]` 표지)은 반드시
        `last_footnote_lines`를 쓸 것 — 그쪽은 `fn`/`en` ctrl로 확정한 부분집합이다.
        """
        result = self._send_cmd({"cmd": "get_text"})
        if not result.get("ok"):
            raise RuntimeError(f"텍스트 추출 실패: {result.get('error')}")
        self.last_page_count = result.get("page_count")
        self.last_note_lines = result.get("note_lines") or []
        self.last_footnote_lines = result.get("footnote_lines") or []
        # 강제 줄 나눔 복원 건수 — 0이 아니면 추출 텍스트가 달라졌다는 뜻이라
        #   호출부(워커)가 화면 로그로 남긴다(사유 없는 결과 변동을 만들지 않기 위함).
        self.last_linebreaks = result.get("linebreaks") or 0
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

    # 메모는 등장 1곳당 0.08초(실측 2026-08-07 · 본문 20곳 1.5초)라 치환보다 무겁다.
    #   배치를 작게 잡아 취소 반응성을 유지한다(브리지 명령 하나는 중단할 수 없다).
    _MEMO_BATCH = 5

    # ★이 항목 수마다 문서를 **저장→닫기→다시 열기**로 재색인한다(`_reindex_document`).
    #   25는 실측으로 고른 값이다(아래 표). 더 잘게 자르면 열고 닫는 값이 커지고,
    #   크게 잡으면 재색인 전에 실패가 다시 쌓인다.
    _MEMO_REINDEX_ITEMS = 25
    # ★★그리고 **메모 수**로도 끊는다 — 색인이 낡는 속도는 항목 수가 아니라 **그 사이
    #   문서에 박은 메모 수**에 달렸다. 실측 2026-08-10(25항목 묶음 6개, 묶음별 메모 수 →
    #   실패 수): 24곳→0 · 40곳→0 · 49곳→2 · 51곳→0 · 58곳→12 · 97곳→11.
    #   **50곳 근처에서 급격히 나빠진다.** 등장이 36곳인 낱말 하나가 묶음 예산을 통째로
    #   써 버리는 일이 잦아, 항목 수 기준만으로는 이 경계를 지킬 수 없다.
    _MEMO_REINDEX_MEMOS = 40

    def _reindex_document(self, log=None):
        """★한/글의 '찾기 색인'을 새로 만든다 — 저장 → 닫기 → 다시 열기.

        ★왜 필요한가(실측 2026-08-10 · 실파일 고독사 · 152항목 · 등장 360곳):
          메모 컨트롤은 문단 안에서 자리를 차지하는데, **같은 세션에서는 이후의
          `RepeatFind`가 그 변화를 반영하지 못한다.** 그래서 이미 메모를 단 자리와
          겹치거나 맞닿은 낱말을 찾으면, 찾기는 '옛 본문' 기준으로 매치를 돌려주고
          그 자리에 메모를 열면 `InsertText`가 `RPC_E_SERVERFAULT`로 죽는다. 한 번
          죽은 자리는 그 세션에서 **어떤 재시도로도 살아나지 않는다**(재열기·삭제 후
          재시도·뒤쪽 좌표·대기·항목 끝 재시도 전부 실패, 102곳 중 1곳만 회복).
          이등분으로 특정한 최소 재현: '키메세지'(36곳)에 메모를 단 뒤 '메세지를'
          (‘키메세지를’ 속)을 달면 죽는다 — 같은 항목 집합에서 '키메세지'만 빼면 12/12 성공.
          문서를 저장해 다시 열면 색인이 새로 만들어져 그 자리도 정상 동작한다.

          실측(같은 원고 152항목 · 등장 360곳 · 메모 성공 자리 수 / 소요):
            재색인 없음        116곳 / 68초   ← 사용자가 겪은 '메모 다량 유실'
            25항목마다 재색인  302곳 / 68초   ← 현재 설계(저장본 빈 메모 0)
          여는 값이 공짜는 아니지만, 죽을 자리에 쏟던 시간이 사라져 총 소요는 같다.
          ⚠ 재색인해도 남는 30곳은 대부분 '이미 메모가 있는 자리와 겹쳐 찾기에서
            사라진 등장'이라, 정오표에 '표시하지 못함'으로 남는다(조용히 사라지지 않는다).

        ⚠ 중간 저장본은 **캐시 폴더**에 둔다. 원고 옆에 두면 사용자가 산출물로 오해하고,
          읽기 전용 설치 폴더에서는 쓸 수도 없다.
        ⚠ 재색인 사이의 `close()`는 중간 저장본을 지우면 안 된다(바로 다시 열 파일이다).
          `_reindexing` 플래그가 그것을 가른다.
        """
        ext = os.path.splitext(self.file_path)[1] or ".hwp"
        try:
            from datapaths import cache_dir
            stage_dir = cache_dir()
        except Exception:
            import tempfile
            stage_dir = tempfile.gettempdir()
        stage = os.path.join(stage_dir, "_ks_memo_stage" + ext)

        self._reindexing = True
        try:
            self.save_as(stage)
            self.close()
            self.file_path = stage
            self._stage_file = stage
            self.open()
        finally:
            self._reindexing = False
        if log:
            log("  [메모] 문서 재색인 — 이어서 답니다")

    def insert_memos(self, corrections: list, progress_cb=None,
                     stop_event: threading.Event = None,
                     mark_anchor: bool = False) -> tuple:
        """수락 교정을 **한/글 메모**로 기록한다 — 원고를 바꾸지 않는다(글자도 서식도).

        ⚠ `mark_anchor`(앵커 글자색)는 기본 **꺼짐** — 사용자 지정 2026-08-07.

        `corrections`는 dict 목록이며 `memo_text`(메모 본문)와 `occ_total`(그 낱말의
        총 등장 수)을 추가로 담는다. 반환 스키마는 `apply_corrections`와 같다
        (stats, detail) — 정오표 조립 코드를 공유하기 위함이다.

        ★**정렬은 apply와 정반대인 '짧은 원문 우선'이다.** apply의 '긴 원문 우선'은
          치환이 서로를 오염시키지 않게 하는 장치인데, 메모는 글자를 바꾸지 않으므로
          그 이유가 없다. 대신 정반대의 이유가 생긴다 — **메모 본문이 원문을 인용하기
          때문**이다. 긴 '고려해야한다'에 메모를 먼저 달면 그 본문에 '해야한다'가 새로
          생기고, 다음 항목의 찾기가 **자기 메모 안의 글자를 원고의 등장으로 오인**해
          그 자리에 메모를 달려다 실패한다(실측 2026-08-08: '해야한다' 단독 14/14 성공 /
          중첩 3항목과 함께면 실패, 실패 자리의 스토리가 앞서 만든 메모의 서브스토리).
          짧은 것부터 달면 이미 달린 메모의 원문은 항상 **더 짧으므로** 뒤에 오는 긴
          원문을 품을 수 없다 — 이 부류가 원리적으로 사라진다.
          A/B 실측(05.hwp · 22항목 · 정오표 37행): 긴 원문 우선 = 메모 31곳·교정본과
          6행 차이(그 6건이 사용자가 보고한 미반영 항목과 일치) / **짧은 원문 우선 =
          메모 32곳·3행 차이**. 되돌리려면 이 측정을 먼저 다시 할 것.
        ⚠ 항목 순서는 **등장 인덱스에 영향을 주지 않는다** — 브리지 memo는 항목마다
          문서 처음부터 다시 훑으므로(MoveDocBegin) 좌표계는 apply/locate와 그대로 같다.
        """
        corr_data = [c for c in corrections if isinstance(c, dict)]
        # ⚠ **'진짜 오탈자를 먼저' 정렬은 실측으로 기각됐다**(2026-08-10). 뒤로 갈수록
        #   실패가 는다는 관찰에서 나온 발상이었으나, 실제로는 실패가 **자리에 붙어 있어**
        #   순서를 바꿔도 같은 항목이 그대로 실패했고 총량만 나빠졌다
        #   (312/335 → 305/336). 재시도하지 말 것.
        corr_data.sort(key=lambda c: len(c.get("original") or ""))

        stats_total  = {"memo": 0, "blocked": 0, "fail": 0}
        detail_total = []
        total = len(corr_data)
        done  = 0
        # ★재색인 경계(`_reindex_document`의 실측 근거 참조). 마지막 묶음 뒤에는 안 한다.
        next_reindex = self._MEMO_REINDEX_ITEMS
        memos_since  = 0            # 마지막 재색인 이후 실제로 단 메모 수

        for i in range(0, total, self._MEMO_BATCH):
            if stop_event is not None and stop_event.is_set():
                break
            due = (i >= next_reindex
                   or memos_since >= self._MEMO_REINDEX_MEMOS)
            if due and i < total:
                next_reindex = i + self._MEMO_REINDEX_ITEMS
                memos_since = 0
                try:
                    self._reindex_document(self.logger)
                except Exception as exc:
                    # 재색인은 품질 장치이지 필수 경로가 아니다 — 실패하면 그대로 잇는다
                    #   (그 뒤로는 실패가 늘겠지만, 지금까지 단 메모는 살아 있다).
                    if self.logger:
                        self.logger(f"  [메모] 문서 재색인 실패 — 그대로 진행: {exc}")
            batch = corr_data[i:i + self._MEMO_BATCH]

            batch_cb = None
            if progress_cb is not None:
                def batch_cb(pct, _t, _done=done, _blen=len(batch)):
                    frac = min(max(pct, 0), 100) / 100.0
                    progress_cb(_done + _blen * frac, total)

            result = self._send_cmd({
                "cmd": "memo",
                "corrections": batch,
                "mark_anchor": bool(mark_anchor),
            }, progress_cb=batch_cb, total=total)

            if not result.get("ok"):
                raise RuntimeError(f"메모 달기 실패: {result.get('error')}")

            for k, v in (result.get("stats") or {}).items():
                stats_total[k] = stats_total.get(k, 0) + v
            batch_detail = result.get("detail") or []
            detail_total.extend(batch_detail)
            memos_since += sum(d.get("memoed", 0) for d in batch_detail)
            done += len(batch)
            if progress_cb is not None:
                progress_cb(done, total)

        # ⚠ **못 단 자리를 재색인 뒤 다시 도는 스윕을 넣지 말 것**(실측 2026-08-10:
        #   35곳 재시도 → 회복 0곳, 재색인 한 번 값 ~10초만 더 든다). 재색인은 색인을
        #   되살리지만 그 자리는 **되살리지 못한다** — 남은 실패는 이미 메모가 달린
        #   앵커와 겹쳐 문서 재열기 뒤에는 아예 찾기에서 사라지는 등장들이고, 등장
        #   인덱스로 다시 겨눌 수도 없다(가리키던 자리가 없어져 번호가 밀린다).
        #   이 자리들은 정오표에 '표시하지 못함' 사유로 남는 것이 정직한 결말이다.
        return stats_total, detail_total

    def export_pdf(self, output_path: str) -> str:
        """현재 문서를 PDF로 내보낸다(문서 무변경). 반환: 실제 저장 경로.

        ⚠ 한/글의 PDF 변환은 대용량 문서에서 수십 초가 걸릴 수 있어(17쪽 1MB 문서
          실측 4.7초) 유휴 타임아웃을 넉넉히 준다. 브리지는 진행률을 내지 않는다.
        """
        result = self._send_cmd({
            "cmd": "export_pdf",
            "output_path": os.path.abspath(output_path),
        }, timeout=600)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "PDF 변환 실패")
        return result.get("pdf") or output_path

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

    # 쪽 번호 수집의 전체 찾기 예산(등장 수). 등장 1곳당 9ms 실측(2026-08-05, 한/글
    #   2010 · 05.hwp 82등장 0.70초)이라 6,000이면 ~55초. 대부분 원고는 등장 수천 곳
    #   이하라 예산에 닿지 않는다.
    _LOCATE_BUDGET = 6000
    # …그래도 최악을 시간으로 한 번 더 묶는다. 이 단계는 정오표에 좌표를 넣기 위한
    #   부가 작업이라, 사용자를 기다리게 할 자격이 이만큼뿐이다. 소진되면 남은 항목의
    #   쪽 칸만 비고 정오표는 그대로 나온다.
    _LOCATE_SECONDS = 45.0

    def locate_originals(self, originals: list, budget: int = None) -> dict:
        """각 원문의 **등장별 쪽 번호** 목록 (치환 없음 — 문서 무변경).

        정오표를 '등장 1곳 = 1행'으로 쓰기 위한 것. ⚠ 반드시 `apply_corrections`
        **이전에 한 번에** 호출할 것 — 치환이 진행되면 뒤 페이지가 밀려 앞뒤 항목의
        쪽이 서로 다른 장면을 가리키게 된다(브리지 locate 주석 참조).

        Returns:
            dict: {original: [쪽|None, …]}. 못 찾은 원문은 키 자체가 없다.
                  브리지 실패 시 예외 대신 빈 dict(쪽 칸만 비는 안전한 실패).
        """
        if not originals:
            return {}
        result = self._send_cmd({
            "cmd": "locate",
            "originals": list(originals),
            "budget": int(budget or self._LOCATE_BUDGET),
            "time_budget": self._LOCATE_SECONDS,
        })
        if not result.get("ok"):
            if self.logger:
                self.logger(f"  [쪽번호] 수집 실패: {result.get('error')} — 쪽 표시 생략")
            return {}
        return result.get("pages", {})

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

        # 재색인 중간 저장본 정리 — 재색인 **사이의** close에서는 지우지 않는다
        #   (바로 다시 열 파일이다).
        if self._stage_file and not self._reindexing:
            try:
                os.remove(self._stage_file)
            except OSError:
                pass
            self._stage_file = None

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
                self._last_worker_line = line[:300]
                try:
                    p = json.loads(line)
                    # progress 메시지면 큐에 넣어 _send_cmd가 소비
                    if isinstance(p, dict) and "progress" in p:
                        self._stderr_queue.put(p)
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass
                # 지연 경고(_StallWatch)만은 **접두사 없이** 그대로 넘긴다 —
                #   activity_panel._DROP이 '[Worker stderr]'를 통째로 감추므로,
                #   접두사를 붙이면 정작 사용자가 봐야 할 이 한 줄이 사라진다.
                if self.logger:
                    if line.startswith("[한/글 대기]"):
                        self.logger(line)
                    else:
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
                        tail = (self._last_worker_line or "").strip()
                        where = (f"마지막 브리지 출력: {tail}" if tail else
                                 "브리지가 출력을 한 줄도 내지 못했습니다 "
                                 "(32비트 워커 실행 자체가 막혔을 수 있습니다).")
                        return {"ok": False, "error": "\n".join([
                            f"HWP 브리지 응답 없음 — {timeout:.0f}초간 아무 출력이 "
                            f"없어 중단했습니다.",
                            where,
                            "",
                            "확인해 주세요:",
                            "  1. 한/글을 직접 한 번 실행해 사용자 등록·개인정보 동의 등 "
                            "안내 창을 모두 닫아 주세요.",
                            "  2. 화면에 한/글 대화상자가 떠 있으면 확인 후 닫아 주세요.",
                            "  3. 작업 관리자에서 남은 hwp.exe를 모두 끝낸 뒤 "
                            "다시 시도해 주세요.",
                        ])}
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
