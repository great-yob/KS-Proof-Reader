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
  verify    — 원문이 '찾기'로 도달 가능한지 검증 (치환 없음)
  locate    — 원문의 등장별 쪽 번호 수집 (치환 없음, 정오표용)
  save_as   — 다른이름으로 저장
  close     — HWP 종료
  quit      — 워커 종료
"""

import ctypes
import difflib
import html
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


# 영문 대문자 → 소문자(한/글 찾기는 영문 대소문자를 가리지 않는다) — `apply_worker._ASCII_FOLD`와 같은 표.
_ASCII_FOLD = str.maketrans({chr(c): chr(c + 32) for c in range(65, 91)})


def _clean(s: str) -> str:
    """NFC + 불가시 문자 제거 + 양 끝 공백 제거. 빈 문자열은 그대로."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = _INVISIBLE_RE.sub("", s)
    return s.strip()


def _red_spans(original: str, corrected: str) -> list:
    """교정본에서 칠할 구간 — 교정문 안의 **바뀐 곳만** [(시작, 끝), …](교정문 좌표·문서 순·겹침 병합).

    교정문 전체를 칠하면 교정본만 보는 사람은 무엇이 틀렸었는지 알 수 없다('기술 개발' 전체가
    빨가면 띄어쓰기를 고친 건지 글자를 고친 건지 모른다 — 사용자 요청 2026-10-22). 그래서:
      · 글자를 바꾸거나 넣은 곳 → 그 글자('리플렛'→'리플릿'은 '릿', 괄호 보충은 그 괄호)
      · 공백만 넣거나 뺀 곳 → **양옆 한 글자씩**(공백은 칠해도 안 보인다)
          '기술개발'→'기술 개발' = '술 개' · '소재 ('→'소재(' = '재(' · '),센'→'), 센' = ', 센'
      · 글자를 지운 곳 → 지운 자리 양옆 한 글자씩
    ⚠ 띄어쓰기만 다르면(공백 뺀 글자열이 같으면) 글자를 하나씩 짝지어 공백열을 견준다 —
      SequenceMatcher는 같은 글자가 되풀이되는 곳에서 엉뚱하게 정렬할 수 있다.
    ⚠ `hwp_memo`의 메모 자리 찾기(`memo_scan`)가 **같은 함수**로 빨간 글자 하나를 골라 판정한다
      — 칠하는 규칙을 바꾸면 그쪽도 함께 바뀐다(한 곳에만 둔 이유).
    """
    n = len(corrected)
    spans = []
    if re.sub(r"\s", "", original) == re.sub(r"\s", "", corrected):
        oi = [i for i, ch in enumerate(original) if not ch.isspace()]
        ci = [j for j, ch in enumerate(corrected) if not ch.isspace()]
        for k in range(1, len(ci)):
            if (original[oi[k - 1] + 1:oi[k]] != corrected[ci[k - 1] + 1:ci[k]]):
                spans.append((ci[k - 1], ci[k] + 1))
    else:
        sm = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
        for tag, _i1, _i2, a, b in sm.get_opcodes():
            if tag == "equal":
                continue
            seg = corrected[a:b]
            lead = not seg.strip() or seg[0].isspace()
            trail = not seg.strip() or seg[-1].isspace()
            if lead:
                while a > 0 and corrected[a - 1].isspace():
                    a -= 1
                a = max(0, a - 1)
            if trail:
                while b < n and corrected[b].isspace():
                    b += 1
                b = min(n, b + 1)
            if a < b:
                spans.append((a, b))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _edit_ops(src: str, dst: str) -> list:
    """src를 dst로 바꾸는 **최소 편집** [(i1, i2, j1, j2), …](문서 순) — src[i1:i2]를 dst[j1:j2]로.

    교정본이 원고의 서식을 지키는 근거다(`HwpBridge._apply_in_place`): 여기 들지 않은 글자는
    한/글에서 **한 번도 건드리지 않으므로** 글자모양도 글자 스타일 소속도 그대로 남는다.
    ⚠ 띄어쓰기만 다르면 `_red_spans`와 같이 글자를 하나씩 짝지어 **공백열만** 바꾼다 —
      SequenceMatcher는 되풀이 글자에서 엉뚱한 글자를 지웠다 넣어 그 글자의 서식을 다시 쓴다.
    """
    if re.sub(r"\s", "", src) == re.sub(r"\s", "", dst):
        oi = [i for i, ch in enumerate(src) if not ch.isspace()]
        ci = [j for j, ch in enumerate(dst) if not ch.isspace()]
        if not oi:
            return [(0, len(src), 0, len(dst))] if src != dst else []
        # 글자 사이 공백열 + 맨 앞·맨 뒤(정리된 문자열이라 보통 비어 있다)
        cuts = ([(0, oi[0], 0, ci[0])]
                + [(oi[k - 1] + 1, oi[k], ci[k - 1] + 1, ci[k]) for k in range(1, len(oi))]
                + [(oi[-1] + 1, len(src), ci[-1] + 1, len(dst))])
        return [(a1, a2, b1, b2) for a1, a2, b1, b2 in cuts if src[a1:a2] != dst[b1:b2]]
    sm = difflib.SequenceMatcher(None, src, dst, autojunk=False)
    return [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _ws_strip(s: str) -> str:
    """공백을 전부 없앤 비교용 표현(각주 라인 ↔ 문단 텍스트 포함 관계 판정에 쓴다).

    같은 글자라도 추출 경로에 따라 공백·개행이 다르게 붙으므로(스캔은 경계 개행을
    넣고 saveblock은 넣지 않는다) 공백을 무시해야 포함 관계가 성립한다.
    """
    if not s:
        return ""
    return re.sub(r"\s+", "", s)


def _file_stamp(path):
    """파일의 (수정 시각 ns, 크기, 파일 id). 없으면 None — '이번에 새로 써졌는가' 판정용."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def _written(path, before) -> bool:
    """저장 직전 스탬프(before) 이후 path가 **새로 써졌는가**(내용이 있는 파일로).

    ⚠ '파일이 존재한다'로 판정하면 안 된다 — 대상이 잠겨 저장이 실패해도 이전 실행의
      파일이 그대로 남아 있어 성공처럼 보인다(save_as·export_pdf 주석의 실측 사고).
    """
    now = _file_stamp(path)
    return now is not None and now[1] > 0 and now != before

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


def _ensure_security_module_registered():
    """FilePathCheckerModule을 한/글의 **per-user 모듈 목록**에 등록한다.

    ★한/글은 이 보안 모듈을 COM ProgID로 찾지 않는다. 자기 레지스트리 키
      `HKCU\Software\HNC\HwpAutomation\Modules\<이름>` = DLL 절대경로 를 본다.
      (실측 2026-08-21: 개발 PC엔 pyhwpx가 만든 이 값이 있었고 COM 등록은
       HKCR·WOW6432Node·HKCU\Software\Classes 어디에도 없었는데
       `RegisterModule`이 True를 돌려줬다. 배포 PC엔 이 값이 없다 — pyhwpx를
       깔 일이 없으므로. 그래서 거기서만 한/글이 접근 승인 모달을 띄운다.)

    ⚠ **HKCU라 관리자 권한이 필요 없다.** 설치 프로그램이
      `PrivilegesRequired=lowest`인 우리에게 열려 있는 유일한 경로다
      (regsvr32는 HKLM을 요구해 쓸 수 없다 — installer-setup-and-bridge32 참조).

    ⚠ 이미 유효한 등록이 있으면 **건드리지 않는다** — 사용자가 다른 경로(예: pyhwpx)로
      등록해 둔 것을 우리 번들 경로로 덮어쓰면, 앱을 지웠을 때 한/글이 사라진 DLL을
      가리키게 된다.

    반환: 최종적으로 등록돼 있는 DLL 경로, 없으면 None.
    """
    dll = _security_dll_path()
    if not dll:
        return None
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\HNC\HwpAutomation\Modules",
                                0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                cur, _ = winreg.QueryValueEx(key, "FilePathCheckerModule")
            except OSError:
                cur = None
            if cur and os.path.isfile(cur):
                _send_log(f"[보안] 모듈 이미 등록됨: {cur}")
                return cur
            winreg.SetValueEx(key, "FilePathCheckerModule", 0, winreg.REG_SZ, dll)
            _send_log(f"[보안] 모듈 신규 등록: {dll}")
            return dll
    except Exception as exc:
        _send_log(f"[보안] 모듈 등록 실패({type(exc).__name__}): {exc}")
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
# ⚠ 실측(2026-06-17)으로 이 목록의 전제는 대부분 무너졌다 — 남겨 둔 것은 동작에 해가
#   없어서다(미등록 ProgID는 Dispatch가 즉시 실패해 다음 후보로 넘어간다):
#   · 모든 한/글 버전이 **CLSID 하나를 공유**해 마지막에 등록된 버전만 COM으로 잡히고,
#     개발 PC에는 버전별 ProgID(.8/.12)가 아예 없다 → 실제로는 늘 기본 ProgID로 떨어진다.
#   · 'hwpx는 2022가 안정적'이라는 아래 주석은 틀렸다 — 한/글 2010도 hwpx를 COM으로
#     완전히 처리한다(원인은 SetMessageBoxMode 값이었다 — open()의 0x2FFF1 주석).
#   메모리 hwp-com-version-constraints · hwpx-2010-com-fix 참조.
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
_SW_SHOW = 5
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


def _hide_windows_of(pids, hidden=None):
    """주어진 PID들의 '보이는' 최상위 창을 SW_HIDE.

    hidden(set)을 주면 **우리가 실제로 숨긴 hwnd만** 기록한다 — 되살릴 때
    (_show_windows) 우리가 건드리지 않은 창까지 띄우지 않기 위함이다.
    """
    if not pids:
        return

    def _cb(hwnd, _lparam):
        try:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and _user32.IsWindowVisible(hwnd):
                _user32.ShowWindow(hwnd, _SW_HIDE)
                if hidden is not None:
                    hidden.add(hwnd)
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    except Exception:
        pass


def _show_windows(hwnds):
    """_hide_windows_of가 숨긴 창을 다시 띄운다.

    ⚠ 이 함수가 있는 이유(배포 첫날 실사고 2026-08-21): 한/글이 **모달 대화상자**를
      띄운 채 COM 호출이 블록되면, 그 창을 우리가 숨겨 둔 탓에 사용자는 보지도
      클릭하지도 못하는 **영구 대기**에 빠진다. 개발 PC는 등록·보안 승인·개인정보
      동의가 이미 정리돼 있어 절대 재현되지 않고, 새로 배포된 PC에서는 첫 실행이
      곧 첫 대화상자다. 지연 감시(_StallWatch)가 이 함수로 창을 되살려, 최소한
      사용자가 상황을 보고 처리할 수 있게 만든다.
    """
    for hwnd in list(hwnds or ()):
        try:
            _user32.ShowWindow(hwnd, _SW_SHOW)
        except Exception:
            pass
    for hwnd in list(hwnds or ()):
        try:
            _user32.SetForegroundWindow(hwnd)
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
        self._hidden = set()      # 우리가 숨긴 hwnd — unhide()로만 되돌린다

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
                    _hide_windows_of(target, self._hidden)
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

    def unhide(self):
        """숨김을 중단하고, 지금까지 숨긴 창을 모두 다시 띄운다.

        ⚠ stop()을 **먼저** 해야 한다 — 루프가 살아 있으면 1ms 안에 다시 숨긴다.
        """
        self.stop()
        _show_windows(self._hidden)
        self._hidden.clear()


class _StallWatch:
    """블로킹 COM 호출이 오래 걸리면 숨긴 한/글 창을 되살리고 **한 번** 경고한다.

    한/글은 최초 실행 등록·개인정보 동의·문서 복구·자동화 보안 승인 같은 모달을
    띄울 수 있고, 그동안 Dispatch/Open은 반환하지 않는다. 그 창이 숨겨져 있으면
    사용자에게는 그냥 '멈춘 앱'이다(→ _show_windows 주석).

    ⚠ 경고는 반드시 **1회**만 낸다. stderr 출력은 부모(_send_cmd)의 유휴 타임아웃을
      리셋하므로, 주기적으로 짖으면 120초 타임아웃이 영원히 오지 않는다.
    """

    def __init__(self, stage, hider, seconds=20.0):
        self._stage = stage
        self._hider = hider
        self._sec = seconds
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self):
        try:
            if self._hider is not None:
                self._hider.unhide()
        except Exception:
            pass
        try:
            _send_log(
                # ⚠ 화면 로그는 `—` 앞에서 잘린다(activity_panel) — 조치 문구를 앞에.
                f"[한/글 대기] 한/글 창을 확인해 주세요 — {self._stage} 단계가 "
                f"{self._sec:.0f}초째 무응답입니다. 한/글이 대화상자(사용자 등록·"
                f"문서 복구·접근 승인 등)를 띄운 것 같아 숨겨 둔 창을 다시 표시했습니다."
            )
        except Exception:
            pass

    def __enter__(self):
        try:
            self._timer.start()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            self._timer.cancel()
        except Exception:
            pass
        return False


class HwpBridge:
    def __init__(self):
        self.hwp = None
        # hwpx → 임시 hwp 변환 시 사용 (close에서 삭제)
        self._temp_file = None
        # 창 깜빡임 억제 워처
        self._hider = None
        # 우리가 만든 메모 서브스토리 id — **문서 단위**로 누적한다(memo() 주석 참조).
        #   None이면 아직 문서를 열지 않았다는 뜻.
        self._memo_stories = None
        # 문서에 정의된 글자 스타일의 글자모양 지문(apply가 명령마다 새로 받는다).
        #   비어 있으면 스타일 보호 없이 예전처럼 빨강 글자만 쓴다.
        self._style_fps = set()
        self._style_fp_quick = set()
        self._style_marked = 0
        # 문서에 정의된 글자 스타일의 번호(= `HStyle.Apply`) — 최소 편집이 소속을 되살릴 때 쓴다.
        self._char_style_ids = set()
        self._style_lost = 0
        self._pen_in_style = 0
        self._inplace_fallback = 0

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
                _send_log(f"[HWP] Dispatch 시도(강제): {forced}")
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
                # ⚠ 반드시 **호출 전에** 남긴다 — Dispatch가 블록되면(한/글 모달)
                #   성공 후 로그는 영원히 오지 않아, 부모에겐 그냥 '침묵'이다.
                _send_log(f"[HWP] Dispatch 시도: {progid}")
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
        # 새 문서 = 우리가 만든 메모가 하나도 없는 상태.
        self._memo_stories = set()
        # 창 깜빡임 억제: Dispatch 직전 hwp.exe 스냅샷을 찍고, 이후 새로 뜨는
        # 창을 워처 스레드가 즉시 SW_HIDE. (visible=True 면 숨기지 않음.)
        if not visible:
            try:
                self._hider = _WindowHider(_list_hwp_pids())
                self._hider.start()
            except Exception:
                self._hider = None
        else:
            self._hider = None
        # 확장자별 선호 ProgID 시도 → HWP 2022가 .hwpx, HWP 2010이 .hwp 처리
        #   지연 감시: 한/글이 최초 실행 대화상자를 띄우면 여기서 영원히 멈춘다.
        with _StallWatch("한/글 연결", self._hider):
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
        #
        # ⚠ **반환값을 봐야 한다.** ProgID가 등록돼 있지 않으면 RegisterModule은
        #   예외를 던지지 않고 그냥 False를 돌려준다. 예전 코드는 예외만 잡고
        #   sec_status를 무조건 덮어써서, 등록이 전혀 안 된 PC에서도
        #   `[보안] 등록된 보안 모듈: FilePathCheckerModule`이라고 **거짓 보고**했다
        #   (실측 2026-08-21: 개발 PC의 HKCR·WOW6432Node·HKCU\Software\Classes
        #   어디에도 FilePathCheckerModule ProgID가 없는데 그 로그가 찍혔다).
        #   원격 진단이 이 한 줄에 기대므로, '시도'와 '성공'을 절대 뭉뚱그리지 말 것.
        sec_status = "없음(기본 동작)"
        # ★RegisterModule보다 **먼저** — 한/글이 참조하는 per-user 키를 채워 둔다.
        _ensure_security_module_registered()
        for progid in ("SecurityModule", "FilePathCheckerModule"):
            if progid == "FilePathCheckerModule" and not _security_dll_path():
                _send_log("[보안] FilePathCheckerModule DLL이 옆에 없어 건너뜀")
                continue
            try:
                ok_reg = self.hwp.RegisterModule("FilePathCheckDLL", progid)
            except Exception as exc:
                _send_log(f"[보안] {progid} 등록 예외: {exc}")
                continue
            _send_log(f"[보안] {progid} RegisterModule 반환={ok_reg!r}")
            if ok_reg:
                sec_status = progid
        _send_log(f"[보안] 실제 등록된 보안 모듈: {sec_status}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"파일 없음: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        fmt = _HWP_FORMAT_BY_EXT.get(ext, "")

        # HWP 버전에 따라 Open의 매개변수 개수 요구사항이 달라 (-2147352562 에러 발생)
        # 안전한 Fallback 적용
        # S6: 어느 단계에서 성공했는지 진단 정보를 stderr로 남김
        ok = False
        open_mode = ""
        _send_log(f"[HWP] 파일 열기 시도: fmt={fmt or 'auto'}")
        # 지연 감시: 보안 모듈이 등록되지 않은 PC에서는 여기서 '문서 접근 허용'
        #   모달이 뜬다(SetMessageBoxMode로도 안 막히는 부류가 있다).
        with _StallWatch("파일 열기", self._hider):
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

    # ── 쪽 번호 취득 ──────────────────────────────────────────────────────────
    #   `KeyIndicator()`는 9-튜플 `(succ, seccnt, secno, prnpageno, colno, line, pos,
    #   over, ctrlname)`을 준다. 우리가 쓰는 건 **prnpageno(index 3)** 하나뿐이다.
    #
    #   ★왜 '절대 쪽'이 아니라 prnpageno인가 — 이게 **한/글 자신이 쓰는 좌표**다(실측
    #   2026-08-05, 한/글 2010 8.5.8.1677): `Goto`로 10을 주면 prnpageno가 정확히 10인
    #   자리로 간다. 즉 정오표에 적힌 숫자를 사용자가 Ctrl+G에 그대로 넣을 수 있다.
    #   반대로 '문서 처음부터 센 물리 쪽'은 어디에도 입력할 수 없는 숫자다.
    #   ⚠ 그 대가로 prnpageno는 **전역 단조도 유일도 아니다** — 구역마다 쪽 번호를 새로
    #   시작할 수 있어서다(실측: 사단법인 파일 PageCount 160인데 문서 끝 prnpageno 152,
    #   05.hwp는 문서 첫 쪽이 2). 그래서 이 값으로 정렬하거나 산술을 하면 안 된다.
    #   표시 전용이다.
    #
    #   ⚠ 비용은 무시해도 된다(실측 0.13ms). 비싼 건 `RepeatFind`(30~50ms/건)이므로,
    #   **이미 찾기를 하고 있는 자리에서만** 곁다리로 읽는다 — 쪽 번호를 위해 별도의
    #   찾기를 새로 도는 일은 최소화한다(locate의 예산 상한 참조).
    def _page_no(self):
        """현재 커서 위치의 한/글 쪽 번호(prnpageno). 실패하면 None."""
        try:
            ki = self.hwp.KeyIndicator()
            if isinstance(ki, tuple) and len(ki) > 3:
                return int(ki[3])
        except Exception:
            pass
        return None

    # 한 원문당 훑을 등장 상한 — apply의 전체 매치 상한과 같은 값(정합).
    #   ⚠ 예전 100은 apply와 함께 등장 100곳 넘는 원문의 뒤쪽 쪽 번호를 비우고, 그 수를
    #     doc_occ로 받는 메모·PDF까지 101번째 이후 자리를 못 보게 만들었다. 실제 비용은
    #     아래 전체 예산(budget·time_budget)이 묶는다.
    _LOCATE_MAX_OCC = 5000

    # apply 상한(apply 주석 참조) — 건너뛰지 않은 매치 / 전체 매치(안전판) / 생존 신호 간격.
    _APPLY_MAX_REPLACE = 1000
    _APPLY_MAX_MATCHES = 5000
    _APPLY_HEARTBEAT = 50

    def locate(self, originals, budget=4000, time_budget=60.0):
        """각 원문의 **등장별 쪽 번호**를 찾기만으로 수집한다(치환 없음 — 문서 무변경).

        정오표를 '등장 1곳 = 1행'으로 쓰기 위한 것.

        ⚠ 반드시 **apply보다 먼저**, 그리고 **한 번에** 돌 것. 치환이 시작되면 글자
        수가 바뀌어 뒤 페이지가 밀리므로(실측: 같은 낱말의 12번째 등장이 15쪽→16쪽),
        쪽 번호는 '치환 전 문서'라는 단일 장면에서 통째로 떠야 서로 정합한다. 거절
        교정의 원문이 겹치는 다른 교정에 먹혀 아예 사라질 수도 있다.

        ⚠ 등장 순서(=인덱스)는 RepeatFind 순서 = 문서 순서라 검수 패널의 등장 인덱스와
        같은 좌표계다(apply의 skip_occurrences가 기대는 것과 동일한 불변식).

        `budget`(찾기 횟수)과 `time_budget`(초)은 **둘 다 상한**이다. 찾기 1건이
        30~50ms라 상한이 없으면 등장 수천 개 원고에서 이 단계만 몇 분이 된다. 둘 중
        하나라도 넘으면 남은 원문은 그냥 건너뛴다(쪽 칸이 빈다 — 정오표는 그대로
        나오고 숫자만 없는, 안전한 실패다).
        """
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        pages, spent, truncated = {}, 0, False
        total = len(originals)
        t_start = time.time()

        def _over():
            return spent >= budget or (time.time() - t_start) >= time_budget

        for idx, original_user in enumerate(originals):
            if _over():
                truncated = True
                break
            original = _clean((original_user or "").replace("\n", "\r"))
            if not original:
                continue
            found = []
            try:
                # 앞 항목이 서브스토리(각주·표)에 커서를 남겼을 수 있다 — apply와 같은
                #   상태 정규화를 거쳐야 문서 처음부터 일관되게 훑는다.
                for _ in range(3):
                    try:
                        if not self.hwp.HAction.Run("CloseEx"):
                            break
                    except Exception:
                        break
                self.hwp.HAction.Run("MoveDocBegin")
                pset = self.hwp.HParameterSet.HFindReplace
                self.hwp.HAction.GetDefault("FindDlg", pset.HSet)
                self.hwp.HAction.Execute("FindDlg", pset.HSet)
                pset.FindString    = original
                pset.IgnoreMessage = 1
                pset.Direction     = 0
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
                # ⚠ RepeatFind는 **SetPos로 커서를 옮겨 주지 않아도** 다음 등장으로
                #   전진한다(실측: '연구' 35건을 SetPos 없이 전부 순회). 다만 어떤
                #   문서에서 커서가 정체하면 무한루프가 되므로 GetPos 정체를 감지해
                #   끊는다 — 상한(_LOCATE_MAX_OCC)만으로는 100회를 헛돌게 된다.
                prev_pos = None
                while len(found) < self._LOCATE_MAX_OCC and not _over():
                    if not self.hwp.HAction.Execute("RepeatFind", pset.HSet):
                        break
                    spent += 1
                    try:
                        cur = self.hwp.GetPos()
                    except Exception:
                        cur = None
                    if cur is not None and cur == prev_pos:
                        break
                    prev_pos = cur
                    found.append(self._page_no())
            except Exception:
                pass
            if found:
                pages[original_user] = found
            if (idx + 1) % 25 == 0 or idx == total - 1:
                _send_progress(int((idx + 1) / max(1, total) * 100),
                               f"쪽 번호 확인 중… {idx + 1}/{total}")
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        if truncated:
            _send_log(f"[쪽번호] 예산 소진(찾기 {spent}회 / {time.time() - t_start:.0f}초) "
                      "— 나머지 원문의 쪽 번호는 생략")
        return {"pages": pages, "truncated": truncated, "finds": spent}

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

    def apply(self, corrections, char_styles=None):
        """
        corrections: [{"original", "corrected", "reason", "source", "color"}, ...]
        char_styles: 문서에 정의된 **글자 스타일**의 글자모양 지문(`core/hwp_styles.py`).
                     없으면 예전과 똑같이 동작한다.

        방식: 각 항목마다 "Find" 액션으로 다음 매치를 찾고
        InsertText로 치환, GetSelectedPos/SelectText로 새 글자를 선택해 빨강 적용 — 빨강은
        교정문 안의 **바뀐 곳에만** 남긴다(`_red_spans` · 띄어쓰기는 양옆 한 글자씩).
        GetDefault("AllReplace")로 검색 옵션의 안전한 기본값을 미리 세팅한다.
        """
        self._style_fps = {tuple(s.get("fp") or []) for s in (char_styles or [])}
        self._style_fps.discard(())
        # 지문의 싼 앞거름 — (한글 글꼴, 크기, 굵기, 기울임). 위치는 `_charshape_fingerprint`의
        #   구성(글꼴 7 · 장평 7 · 자간 7 · 상대크기 7 · 글자위치 7 · 크기 · 속성 …)을 따른다.
        self._style_fp_quick = {(fp[0], fp[35], (fp[36] >> 1) & 1, fp[36] & 1)
                                for fp in self._style_fps if len(fp) > 36}
        self._char_style_ids = {int(i) for s in (char_styles or []) for i in (s.get("ids") or [])}
        self._style_marked = 0          # 이번 명령에서 형광펜으로 표시한 자리 수
        self._style_lost = 0            # 새 글자의 글자 스타일 소속을 되살리지 못한 자리 수
        self._pen_in_style = 0          # 글자 스타일 구간 안에 형광펜을 칠한 자리 수(.hwpx 경고용)
        self._inplace_fallback = 0      # 최소 편집을 못 하고 통째 치환한 자리 수
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
            # 등장별 실적용 기록 — 정오표가 '등장 1곳 = 1행'이라 **어느 등장이 실제로
            #   치환됐는지**를 알아야 한다. 매치마다 커서가 그 자리에 있으므로
            #   KeyIndicator 한 번(0.13ms)이면 쪽까지 공짜로 딸려 온다.
            #   [{"i": 등장 인덱스(0-based, 문서 순), "page": 쪽|None, "ok": 치환됨}, …]
            #   ⚠ `ok=False`는 **부분 거절로 건너뛴 등장**이다 — 실패가 아니다.
            #   ⚠ 여기 실린 `page`는 **치환이 진행되는 중의 쪽**이라 앞 항목들이 늘리거나
            #     줄인 만큼 밀려 있다. 정오표의 정본은 apply 전에 한 번에 뜬 locate 결과이고
            #     이 값은 폴백일 뿐이다(apply_worker._build_errata_rows 참조).
            #   ⚠ AllReplace 폴백 경로는 등장별 정보가 없어 이 목록이 빈 채로 남는다
            #     (호출부가 '등장별 근거 없음'으로 보고 항목 단위 결과로 폴백한다).
            occ_pages = []
            # 형광펜으로 표시한 자리의 **문단 좌표** [[list, para], …] — 아래 `spots_raw`가
            #   없는 경로(결과 화면의 '교정본에 메모 추가')에서 메모 단계가 쓰는 보조 단서다.
            pen_paras = []
            # ★치환한 자리의 **좌표 기록**(치환 순서대로) — '교정본+메모'의 메모 단계가 찾기
            #   없이 그 자리로 곧장 가는 근거다(`memo_place` 머리말). 한 치환 = 한 줄:
            #     [list, para, 시작, 원문 길이, 교정문 길이, 등장 번호]  — 좌표를 확인한 치환
            #     ["dirty", list, para] / ["dirty_list", list] / ["dirty_all"] — 확인 못 한 치환
            #   ⚠ 여기 적힌 시작 위치는 **그 순간의** 좌표다. 뒤이은 치환이 같은 문단 앞쪽을
            #     늘리거나 줄이면 밀리므로, 최종 좌표는 호출 측이 이 기록을 순서대로 되짚어
            #     계산한다(`HwpEditor._resolve_spots`). 브리지는 상태를 들고 있지 않는다.
            spots_raw = []
            # corrected가 original을 부분문자열로 포함하면(괄호 추가류) 찾기-치환이 자기
            #   삽입분을 다시 잡아 무한 증식한다 → 1차 RepeatFind는 랩 감지로, 2차
            #   AllReplace는 폴백 금지로 차단한다. try 밖(폴백)에서도 참조하므로 여기서 정의.
            #   ⚠ **영문 대소문자를 접어서** 판정한다 — 한/글 찾기는 대소문자를 가리지 않으므로
            #     'TRaining'→'Training'(가드 ⑭가 살리는 Shift 오타)도 자기 삽입분을 다시 잡는다.
            #     가려서 판정하던 때는 같은 자리를 상한(1000곳)까지 거듭 치환했다(실측 2026-10-22,
            #     실파일 영문 참고문헌 제목 'X Drives'→'X drives' 한 자리 = 1000곳). 교정본 계획
            #     (`ApplyWorker._BridgeReplay`)도 같은 판정을 쓴다.
            self_matching = original.translate(_ASCII_FOLD) in corrected.translate(_ASCII_FOLD)
            # ★바뀐 곳만 칠한다(_red_spans) — 교정문 전체가 아니라. 문단을 넘는 교정문은 좌표
            #   셈이 성립하지 않아 예전처럼 전체를 칠한다.
            red_spans = _red_spans(original, corrected) if "\r" not in corrected else []
            if red_spans == [(0, len(corrected))]:
                red_spans = []

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
                # ★상한은 **건너뛰지 않은** 매치로 센다(`count - skipped_seen`). 예전엔
                #   `max_iters = 100`이 건너뛴 매치까지 세어, 등장 172곳 중 171곳이 제외
                #   (excluded — 반복되는 긴 기관명 속 부분문자열)이고 진짜 등장이
                #   155번째인 교정이 **영원히 적용되지 못했다**(실측 2026-10-16, 실파일). 그 실패는
                #   S3가 '긴 교정에 포함(정상)'으로 재분류해 화면엔 '실패 0건'으로 나갔다.
                #   전체 매치 상한(_APPLY_MAX_MATCHES)은 폭주 방지용 안전판으로만 남긴다
                #   — 자기 재매칭 폭주는 아래 self_matching 가드가 따로 막는다.
                _MAX_PASSES = 8
                _first_pass_replaced = None   # 회수 로그용(패스 0 치환 수)
                skipped_seen = 0              # 지금까지 스킵 판정한 등장 수(재탐색 시 재스킵 대상)

                # ⚠ 자기 재매칭(무한 증식) 방지 — corrected가 original을 부분문자열로
                #   포함하면(괄호 추가 '센터)'→'(센터)', '…참고]'→'…참고])') RepeatFind가
                #   문서 끝에서 처음으로 되돌아가(wrap-around) 방금 삽입한 텍스트를 다시
                #   잡아 괄호를 매치 상한까지 무한 증식시킨다('((((…' / '))))…' 오염).
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

                    while ((count - skipped_seen) < self._APPLY_MAX_REPLACE
                           and count < self._APPLY_MAX_MATCHES):
                        # 등장이 많은 낱말 하나에서 출력이 끊기면 부모의 유휴 타임아웃
                        #   (_send_cmd, 120초)이 '한/글 행업'으로 오판해 브리지를 죽인다
                        #   (메모 루프가 실측으로 겪은 것과 같은 부류). 진행률은 그대로 두고
                        #   신호만 보낸다.
                        if count and count % self._APPLY_HEARTBEAT == 0:
                            _send_progress(int(idx / max(1, total) * 100),
                                           f"교정 적용 중… {idx + 1}/{total} ({count}곳)")
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
                        # 이 등장이 몇 쪽인지 — 치환 전, 커서가 매치 위에 있을 때 읽는다.
                        _pg = self._page_no()
                        # 부분 거절 — 이 등장은 치환하지 않고 다음 매치로 커서만 이동
                        if count in skip_occ:
                            occ_pages.append({"i": count, "page": _pg, "ok": False})
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
                            # ★치환 **전에** 이 자리의 글자모양을 파라미터셋째로 떠 둔다.
                            #   ① `InsertText`는 원문이 런 전체를 덮으면 새 글자에 **옆 런의**
                            #      글자모양을 물린다(실측: 'Figure 1.' KoPub돋움체 Bold →
                            #      Light). 색을 안 칠해도 서식이 바뀐다.
                            #   ② 되씌울 때 **필드를 하나씩 옮겨 담지 않고 파라미터셋을 그대로**
                            #      다시 Execute한다 — 글자 테두리/배경(BorderFill)처럼 값으로
                            #      복사하기 어려운 항목까지 통째로 따라오고, 결과가 원래와
                            #      같으면 한/글이 **원래 글자모양 id를 그대로 유지**한다
                            #      (= 글자 스타일 소속이 살아남는다).
                            #   ⚠ 그래서 여기서 뜬 뒤 치환이 끝날 때까지 `GetDefault("CharShape")`
                            #     를 **다시 부르면 안 된다** — 스냅샷이 덮인다.
                            #   ⚠⚠ 그리고 **같은 객체를 끝까지 들고 가야 한다**. 되씌울 때
                            #     `self.hwp.HParameterSet.HCharShape`를 다시 꺼내면 값이 빈
                            #     파라미터셋이 와서 Execute가 조용히 아무 일도 안 한다
                            #     (실측 2026-09-10: 되씌움이 통째로 무시돼 'Table 2.'가
                            #     옆 런 글자모양 cs#8을 그대로 뒤집어썼다 — 예외도 로그도 없다).
                            #   ⚠⚠⚠ **찾기 선택 위에서 읽으면 안 된다.** 그 상태의
                            #     `GetDefault("CharShape")`는 문서에 따라 글꼴 이름이 빈
                            #     섞인 값을 돌려준다(실측 2026-09-10 한전 문서: 문단 끝에
                            #     걸린 매치 3곳 중 2곳이 그랬다). **매치 첫 글자 하나만
                            #     골라** 읽는다 — 한 글자는 섞일 수 없다.
                            #     ⚠ 커서만 두고 읽지 말 것: 한/글 2010은 선택이 없으면 커서
                            #     **앞** 글자의 모양을 준다(위치 i → 글자 i−1, 문단 첫머리만
                            #     글자 0). 매치가 문단 중간의 **런 경계**에서 시작하면 앞 런의
                            #     모양을 떠서 교정문에 되씌우게 된다(실측 2026-10-23 05.hwp
                            #     참고문헌 이탤릭 제목: 기울임이 지워지고 스타일 판정도 빗나가
                            #     형광펜 대신 빨강). 예전 검증 표본은 모두 문단 첫머리라
                            #     드러나지 않았다. `_match_is_red`도 같은 이유로 골라 읽는다.
                            #     읽은 뒤에는 `MoveSelRight`로 선택을 마저 넓힌다
                            #     (`SelectText`는 이 환경에서 예외를 던져 쓸 수 없다).
                            shape_snap = None
                            style_locked = False
                            match_end = None
                            doc_text = None    # 문서에 실제로 있는 매치 글자(최소 편집 경로의 전제)
                            inplace_why = "매치 위치 셈 불가"   # 최소 편집을 못 한 사유(진단 로그)
                            try:
                                p = self.hwp.GetPos()      # 찾기 직후 커서 = 매치 끝
                            except Exception:
                                p = None
                            # 매치가 문단을 넘어가면(원문에 \r) 위치 셈이 성립하지 않는다 —
                            #   그때는 스냅샷을 포기하고 예전 경로로 간다(동작 불변).
                            if (isinstance(p, tuple) and len(p) >= 3 and "\r" not in original
                                    and p[2] - len(original) >= 0):
                                match_end = (p[0], p[1], p[2])
                                start_pos = p[2] - len(original)
                                moved = 0      # 이미 고른 글자 수 — 아래 선택 넓히기가 이어받는다
                                try:
                                    self.hwp.SetPos(p[0], p[1], start_pos)
                                    self.hwp.HAction.Run("MoveSelRight")
                                    moved = 1
                                    cset = self.hwp.HParameterSet.HCharShape
                                    self.hwp.HAction.GetDefault("CharShape", cset.HSet)
                                    fp = self._charshape_fingerprint(cset)
                                    if fp is not None:
                                        shape_snap = cset
                                        style_locked = fp in self._style_fps
                                except Exception:
                                    shape_snap = None
                                    style_locked = False
                                # 선택 되살리기 — 되살아났는지 **커서 위치로 확인**한다.
                                #   ⚠ 확인은 선택이 아니라 안전장치다: 선택이 없는 채로
                                #     InsertText를 하면 치환이 아니라 **삽입**이 되어 원고가
                                #     오염된다. 확인에 실패하면 같은 매치를 다시 찾아 세운다.
                                restored = False
                                try:
                                    for _ in range(len(original) - moved):
                                        self.hwp.HAction.Run("MoveSelRight")
                                    now = self.hwp.GetPos()
                                    restored = (isinstance(now, tuple) and len(now) >= 3
                                                and tuple(now[:3]) == match_end)
                                except Exception:
                                    restored = False
                                if not restored:
                                    shape_snap = None
                                    inplace_why = "선택 되살리기 재탐색"
                                    try:
                                        self.hwp.SetPos(p[0], p[1], start_pos)
                                        restored = bool(self.hwp.HAction.Execute(
                                            "RepeatFind", pset_find.HSet))
                                    except Exception:
                                        restored = False
                                    if not restored:
                                        err_msg = ("글자모양 확인 뒤 원문 선택을 되살리지 "
                                                   "못했습니다 — 이 항목은 적용하지 않습니다")
                                        break
                                elif "\r" not in corrected:
                                    # ★최소 편집 경로의 전제 — 선택한 매치 글자를 **문서에서** 읽는다.
                                    #   찾기는 영문 대소문자를 가리지 않아 원문과 다를 수 있다('training'
                                    #   을 'TRaining' 카드가 잡는다) — 교정문과 견줄 것은 문서 쪽이다.
                                    doc_text = self._block_text()
                                    if doc_text is None or len(doc_text) != len(original):
                                        inplace_why = (f"매치 글자 읽기 실패({doc_text!r} · "
                                                       f"{getattr(self, '_scan_note', None)!r})")
                                        doc_text = None
                                else:
                                    inplace_why = "문단을 넘는 교정문"

                            # ★최소 편집 경로 — 바뀐 글자만 고치고, 새 글자에 원래 글자의 글자모양·
                            #   글자 스타일을 되씌운다(`_apply_in_place` — 한 매치 안에 서식이 여럿이어도
                            #   각자 그대로 남는다). 위치를 확인하지 못한 매치(조판 부호를 걸침·문단을
                            #   넘는 교정문·선택 되살리기 재탐색)만 아래 예전 경로(통째 치환)로 간다.
                            in_place = None
                            if doc_text is not None:
                                in_place = self._apply_in_place(
                                    match_end[0], match_end[1], start_pos, doc_text, corrected,
                                    red_spans if doc_text == original
                                    else _red_spans(doc_text, corrected))
                                inplace_why = "원래 글자 모양 읽기 실패"
                            if in_place is not None:
                                marked_with_pen = in_place
                                ins_ok = True
                                ins_end = (match_end[0], match_end[1], start_pos + len(corrected))
                                if marked_with_pen:
                                    self._style_marked += 1
                                    pen_paras.append([match_end[0], match_end[1]])
                            else:
                                # ⚠ 이 경로는 매치 안의 서식이 첫 글자 모양 하나로 덮이고 글자 스타일
                                #   소속이 풀릴 수 있다 — 자리를 세어 호출부가 화면에 낸다.
                                self._inplace_fallback += 1
                                _send_log(f"[적용진단] 서식 보존 치환 불가({inplace_why}) — "
                                          f"통째 치환: '{original}'")
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
                                # 교정문이 [시작, 시작+길이)에 그대로 들어갔음이 확인됐나 — 부분 칠하기와
                                #   좌표 기록(spots_raw)이 둘 다 이 확인 위에서만 선다.
                                ins_ok = (match_end is not None and "\r" not in corrected
                                          and isinstance(ins_end, tuple) and len(ins_end) >= 3
                                          and tuple(ins_end[:3]) == (
                                              match_end[0], match_end[1],
                                              match_end[2] - len(original) + len(corrected)))
                                part = red_spans if ins_ok else []

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

                                # ★교정 표시(예전 경로 — 통째 치환이라 매치 안의 서식이 첫 글자
                                #   모양 하나로 덮이고, 런 전체를 덮으면 글자 스타일 태그도 지워진다.
                                #   위치를 확인하지 못한 매치만 여기로 온다) — 자리마다 두 갈래다.
                                #   · 보통 자리: 떠 둔 글자모양 + 빨강 글자색을 한 번에 되씌운다.
                                #   · **글자 스타일이 걸린 자리**: 글자색을 바꾸면 한/글이
                                #     '원래 모양 + 빨강'인 새 글자모양을 만들어 물린다 — 스타일의
                                #     글자모양 id와 갈라진다(사용자 결정 2026-09-10: 스타일 자리는
                                #     **빨강 형광펜**). 형광펜은 글자모양이 아니라 런 안의 별도
                                #     요소라(`<MARKPENBEGIN>`) 글자모양을 건드리지 않는다.
                                marked_with_pen = False
                                try:
                                    if shape_snap is not None:
                                        # ⚠ 새로 꺼내지 말고 **떠 둔 그 객체**를 되씌운다.
                                        orig_color = int(shape_snap.TextColor)
                                        if not style_locked:
                                            shape_snap.TextColor = self._APPLY_RED
                                        self.hwp.HAction.Execute("CharShape", shape_snap.HSet)
                                        if not style_locked and part:
                                            # 바뀌지 않은 조각만 원래 글자색으로 되돌린다. 전체를 먼저
                                            #   칠하고 빼는 순서라, 어느 조각이 실패해도 **덜 칠해지는
                                            #   일은 없다**(더 칠해질 뿐 — 예전 동작 쪽).
                                            shape_snap.TextColor = orig_color
                                            self._recolor_outside(match_end, len(original),
                                                                  len(corrected), part, shape_snap)
                                        if style_locked:
                                            # ⚠ 형광펜은 **찾기 직후 첫 서식 액션으로는 먹히지
                                            #   않는다**(실측: 첫 호출은 True를 돌려주고 아무 일도
                                            #   안 한다). 바로 위 CharShape Execute가 그 한 번을
                                            #   소진하므로 여기서는 정상 적용된다 — 순서를 바꾸지 말 것.
                                            if part:
                                                marked_with_pen = self._markpen_spans(
                                                    match_end, len(original), len(corrected), part)
                                            else:
                                                marked_with_pen = self._markpen(self._APPLY_RED)
                                            self._style_marked += 1
                                            # ★형광펜 자리는 **빨갛지 않다** — '교정본+메모'
                                            #   모드의 메모 단계가 `_match_is_red`로 자리를
                                            #   고르므로 그대로 두면 이 자리만 메모를 잃는다.
                                            #   그래서 **문단 좌표**를 적어 넘긴다. 글자 위치는
                                            #   뒤이은 치환에 밀리지만 **문단 번호는 밀리지
                                            #   않는다**(치환은 문단을 만들거나 없애지 않는다).
                                            if match_end is not None:
                                                pen_paras.append([match_end[0], match_end[1]])
                                    else:
                                        # 글자모양을 읽지 못한 자리(혼합 선택 등) — 예전 경로.
                                        cset = self.hwp.HParameterSet.HCharShape
                                        self.hwp.HAction.GetDefault("CharShape", cset.HSet)
                                        cset.TextColor = self._APPLY_RED
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

                            # 좌표 기록 — 삽입 직후 커서가 정확히 '시작 + 교정문 길이'에
                            #   있어야 믿는다(매치가 조판 부호를 걸쳤거나 선택을 다시 세운
                            #   경우를 거른다). 못 믿으면 그 문단의 기록을 무효로 돌린다 —
                            #   뒤늦게 엉뚱한 자리에 메모가 붙는 것보다 찾기로 되돌아가는 편이
                            #   낫다(호출 측 폴백).
                            if ins_ok:
                                spots_raw.append([match_end[0], match_end[1],
                                                  match_end[2] - len(original),
                                                  len(original), len(corrected), count])
                            elif "\r" in original or "\r" in corrected:
                                # 문단이 생기거나 없어진다 — 그 스토리의 문단 번호가 전부 밀린다.
                                spots_raw.append(["dirty_list", p[0]]
                                                 if isinstance(p, tuple) and p
                                                 else ["dirty_all"])
                            elif isinstance(p, tuple) and len(p) >= 2:
                                spots_raw.append(["dirty", p[0], p[1]])
                            else:
                                spots_raw.append(["dirty_all"])

                            replaced += 1
                            pass_replaced += 1
                            occ_pages.append({"i": count, "page": _pg, "ok": True,
                                              # 이 자리는 빨강 글자가 아니라 형광펜으로
                                              #   표시했다(글자 스타일 보존). 정오표가
                                              #   '어떻게 표시했는지'를 말할 근거.
                                              "pen": bool(marked_with_pen)})
                        except Exception as e:
                            err_msg = f"교정/색상 적용 실패: {e}"
                            # 치환이 반쯤 됐을 수 있다 — 좌표 기록을 믿지 않게 한다.
                            spots_raw.append(["dirty_all"])
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
                        # 어디를 바꿨는지 모른다 — 지금까지의 좌표 기록을 전부 믿을 수 없다.
                        spots_raw.append(["dirty_all"])
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
                    "occ_pages": occ_pages,  # 등장별 쪽 번호·치환 여부(정오표 행 근거)
                    "pen_paras": pen_paras,  # 형광펜으로 표시한 자리의 문단 좌표
                    "spots_raw": spots_raw,  # 치환 자리 좌표 기록(HwpEditor._resolve_spots)
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
                    "occ_pages": occ_pages,
                    "spots_raw": spots_raw,
                })

            # 진행률 (stderr)
            if (idx + 1) % 5 == 0 or idx == total - 1:
                pct = int(((idx + 1) / total) * 100)
                _send_progress(pct, f"교정 적용 중… {idx+1}/{total}")

        # ⚠ 화면 로그 규약(activity_panel): `[태그]` + `n건`, 개별 예시 금지.
        if self._style_marked:
            _send_log(f"[글자 스타일] 원고 글자 스타일을 지키려고 빨강 글자 대신 "
                      f"형광펜으로 표시 {self._style_marked}곳")
        return {"stats": stats, "detail": detail,
                "style_marked": self._style_marked,
                "style_lost": self._style_lost,
                "pen_in_style": self._pen_in_style,
                "inplace_fallback": self._inplace_fallback}

    # ══════════════════════════════════════════════════════════
    # ▌메모 달기 (결과물 축 output_mode="memo" · "hwp_memo")
    # ══════════════════════════════════════════════════════════
    #
    # ★실측으로 확정한 불변식 (2026-08-07, 한/글 2010 8.5.8.1677)
    #   ① **`HAction.Run("InsertFieldMemo")`만 쓴다.** 최상위 `hwp.Run(...)`은 2010에서
    #      **아무 일도 하지 않으면서 None을 돌려주는 no-op**이다(`MoveDocBegin`조차 None을
    #      돌려줘 성공/실패를 구분할 수도 없다). 그걸 성공으로 믿고 InsertText를 하면
    #      사유 문장이 **원고 본문에 그대로 박힌다** — 실측에서 머리말이 오염됐다.
    #   ② **반환값만 믿지 않는다.** 메모는 서브스토리를 여는 동작이라 `GetPos()[0]`(story)이
    #      바뀌어야 진짜 열린 것이다. 안 바뀌었으면 InsertText **금지**(①의 오염과 같은 결과).
    #   ③ **머리말·꼬리말 스토리에서는 거부된다**(실측: story 9 → False / story 0 → True).
    #      실패가 아니라 구조적 제약이므로 'blocked'로 따로 세어 정오표에 남긴다 —
    #      조용히 사라지면 안 된다.
    #
    # ★★2단계 설계 — **자리를 전부 찾은 뒤에야 메모를 단다** (2026-10-17 · 실측 재설계)
    #   memo_scan  → 메모 없이 각 항목의 자리 좌표를 찾기만 한다(문서 무변경)
    #   memo_keys  → 서브스토리 자리의 '본문 기준 위치'(문서 순 정렬 키)
    #   memo_place → 좌표로 곧장 가서 **문서 뒤쪽부터** 메모를 단다(찾기 0회)
    #
    #   ④ ★★**메모가 박힌 문서에서 '찾기'를 많이 돌리면 세션이 오염된다.** 그 뒤로는 어느
    #      자리에 메모를 열어도 `InsertText`가 `RPC_E_SERVERFAULT`로 죽고, 문서를 닫았다 열
    #      때까지 회복되지 않는다. 통제 실험(doc04 교정본, 2026-10-17):
    #        메모 48곳 + 찾기 300회 → 정상 / 메모 0~5곳 + 찾기 2,000회 → 정상
    #        메모 48곳 + 찾기 2,000회 → **이후 메모 전부 실패**(색 판독 유무와 무관)
    #      그래서 예전 설계(항목마다 문서 처음부터 찾으며 그 자리에 바로 단다)는 메모가
    #      쌓일수록 반드시 무너졌다. 특히 '교정본+메모'는 앵커가 흔한 교정문이라('자녀
    #      출산'·'정책 효과') 빨갛지 않은 매치까지 수천 번 훑어 훨씬 빨리 무너졌다 —
    #      doc04 실측 메모 291곳 · 못 함 331곳, 그중 234곳이 이 연쇄였다.
    #      ⚠ 예전 기록은 이 현상을 '이미 메모를 단 자리와 겹친 낱말에서 찾기 색인이
    #        낡는다'로 해석했고, 처방은 25항목마다 문서를 저장·닫기·다시 열기
    #        (`_reindex_document`)였다. 겹침은 첫 실패를 부르는 한 경로였을 뿐, 그 뒤의
    #        '전부 실패'는 세션 오염이었다 — 재색인은 오염을 주기적으로 씻어 낸 것이다.
    #        이제 메모를 단 뒤에는 찾기를 하지 않으므로 재색인 자체가 필요 없다.
    #   ⑤ ★**문서 뒤쪽부터 단다.** 메모 하나는 그 자리 문단의 위치를 밀고(앵커 시작 +8,
    #      앵커 뒤 +16 — 필드 시작·끝 컨트롤), **그보다 뒤에 있는 모든 서브스토리의 list
    #      id를 +2** 민다(실측: 본문 8번 문단에 메모 하나 → 뒤쪽 표 셀 26→28, 110→112).
    #      뒤에서부터 달면 아직 안 단 자리(전부 앞쪽)는 어느 것도 움직이지 않는다.
    #      ⚠ 그래서 정렬 키에 서브스토리의 **본문 기준 위치**가 필요하다(`memo_keys`).
    #   ⑥ ★**달기 전에 그 자리를 확인한다** — 좌표로 선택한 뒤 끝 위치와 글자가 앵커와
    #      같아야 단다. 어긋나면(좌표 계산이 틀렸거나 두 항목이 한 자리를 다툼) 메모를
    #      달지 않고 '표시 못 함'으로 돌려준다. 엉뚱한 자리에 붙는 것보다 낫다.
    #   ⑦ ★**메모 본문이 원문을 인용한다** — 메모를 다는 동안 찾기를 하지 않으므로 우리
    #      메모가 다음 항목의 매치가 될 일은 없다(예전엔 그 매치를 거르느라 메모 스토리
    #      id를 기억해야 했다 — `_memo_stories`는 이제 정보용이다).

    # ⚠ 예전엔 메모 본문 끝에 '· 이 낱말 N곳 중 k번째'를 붙였다. 사용자 지정으로
    #   **제거**했다(2026-08-10) — 반복 횟수는 정오표가 등장 1곳 = 1행으로 말하고,
    #   말풍선에 또 적으면 편집자가 읽을 것만 늘어난다. 되살리려면 그 결정부터 확인할 것.
    # 등장 수를 모를 때(전자동 등)의 안전 상한 / 등장 수를 알 때의 절대 상한.
    #   ⚠ 예전엔 하나(100)로 둘 다 했다 — doc_occ가 172여도 100에서 잘려 101번째
    #     이후 등장(제외 자리를 지나 155번째에 있던 진짜 등장 등)에 메모가 닿지 못했다.
    _MEMO_FALLBACK_OCC = 100
    _MEMO_MAX_OCC = 5000
    _MEMO_MAX_PASSES = 4
    # 메모 본문 서식 (사용자 지정 2026-08-12) — 말풍선 안에서만 쓰인다.
    _MEMO_FONT = "맑은 고딕"
    _MEMO_FONT_SIZE = 800        # HWPUNIT(1pt = 100) → 8pt
    _MEMO_LINE_SPACING = 140     # %

    def _apply_memo_style(self):
        """메모 편집 상태에서 **글자·문단 모양을 통일**한다(사용자 지정 2026-08-12).

        맑은 고딕 8pt · 줄간격 140% — 말풍선은 좁고 본문 서식(원고 폰트·크기)을 그대로
        물려받으면 장마다 크기가 달라 읽는 리듬이 끊긴다.

        ⚠ **선택 없이, 글자를 쓰기 전에** 건다. 그래야 '앞으로 입력될 글자'의 모양이
          되고, 뒤이어 `InsertText`가 만드는 두·세째 줄까지 같은 모양을 물려받는다.
          ⚠ `SelectAll`로 잡아 거는 길을 택하지 말 것 — 서브스토리 밖까지 잡히면
            **원고 전체**에 8pt가 걸린다(메모 모드의 약속은 원고 무변경이다).
        ⚠ 서식 실패는 메모 자체의 실패가 아니다 — 통째로 삼키고 본문 쓰기로 넘어간다.
          내용 없는 말풍선보다 서식이 다른 말풍선이 낫다.
        """
        try:
            cset = self.hwp.HParameterSet.HCharShape
            self.hwp.HAction.GetDefault("CharShape", cset.HSet)
            # 언어별 글꼴 칸이 따로 있다 — 하나만 바꾸면 한글·영문이 갈린다.
            for attr in ("FaceNameHangul", "FaceNameLatin", "FaceNameHanja",
                         "FaceNameJapanese", "FaceNameOther", "FaceNameSymbol",
                         "FaceNameUser"):
                try:
                    setattr(cset, attr, self._MEMO_FONT)
                except Exception:
                    pass
            cset.Height = self._MEMO_FONT_SIZE      # HWPUNIT = pt × 100
            self.hwp.HAction.Execute("CharShape", cset.HSet)
        except Exception:
            pass
        try:
            pset = self.hwp.HParameterSet.HParaShape
            # ⚠ 액션 이름은 **"ParagraphShape"**다 — 파라미터셋 이름(`HParaShape`)을
            #   그대로 따라 "ParaShape"라고 쓰면 **예외도 없이 아무 일도 일어나지
            #   않는다**(실측 2026-08-12: 메모 문단이 기본 paraPr 0 · 줄간격 160%
            #   그대로였다. 글자 모양은 같은 방식으로 정상 적용돼 더 헷갈린다).
            self.hwp.HAction.GetDefault("ParagraphShape", pset.HSet)
            try:
                pset.LineSpacingType = self.hwp.LineSpacingMethod("Percent")
            except Exception:
                pset.LineSpacingType = 0            # 0 = 글자에 따라(%)
            pset.LineSpacing = self._MEMO_LINE_SPACING
            self.hwp.HAction.Execute("ParagraphShape", pset.HSet)
        except Exception:
            pass

    def _write_memo_body(self, text):
        """메모 편집 상태에서 본문을 쓴다. 반환 사유 문자열(빈 문자열이면 성공).

        ⚠ COM 메서드 `hwp.InsertText(...)`를 대체 경로로 쓰지 말 것 — HWP 2010에는
          **없다**(실측 2026-08-08: `AttributeError: HWPFrame.HwpObject.InsertText`).
          파라미터셋 경로가 유일하다.
        """
        self._apply_memo_style()
        try:
            iset = self.hwp.HParameterSet.HInsertText
            self.hwp.HAction.GetDefault("InsertText", iset.HSet)
            iset.Text = text
            self.hwp.HAction.Execute("InsertText", iset.HSet)
            return ""
        except Exception as exc:
            return f"메모 본문 입력 실패: {exc}"

    # ⚠ **같은 자리에서 메모를 다시 여는 재시도를 넣지 말 것**(실측 2026-08-10).
    #   `InsertText`가 죽은 자리는 그 세션 안에서 살아나지 않는다 — `CloseEx` 뒤 재열기,
    #   `DeleteFieldMemo` 뒤 재열기, 매치 뒤쪽 좌표에서 재열기, 0.3초 대기, 항목을 끝낸
    #   뒤 재시도까지 전부 시험했고 회복률은 102곳 중 1곳이었다. 게다가 재열기는 **빈
    #   메모를 하나 더 남긴다**(실측: 302곳 성공 · 빈 메모 92개가 저장본에 실렸다).
    #   원인은 이 자리가 아니라 **세션 오염**(메모 달린 문서에서의 대량 찾기)이며, 해법은
    #   메모를 단 뒤에 찾기를 하지 않는 것이다 — 메모 머리말 불변식④(2단계 설계).

    # 세션 오염의 지문 — `InsertText`가 이 HRESULT(RPC_E_SERVERFAULT)로 죽으면 그 세션의 뒤
    #   메모는 전부 같은 식으로 죽는다(메모 머리말 불변식④).
    _SESSION_FAULT = "-2147417851"

    @classmethod
    def _is_session_fault(cls, msg) -> bool:
        return cls._SESSION_FAULT in str(msg or "")

    def _normalize_edit_state(self):
        """서브스토리·선택·편집 상태를 본문 기준으로 되돌린다(실패 뒤 재시도 준비)."""
        for _ in range(3):
            try:
                self.hwp.HAction.Run("CloseEx")
            except Exception:
                break
        try:
            self.hwp.HAction.Run("Cancel")
        except Exception:
            pass

    def _insert_memo_here(self, body, anchor_color, original=""):
        """선택해 둔 **현재 자리**(앵커)에 메모 하나를 단다. 반환 (성공, 사유).

        성공하면 만들어진 메모 서브스토리 id를 `self._memo_stories`에 기록한다 —
        그 안에서 잡히는 매치를 등장으로 세지 않기 위한 방어다(메모 머리말 불변식⑦).

        원고 글자는 바뀌지 않는다 — 메모 본문은 필드의 서브스토리에 들어간다
        (OWPML `<hp:fieldBegin type="MEMO"><hp:subList>…`).

        ⚠ 본문 쓰기가 실패하면 **빈 메모를 지우고 그 자리에서 한 번 더 시도한다.**
          이 실패는 비결정적이라(위 `_write_memo_body` 주석) 한 번의 실패로 자리를
          버리면 세 산출물의 반영 자리가 갈린다.
        """
        # 앵커 강조(글자색)는 **기본으로 끈다** — 사용자 지정 2026-08-07: "해당 글자
        #   색상은 변환하지 말아줘". 메모 모드의 약속은 '원고를 손대지 않는다'이고,
        #   글자색도 원고의 서식이다. 켜려면 호출부가 명시적으로 색을 넘겨야 한다.
        #   ⚠ 켤 때는 **메모보다 먼저** 해야 한다 — RepeatFind가 남긴 선택이 살아
        #     있을 때만 글자색을 입힐 수 있고, 메모 삽입이 그 선택을 소비하기 때문.
        if anchor_color is not None:
            try:
                cset = self.hwp.HParameterSet.HCharShape
                self.hwp.HAction.GetDefault("CharShape", cset.HSet)
                cset.TextColor = int(anchor_color)
                self.hwp.HAction.Execute("CharShape", cset.HSet)
            except Exception:
                pass

        try:
            before = self.hwp.GetPos()
        except Exception:
            before = None

        try:
            opened = bool(self.hwp.HAction.Run("InsertFieldMemo"))   # ★불변식①
        except Exception as exc:
            return False, f"메모 삽입 예외: {exc}"
        if not opened:
            sid = before[0] if isinstance(before, tuple) and before else "?"
            return False, (f"이 자리에는 메모를 달 수 없습니다 "
                           f"(머리말·꼬리말 등 story {sid})")

        try:
            after = self.hwp.GetPos()
        except Exception:
            after = None
        # ★불변식② — 서브스토리 진입 실패면 본문에 쓰지 않고 물러난다.
        if (not isinstance(before, tuple) or not isinstance(after, tuple)
                or not before or not after or after[0] == before[0]):
            try:
                self.hwp.HAction.Run("CloseEx")
            except Exception:
                pass
            return False, "메모 편집 상태로 진입하지 못했습니다 (본문 오염 방지로 건너뜀)"

        # 메모 머리말 불변식⑦ — 이 메모의 서브스토리 id를 기억해 둔다. 메모 본문이 원문을
        #   인용하므로, 메모를 단 뒤 찾기를 하면 이 메모 안의 글자가 '새 등장'으로 잡힌다
        #   (2단계 설계에선 그런 찾기가 없지만 방어로 남긴다).
        self._memo_stories.add(after[0])

        # ⚠ '이 낱말 N곳 중 k번째' 줄은 **넣지 않는다**(사용자 지정 2026-08-10).
        #   메모는 그 자리에서 무엇을 무엇으로 고칠지만 말하면 되고, 반복 횟수는 정오표가
        #   등장 1곳 = 1행으로 이미 말한다. 말풍선마다 붙으면 읽을 것만 늘어난다.
        text = body
        # ⚠ 실패 사유에 **앵커가 있던 스토리**를 적는다 — 본문(0)인지 각주·표 같은
        #   서브스토리인지가 사유의 전부이고, 없으면 진단할 길이 없다.
        where = f" (앵커 story {before[0]} → 메모 story {after[0]})"
        err = self._write_memo_body(text)
        if err:
            err += where
        # 메모 편집 상태는 **반드시** 빠져나온다 — 남으면 다음 찾기가 메모 안을 돈다.
        try:
            self.hwp.HAction.Run("CloseEx")
        except Exception:
            pass
        if not err:
            return True, ""

        # ── 실패 — 빈 메모를 지우고 **그 자리에서 한 번 더** ──────────────────
        # ⚠ 본문을 못 넣었으면 **빈 메모가 문서에 남는다.** 그대로 두면 편집자에게
        #   내용 없는 말풍선이 보이고, 그게 무엇인지 알 길이 없다 — 반드시 지운다.
        try:
            self.hwp.HAction.Run("DeleteFieldMemo")
        except Exception:
            pass
        self._memo_stories.discard(after[0])
        self._normalize_edit_state()
        # ★세션 오염(메모 머리말 불변식④)이면 같은 세션의 어떤 재시도도 죽는다 — 곧장
        #   돌려보내 호출 측이 문서를 다시 열게 한다(`memo_place`의 poisoned).
        if self._is_session_fault(err):
            return False, err
        # ★같은 자리에 다시 넣으면 **똑같이 실패한다**(실측: 비결정이 아니라 결정적).
        #   원인은 앞 항목이 박아 둔 **메모 컨트롤이 이 낱말의 시작 자리를 먹고 있는 것**
        #   이다 — 중첩 원문에서 특히 잦다('고려해야한다'에 메모를 달면 그 안의
        #   '해야한다' 앵커가 컨트롤 경계에 걸린다). 실측 2026-08-08: 중첩 3항목을 뺀
        #   같은 낱말 집합은 36곳 전부 성공(파일 실측 36개), 넣으면 9곳이 실패했다.
        #   그래서 재시도는 **낱말 한 칸 안쪽**에서 한다 — 편집자에게는 같은 낱말의
        #   같은 자리이고, 컨트롤 경계는 확실히 벗어난다.
        nudge = 1 if len(original or "") > 1 else 0
        try:
            self.hwp.SetPos(before[0], before[1], before[2] + nudge)
        except Exception:
            return False, err

        try:
            reopened = bool(self.hwp.HAction.Run("InsertFieldMemo"))
            again = self.hwp.GetPos() if reopened else None
        except Exception:
            return False, err
        if not reopened or not isinstance(again, tuple) or not again \
                or again[0] == before[0]:
            return False, err
        self._memo_stories.add(again[0])
        err2 = self._write_memo_body(text)
        try:
            self.hwp.HAction.Run("CloseEx")
        except Exception:
            pass
        if not err2:
            return True, ""
        try:
            self.hwp.HAction.Run("DeleteFieldMemo")
        except Exception:
            pass
        self._memo_stories.discard(again[0])
        self._normalize_edit_state()
        return False, err2 + where

    # 교정본이 교정 자리에 칠하는 글자색(COLORREF 0x0000FF = 빨강). apply의 CharShape
    #   적용과 **같은 값**이어야 한다 — 여기 값이 어긋나면 결합 모드가 자리를 못 찾는다.
    #   글자 스타일이 걸린 자리에 쓰는 **형광펜도 같은 빨강**이다.
    _APPLY_RED = 255

    # 지문에 쓰는 언어 칸 순서 — 바이너리(한글·영어·한자·일어·기타·기호·사용자)와
    #   같은 순서여야 한다. `core/hwp_styles.py`의 `_fingerprint()`와 **한 쌍**이다.
    _FP_LANGS = ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol", "User")

    def _charshape_fingerprint(self, cset):
        """이미 `GetDefault("CharShape")`로 채워진 파라미터셋 → 지문 튜플(못 읽으면 None).

        ⚠ 여기서 `GetDefault`를 부르지 않는다 — 호출부가 치환 전에 떠 둔 **스냅샷을
          그대로 되씌워야** 하는데, 다시 부르면 그 스냅샷이 덮인다.
        ⚠ 한글 글꼴 이름이 비면(혼합 선택 등) **None**을 돌려준다. 그 자리는 판정도
          복원도 하지 않고 예전 경로로 보낸다 — 빈 글꼴 이름을 되씌우면 서식이 무너진다.
        ⚠ 필드 구성·순서는 `core/hwp_styles.py`와 정확히 같아야 한다.
        """
        try:
            faces = [getattr(cset, "FaceName" + lang) for lang in self._FP_LANGS]
            if not faces[0]:
                return None
            fp = list(faces)
            for prefix in ("Ratio", "Spacing", "Size", "Offset"):
                fp.extend(int(getattr(cset, prefix + lang)) for lang in self._FP_LANGS)
            attr = ((int(cset.Italic) & 1)
                    | (int(cset.Bold) & 1) << 1
                    | (int(cset.UnderlineType) & 3) << 2
                    | (int(cset.UnderlineShape) & 0xF) << 4
                    | (int(cset.OutLineType) & 7) << 8
                    | (int(cset.ShadowType) & 3) << 11
                    | (int(cset.Emboss) & 1) << 13
                    | (int(cset.Engrave) & 1) << 14
                    | (int(cset.SuperScript) & 1) << 15
                    | (int(cset.SubScript) & 1) << 16)
            # ⚠ 꺼져 있는 속성의 색은 0으로 눕힌다 — `hwp_styles._fingerprint()`와
            #   **같은 규칙**이어야 한다(.hwp/.hwpx가 그 칸을 다르게 들고 있어서다).
            ul_color = int(cset.UnderlineColor) if (attr >> 2) & 3 else 0
            shadow = int(cset.ShadowColor) if (attr >> 11) & 3 else 0
            fp.extend([int(cset.Height), attr, int(cset.TextColor),
                       ul_color, int(cset.ShadeColor), shadow])
            return tuple(fp)
        except Exception:
            return None

    def _markpen(self, color):
        """선택 범위에 형광펜을 칠한다. 성공 여부(추정)를 돌려준다.

        ⚠ 반환값을 믿지 말 것 — 실측에서 **True를 돌려주고 아무 일도 안 하는 호출**
          (찾기 직후 첫 서식 액션)과 **False를 돌려주고 칠해지는 호출**이 모두 나왔다.
          호출부가 순서(CharShape Execute 다음)를 지키는 것이 실제 보장이다.
        """
        try:
            ms = self.hwp.HParameterSet.HMarkpenShape
            self.hwp.HAction.GetDefault("MarkPenShape", ms.HSet)
            ms.Color = color
            self.hwp.HAction.Execute("MarkPenShape", ms.HSet)
            return True
        except Exception:
            return False

    def _caret(self):
        try:
            p = self.hwp.GetPos()
            return tuple(p[:3]) if isinstance(p, tuple) and len(p) >= 3 else None
        except Exception:
            return None

    def _restore_caret(self, p) -> None:
        """부분 칠하기 전 커서로 되돌린다 — 다음 찾기가 예전과 같은 자리에서 이어지게
        (교정본 계획 `ApplyWorker._BridgeReplay`가 그 재개 자리를 전제한다)."""
        try:
            self.hwp.HAction.Run("Cancel")
            if p is not None:
                self.hwp.SetPos(*p)
        except Exception:
            pass

    def _select_in_para(self, lst, para, a, b) -> bool:
        """(lst, para) 문단의 [a, b) 글자를 고르고 **커서 위치로 확인**한다(치환 전 선택 되살리기와
        같은 방식 — `SelectText`는 이 환경에서 예외를 던진다)."""
        try:
            self.hwp.HAction.Run("Cancel")
            self.hwp.SetPos(lst, para, a)
            for _ in range(b - a):
                self.hwp.HAction.Run("MoveSelRight")
            now = self.hwp.GetPos()
            return isinstance(now, tuple) and len(now) >= 3 and tuple(now[:3]) == (lst, para, b)
        except Exception:
            return False

    def _recolor_outside(self, match_end, n_orig, n_corr, spans, shape) -> None:
        """방금 통째로 칠한 교정문에서 `spans` **밖** 조각에 `shape`(원래 글자색)를 되씌운다.

        ⚠ 같은 스냅샷 객체를 쓴다 — 색만 원래 값이라 결과 글자모양이 원래와 같아져 한/글이
          **원래 글자모양 id를 그대로** 쓴다(빨강을 안 칠한 것과 같다). 조각을 고르지 못하면
          그 조각은 빨간 채로 둔다(덜 칠하는 쪽으로는 무너지지 않는다).
        """
        lst, para, base = match_end[0], match_end[1], match_end[2] - n_orig
        keep = self._caret()
        try:
            last = 0
            for a, b in list(spans) + [(n_corr, n_corr)]:
                if last < a and self._select_in_para(lst, para, base + last, base + a):
                    self.hwp.HAction.Execute("CharShape", shape.HSet)
                last = b
        except Exception:
            pass
        finally:
            self._restore_caret(keep)

    def _markpen_spans(self, match_end, n_orig, n_corr, spans) -> bool:
        """글자 스타일 자리 — 교정문의 `spans` 조각에만 형광펜. 한 조각이라도 고르지 못하면
        교정문 전체에 칠한다(예전 동작). 형광펜은 지울 길이 마땅치 않아 빨강과 순서가 반대다."""
        lst, para, base = match_end[0], match_end[1], match_end[2] - n_orig
        keep = self._caret()
        try:
            for a, b in spans:
                if not self._select_in_para(lst, para, base + a, base + b):
                    break
                self._markpen(self._APPLY_RED)
            else:
                return True
            if self._select_in_para(lst, para, base, base + n_corr):
                return self._markpen(self._APPLY_RED)
            return False
        finally:
            self._restore_caret(keep)

    # ══════════════════════════════════════════════════════════
    # ▌최소 편집 치환 — 원고의 서식·글자 스타일을 한 글자도 풀지 않는다
    # ══════════════════════════════════════════════════════════
    #
    # 사용자 지시 2026-10-23: "원본의 글자 스타일이 절대 풀리지 않게" · "한 문장에 볼드·볼드+이탤릭·
    # 볼드+밑줄·밑줄·이탤릭이 섞여 있어도 각각 그대로 유지한 채 교정 글자만 치환".
    # 매치 전체를 `InsertText` 한 번으로 덮으면 둘 다 불가능하다 — 새 글자는 전부 **커서 앞 글자**의
    # 모양·글자 스타일 소속을 물려받고(실측), 런 전체를 덮으면 글자 스타일 태그(`PARA_RANGE_TAG`
    # 종류 1)가 통째로 지워진다. 그래서 **바뀐 글자만** 고치고(`_edit_ops`), 새 글자에는 그 자리
    # 원래 글자의 소속과 글자모양을 되씌운다. 고치지 않은 글자는 한/글에서 한 번도 건드리지 않는다.

    def _insert_text(self, text) -> None:
        iset = self.hwp.HParameterSet.HInsertText
        self.hwp.HAction.GetDefault("InsertText", iset.HSet)
        iset.Text = text
        self.hwp.HAction.Execute("InsertText", iset.HSet)

    def _block_text(self):
        """지금 선택한 블록의 글자 — 문서에 실제로 있는 그대로. 못 읽으면 None.

        `InitScan`의 범위 0x00ff = 선택 블록. 읽은 뒤에도 선택과 커서는 그대로다(실측 2026-10-23).
        ⚠ 일반 텍스트(상태 2)가 아닌 조각(문단 넘김·컨트롤 출입)이 끼면 None — 한 문단 안의
          글자만 이어진 매치가 아니면 최소 편집의 좌표 셈이 성립하지 않는다.
        """
        # ⚠ 인자 6개를 다 준다 — 동적 디스패치(브리지 서브프로세스)에서는 줄인 형식이
        #   'DISP_E_BADPARAMCOUNT'로 실패한다(실측 2026-10-23: 형식 정보가 캐시된 단독 시험에선
        #   2개로도 되어 놓쳤다). `get_text`와 같은 폴백 순서.
        self._scan_note = []        # 진단용 — 읽은 조각의 (상태, 글자)
        for args in ((0x00, 0x00ff, 0, 0, 0, 0), (0x00, 0x00ff)):
            try:
                self._scan_note.append(("init", self.hwp.InitScan(*args)))
                break
            except Exception as exc:
                self._scan_note.append(("init", repr(exc)))
        else:
            return None
        out = []
        try:
            for _ in range(256):
                state, text = self.hwp.GetText()
                self._scan_note.append((state, text))
                if state in (0, 1):
                    return "".join(out)
                if state != 2:
                    return None
                out.append(text)
            return None
        except Exception as exc:
            self._scan_note.append(("err", repr(exc)))
            return None
        finally:
            try:
                self.hwp.ReleaseScan()
            except Exception:
                pass

    def _style_id_sel(self):
        """지금 선택의 스타일 번호(`HStyle.Apply`) — 글자 스타일 소속이면 그 번호, 아니면 문단 스타일.

        한/글은 소속을 글자모양 id가 아니라 **글자 스타일 태그**로 판정한다(실측 2026-10-23 —
        빨강으로 글자모양이 바뀐 글자도 태그 안이면 그 글자 스타일로 보고한다).
        """
        try:
            sset = self.hwp.HParameterSet.HStyle
            self.hwp.HAction.GetDefault("Style", sset.HSet)
            return int(sset.Apply)
        except Exception:
            return None

    def _char_format(self, lst, para, pos):
        """(lst, para) 문단 pos 글자 하나의 (글자모양 스냅샷, 스타일 번호). 못 고르면 None.

        ⚠ 한 글자를 **골라** 읽는다 — 커서만 두면 한/글 2010은 커서 **앞** 글자의 모양을 준다.
        ⚠ 스냅샷은 매번 새로 꺼낸 파라미터셋 객체라 여러 개를 함께 들고 각각 되씌울 수 있다(실측).
          되씌울 때는 **그 객체 그대로** Execute한다(새로 꺼내면 빈 셋 — apply 주석 ③).
        """
        if not self._select_in_para(lst, para, pos, pos + 1):
            return None
        try:
            cset = self.hwp.HParameterSet.HCharShape
            self.hwp.HAction.GetDefault("CharShape", cset.HSet)
        except Exception:
            return None
        # 글자 스타일이 없는 문서는 소속을 읽을 까닭이 없다(스타일 번호 읽기가 가장 비싼 호출이다).
        return cset, (self._style_id_sel() if self._char_style_ids else None)

    def _style_fp_hit(self, cset) -> bool:
        """이 글자모양이 글자 스타일 지문과 같은가(형광펜으로 칠할 자리인가).

        지문은 COM 속성 50여 개라 글자마다 다 읽으면 느리다(실측: 교정 한 곳에 100ms 넘게 늘었다).
        한글 글꼴·크기·굵기·기울임 네 칸으로 먼저 거르고, 거기서 맞을 때만 전부 읽는다.
        ⚠ 한글 글꼴 이름이 비면(섞인 값) True — 그 스냅샷에 빨강을 되씌우면 서식이 무너진다.
        """
        try:
            face = cset.FaceNameHangul
            if not face:
                return True
            if not self._style_fps:
                return False
            quick = (face, int(cset.Height), int(cset.Bold) & 1, int(cset.Italic) & 1)
        except Exception:
            return True
        if quick not in self._style_fp_quick:
            return False
        fp = self._charshape_fingerprint(cset)
        return fp is None or fp in self._style_fps

    def _restore_format(self, lst, para, a, b, fmt) -> bool:
        """새로 넣은 [a, b) 글자에 출처 글자의 **글자 스타일 소속**과 **글자모양**을 되씌운다.
        소속까지 출처와 같아졌으면 True.

        ① 소속이 출처와 다르면(이웃 런을 물려받음) 글자 스타일을 풀고(`StyleClearCharStyle` — 글자
           모양도 문단 기본으로 돌아간다), 출처가 글자 스타일이면 그 스타일을 다시 건다(`Style` —
           글자 스타일은 **선택에만** 걸린다. 실측: 태그·글자모양·형광펜이 모두 산다).
        ② 마지막에 출처 스냅샷을 되씌운다 — 글자모양 id까지 원래와 같아진다(스타일 정의와 다르게
           고쳐 쓴 글자도 그대로).
        ⚠ **문단 스타일** 번호를 `Style`로 걸면 문단 전체가 바뀐다 — 문서가 정의한 글자 스타일
          번호(`_char_style_ids`)일 때만 건다. 동작마다 선택을 다시 세운다(선택 없이 `Style`을
          부르면 문단에 걸린다).
        """
        cset, sid = fmt
        if not self._select_in_para(lst, para, a, b):
            raise RuntimeError("새로 넣은 글자를 고르지 못했습니다")
        ok = True
        if sid is not None and self._style_id_sel() != sid:
            self.hwp.HAction.Run("StyleClearCharStyle")
            if sid in self._char_style_ids:
                if not self._select_in_para(lst, para, a, b):
                    raise RuntimeError("새로 넣은 글자를 고르지 못했습니다")
                sset = self.hwp.HParameterSet.HStyle
                self.hwp.HAction.GetDefault("Style", sset.HSet)
                sset.Apply = sid
                self.hwp.HAction.Execute("Style", sset.HSet)
            ok = self._style_id_sel() == sid
            if not self._select_in_para(lst, para, a, b):
                raise RuntimeError("새로 넣은 글자를 고르지 못했습니다")
        self.hwp.HAction.Execute("CharShape", cset.HSet)
        return ok

    def _paint_in_place(self, lst, para, start, n, spans, known) -> bool:
        """최소 편집을 마친 교정문 [start, start+n)의 `spans` 조각을 칠한다. 형광펜을 썼으면 True.

        글자마다 **그 글자의 모양·소속**으로 고른다 — 한 매치에 서식이 여럿이어도 각자 지킨다.
          · 글자 스타일 소속(스타일 번호)이거나 글자 스타일 지문과 같은 글자 → 빨강 형광펜
            (글자모양을 건드리지 않는다 — 사용자 결정 2026-09-10)
          · 그 밖 → 그 글자 자신의 스냅샷에 글자색만 빨강(굵기·기울임·밑줄은 그대로)
          · 모양을 읽지 못한 글자 → 형광펜(글자모양을 건드리지 않는 쪽으로 무너진다)
        `known`({교정문 위치: (스냅샷, 스타일 번호)})은 이미 아는 글자다 — 새로 넣은 글자는 방금
        출처 모양을 되씌웠고, 남긴 글자 중 출처로 읽어 둔 것도 있다. 글자 하나 읽기가 COM 호출
        10여 번이라(실측: 칠하기가 최소 편집 시간의 60%) 아는 글자는 다시 읽지 않는다.
        """
        cells = []
        in_style = False
        hit = {}                        # 같은 스냅샷 객체의 지문 판정은 한 번만
        for a, b in spans:
            for c in range(a, min(b, n)):
                f = known.get(c) or self._char_format(lst, para, start + c)
                if f is None:
                    cells.append((c, None, True))
                    continue
                cset, sid = f
                if sid in self._char_style_ids:
                    in_style = True
                    cells.append((c, cset, True))
                else:
                    if id(cset) not in hit:
                        hit[id(cset)] = self._style_fp_hit(cset)
                    cells.append((c, cset, hit[id(cset)]))
        pen = primed = False
        k = 0
        while k < len(cells):
            c, cset, locked = cells[k]
            if not locked:
                e = k       # 같은 스냅샷을 쓰는 이웃 글자(새 글자와 그 출처 등)는 한 번에
                while (e + 1 < len(cells) and not cells[e + 1][2] and cells[e + 1][1] is cset
                       and cells[e + 1][0] == cells[e][0] + 1):
                    e += 1
                if self._select_in_para(lst, para, start + c, start + cells[e][0] + 1):
                    cset.TextColor = self._APPLY_RED
                    self.hwp.HAction.Execute("CharShape", cset.HSet)
                    primed = True
                k = e + 1
                continue
            e = k           # 이어지는 형광펜 글자는 한 번에 칠한다
            while e + 1 < len(cells) and cells[e + 1][2] and cells[e + 1][0] == cells[e][0] + 1:
                e += 1
            if not primed and cset is not None:
                # ⚠ 형광펜은 **찾기 직후 첫 서식 액션으로는 먹히지 않는다**(실측) — 그 글자의
                #   모양을 그대로 한 번 되씌워 그 한 번을 소진한다(글자모양은 변하지 않는다).
                if self._select_in_para(lst, para, start + c, start + c + 1):
                    self.hwp.HAction.Execute("CharShape", cset.HSet)
                    primed = True
            if self._select_in_para(lst, para, start + c, start + cells[e][0] + 1):
                self._markpen(self._APPLY_RED)
                pen = True
            k = e + 1
        if pen and in_style:
            # ⚠ 한/글 2010의 `.hwpx` 저장기는 글자 스타일 구간 안의 형광펜을 **버린다**(실측
            #   2026-10-23 — .hwp엔 남는다). 호출부가 .hwpx 원고일 때 경고하도록 센다.
            self._pen_in_style += 1
        return pen

    def _apply_in_place(self, lst, para, start, doc_text, corrected, spans):
        """★매치 [start, start+len(doc_text))를 교정문으로 — **바뀐 글자만** 고친다.

        ① 바뀌는 글자마다 출처(원래 글자)를 정해 고치기 **전에** 모양·소속을 떠 둔다 — 바꾼
           글자는 그 자리의 원래 글자, 끼워 넣은 글자는 앞 글자(매치 첫머리면 첫 글자).
        ② 뒤에서부터 고친다(앞쪽 좌표가 밀리지 않는다). 고칠 때마다 새 글자에 출처의 소속·모양을
           되씌운다(`_restore_format`).
        ③ 결과를 문서에서 다시 읽어 교정문과 견준다 → 바뀐 곳만 칠한다 → 커서는 교정문 **뒤**
           (다음 찾기가 여기서 이어진다 — 교정본 계획 `ApplyWorker._BridgeReplay`의 전제).

        Returns: 형광펜을 썼으면 True, 아니면 False. **손대기 전에** 포기하면 None(호출부가 예전
          경로로 간다). ⚠ 고치기 시작한 뒤의 실패는 예외로 올린다 — 반쯤 고친 자리에 통째 치환을
          또 돌리면 두 번 고친다. 호출부의 except가 그 항목을 멈추고 좌표를 무효로 돌린다.
        """
        plan, fmts = [], {}
        for i1, i2, j1, j2 in _edit_ops(doc_text, corrected):
            if i2 > i1:
                srcs = [i1 + min(k, i2 - i1 - 1) for k in range(j2 - j1)]
            else:
                srcs = [i1 - 1 if i1 > 0 else 0] * (j2 - j1)
            for s in srcs:
                if s not in fmts:
                    f = self._char_format(lst, para, start + s)
                    if f is None:
                        return None
                    fmts[s] = f
            plan.append((i1, i2, j1, corrected[j1:j2], srcs))

        # 칠하기가 다시 읽지 않도록 교정문 위치 → 이미 아는 모양(새 글자 = 출처 · 남긴 글자 중
        #   출처로 읽어 둔 것). 남긴 글자는 한 번도 건드리지 않으므로 읽어 둔 모양 그대로다.
        known, pi, pj = {}, 0, 0
        for i1, i2, j1, new, srcs in plan + [(len(doc_text), len(doc_text), len(corrected), "", [])]:
            for t in range(j1 - pj):
                if pi + t in fmts:
                    known[pj + t] = fmts[pi + t]
            for t, s in enumerate(srcs):
                known[j1 + t] = fmts[s]
            pi, pj = i2, j1 + len(new)

        lost = 0
        for i1, i2, _j1, new, srcs in reversed(plan):
            at = start + i1
            if i2 > i1:
                if not self._select_in_para(lst, para, at, start + i2):
                    raise RuntimeError("고칠 글자를 고르지 못했습니다")
                if new:
                    self._insert_text(new)
                else:
                    self.hwp.HAction.Run("Delete")
            else:
                self.hwp.HAction.Run("Cancel")
                self.hwp.SetPos(lst, para, at)
                self._insert_text(new)
            if self._caret() != (lst, para, at + len(new)):
                raise RuntimeError("고친 뒤 커서가 예상 자리에 없습니다")
            k = 0
            while k < len(new):         # 출처가 같은 새 글자끼리 묶어 되씌운다
                e = k
                while e + 1 < len(new) and srcs[e + 1] == srcs[k]:
                    e += 1
                if not self._restore_format(lst, para, at + k, at + e + 1, fmts[srcs[k]]):
                    lost += 1
                k = e + 1

        n = len(corrected)
        if not (self._select_in_para(lst, para, start, start + n)
                and self._block_text() == corrected):
            raise RuntimeError("고친 결과가 교정문과 다릅니다")
        pen = (self._paint_in_place(lst, para, start, n, spans or [(0, n)], known)
               if plan else False)
        self._restore_caret((lst, para, start + n))
        if lost:
            self._style_lost += 1
        return pen

    def _consume_pen_para(self, avail, used) -> bool:
        """지금 매치가 **형광펜으로 표시해 둔 문단**인가.

        '교정본+메모' 모드에서 빨강 대신 형광펜을 쓴 자리를 메모 단계가 알아보게 하는
        유일한 단서다(`_match_is_red`가 왜 형광펜을 못 읽는지는 그 함수 주석 참조).
        ⚠ 문단 안에 교정문이 여러 번 나오면 **먼저 만난 자리**를 우리 자리로 본다 —
          같은 문단·같은 문자열·같은 글자 스타일이 겹칠 때만 어긋나므로 드물고,
          어긋나도 메모 한 장이 이웃 자리에 갈 뿐 원고는 변하지 않는다.
        ⚠ 이미 인정한 문단(`used`)도 **True로 돌려준다**. 재탐색 패스에서 이 자리가
          다시 잡히기 때문인데, 여기서 걸러 버리면 빨강 자리와 달리 `reskip_left`에
          닿지 못해 **등장 계수가 어긋난다**(멀쩡한 뒷자리가 대신 건너뛰어진다).
          빨간 자리도 메모를 단 뒤 여전히 빨갛게 잡혀 reskip이 소비하는 것과 같은 취급이다.
        """
        try:
            pos = self.hwp.GetPos()
        except Exception:
            return False
        if not (isinstance(pos, tuple) and len(pos) >= 2):
            return False
        key = (pos[0], pos[1])
        if key in avail:
            avail.remove(key)
            used.append(key)
            return True
        return key in used

    def _match_is_red(self, red_at=None, n=0) -> bool:
        """지금 찾은 매치(선택 상태)의 글자색이 **교정 표시 빨강**인가.

        ★'교정본+메모'(output_mode="hwp_memo") 전용 판정이다. 그 모드는 치환이 끝난
          문서에 메모를 다는데, 앵커가 **교정문**이라 원래부터 그 표기였던 자리까지
          찾힌다('해야한다'→'해야 한다'를 3곳 고쳤는데 '해야 한다'가 원래 20곳 있는
          문서를 생각해 보라 — 앞의 20곳에 메모가 달린다). 우리가 고친 자리는 빨간색이
          칠해져 있으므로 그것이 유일하게 믿을 수 있는 표식이다.
        ⚠ 색을 읽지 못하면 **True**(통과)로 본다. 색 판독 실패로 메모를 잃는 것보다,
          등장 상한(doc_occ=실제 치환 수)에 기대어 지나가는 편이 덜 나쁘다.
        ⚠ **글자 스타일이 걸린 자리는 글자색이 아니라 형광펜으로 표시**하므로(apply 참조)
          이 판정에 걸리지 않는다 — 결합 모드에서 그 자리만 메모가 빠진다. 형광펜으로
          보완하려 했으나 **불가능하다**: `HMarkpenShape.Color`는 선택 자리의 형광펜이
          아니라 **마지막으로 쓴 펜 색**을 돌려준다(실측 2026-09-10: 칠하지 않은 자리에서도
          255가 나왔다). 그 값을 믿으면 모든 자리가 통과해 메모가 엉뚱한 자리에 박힌다 —
          이 함수가 애초에 막으려던 바로 그 사고다. **다시 넣지 말 것.**
        ★교정본은 교정문의 **바뀐 곳만** 칠한다(`_red_spans`). 그러면 선택 전체의 색은 섞인 값이라
          빨강으로 읽히지 않으므로, `red_at`(교정문 안 칠한 글자 하나의 위치)이 주어지면 **그 글자
          하나를 골라** 읽는다. 읽은 뒤 커서를 매치 끝으로 돌려 다음 찾기가 이어지게 한다.
          `n`은 앵커 길이다.
          ⚠ 고르지 않고 커서만 두고 읽지 말 것 — 한/글 2010은 선택이 없으면 커서 **앞** 글자의
            모양을 준다(실측 2026-10-22: 위치 i에서 글자 i−1의 색, 문단 첫머리만 글자 0).
            그 앞 글자는 칠하지 않은 글자라 우리 자리를 놓친다.
        """
        try:
            if red_at is not None:
                cur = self.hwp.GetPos()
                self.hwp.SetPos(cur[0], cur[1], cur[2] - n + red_at)
                self.hwp.HAction.Run("MoveSelRight")
                cset = self.hwp.HParameterSet.HCharShape
                self.hwp.HAction.GetDefault("CharShape", cset.HSet)
                red = int(cset.TextColor) == self._APPLY_RED
                self.hwp.HAction.Run("Cancel")
                self.hwp.SetPos(cur[0], cur[1], cur[2])
                return red
            cset = self.hwp.HParameterSet.HCharShape
            self.hwp.HAction.GetDefault("CharShape", cset.HSet)
            return int(cset.TextColor) == self._APPLY_RED
        except Exception:
            return True

    def memo_scan(self, items):
        """메모 1단계 — 각 항목의 **자리 좌표만** 찾는다(메모 없음 · 문서 무변경).

        items: [{"original": 앵커, "need": 꼭 필요한 수, "limit": 최대 수,
                 "red_only", "pen_paras"}, …]
        반환: {"scan": [{"matches": [[list, para, 시작, 길이, 쪽], …], "error": ""}, …]}
              matches는 문서 순이다. 어느 매치를 쓸지(등장 번호·부분 거절·상한·자리 다툼)는
              호출 측이 정한다(`HwpEditor.insert_memos`).

        ★이 단계에서는 메모를 **하나도 달지 않는다** — 메모가 박힌 문서에서 찾기를 많이
          돌리면 세션이 오염되기 때문이다(머리말 불변식④). 그래서 찾기는 전부 여기서
          끝내고, 메모는 `memo_place`가 좌표로 단다.
        ★'교정본+메모'의 빨간 매치(`red_only`)는 `limit`까지 **전부** 돌려준다. 짧은 앵커가
          긴 교정문 속에 들어 있으면('취업 유지' ⊂ '취업 유지 후 이탈') 남의 빨간 자리도
          빨갛게 잡히므로, 어느 자리가 누구 것인지는 모든 항목의 매치를 모은 뒤에야 가릴
          수 있다(호출 측 자리 다툼 해소).
        ⚠ 다중 패스는 apply·옛 memo와 같은 안전장치다 — 한/글 찾기가 문서 중간에서 조기
          False를 돌려주는 일이 있어, 필요한 수에 못 미치면 처음부터 다시 훑되 이미 센
          매치는 소비만 하고 지나간다.
        """
        if getattr(self, "_memo_stories", None) is None:
            self._memo_stories = set()
        out = []
        total = len(items)
        for idx, item in enumerate(items):
            anchor = _clean((item.get("original") or "").replace("\n", "\r"))
            need = max(0, int(item.get("need") or 0))
            limit = min(max(need, int(item.get("limit") or 0)) or self._MEMO_FALLBACK_OCC,
                        self._MEMO_MAX_OCC)
            # ★'교정본+메모' — 앵커가 교정문이라 원래부터 그 표기였던 자리까지 찾힌다.
            #   우리가 고친 자리는 빨갛게 칠해져 있으므로 그것만 받는다(_match_is_red).
            red_only = bool(item.get("red_only"))
            # 교정본은 교정문의 바뀐 곳만 칠했다 — 칠한 첫 글자 위치(apply와 같은 `_red_spans`).
            #   `anchor_of`는 그 교정의 원문이다. None이면 교정문 전체가 빨갛다(예전 판정).
            red_at = None
            if red_only and "\r" not in anchor:
                anchor_of = _clean((item.get("anchor_of") or "").replace("\n", "\r"))
                spans = _red_spans(anchor_of, anchor) if anchor_of else []
                if spans and spans != [(0, len(anchor))]:
                    red_at = spans[0][0]
            # ★형광펜으로 표시한 자리의 문단 좌표(apply가 돌려준 것) — 빨강 대신 형광펜을
            #   쓴 자리는 `red_only`에 걸러지므로, 여기 적힌 문단에서 잡히면 우리 자리로 본다.
            pen_paras = [tuple(x[:2]) for x in (item.get("pen_paras") or []) if len(x) >= 2]
            pen_used = []
            matches = []
            err = ""
            if anchor:
                try:
                    for _pass in range(self._MEMO_MAX_PASSES):
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
                        pset = self.hwp.HParameterSet.HFindReplace
                        self.hwp.HAction.GetDefault("FindDlg", pset.HSet)
                        self.hwp.HAction.Execute("FindDlg", pset.HSet)
                        pset.FindString    = anchor
                        pset.IgnoreMessage = 1
                        pset.Direction     = 0
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

                        before = len(matches)
                        reskip_left = before
                        prev_pos = None
                        finds = 0
                        while len(matches) < limit:
                            if not self.hwp.HAction.Execute("RepeatFind", pset.HSet):
                                break
                            # ⚠ 생존 신호는 **찾기 횟수**로 낸다. '교정본+메모'의 앵커는 흔한
                            #   교정문이라 빨갛지 않은 매치를 수천 번 건너뛰는데, 받은 매치로만
                            #   세면 그동안 출력이 끊겨 부모의 유휴 타임아웃(_send_cmd)이
                            #   '한/글 행업'으로 오판한다.
                            finds += 1
                            if finds % 200 == 0:
                                _send_progress(int(idx / max(1, total) * 100),
                                               f"메모 자리 찾는 중… {idx + 1}/{total}")
                            try:
                                cur = self.hwp.GetPos()
                            except Exception:
                                cur = None
                            if not (isinstance(cur, tuple) and len(cur) >= 3):
                                break
                            if cur == prev_pos:      # 같은 자리를 맴돈다
                                break
                            prev_pos = cur
                            # 이번 세션에서 우리가 단 메모 본문 — 2단계 설계에선 생기지 않지만
                            #   방어로 남긴다(원고의 등장이 아니다).
                            if cur[0] in self._memo_stories:
                                continue
                            # ★빨강 필터 — 우리가 고치지 않은 자리는 **등장이 아니다**.
                            if red_only and not self._match_is_red(red_at, len(anchor)):
                                if not self._consume_pen_para(pen_paras, pen_used):
                                    continue
                            if reskip_left > 0:     # 앞 패스에서 이미 센 매치
                                reskip_left -= 1
                                continue
                            # 찾기 직후 커서 = 매치 **끝** → 시작은 앵커 길이만큼 앞이다.
                            matches.append([cur[0], cur[1], cur[2] - len(anchor),
                                            len(anchor), self._page_no()])
                        if len(matches) >= need or len(matches) == before:
                            break
                except Exception as exc:
                    err = f"메모 자리 찾기 실패: {exc}"
            out.append({"matches": matches, "error": err})
            if (idx + 1) % 5 == 0 or idx == total - 1:
                _send_progress(int(((idx + 1) / max(1, total)) * 100),
                               f"메모 자리 찾는 중… {idx + 1}/{total}")
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        return {"scan": out}

    def memo_keys(self, points):
        """서브스토리 자리의 **본문 기준 위치** — 메모를 문서 뒤쪽부터 달기 위한 정렬 키.

        points: [[list, para, pos], …] → {"keys": [[본문 para, 본문 pos] | None, …]}
        본문(list 0)은 자기 좌표 그대로다. 표 셀·글상자 같은 서브스토리는 `CloseEx`로
        본문까지 빠져나와 그 컨트롤이 놓인 자리를 읽는다(중첩이면 여러 번).
        ⚠ list id는 문서 순으로 매겨지고 메모 하나가 뒤쪽 id를 +2씩 민다(머리말 불변식⑤) —
          그래서 '본문 어디에 붙은 서브스토리인가'를 알아야 뒤에서부터 달 수 있다.
        ⚠ 본문까지 못 빠져나오면(머리말 등) None — 호출 측이 맨 앞(가장 먼저 처리)에 둔다.
          그런 스토리는 대개 메모를 거부해 다른 자리를 밀지 않는다.
        """
        keys = []
        for pt in points or []:
            try:
                L, P, s = int(pt[0]), int(pt[1]), int(pt[2])
            except Exception:
                keys.append(None)
                continue
            if L == 0:
                keys.append([P, s])
                continue
            key = None
            try:
                self.hwp.SetPos(L, P, s)
                for _ in range(8):
                    try:
                        self.hwp.HAction.Run("CloseEx")
                    except Exception:
                        break
                    q = self.hwp.GetPos()
                    if isinstance(q, tuple) and len(q) >= 3 and q[0] == 0:
                        key = [q[1], q[2]]
                        break
            except Exception:
                key = None
            keys.append(key)
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        return {"keys": keys}

    def _selected_text(self):
        """지금 선택 블록의 글자(못 읽으면 None) — 메모를 달기 전 자리 확인용.

        ⚠ `GetTextFile("TEXT")`는 일부 기호를 **HTML 숫자 참조로** 돌려준다(실측 2026-10-17:
          '≈' → '&#8776;', 목차 점선 '･' → '&#65381;'). 풀지 않으면 맞는 자리가 '글자가
          다르다'로 걸러진다.
        """
        try:
            t = self.hwp.GetTextFile("TEXT", "saveblock:true")
        except Exception:
            return None
        if not isinstance(t, str):
            return None
        return _clean(html.unescape(t).replace("\r\n", "\r").replace("\n", "\r"))

    def memo_place(self, spots, mark_anchor=False):
        """메모 2단계 — 좌표로 곧장 가서 메모를 단다(**찾기 0회**).

        spots: [{"pos": [list, para, 시작, 길이], "anchor", "memo_text", "color"}, …]
               ⚠ 호출 측이 **문서 뒤쪽부터** 정렬해 보낸다(머리말 불변식⑤).
        반환: {"placed": [{"ok", "page", "blocked", "note"}, …], "poisoned": bool}
              placed는 spots와 같은 순서이고, 세션 오염을 만나면 그 자리까지만 담겨 짧아진다.

        ★자리 확인(불변식⑥) — 좌표로 앵커 길이만큼 선택한 뒤 ① 끝 위치가 계산대로이고
          ② 선택 글자가 앵커와 같을 때만 단다. 어긋나면 달지 않는다.
        ⚠ `mark_anchor`(앵커 글자색)는 기본 **꺼짐**이다(사용자 지정 2026-08-07: 앵커
          글자색을 바꾸지 말 것). 메모 모드의 약속은 원고를 손대지 않는 것이다.
        """
        if getattr(self, "_memo_stories", None) is None:
            self._memo_stories = set()
        placed = []
        poisoned = False
        total = len(spots)
        for idx, sp in enumerate(spots):
            try:
                L, P, s, n = (int(v) for v in sp["pos"][:4])
            except Exception:
                placed.append({"ok": False, "page": None, "blocked": True,
                               "note": "메모 자리 좌표가 없습니다 (수동 확인 필요)"})
                continue
            anchor = _clean((sp.get("anchor") or "").replace("\n", "\r"))
            body = _clean(sp.get("memo_text") or "").replace("\n", "\r")
            self._normalize_edit_state()
            now = None
            try:
                self.hwp.SetPos(L, P, s)
                for _ in range(n):
                    self.hwp.HAction.Run("MoveSelRight")
                now = self.hwp.GetPos()
            except Exception:
                now = None
            got = self._selected_text() if now is not None else None
            # ⚠ 자동 글머리표 문단에서는 선택을 내보내면 **글머리표가 앞에 딸려 나온다** —
            #   문단 어디를 골라도 그렇다(실측 hwpx: '◯ 주요내용'·'∙ 스마일'. 글머리표는
            #   글자가 아니라 문단 속성이라 좌표에는 없다). 끝 위치가 시작 + 앵커 길이로
            #   정확하면 고른 글자는 정확히 앵커 길이이므로, 내보낸 글이 앵커로 **끝나면**
            #   고른 글자가 곧 앵커다(앞은 글머리표일 수밖에 없다).
            text_ok = got is None or got == anchor or got.endswith(anchor)
            if (not isinstance(now, tuple) or tuple(now[:3]) != (L, P, s + n)
                    or not text_ok):
                try:
                    self.hwp.HAction.Run("Cancel")
                except Exception:
                    pass
                if isinstance(now, tuple) and tuple(now[:3]) == (L, P, s) and L != 0:
                    # 커서가 한 칸도 못 움직인다 — 찾기·치환은 닿지만 편집 커서는 못 드는
                    #   스토리다(실측: 장 제목을 되풀이하는 머리말류). 메모도 달 수 없다.
                    note = (f"이 자리에는 메모를 달 수 없습니다 (머리말·꼬리말 등 story {L})")
                else:
                    note = ("메모를 달 자리의 글자가 예상과 달라 달지 않았습니다 "
                            "(겹치는 교정 등 — 수동 확인 필요)")
                placed.append({"ok": False, "page": None, "blocked": True, "note": note})
                continue
            pg = self._page_no()
            ok, why = self._insert_memo_here(
                body, sp.get("color", 255) if mark_anchor else None, original=anchor)
            try:
                self.hwp.HAction.Run("Cancel")
            except Exception:
                pass
            placed.append({"ok": bool(ok), "page": pg, "blocked": not ok, "note": why})
            if not ok and self._is_session_fault(why):
                # ★세션이 오염됐다 — 여기서 멈춘다. 이대로 이어 가면 남은 자리가 **전부**
                #   같은 식으로 죽는다. 호출 측이 문서를 다시 열고 나머지를 보낸다
                #   (`HwpEditor.insert_memos`). 뒤쪽부터 달고 있으므로 남은 자리 좌표는
                #   그대로 유효하다.
                poisoned = True
                break
            if (idx + 1) % 5 == 0 or idx == total - 1:
                _send_progress(int(((idx + 1) / max(1, total)) * 100),
                               f"메모 다는 중… {idx + 1}/{total}")
        try:
            self.hwp.HAction.Run("MoveDocBegin")
        except Exception:
            pass
        return {"placed": placed, "poisoned": poisoned}

    # ══════════════════════════════════════════════════════════
    # ▌PDF 내보내기 (결과물 축 output_mode="pdf")
    # ══════════════════════════════════════════════════════════
    def export_pdf(self, output_path):
        """현재 문서를 PDF로 내보낸다 (문서 무변경).

        ⚠★**선택 영역이 살아 있으면 그 부분만 저장된다.** 실측(2026-08-07): 찾기 직후
          (선택 상태)에 저장했더니 17쪽 문서가 **1쪽 12,854B**로 나왔는데 반환값은
          정상과 똑같은 True였다. 예외도 경고도 없는 조용한 손실이라, 저장 직전
          서브스토리 탈출 + `MoveDocBegin`으로 **선택을 반드시 해제**한다.
        """
        for _ in range(3):
            try:
                if not self.hwp.HAction.Run("CloseEx"):
                    break
            except Exception:
                break
        try:
            self.hwp.HAction.Run("MoveDocBegin")     # ★선택 해제 — 위 경고 참조
        except Exception:
            pass
        try:
            self.hwp.HAction.Run("Cancel")
        except Exception:
            pass

        # ★성공 판정은 '파일이 있다'가 아니라 **'이번에 새로 써졌다'**다. 예전엔 존재만
        #   봤는데, 이전 실행의 PDF가 뷰어에 열려(잠겨) 있으면 아래 삭제가 실패하고
        #   한/글도 덮어쓰지 못한다 — 그런데 옛 파일이 그대로 있으니 '변환 성공'으로
        #   보고했고, 주석 단계는 **이미 주석이 달린 옛 PDF**를 다시 읽었다(실측 2026-10-16).
        before = _file_stamp(output_path)
        locked = False
        if before is not None:
            try:
                os.remove(output_path)
            except Exception:
                locked = True
                _send_log("[PDF] 이전 PDF를 지우지 못했습니다 — 다른 프로그램이 열고 있을 수 있음")

        # 한/글 2010에서 세 경로 모두 17쪽 전체를 정상 출력했다(실측). 앞의 것이
        #   실패하는 버전이 있을 수 있어 순서대로 폴백한다.
        attempts = (
            ("FileSaveAs_S",  0),
            ("FileSaveAsPdf", 16384),
        )
        last = ""
        for action, attrs in attempts:
            try:
                pset = self.hwp.HParameterSet.HFileOpenSave
                self.hwp.HAction.GetDefault(action, pset.HSet)
                pset.filename = output_path
                pset.Format   = "PDF"
                try:
                    pset.Attributes = attrs
                except Exception:
                    pass
                self.hwp.HAction.Execute(action, pset.HSet)
            except Exception as exc:
                last = f"{action}: {exc}"
            if _written(output_path, before):
                _send_log(f"[PDF] {action} 성공 — {os.path.getsize(output_path)}B")
                return {"pdf": output_path, "size": os.path.getsize(output_path)}

        try:
            self.hwp.SaveAs(output_path, "PDF", "")
        except Exception as exc:
            last = f"SaveAs: {exc}"
        if _written(output_path, before):
            _send_log(f"[PDF] SaveAs 성공 — {os.path.getsize(output_path)}B")
            return {"pdf": output_path, "size": os.path.getsize(output_path)}

        if locked:
            raise RuntimeError(
                f"PDF 파일을 덮어쓸 수 없습니다 — '{os.path.basename(output_path)}'이(가) "
                "다른 프로그램(PDF 뷰어 등)에 열려 있으면 닫고 다시 시도하세요.")
        raise RuntimeError(
            "PDF로 저장하지 못했습니다. 한/글에 PDF 변환 기능(한컴 PDF)이 설치돼 "
            f"있는지 확인하세요. 마지막 오류: {last or '알 수 없음'}"
        )

    def save_as(self, output_path):
        # HWP 버전별 SaveAs 시그니처 차이를 흡수.
        # HWP 2010 (8.5.x)은 `SaveAs(Path, Format, arg)` 3개 인자를 요구하며,
        # 1개만 주면 DISP_E_BADPARAMCOUNT (-2147352562)로 실패한다.
        # Open과 동일한 폴백 패턴 적용.
        #
        # ★★예외가 없다고 저장된 게 아니다(실측 2026-10-16). 대상 파일이 다른 한/글에
        #   열려 있으면 `SaveAs`는 예외 없이 **False**를 돌려주고 파일은 그대로다. 예전엔
        #   반환값을 보지 않아 '저장 완료'로 보고했고, 사용자는 **이전 실행의 교정본**을
        #   새 결과로 받았다. 게다가 문서가 저장되지 않은 채(dirty) 닫히면서 원본 파일에
        #   대한 '저장할까요?' 모달이 좀비 한/글에 남았다(→ close의 Clear(1)).
        #   그래서 반환값 False와 '파일이 이번에 새로 써졌는가'를 둘 다 확인한다.
        ext = os.path.splitext(output_path)[1].lower()
        fmt = _HWP_FORMAT_BY_EXT.get(ext, "")

        before = _file_stamp(output_path)
        last = ""
        for args, label in (
            ((output_path, fmt, ""), "3-args (HWP 2010 표준)"),
            ((output_path, fmt),     "2-args"),
            ((output_path,),         "1-arg"),
        ):
            try:
                ret = self.hwp.SaveAs(*args)
            except Exception as exc:
                last = f"{label}: {exc}"
                _send_log(f"[저장] SaveAs 실패 — {last}")
                continue
            if ret is not False and _written(output_path, before):
                _send_log(f"[저장] SaveAs 성공 — {label}, fmt={fmt or 'auto'}")
                return {"saved": output_path}
            last = f"{label}: 반환값 {ret!r} · 파일이 갱신되지 않음"
            _send_log(f"[저장] SaveAs 실패 — {last}")

        if before is not None and not _written(output_path, before):
            raise RuntimeError(
                f"'{os.path.basename(output_path)}'을(를) 저장하지 못했습니다 — 같은 이름의 "
                "파일이 한/글 등 다른 프로그램에 열려 있으면 닫고 다시 시도하세요.")
        raise RuntimeError(f"HWP SaveAs 모든 시그니처 시도 실패: {last}")

    def close(self):
        # 워처가 아직 살아있으면(open 중 예외 등) 정리
        if self._hider is not None:
            try:
                self._hider.stop()
            except Exception:
                pass
            self._hider = None
        if self.hwp:
            # ★변경을 **버리고** 닫는다(Clear(1) = hwpDiscard). 저장하지 않은(저장에
            #   실패했거나 취소된) 문서를 그대로 Quit하면 한/글이 **원본 파일**에 대한
            #   '저장할까요?' 모달(기본 버튼 '저장')을 띄운 채 남는다 — 브리지가 15초 뒤
            #   kill되면 그 모달이 주인 없는 창으로 화면에 떠 있고, '저장'을 누르면 원고
            #   원본이 교정 내용으로 덮어써진다(실측 2026-10-16). 저장할 것은 이미
            #   save_as가 저장했으므로 여기서 버릴 것은 없다.
            try:
                self.hwp.Clear(1)
            except Exception:
                pass
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
    # ⚠ 첫 명령을 읽기 **전에** 낸다 — 이 줄이 없으면 부모는 '워커가 아예 못 떴다'와
    #   '워커는 떴는데 COM이 막혔다'를 구분할 수 없다(둘 다 그냥 침묵이다).
    try:
        _send_log(f"[브리지] 워커 시작 (pid {os.getpid()}, "
                  f"{8 * ctypes.sizeof(ctypes.c_void_p)}비트)")
    except Exception:
        pass
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
                result = bridge.apply(req["corrections"],
                                      char_styles=req.get("char_styles"))
            elif cmd == "memo_scan":
                result = bridge.memo_scan(req["items"])
            elif cmd == "memo_keys":
                result = bridge.memo_keys(req["points"])
            elif cmd == "memo_place":
                result = bridge.memo_place(req["spots"],
                                           mark_anchor=req.get("mark_anchor", False))
            elif cmd == "export_pdf":
                result = bridge.export_pdf(req["output_path"])
            elif cmd == "verify":
                result = bridge.verify(req["originals"])
            elif cmd == "locate":
                result = bridge.locate(req["originals"],
                                       budget=req.get("budget", 4000),
                                       time_budget=req.get("time_budget", 60.0))
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
