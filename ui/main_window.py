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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QMessageBox, QApplication, QAbstractButton,
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
        self.setWindowTitle("KS-Proof Reader")
        # 네이티브 타이틀바 제거 — 창 컨트롤은 헤더가 직접 제공(프레임리스)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(960, 600)

        self._config = ConfigLoader()
        try:
            w, h = self._config.get_window_size()
        except Exception:
            w, h = 1240, 800
        self.resize(max(w, 960), max(h, 600))

        self._file_path = ""
        self._options = {}
        self._corrections = []
        self._extracted_text = ""
        self._page_count = None
        self._result = {}
        self._worker = None
        self._apply_worker = None
        self._sync_workers = []   # 공유 용어 뇌 동기화 워커(단명, fire-and-forget)
        self._phase = "setup"

        self._build_ui()
        self._setup_dwm_style()   # Win11 라운드 모서리 + 테두리 + 그림자
        self._wire()
        self._show_phase("setup")

        # 공유 용어 뇌 — 로그인은 **선택**. 앱 시작 시 저장된 사내 세션을 백그라운드 복원하고,
        #   로그인돼 있으면 동기화·큐레이션을 활성화한다(미로그인 시 교정 기능엔 영향 없음).
        self._auth_workers = []
        self._curator_panel = None
        self._login_dialog = None
        self._start_session_restore()

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

        self.file_panel.file_selected.connect(self._on_file_selected)
        self.setup_panel.options_changed.connect(self._refresh_setup_footer)

        self.review_panel.counts_changed.connect(self._on_review_counts)

        self.footer.primary_clicked.connect(self._on_primary)
        self.footer.cancel_clicked.connect(self._on_cancel)
        self.footer.reset_clicked.connect(self._reset)
        self.footer.errata_clicked.connect(self._on_footer_errata_clicked)
        self.footer.folder_clicked.connect(self._on_footer_folder_clicked)

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
            self.footer.set_primary("수정된 HWP 열기",
                                    variant="primary", enabled=False, visible=False, show_reset=True)
            errata_path = self._result.get("errata_path", "")
            has_errata = bool(errata_path) and os.path.exists(errata_path)
            self.footer.set_result_actions(True, has_errata=has_errata)

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
            self._start_apply()
        elif self._phase == "result":
            self._reset()

    # ── 분석 시작 ───────────────────────────────
    def _start_analysis(self):
        if not self._file_path or not self.setup_panel.scopes_selected():
            return
        self._options = self.setup_panel.get_options()

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

    def _on_page_count(self, page_count):
        """문서 총 페이지 수(없으면 None) — 완료 대시보드 대표 수치용."""
        self._page_count = page_count
        if page_count:
            self.rail.set_step_result("setup", f"{page_count:,}페이지")

    def _on_analysis_done(self, corrections: list):
        self._corrections = corrections
        detected = len(corrections)
        self.activity.set_detected(detected)
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
                                   full_text=self._extracted_text)
                                   
            self._start_apply()
            return

        # 수동 검토 모드
        self.review_panel.load(corrections, self._options,
                               os.path.basename(self._file_path),
                               full_text=self._extracted_text)

        # 교정 N'건'(항목)이 본문 몇 '곳'(등장)에 해당하는지 한 줄로 연결 — 검토 단계의
        #   '수락 X / Y곳' 숫자(Y=등장)가 분석의 '교정 N건'(N=항목)과 왜 다른지 설명.
        _, _, occ_total = self.review_panel.get_counts()
        if occ_total and occ_total != detected:
            self.activity.log(f"  · 본문 {occ_total}곳에 해당 (반복 등장 포함)")

        self.running_panel.set_title("분석 완료")
        self.running_panel.set_detail("아래 버튼을 눌러 교정 제안 검토를 시작하세요.", tone="text_success")
        self.running_panel.set_animating(False)
        self.footer.set_idle(f"분석 완료 · 교정 {detected}건")
        self.footer.set_primary("교정 검토 시작", variant="success_solid", visible=True, show_reset=True)

    # ── 검토 카운트 → 푸터/레일 ──────────────────
    def _on_review_counts(self, pending: int, accepted: int, total: int):
        if self._phase != "review":
            return
        # 용어 통일: 검토 단계는 '수락'(완료 단계의 '적용'과 구분). 카드=본문 등장이라 '곳'.
        #   ⚠ 'AI 분석 제외' 모드에서도 결정론 교정은 실제 적용되므로 라벨은 공통이다.
        #   순수 검수(치환 0건) 여부는 ApplyWorker가 동적으로 판단해 처리한다.
        self.rail.set_step_result("review", f"수락 : {accepted} / {total}")
        self.footer.set_status(f"사용자 검토 중 — 수락 : {accepted} / {total}항목 · 대기 : {pending}항목")
        # 대기 항목이 있어도 버튼은 활성 — 누르면 _start_apply가 미선택을 막고 에러 팝업.
        self.footer.set_primary(f"✓  교정 적용 ({accepted}항목)", variant="action_pink",
                                enabled=total > 0, visible=True, show_reset=True)

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
        self._apply_worker = ApplyWorker(self._file_path, self._corrections,
                                         self._options, parent=self)
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
        """저장된 사내 세션(DPAPI)을 백그라운드 복원. 성공 시 로그인 상태·동기화 활성."""
        try:
            from ui.workers.login_worker import RestoreWorker
            w = RestoreWorker(parent=self)
            w.done.connect(self._on_session_restored)
            self._track_auth_worker(w)
            w.start()
        except Exception:
            pass

    def _on_session_restored(self, user):
        try:
            self._apply_auth_state(user)
            if user:
                self.activity.log(
                    f"  [계정] 세션 복원 — {user.get('name') or user.get('email')}")
                self._start_sync("sync")   # 보류 이벤트 push + 최신 스냅샷 pull
        except Exception:
            self._log_auth_error("세션 복원")

    def _open_login_dialog(self):
        """사내 계정 로그인 다이얼로그(선택). 성공 시 동기화·큐레이션 활성."""
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

    def _track_auth_worker(self, w):
        self._auth_workers = [x for x in getattr(self, "_auth_workers", []) if x.isRunning()]
        self._auth_workers.append(w)
        w.finished.connect(self._prune_auth_workers)

    def _prune_auth_workers(self):
        self._auth_workers = [x for x in self._auth_workers if x.isRunning()]

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
        consumed = result.get("consumed", 0)
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
        self.activity.set_summary(applied=applied, failed=failed, excluded=consumed)
        if flagged > 0 and applied == 0:
            # 사전 전용 검수 모드 — HWP 미수정, 정오표만
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

    def _on_footer_errata_clicked(self):
        errata_path = self._result.get("errata_path", "")
        if errata_path and os.path.exists(errata_path):
            self._open_path_folder(errata_path)
        else:
            self._on_generate_errata_requested()

    def _on_footer_folder_clicked(self):
        self._open_path_folder(self._result.get("hwp_path", ""))

    def _open_path_folder(self, path: str):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "파일 없음", "해당 파일이나 폴더를 찾을 수 없습니다.")
            return
        folder = os.path.dirname(path)
        if not os.path.exists(folder):
            folder = path
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_generate_errata_requested(self):
        try:
            from output.errata_generator import generate_errata
            mode = "polish" if self._options.get("scope_polish") else "typo"
            # ApplyWorker가 동봉한, 실제 적용 결과(applied/error/partial/consumed)가
            #   병합된 행 데이터를 그대로 재사용한다 — 수동 정오표도 진실을 기록.
            full_detail = self._result.get("errata_detail")
            if not full_detail:
                # 폴백(적용 결과 데이터 부재) — 결정만으로 구성(적용 여부는 미확인 상태)
                full_detail = [
                    {
                        "original":  c["original"],
                        "corrected": c["corrected"],
                        "reason":    c.get("reason", ""),
                        "source":    c.get("source", "dict"),
                        "color":     c.get("color", 0),
                        "decision":  "accepted" if c.get("status") == "accepted" else "rejected",
                        "applied":   c.get("status") == "accepted",
                        "error":     "",
                    }
                    for c in self._corrections
                ]
            errata_path = generate_errata(
                detail   = full_detail,
                hwp_path = self._file_path,
                options  = {
                    "used_ai":         self._options.get("use_ai", True),
                    "mode":            mode,
                    "used_dict":       True,
                    "deep_screening":  self._options.get("deep_screening", False),
                },
            )
            self._result["errata_path"] = errata_path
            self.activity.log(f"✓ 정오표 수동 생성 완료: {os.path.basename(errata_path)}")
            self.result_panel.show_result(self._result, self.activity.get_proofreading_log())
            self.footer.set_result_actions(True, has_errata=True)
            self._open_path_folder(errata_path)
        except Exception as exc:
            QMessageBox.critical(self, "정오표 생성 오류", f"정오표를 생성하지 못했습니다:\n{exc}")

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
        w = self._apply_worker
        return w is not None and w.isRunning()

    def _on_cancel(self):
        self.footer.mark_cancelling()
        self.activity.log("⚠ 취소 요청 — 진행 중인 작업을 중단합니다…")
        for attr in ("_worker", "_apply_worker"):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning() and hasattr(w, "request_stop"):
                w.request_stop()

    def _reset(self):
        self._cleanup_worker("_worker")
        self._cleanup_worker("_apply_worker")
        self._file_path = ""
        self._options = {}
        self._corrections = []
        self._extracted_text = ""
        self._page_count = None
        self._result = {}
        self.file_panel.set_file("")
        self.activity.clear()
        self.rail.reset()
        self._show_phase("setup")

    # ── 레일 클릭(설정으로 복귀해 재실행) ────────
    def _on_rail_click(self, key: str):
        if self._phase in ("running", "apply_running"):
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
        # 동기화·인증 워커는 단명 — 잠시 대기 후 남아있으면 강제 종료(네트워크 블로킹 회피).
        for attr in ("_sync_workers", "_auth_workers"):
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
