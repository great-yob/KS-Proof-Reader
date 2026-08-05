"""
ui/widgets/login_dialog.py — 사내 계정 로그인 **= 앱 실행 게이트**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works(근태앱)와 동일한 입력 규칙으로 로그인한다: 이메일 또는 사번(프리픽스).
인증은 core.auth(Supabase Auth password grant)가 담당한다.

⚠ **로그인은 선택이 아니라 게이트다(2026-08-05).** 퍼블릭 릴리스라 누구나 앱을 받을
수 있는 반면 AI/사전 API는 무료 한도를 공유하므로, `require_login()`이 통과하기 전에는
main.py가 MainWindow를 만들지 않는다. 게이트 모드에서는:
  · 저장된 세션(DPAPI)을 먼저 조용히 검증하고, 성공하면 입력 없이 즉시 통과한다.
  · 취소/닫기 = **앱 종료**(취소 버튼 문구도 '종료').
  · 서버에 못 닿았을 때는 core.auth의 오프라인 유예(7일)가 통과를 결정한다.
개발 편의: 프리즈드가 **아닐 때만** 환경변수 KS_SKIP_LOGIN=1로 게이트를 건너뛴다
(배포본에서는 무시 — 있으나 마나 한 우회로를 남기지 않는다).
"""

import os
import sys

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit

from ui.widgets.components import label, sub_label, title_label, make_button, IconLabel
from ui.styles.theme import current_palette
from ui.workers.login_worker import LoginWorker, SessionRestoreWorker


class LoginDialog(QDialog):
    logged_in = Signal(dict)       # 성공 시 user dict

    def __init__(self, parent=None, gate: bool = False):
        super().__init__(parent)
        self.setWindowTitle("로그인")
        self.setModal(True)
        self.setMinimumWidth(380)
        self._gate = gate
        self._worker = None
        self._restore_worker = None
        self.user = None            # 성공 시 user dict(게이트 호출측이 읽는다)
        self._mode = None           # "verified" | "offline"
        self._grace_days = None     # offline일 때 잔여 유예 일수
        self._build_ui()
        self.refresh_theme()
        if gate:
            self._start_restore()

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
            "사내 계정으로 로그인해야 사용할 수 있습니다."
            "\n로그인하면 공유 용어 사전(동기화·큐레이션)도 함께 활성화됩니다.", wrap=True))

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
        # 게이트에서는 취소 = 앱 종료 — 문구로 결과를 분명히 알린다.
        self._cancel = make_button("종료" if self._gate else "취소",
                                   variant="ghost", on_click=self.reject)
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

    # ── 게이트: 저장된 세션 자동 검증 ────────────────
    def _start_restore(self):
        """저장된 사내 세션(DPAPI)을 서버 검증한다. 성공하면 입력 없이 통과."""
        self._set_busy(True, "사내 계정 확인 중…")
        self._restore_worker = SessionRestoreWorker(self)
        self._restore_worker.done.connect(self._on_restored)
        self._restore_worker.start()

    def _on_restored(self, res: dict):
        # 워커 소유자가 이 다이얼로그다 — accept() 전에 반드시 종료시킨다(파괴 중 실행 방지).
        if self._restore_worker is not None:
            self._restore_worker.wait()
            self._restore_worker = None
        self._set_busy(False)
        if res.get("ok"):
            self.user = res.get("user") or {}
            self._mode = res.get("mode") or "verified"
            self._grace_days = res.get("grace_days_left")
            self.accept()
            return
        # 실패 — 로그인 폼으로. 사유를 그대로 알려야 사용자가 무엇을 해야 할지 안다.
        reason = res.get("reason")
        if reason == "rejected":
            self._show_err("세션이 만료되었습니다. 다시 로그인해 주세요.")
        elif reason == "offline_expired":
            self._show_err(
                f"오프라인 상태가 {_policy_grace_days()}일을 넘었습니다.\n"
                "네트워크에 연결한 뒤 다시 로그인해 주세요.")
        elif reason == "no_config":
            self._show_err("서버 설정이 없습니다(관리자 문의).")
        self._id.setFocus()

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
        self.user = res.get("user") or {}
        self._mode = "verified"
        # 로그인 성공 후 후속 처리(헤더 갱신·동기화)는 호출측 슬롯에서 일어난다.
        #   그 과정의 어떤 예외도 앱을 종료시키지 않도록 방어한다(인증 실패가 앱을
        #   죽이지 않는다는 원칙은 게이트가 돼도 그대로다).
        try:
            self.logged_in.emit(self.user)
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

    def _set_busy(self, busy: bool, msg: str = "로그인 중…"):
        self._btn.setEnabled(not busy)
        self._btn.setText(msg if busy else "로그인")
        self._id.setEnabled(not busy)
        self._pw.setEnabled(not busy)
        # 세션 확인 중에도 '종료'는 살려 둔다 — 회선이 죽었을 때 12초를 갇혀 있지 않게.
        self._cancel.setEnabled(not busy or self._gate)

    def closeEvent(self, event):
        # 워커가 살아있는 채로 닫히면 파괴 중 실행 크래시 → 끝날 때까지 대기(로그인은 빠름).
        for w in (self._worker, self._restore_worker):
            if w is not None and w.isRunning():
                w.wait()
        event.accept()

    def reject(self):
        # 게이트에서 사용자가 '종료'를 눌렀는데 세션 확인 워커가 아직 돌고 있으면,
        #   여기서 기다려 주지 않으면 다이얼로그가 실행 중 파괴돼 앱이 죽는다.
        w = self._restore_worker
        if w is not None and w.isRunning():
            try:
                w.disconnect()
            except Exception:
                pass
            w.wait()
            self._restore_worker = None
        super().reject()


def _policy_grace_days() -> int:
    """core.auth의 유예 일수(문구용). import 실패 시 기본 7."""
    try:
        from core import auth
        return int(auth.GRACE_DAYS)
    except Exception:
        return 7


def require_login(parent=None) -> "dict | None":
    """**실행 게이트** — 사내 계정이 확인될 때까지 진행을 막는다.

    반환: 통과 시 user dict(개발 우회 시 최소 dict), 사용자가 종료를 택하면 None.
    호출측(main.py)은 None이면 앱을 띄우지 않고 종료한다.
    """
    if not getattr(sys, "frozen", False) and os.environ.get("KS_SKIP_LOGIN") == "1":
        # 개발 전용 우회 — 배포본(frozen)에서는 이 분기에 절대 들어오지 않는다.
        return {"name": "개발 모드", "role": "employee", "_dev_bypass": True}
    dlg = LoginDialog(parent, gate=True)
    dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    ok = dlg.exec()
    user = dlg.user if ok else None
    dlg.deleteLater()
    return user
