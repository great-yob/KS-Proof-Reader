"""
ui/main_window.py — 단일 워크스페이스 셸
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
헤더 · 좌측 스텝퍼 레일 · 중앙 스테이지(QStackedWidget) · 우측 활동 로그 ·
하단 상태바를 영구 배치한다. 중앙만 컨텍스트에 따라 교체되고, 레일/로그/
푸터는 전 단계에 걸쳐 전역 상태를 반영한다(이전 단계 진행상황 · 누적 로그
한눈에 확인 — 사용자 지적 ①② 해소).

core/ 엔진과 워커 시그널 계약은 변경하지 않고 그대로 소비한다.
"""

import os
import sys
import ctypes

try:
    from ctypes.wintypes import MSG as _MSG     # 프레임리스 NCHITTEST용(Windows)
except Exception:
    _MSG = None

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QMessageBox, QApplication, QAbstractButton, QSizePolicy,
)

from ui.widgets.app_header import AppHeader
from ui.widgets.sidebar import Sidebar
from ui.widgets.activity_panel import ActivityPanel
from ui.widgets.status_footer import StatusFooter
from ui.widgets.setup_panel import SetupPanel
from ui.widgets.running_panel import RunningPanel
from ui.widgets.review_panel import ReviewPanel
from ui.widgets.result_panel import ResultPanel
from ui.workers.proofreading_worker import ProofreadingWorker
from ui.workers.apply_worker import ApplyWorker
from ui.styles import theme
from core import ConfigLoader


# ── 프레임리스 창 컨트롤(Win32 NCHITTEST) 상수 ───────────────
_WM_NCHITTEST = 0x0084
_HTCLIENT, _HTCAPTION = 1, 2
_HTLEFT, _HTRIGHT, _HTTOP = 10, 11, 12
_HTTOPLEFT, _HTTOPRIGHT = 13, 14
_HTBOTTOM, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 15, 16, 17
_RESIZE_BORDER = 6     # 가장자리 리사이즈 감지 폭(px)
_DRAG_ZONE_H   = 53    # 상단 드래그 영역 높이(헤더 높이와 동일)

# ── DWM 윈도우 스타일 상수 (Win11 라운드 모서리 · 테두리 · 그림자) ──
_DWMWA_WINDOW_CORNER_PREFERENCE = 33   # DWM 라운드 모서리 속성
_DWMWCP_ROUND = 2                       # 표준 라운드 모서리 (Win11 기본 ~8px)
_DWMWA_BORDER_COLOR = 34               # DWM 테두리 색상 속성 (COLORREF: 0x00BBGGRR)


