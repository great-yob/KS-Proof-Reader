"""
preview_running_panel.py — 진행 화면(RunningPanel) 라이브 미리보기 (핫 리로드)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
진행 패널(책 애니메이션)만 즉시 띄워, 실제 교정을 돌리지 않고도
디자인/애니메이션 수정을 바로바로 확인하기 위한 도구.

실행:
    .\\.venv64\\Scripts\\python.exe preview_running_panel.py

기능:
  · 상단 컨트롤 바 — 테마(라이트/다크) · 진행률 슬라이더 · ▶/⏸ 애니 토글
    · ⟳ 파이프라인 시뮬레이션(0→100% 단계 메시지 재생)
  · 핫 리로드 — ui/widgets/book_progress.py / running_panel.py(및 theme.py,
    components.py)를 저장하면 자동으로 모듈을 리임포트하고 패널을 다시 만들어
    즉시 반영한다. (수정 → 저장(Ctrl+S) → 창에 바로 반영. 앱 재시작 불필요)
"""

import os
import sys
import importlib

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QToolTip,
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTimer, QFileSystemWatcher

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)

from ui.styles.fonts import load_fonts
from ui.styles import theme as theme_mod
import ui.widgets.ring_particles as ring_mod
import ui.widgets.components as comp_mod
import ui.widgets.book_progress as book_mod
import ui.widgets.running_panel as run_mod

# 핫 리로드 감시 대상 (의존 순서대로 리로드: theme → ring → components → book → panel)
_WATCH_FILES = (
    "ui/styles/theme.py",
    "ui/widgets/ring_particles.py",
    "ui/widgets/components.py",
    "ui/widgets/book_progress.py",
    "ui/widgets/running_panel.py",
)

