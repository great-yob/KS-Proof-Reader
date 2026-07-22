"""
ui/widgets/status_footer.py — 하단 영구 상태바
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
상태 텍스트 + 진행바(실행 중에만) + 취소 + 컨텍스트 1차 액션 버튼.
워크스페이스 전 단계에 걸쳐 항상 표시되며, 현재 단계에 맞는 주 행동을
한 곳에서 제공한다.
"""

from PySide6.QtCore import Signal, QVariantAnimation, QEasingCurve
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QWidget, QVBoxLayout

from ui.widgets.components import label, IconButton
from ui.styles.theme import restyle


class StatusFooter(QFrame):
    primary_clicked = Signal()
    cancel_clicked  = Signal()
    reset_clicked   = Signal()
    errata_clicked  = Signal()
    folder_clicked  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "footer")
        self.setFixedHeight(60)
        self._build_ui()
        
        self._aurora_anim = QVariantAnimation(self)
        self._aurora_anim.setDuration(15000) # 15.0s loop for a very slow, elegant dynamic feel
        self._aurora_anim.setEasingCurve(QEasingCurve.Linear)
        self._aurora_anim.setLoopCount(-1) # Infinite loop
        self._aurora_anim.setStartValue(0.0)
        self._aurora_anim.setEndValue(1.0)
        self._aurora_anim.valueChanged.connect(self._on_aurora_anim)
        self.set_idle("준비됨")

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(14)

        self._status = label("준비됨", role="sub")
        lay.addWidget(self._status)
        lay.addStretch()

        self._cancel_btn = IconButton("x", text="취소", variant="danger_solid",
                                      role="accent_fg", size=14,
                                      on_click=self.cancel_clicked.emit)
        self._cancel_btn.setVisible(False)
        lay.addWidget(self._cancel_btn)

        self._reset_btn = IconButton("rotate-ccw", text="초기화", variant="ghost",
                                     role="text_sub", size=14,
                                     on_click=self.reset_clicked.emit)
        self._reset_btn.setVisible(False)
        lay.addWidget(self._reset_btn)

        self._errata_btn = IconButton("table", text="정오표 생성", variant="ghost",
                                      role="text_sub", size=14,
                                      on_click=self.errata_clicked.emit)
        self._errata_btn.setVisible(False)
        lay.addWidget(self._errata_btn)

        self._folder_btn = IconButton("folder-open", text="폴더 열기", variant="ghost",
                                      role="text_sub", size=14,
                                      on_click=self.folder_clicked.emit)
        self._folder_btn.setVisible(False)
        lay.addWidget(self._folder_btn)

        self._primary = IconButton("", text="", variant="primary",
                                   role="accent_fg", size=16,
                                   on_click=self.primary_clicked.emit)
        self._primary.setMinimumWidth(170)
        self._primary.setMinimumHeight(38)
        self._primary.setVisible(False)
        lay.addWidget(self._primary)

    # ══════════════════════════════════════════════
    # 상태 API
    # ══════════════════════════════════════════════
    def set_status(self, text: str):
        self._status.setText(text)

    def set_progress(self, value: int, message: str = ""):
        val = max(0, min(100, value))
        if message:
            self._status.setText(message)

        busy_text = getattr(self, '_busy_text', '처리 중')
        self._primary.setText(f"{busy_text} ({val}%)")

    def _on_aurora_anim(self, phase: float):
        import math
        t = phase * 2 * math.pi
        
        # 주기를 완벽하게 일치시키기 위해 정수 배수만 사용 (루프 점프 방지)
        cx = 0.5 + 0.6 * math.sin(t)
        cy = 0.5 + 0.6 * math.cos(t * 2)
        
        fx = cx + 0.3 * math.sin(t * 3)
        fy = cy + 0.3 * math.cos(t * 2)
        
        radius = 1.3 + 0.4 * math.sin(t)
        
        # 방사형 그라데이션으로 선형(선) 경계를 없애고 퍼지는 물감처럼 배합
        qss = f"""
            QPushButton:disabled {{
                background: qradialgradient(cx:{cx:.3f}, cy:{cy:.3f}, radius:{radius:.3f}, fx:{fx:.3f}, fy:{fy:.3f},
                    stop:0 #4f46e5, stop:0.4 #7e22ce, stop:0.7 #0f766e, stop:1 #111827);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 10px 20px;
                font-weight: 700;
            }}
        """
        self._primary.setStyleSheet(qss)

    def set_idle(self, status: str = "준비됨"):
        self._status.setText(status)
        self._cancel_btn.setVisible(False)
        self._reset_btn.setVisible(False)
        self._errata_btn.setVisible(False)
        self._folder_btn.setVisible(False)
        self._reset_cancel()
        
        if hasattr(self, '_aurora_anim'):
            self._aurora_anim.stop()
        self._primary.setStyleSheet("")

    def set_busy(self, status: str = "처리 중…", primary_text: str = "처리 중"):
        self._status.setText(status)
        self._cancel_btn.setVisible(True)
        self._reset_btn.setVisible(False)
        self._errata_btn.setVisible(False)
        self._folder_btn.setVisible(False)
        self._reset_cancel()
        
        self._busy_text = primary_text
        self.set_primary(primary_text, enabled=False, visible=True)
        
        if self._aurora_anim.state() != QVariantAnimation.Running:
            self._aurora_anim.start()

    # ── 1차 액션 버튼 ──────────────────────────────
    def set_primary(self, text: str = None, *, icon: str = None,
                    enabled: bool = True, visible: bool = True,
                    variant: str = "primary", show_reset: bool = False):
        if text is not None:
            self._primary.setText(text)
        if icon is not None:
            self._primary.set_icon_name(icon)
        else:
            self._primary.set_icon_name("")
        self._primary.setProperty("variant", variant)
        self._primary.set_icon_role(self._icon_role_for(variant))
        restyle(self._primary)
        self._primary.setEnabled(enabled)
        self._primary.setVisible(visible)
        self._reset_btn.setVisible(show_reset)

    # ── 결과 화면 전용 액션(정오표/폴더 열기) ─────────
    def set_result_actions(self, visible: bool, has_errata: bool = False):
        self._errata_btn.setText("정오표 열기" if has_errata else "정오표 생성")
        self._errata_btn.setVisible(visible)
        self._folder_btn.setVisible(visible)

    @staticmethod
    def _icon_role_for(variant: str) -> str:
        return {"primary": "accent_fg", "success": "success",
                "danger": "error"}.get(variant, "text_sub")

    # ── 취소 버튼 ──────────────────────────────────
    def mark_cancelling(self):
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("취소 중…")

    def _reset_cancel(self):
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("취소")
