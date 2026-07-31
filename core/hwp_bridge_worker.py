"""
core/hwp_bridge_worker.py — 32비트 HWP COM 브리지 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
이 스크립트는 32비트 Python으로 직접 실행되어
64비트 메인 앱과 JSON stdin/stdout으로 통신합니다.

프로토콜:
  메인앱 → stdin:  {"cmd": "...", ...params...}  (한 줄 JSON)
  워커 → stdout:   {"ok": true, ...data...}       (한 줄 JSON)
                    {"ok": false, "error": "..."}

지원 명령:
  open      — HWP 파일 열기
  get_text  — 전체 텍스트 추출
  apply     — 교정 적용 (corrections 리스트)
  save_as   — 다른이름으로 저장
  close     — HWP 종료
  quit      — 워커 종료
"""

import ctypes
import json
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
from ctypes import wintypes

# 시각적으로 안 보이거나 정상 텍스트 흐름을 방해하는 코드포인트 일괄.
# 한글 자모 채움 문자(U+115F, U+1160, U+3164, U+FFA0)도 포함 —
# AI 응답이 자모 분리된 한글을 NFC해도 채움 문자가 남는 케이스 방지.
_INVISIBLE_RE = re.compile(
    "["
    "­"            # Soft Hyphen
    "͏"            # Combining Grapheme Joiner
    "؜"            # Arabic Letter Mark
    "ᅟᅠ"      # Hangul Choseong/Jungseong Filler
    "឴឵"      # Khmer invisible vowels
    "᠎"            # Mongolian Vowel Separator
    "​-‏"     # ZWSP, ZWNJ, ZWJ, LRM, RLM
    "‪-‮"     # Directional formatting
    "⁠-⁯"     # Word Joiner, Function Application, etc.
    "ㅤ"            # Hangul Filler
    "︀-️"     # Variation Selectors
    "﻿"            # BOM / ZWNBSP
    "ﾠ"            # Halfwidth Hangul Filler
    "]"
)