# 파이프라인 시뮬레이션 단계 (진행률 문턱, 상세 메시지)
_SIM_STAGES = [
    (0,  "문서 텍스트 추출 중…"),
    (10, "사전 스크리닝 — 의심어 추출 중…"),
    (22, "형태소 분석으로 활용형 복원 중…"),
    (30, "AI 교정 제안 요청 중… (1/4)"),
    (45, "AI 교정 제안 요청 중… (2/4)"),
    (58, "AI 교정 제안 요청 중… (3/4)"),
    (70, "AI 교정 제안 요청 중… (4/4)"),
    (82, "제안 병합·중복 제거 중…"),
    (90, "사전 재검증(3차) 진행 중…"),
    (97, "일관성 후처리 중…"),
]


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("진행 화면 미리보기 — 핫 리로드")
        self.resize(1100, 780)

        # 패널에 다시 주입할 상태 (리로드/재생성 후에도 유지)
        self._mode = "light"
        self._progress = 34
        self._animating = True
        self._title = "교정 분석 중"
        self._detail = "AI 교정 제안 요청 중… (2/4)"
        self._tone = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 컨트롤 바 ────────────────────────────────
        bar = QWidget()
        bar.setStyleSheet("background:#15171d;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(10)

        def mkbtn(text, cb):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#2a2f3a;color:#e7eaee;border:1px solid #3c434e;"
                "border-radius:8px;padding:7px 14px;font-size:13px;font-weight:600;}"
                "QPushButton:hover{background:#3c434e;}")
            b.clicked.connect(cb)
            return b

        self._theme_btn = mkbtn("🌙 다크로", self._toggle_theme)
        bl.addWidget(self._theme_btn)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(self._progress)
        self._slider.setFixedWidth(220)
        self._slider.valueChanged.connect(self._on_slider)
        prog_lbl = QLabel("진행률")
        prog_lbl.setStyleSheet("color:#8a93a1;font-size:12px;")
        bl.addWidget(prog_lbl)
        bl.addWidget(self._slider)

        self._anim_btn = mkbtn("⏸ 애니 정지", self._toggle_anim)
        bl.addWidget(self._anim_btn)
        bl.addWidget(mkbtn("⟳ 시뮬레이션", self._start_sim))
        bl.addWidget(mkbtn("↻ 새로고침", self._rebuild))
        bl.addStretch()
        self._status = QLabel("핫 리로드 감시 중 — book_progress.py 저장 시 자동 반영")
        self._status.setStyleSheet("color:#8a93a1;font-size:12px;")
        bl.addWidget(self._status)
        root.addWidget(bar)

        # ── 패널 컨테이너 (앱의 stage 영역 흉내) ──────
        self._wrap = QWidget()
        self._holder = QVBoxLayout(self._wrap)
        self._holder.setContentsMargins(48, 40, 48, 40)
        root.addWidget(self._wrap, 1)

        self._panel = None
        theme_mod.set_mode(self._mode)
        self._build_panel()

        # 파이프라인 시뮬레이션 타이머
        self._sim = QTimer(self)
        self._sim.setInterval(160)
        self._sim.timeout.connect(self._sim_tick)

        # 핫 리로드 감시자 — 편집 시 재저장이 두 번 일어나는 에디터가 있어 디바운스.
        self._watch = QFileSystemWatcher(self)
        for rel in _WATCH_FILES:
            p = os.path.join(REPO, rel)
            if os.path.exists(p):
                self._watch.addPath(p)
        self._watch.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._hot_reload)

    # ── 패널 (재)생성 ────────────────────────────────
    def _build_panel(self):
        # 참조를 먼저 비운다 — 아래에서 RunningPanel() 생성이 실패해도(편집 중
        # 저장으로 모듈에 일시적 오류) 죽은 위젯 참조가 남지 않아, 다음 리로드가
        # "Internal C++ object already deleted" 없이 자연 복구된다.
        old, self._panel = self._panel, None
        if old is not None:
            try:
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass   # 이전 실패 리로드 등으로 C++ 객체가 이미 삭제된 경우
        pal = theme_mod.current_palette()
        self._wrap.setStyleSheet(f"background:{pal['bg']};")
        self._panel = run_mod.RunningPanel()
        self._holder.addWidget(self._panel)
        self._apply_state()

    def _apply_state(self):
        if self._panel is None:   # 직전 리로드 실패로 패널 부재 — 다음 성공 시 재주입
            return
        self._panel.set_title(self._title)
        self._panel.set_detail(self._detail, self._tone)
        self._panel.set_progress(self._progress)
        self._panel.set_animating(self._animating)

    def _rebuild(self):
        self._build_panel()

    # ── 컨트롤 ───────────────────────────────────────
    def _on_slider(self, v):
        self._progress = v
        if self._panel:
            self._panel.set_progress(v)

    def _toggle_anim(self):
        self._animating = not self._animating
        self._anim_btn.setText("⏸ 애니 정지" if self._animating else "▶ 애니 재생")
        if self._panel:
            self._panel.set_animating(self._animating)

    def _start_sim(self):
        self._sim.stop()
        self._progress = 0
        self._animating = True
        self._title = "교정 분석 중"
        self._detail = _SIM_STAGES[0][1]
        self._tone = ""
        self._anim_btn.setText("⏸ 애니 정지")
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._apply_state()
        self._sim.start()

    def _sim_tick(self):
        if self._panel is None:   # 리로드 실패 상태 — 패널 복구 전까지 대기
            return
        self._progress = min(100, self._progress + 1)
        self._slider.blockSignals(True)
        self._slider.setValue(self._progress)
        self._slider.blockSignals(False)
        detail = next((m for th, m in reversed(_SIM_STAGES)
                       if self._progress >= th), _SIM_STAGES[0][1])
        self._panel.set_progress(self._progress)
        if self._progress >= 100:
            self._sim.stop()
            self._animating = False
            self._title = "분석 완료"
            self._detail = "아래 버튼을 눌러 교정 제안 검토를 시작하세요."
            self._tone = "text_success"
            self._anim_btn.setText("▶ 애니 재생")
            self._apply_state()
        elif detail != self._detail:
            self._detail = detail
            self._panel.set_detail(detail)

    def _toggle_theme(self):
        self._mode = "dark" if self._mode == "light" else "light"
        theme_mod.apply_theme(QApplication.instance(), self._mode)
        self._theme_btn.setText("🌙 다크로" if self._mode == "light" else "☀ 라이트로")
        self._build_panel()

    # ── 핫 리로드 ────────────────────────────────────
    def _on_file_changed(self, path):
        # 일부 에디터는 저장 시 파일을 교체(삭제→생성)해 감시가 끊긴다 — 재등록.
        if path not in self._watch.files():
            if os.path.exists(path):
                self._watch.addPath(path)
        self._debounce.start()

    def _hot_reload(self):
        try:
            # running_panel은 components/book_progress의 심볼을, 그 둘은 theme의
            # 심볼을 import하므로 의존 역순으로 리로드한다.
            # ring_particles는 components(AnimatedGradientBorder)가 지연 import한다.
            importlib.reload(theme_mod)
            importlib.reload(ring_mod)
            importlib.reload(comp_mod)
            importlib.reload(book_mod)
            importlib.reload(run_mod)
            theme_mod.set_mode(self._mode)
            theme_mod.apply_theme(QApplication.instance(), self._mode)
            self._build_panel()
            import datetime
            self._status.setText(
                f"✓ 리로드됨 {datetime.datetime.now():%H:%M:%S} — 저장 감시 중")
            self._status.setStyleSheet("color:#54d17f;font-size:12px;")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._status.setText(f"✕ 리로드 실패: {exc}")
            self._status.setStyleSheet("color:#f1726e;font-size:12px;")


def main():
    app = QApplication(sys.argv)
    family = load_fonts()
    font = QFont(family, 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    QToolTip.setFont(font)
    theme_mod.apply_theme(app, "light")

    w = Preview()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
