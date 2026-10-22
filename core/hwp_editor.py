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
        # 메모 세션 오염 복구가 만드는 중간 저장본(`_reopen_document`). 마지막 close가 지운다.
        self._stage_file = None
        self._reopening = False
        # ★문서에 정의된 **글자 스타일**의 글자모양 지문(`core/hwp_styles.py`).
        #   교정 표시 빨강이 글자 스타일을 해제하는 것을 막기 위해 apply에 실어 보낸다.
        #   None = 아직 안 읽음, [] = 읽을 수 없음(그 경우 예전 동작 그대로).
        self._char_styles = None

    def open(self):
        """32비트 서브프로세스를 시작하고 HWP 파일을 엽니다."""
        cmd = _bridge_command()
        # 같은 편집기를 닫았다 다시 열 때도 drain 스레드가 돌아야 한다 — close가 세워 둔
        #   정지 플래그를 내려 두지 않으면 새 스레드가 첫 루프에서 바로 끝난다.
        self._stderr_stop.clear()
        # ⚠ 앞 프로세스가 남긴 큐 내용도 버린다. EOF 센티널(None)이 남아 있으면 새
        #   프로세스의 첫 명령이 그것을 먼저 읽고 **'브리지 응답 없음(프로세스
        #   종료됨)'**으로 실패한다(실측 2026-08-10: 옛 메모 재색인의 첫 재열기만 실패했다).
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

    def _char_style_fingerprints(self) -> list:
        """★문서의 **글자 스타일** 글자모양 지문 — 한 번만 읽어 캐시한다.

        빨강 교정 표시가 글자 스타일을 해제하는 것을 막기 위한 정보다(자세한 근거는
        `core/hwp_styles.py` 머리말). 읽기는 COM이 아니라 **파일 직접 판독**이라
        한/글이 문서를 연 상태와 무관하고, 실패하면 빈 목록이라 예전 동작으로
        조용히 되돌아간다 — 스타일 보호는 교정의 전제가 아니라 보강이다.
        """
        if self._char_styles is not None:
            return self._char_styles
        try:
            from .hwp_styles import char_style_fingerprints
            self._char_styles = char_style_fingerprints(self.file_path, self.logger)
        except Exception:
            self._char_styles = []
        if self._char_styles and self.logger:
            self.logger(f"  [글자 스타일] 원고에 정의된 글자 스타일 "
                        f"{len(self._char_styles)}종 확인 — 해당 자리는 형광펜으로 표시")
        return self._char_styles

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

        char_styles = self._char_style_fingerprints()

        stats_total  = {"dict": 0, "ai_typo": 0, "ai_polish": 0, "fail": 0}
        detail_total = []
        style_marked = 0        # 글자 스타일 보존 위해 형광펜으로 표시한 자리 수
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
                # 배치마다 함께 보낸다 — 목록이 스무 줄 남짓이라 비용이 없고,
                #   브리지가 상태를 들고 있지 않아도 되어 재색인·재시작에 강하다.
                "char_styles": char_styles,
            }, progress_cb=batch_cb,
               total=total)

            if not result.get("ok"):
                raise RuntimeError(f"교정 적용 실패: {result.get('error')}")

            for k, v in (result.get("stats") or {}).items():
                stats_total[k] = stats_total.get(k, 0) + v
            detail_total.extend(result.get("detail") or [])
            style_marked += int(result.get("style_marked") or 0)
            done += len(batch)
            if progress_cb is not None:
                progress_cb(done, total)

        # ⚠ 브리지가 낸 같은 뜻의 로그는 `[Worker stderr]` 접두사 탓에 화면에서
        #   통째로 감춰진다(activity_panel._DROP) — 사용자에게 보일 줄은 여기서 낸다.
        if style_marked and self.logger:
            self.logger(f"  [글자 스타일] 원고 글자 스타일을 지키려고 빨강 글자 대신 "
                        f"형광펜으로 표시 {style_marked}곳")
        self.last_style_marked = style_marked
        self._resolve_spots(detail_total)
        return stats_total, detail_total

    @staticmethod
    def _resolve_spots(detail: list) -> None:
        """브리지가 남긴 치환 좌표 기록(`spots_raw`)을 **치환이 다 끝난 문서의 좌표**로 바꾼다.

        결과는 각 항목의 `detail["spots"] = [[list, para, 시작, 길이], …]`(등장 번호 순)이고,
        '교정본+메모'의 메모 단계가 찾기 없이 그 자리로 곧장 가는 근거다(`insert_memos`).
        ★왜 좌표인가 — 빨간 글자로 자리를 고르면 **누가 고친 빨강인지**를 모른다. 짧은
          교정문이 긴 교정문 속에 들어 있으면('취업 유지' ⊂ '취업 유지 후 이탈') 남의 자리를
          제 것으로 세고, 같은 교정문을 내는 두 교정('성별 임금 격차'·'성별임금격차' →
          '성별 임금격차')은 자리를 나눌 수 없다(실측 24만 자 원고: 앵커 충돌 30곳).
        ★기록은 **치환 순서**대로 되짚는다 — 뒤 치환이 같은 문단 앞쪽을 늘리거나 줄이면
          이미 기록된 자리가 그만큼 밀린다(빨강 교정문 '임금격차' 앞에서 '경제활동 참가'가
          '경제활동참가'로 줄면 한 칸 당겨진다). detail 순서 = 브리지 처리 순서다(배치를
          나눠도 HwpEditor가 전역 정렬한 순서 그대로 보낸다).
        ⚠ 좌표를 확인하지 못한 치환(`dirty*`)이 있으면 그 문단(또는 스토리·문서 전체)의 기록을
          믿지 않는다 — 그 항목은 `spots`가 없고, 메모 단계는 빨간 글자 찾기로 되돌아간다.
          조용히 밀린 좌표에 메모가 붙는 것보다 낫다(메모 단계도 달기 전에 글자를 확인한다).
        """
        reg = {}            # (list, para) → [자리, …]
        every = []
        per_item = []
        for d in detail:
            mine = []
            for ev in d.pop("spots_raw", None) or []:
                tag = ev[0] if ev else "dirty_all"
                if tag == "dirty_all":
                    for sp in every:
                        sp["ok"] = False
                    mine.append(None)
                elif tag == "dirty_list":
                    for (lst, _p), group in reg.items():
                        if lst == ev[1]:
                            for sp in group:
                                sp["ok"] = False
                    mine.append(None)
                elif tag == "dirty":
                    for sp in reg.get((ev[1], ev[2]), ()):
                        sp["ok"] = False
                    mine.append(None)
                else:
                    lst, para, s0, a, b, i = ev
                    for sp in reg.get((lst, para), ()):
                        if sp["s"] >= s0 + a:
                            sp["s"] += b - a            # 뒤쪽 — 길이 차만큼 밀린다
                        elif sp["s"] + sp["n"] > s0:
                            sp["ok"] = False            # 겹침 — 좌표를 믿을 수 없다
                    sp = {"L": lst, "P": para, "s": s0, "n": b, "i": i, "ok": True}
                    reg.setdefault((lst, para), []).append(sp)
                    every.append(sp)
                    mine.append(sp)
            per_item.append(mine)
        for d, mine in zip(detail, per_item):
            if (mine and len(mine) == int(d.get("replaced") or 0)
                    and all(sp is not None and sp["ok"] for sp in mine)):
                d["spots"] = [[sp["L"], sp["P"], sp["s"], sp["n"]]
                              for sp in sorted(mine, key=lambda x: x["i"])]

    # 메모는 등장 1곳당 0.08초(실측 2026-08-07 · 본문 20곳 1.5초)라 치환보다 무겁다.
    #   브리지 명령 하나는 중간에 끊을 수 없으므로 작게 나눠 취소 반응성을 지킨다.
    _MEMO_SCAN_BATCH = 10       # 자리 찾기 — 항목 수
    _MEMO_KEYS_BATCH = 200      # 서브스토리 정렬 키 — 자리 수
    _MEMO_PLACE_BATCH = 40      # 메모 달기 — 자리 수(≈3초)
    # 진행률에서 '자리 찾기'가 차지하는 몫(나머지는 메모 달기).
    _MEMO_SCAN_SHARE = 0.35
    # 메모 달기 중 세션 오염(브리지 `memo_place`의 poisoned)을 만나면 문서를 다시 여는 최대 횟수.
    #   정상 원고에서는 0회다(실측 2026-10-17: 원고 7개·약 4,800곳에서 오염 0). 이미 메모가
    #   많이 달린 원고는 자리 찾기(찾기 수천 회)만으로 세션이 오염되므로 첫 메모에서 한 번 연다.
    _MEMO_MAX_REOPENS = 5
    # 등장 수를 모를 때의 안전 상한 / 절대 상한 — 브리지(`HwpBridge._MEMO_*`)와 같은 값.
    _MEMO_FALLBACK_OCC = 100
    _MEMO_MAX_OCC = 5000

    def _reopen_document(self):
        """메모 세션 오염 복구 — 지금까지 단 메모를 **중간 저장본**에 담아 닫고 다시 연다.

        오염은 그 한/글 세션에만 붙어 있고 문서를 다시 열면 사라진다(브리지 메모 머리말
        불변식④). 예전엔 이것을 25항목마다 **주기적으로** 했는데(옛 `_reindex_document`),
        지금은 오염을 실제로 만났을 때만 한다 — 정상 원고에서는 한 번도 돌지 않는다.
        ⚠ 중간 저장본은 **캐시 폴더**에 둔다(원고 옆이면 산출물로 오해, 설치 폴더는 읽기 전용).
          마지막 `close()`가 지운다 — 다시 열 때의 close는 지우지 않는다(`_reopening`).
        """
        ext = os.path.splitext(self.file_path)[1] or ".hwp"
        try:
            from datapaths import cache_dir
            stage_dir = cache_dir()
        except Exception:
            import tempfile
            stage_dir = tempfile.gettempdir()
        stage = os.path.join(stage_dir, "_ks_memo_stage" + ext)
        self._reopening = True
        try:
            self.save_as(stage)
            self.close()
            self.file_path = stage
            self._stage_file = stage
            self.open()
        finally:
            self._reopening = False

    def insert_memos(self, corrections: list, progress_cb=None,
                     stop_event: threading.Event = None,
                     mark_anchor: bool = False) -> tuple:
        """수락 교정을 **한/글 메모**로 기록한다 — 원고를 바꾸지 않는다(글자도 서식도).

        ⚠ `mark_anchor`(앵커 글자색)는 기본 **꺼짐** — 사용자 지정 2026-08-07.

        `corrections`는 dict 목록이며 `memo_text`(메모 본문)·`occ_total`·`doc_occ`(등장 수)·
        `skip_occurrences`(표시용 skip)를 담는다. '교정본+메모'는 `red_only`·`anchor_of`와,
        치환 단계가 확인한 자리 좌표 `spots`(`_resolve_spots`)를 함께 싣는다.
        반환 스키마는 `apply_corrections`와 같다(stats, detail) — 정오표 조립 코드를 공유한다.

        ★★2단계다 — **자리를 전부 찾은 뒤에야 메모를 단다**(브리지 메모 머리말 불변식④·⑤).
          ① 자리 정하기: `spots`가 있으면 그대로, 없으면 `memo_scan`(찾기만)
          ② 자리 다툼 해소: 빨간 매치가 다른 교정의 자리와 겹치면 버린다
          ③ 정렬: 서브스토리는 `memo_keys`로 본문 기준 위치를 얻어 **문서 순**으로
          ④ 달기: `memo_place`가 **문서 뒤쪽부터** 좌표로 단다(찾기 0회)
          예전엔 항목마다 문서를 훑으며 그 자리에 바로 달았는데, 메모가 박힌 문서에서
          찾기를 많이 돌리면 세션이 오염돼 그 뒤 메모가 전부 죽었다. 그걸 25항목마다
          저장·닫기·다시 열기(재색인)로 씻었다 — 실측(24만 자 원고 '교정본+메모', 같은 결정):
            예전   메모 291곳 · 못 함 331곳 · 339초(재색인 10회)
            2단계  메모 609곳 · 못 함  13곳 · 157초(남은 13곳은 메모를 거부하는 캡션·머리말류)
          ⚠ 메모를 단 뒤에 찾기를 다시 하는 경로(실패 자리 재스윕 등)를 넣지 말 것 — 오염이 돌아온다.
        ⚠ 항목 순서는 등장 번호에 영향을 주지 않는다 — 찾기는 항목마다 문서 처음부터이고
          메모가 아직 하나도 없으므로 좌표계는 apply/locate와 같다.
        """
        items = [c for c in corrections if isinstance(c, dict)]
        total = len(items)

        def report(frac):
            if progress_cb is not None and total:
                progress_cb(min(max(frac, 0.0), 1.0) * total, total)

        def stopped():
            return stop_event is not None and stop_event.is_set()

        # ── ① 자리 후보 ─────────────────────────────────────────────
        plans = [{"spots": [], "skipped": [], "error": ""} for _ in items]
        need_scan = []
        for k, it in enumerate(items):
            fixed = it.get("spots")
            if fixed:
                plans[k]["spots"] = [{"i": j, "pos": list(p[:4]), "page": None}
                                     for j, p in enumerate(fixed)]
            else:
                need_scan.append(k)

        def cap_of(it):
            cap = (int(it.get("doc_occ") or 0) or int(it.get("occ_total") or 0)
                   or self._MEMO_FALLBACK_OCC)
            return min(cap, self._MEMO_MAX_OCC)

        # ★치환이 **새로 만든** 글자 — '교정본+메모'에서 원문 앵커(검수 필요 카드)가 다른 교정의
        #   결과 속에서 새로 생길 수 있다('설치 대학'→'설치대학'이 '설치대학'을 하나 더 만든다).
        #   그 카드의 등장 수(doc_occ)는 **치환 전** 문서 기준이라, 생긴 자리를 등장으로 세면
        #   진짜 뒤쪽 등장에 닿지 못한다(실측 hwpx 원고: 검수 카드 2건이 그렇게 '표시 못 함').
        #   치환 좌표가 있는 자리 중 교정문에는 앵커가 있고 원문에는 없는 곳 = 생긴 자리.
        created = {}            # (list, para) → [(시작, 끝, 교정문, 원문), …]
        for k, it in enumerate(items):
            if it.get("spots") and it.get("red_only"):
                for p in it["spots"]:
                    created.setdefault((p[0], p[1]), []).append(
                        (p[2], p[2] + p[3], it.get("original") or "", it.get("anchor_of") or ""))

        def made_here(pos, anchor):
            s0, e0 = pos[2], pos[2] + pos[3]
            return any(not (e0 <= a or s0 >= b2) and anchor in corr and anchor not in orig
                       for a, b2, corr, orig in created.get((pos[0], pos[1]), ()))

        def extra_for(anchor):
            return sum(1 for group in created.values() for _a, _b, corr, orig in group
                       if anchor in corr and anchor not in orig)

        scanned = {}
        for b in range(0, len(need_scan), self._MEMO_SCAN_BATCH):
            if stopped():
                return {"memo": 0, "blocked": 0, "fail": 0}, []
            ks = need_scan[b:b + self._MEMO_SCAN_BATCH]
            req = []
            for k in ks:
                it = items[k]
                cap = cap_of(it)
                req.append({
                    "original":  it.get("original", ""),
                    "need":      cap,
                    # ★빨간 매치는 **전부** 받는다 — 남의 자리를 걸러 낸 뒤에 cap을 채운다(②).
                    #   원문 앵커는 치환이 새로 만든 자리만큼 더 받는다(위 `created`).
                    "limit":     (self._MEMO_MAX_OCC if it.get("red_only")
                                  else min(cap + extra_for(it.get("original") or ""),
                                           self._MEMO_MAX_OCC)),
                    "red_only":  bool(it.get("red_only")),
                    "pen_paras": it.get("pen_paras") or [],
                    # 교정본은 바뀐 곳만 칠했다 — 브리지가 원문과 견줘 칠한 글자를 찾는다.
                    "anchor_of": it.get("anchor_of") or "",
                })
            done0 = b

            def scan_cb(pct, _t, _done=done0, _n=len(ks)):
                frac = (_done + _n * min(max(pct, 0), 100) / 100.0) / max(1, len(need_scan))
                report(frac * self._MEMO_SCAN_SHARE)

            result = self._send_cmd({"cmd": "memo_scan", "items": req},
                                    progress_cb=scan_cb, total=total)
            if not result.get("ok"):
                raise RuntimeError(f"메모 자리 찾기 실패: {result.get('error')}")
            for k, r in zip(ks, result.get("scan") or []):
                scanned[k] = r
        report(self._MEMO_SCAN_SHARE)

        # ── ② 등장 번호 · 부분 거절 · 자리 다툼 ────────────────────────
        #   claimed: 이미 주인이 정해진 구간 — (list, para) → [(시작, 끝), …]
        claimed = {}

        def claim(pos):
            claimed.setdefault((pos[0], pos[1]), []).append((pos[2], pos[2] + pos[3]))

        def taken(pos):
            s, e = pos[2], pos[2] + pos[3]
            return any(not (e <= a or s >= b2)
                       for a, b2 in claimed.get((pos[0], pos[1]), ()))

        for pl in plans:
            for sp in pl["spots"]:
                claim(sp["pos"])
        # 빨간 찾기 항목 — **긴 앵커부터** 자리를 차지한다. 짧은 앵커가 긴 교정문 속에서
        #   잡힌 빨강은 그 긴 교정의 자리다('취업 유지' ⊂ '취업 유지 후 이탈').
        red_ks = sorted((k for k in need_scan if items[k].get("red_only")),
                        key=lambda k: -len(items[k].get("original") or ""))
        for k in red_ks:
            r = scanned.get(k) or {}
            plans[k]["error"] = r.get("error", "")
            cap = cap_of(items[k])
            for m in r.get("matches") or []:
                if len(plans[k]["spots"]) >= cap:
                    break
                pos = [m[0], m[1], m[2], m[3]]
                if taken(pos):
                    continue
                claim(pos)
                plans[k]["spots"].append({"i": len(plans[k]["spots"]), "pos": pos,
                                          "page": m[4]})
        # 원문 앵커 항목(메모 모드·검수 필요 카드) — 찾기 순번이 곧 등장 번호다.
        for k in need_scan:
            if items[k].get("red_only"):
                continue
            r = scanned.get(k) or {}
            plans[k]["error"] = r.get("error", "")
            skip = set(items[k].get("skip_occurrences") or [])
            anchor = items[k].get("original") or ""
            real = [m for m in (r.get("matches") or [])
                    if not made_here(m[:4], anchor)]        # 치환이 만든 자리는 등장이 아니다
            for i, m in enumerate(real[:cap_of(items[k])]):
                if i in skip:          # 부분 거절 — 메모도 달지 않는다
                    plans[k]["skipped"].append({"i": i, "page": m[4], "ok": False})
                else:
                    plans[k]["spots"].append({"i": i, "pos": [m[0], m[1], m[2], m[3]],
                                              "page": m[4]})

        # ── ③ 문서 순 정렬 키 ────────────────────────────────────────
        flat = [(k, sp) for k, pl in enumerate(plans) for sp in pl["spots"]]
        subs = [sp for _k, sp in flat if sp["pos"][0] != 0]
        for b in range(0, len(subs), self._MEMO_KEYS_BATCH):
            chunk = subs[b:b + self._MEMO_KEYS_BATCH]
            result = self._send_cmd({"cmd": "memo_keys",
                                     "points": [sp["pos"][:3] for sp in chunk]})
            if not result.get("ok"):
                raise RuntimeError(f"메모 자리 정렬 실패: {result.get('error')}")
            for sp, key in zip(chunk, result.get("keys") or []):
                sp["body"] = key
        _FAR = 10 ** 9          # 본문까지 못 빠져나온 스토리 — 맨 먼저 처리(머리말 등)

        def order(entry):
            pos = entry[1]["pos"]
            if pos[0] == 0:
                return (pos[1], pos[2], 0, pos[1], pos[2])
            body = entry[1].get("body") or [_FAR, 0]
            return (body[0], body[1], pos[0], pos[1], pos[2])

        # ── ④ 문서 뒤쪽부터 단다 ─────────────────────────────────────
        flat.sort(key=order, reverse=True)
        b = 0
        reopens = 0
        while b < len(flat):
            if stopped():
                break
            chunk = flat[b:b + self._MEMO_PLACE_BATCH]
            done0 = b

            def place_cb(pct, _t, _done=done0, _n=len(chunk)):
                frac = (_done + _n * min(max(pct, 0), 100) / 100.0) / max(1, len(flat))
                report(self._MEMO_SCAN_SHARE + frac * (1 - self._MEMO_SCAN_SHARE))

            result = self._send_cmd({
                "cmd": "memo_place",
                "spots": [{"pos": sp["pos"], "anchor": items[k].get("original", ""),
                           "memo_text": items[k].get("memo_text", ""),
                           "color": items[k].get("color", 255)} for k, sp in chunk],
                "mark_anchor": bool(mark_anchor),
            }, progress_cb=place_cb, total=total)
            if not result.get("ok"):
                raise RuntimeError(f"메모 달기 실패: {result.get('error')}")
            placed = result.get("placed") or []
            for (_k, sp), r in zip(chunk, placed):
                sp["result"] = r
            b += max(1, len(placed)) if placed else len(chunk)
            if result.get("poisoned"):
                # ★세션 오염 — 문서를 다시 열고 나머지를 잇는다. 뒤쪽부터 달고 있으므로 남은
                #   자리(전부 앞쪽)의 좌표는 그대로다. 오염된 그 자리는 실패로 남긴다.
                #   ⚠ 횟수를 묶는다 — 자리 자체가 메모를 죽이는 원고(메모 본문 속 자리 등)에서
                #     열고 닫기만 되풀이하지 않게. 다 쓰면 남은 자리도 그대로 시도한다.
                if reopens >= self._MEMO_MAX_REOPENS:
                    continue
                reopens += 1
                if self.logger:
                    self.logger(f"  [메모] 한/글 세션 오류 — 문서를 다시 열고 이어서 답니다 "
                                f"({reopens}회)")
                self._reopen_document()
        report(1.0)

        # ── ⑤ 결과 — apply와 같은 스키마(정오표 조립 공유) ─────────────
        stats_total = {"memo": 0, "blocked": 0, "fail": 0}
        detail_total = []
        for it, pl in zip(items, plans):
            occ_pages = list(pl["skipped"])
            inserted = blocked = 0
            err_msg = pl["error"]
            for sp in pl["spots"]:
                r = sp.get("result")
                if r is None:          # 취소로 못 간 자리
                    continue
                page = r.get("page") if r.get("page") is not None else sp.get("page")
                if r.get("ok"):
                    inserted += 1
                    occ_pages.append({"i": sp["i"], "page": page, "ok": True})
                else:
                    blocked += 1
                    occ_pages.append({"i": sp["i"], "page": page, "ok": False,
                                      "blocked": True, "note": r.get("note", "")})
                    err_msg = err_msg or r.get("note", "")
            occ_pages.sort(key=lambda e: e.get("i", 0))
            if inserted:
                stats_total["memo"] += 1
            elif blocked:
                stats_total["blocked"] += 1
            else:
                stats_total["fail"] += 1
            detail_total.append({
                # ★'교정본+메모'는 앵커가 교정문이라 `original`이 교정문이다. 정오표는
                #   **원문 기준**으로 맞물리므로 원래 문자열을 돌려준다(anchor_of).
                "original":  it.get("anchor_of") or it.get("original", ""),
                "corrected": it.get("corrected", ""),
                "reason":    it.get("reason", ""),
                "source":    it.get("source", "dict"),
                "color":     it.get("color", 255),
                "applied":   inserted > 0,
                "replaced":  inserted,
                "occ_pages": occ_pages,
                "memoed":    inserted,
                "blocked":   blocked,
                "error":     err_msg or ("" if inserted else
                                         "메모를 달 수 있는 등장을 찾지 못했습니다"),
                "path":      "InsertFieldMemo",
            })
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

        # 메모 복구용 중간 저장본 정리 — 다시 여는 **사이의** close에서는 지우지 않는다.
        if self._stage_file and not self._reopening:
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
