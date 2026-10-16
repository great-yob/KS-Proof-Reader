"""
ui/widgets/password_dialog.py — 비밀번호 변경 (앱 전용 회원)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
두 경로에서 열린다:
  · **강제(forced)** — 초기 비밀번호(ks2026)로 만든 계정의 첫 로그인. 로그인 게이트
    (`login_dialog.require_login`)가 MainWindow를 만들기 **전에** 띄우고, 바꾸지 않으면
    통과시키지 않는다. 취소 문구가 '종료'인 이유다(게이트에서 reject = 앱 종료).
  · 선택 — 헤더 계정 메뉴의 '비밀번호 변경'.

ks-works 직원 계정은 이 창을 쓰지 않는다(비밀번호는 ks-works가 관리 — core/auth.py).
실제 변경과 강제 플래그 해제는 서버(Edge Function ksai-change-password)가 함께 한다.

⚠ 기본 버튼은 '변경'이다 — login_dialog의 Enter 사고(첫 autoDefault 버튼이 '종료'라
  Enter 한 번이 앱을 끄던 것)와 같은 함정을 피한다.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit

from ui.widgets.components import label, sub_label, title_label, make_button, IconLabel
from ui.styles.theme import current_palette
from ui.workers.account_worker import AccountWorker

MIN_LENGTH = 6            # 서버(ksai-change-password)와 같은 값 — 최종 판정은 서버
from core.member_admin import INITIAL_PASSWORD as _INITIAL_PASSWORD   # noqa: E402


class PasswordChangeDialog(QDialog):
    def __init__(self, parent=None, forced: bool = False, gate: bool = False):
        super().__init__(parent)
        self.setWindowTitle("비밀번호 변경")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._forced = forced
        self._gate = gate
        self._worker = None
        self._build_ui()
        self.refresh_theme()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconLabel("user", role="accent", size=20))   # 번들 아이콘엔 자물쇠가 없다
        head.addWidget(title_label("비밀번호 변경"))
        head.addStretch()
        root.addLayout(head)

        if self._forced:
            msg = ("초기 비밀번호를 사용 중입니다.\n"
                   "계속하려면 새 비밀번호로 변경해 주세요.")
        else:
            msg = "현재 비밀번호를 확인한 뒤 새 비밀번호로 변경합니다."
        root.addWidget(sub_label(msg + f"\n새 비밀번호는 {MIN_LENGTH}자 이상이어야 합니다.",
                                 wrap=True))

        self._cur = self._field("현재 비밀번호")
        self._new = self._field("새 비밀번호")
        self._new2 = self._field("새 비밀번호 확인")
        for w in (self._cur, self._new, self._new2):
            root.addWidget(w)
            w.returnPressed.connect(self._submit)

        self._err = label("", tone="error", wrap=True)
        self._err.setVisible(False)
        root.addWidget(self._err)

        row = QHBoxLayout()
        row.addStretch()
        self._cancel = make_button("종료" if (self._forced and self._gate) else "취소",
                                   variant="ghost", on_click=self.reject)
        self._btn = make_button("변경", variant="primary", on_click=self._submit)
        row.addWidget(self._cancel)
        row.addWidget(self._btn)
        root.addLayout(row)

        self._cancel.setAutoDefault(False)
        self._btn.setAutoDefault(True)
        self._btn.setDefault(True)
        self._cur.setFocus()

    @staticmethod
    def _field(placeholder: str) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setEchoMode(QLineEdit.Password)
        return le

    def refresh_theme(self):
        pal = current_palette()
        self.setStyleSheet(f"QDialog {{ background: {pal['bg']}; }}")
        le = (f"QLineEdit{{background:{pal['surface']};color:{pal['text']};"
              f"border:1px solid {pal['border_strong']};border-radius:8px;padding:9px 11px;"
              f"font-size:14px;selection-background-color:{pal['accent']};"
              f"selection-color:{pal['accent_fg']};}}"
              f"QLineEdit:focus{{border:1px solid {pal['accent']};}}")
        for w in (self._cur, self._new, self._new2):
            w.setStyleSheet(le)

    # ── 동작 ──────────────────────────────────────────
    def _submit(self):
        if self._worker is not None and self._worker.isRunning():
            return
        cur, new, new2 = self._cur.text(), self._new.text(), self._new2.text()
        if not cur or not new:
            return self._show_err("현재 비밀번호와 새 비밀번호를 입력하세요.")
        if len(new) < MIN_LENGTH:
            return self._show_err(f"새 비밀번호는 {MIN_LENGTH}자 이상이어야 합니다.")
        if new != new2:
            return self._show_err("새 비밀번호가 서로 일치하지 않습니다.")
        if new == cur:
            return self._show_err("현재 비밀번호와 다른 비밀번호를 입력하세요.")
        if new == _INITIAL_PASSWORD:
            return self._show_err("초기 비밀번호는 사용할 수 없습니다.")
        self._set_busy(True)
        self._err.setVisible(False)
        self._worker = AccountWorker("auth.change_password", cur, new, parent=self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, res: dict):
        if self._worker is not None:
            self._worker.wait()
            self._worker = None
        self._set_busy(False)
        if not res.get("ok"):
            self._show_err(res.get("error") or "비밀번호를 변경하지 못했습니다.")
            return
        self.accept()

    def _show_err(self, msg: str):
        pal = current_palette()
        self._err.setStyleSheet(f"color:{pal['error']}; background:transparent; border:none; font-size:12px;")
        self._err.setText(msg)
        self._err.setVisible(True)

    def _set_busy(self, busy: bool):
        self._btn.setEnabled(not busy)
        self._btn.setText("변경 중…" if busy else "변경")
        for w in (self._cur, self._new, self._new2):
            w.setEnabled(not busy)
        self._cancel.setEnabled(not busy)

    def _wait_worker(self):
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.disconnect()
            except Exception:
                pass
            w.wait()
        self._worker = None

    def closeEvent(self, event):
        self._wait_worker()
        event.accept()

    def reject(self):
        self._wait_worker()
        super().reject()
