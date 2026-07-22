"""
ui/widgets/login_dialog.py — 사내 계정 로그인 (선택적 로그인)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works(근태앱)와 동일한 입력 규칙으로 로그인한다: 이메일 또는 사번(프리픽스).
로그인은 **선택**이며, 성공 시 공유 용어 뇌(동기화·큐레이션)가 활성된다. 교정 기능은
로그인과 무관하게 동작한다. 인증은 core.auth(Supabase Auth password grant)가 담당한다.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit

from ui.widgets.components import label, sub_label, title_label, make_button, IconLabel
from ui.styles.theme import current_palette
from ui.workers.login_worker import LoginWorker


class LoginDialog(QDialog):
    logged_in = Signal(dict)       # 성공 시 user dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("로그인")
        self.setModal(True)
        self.setMinimumWidth(380)
        self._worker = None
        self._build_ui()
        self.refresh_theme()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        brand.addWidget(IconLabel("spell-check", role="accent", size=20))
        brand.addWidget(title_label("KyungSung AI Editor 로그인"))
        brand.addStretch()
        root.addLayout(brand)

        root.addWidget(sub_label(
            "사내 계정으로 로그인하면 공유 용어 사전 기능이 활성화됩니다."
            "\n교정 기능은 로그인 없이도 사용할 수 있습니다.", wrap=True))

        self._id = QLineEdit()
        self._id.setPlaceholderText("사내 이메일 ID")
        self._pw = QLineEdit()
        self._pw.setPlaceholderText("비밀번호")
        self._pw.setEchoMode(QLineEdit.Password)
        root.addWidget(self._id)
        root.addWidget(self._pw)

        self._err = label("", tone="error", wrap=True)
        self._err.setVisible(False)
        root.addWidget(self._err)

        row = QHBoxLayout()
        row.addStretch()
        self._cancel = make_button("취소", variant="ghost", on_click=self.reject)
        self._btn = make_button("로그인", variant="primary", on_click=self._submit)
        row.addWidget(self._cancel)
        row.addWidget(self._btn)
        root.addLayout(row)

        self._id.returnPressed.connect(self._submit)
        self._pw.returnPressed.connect(self._submit)

    def refresh_theme(self):
        pal = current_palette()
        self.setStyleSheet(f"QDialog {{ background: {pal['bg']}; }}")
        le = (f"QLineEdit{{background:{pal['surface']};color:{pal['text']};"
              f"border:1px solid {pal['border_strong']};border-radius:8px;padding:9px 11px;"
              f"font-size:14px;selection-background-color:{pal['accent']};"
              f"selection-color:{pal['accent_fg']};}}"
              f"QLineEdit:focus{{border:1px solid {pal['accent']};}}")
        self._id.setStyleSheet(le)
        self._pw.setStyleSheet(le)

    # ── 동작 ──────────────────────────────────────────
    def _submit(self):
        email = self._id.text().strip()
        pw = self._pw.text()
        if not email or not pw:
            self._show_err("이메일 ID와 비밀번호를 입력하세요.")
            return
        self._set_busy(True)
        self._err.setVisible(False)
        self._worker = LoginWorker(email, pw, self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, res: dict):
        self._set_busy(False)
        # 워커 스레드를 완전히 종료시킨 뒤 진행한다 — 다이얼로그가 닫히며 파괴될 때
        #   스레드가 아직 돌고 있으면 "QThread: Destroyed while running"으로 앱이 죽는다.
        if self._worker is not None:
            self._worker.wait()
        if not res.get("ok"):
            self._show_err(res.get("error") or "로그인에 실패했습니다.")
            return
        # 로그인 성공 후 후속 처리(헤더 갱신·동기화)는 호출측 슬롯에서 일어난다.
        #   그 과정의 어떤 예외도 앱을 종료시키지 않도록 방어한다(선택적 로그인 원칙).
        try:
            self.logged_in.emit(res.get("user") or {})
        except Exception:
            import traceback
            self._show_err("로그인 후 처리 중 오류:\n" + traceback.format_exc())
            return
        self.accept()

    def _show_err(self, msg: str):
        pal = current_palette()
        self._err.setStyleSheet(f"color:{pal['error']}; background:transparent; border:none; font-size:12px;")
        self._err.setText(msg)
        self._err.setVisible(True)

    def _set_busy(self, busy: bool):
        self._btn.setEnabled(not busy)
        self._btn.setText("로그인 중…" if busy else "로그인")
        self._id.setEnabled(not busy)
        self._pw.setEnabled(not busy)
        self._cancel.setEnabled(not busy)

    def closeEvent(self, event):
        # 워커가 살아있는 채로 닫히면 파괴 중 실행 크래시 → 끝날 때까지 대기(로그인은 빠름).
        w = self._worker
        if w is not None and w.isRunning():
            w.wait()
        event.accept()