class _MARGINS(ctypes.Structure):
    """DwmExtendFrameIntoClientArea용 MARGINS 구조체."""
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KS-AI Editor")
        # 작업표시줄/Alt-Tab 아이콘 — 프레임리스라도 최상위 창 아이콘은 여기서 잡힌다.
        try:
            from ui.styles.icons import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        # 네이티브 타이틀바 제거 — 창 컨트롤은 헤더가 직접 제공(프레임리스)
        self.setWindowFlag(Qt.FramelessWindowHint, True)

        # ⚠ `config.ini [APP] WIDTH/HEIGHT`는 **시작 크기이자 최소 크기**다(2026-08-06).
        #   예전엔 최소가 960×600으로 따로 박혀 있어, 설정값과 무관하게 창을 그보다
        #   작게 줄일 수 있었다. 설정 화면은 좌우 1:1 고정에 부가 기능 제목이 한 줄
        #   고정이라 칸 최소폭이 520px이고, 창이 1270 밑으로 가면 설정 칸에 가로
        #   스크롤바가 생긴다 — '레이아웃이 성립하는 최소'와 '줄일 수 있는 최소'가
        #   달랐던 것이 원인이라 둘을 하나로 묶는다.
        #   ⚠ 단, 화면보다 큰 최소치는 창을 아예 못 쓰게 만든다(작은 노트북·원격 데스크톱)
        #     → 사용 가능한 화면 크기로 클램프한다. 그 경우에만 스크롤바가 다시 등장한다.
        self._config = ConfigLoader()
        try:
            w, h = self._config.get_window_size()
        except Exception:
            w, h = 1400, 900
        min_w, min_h = w, h
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            min_w = min(min_w, avail.width())
            min_h = min(min_h, avail.height())
        self.setMinimumSize(min_w, min_h)
        self.resize(max(w, min_w), max(h, min_h))

        self._file_path = ""
        self._options = {}
        self._corrections = []
        self._extracted_text = ""
        self._page_count = None
        self._footnote_lines = []   # **실제 각주** 라인 인덱스(미리보기 [각주] 표지용)
        # 표기 일관성 단계를 이미 거쳤는가 — 한 문서에 한 번만 끼운다(통일 후 다시 갈리면
        #   그건 사용자가 방금 내린 결정이므로 되묻지 않는다).
        self._consistency_done = False
        self._consistency_logged = False
        self._result = {}
        self._worker = None
        self._apply_worker = None
        # 결과 화면의 '결과물 추가' 실행 — 1차 적용과 같은 ApplyWorker를 쓰되 별도
        #   슬롯에 둔다(1차 결과 dict를 덮어쓰면 안 되므로 완료 핸들러가 다르다).
        self._extra_worker = None
        self._extra_mode = ""
        # 1차 적용에 넘긴 등장별 결정 스냅숏 — 추가 산출물이 **같은 결정**을 쓰게 한다.
        self._applied_occ_rows = None
        self._sync_workers = []   # 공유 용어 뇌 동기화 워커(단명, fire-and-forget)
        self._phase = "setup"

        self._build_ui()
        self._setup_dwm_style()   # Win11 라운드 모서리 + 테두리 + 그림자
        self._wire()
        self._show_phase("setup")

        # 사내 계정 — 로그인은 **실행 게이트**라 여기 도달했다는 건 이미 통과했다는 뜻이다
        #   (main.py require_login). 여기서는 세션을 헤더에 반영하고 공유 용어 뇌
        #   동기화·큐레이션을 활성화하기만 한다.
        self._curator_panel = None
        self._login_dialog = None
        self._start_session_restore()

        # 자동 업데이트 — **확인만** 자동, 설치는 사용자 클릭(core/updater.py 규율).
        self._update_info = {}        # {channel: info} — 확인된 새 릴리스
        self._update_worker = None
        self._update_dialog = None
        self._update_prompted = False  # 이번 실행에서 모달을 이미 띄웠는가
        self._start_update_check()

    # ══════════════════════════════════════════════
    # DWM 네이티브 스타일 — 라운드 모서리 · 테두리선 · 그림자
    # ══════════════════════════════════════════════
    def _setup_dwm_style(self):
        """Windows 11 DWM API로 기본 폴더(탐색기)와 동일한
        라운드 모서리, 테두리선, 드롭 섀도우를 적용한다.
        Win10 이하에서는 조용히 무시."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi

            # ① 라운드 모서리 — DWMWCP_ROUND (Win11 기본 8px 라운드)
            corner = ctypes.c_int(_DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner), ctypes.sizeof(corner))

            # ② 테두리선 — 현재 테마에 맞는 색상 적용
            self._update_dwm_border()

            # ③ 그림자 — 프레임리스 창의 네이티브 드롭 섀도우 복원
            margins = _MARGINS(-1, -1, -1, -1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass   # Win10 이하 또는 DWM 미사용 환경 — 조용히 무시

    def _update_dwm_border(self):
        """현재 테마 모드에 맞는 DWM 테두리 색상 적용.
        라이트: 탐색기 기본 연한 회색 rgb(204,204,204)
        다크:   앱 border 토큰과 동일한 어두운 회색 rgb(46,52,61)"""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            # COLORREF: 0x00BBGGRR
            if theme.current_mode() == "dark":
                colorref = 0x003D342E   # rgb(46,52,61) — DARK["border"] #2E343D
            else:
                colorref = 0x00CCCCCC   # rgb(204,204,204) — Win11 탐색기 기본
            border_color = ctypes.c_int(colorref)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_BORDER_COLOR,
                ctypes.byref(border_color), ctypes.sizeof(border_color))
        except Exception:
            pass

    # ══════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        # 루트는 수평: [좌측 사이드바(전체 높이)] | [메인 컬럼]
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 좌측 사이드바 — 로고 + 단계 네비 + 저작권 (헤더/풋터와 분리된 전체 높이 컬럼)
        self.sidebar = Sidebar()
        self.rail = self.sidebar.rail   # 기존 호출부 호환을 위해 노출
        root.addWidget(self.sidebar)

        # 메인 컬럼 — 헤더(상단) + 스테이지/활동로그(중앙) + 풋터(하단)
        main_col = QVBoxLayout()
        main_col.setContentsMargins(0, 0, 0, 0)
        main_col.setSpacing(0)

        self.header = AppHeader()
        main_col.addWidget(self.header)

        from ui.widgets.components import FadingStackedWidget
        self.main_stage = FadingStackedWidget()

        from ui.widgets.setup_panel import FilePanel
        
        # 1. Setup View (설정 단계)
        setup_widget = QWidget()
        setup_layout = QHBoxLayout(setup_widget)
        setup_layout.setContentsMargins(24, 24, 24, 24)
        setup_layout.setSpacing(24)
        
        self.setup_panel = SetupPanel()
        self.file_panel = FilePanel()
        setup_layout.addWidget(self.setup_panel, 1)
        setup_layout.addWidget(self.file_panel, 1)
        # ⚠ 좌우 1:1 **고정** — stretch(1, 1)만으로는 반반이 되지 않는다.
        #   QHBoxLayout은 stretch를 나누기 전에 각 칸의 최소폭(minimumSizeHint)을 먼저
        #   확보하므로, 한쪽 내용이 길어지면 그 칸이 넓어지고 반대쪽이 밀린다.
        #   실사고: 긴 파일명(줄바꿈 없는 QLabel의 최소폭 = 글자 전체 폭)이 문서 선택
        #   칸을 밀어 넓혀, 설정 칸이 잘리고 가로 스크롤바가 생겼다.
        #   가로 정책 Ignored는 sizeHint·최소폭을 0으로 취급시켜 폭이 오직 stretch로만
        #   결정되게 한다 — 내용은 각 패널 안에서 줄이거나(파일명 말줄임) 스크롤한다.
        #   ⚠ 세로 정책은 건드리지 말 것(높이는 그대로 채워야 한다).
        for _panel in (self.setup_panel, self.file_panel):
            _sp = _panel.sizePolicy()
            _sp.setHorizontalPolicy(QSizePolicy.Ignored)
            _panel.setSizePolicy(_sp)
        
        self.main_stage.addWidget(setup_widget) # Index 0: Setup
        
        # 2. Analyze View (분석 단계)
        analyze_widget = QWidget()
        analyze_layout = QHBoxLayout(analyze_widget)
        analyze_layout.setContentsMargins(24, 24, 24, 24)
        analyze_layout.setSpacing(24)
        
        self.activity = ActivityPanel()
        self.running_panel = RunningPanel()
        analyze_layout.addWidget(self.activity, 1)
        analyze_layout.addWidget(self.running_panel, 1)
        
        self.main_stage.addWidget(analyze_widget) # Index 1: Analyze
        
        self.review_panel = ReviewPanel()
        self.main_stage.addWidget(self.review_panel) # Index 2: Full body review panel
        
        self.result_panel = ResultPanel()
        self.main_stage.addWidget(self.result_panel) # Index 3: Full body result panel
        
        main_col.addWidget(self.main_stage, 1)

        # 풋터 — 메인 컬럼 하단(사이드바와 분리)
        self.footer = StatusFooter()
        main_col.addWidget(self.footer)

        root.addLayout(main_col, 1)

        # 테마 아이콘 초기화
        self.header.set_theme_icon(theme.current_mode())

    # _STAGE_INDEX removed

    def _wire(self):
        self.header.theme_toggled.connect(self._toggle_theme)
        self.header.new_file_requested.connect(self._reset)
        self.header.minimize_requested.connect(self.showMinimized)
        self.header.maximize_requested.connect(self._toggle_maximize)
        self.header.close_requested.connect(self.close)
        self.header.curator_requested.connect(self._open_curator_panel)
        self.header.login_requested.connect(self._open_login_dialog)
        self.header.logout_requested.connect(self._logout)

        self.rail.step_clicked.connect(self._on_rail_click)
        self.sidebar.update_clicked.connect(self._open_update_dialog)

        self.file_panel.file_selected.connect(self._on_file_selected)
        self.setup_panel.options_changed.connect(self._refresh_setup_footer)

        self.review_panel.counts_changed.connect(self._on_review_counts)
        self.review_panel.consistency_counts_changed.connect(self._on_consistency_counts)

        self.result_panel.extra_output_requested.connect(self._start_extra_output)
        self.result_panel.output_open_requested.connect(self._open_path_folder)

        self.footer.primary_clicked.connect(self._on_primary)
        self.footer.cancel_clicked.connect(self._on_cancel)
        self.footer.reset_clicked.connect(self._reset)
        self.footer.recheck_clicked.connect(self._enter_consistency)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ══════════════════════════════════════════════
    # 단계 전환
    # ══════════════════════════════════════════════
    def _show_phase(self, phase: str):
        self._phase = phase
        if phase == "review":
            self.main_stage.setCurrentIndex(2)
        elif phase == "result":
            self.main_stage.setCurrentIndex(3)
        elif phase == "running":
            self.main_stage.setCurrentIndex(1)
        else:
            self.main_stage.setCurrentIndex(0)

        if phase == "setup":
            self.rail.set_phase("setup")
            self._refresh_setup_footer()
        elif phase == "running":
            # rail은 호출부에서 analyze/done 지정
            self.footer.set_busy("처리 중…", "처리 중")
        elif phase == "review":
            self.rail.set_phase("review")
            # 푸터 primary는 counts_changed가 갱신
            self.footer.set_idle("교정 제안 검토 중")
            self._on_review_counts(
                *self._count_review())
        elif phase == "result":
            self.rail.complete_all()
            self.footer.set_idle("교정 완료")
            # ⚠ 완료 단계 푸터에 남는 액션은 **초기화 하나뿐**이다(사용자 지시
            #   2026-08-11). 산출물을 열고·더 만드는 일은 결과 화면의 '산출물'
            #   카드가 전담한다 — 같은 행동을 푸터에 한 벌 더 두면 '정오표 열기'가
            #   어느 정오표(1차/메모/PDF)를 여는지 말할 수 없다.
            self.footer.set_primary("수정된 HWP 열기",
                                    variant="primary", enabled=False, visible=False, show_reset=True)

    def _refresh_setup_footer(self):
        if self._phase != "setup":
            return
            
        has_file = self.file_panel.has_file()
        has_scope = self.setup_panel.scopes_selected()
        can_start = has_file and has_scope
        
        if not has_file:
            msg = "옵션을 설정하고, 교정할 한글 원고 파일을 선택하세요"
        elif not has_scope:
            msg = "교정 범위를 1개 이상 선택하세요"
        else:
            msg = f"설정: {self.setup_panel.summary_text()}"
            
        self.footer.set_idle(msg)
        self.footer.set_primary("교정 분석 시작", enabled=can_start, visible=True, show_reset=True)

    def _count_review(self):
        # 등장(카드) 단위 카운트 — 부분 거절 반영
        return self.review_panel.get_counts()

    # ══════════════════════════════════════════════
    # 파일 선택
    # ══════════════════════════════════════════════
    def _on_file_selected(self, file_path: str):
        # 파일 용량 경고 팝업 삭제됨

        self._file_path = file_path
        
        if not file_path:
            self.file_panel.set_file("")
            self.activity.log("파일 선택 취소됨")
            if self._phase != "setup":
                self._reset()
            else:
                self._refresh_setup_footer()
            return
            
        name = os.path.basename(file_path)
        self.file_panel.set_file(file_path)
        self.activity.log(f"파일 선택: {name}")
        self._show_phase("setup")

    # ══════════════════════════════════════════════
    # 푸터 1차 액션 (단계별)
    # ══════════════════════════════════════════════
    def _on_primary(self):
        if self._phase == "setup":
            self._start_analysis()
        elif self._phase == "running":
            self._show_phase("review")
        elif self._phase == "review":
            # 검토 단계 안의 3단 전환: 교정 검토 → (표기 일관성 검토 → 적용) → 교정 적용.
            if self.review_panel.in_consistency_mode():
                self._apply_consistency()
            elif not self._consistency_done and self._count_review()[0] == 0 \
                    and self.review_panel.consistency_families():
                self._enter_consistency()
            else:
                self._start_apply()
        elif self._phase == "result":
            self._reset()

    # ── 분석 시작 ───────────────────────────────
    def _start_analysis(self):
        if not self._file_path or not self.setup_panel.scopes_selected():
            return
        self._options = self.setup_panel.get_options()
        self._consistency_done = False     # 새 분석 = 표기 일관성 단계도 다시
        self._consistency_logged = False

        self.running_panel.set_title("교정 분석 중")
        self.running_panel.set_detail("잠시만 기다려주세요…")
        self.running_panel.set_animating(True)
        self._show_phase("running")
        self.rail.set_phase("analyze")
        self.footer.set_busy("분석 준비 중…", "분석 중")
        self.activity.log("교정 분석을 시작합니다…")

        self._cleanup_worker("_worker")
        self._worker = ProofreadingWorker(self._file_path, self._options, parent=self)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.log_message.connect(self.activity.log)
        self._worker.step_changed.connect(self._on_step_changed)
        self._worker.text_extracted.connect(self._on_text_extracted)
        self._worker.page_count_extracted.connect(self._on_page_count)
        self._worker.footnote_lines_extracted.connect(self._on_footnote_lines)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()
    def _on_worker_progress(self, percent: int, msg: str):
        self.footer.set_progress(percent, msg)
        self.running_panel.set_progress(percent)

    def _on_step_changed(self, step_id: str, message: str):
        self.running_panel.set_detail(message)
        self.footer.set_status(message)

    def _on_text_extracted(self, text: str):
        """텍스트 추출 직후(분석 시작 직전) — 전체 글자 수 확보."""
        self._extracted_text = text

    def _on_footnote_lines(self, footnote_lines: list):
        """**실제 각주** 라인 인덱스 — 미리보기 `[각주]` 표지용(review_panel.load에 전달).

        ⚠ 워커의 `note_lines_extracted`(컨트롤 텍스트 전부)를 여기에 연결하면 글상자·
        표·목차까지 각주로 표시된다 — 그건 AI 가드용이고 표시용이 아니다.
        """
        self._footnote_lines = list(footnote_lines or [])

    def _on_page_count(self, page_count):
        """문서 총 페이지 수(없으면 None) — 완료 대시보드 대표 수치용."""
        self._page_count = page_count
        if page_count:
            self.rail.set_step_result("setup", f"{page_count:,}페이지")

    def _on_analysis_done(self, corrections: list):
        self._corrections = corrections
        detected = len(corrections)
        # 용어 통일: '건'=교정 항목 수(분석·완료), '곳'=본문 등장/치환 위치 수(검토·적용).
        self.activity.log(f"✓ 분석 완료 — 교정 {detected}건")
        self.rail.set_step_result("analyze", f"교정 제안 : {detected}건")

        if not corrections:
            self.activity.log("교정할 항목이 없습니다.")
            QMessageBox.information(self, "분석 완료", "교정할 항목이 없습니다.")
            self._show_phase("setup")
            return

        # 전자동 모드 — confidence=="low"는 자동 거절(출판 사고 방지).
        #   단, 검수 플래그(dict_flag)는 HWP를 수정하지 않으므로 정오표 기록을 위해 수락.
        if self._options.get("auto_apply", False):
            low = 0
            for c in corrections:
                if c.get("source") == "dict_flag":
                    c["status"] = "accepted"
                elif c.get("confidence") == "low":
                    c["status"] = "rejected"
                    low += 1
                else:
                    c["status"] = "accepted"
            if low:
                self.activity.log(f"⚠ 신뢰도 낮음 {low}건은 자동 적용에서 제외(검토 필요)")
                
            self.review_panel.load(corrections, self._options,
                                   os.path.basename(self._file_path),
                                   full_text=self._extracted_text,
                                   footnote_lines=self._footnote_lines)
                                   
            self._start_apply()
            return

        # 수동 검토 모드
        # ⚠ footnote_lines를 빠뜨리면 미리보기 '[각주]' 표지가 통째로 사라진다(오류·로그
        #   없이 조용히 — 실제로 그렇게 새어 나갔다). 전자동 경로와 인자 목록을 같이 볼 것.
        self.review_panel.load(corrections, self._options,
                               os.path.basename(self._file_path),
                               full_text=self._extracted_text,
                               footnote_lines=self._footnote_lines)

        # 교정 N'건'(항목)이 본문 몇 '곳'(등장)에 해당하는지 한 줄로 연결 — 검토 단계의
        #   '수락 X / Y곳' 숫자(Y=등장)가 분석의 '교정 N건'(N=항목)과 왜 다른지 설명.
        _, _, occ_total = self.review_panel.get_counts()
        if occ_total and occ_total != detected:
            # ⚠ 선두 불릿(·)을 쓰지 말 것 — 활동 패널은 불릿 라인을 '개별 항목 상세'로
            #   보고 화면에서 숨긴다(activity_panel._ITEM_RE). 이건 집계 라인이다.
            self.activity.log(f"  → 본문 {occ_total}곳에 해당 (반복 등장 포함)")

        self.running_panel.set_title("분석 완료")
        self.running_panel.set_detail("아래 버튼을 눌러 교정 제안 검토를 시작하세요.", tone="text_success")
        self.running_panel.set_animating(False)
        self.footer.set_idle(f"분석 완료 · 교정 {detected}건")
        self.footer.set_primary("교정 검토 시작", variant="success_solid", visible=True, show_reset=True)

    # ── 검토 카운트 → 푸터/레일 ──────────────────
    def _on_review_counts(self, pending: int, accepted: int, total: int):
        if self._phase != "review" or self.review_panel.in_consistency_mode():
            return
        # 용어 통일: 검토 단계는 '수락'(완료 단계의 '적용'과 구분). 카드=본문 등장이라 '곳'.
        #   ⚠ 'AI 분석 제외' 모드에서도 결정론 교정은 실제 적용되므로 라벨은 공통이다.
        #   순수 검수(치환 0건) 여부는 ApplyWorker가 동적으로 판단해 처리한다.
        self.rail.set_step_result("review", f"수락 : {accepted} / {total}")
        self.footer.set_status(f"사용자 검토 중 — 수락 : {accepted} / {total}항목 · 대기 : {pending}항목")
        # 모든 카드를 결정했고 **복합명사 계열 표기가 갈렸으면** 적용 앞에 '표기 일관성
        #   검토' 단계를 한 번 끼운다(사용자 결정 2026-08-03). 낱말별 다수결은 서로 독립이라
        #   '수익 모델'은 띄우고 '사업모델'은 붙이는 결과가 나올 수 있고, 그대로 나가면
        #   교정본을 사람이 다시 대조해야 한다 — core/consistency_family.py 헤더 참조.
        n_fam = 0
        if pending == 0 and total > 0:
            n_fam = len(self.review_panel.consistency_families())
            # ⚠ 0건일 때도 **한 번은 로그를 남긴다** — 버튼이 안 나오는 이유가 '검사할 게
            #   없어서'인지 '결함인지' 화면에서 구분이 안 됐다(사용자 보고 2026-08-03).
            #   화면 로그 규약: [태그] + n건, 개별 예시 금지.
            if not self._consistency_logged:
                self._consistency_logged = True
                # ⚠ 화면 로그는 34자를 넘으면 ' — ' 앞에서 잘린다(activity_panel._condense)
                #   → **수치를 사유 앞에 두거나 ' — '를 쓰지 말 것**. 예전 문구는 계열이
                #   두 자리(10건 이상)가 되는 순간 '[일관성] 복합명사 계열 검사'만 남아
                #   건수가 통째로 사라졌다(규약: 표시 줄은 반드시 n건을 남긴다).
                self.activity.log(
                    f"[표기 일관성] 복합명사 계열 검사 · 표기 갈린 계열 {n_fam}건")
            if n_fam and not self._consistency_done:
                self.footer.set_status(
                    f"검토 완료 — 수락 : {accepted} / {total}항목 · "
                    f"표기가 갈린 복합명사 계열 {n_fam}건")
                self.footer.set_primary(f"표기 일관성 검토 ({n_fam}건)",
                                        variant="success_solid", enabled=True,
                                        visible=True, show_reset=True)
                self.footer.set_recheck(False)
                return
        # 대기 항목이 있어도 버튼은 활성 — 누르면 _start_apply가 미선택을 막고 에러 팝업.
        self.footer.set_primary(f"✓  교정 적용 ({accepted}항목)", variant="action_pink",
                                enabled=total > 0, visible=True, show_reset=True)
        # 1차 통일 뒤에도 **아직 결정하지 않은** 계열·낱말이 남으면 2단계 재검토를 보조
        #   버튼으로 연다. ⚠ 남은 '갈림' 전체를 세면 안 된다 — 사용자가 '그대로 두기'로
        #   결정한 계열도 갈린 상태로 남으므로, 무엇을 골라도 버튼이 사라지지 않아
        #   교정이 끝나지 않는 화면이 된다(사용자 보고 2026-08-04).
        left = self.review_panel.consistency_pending_count() if self._consistency_done else 0
        self.footer.set_recheck(bool(left), left)

    # ── 표기 일관성 단계 ─────────────────────────
    def _on_consistency_counts(self, pending: int, done: int, total: int):
        if not self.review_panel.in_consistency_mode():
            return
        # 용어: 이 단계의 카드는 '통일/그대로 두기/낱말 표기 고르기'가 섞여 있으므로
        #   공통 어휘는 **결정**이다(수락이 아니다).
        self.rail.set_step_result("review", f"표기 일관성 {done} / {total}")
        self.footer.set_status(
            f"표기 일관성 검토 중 — 결정 : {done} / {total} · 대기 : {pending}")
        self.footer.set_primary(f"✓  표기 일관성 적용 ({done}항목)", variant="action_pink",
                                enabled=True, visible=True, show_reset=True)

    def _enter_consistency(self):
        """'교정 제안' 그리드를 '표기 일관성 제안'으로 전환(2회차 이상 = 재검토)."""
        if self._phase != "review" or self.review_panel.in_consistency_mode():
            return
        n = self.review_panel.enter_consistency_mode()
        if not n:                      # 그 사이 충돌이 사라졌다 → 곧장 적용 단계로
            self._consistency_done = True
            self._on_review_counts(*self._count_review())
            return
        self.footer.set_idle("표기 일관성 검토 중")
        rd = self.review_panel.consistency_round()
        self.activity.log(f"[표기 일관성] {rd}차 검토 시작 · 표기가 갈린 계열 {n}건")
        self._on_consistency_counts(*self.review_panel.get_consistency_counts())

    def _apply_consistency(self):
        """선택한 계열을 통일하고 '교정 제안' 그리드로 복귀."""
        pending, done, total = self.review_panel.get_consistency_counts()
        if pending > 0:
            self._warn_popup(
                "검토가 끝나지 않았습니다",
                f"계열 카드는 '통일(✓)' 또는 '그대로 두기(✕)'를, 낱말 카드는 쓸 표기를\n"
                f"골라야 합니다. 아직 결정하지 않은 항목이 {pending}건 남아 있습니다.")
            return
        rd = self.review_panel.consistency_round()
        n = self.review_panel.apply_consistency()
        self._consistency_done = True
        self.activity.log(
            f"[표기 일관성] {rd}차 결정 {done}건 · 교정 {n}건 방향 조정")
        # ⚠ 겹치는 낱말(두 계열이 다르게 요구)은 **낱말 카드에서 이미 결정됐다** — 여기서
        #   보고할 게 없다. 남는 보고는 '멀쩡한 계열을 깨뜨려 되돌린 낱말'뿐이다(그 계열은
        #   갈리지 않아 카드로도 안 나오니 사용자가 볼 기회조차 없다).
        #   화면엔 집계만, 개별 낱말은 원문 로그에 불릿으로(화면 로그 규약).
        blocked = self.review_panel.get_consistency_blocked()
        if blocked:
            self.activity.log(
                f"[표기 일관성] 다른 계열의 표기가 갈릴까 봐 통일하지 않은 낱말 "
                f"{len(blocked)}건 — 이 단계는 새 혼재를 만들지 않습니다")
            for _w in blocked:
                self.activity.log(f"      · 다른 계열 보호 — 그대로 둠 '{_w}'")
        # 2차 재검토에서는 가드를 풀고 **사용자가 알고** 다른 계열을 가를 수 있다
        #   (카드에 '…계열이 갈립니다'로 표시됨). 결과를 남긴다.
        broken = self.review_panel.get_consistency_broken()
        if broken:
            self.activity.log(
                f"[표기 일관성] 사용자 선택으로 다른 계열이 갈리게 된 낱말 {len(broken)}건")
            for _w in broken:
                self.activity.log(f"      · 사용자 선택으로 다른 계열이 갈림 '{_w}'")
        # 아직 **결정하지 않은** 것이 남으면 재검토 버튼으로 다시 볼 수 있다
        #   (_on_review_counts가 켠다). 이미 결정한 계열은 갈린 채로 남아도 다시 묻지 않는다
        #   — 그래야 사용자에게 '끝'이 있다.
        left = self.review_panel.consistency_pending_count()
        self.activity.log(
            f"[표기 일관성] {rd}차 뒤 결정할 계열·낱말 {left}건"
            + ("" if left else " · 표기 일관성 검토 완료"))
        self.footer.set_idle("교정 제안 검토 중")
        self._on_review_counts(*self._count_review())

    def _warn_popup(self, title: str, message: str):
        """경고 에러 팝업(모달)."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    # ── 적용 시작 ───────────────────────────────
    def _start_apply(self):
        # [#5] 항목별 검토 모드: 모든 제안을 '적용/거절'로 결정해야 적용 가능.
        #   미선택(대기) 카드가 하나라도 남으면 적용을 막고 에러 팝업을 띄운다.
        if self._phase == "review":
            pending, _accepted, _total = self.review_panel.get_counts()
            if pending > 0:
                self._warn_popup(
                    "검토가 끝나지 않았습니다",
                    f"모든 교정 제안을 '적용' 또는 '거절'로 선택해야 합니다.\n"
                    f"아직 결정하지 않은 항목이 {pending}건 남아 있습니다.")
                return

        # 검수 패널이 추가한 교정(예: 일관성 '반대 표기로 통일'의 역방향 교정)을 포함해
        #   최신 목록으로 동기화 — 패널 목록은 load 때 정렬 '사본'이라 패널 쪽 append가
        #   self._corrections엔 반영되지 않는다(상태 변경은 dict 공유라 자동 반영).
        panel_cors = self.review_panel.get_corrections()
        if panel_cors:
            self._corrections = panel_cors

        # 현재 검토 화면(review)에 있다면 running_panel로 전환하지 않고 화면을 유지한다.
        if self._phase == "review":
            self._phase = "apply_running"
        else:
            self.running_panel.set_title("교정 적용 중")
            self.running_panel.set_detail("HWP 파일에 적용하는 중…")
            self.running_panel.set_animating(True)
            self._show_phase("running")

        self.rail.set_phase("done")
        self.footer.set_busy("교정 적용 중…", "적용 중")
        self.activity.log("교정 적용을 시작합니다…")

        # 공유 용어 뇌(DO-3) — 검토/auto_apply 결정을 용어 단위 학습 이벤트로 로컬 큐에
        #   적재한다. 순수 부수효과(실패해도 적용에 영향 0, 문맥 스니펫 미저장).
        self._capture_correction_events()

        self._cleanup_worker("_apply_worker")
        # 등장(occurrence)별 결정을 함께 넘긴다 — 정오표가 '등장 1곳 = 1행'이라
        #   skip_occurrences만으로는 복원할 수 없는 구분(사용자 거절 ↔ 애초에 등장이
        #   아닌 자리)이 필요하다. ReviewPanel.get_occurrence_rows 주석 참조.
        #   ⚠ 스냅숏으로 들고 있는다 — 결과 화면의 '결과물 추가'가 **같은 결정**을
        #     써야 하는데, 그때 패널을 다시 물으면 그 사이 상태가 달라졌을 수 있다.
        self._applied_occ_rows = self.review_panel.get_occurrence_rows()
        self._apply_worker = ApplyWorker(self._file_path, self._corrections,
                                         self._options,
                                         occ_rows=self._applied_occ_rows,
                                         parent=self)
        self._apply_worker.progress.connect(self._on_worker_progress)
        self._apply_worker.log_message.connect(self.activity.log)
        self._apply_worker.finished.connect(self._on_apply_done)
        self._apply_worker.error.connect(self._on_error)
        self._apply_worker.start()

    def _capture_correction_events(self):
        """검토/auto_apply 결정을 용어 단위 학습 이벤트로 로컬 큐(data/event_queue.db)에
        적재한다(공유 용어 뇌 DO-3). 전적으로 부수효과 — 어떤 실패도 교정 적용을 막지
        않는다. 서버 업로드는 없다(DO-4). 문맥 스니펫은 저장하지 않는다(프라이버시).
        """
        try:
            from core import event_queue
            n = event_queue.record_corrections(
                self._corrections,
                doc_type=self._options.get("doc_type"))
            if n:
                self.activity.log(f"  [학습] 교정 결정 {n}건을 사내 용어 학습 큐에 기록")
        except Exception as exc:
            try:
                self.activity.log(f"  [학습] 이벤트 기록 스킵: {exc}")
            except Exception:
                pass
        # 큐에 쌓인 이벤트를 백그라운드로 업로드(미설정/오프라인 시 큐에 보존).
        self._start_sync("push")

    def _start_sync(self, mode: str):
        """공유 용어 뇌 동기화를 백그라운드 스레드로 실행(graceful — 미설정 시 즉시 종료)."""
        try:
            from ui.workers.sync_worker import SyncWorker
            self._sync_workers = [w for w in self._sync_workers if w.isRunning()]
            w = SyncWorker(mode, parent=self)
            w.log_message.connect(self.activity.log)
            w.finished.connect(self._prune_sync_workers)
            self._sync_workers.append(w)
            w.start()
        except Exception:
            pass

    def _prune_sync_workers(self):
        self._sync_workers = [w for w in self._sync_workers if w.isRunning()]

    def _start_session_restore(self):
        """세션 상태를 헤더에 반영하고, 로그인 상태면 동기화를 건다.

        ⚠ 실행 게이트(main.py `require_login`)가 창을 만들기 **전에** 이미 세션을
        복원·검증했다 — 여기서 다시 restore()를 부르면 refresh_token을 한 번 더
        회전시키는 불필요한 왕복이다. 그래서 세션이 있으면 상태 반영만 하고,
        없을 때(개발 우회 KS_SKIP_LOGIN)만 조용히 넘어간다.
        """
        try:
            from core import auth
            user = auth.current_user()
            self._apply_auth_state(user)
            if not user:
                return
            self.activity.log(
                f"  [계정] {user.get('name') or user.get('email')} 로그인됨"
                + ("  (관리자)" if user.get("role") == "admin" else ""))
            if auth.is_offline_session():
                # 오프라인 유예 통과 — 동기화·큐레이션은 자동 no-op(토큰 없음).
                self.activity.log(
                    f"  [계정] 오프라인 모드 — 공유 용어 사전 동기화는 다음 접속 시 "
                    f"(유예 {auth.grace_days_left()}일 남음)")
                return
            self._start_sync("sync")   # 보류 이벤트 push + 최신 스냅샷 pull
        except Exception:
            self._log_auth_error("세션 확인")

    def _open_login_dialog(self):
        """사내 계정 로그인 다이얼로그. 성공 시 동기화·큐레이션 활성.

        게이트를 통과해 들어온 상태에서는 헤더 버튼이 '로그아웃'이므로, 이 경로는
        개발 우회(KS_SKIP_LOGIN)로 무세션 실행 중일 때만 열린다.
        """
        try:
            from ui.widgets.login_dialog import LoginDialog
            # 모달 수명 동안 강한 참조 유지 — 워커 스레드가 다이얼로그 GC와 함께
            #   파괴되며 죽는 race 방지.
            self._login_dialog = LoginDialog(self)
            self._login_dialog.logged_in.connect(self._on_logged_in)
            self._login_dialog.exec()
            self._login_dialog = None
        except Exception as exc:
            self.activity.log(f"  [계정] 로그인 창 열기 실패: {exc}")

    def _on_logged_in(self, user):
        try:
            self._apply_auth_state(user)
            self.activity.log(
                f"  [계정] 로그인 — {user.get('name') or user.get('email')}"
                + ("  (관리자)" if user.get("role") == "admin" else ""))
            self._start_sync("sync")
        except Exception:
            self._log_auth_error("로그인 후 처리")

    def _logout(self):
        """로그아웃 후 **다시 게이트를 세운다** — 로그인이 앱 사용 조건이므로 무세션
        상태로 창을 남겨 둘 수 없다. 사용자가 로그인 창에서 종료를 택하면 앱을 닫는다
        (계정 전환은 여기서 그대로 가능).
        """
        try:
            from core import auth
            auth.logout()
            self._apply_auth_state(None)
            if getattr(self, "_curator_panel", None) is not None:
                try:
                    self._curator_panel.close()
                except Exception:
                    pass
                self._curator_panel = None
            self.activity.log("  [계정] 로그아웃")
        except Exception:
            self._log_auth_error("로그아웃")
            return
        try:
            from ui.widgets.login_dialog import require_login
            user = require_login(self)
            if user is None:
                self.close()
                return
            self._apply_auth_state(auth.current_user())
            self.activity.log(
                f"  [계정] 로그인 — {user.get('name') or user.get('email')}"
                + ("  (관리자)" if user.get("role") == "admin" else ""))
            self._start_sync("sync")
        except Exception:
            self._log_auth_error("재로그인")
            self.close()

    def _log_auth_error(self, where: str):
        """인증 관련 슬롯의 예외를 로그로 흡수 — 선택적 로그인이 앱을 종료시키지 않게 한다."""
        import traceback
        try:
            self.activity.log(f"  [계정] {where} 중 오류(무시·교정 기능엔 영향 없음):\n{traceback.format_exc()}")
        except Exception:
            pass

    def _apply_auth_state(self, user):
        """헤더 로그인 표시 + 관리자면 큐레이션 버튼 노출."""
        self.header.set_auth_state(user)
        self.header.set_curator_visible(bool(user) and user.get("role") == "admin")

    def _open_curator_panel(self):
        """사내 용어 큐레이션 패널(관리자) 열기."""
        try:
            from ui.widgets.curator_panel import CuratorPanel
            if getattr(self, "_curator_panel", None) is not None:
                try:
                    self._curator_panel.raise_()
                    self._curator_panel.activateWindow()
                    return
                except Exception:
                    self._curator_panel = None
            self._curator_panel = CuratorPanel(self)
            self._curator_panel.finished.connect(lambda *_: setattr(self, "_curator_panel", None))
            self._curator_panel.show()
            self._curator_panel.raise_()
            self._curator_panel.activateWindow()
            self.activity.log("  [큐레이션] 패널 열기")
        except Exception as exc:
            import traceback
            self.activity.log(f"  [큐레이션] 패널 열기 실패: {exc}\n{traceback.format_exc()}")

    def _on_apply_done(self, result: dict):
        self._result = result
        applied  = result.get("applied", 0)
        occ      = result.get("occurrences", 0)
        failed   = result.get("failed", 0)
        flagged  = result.get("flagged", 0)

        # 완료 대시보드 — 파이프라인/차트용 부가 데이터(제안 목록·쪽수·글자 수·문서명) 동봉.
        char_count = None
        if getattr(self, "_extracted_text", None):
            char_count = len(self._extracted_text.replace("\n", "").replace(" ", ""))
        self.result_panel.show_result(
            result, self.activity.get_proofreading_log(),
            corrections=self._corrections,
            char_count=char_count,
            page_count=getattr(self, "_page_count", None),
            file_name=os.path.basename(self._file_path) if self._file_path else "")
        out_mode = result.get("output_mode") or (
            "errata" if result.get("errata_only") else "hwp")
        if out_mode != "hwp":
            # 한글 파일을 열되 고치지 않은 결과물 3종(정오표만·메모·PDF 주석).
            #   ⚠ 여기서 '적용'이라는 말을 쓰면 안 된다. 반영은 사람이 한다.
            n = result.get("to_apply", 0)
            occ_n = result.get("to_apply_occ", 0)
            occ_part = f" · 본문 {occ_n}곳" if occ_n else ""
            flag_part = f" · 검수 {flagged}건" if flagged else ""
            # ⚠ ' — '를 쓰지 말 것. 화면 로그는 34자를 넘으면 **' — ' 앞에서 자른다**
            #   (activity_panel._condense) — 그러면 '정오표 생성 완료'만 남고 수치가
            #   통째로 사라진다(실측 확인). 규약: 수치를 앞에, 구분은 ' · '로.
            if out_mode == "memo":
                marked = result.get("memoed", 0)
                blocked = result.get("memo_blocked", 0)
                blk = f" · 메모 불가 {blocked}곳" if blocked else ""
                self.activity.log(
                    f"✓ 메모본 생성 완료 · 메모 {marked}곳{blk}{flag_part} (본문 글자 무변경)")
                self.rail.set_step_result("done", f"메모 : {marked}곳")
            elif out_mode == "pdf":
                annot = result.get("annotated", 0)
                miss = result.get("pdf_missing", 0)
                miss_part = f" · 미탐 {miss}건" if miss else ""
                self.activity.log(
                    f"✓ PDF 주석 완료 · 주석 {annot}곳{miss_part}{flag_part} (원본 무변경)")
                self.rail.set_step_result("done", f"주석 : {annot}곳")
            else:
                self.activity.log(
                    f"✓ 정오표 생성 완료 · 반영 필요 {n}건{occ_part}{flag_part} (한글 파일 미수정)")
                self.rail.set_step_result("done", f"정오표 : {n}건")
        elif flagged > 0 and applied == 0:
            # 적용할 교정이 없어 치환이 0건이었던 실행(옵션이 아니라 상태).
            self.activity.log(f"✓ 검수 완료 — 검수 {flagged}건 정오표 기록 (HWP 미수정)")
            self.rail.set_step_result("done", f"검수 : {flagged}건")
        else:
            occ_part = f" · 본문 {occ}곳 치환" if occ else ""
            fail_part = f" · 실패 {failed}건" if failed else ""
            self.activity.log(
                f"✓ 완료 — 적용 {applied}건{occ_part}{fail_part}")
            self.rail.set_step_result("done", f"적용 : {applied}건")
        self.running_panel.set_animating(False)
        self._show_phase("result")

    # ══════════════════════════════════════════════
    # 결과물 추가 — 검토를 다시 거치지 않고 산출물을 하나 더
    # ══════════════════════════════════════════════
    #   ★사용자가 결과물 하나를 받아 본 뒤 "다른 형태도 필요하다"고 할 때, 예전엔
    #     초기화 → 옵션 선택 → **같은 검토를 처음부터 다시**가 유일한 길이었다
    #     (사용자 요구 2026-08-07). 수락/거절·부분 거절은 이미 확정된 값이므로
    #     그대로 재사용하면 되는 일이다.
    #
    #   ⚠ 불변식 넷 — 하나라도 깨면 조용히 틀린 산출물이 나온다.
    #     ① **언제나 원본 파일**에서 만든다. 교정본(_교정본.hwp) 위에 메모·주석을 달면
    #        원문 문자열이 이미 바뀌어 있어 **찾을 수가 없다**. 원본은 어느 모드에서도
    #        수정되지 않는다(save_as는 새 파일로 저장, close는 Quit — 덮어쓰기 없음).
    #     ② **결정은 다시 묻지 않는다** — `self._corrections`(상태·skip_occurrences)와
    #        1차 적용에 넘긴 `_applied_occ_rows`를 그대로 넘긴다.
    #     ③ **1차 실행의 수치를 덮어쓰지 않는다** — `applied`가 "본문 글자를 실제로
    #        바꾼 건수"라는 뜻을 넓히지 않기 위해, 추가분은 `extra_outputs` 목록에 싣는다.
    #     ④ **기존 정오표를 덮어쓰지 않는다** — 모드 이름을 붙인 새 파일로 낸다.
    _EXTRA_LABEL = {"hwp": "교정본", "memo": "메모본",
                    "pdf": "PDF 주석본", "errata": "정오표"}

    def _start_extra_output(self, mode: str):
        if mode not in self._EXTRA_LABEL:
            return
        if self._phase in ("running", "apply_running", "extra_running"):
            return
        if self._extra_worker is not None and self._extra_worker.isRunning():
            return
        if not self._file_path or not os.path.exists(self._file_path):
            self._warn_popup("원본 파일 없음",
                             "결과물을 추가하려면 원본 한글 파일이 그대로 있어야 합니다.\n"
                             "파일이 이동·삭제되었는지 확인해 주세요.")
            return
        if not self._corrections:
            self._warn_popup("교정 결과 없음", "재사용할 교정 결과가 없습니다.")
            return

        label = self._EXTRA_LABEL[mode]
        opts = dict(self._options)
        opts["output_mode"] = mode
        # 정오표 잠금 규율(setup_panel._sync_errata_lock)을 여기서도 곱한다 — 한글을
        #   고치지 않는 모드는 '표시하지 못한 자리'를 적을 곳이 정오표뿐이다.
        opts["gen_errata"] = True if mode != "hwp" else self._options.get("gen_errata", True)
        opts["errata_output_path"] = self._extra_errata_path(mode)

        self._extra_mode = mode
        self._phase = "extra_running"
        # ⚠ 화면 로그 규약: 수치는 괄호 밖에 — `_condense`가 괄호 절을 통째로 지운다
        #   (실측: '… 재사용 (교정 412건)' → '건'이 사라진 채 표시됨).
        self.activity.log(f"[추가] {label} 생성 시작 · 교정 {len(self._corrections)}건 "
                          f"재사용 · 검토 생략")
        self.result_panel.set_extra_busy(mode, f"{label} 만드는 중… \n"
                                               f"진행률은 아래 상태바에 표시됩니다")
        self.footer.set_busy(f"{label} 생성 중…", "생성 중")

        self._cleanup_worker("_extra_worker")
        self._extra_worker = ApplyWorker(
            self._file_path, self._corrections, opts,
            occ_rows=self._applied_occ_rows,
            # 1차 실행이 뜬 쪽 번호를 그대로 — 같은 원본의 같은 장면이다.
            known_pages=self._result.get("pages_by_original") or {},
            parent=self)
        self._extra_worker.progress.connect(self._on_worker_progress)
        self._extra_worker.log_message.connect(self.activity.log)
        self._extra_worker.finished.connect(self._on_extra_done)
        self._extra_worker.error.connect(self._on_extra_error)
        self._extra_worker.start()

    def _extra_errata_path(self, mode: str) -> str:
        """추가 실행이 쓸 정오표 경로 — **언제나** 모드 이름을 붙인다.

        ⚠ 기본 이름(`…_정오표.xlsx`)을 쓰면 1차 실행의 정오표를 말없이 덮어쓴다.
          모드마다 내용도 다르다(PDF는 'PDF 쪽' 칸이 붙고, 메모는 메모를 달지 못한
          자리가 사유로 실린다).
        ⚠ 예전엔 "1차가 정오표를 안 만들었으면 기본 이름을 쓴다"는 예외가 있었다.
          그 경로에서 실제로 사고가 났다(2026-08-10 사용자 보고): 1차(교정본)에서 정오표
          토글을 끈 실행에 메모본을 추가하자 메모 실행의 정오표가 `…_정오표.xlsx`로
          저장됐고, 결과 화면은 그것을 1차 산출물처럼 '정오표'로만 표시해 사용자는
          **'정오표(메모)가 생성되지 않았다'**고 읽었다 — 메모 불가 150곳의 사유가
          그 파일 안에 있는데도 찾을 길이 없었다. 이름은 항상 출처를 말해야 한다.
        """
        base, _ext = os.path.splitext(self._file_path)
        # ⚠ 괄호 안 말은 결과 화면의 꼬리표(`ResultPanel._OUT_META[…][4]`)와 **같아야**
        #   한다 — 화면엔 '정오표 (메모)', 파일은 `…_정오표(메모).xlsx`.
        name = {"hwp": "교정본", "memo": "메모", "pdf": "PDF", "errata": "정오표만"}[mode]
        return f"{base}_정오표({name}).xlsx"

    def _on_extra_done(self, result: dict):
        """추가 산출물 완료 — **1차 결과 dict를 덮어쓰지 않고** 목록에 한 줄 더한다."""
        mode = result.get("output_mode") or self._extra_mode
        label = self._EXTRA_LABEL.get(mode, "산출물")
        path = (result.get("pdf_path") if mode == "pdf"
                else result.get("hwp_path") if mode in ("hwp", "memo")
                else "")
        entry = {
            "mode":         mode,
            "path":         path or "",
            "errata_path":  result.get("errata_path", ""),
            "applied":      result.get("applied", 0),
            "occurrences":  result.get("occurrences", 0),
            "memoed":       result.get("memoed", 0),
            "memo_blocked": result.get("memo_blocked", 0),
            # ★수락했는데 표시하지 못한 자리 — 화면 수치가 정오표의 수락 행 수와
            #   맞아떨어지게 하는 값이다(`_mode_summary` 주석).
            "unmarked":     result.get("unmarked", 0),
            "annotated":    result.get("annotated", 0),
            "pdf_missing":  result.get("pdf_missing", 0),
            "errata_rows":  result.get("errata_rows") or [],
        }
        extras = list(self._result.get("extra_outputs") or [])
        extras = [e for e in extras if e.get("mode") != mode]   # 같은 모드 재실행은 갱신
        extras.append(entry)
        self._result["extra_outputs"] = extras
        # 1차 실행에 정오표가 아예 없었다면(교정본 모드 + 정오표 끄기) 이번 것이
        #   그 자리를 채운다 — 산출물 카드가 '정오표는 이미 있다'고 셀 근거가 생긴다
        #   (`ResultPanel._produced_modes`).
        #   ⚠ 다만 **누가 만든 것인지**를 함께 남긴다. 안 남기면 산출물 목록이 그것을
        #     1차 정오표인 양 '정오표'로만 적고 1차 실행의 행 수까지 붙여, 사용자가
        #     '이 모드의 정오표는 안 만들어졌다'고 읽는다(2026-08-10 사용자 보고).
        if not self._result.get("errata_path") and entry["errata_path"]:
            self._result["errata_path"] = entry["errata_path"]
            self._result["errata_from_extra"] = mode

        detail = {
            "hwp":  f"적용 {entry['applied']}건 · 본문 {entry['occurrences']}곳",
            "memo": f"메모 {entry['memoed']}곳"
                    + (f" · 표시 못 함 {entry['unmarked'] or entry['memo_blocked']}곳"
                       if (entry["unmarked"] or entry["memo_blocked"]) else ""),
            "pdf":  f"주석 {entry['annotated']}곳"
                    + (f" · 미탐 {entry['pdf_missing']}건"
                       if entry["pdf_missing"] else ""),
            "errata": f"{len(entry['errata_rows'])}행 기록",
        }.get(mode, "")
        # ⚠ 화면 로그 규약: `[태그]` + 수치를 앞에, 구분은 ' · '(' — '는 잘린다).
        self.activity.log(f"✓ [추가] {label} 생성 완료 · {detail}")
        self._finish_extra()

    def _on_extra_error(self, message: str):
        """추가 산출물 실행 중 오류 — **결과 화면에 머문다**.

        ⚠ 1차 실행용 `_on_error`를 재사용하면 안 된다. 그 핸들러는 설정 화면으로
          되돌리는데, 여기서 그러면 이미 받아 둔 1차 산출물의 결과 화면이 통째로
          사라진다(추가 실패가 성공한 작업을 지우는 셈).
        """
        self.activity.log(message, level="err")
        self._finish_extra()
        QMessageBox.warning(self, "결과물 추가 실패", message)

    def _finish_extra(self):
        """추가 산출물 실행 종료 — UI를 결과 화면 상태로 되돌린다(멱등).
        `update_result`가 진행 표시 해제까지 겸한다(렌더 1회)."""
        self._extra_mode = ""
        self.result_panel.update_result(self._result)
        self._show_phase("result")

    def _open_path_folder(self, path: str):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "파일 없음", "해당 파일이나 폴더를 찾을 수 없습니다.")
            return
        folder = os.path.dirname(path)
        if not os.path.exists(folder):
            folder = path
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ⚠ 예전의 '정오표 수동 생성'(`_on_generate_errata_requested`)은 푸터의 '정오표
    #   생성' 버튼과 함께 제거됐다(2026-08-11). 1차 실행이 정오표를 만들지 않았어도
    #   결과 화면 '산출물' 카드의 **결과물 추가**가 같은 일을 더 정확하게 한다 —
    #   ApplyWorker를 `output_mode="errata"`로 다시 태우므로 쪽 번호·적용 결과가
    #   실제 실행에서 나오고(수동 경로는 데이터가 없으면 쪽을 비웠다), 파일 이름도
    #   `_정오표(정오표만).xlsx`로 출처를 밝힌다(`_extra_errata_path` 주석 참조).

    # ══════════════════════════════════════════════
    # 오류 / 취소 / 리셋
    # ══════════════════════════════════════════════
    def _on_error(self, message: str):
        self.activity.log(message, level="err")
        # 진행 중이던 단계에 에러 표시
        self.rail.set_error("done" if self._apply_running() else "analyze")
        self.footer.set_idle("오류 발생")
        self.running_panel.set_animating(False)
        QMessageBox.critical(self, "오류", message)
        # 활동 로그/파일은 유지한 채 설정으로 복귀(재시도 가능)
        self._show_phase("setup")

    def _apply_running(self) -> bool:
        for w in (self._apply_worker, self._extra_worker):
            if w is not None and w.isRunning():
                return True
        return False

    def _on_cancel(self):
        self.footer.mark_cancelling()
        self.activity.log("⚠ 취소 요청 — 진행 중인 작업을 중단합니다…")
        for attr in ("_worker", "_apply_worker", "_extra_worker"):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning() and hasattr(w, "request_stop"):
                w.request_stop()

    def _reset(self):
        self._cleanup_worker("_worker")
        self._cleanup_worker("_apply_worker")
        self._cleanup_worker("_extra_worker")
        self._extra_mode = ""
        self._applied_occ_rows = None
        self._file_path = ""
        self._options = {}
        self._corrections = []
        self._extracted_text = ""
        self._page_count = None
        self._footnote_lines = []   # **실제 각주** 라인 인덱스(미리보기 [각주] 표지용)
        # 표기 일관성 단계를 이미 거쳤는가 — 한 문서에 한 번만 끼운다(통일 후 다시 갈리면
        #   그건 사용자가 방금 내린 결정이므로 되묻지 않는다).
        self._consistency_done = False
        self._consistency_logged = False
        self._result = {}
        self.file_panel.set_file("")
        self.activity.clear()
        self.rail.reset()
        self._show_phase("setup")

    # ── 레일 클릭(설정으로 복귀해 재실행) ────────
    def _on_rail_click(self, key: str):
        if self._phase in ("running", "apply_running", "extra_running"):
            return
            
        # 바디 내용만 교체 (푸터와 레일의 진행상태는 마지막 단계 유지)
        if key == "setup":
            self.main_stage.setCurrentIndex(0)
        elif key == "analyze" and self._corrections:
            self.main_stage.setCurrentIndex(1)
        elif key == "review" and self._corrections:
            self.main_stage.setCurrentIndex(2)
        elif key == "done" and self._result:
            self.main_stage.setCurrentIndex(3)

    # ══════════════════════════════════════════════
    # 테마 / 설정
    # ══════════════════════════════════════════════
    def _toggle_theme(self):
        from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer
        
        # 1. 현재 화면 스크린샷 캡처 (오버레이 생성)
        pixmap = self.grab()
        self._theme_overlay = QLabel(self)
        self._theme_overlay.setPixmap(pixmap)
        self._theme_overlay.setGeometry(self.rect())
        self._theme_overlay.show()
        self._theme_overlay.raise_()
        
        # 오버레이 화면에 표시 강제
        QApplication.processEvents()

        def do_apply():
            # 2. 메인 스레드를 블로킹하며 무거운 글로벌 QSS 테마 적용
            new_mode = "light" if theme.current_mode() == "dark" else "dark"
            theme.apply_theme(QApplication.instance(), new_mode)
            try:
                self._config.set_theme(new_mode)
            except Exception:
                pass
            self.header.set_theme_icon(new_mode)
            self._update_dwm_border()   # DWM 테두리 색상도 테마에 맞게 갱신
            self._refresh_all_themes()

            # 3. 테마 적용 완료 후 오버레이를 페이드 아웃 (맥북 스타일)
            effect = QGraphicsOpacityEffect(self._theme_overlay)
            self._theme_overlay.setGraphicsEffect(effect)
            
            self._theme_fade_anim = QPropertyAnimation(effect, b"opacity")
            self._theme_fade_anim.setDuration(400)
            self._theme_fade_anim.setStartValue(1.0)
            self._theme_fade_anim.setEndValue(0.0)
            self._theme_fade_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._theme_fade_anim.finished.connect(self._theme_overlay.deleteLater)
            self._theme_fade_anim.start()

        # 약간의 지연 후 실행하여 오버레이가 먼저 확실히 그려지도록 함
        QTimer.singleShot(10, do_apply)

    def _refresh_all_themes(self):
        """전체 위젯 트리에서 refresh_theme 보유 위젯을 안전하게 갱신.
        (직접 페인트/HTML/SVG 아이콘 위젯이 새 팔레트를 반영)"""
        try:
            from shiboken6 import isValid
        except Exception:
            isValid = lambda _w: True
        for w in self.findChildren(QWidget):
            if isValid(w) and hasattr(w, "refresh_theme"):
                try:
                    w.refresh_theme()
                except Exception:
                    pass

    # ══════════════════════════════════════════════
    # 프레임리스 창 — 가장자리 리사이즈 + 헤더 드래그(Win32 NCHITTEST)
    # ══════════════════════════════════════════════
    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and _MSG is not None:
            try:
                msg = _MSG.from_address(int(message))
                if msg.message == _WM_NCHITTEST:
                    return True, self._hit_test()
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _hit_test(self) -> int:
        pos = self.mapFromGlobal(QCursor.pos())
        x, y, w, h, b = pos.x(), pos.y(), self.width(), self.height(), _RESIZE_BORDER
        left, right = x < b, x >= w - b
        top, bottom = y < b, y >= h - b
        if not self.isMaximized():
            if top and left:     return _HTTOPLEFT
            if top and right:    return _HTTOPRIGHT
            if bottom and left:  return _HTBOTTOMLEFT
            if bottom and right: return _HTBOTTOMRIGHT
            if left:   return _HTLEFT
            if right:  return _HTRIGHT
            if top:    return _HTTOP
            if bottom: return _HTBOTTOM
        if self._in_drag_zone(pos):
            return _HTCAPTION      # 네이티브 창 이동(+ 스냅) 위임
        return _HTCLIENT

    def _in_drag_zone(self, pos) -> bool:
        """상단 드래그 영역(버튼 등 인터랙티브 위젯 제외)인지."""
        if pos.y() > _DRAG_ZONE_H:
            return False
        w = self.childAt(pos)
        while w is not None:
            if isinstance(w, QAbstractButton):
                return False
            w = w.parentWidget()
        return True

    # ══════════════════════════════════════════════
    # 자동 업데이트 (확인=자동 · 설치=사용자 클릭)
    # ══════════════════════════════════════════════
    def _start_update_check(self):
        """앱 시작 시 백그라운드로 두 채널을 확인한다.

        네트워크가 없거나 저장소가 비공개로 바뀌어도 updater가 조용히 None을
        주므로(graceful) 이 경로는 앱 동작에 영향을 주지 않는다.
        """
        try:
            from ui.workers.update_worker import UpdateCheckWorker
        except Exception:
            return
        self._update_worker = UpdateCheckWorker(parent=self)
        self._update_worker.done.connect(self._on_update_checked)
        self._update_worker.start()

    def _on_update_checked(self, result: dict):
        self._update_info = {ch: info for ch, info in (result or {}).items() if info}
        if not self._update_info:
            return
        # 사이드바 배지 — 모달을 닫아도 남는 상시 진입점
        labels = []
        for ch in ("app", "data"):
            info = self._update_info.get(ch)
            if info:
                labels.append(("v" if ch == "app" else "") + str(info.get("version", "")))
        self.sidebar.set_update_available(labels)
        self.activity.log(
            "새 버전 " + " · ".join(labels) + " 확인 — 사이드바에서 설치할 수 있습니다")
        # 이번 실행에서 한 번만 모달로 알린다(매번 띄우면 방해가 된다).
        if not self._update_prompted:
            self._update_prompted = True
            QTimer.singleShot(600, self._open_update_dialog)

    def _open_update_dialog(self):
        """앱 채널을 우선 안내한다(둘 다 있으면 앱 → 다음 실행에 데이터)."""
        if not self._update_info:
            return
        if self._update_dialog is not None and self._update_dialog.isVisible():
            self._update_dialog.raise_()
            return
        # ⚠ 교정·적용이 도는 중엔 설치를 권하지 않는다 — 앱 교체는 재시작을
        #   동반하므로 진행 중인 작업이 통째로 날아간다(updater 설계 규율).
        if self._phase in ("running", "apply_running"):
            self.activity.log("  작업이 끝난 뒤 사이드바에서 업데이트를 설치할 수 있습니다")
            return
        channel = "app" if "app" in self._update_info else "data"
        info = self._update_info[channel]
        from ui.widgets.update_dialog import UpdateDialog
        dlg = UpdateDialog(self, info, channel)
        self._update_dialog = dlg
        dlg.apply_requested.connect(self._on_update_apply)
        dlg.exec()
        dlg.deleteLater()
        self._update_dialog = None

    def _on_update_apply(self, channel: str, zip_path):
        """다운로드된 패키지를 실제로 설치한다 — 사용자가 '재시작'을 누른 뒤에만.

        ⚠ 이 슬롯은 **모달 dlg.exec()의 중첩 이벤트 루프 안에서** 호출된다. 거기서
          곧바로 self.close()를 부르면 메인 창을 자식 모달이 살아 있는 채로 닫는
          꼴이라 종료가 무시되거나 유령 창이 남는다(검수 패널이 팝업 뒤처리를
          singleShot(0)으로 미루는 것과 같은 이유). 실제 작업은 다이얼로그가
          완전히 닫힌 다음 턴으로 미룬다.
        """
        QTimer.singleShot(0, lambda: self._do_update_apply(channel, zip_path))

    def _do_update_apply(self, channel: str, zip_path):
        from core import updater
        from ui.widgets.review_panel import LightConfirmDialog
        log = self.activity.log
        if channel == "data":
            if updater.install_data(zip_path, logger=log):
                LightConfirmDialog.ask(
                    self, "사전 데이터 업데이트",
                    "새 사전을 설치했습니다. 앱을 다시 시작하면 적용됩니다.",
                    yes_text="확인", no_text="닫기")
            return
        # 앱 교체 — 헬퍼 배치가 앱 종료를 기다렸다가 폴더를 갈아 끼우고 재실행한다.
        #   install_app이 True면 헬퍼가 이미 떠서 기다리는 중이므로 **반드시 종료**해야 한다.
        #   version을 함께 넘겨 제어판(ARP) 등록 버전까지 갱신한다 — 안 넘기면 폴더만
        #   바뀌고 '앱 및 기능'에는 옛 버전이 남는다.
        new_ver = (self._update_info.get(channel) or {}).get("version")
        if updater.install_app(zip_path, logger=log, version=new_ver):
            self.close()

    # ══════════════════════════════════════════════
    # 워커 정리 / 종료
    # ══════════════════════════════════════════════
    def _cleanup_worker(self, attr_name: str):
        worker = getattr(self, attr_name, None)
        if worker is not None:
            if worker.isRunning():
                if hasattr(worker, "request_stop"):
                    worker.request_stop()
                worker.quit()
                if not worker.wait(5000):
                    # 5초 내 안 멈춤 — 참조만 버리면 뒤늦게 도착한 finished/error
                    #   시그널이 '새 세션'의 상태를 오염시킨다(예: 이전 문서의 분석
                    #   결과가 새 문서 세션에 로드). 시그널을 전부 끊어 격리한다.
                    #   (parent=self라 객체는 살아 있고, 스레드는 자연 종료된다.)
                    try:
                        worker.disconnect()
                    except Exception:
                        pass
            setattr(self, attr_name, None)

    def closeEvent(self, event):
        self._cleanup_worker("_worker")
        self._cleanup_worker("_apply_worker")
        self._cleanup_worker("_extra_worker")
        # 업데이트 확인 워커는 순수 네트워크 대기라 중단 수단이 없다 —
        #   _cleanup_worker의 5초 대기를 그대로 쓰면 회선이 먹통일 때 종료가
        #   그만큼 늦어진다. 시그널만 끊고 짧게 기다린 뒤 강제 종료한다
        #   (결과를 버려도 앱 상태에 영향이 없는 부가 기능).
        w = getattr(self, "_update_worker", None)
        if w is not None:
            try:
                w.disconnect()
            except Exception:
                pass
            if w.isRunning() and not w.wait(800):
                w.terminate()
                w.wait(500)
            self._update_worker = None
        # 동기화 워커는 단명 — 잠시 대기 후 남아있으면 강제 종료(네트워크 블로킹 회피).
        for attr in ("_sync_workers",):
            for w in getattr(self, attr, []):
                try:
                    if w.isRunning():
                        w.wait(3000)
                        if w.isRunning():
                            w.terminate()
                except Exception:
                    pass
            setattr(self, attr, [])
        event.accept()
