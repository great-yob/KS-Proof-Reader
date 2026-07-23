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
    # ⚠ 완료 화면은 로그를 재가공하지 않는다 — ActivityPanel이 이미 요약한
    #   `[태그] 내용` 표시본을 그대로 받는다. 그래서 이 가짜 데이터도 그 형식으로
    #   둔다(형식이 어긋나면 프리뷰가 실제와 다르게 보인다).
    log = [
        ("14:02:08", "info", "[대상파일] 2026 지역혁신 성과보고서.hwp"),
        ("14:02:09", "info", "[원고 추출 시작]"),
        ("14:02:11", "ok",   "[원고 추출 완료] 87페이지 · 28,979자"),
        ("14:02:12", "info", "[사전(국립국어원) 분석 시작]"),
        ("14:02:31", "info", "[표준국어대사전+우리말샘 DB] 미등재 · 비표준 63건 탐지"),
        ("14:02:44", "info", "[우리말샘 API] 실재어 5건 제외"),
        ("14:02:46", "info", "[AI (Gemini) 분석 시작]"),
        ("14:03:52", "info", "[AI 분석] 4/4 청크 분석 진행"),
        ("14:03:58", "info", "[AI 분석] 교정 22건 제안"),
        ("14:03:58", "info", "[AI 필터] 문장부호 가감 3건 제외"),
        ("14:04:01", "ok",   "[AI (Gemini) 분석 완료] 교정 19건 확정"),
        ("14:04:02", "info", "[사전+결정론규칙 검증 시작]"),
        ("14:04:08", "info", "[검증] 빈출 미등재어 12건 제외"),
        ("14:04:12", "ok",   "[사전+결정론규칙 검증 완료]"),
        ("14:04:12", "info", "[분석+검증 결과 정리 시작]"),
        ("14:04:13", "info", "[결과] 자동교정 58건 (규범표기 3 + 띄어쓰기 55)"),
        ("14:04:13", "info", "[결과] 검수필요 17건 (괄호 1 + 안전망 8 + 띄어쓰기 8)"),
        ("14:04:13", "ok",   "[분석 완료] 교정제안 75건 확정"),
        ("14:04:13", "info", "[적용 대상] 본문 112항목 (반복 포함)"),
        ("14:06:40", "info", "[적용] 적용 47건 · 본문 112곳 · 실패 1건"),
        ("14:06:41", "warn", "[적용] 부분 반영 1건 · 원문 위치 확인"),
        ("14:06:44", "ok",   "[저장 완료]"),
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
