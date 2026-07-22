"""
ui/widgets/activity_panel.py — 우측 영구 활동 로그 패널
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
전 단계(추출→AI→적용)의 로그를 한 곳에 누적 표시한다. 분석→적용 사이에
클리어하지 않으므로 성공/실패 흐름을 한눈에 추적 가능 — 사용자 지적 ②를 해소.
상단 요약칩으로 검출/적용/실패/제외 건수를 항상 노출한다.

레벨은 메시지 키워드로 추론하며, 테마 전환 시 전체 HTML을 재렌더한다.
"""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel,
)

from ui.widgets.components import label, icon_button, divider, chip
from ui.styles.theme import current_palette, restyle


_ERR_KW  = ("오류", "에러", "Exception", "Traceback", "✕", "치명")
_WARN_KW = ("경고", "⚠", "건너뜀", "스킵", "ℹ", "주의")
_OK_KW   = ("✓", "완료", "성공")

# 교정교열과 무관한 HWP 처리 배관(plumbing) 로그 — 완료 보고서에선 제외
_PLUMBING_KW = (
    "[Worker stderr]", "편집기:", "HWP 브리지", "Dispatch", "[보안]",
    "보안 모듈", "[변환]", "버전:", "SaveAs", "python.exe", "재오픈",
)


def _infer_level(msg: str) -> str:
    for kw in _ERR_KW:
        if kw in msg:
            return "err"
    for kw in _WARN_KW:
        if kw in msg:
            return "warn"
    for kw in _OK_KW:
        if kw in msg:
            return "ok"
    return "info"


class ActivityPanel(QFrame):
    EXPANDED_WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []     # (time_str, level, msg)
        self._build_ui()

    def _build_ui(self):
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        from ui.widgets.components import section_card, divider, chip
        frame, body = section_card("진행 및 결과", "clipboard-check")

        # 요약칩
        self._chip_row = QHBoxLayout()
        self._chip_row.setSpacing(6)
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chips = {}
        for key, text, tone in [
            ("detected", "교정 0", "accent"),
            ("applied",  "✓ 0",   "success"),
            ("failed",   "✕ 0",   "error"),
            ("excluded", "⊘ 0",   "warning"),
        ]:
            c = chip(text, tone=tone)
            c.setVisible(False)
            self._chips[key] = c
            self._chip_row.addWidget(c)
        self._chip_row.addStretch()
        body.addLayout(self._chip_row)

        # 로그 영역
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("작업을 시작하면 여기에 진행 내역이 누적됩니다.")
        self._log.setLineWrapMode(QTextEdit.WidgetWidth)
        body.addWidget(self._log, 1)

        self._root.addWidget(frame)



    # ══════════════════════════════════════════════
    # 로그 API
    # ══════════════════════════════════════════════
    def log(self, msg: str, level: str = None):
        msg = (msg or "").rstrip()
        if not msg:
            return
        lvl = level or _infer_level(msg)
        ts = time.strftime("%H:%M:%S")
        self._entries.append((ts, lvl, msg))
        self._log.append(self._fmt_line(ts, lvl, msg))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _fmt_line(self, ts: str, lvl: str, msg: str) -> str:
        pal = current_palette()
        color = {
            "err":  pal["log_err"], "warn": pal["log_warn"],
            "ok":   pal["log_ok"],  "info": pal["text_sub"],
        }.get(lvl, pal["text_sub"])
        ts_color = pal["text_dim"]
        safe = (msg.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;").replace(" ", "&nbsp;"))
        return (
            f'<div style="margin:0; line-height:1.5;">'
            f'<span style="color:{ts_color}; font-size:10px;">{ts}</span>&nbsp;&nbsp;'
            f'<span style="color:{color}; font-size:11px;">{safe}</span></div>'
        )

    def _render_all(self):
        self._log.clear()
        for ts, lvl, msg in self._entries:
            self._log.append(self._fmt_line(ts, lvl, msg))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def get_proofreading_log(self):
        """완료 보고서용 — HWP 배관 로그를 제외한 교정교열 관련 로그만 반환.
        반환: [(time_str, level, msg), ...]"""
        out = []
        for ts, lvl, msg in self._entries:
            if any(kw in msg for kw in _PLUMBING_KW):
                continue
            out.append((ts, lvl, msg))
        return out

    # ══════════════════════════════════════════════
    # 요약칩 API
    # ══════════════════════════════════════════════
    def set_detected(self, n: int):
        self._set_chip("detected", f"교정 {n}", n > 0)

    def set_summary(self, applied: int = None, failed: int = None,
                    excluded: int = None):
        if applied is not None:
            self._set_chip("applied", f"✓ {applied} 적용", True)
        if failed is not None:
            self._set_chip("failed", f"✕ {failed} 실패", failed > 0)
        if excluded is not None:
            self._set_chip("excluded", f"⊘ {excluded} 제외", excluded > 0)

    def _set_chip(self, key: str, text: str, visible: bool):
        c = self._chips[key]
        c.setText(text)
        c.setVisible(visible)

    def clear(self):
        """새 파일에서만 호출 — 로그/요약 초기화."""
        self._entries.clear()
        self._log.clear()
    # ══════════════════════════════════════════════
    # 테마
    # ══════════════════════════════════════════════
    def refresh_theme(self):
        self._render_all()
        for c in self._chips.values():
            restyle(c)
