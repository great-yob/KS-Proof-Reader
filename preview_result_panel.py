"""
preview_result_panel.py — 결과 페이지 라이브 미리보기 (핫 리로드)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
결과 페이지(ResultPanel)만 가짜 데이터로 즉시 띄워, 앱 전체를 서버 실행해
교정을 끝까지 돌리지 않고도 디자인 수정을 바로바로 확인하기 위한 도구.

실행:
    .\.venv64\Scripts\python.exe preview_result_panel.py

기능:
  · 상단 컨트롤 바 — 테마(라이트/다크) · 데이터셋(완료/검수/레거시) · ▶ 애니 재생
  · 핫 리로드 — ui/widgets/result_panel.py(및 theme.py)를 저장하면 자동으로
    모듈을 리임포트하고 패널을 다시 만들어 애니메이션과 함께 즉시 반영한다.
    (수정 → 저장(Ctrl+S) → 창에 바로 반영. 앱 재시작 불필요)
"""

import os
import sys
import importlib

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QToolTip,
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTimer, QFileSystemWatcher

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)

from ui.styles.fonts import load_fonts
from ui.styles import theme as theme_mod
import ui.widgets.result_panel as rp_mod


# ── 가짜 데이터셋 ─────────────────────────────────────────────
def _dataset(kind: str):
    """(result, log, corrections, char_count, page_count, file_name)"""
    log = [
        ("14:02:11", "info", "[1/7] 문서 텍스트 추출 완료 (28,979자)"),
        ("14:02:31", "info", "[2/7] 사전 스크리닝: 의심어 63건"),
        ("14:03:58", "info", "[3/7] AI 교정 제안 22건 수신"),
        ("14:04:02", "ok",   "[4/7] 병합·재검증 완료: 총 75건"),
        ("14:06:40", "ok",   "[6/7] 본문 79곳 치환 완료"),
        ("14:06:41", "warn", "실패 1건 — 상세는 실패 항목 참조"),
        ("14:06:44", "ok",   "[7/7] 정오표 저장 완료"),
    ]
    cors = (
        [{"source": "dict", "category": "띄어쓰기", "confidence": "high"}] * 24
        + [{"source": "ai_typo", "category": "오탈자", "confidence": "high"}] * 15
        + [{"source": "punct", "category": "문장부호", "confidence": "high"}] * 12
        + [{"source": "ai_polish", "category": "윤문", "confidence": "high"}] * 7
        + [{"source": "dict_flag", "category": "", "confidence": "low"}] * 8
    )
    if kind == "review":
        result = {"applied": 0, "occurrences": 0, "failed": 0, "consumed": 0,
                  "flagged": 66, "fail_samples": [], "errata_path": "", "hwp_path": ""}
        return result, log, cors, 28979, 74, "검수 모드 원고.hwp"
    if kind == "legacy":
        result = {"applied": 47, "occurrences": 112, "failed": 0, "consumed": 0,
                  "flagged": 0, "fail_samples": [], "errata_path": "", "hwp_path": ""}
        return result, log, None, None, None, "레거시 (부가데이터 없음).hwp"
    # 완료(기본)
    result = {
        "applied": 59, "occurrences": 79, "failed": 1, "consumed": 1, "flagged": 0,
        "fail_samples": [
            {"original": "홍보전략을", "corrected": "홍보 전략을",
             "error": "본문에서 원문을 찾지 못했습니다"},
        ],
        "errata_path": "", "hwp_path": "",
    }
    return result, log, cors, 28979, 87, "2026 지역혁신 성과보고서.hwp"


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("결과 페이지 미리보기 — 핫 리로드")
        self.resize(1360, 940)
        self._kind = "done"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 컨트롤 바
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

        self._ds = QComboBox()
        self._ds.addItems(["완료", "검수", "레거시"])
        self._ds.setStyleSheet(
            "QComboBox{background:#2a2f3a;color:#e7eaee;border:1px solid #3c434e;"
            "border-radius:8px;padding:6px 12px;font-size:13px;}"
            "QComboBox QAbstractItemView{background:#2a2f3a;color:#e7eaee;"
            "selection-background-color:#5e6ad2;}")
        self._ds.currentIndexChanged.connect(self._change_dataset)
        bl.addWidget(self._ds)

        bl.addWidget(mkbtn("▶ 애니 재생", self._replay))
        bl.addWidget(mkbtn("↻ 새로고침", self._rebuild))
        bl.addStretch()
        self._status = QLabel("핫 리로드 감시 중 — result_panel.py 저장 시 자동 반영")
        self._status.setStyleSheet("color:#8a93a1;font-size:12px;")
        bl.addWidget(self._status)
        root.addWidget(bar)

        self._holder = QVBoxLayout()
        self._holder.setContentsMargins(0, 0, 0, 0)
        wrap = QWidget()
        wrap.setLayout(self._holder)
        root.addWidget(wrap, 1)

        self._panel = None
        self._mode = "light"
        theme_mod.set_mode(self._mode)
        self._build_panel(animate=True)

        # 핫 리로드 감시자 — 편집 시 재저장이 두 번 일어나는 에디터가 있어 디바운스.
        self._watch = QFileSystemWatcher(self)
        for rel in ("ui/widgets/result_panel.py", "ui/styles/theme.py",
                    "ui/widgets/components.py"):
            p = os.path.join(REPO, rel)
            if os.path.exists(p):
                self._watch.addPath(p)
        self._watch.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._hot_reload)

    # ── 패널 (재)생성 ────────────────────────────────
    def _build_panel(self, animate: bool):
        # 참조를 먼저 비운다 — 아래 생성이 실패해도(편집 중 저장으로 모듈에
        # 일시적 오류) 죽은 위젯 참조가 남지 않아 다음 리로드가 자연 복구된다.
        old, self._panel = self._panel, None
        if old is not None:
            try:
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass   # 이전 실패 리로드 등으로 C++ 객체가 이미 삭제된 경우
        self._panel = rp_mod.ResultPanel()
        self._holder.addWidget(self._panel)
        result, log, cors, cc, pc, fname = _dataset(self._kind)
        # show_result는 fresh 판정에 dict identity를 쓴다 — 매번 새 dict라 시각 갱신.
        self._panel.show_result(result, log, corrections=cors,
                                char_count=cc, page_count=pc, file_name=fname)
        if not animate:
            self._panel.refresh_theme()   # 정적 최종 상태

    def _rebuild(self):
        self._build_panel(animate=True)

    def _replay(self):
        self._build_panel(animate=True)

    def _change_dataset(self, idx):
        self._kind = {0: "done", 1: "review", 2: "legacy"}.get(idx, "done")
        self._build_panel(animate=True)

    def _toggle_theme(self):
        self._mode = "dark" if self._mode == "light" else "light"
        theme_mod.apply_theme(QApplication.instance(), self._mode)
        self._theme_btn.setText("🌙 다크로" if self._mode == "light" else "☀ 라이트로")
        self._build_panel(animate=True)

    # ── 핫 리로드 ────────────────────────────────────
    def _on_file_changed(self, path):
        # 일부 에디터는 저장 시 파일을 교체(삭제→생성)해 감시가 끊긴다 — 재등록.
        if path not in self._watch.files():
            if os.path.exists(path):
                self._watch.addPath(path)
        self._debounce.start()

    def _hot_reload(self):
        try:
            importlib.reload(theme_mod)
            # result_panel은 theme_mod의 심볼(current_palette 등)을 import하므로
            # theme 먼저 리로드 후 패널 모듈 리로드.
            importlib.reload(rp_mod)
            theme_mod.set_mode(self._mode)
            self._build_panel(animate=True)
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