def _clean(s: str) -> str:
    """NFC + 불가시 문자 제거 + 양 끝 공백 제거. 빈 문자열은 그대로."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = _INVISIBLE_RE.sub("", s)
    return s.strip()


def _ws_strip(s: str) -> str:
    """공백을 전부 없앤 비교용 표현(각주 라인 ↔ 문단 텍스트 포함 관계 판정에 쓴다).

    같은 글자라도 추출 경로에 따라 공백·개행이 다르게 붙으므로(스캔은 경계 개행을
    넣고 saveblock은 넣지 않는다) 공백을 무시해야 포함 관계가 성립한다.
    """
    if not s:
        return ""
    return re.sub(r"\s+", "", s)

# 부모(64비트)와 JSON을 주고받기 전에 stdio를 UTF-8로 고정.
# 한국 Windows에서 32비트 Python의 기본 인코딩은 CP949라
# 한국어가 포함된 JSON을 그대로 쓰면 부모 쪽 utf-8 디코더가 깨진다.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
# 프로토콜 채널 격리 — ⚠ 아래 import보다 **먼저** 실행돼야 한다
# ══════════════════════════════════════════════════════════════
# 부모와의 JSON 라인 프로토콜은 stdout 전용인데, 라이브러리가 stdout에 찍는 일이
# 실제로 있다: 배포본 첫 실행에서 win32com이 타입 라이브러리 캐시를 만들며
# "Rebuilding cache of generated files for COM support..." 등을 출력한다
# (gencache는 **import 시점에** __init__()을 돌리므로 import 뒤에 Rebuild를
#  monkeypatch하는 식의 방어는 무력하다 — 실측으로 확인).
#
# → 진짜 stdout(fd 1)을 복제해 프로토콜 전용으로 숨겨 두고, fd 1 자체는 stderr로
#   돌린다. 이제 누가 무엇을 print하든 로그 채널(stderr)로 갈 뿐 JSON 스트림은
#   구조적으로 오염되지 않는다. 부모가 잡소리 줄을 관용적으로 무시하고는 있지만,
#   프로토콜이 관용에 기대는 것보다 채널이 깨끗한 편이 낫다.
try:
    _PROTO_OUT = os.fdopen(os.dup(1), "w", encoding="utf-8",
                           errors="replace", newline="\n")
    os.dup2(2, 1)                 # fd1(stdout) → stderr : C 레벨 출력까지 포함
    sys.stdout = sys.stderr       # 파이썬 레벨 print()
except Exception:
    _PROTO_OUT = sys.stdout       # 격리 실패 시 기존 동작으로 폴백

import pythoncom
import win32com.client as win32


def _security_dll_path():
    """FilePathCheckerModule.dll 경로 — 있으면 보안 팝업을 완벽히 우회할 수 있다.

    ⚠ `RegisterModule`은 **경로가 아니라 등록된 COM ProgID**를 받으므로 이 함수는
      '그 모듈이 이 PC에 깔려 있는가'를 가늠하는 신호일 뿐이다(등록 자체는 regsvr32 몫).

    탐색 순서:
      1. EXE 옆 — 배포본. build_dist.py가 32비트 파이썬의 pyhwpx에서 복사해 동봉한다.
      2. pyhwpx 설치본 — 개발 PC.
    """
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(base, "FilePathCheckerModule.dll")
    if os.path.exists(cand):
        return cand
    try:
        import pyhwpx
        p = os.path.join(os.path.dirname(pyhwpx.__file__), "FilePathCheckerModule.dll")
        return p if os.path.exists(p) else None
    except Exception:
        return None


# 확장자 → HWP COM Open()의 Format 인자
_HWP_FORMAT_BY_EXT = {
    ".hwp":   "HWP",
    ".hwpx":  "HWPX",
    ".hml":   "HWPML2X",
    ".html":  "HTML",
    ".htm":   "HTML",
    ".txt":   "TEXT",
    ".rtf":   "RTF",
    ".docx":  "DOCX",
}

# 확장자별 선호 HWP 버전 ProgID 우선순위.
# 같은 PC에 여러 한글 버전이 설치된 경우, 마지막 등록 버전이 기본 ProgID로 잡히지만
# 버전별 ProgID(.X)는 별도로 호출 가능.
#
# Hancom의 버전별 ProgID 명명 규칙: HWPFrame.HwpObject.<MajorVersion>
#   HWP 2010 = 8.x  → HWPFrame.HwpObject.8
#   HWP 2014 = 9.x  → HWPFrame.HwpObject.9
#   HWP 2018 = 10.x → HWPFrame.HwpObject.10
#   HWP 2020 = 11.x → HWPFrame.HwpObject.11
#   HWP 2022 = 12.x → HWPFrame.HwpObject.12
#
# 미등록 버전은 Dispatch 시 throw → 다음 후보 시도.
# 모두 실패하면 generic ProgID로 폴백.
_HWP_PROGID_BY_EXT = {
    ".hwpx": [
        "HWPFrame.HwpObject.12",   # HWP 2022 — hwpx 지원 가장 안정적
        "HWPFrame.HwpObject.11",   # HWP 2020
        "HWPFrame.HwpObject.10",   # HWP 2018
        "HWPFrame.HwpObject.9",    # HWP 2014
        "HWPFrame.HwpObject",      # 기본 (마지막 등록 버전)
    ],
    ".hwp": [
        "HWPFrame.HwpObject.8",    # HWP 2010 — 가장 안정적인 .hwp 처리
        "HWPFrame.HwpObject",      # 기본
    ],
}


# ─────────────────────────────────────────────────────────────
# HWP 창 깜빡임 억제 — 새로 뜨는 hwp.exe 창을 그려지는 즉시 SW_HIDE.
#   COM의 Visible 속성을 Open 전에 만지면 Open이 모달에서 블록됨(실측 2026-06-17).
#   그래서 COM을 건드리지 않고 순수 Win32 ShowWindow로만 숨긴다.
#   대상은 'Dispatch 직전 스냅샷에 없던 새 hwp.exe PID'로 한정 →
#   사용자가 이미 열어둔 한/글 문서 창은 절대 건드리지 않는다.
# ─────────────────────────────────────────────────────────────
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_SW_HIDE = 0
try:
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
except Exception:
    pass
_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _list_hwp_pids():
    """현재 실행 중인 hwp.exe PID 집합 (Toolhelp 스냅샷)."""
    TH32CS_SNAPPROCESS = 0x2
    class _PE(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260),
        ]
    pids = set()
    try:
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        pe = _PE(); pe.dwSize = ctypes.sizeof(_PE)
        if _kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == b"hwp.exe":
                    pids.add(pe.th32ProcessID)
                if not _kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        _kernel32.CloseHandle(snap)
    except Exception:
        pass
    return pids


def _hide_windows_of(pids):
    """주어진 PID들의 '보이는' 최상위 창을 SW_HIDE."""
    if not pids:
        return

    def _cb(hwnd, _lparam):
        try:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and _user32.IsWindowVisible(hwnd):
                _user32.ShowWindow(hwnd, _SW_HIDE)
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    except Exception:
        pass


class _WindowHider:
    """별도 스레드로, Dispatch 이후 새로 생기는 hwp.exe 창을 떠오르는 즉시 숨긴다.
    COM을 호출하지 않으므로(순수 Win32) Open 동작을 방해하지 않는다."""

    def __init__(self, pre_pids, max_seconds=30.0):
        self._pre = set(pre_pids)
        self._max = max_seconds
        self._stop = threading.Event()
        self._th = None

    def start(self):
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        # 깜빡임 최소화: 창 숨김은 ~1ms마다 촘촘히, 무거운 PID 스냅샷(Toolhelp)은
        # 새 PID를 찾기 전엔 10ms마다, 찾은 뒤엔 0.5s마다만 갱신해 CPU 낭비를 막는다.
        t0 = time.time()
        target = set()
        last_snap = 0.0
        while not self._stop.is_set() and (time.time() - t0) < self._max:
            try:
                now = time.time()
                if (now - last_snap) >= (0.5 if target else 0.01):
                    target = _list_hwp_pids() - self._pre
                    last_snap = now
                if target:
                    _hide_windows_of(target)
            except Exception:
                pass
            time.sleep(0.001)

    def stop(self):
        self._stop.set()
        if self._th is not None:
            try:
                self._th.join(timeout=1)
            except Exception:
                pass


class HwpBridge:
    def __init__(self):
        self.hwp = None
        # hwpx → 임시 hwp 변환 시 사용 (close에서 삭제)
        self._temp_file = None
        # 창 깜빡임 억제 워처
        self._hider = None

    def _dispatch_hwp(self, file_path):
        """확장자에 맞는 HWP 버전을 우선 Dispatch.

        우선순위:
          1. 환경변수 KS_HWP_PROGID 가 지정되면 그것만 사용 (사용자 강제)
          2. 확장자별 선호 ProgID 목록을 순서대로 시도
          3. 모두 실패 시 generic "HWPFrame.HwpObject"

        반환: COM 인스턴스
        """
        # 1. 환경변수로 강제 지정
        forced = os.environ.get("KS_HWP_PROGID", "").strip()
        if forced:
            try:
                hwp = win32.Dispatch(forced)
                _send_log(f"[HWP] Dispatch 강제 지정 성공: {forced}")
                return hwp
            except Exception as exc:
                _send_log(f"[HWP] 강제 ProgID '{forced}' 실패: {exc} — 자동 선택으로 폴백")

        # 2. 확장자별 우선순위 시도
        ext = os.path.splitext(file_path)[1].lower()
        candidates = _HWP_PROGID_BY_EXT.get(ext, ["HWPFrame.HwpObject"])

        last_exc = None
        for progid in candidates:
            try:
                hwp = win32.Dispatch(progid)
                _send_log(f"[HWP] Dispatch 성공: {progid} (확장자 {ext})")
                return hwp
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(
            f"한/글 프로그램을 찾을 수 없습니다. 마지막 오류: {last_exc}"
        )

    def open(self, file_path, visible=False):
        pythoncom.CoInitialize()
        # 창 깜빡임 억제: Dispatch 직전 hwp.exe 스냅샷을 찍고, 이후 새로 뜨는
        # 창을 워처 스레드가 즉시 SW_HIDE. (visible=True 면 숨기지 않음.)
        if not visible:
            try:
                self._hider = _WindowHider(_list_hwp_pids())
                self._hider.start()
            except Exception:
                self._hider = None
        # 확장자별 선호 ProgID 시도 → HWP 2022가 .hwpx, HWP 2010이 .hwp 처리
        self.hwp = self._dispatch_hwp(file_path)

        # #1: HWP 버전 진단 — 어느 버전이 실제로 열렸는지 stderr로 알림.
        try:
            version = self.hwp.Version
            _send_log(f"[HWP] 버전: {version}")
        except Exception:
            try:
                _send_log("[HWP] 버전 속성 미지원 — 버전 확인 불가")
            except Exception:
                pass

        # ── 백그라운드 동작 강화 ─────────────────
        # 모든 메시지 박스를 자동 처리 (보안 경고/저장 확인 등).
        # ⚠ 0x20000 은 HWP 2010이 hwpx를 열 때 이후 RepeatFind를 0건으로 망가뜨림
        #   (apply 단계에서 0x2FFF1로 바꿔도 open 시점 손상이 남음 — 실측 확인 2026-06-17).
        #   apply()와 동일한 0x2FFF1 을 사용해야 hwp/hwpx 모두 찾기/치환이 정상 동작.
        try:
            self.hwp.SetMessageBoxMode(0x2FFF1)
        except Exception:
            pass

        # 보안 모듈 등록 (보안 경고 팝업 우회) — I5: 결과를 stderr 진단으로 남김
        sec_status = "기본"
        try:
            # 1. HWP 기본 보안 모듈 시도
            self.hwp.RegisterModule("FilePathCheckDLL", "SecurityModule")
            sec_status = "SecurityModule"
        except Exception as exc:
            _send_log(f"[보안] SecurityModule 등록 실패: {exc}")

        try:
            # 2. FilePathCheckerModule DLL이 있으면 활용 (팝업 완벽 우회 가능)
            if _security_dll_path():
                self.hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
                sec_status = "FilePathCheckerModule"
        except Exception as exc:
            _send_log(f"[보안] FilePathCheckerModule 등록 실패: {exc}")
        _send_log(f"[보안] 등록된 보안 모듈: {sec_status}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"파일 없음: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        fmt = _HWP_FORMAT_BY_EXT.get(ext, "")

        # HWP 버전에 따라 Open의 매개변수 개수 요구사항이 달라 (-2147352562 에러 발생)
        # 안전한 Fallback 적용
        # S6: 어느 단계에서 성공했는지 진단 정보를 stderr로 남김
        ok = False
        open_mode = ""
        try:
            ok = self.hwp.Open(file_path, fmt, "forceopen:true")
            open_mode = "forceopen"
        except Exception:
            try:
                ok = self.hwp.Open(file_path, fmt, "")
                open_mode = "empty-arg"
            except Exception:
                try:
                    ok = self.hwp.Open(file_path, fmt)
                    open_mode = "no-arg"
                except Exception:
                    ok = self.hwp.Open(file_path)
                    open_mode = "fmt-omitted"

        if ok is False:
            raise RuntimeError(f"HWP에서 파일을 열지 못했습니다: {file_path}")

        _send_progress(0, f"파일 열림 (mode={open_mode}, fmt={fmt or 'auto'})")

        # ── hwpx 처리: 임시 hwp 변환 없이 그대로 연다 (기본) ──────────────
        # 과거엔 구버전 HWP의 hwpx Find 호환성 때문에 임시 .hwp로 변환했으나,
        # 실측(2026-06-17) 결과 HWP 2010도 hwpx를 직접 열어 찾기/치환/색상/저장이
        # 모두 정상 동작한다(앞의 SetMessageBoxMode(0x2FFF1) 수정이 전제).
        # 오히려 임시 변환 경로가 RepeatFind 0건을 유발했으므로 기본 비활성.
        # 비상시에만 KS_HWPX_FORCE_CONVERT=1 로 옛 변환 경로를 강제할 수 있다.
        if ext == ".hwpx" and os.environ.get("KS_HWPX_FORCE_CONVERT") == "1":
            try:
                version_str = str(self.hwp.Version)
                # "8, 5, 8, 1677" 같은 콤마 구분 형식 처리
                major_str = version_str.replace(",", " ").split()[0]
                major = int(major_str)
            except Exception:
                major = 99   # 알 수 없으면 변환 생략
            if major < 9:
                temp_hwp = file_path + ".__cvt__.hwp"
                try:
                    _send_log(f"[변환] HWP {major}.x + hwpx → 임시 hwp로 변환 (Find 호환성)")
                    # 현재 문서를 hwp로 저장
                    self.hwp.SaveAs(temp_hwp, "HWP", "")
                    # 현재 문서 닫고 hwp로 재오픈
                    try:
                        self.hwp.HAction.Run("FileClose")
                    except Exception:
                        pass
                    ok2 = False
                    for args in (
                        (temp_hwp, "HWP", "forceopen:true"),
                        (temp_hwp, "HWP", ""),
                        (temp_hwp, "HWP"),
                        (temp_hwp,),
                    ):
                        try:
                            ok2 = self.hwp.Open(*args)
                            if ok2 is not False:
                                break
                        except Exception:
                            continue
                    if ok2 is not False:
                        self._temp_file = temp_hwp
                        _send_log("[변환] 임시 hwp 재오픈 성공 — Find 가능 상태")
                    else:
                        _send_log("[변환] 임시 hwp 재오픈 실패 — 원본 hwpx 그대로 진행")
                        # temp 파일이 남아있으면 정리
                        try:
                            if os.path.exists(temp_hwp):
                                os.remove(temp_hwp)
                        except Exception:
                            pass
                except Exception as exc:
                    _send_log(f"[변환] 시도 중 오류: {exc} — 원본으로 진행")

        # 모든 한글 윈도우 숨김 (다중 윈도우 모두 처리)
        try:
            wins = self.hwp.XHwpWindows
            for i in range(wins.Count):
                try:
                    wins.Item(i).Visible = visible
                except Exception:
                    pass
        except Exception:
            pass

        # 창이 떠서 숨겨진 시점까지 워처가 일했으니 이제 중지 (이후 재출현 없음).
        if self._hider is not None:
            self._hider.stop()
            self._hider = None
        return {"opened": True}

    def get_text(self):
        # InitScan(option, range, spara, epara, spos, epos)
        # option: 0xff(표/글상자 등 모든 텍스트 포함)
        # range: 0x77(문서 전체: 0x0070(문서시작) | 0x0007(문서끝))
        # HWP 버전에 따라 요구되는 파라미터 개수가 다르므로 철저한 Fallback 적용
        try:
            self.hwp.InitScan(0xff, 0x77, 0, 0, 0, 0)
        except Exception:
            try:
                self.hwp.InitScan(0xff, 0x77)
            except Exception:
                try:
                    self.hwp.InitScan(0xff)
                except Exception:
                    self.hwp.InitScan()
        # ⚠ 컨트롤(각주·글상자 등) 경계 구분(2026-07-03) — GetText의 state 4(컨트롤 진입)/
        #   5(탈출) 전이에서 그냥 buf += text 하면 **각주 텍스트가 본문 문장 한가운데에
        #   이어붙는다**('…성장 스타트업이다'+[각주]' 인터뷰를 수행한…'). AI가 그 접합부를
        #   넘는 교정을 만들고, 문서에는 그런 연속 문자열이 없어 적용이 '원문 없음'으로
        #   실패했다(실측 30.hwp). state 4/5를 만나면 다음 텍스트 앞에 개행을 넣어 경계를
        #   보존한다. 같은 컨트롤 안에서 이어지는 텍스트(state 4가 텍스트를 실어 온 뒤의
        #   state 2, 예: 표지 '수시연구 24-0'+'0')는 그대로 이어붙는다(실측 — 글자 훼손 0).
        buf = ""
        pending_break = False
        while True:
            state, text = self.hwp.GetText()
            if state in [0, 1]:
                break
            if state in (4, 5):
                pending_break = True
            if text:
                if pending_break and buf and not buf.endswith(("\r", "\n")):
                    buf += "\r\n"
                pending_break = False
                buf += text
        self.hwp.ReleaseScan()
        text = buf.replace('\r', '\n').strip()
        # ★ 강제 줄 나눔(Shift+Enter) 복원 — **반드시 아래 라인 분류보다 앞**에서.
        #   note_lines/footnote_lines는 '라인 인덱스'라 개행을 나중에 넣으면 전부 어긋난다.
        text = self._restore_linebreaks(text)
        # 문서 총 페이지 수 — 교정 현장 단위(하루 80~100쪽)라 결과 보고서의
        #   대표 수치로 쓴다. 버전에 따라 없을 수 있어 실패 시 None(표시 생략).
        page_count = None
        try:
            page_count = int(self.hwp.PageCount)
        except Exception:
            pass
        note_lines = self._classify_note_lines(text)
        return {"text": text,
                "note_lines": note_lines,
                "footnote_lines": self._classify_footnote_lines(text, note_lines),
                # 복원 건수는 **화면 로그로 올린다** — 이 값이 0이 아니면 추출 텍스트가
                #   달라지고 그에 따라 교정 결과도 달라진다. 이유가 보이지 않으면
                #   사용자는 원인 모를 변동으로 받아들인다(브리지 stderr는 배관으로 숨겨진다).
                "linebreaks": getattr(self, "_lb_restored", 0),
                "page_count": page_count}

    # ── 강제 줄 나눔(Shift+Enter) 복원 ───────────────────────────────────────
    #   ⚠ **한/글의 텍스트 계열 추출은 강제 줄 나눔을 통째로 버린다**(실측 2026-07-31,
    #   32비트 COM 직접 확인): `GetText`는 해당 문단을 **한 청크(state=2)** 로 주고 제어문자는
    #   문단 끝 `\r\n`뿐이며, `GetTextFile("TEXT","")`(문서 전체)도, `SelectText`+saveblock
    #   (문단 블록)도 똑같이 붙여서 준다. 즉 state 4/5 경계 처리로는 원리적으로 못 잡는다.
    #   그 결과 문서에서 줄이 나뉘어 있던 어절이 **붙어서** 추출된다:
    #     '임 덕 선 부연구위원'⏎'최 혜 지 전문연구원'⏎'이 인 수 연구원'⏎'윤 선 우 변호사'
    #       → '…부연구위원최 혜 지 전문연구원이 인 수 연구원윤 선 우 변호사'
    #   피해는 셋이다 — ① 없는 낱말('연구원윤'·'감면단')이 검수 카드로 뜨고, ② AI가 붙은
    #   어절을 보고 오교정을 만들며, ③ 원문이 줄나눔을 넘으면 찾기/치환이 그 경계를 못 넘어
    #   **적용이 실패**한다(과거 '본문에 원문 없음' 부류와 같은 뿌리).
    #
    #   ★유일하게 정보가 남는 곳이 **구조 export**다: `GetTextFile("HWPML2X","")`의
    #   `<LINEBREAK/>`. 실측 이 문서 163개(그중 어절을 실제로 붙이는 것 27개, 나머지는
    #   이미 공백·부호가 있어 무해). 한/글 2010(8.5.8.1677)에서 동작 확인.
    #
    #   ⚠ **불변식: 개행을 '삽입'만 한다. 글자는 하나도 바꾸지 않는다.**(junction_pass와 같은
    #   철학) 삽입 후 공백 제거 문자열이 원본과 다르면 **전체를 포기하고 원문을 쓴다** —
    #   추출 텍스트가 조금이라도 훼손되면 적용 인덱스가 통째로 어긋나기 때문이다.
    #   ⚠ 전역 정렬은 쓰지 않는다. 스캔 텍스트는 컨트롤 경계에서 개행을 끼워 넣고 각주를
    #   본문 사이에 실어 오므로 HWPML2X의 문자열과 **전역으로는 일치하지 않는다**(실측:
    #   단순 이어붙이기로 27개 중 6개가 표 안에서 불일치). 그래서 접합부 **국소 문맥**만
    #   공백 무시로 대조한다.
    _XML_ENT = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))
    _LB_CTX = (6, 10, 14, 18)      # 접합 문맥 길이 후보 — 짧은 것부터, 유일해질 때까지
    # ⚠ 반드시 **이스케이프 표기**로 둘 것 — 사용자 정의 영역 문자를 소스에 직접 박으면
    #   편집기·도구를 거치며 조용히 사라져 표시자가 빈 문자열이 된다(실제로 겪음).
    _LB_MARK = "\uE000"      # 사용자 정의 영역 — 본문에 나올 수 없는 표시자

    def _restore_linebreaks(self, text):
        """강제 줄 나눔 자리에 개행을 삽입한 텍스트를 돌려준다(실패 시 원문 그대로).

        `KS_HWP_NO_LINEBREAK_FIX=1`이면 통째로 끈다 — 현장에서 이 복원이 의심될 때
        되돌려 볼 수 있는 탈출구이자, 개발 중 before/after 측정 스위치다.
        """
        self._lb_restored = 0
        if not text or os.environ.get("KS_HWP_NO_LINEBREAK_FIX"):
            return text
        # ⚠ 이 호출은 **무출력으로 수 초 블록**한다(실측 6.1s / 본문 156K자·이미지 94개.
        #   반환 문자열 16.5MB 중 본문 `<CHAR>`는 2.8%뿐이고 나머지는 문서에 박힌 이미지
        #   바이너리다 — 즉 시간의 대부분은 한/글이 이미지를 직렬화하는 데 쓰인다).
        #   브리지 유휴 타임아웃이 120초라 20배 여유가 있으나, 이미지가 극단적으로 많은
        #   문서에선 여기서 멈춘 것처럼 보일 수 있다. 그때 어디서 멈췄는지 알 수 있도록
        #   **호출 전에** 로그를 남긴다(무음 구간을 만들지 않는다는 이 저장소의 원칙).
        _send_log("[줄나눔] 강제 줄 나눔 확인을 위해 문서 구조(HWPML2X) 읽는 중…")
        try:
            xml = self.hwp.GetTextFile("HWPML2X", "")
        except Exception as exc:
            _send_log(f"[줄나눔] HWPML2X 획득 실패: {exc} — 강제 줄 나눔 복원 건너뜀")
            return text
        if not xml or "<LINEBREAK/>" not in xml:
            return text

        # <CHAR> 안의 글자만 이어 붙이되 <LINEBREAK/> 자리에 표시자를 남긴다.
        flat = []
        for chunk in re.findall(r"<CHAR[^>]*>([\s\S]*?)</CHAR>", xml):
            chunk = chunk.replace("<LINEBREAK/>", self._LB_MARK)
            chunk = re.sub(r"<[^>]+>", "", chunk)      # 남은 태그 제거
            flat.append(chunk)
        flat = "".join(flat)
        for ent, ch in self._XML_ENT:
            flat = flat.replace(ent, ch)
        flat = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), flat)
        flat = re.sub(r"&#x([0-9A-Fa-f]+);", lambda m: chr(int(m.group(1), 16)), flat)

        # 접합부 수집 — **줄 나눔은 전부 복원한다.**
        #   ⚠ 처음엔 '앞뒤가 모두 낱말 글자일 때만'(어절이 붙는 경우만) 복원하고 나머지는
        #   무해하다고 봤는데 **틀렸다**(사용자 보고 2026-07-31). 실측 163개 중 어절 접합은
        #   27개뿐이고, **부호 접합 71개**(제목 '…조사 연구'⏎'- 전자문서…', 연구진
        #   '…변호사(엘케이파트너스)'⏎'박 상 진 교수…', 표 셀 나열 ','⏎'목표 지향적…')와
        #   **한쪽 공백 65개**가 남아 문서에서 나뉜 줄이 그대로 한 줄로 붙어 있었다.
        #   '가짜 낱말이 생기느냐'와 '줄 구조가 보존되느냐'는 다른 문제다 — 후자는
        #   미리보기 표시와 AI가 보는 문장 경계에 그대로 영향을 준다.
        marks = []            # [(tail, head), …] — 표시자 하나당 한 항목
        for m in re.finditer(re.escape(self._LB_MARK), flat):
            i = m.start()
            tail = flat[max(0, i - 40):i].replace(self._LB_MARK, "")
            head = flat[i + 1:i + 41].replace(self._LB_MARK, "")
            if tail and head:
                marks.append((tail, head))

        if not marks:
            return text

        # 국소 대조 — 문맥을 '공백 무시'로 찾는다(스캔 텍스트엔 컨트롤 경계 개행이 끼어 있다).
        #   ⚠ **표시자 개수는 실제 대조에 쓰는 길이(ctx)로 묶어서 센다.** 40자 전체 문맥으로
        #   묶으면 같은 문장이 문서에 두 번 나올 때(표지 제목 + 속표지 제목) 앞뒤 40자가
        #   미세하게 달라 **별개 needle 2개(각 1개)** 로 갈리고, 본문 매치는 2곳이라
        #   '개수 불일치'로 둘 다 건너뛴다(실측: 제목이 복원되지 않은 원인).
        cuts, skipped = set(), 0
        pending = list(range(len(marks)))
        for ctx in self._LB_CTX:
            counts = {}
            for idx in pending:
                key = (marks[idx][0][-ctx:], marks[idx][1][:ctx])
                counts[key] = counts.get(key, 0) + 1
            resolved = set()
            for (t, h), n_mark in sorted(counts.items()):        # 정렬 = 결정성 보장
                pat = re.compile(r"\s*".join(re.escape(c) for c in t)
                                 + r"()" + r"\s*".join(re.escape(c) for c in h))
                hits = list(pat.finditer(text))
                # 유일하거나, 표시자 개수와 매치 수가 정확히 같을 때만 적용한다.
                if hits and (len(hits) == 1 or len(hits) == n_mark):
                    for mm in hits:
                        cut = mm.start(1)
                        # **이미 개행이 있을 때만** 건너뛴다. 공백은 건너뛸 이유가 아니다 —
                        #   문서에서 줄이 나뉘어 있으면 앞에 공백이 있든 없든 나뉜 줄이다
                        #   (공백까지 스킵 조건에 넣었더니 '한쪽 공백' 65곳이 통째로 빠졌다).
                        if cut > 0 and text[cut - 1] != "\n" and text[cut:cut + 1] != "\n":
                            cuts.add(cut)
                    resolved.add((t, h))
            if resolved:
                pending = [i for i in pending
                           if (marks[i][0][-ctx:], marks[i][1][:ctx]) not in resolved]
            if not pending:
                break
        skipped = len(pending)

        if not cuts:
            if skipped:
                _send_log(f"[줄나눔] 접합 {skipped}곳을 본문에서 특정하지 못해 건너뜀")
            return text

        out = list(text)
        for cut in sorted(cuts, reverse=True):       # 뒤에서부터 = 앞 인덱스 불변
            out.insert(cut, "\n")
        new = "".join(out)

        # ★불변식 — 개행 말고는 아무것도 달라지지 않았는가.
        if new.replace("\n", "") != text.replace("\n", ""):
            _send_log("[줄나눔] ⚠ 불변식 위반(글자 변경 감지) — 복원 전체를 취소하고 원문 사용")
            return text
        self._lb_restored = len(cuts)
        _send_log(f"[줄나눔] 강제 줄 나눔 {len(cuts)}곳 복원"
                  + (f" · 특정 실패 {skipped}곳" if skipped else ""))
        return new

    # ── 각주·글상자 라인 식별 ────────────────────────────────────────────────
    #   위 스캔(option 0xff)은 각주·글상자 텍스트를 **본문 문장 한가운데**에 실어 오고,
    #   경계 개행 때문에 한 문장이 여러 조각으로 쪼개진다. 실측(복지지갑 연구 .hwp):
    #     …이름 일치를 엄격히 검증하고, TLS 1.3      ← 본문
    #      전송 구간 암호화를 위한 최신 프로토콜로…    ← 각주
    #     을 적용해 전진 기밀성이 보장되는…           ← 본문(이어짐)
    #   AI는 '을 적용해…'를 주어 없는 문장으로 보고 'TLS 1.3'을 덧붙이는 **오탐**을 낸다
    #   (사용자 보고 2026-07-30). 어느 라인이 각주인지 알면 그 오탐을 결정론으로 막고
    #   미리보기에도 각주로 표시할 수 있다.
    #
    #   ⚠ **추출 텍스트 자체는 절대 재배열하지 않는다.** 본문만 뽑아(option 0x00) 그것을
    #   파이프라인 텍스트로 쓰면 ① 각주 분량(실측 본문의 25.7%)이 교정 대상에서 빠지고
    #   ② 본문·각주에 함께 나오는 낱말이 385개라 등장 인덱스가 브리지 RepeatFind 순서와
    #   어긋난다(부분 거절 오적용 — 이 저장소가 반복해서 데인 부류). 그래서 순서·오프셋은
    #   그대로 두고 **분류 정보만** 덧붙인다.
    #
    #   ⚠ state 4/5 depth 카운터로는 못 가른다 — 실측에서 진입 230회 : 탈출 266회로
    #   **불균형**이라 카운터가 어긋나고, '컨트롤 내용'에는 각주 말고 표지 글상자·머리말도
    #   섞인다. 그래서 본문 전용 스캔과 대조하는 방식을 쓴다.
    def _classify_note_lines(self, text):
        """본문 전용 스캔(option 0x00)과 대조해 '본문에 없는 라인'의 인덱스를 돌려준다.

        판정이 실패하면 빈 목록 — 호출부는 전부 본문으로 취급(현행 동작 유지).
        짧은 각주 라인이 우연히 본문에도 존재하면 '본문'으로 오분류되는데, 그 방향은
        마커·가드가 발동하지 않을 뿐이라 안전하다(억제 실패 = 현행 동작).
        """
        if not text:
            return []
        body = ""
        try:
            try:
                self.hwp.InitScan(0x00, 0x77, 0, 0, 0, 0)
            except Exception:
                self.hwp.InitScan(0x00, 0x77)
            while True:
                state, t = self.hwp.GetText()
                if state in (0, 1):
                    break
                if t:
                    body += t
            self.hwp.ReleaseScan()
        except Exception:
            try:
                self.hwp.ReleaseScan()
            except Exception:
                pass
            return []
        body = body.replace('\r', '\n')
        if not body.strip():
            return []
        out = []
        for i, ln in enumerate(text.split('\n')):
            s = ln.strip()
            if s and s not in body:
                out.append(i)
        return out

    # ── 각주 라인만 골라내기 ──────────────────────────────────────────────────
    #   ⚠ 위 `note_lines`는 이름과 달리 **각주가 아니라 '본문 전용 스캔에 없는 모든 줄'**
    #   이다 — 각주·미주는 물론 글상자·표·목차·머리말·꼬리말이 전부 섞인다(실측 이 파일
    #   1,719줄 / 각주는 3개뿐). 미리보기 `[각주]` 표지를 그걸로 그리면 조판부호가 붙은
    #   모든 항목에 표지가 달린다(사용자 보고 2026-07-30). 표지는 **실제 각주에만** 붙어야
    #   하므로 각주를 따로 식별한다.
    #
    #   ★식별 근거 = ctrl 인벤토리(실측 `CtrlID`: fn=각주, en=미주, gso=글상자, tbl=표,
    #   head/foot=머리말·꼬리말, pghd/atno/nwno=번호류). `fn`/`en` ctrl의
    #   `GetAnchorPos(0)`가 **본문 리스트(List=0)의 문단 번호**를 주므로, 그 문단만
    #   선택해 텍스트를 읽고(SelectText+GetTextFile("TEXT","saveblock") — 각주 본문이
    #   인라인으로 함께 나온다) 추출 라인이 그 안에 들어 있는지로 판정한다.
    #
    #   ⚠ **앵커의 `Pos`(문단 내 오프셋)로 자르면 안 된다**(실측 실패): HWP의 Pos는
    #   조판부호까지 세는 좌표라 추출 텍스트 오프셋과 어긋난다(3개 중 1개가 각주 번호
    #   뒤 8자를 넘겨 잡아 본문 라인을 못 찾았다). 그래서 오프셋 대신 **포함 관계**로
    #   판정한다 — 좌표계 불일치에 영향받지 않는다.
    #
    #   ⚠ `InitScan`의 문단 범위(spara/epara) 인자는 **무시된다**(실측: 어떤 인자 순서로
    #   넣어도 문서 전체 268,034자가 나온다). 리스트 id 브루트포스(`SetPos(lid,0,0)` +
    #   `InitScan(0x00,0x00)`)도 1글자만 돌려준다. 두 경로 모두 재시도 금지.
    _FN_MAX_PARAS = 400          # 각주가 이보다 많으면 COM 왕복 비용이 커져 중단(표지 생략)
    _FN_RUN_WINDOW = 40          # 한 문단의 각주 본문들이 흩어질 수 있는 최대 줄 간격

    def _classify_footnote_lines(self, text, note_lines):
        """`note_lines` 중 **실제 각주·미주 본문**인 라인 인덱스만 돌려준다.

        실패하면 빈 목록 — 표지가 안 붙을 뿐이라 안전한 실패다(과표시가 훨씬 나쁘다).
        """
        if not text or not note_lines:
            return []
        try:
            paras = self._footnote_anchor_paras()
        except Exception:
            return []
        if not paras or len(paras) > self._FN_MAX_PARAS:
            return []

        lines = text.split('\n')
        want = set(note_lines)
        # ⚠ 후보를 `note_lines`로만 잡으면 각주를 절반 이상 놓친다(실측 99개 중 21개만
        #   탐지). `_classify_note_lines`는 '본문 전용 스캔에 없는 줄'을 컨트롤로 보는데,
        #   **참고문헌 목록에 같은 인용이 그대로 실린 각주**는 본문에도 존재하므로 본문으로
        #   분류돼 버린다(실측: 각주 314줄 = 참고문헌 3080줄, 글자까지 동일). 그런 줄은
        #   추출 텍스트에 **두 번 이상** 나타난다는 특징이 있으므로 후보에 함께 넣는다.
        #   문단 텍스트 포함 + 직전 본문 라인 확인이라는 2중 검증이 뒤에 있으므로,
        #   후보를 넓혀도 오탐 방향으로는 열리지 않는다.
        dup = set()
        seen = {}
        for ln in lines:
            s = _ws_strip(ln)
            if len(s) < 6:
                continue
            seen[s] = seen.get(s, 0) + 1
            if seen[s] > 1:
                dup.add(s)
        cands = []               # (라인 idx, 정규화 텍스트, 직전 본문 라인 정규화 꼬리)
        prev_body = -1
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            s = _ws_strip(ln)
            if i in want or s in dup:
                prev_s = _ws_strip(lines[prev_body]) if prev_body >= 0 else ""
                # ⚠ 중복 인용으로 후보가 된 줄은 **참고문헌 목록 자체**일 수도 있다(실측
                #   오탐 4건: 각주 2개가 걸린 문단의 두 인용이 참고문헌에서도 나란히 있어
                #   '직전 줄 꼬리도 같은 문단에 있음' 확인을 통과했다). 참고문헌에서는
                #   직전 줄이 **또 다른 인용(=중복 후보)**이고, 진짜 각주 자리에서는 직전
                #   줄이 평범한 본문 산문이다 — 그 차이로 가른다.
                ok = prev_s and len(s) >= 6 and (i in want or prev_s not in dup)
                if ok:
                    cands.append((i, s, prev_s[-12:]))
            if i not in want:
                prev_body = i
        if not cands:
            return []

        # ★문단은 오름차순이고 각주 본문도 문서 순서대로 실려 오므로 **순서를 보존하는
        #   단조 배정**을 한다(문단 pk의 각주 라인 > 문단 pk-1의 각주 라인). 이것이
        #   참고문헌 오탐을 끊는 결정적 제약이다 — 참고문헌의 인용은 그 각주가 달린
        #   문단보다 **수천 줄 뒤**에 있어서, 앞선 문단이 이미 진짜 각주 라인을 집어간
        #   뒤에는 배정 대상이 되지 못한다(실측: 남아 있던 오탐 3건이 0건으로).
        #   한 문단에 각주가 여럿이면 그 본문들은 서로 붙어 있으므로 첫 매치로부터
        #   _FN_RUN_WINDOW 줄 안쪽만 같은 문단의 것으로 인정한다.
        out = set()
        last = -1
        try:
            self.hwp.HAction.Run("Cancel")
        except Exception:
            pass
        for p in paras:
            try:
                self.hwp.SelectText(p, 0, p, -1)
                ptext = _ws_strip(self.hwp.GetTextFile("TEXT", "saveblock"))
            except Exception:
                continue
            finally:
                try:
                    self.hwp.HAction.Run("Cancel")
                except Exception:
                    pass
            if len(ptext) < 10:
                continue
            picked = []
            for i, s, tail in cands:
                if i <= last or s not in ptext:
                    continue
                # 직전 본문 라인도 같은 문단이어야 한다 — 같은 문단에 각주와 글상자가
                #   함께 걸린 경우 글상자 텍스트가 오탐되는 것을 막는 2차 확인.
                if not tail or tail not in ptext:
                    continue
                if picked and i - picked[0] > self._FN_RUN_WINDOW:
                    break
                picked.append(i)
            if picked:
                out.update(picked)
                last = picked[-1]
        try:
            self.hwp.HAction.Run("Cancel")
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        return sorted(out)

    def _footnote_anchor_paras(self):
        """`fn`/`en` ctrl이 걸린 **본문 문단 번호** 목록(오름차순·중복 제거)."""
        paras = set()
        ctrl = self.hwp.HeadCtrl
        n = 0
        while ctrl is not None and n < 20000:
            n += 1
            try:
                if str(ctrl.CtrlID) in ("fn", "en"):
                    aps = ctrl.GetAnchorPos(0)
                    if aps.Item("List") == 0:
                        paras.add(int(aps.Item("Para")))
            except Exception:
                pass
            try:
                ctrl = ctrl.Next
            except Exception:
                break
        return sorted(paras)

    def verify(self, originals):
        """각 원문 문자열이 문서에서 '찾기'로 도달 가능한지 검증 (치환 없음 — 문서 무변경).

        분석 파이프라인 끝에서 호출된다 — 추출 텍스트에는 있는데 문서에는 연속으로
        존재하지 않는 원문(각주 앵커·책갈피·메모 등 **보이지 않는 제어문자가 원문 중간에
        낀 경우**)을 걸러 '적용 불가 카드'가 검수/적용 단계로 새는 것을 막는다.
        RepeatFind는 본문·표·글상자·각주까지 닿아(실측 2026-07-03) apply 1차 경로와
        도달 범위가 같다. 반환: {"found": {original: true/false}}.
        """
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        found = {}
        total = len(originals)
        for idx, original_user in enumerate(originals):
            original = _clean((original_user or "").replace("\n", "\r"))
            if not original:
                found[original_user] = False
                continue
            ok = False
            try:
                self.hwp.HAction.Run("MoveDocBegin")
                pset = self.hwp.HParameterSet.HFindReplace
                self.hwp.HAction.GetDefault("FindDlg", pset.HSet)
                self.hwp.HAction.Execute("FindDlg", pset.HSet)
                pset.FindString = original
                pset.IgnoreMessage = 1
                pset.Direction = 0
                for prop, val in (
                    ("WholeWordOnly", 0), ("CaseSensitive", 0), ("MatchCase", 0),
                    ("FindRegExp", 0), ("UseWildCards", 0), ("SeveralWords", 0),
                    ("AllWordForms", 0), ("HanjaFromHangul", 0), ("FindJaso", 0),
                    ("IgnoreFindString", 0), ("IgnoreReplaceString", 1),
                    ("AutoSpell", 0),
                ):
                    try:
                        setattr(pset, prop, val)
                    except Exception:
                        pass
                ok = bool(self.hwp.HAction.Execute("RepeatFind", pset.HSet))
            except Exception:
                ok = True   # 검증 자체가 실패하면 보수적으로 '있음' 처리(카드 유지)
            found[original_user] = ok
            if (idx + 1) % 25 == 0 or idx == total - 1:
                _send_progress(int((idx + 1) / max(1, total) * 100),
                               f"문서 대조 검증 중… {idx + 1}/{total}")
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        return {"found": found}

    def apply(self, corrections):
        """
        corrections: [{"original", "corrected", "reason", "source", "color"}, ...]

        방식: 각 항목마다 "Find" 액션으로 다음 매치를 찾고
        InsertText로 치환, GetSelectedPos/SelectText로 새 글자를 선택해 빨강 적용.
        GetDefault("AllReplace")로 검색 옵션의 안전한 기본값을 미리 세팅한다.
        """
        stats  = {"dict": 0, "ai_typo": 0, "ai_polish": 0, "fail": 0}
        detail = []

        # 메인 시도 전 커서 위치 초기화 (안전)
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass

        # 적용 순서 불변식: 원문이 긴 항목을 먼저 치환한다(부분문자열 오염 방지).
        #   RepeatFind/AllReplace는 원문을 '부분 문자열'로도 매칭한다(WholeWordOnly=0).
        #   짧은 어간(예: '퀴메세지')이 긴 조사 변형(예: '퀴메세지는')보다 먼저 적용되면
        #   어간이 본문의 일부를 먼저 치환('키메시지는')해, 뒤따르는 변형 항목이 0건
        #   매칭 → 거짓 '실패'가 된다. CorrectionMerger가 1차 정렬하지만 일관성 후처리가
        #   변형 항목을 목록 끝에 재정렬 없이 덧붙여 순서가 깨질 수 있으므로, 적용 직전
        #   다시 길이 내림차순으로 정렬해 불변식을 회복한다(안정 정렬 — 동순위 순서 보존).
        corrections = sorted(
            corrections, key=lambda c: len(c.get("original") or ""), reverse=True
        )

        total = len(corrections)
        for idx, item in enumerate(corrections):
            # I2: 원본 형태(\n) 보존 — 매칭 직전에만 \r로 변환하고
            #     detail로 돌려보낼 때는 원본 형태를 유지한다.
            original_user  = item["original"]
            corrected_user = item["corrected"]

            # 강한 정규화 — NFC + 불가시 문자 제거 + 공백 정리.
            # HWP 내부는 개행문자로 \r을 사용하므로 \n도 함께 변환.
            original = _clean((original_user or "").replace('\n', '\r'))
            corrected = _clean((corrected_user or "").replace('\n', '\r'))

            color     = item.get("color", 10485760)
            source    = item.get("source", "dict")
            reason    = item.get("reason", "")
            # 부분 거절 — 건너뛸 등장 인덱스(문서 등장 순, 0-based)
            skip_occ  = set(item.get("skip_occurrences") or [])

            if not original or not corrected or original == corrected:
                continue

            replaced = 0
            err_msg  = ""
            path_used = "실패"
            # corrected가 original을 부분문자열로 포함하면(괄호 추가류) 찾기-치환이 자기
            #   삽입분을 다시 잡아 무한 증식한다 → 1차 RepeatFind는 랩 감지로, 2차
            #   AllReplace는 폴백 금지로 차단한다. try 밖(폴백)에서도 참조하므로 여기서 정의.
            self_matching = original in corrected

            # ── 1차 경로: FindDlg + RepeatFind + InsertText + Color ──────
            #   HWP 2010~2022에서 검증된 패턴. 본문 텍스트는 안정적으로 잡힘.
            #
            #   ⚠ 다중 패스 재탐색 (2026-07-14, 부분 반영 실측 수정) — 한 세션에서 수십 개
            #   카드를 연달아 처리하면 앞선 카드들이 남긴 편집 상태(각주·표 등 서브스토리
            #   커서 잔류 등) 탓에 뒤 카드(짧은 원문=정렬 끝, 세션 후반)의 RepeatFind가
            #   문서 '중간'에서 조기 False를 반환해, 등장 앞부분만 치환되고 나머지가
            #   조용히 누락된다(사단법인 문서 4건: 단독 실행 시 전량 치환 = 도달 문제 아님).
            #   ★재현 확정(위험 관리 skip=[1,3] 실측): 부분 거절 skip의 SetPos(매치 끝으로
            #   커서 이동)가 이 조기 False를 카드 '안'에서도 유발한다(매치 6/11에서 정지) —
            #   상태 오염의 주범이 skip 경로이고, 무skip 카드의 실패는 앞 카드들의 오염이
            #   전염된 것. 대책 2중:
            #     (a) 카드 시작마다 CloseEx로 서브스토리 편집 상태를 탈출(상태 정규화),
            #     (b) 패스에서 1건이라도 치환됐으면 검색 컨텍스트를 재초기화해 재탐색 —
            #         진전(치환) 없는 패스에서 종료하므로 정상 카드는 검증 패스 1회만 추가.
            #   skip 카드의 재탐색 인덱스 정합(재스킵): 이전 패스에서 스킵 판정된 등장은
            #   원문 그대로 남아 재탐색에 다시 잡히는데, 문서 순서상 미방문 등장보다 항상
            #   앞이므로(순방향 순회) 패스 시작 시 skipped_seen개를 '소비만' 하고 지나가면
            #   전역 인덱스(count)가 유지된다 — 거절 등장 오적용 없음(아래 검증).
            #   단일 패스 유지 예외: self_matching(재탐색이 삽입분을 다시 잡아 증식 위험).
            try:
                count = 0
                max_iters = 100
                _MAX_PASSES = 8
                _first_pass_replaced = None   # 회수 로그용(패스 0 치환 수)
                skipped_seen = 0              # 지금까지 스킵 판정한 등장 수(재탐색 시 재스킵 대상)

                # ⚠ 자기 재매칭(무한 증식) 방지 — corrected가 original을 부분문자열로
                #   포함하면(괄호 추가 '센터)'→'(센터)', '…참고]'→'…참고])') RepeatFind가
                #   문서 끝에서 처음으로 되돌아가(wrap-around) 방금 삽입한 텍스트를 다시
                #   잡아 괄호를 max_iters까지 무한 증식시킨다('((((…' / '))))…' 오염).
                #   일반 교정은 corrected에 original이 없어 자연 종료되므로 영향 없다.
                #   self_matching일 때만 매 검색 전 커서를 기록해, RepeatFind가 '뒤로'
                #   점프하면(=wrap) 루프를 끊는다. (self_matching은 위에서 정의됨)

                # ── 진단 로깅(부분 거절 항목만) ──────────────────────────
                #   부분 거절(skip_occ)이 있는 항목은 등장 인덱스 정합성이 깨지면
                #   '수락한 등장은 안 바뀌고 거절한 등장이 바뀌는' 버그가 난다.
                #   각 RepeatFind 매치의 story(list)/위치와 skip/replace 결정을 남겨
                #   브리지의 찾기 순서가 검수 패널의 등장 순서와 일치하는지 검증한다.
                _dbg = bool(skip_occ)
                if _dbg:
                    _send_log(f"[적용진단] '{original}'→'{corrected}' "
                              f"skip_occurrences={sorted(skip_occ)} (총 등장 중 이 인덱스는 건너뜀)")

                for pass_no in range(_MAX_PASSES):
                    # 서브스토리(각주/머리말/표 캡션 등) 편집 상태 탈출 — 앞 카드가 남긴
                    #   커서 상태를 정규화한다. 본문이면 CloseEx가 실패/무동작이라 무해.
                    for _ in range(3):
                        try:
                            if not self.hwp.HAction.Run("CloseEx"):
                                break
                        except Exception:
                            break
                    self.hwp.HAction.Run("MoveDocBegin")
                    try:
                        self.hwp.SetMessageBoxMode(0x2FFF1)
                    except Exception:
                        pass

                    # FindDlg로 검색 컨텍스트 초기화 (IgnoreMessage=1이라 다이얼로그 안 뜸)
                    pset_dlg = self.hwp.HParameterSet.HFindReplace
                    self.hwp.HAction.GetDefault("FindDlg", pset_dlg.HSet)
                    self.hwp.HAction.Execute("FindDlg", pset_dlg.HSet)

                    # RepeatFind용 파라미터 — 같은 HFindReplace 객체에 핵심 필드 설정
                    pset_find = self.hwp.HParameterSet.HFindReplace
                    pset_find.FindString    = original
                    pset_find.IgnoreMessage = 1
                    pset_find.Direction     = 0   # 0=Forward

                    # 안전 옵션 (버전별로 일부가 없을 수 있으므로 개별 try/except)
                    for prop, val in (
                        ("WholeWordOnly",       0), ("CaseSensitive", 0), ("MatchCase", 0),
                        ("FindRegExp",          0), ("UseWildCards",  0), ("SeveralWords", 0),
                        ("AllWordForms",        0), ("HanjaFromHangul", 0), ("FindJaso", 0),
                        ("IgnoreFindString",    0), ("IgnoreReplaceString", 1),
                        ("AutoSpell",           0),
                    ):
                        try:
                            setattr(pset_find, prop, val)
                        except Exception:
                            pass

                    pass_replaced = 0
                    # 이번 패스에서 다시 만나게 될 '이전 패스 스킵' 등장 수 — 소비만 하고
                    #   지나간다(전역 인덱스 유지, 상단 재스킵 주석 참조).
                    reskip_left = skipped_seen

                    while count < max_iters:
                        # self_matching이면 검색 직전 커서를 기록(랩어라운드 감지용)
                        prev_cursor = None
                        if self_matching:
                            try:
                                prev_cursor = self.hwp.GetPos()
                            except Exception:
                                prev_cursor = None
                        if not self.hwp.HAction.Execute("RepeatFind", pset_find.HSet):
                            break
                        # 랩어라운드 감지 — 매치가 직전 커서보다 '뒤'(같은 story 내)면 문서 끝에서
                        #   되돌아와 방금 삽입분을 다시 잡은 것 → 즉시 종료(증식 차단).
                        if self_matching and isinstance(prev_cursor, tuple) and len(prev_cursor) >= 3:
                            try:
                                _r = self.hwp.GetSelectedPos()
                                _ms = (_r[1], _r[2], _r[3]) if isinstance(_r, tuple) and len(_r) >= 7 else None
                            except Exception:
                                _ms = None
                            if (_ms is not None and _ms[0] == prev_cursor[0]
                                    and _ms < (prev_cursor[0], prev_cursor[1], prev_cursor[2])):
                                break
                        # 재스킵 — 이전 패스에서 스킵 판정된 등장(원문 잔존)이 재탐색에
                        #   다시 잡힌 것. 문서 순서상 미방문 등장보다 앞이므로 앞에서부터
                        #   reskip_left개는 소비만 하고 지나간다(count 미증가 = 정합 유지).
                        if reskip_left > 0:
                            reskip_left -= 1
                            if _dbg:
                                _send_log("[적용진단]   (재탐색) 이전 스킵 등장 재통과")
                            try:
                                r = self.hwp.GetSelectedPos()
                                if isinstance(r, tuple) and len(r) >= 7:
                                    self.hwp.SetPos(r[4], r[5], r[6])
                            except Exception:
                                pass
                            continue
                        if _dbg:
                            try:
                                _gp = self.hwp.GetSelectedPos()
                                _gpos = (_gp[1], _gp[2], _gp[3]) if isinstance(_gp, tuple) and len(_gp) >= 7 else _gp
                            except Exception:
                                _gpos = None
                            _send_log(f"[적용진단]   매치#{count} (list,para,pos)={_gpos} "
                                      f"→ {'skip' if count in skip_occ else 'REPLACE'}")
                        # 부분 거절 — 이 등장은 치환하지 않고 다음 매치로 커서만 이동
                        if count in skip_occ:
                            try:
                                r = self.hwp.GetSelectedPos()
                                if isinstance(r, tuple) and len(r) >= 7:
                                    self.hwp.SetPos(r[4], r[5], r[6])
                            except Exception:
                                pass
                            skipped_seen += 1
                            count += 1
                            continue
                        try:
                            # 선택의 시작 좌표 저장 (빠른 색상 적용용)
                            sel_start = None
                            try:
                                r = self.hwp.GetSelectedPos()
                                if isinstance(r, tuple) and len(r) >= 7:
                                    sel_start = (r[1], r[2], r[3])
                                elif isinstance(r, tuple) and len(r) >= 6:
                                    sel_start = (r[0], r[1], r[2])
                            except Exception:
                                sel_start = None

                            # 선택된 원문을 새 텍스트로 치환
                            iset = self.hwp.HParameterSet.HInsertText
                            self.hwp.HAction.GetDefault("InsertText", iset.HSet)
                            iset.Text = corrected
                            self.hwp.HAction.Execute("InsertText", iset.HSet)

                            # 삽입 직후 커서(= 삽입 텍스트 '끝') 기록 — self_matching일 때
                            #   다음 검색을 이 뒤에서 재개해 삽입분 내부를 다시 잡지 않게 한다.
                            ins_end = None
                            try:
                                ins_end = self.hwp.GetPos()
                            except Exception:
                                ins_end = None

                            # 방금 삽입한 텍스트 선택
                            selected_ok = False
                            if sel_start is not None:
                                try:
                                    end_pos = self.hwp.GetPos()
                                    if isinstance(end_pos, tuple) and len(end_pos) >= 3:
                                        self.hwp.SelectText(
                                            sel_start[0], sel_start[1], sel_start[2],
                                            end_pos[0],   end_pos[1],   end_pos[2],
                                        )
                                        selected_ok = True
                                except Exception:
                                    selected_ok = False

                            if not selected_ok:
                                for _ in range(len(corrected)):
                                    try:
                                        self.hwp.HAction.Run("MoveSelLeft")
                                    except Exception:
                                        break

                            # 빨강 색상 적용
                            try:
                                cset = self.hwp.HParameterSet.HCharShape
                                self.hwp.HAction.GetDefault("CharShape", cset.HSet)
                                cset.TextColor = 255   # COLORREF 0x0000FF
                                self.hwp.HAction.Execute("CharShape", cset.HSet)
                            except Exception:
                                try:
                                    self.hwp.HAction.Run("CharShapeTextColorRed")
                                except Exception:
                                    pass

                            # 선택 해제
                            try:
                                self.hwp.HAction.Run("Cancel")
                            except Exception:
                                pass

                            # self_matching(괄호 추가류) — 커서를 삽입 텍스트 '뒤'로 옮겨
                            #   다음 RepeatFind가 방금 넣은 부분문자열을 앞으로 다시 잡아
                            #   무한 증식하는 것을 막는다(색상용 Cancel이 커서를 앞으로
                            #   되돌려 놓을 수 있으므로 명시적으로 재배치).
                            if self_matching and isinstance(ins_end, tuple) and len(ins_end) >= 3:
                                try:
                                    self.hwp.SetPos(ins_end[0], ins_end[1], ins_end[2])
                                except Exception:
                                    pass

                            replaced += 1
                            pass_replaced += 1
                        except Exception as e:
                            err_msg = f"교정/색상 적용 실패: {e}"
                            break

                        count += 1

                    if _first_pass_replaced is None:
                        _first_pass_replaced = pass_replaced
                    # 진전(치환) 없는 패스에서 종료. self_matching은 단일 패스 유지
                    #   (재탐색이 삽입분을 다시 잡아 증식 위험), 치환 오류 시 중단.
                    if self_matching or pass_replaced == 0 or err_msg:
                        break

                # 재탐색 회수 로그 — 조기 종료로 누락될 뻔한 등장이 몇 건 회수됐는지 남긴다.
                if (_first_pass_replaced is not None
                        and replaced > _first_pass_replaced):
                    _send_log(f"[적용] '{original}' 재탐색 패스가 조기 종료 누락 "
                              f"{replaced - _first_pass_replaced}건 회수 — 총 {replaced}건 치환")

                if replaced > 0:
                    path_used = "FindDlg+RepeatFind"

            except Exception as exc:
                err_msg = f"FindDlg+RepeatFind 실패: {exc}"

            # ── 2차 폴백: AllReplace ──────────────────────────────────
            #   1차가 0건 매칭일 때만. 텍스트 변경만 수행 (색상 없음).
            #   본문이 아닌 표/텍스트박스/머리말 등에 원문이 있는 경우 대비.
            #   단, 부분 거절(skip)이 지정된 경우엔 모든 등장을 바꿔버리면 안 되므로 폴백 금지.
            #   self_matching(corrected⊇original, 괄호 추가류)도 AllReplace가 삽입분을 다시
            #   잡아 증식할 수 있어 폴백 금지(검수 카드라 한 곳 누락은 허용, 오염은 불가).
            if replaced == 0 and not skip_occ and not self_matching:
                try:
                    self.hwp.HAction.Run("MoveDocBegin")
                    ar_pset = self.hwp.HParameterSet.HFindReplace
                    self.hwp.HAction.GetDefault("AllReplace", ar_pset.HSet)
                    ar_pset.FindString    = original
                    ar_pset.ReplaceString = corrected
                    ar_pset.IgnoreMessage = 1
                    ar_pset.Direction     = 0
                    for prop, val in (
                        ("WholeWordOnly", 0), ("CaseSensitive", 0), ("MatchCase", 0),
                        ("FindRegExp", 0),    ("UseWildCards", 0),  ("SeveralWords", 0),
                        ("AllWordForms", 0),  ("HanjaFromHangul", 0), ("FindJaso", 0),
                        ("IgnoreFindString", 0), ("IgnoreReplaceString", 0),
                        ("AutoSpell", 0),     ("ReplaceMode", 1),
                    ):
                        try:
                            setattr(ar_pset, prop, val)
                        except Exception:
                            pass
                    ar_result = self.hwp.HAction.Execute("AllReplace", ar_pset.HSet)
                    if ar_result:
                        replaced = ar_result if isinstance(ar_result, int) and ar_result > 0 else 1
                        path_used = "AllReplace (텍스트만, 색상 없음)"
                        err_msg = "1차 실패 → AllReplace 폴백 (색상 미적용)"
                except Exception as exc:
                    if not err_msg:
                        err_msg = f"AllReplace 폴백 실패: {exc}"

            # path_used는 위에서 적용 경로별로 이미 설정됨.
            if replaced > 0:
                source_key = source if source in stats else "dict"
                stats[source_key] = stats.get(source_key, 0) + 1
                detail.append({
                    "original":  original_user, "corrected": corrected_user,
                    "reason":    reason,   "source":    source,
                    "color":     color,    "applied":   True,
                    "error":     err_msg,
                    "path":      path_used,
                    "replaced":  replaced,   # 본문에서 실제 치환된 등장 수
                })
            else:
                stats["fail"] += 1
                detail.append({
                    "original":  original_user, "corrected": corrected_user,
                    "reason":    reason,   "source":    source,
                    "color":     color,    "applied":   False,
                    "error":     err_msg or ("1차/2차 모두 매칭 0건 — 원문 위치에 보이지 않는"
                                             " 조판 문자(각주·책갈피 등)가 있거나 앞선 교정이"
                                             " 이미 치환한 경우 (수동 확인 필요)"),
                    "path":      path_used,
                    "replaced":  0,
                })

            # 진행률 (stderr)
            if (idx + 1) % 5 == 0 or idx == total - 1:
                pct = int(((idx + 1) / total) * 100)
                _send_progress(pct, f"교정 적용 중… {idx+1}/{total}")

        return {"stats": stats, "detail": detail}

    def save_as(self, output_path):
        # HWP 버전별 SaveAs 시그니처 차이를 흡수.
        # HWP 2010 (8.5.x)은 `SaveAs(Path, Format, arg)` 3개 인자를 요구하며,
        # 1개만 주면 DISP_E_BADPARAMCOUNT (-2147352562)로 실패한다.
        # Open과 동일한 폴백 패턴 적용.
        ext = os.path.splitext(output_path)[1].lower()
        fmt = _HWP_FORMAT_BY_EXT.get(ext, "")

        last_exc = None
        for args, label in (
            ((output_path, fmt, ""), "3-args (HWP 2010 표준)"),
            ((output_path, fmt),     "2-args"),
            ((output_path,),         "1-arg"),
        ):
            try:
                self.hwp.SaveAs(*args)
                _send_log(f"[저장] SaveAs 성공 — {label}, fmt={fmt or 'auto'}")
                return {"saved": output_path}
            except Exception as exc:
                last_exc = exc
                _send_log(f"[저장] SaveAs 실패 — {label}: {exc}")
                continue

        raise RuntimeError(f"HWP SaveAs 모든 시그니처 시도 실패: {last_exc}")

    def close(self):
        # 워처가 아직 살아있으면(open 중 예외 등) 정리
        if self._hider is not None:
            try:
                self._hider.stop()
            except Exception:
                pass
            self._hider = None
        if self.hwp:
            try:
                self.hwp.Quit()
            except Exception:
                pass
            self.hwp = None
        # 임시 변환 파일 정리
        if self._temp_file:
            try:
                if os.path.exists(self._temp_file):
                    os.remove(self._temp_file)
            except Exception:
                pass
            self._temp_file = None
        return {"closed": True}


def _send(data):
    """프로토콜 채널(격리해 둔 원래 stdout)로 JSON 한 줄 전송.

    ⚠ `sys.stdout`을 쓰면 안 된다 — 파일 상단에서 stderr로 돌려놨다(채널 격리 참조).
    """
    _PROTO_OUT.write(json.dumps(data, ensure_ascii=False) + "\n")
    _PROTO_OUT.flush()


def _send_progress(pct, msg):
    """stderr로 진행률 전송 (메인앱이 읽음)"""
    sys.stderr.write(json.dumps({"progress": pct, "message": msg}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _send_log(msg):
    """stderr로 일반 로그 전송 — 진행률 JSON이 아니라 메인앱이 logger로 흘려보냄"""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def main():
    bridge = HwpBridge()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _send({"ok": False, "error": f"JSON parse error: {exc}"})
            continue

        cmd = req.get("cmd", "")

        try:
            if cmd == "open":
                result = bridge.open(
                    req["file_path"],
                    visible=req.get("visible", False),
                )
            elif cmd == "get_text":
                result = bridge.get_text()
            elif cmd == "apply":
                result = bridge.apply(req["corrections"])
            elif cmd == "verify":
                result = bridge.verify(req["originals"])
            elif cmd == "save_as":
                result = bridge.save_as(req["output_path"])
            elif cmd == "close":
                result = bridge.close()
            elif cmd == "quit":
                bridge.close()
                _send({"ok": True, "quit": True})
                break
            else:
                _send({"ok": False, "error": f"Unknown command: {cmd}"})
                continue

            _send({"ok": True, **result})

        except Exception as exc:
            _send({
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })

    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    main()
