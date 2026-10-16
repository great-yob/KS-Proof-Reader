"""
ui/widgets/member_admin_dialog.py — 사용자 관리 (관리자 전용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ks-works 직원으로 등록하지 않고 **이 앱만** 쓰는 사용자를 추가·관리한다(2026-10-16).
  · 추가 — 로그인 ID(사내 ID면 도메인 자동, 아니면 전체 이메일) + 이름 (+ 관리자).
    초기 비밀번호는 ks2026이며 첫 로그인 때 변경이 강제된다.
    ks-works **퇴사자** ID를 넣으면 확인 후 그 계정을 앱 전용으로 전환한다(ks-works는
    퇴사 기록 때문에 계속 차단된다 — 같은 로그인 ID는 계정이 하나뿐이라 새로 만들 수 없다).
  · 비밀번호 초기화 — ks2026으로 되돌리고 다음 로그인 때 다시 변경을 강제.
  · 사용 중지/재개 — 로그인 차단(저장 세션도 다음 실행에 거부된다).
  · 명단 계정 일괄 생성 — DB 명단(ksai_roster)에서 계정이 없는 사람을 한 번에 만든다.
  · 공유 뇌 허용/해제 — 이벤트 기여자 지정(ksai_brain_contributors). 관리자는 항상 포함.
목록은 명단·계정·기여자를 합친 '사람' 단위다. 전화번호 등 개인정보는 서버가 내보내지
않으므로 이 화면에 없다(DB에만 있다).

ks-works 직원 계정은 여기 나오지 않고 다룰 수도 없다 — 직원은 ks-works가 관리하며
지금처럼 그 계정으로 로그인한다. 권한은 서버가 판정한다(ksai-admin-members).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
)

from ui.widgets.components import sub_label, title_label, make_button, IconLabel, divider
from ui.styles.theme import current_palette
from ui.workers.account_worker import AccountWorker
from core.member_admin import INITIAL_PASSWORD

_COLS = ("이름", "로그인 ID", "소속", "공유 뇌", "상태")


def _status_text(m: dict) -> str:
    kind = m.get("kind")
    if kind == "roster":
        return "계정 없음"
    if kind == "employee":
        return "ks-works 직원"
    if kind == "former_employee":
        return "ks-works 퇴사자"
    if kind != "member":
        return "확인 필요"
    if m.get("disabled_at"):
        s = "사용 중지"
    elif m.get("must_change_password"):
        s = "초기 비밀번호"
    else:
        s = "사용 중"
    return s + (" · 관리자" if m.get("role") == "admin" else "")


def _affiliation(m: dict) -> str:
    return " ".join(x for x in (m.get("department"), m.get("position")) if x)


class MemberAdminDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("사용자 관리")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(780, 620)
        self._members = []
        self._workers = []
        self._pending_msg = ""
        self._build_ui()
        self.refresh_theme()
        self._reload()

    # ── UI ────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconLabel("user", role="accent", size=18))
        head.addWidget(title_label("사용자 관리"))
        head.addStretch()
        self._provision_btn = make_button("명단 계정 일괄 생성", variant="ghost",
                                          on_click=self._provision)
        self._reload_btn = make_button("새로고침", variant="ghost", icon="rotate-ccw",
                                       on_click=self._reload)
        for b in (self._provision_btn, self._reload_btn):
            b.setAutoDefault(False)
            head.addWidget(b)
        root.addLayout(head)

        root.addWidget(sub_label(
            "이 앱만 쓰는 사용자를 추가합니다 — ks-works에는 등록되지 않습니다.\n"
            f"초기 비밀번호는 {INITIAL_PASSWORD}이며, 첫 로그인 때 새 비밀번호로 변경해야 합니다.\n"
            "ks-works 재직자는 지금처럼 ks-works 계정으로 로그인하므로 여기 추가하지 않습니다.\n"
            "ks-works 퇴사자는 같은 ID로 추가하면 앱 전용으로 전환됩니다(ks-works는 계속 차단).\n"
            "공유 뇌 ○ 표시자(와 관리자)의 교정 결정만 사내 용어 학습에 쓰입니다.",
            wrap=True))

        # 추가 폼
        form = QHBoxLayout()
        form.setSpacing(8)
        self._login = QLineEdit()
        self._login.setPlaceholderText("로그인 ID (예: hong 또는 hong@example.com)")
        self._name = QLineEdit()
        self._name.setPlaceholderText("이름")
        self._admin = QCheckBox("관리자")
        self._add_btn = make_button("추가", variant="primary", on_click=self._create)
        self._add_btn.setAutoDefault(True)
        self._add_btn.setDefault(True)
        form.addWidget(self._login, 3)
        form.addWidget(self._name, 2)
        form.addWidget(self._admin)
        form.addWidget(self._add_btn)
        root.addLayout(form)
        self._login.returnPressed.connect(self._create)
        self._name.returnPressed.connect(self._create)

        root.addWidget(divider())

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._sync_actions)
        root.addWidget(self._table, 1)

        acts = QHBoxLayout()
        acts.setSpacing(8)
        self._status = sub_label("", wrap=True)
        acts.addWidget(self._status, 1)
        self._brain_btn = make_button("공유 뇌 허용", variant="ghost", on_click=self._toggle_brain)
        self._reset_btn = make_button("비밀번호 초기화", variant="ghost", on_click=self._reset)
        self._toggle_btn = make_button("사용 중지", variant="ghost", on_click=self._toggle)
        close_btn = make_button("닫기", variant="ghost", on_click=self.accept)
        for b in (self._brain_btn, self._reset_btn, self._toggle_btn, close_btn):
            b.setAutoDefault(False)
            acts.addWidget(b)
        root.addLayout(acts)
        self._sync_actions()

    def refresh_theme(self):
        pal = current_palette()
        le = (f"QLineEdit{{background:{pal['surface']};color:{pal['text']};"
              f"border:1px solid {pal['border_strong']};border-radius:8px;padding:8px 10px;}}"
              f"QLineEdit:focus{{border:1px solid {pal['accent']};}}")
        self.setStyleSheet(
            f"QDialog {{ background: {pal['bg']}; }}"
            + le
            + f"QTableWidget{{background:{pal['surface']};color:{pal['text']};"
              f"border:1px solid {pal['border']};border-radius:8px;"
              f"selection-background-color:{pal['accent_soft']};selection-color:{pal['text']};}}"
              f"QTableWidget::item{{padding:6px 8px;}}"
              f"QHeaderView::section{{background:{pal['surface_alt']};color:{pal['text_sub']};"
              f"border:none;border-bottom:1px solid {pal['border']};padding:6px 8px;"
              f"font-weight:600;}}")

    # ── 데이터 ────────────────────────────────────────
    def _run(self, target: str, *args, on_done=None):
        w = AccountWorker(target, *args, parent=self)
        w.done.connect(lambda res, w=w: self._finish(w, res, on_done))
        self._workers.append(w)
        self._set_busy(True)
        w.start()

    def _finish(self, w, res, on_done):
        w.wait()
        if w in self._workers:
            self._workers.remove(w)
        self._set_busy(bool(self._workers))
        if on_done is not None:
            on_done(res)

    def _reload(self):
        self._status.setText("목록을 불러오는 중…")
        self._run("member_admin.list_members", on_done=self._on_list)

    def _on_list(self, res: dict):
        if not res.get("ok"):
            self._status.setText(res.get("error") or "목록을 불러오지 못했습니다.")
            return
        self._members = res.get("people") or []
        self._table.setRowCount(len(self._members))
        for r, m in enumerate(self._members):
            vals = (m.get("name") or "", m.get("login_email") or "", _affiliation(m),
                    "○" if m.get("brain_access") else "", _status_text(m))
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(r, c, it)
        n_acc = sum(1 for m in self._members if m.get("kind") == "member")
        n_wait = sum(1 for m in self._members if m.get("kind") == "roster")
        n_brain = sum(1 for m in self._members if m.get("brain_access"))
        msg = (f"앱 계정 {n_acc}명 · 계정 없음 {n_wait}명 · 공유 뇌 {n_brain}명(+관리자)"
               if self._members else "아직 등록된 사용자가 없습니다.")
        if self._pending_msg:
            msg = f"{self._pending_msg}  ·  {msg}"
            self._pending_msg = ""
        self._status.setText(msg)
        self._sync_actions()

    def _selected(self):
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        r = rows[0].row()
        return self._members[r] if 0 <= r < len(self._members) else None

    def _sync_actions(self):
        m = self._selected()
        busy = bool(self._workers)
        # 비밀번호·사용 중지는 **앱 계정**에만 — 직원 계정은 ks-works가 관리한다.
        is_member = m is not None and m.get("kind") == "member"
        self._reset_btn.setEnabled(is_member and not busy and not m.get("disabled_at"))
        self._toggle_btn.setEnabled(is_member and not busy)
        self._toggle_btn.setText("사용 재개" if (m and m.get("disabled_at")) else "사용 중지")
        self._brain_btn.setEnabled(m is not None and not busy and bool(m.get("login_email")))
        self._brain_btn.setText("공유 뇌 해제" if (m and m.get("brain_access")) else "공유 뇌 허용")

    def _set_busy(self, busy: bool):
        self._add_btn.setEnabled(not busy)
        self._reload_btn.setEnabled(not busy)
        self._provision_btn.setEnabled(not busy)
        self._sync_actions()

    # ── 동작 ──────────────────────────────────────────
    def _create(self):
        if self._workers:
            return
        login, name = self._login.text().strip(), self._name.text().strip()
        if not login or not name:
            self._status.setText("로그인 ID와 이름을 입력하세요.")
            return
        self._submit_create(login, name, self._admin.isChecked(), allow_former=False)

    def _submit_create(self, login: str, name: str, admin: bool, allow_former: bool):
        self._status.setText("사용자를 추가하는 중…")
        self._run("member_admin.create_member", login, name, admin, allow_former,
                  on_done=lambda res: self._on_created(res, login, name, admin))

    def _on_created(self, res: dict, login: str, name: str, admin: bool):
        if not res.get("ok"):
            if res.get("code") == "former_employee":
                # 퇴사자 — 같은 로그인 ID의 계정을 앱 전용으로 전환할지 묻는다.
                ok = QMessageBox.question(
                    self, "퇴사자 계정 전환",
                    f"'{login}'은(는) ks-works 퇴사자 계정입니다.\n\n"
                    "이 계정을 앱 전용 사용자로 전환할까요?\n"
                    f"  · 비밀번호가 초기 비밀번호({INITIAL_PASSWORD})로 바뀝니다.\n"
                    "  · ks-works 로그인은 계속 막힙니다(퇴사 기록은 그대로).")
                if ok == QMessageBox.Yes:
                    self._submit_create(login, name, admin, allow_former=True)
                else:
                    self._status.setText("전환을 취소했습니다.")
                return
            self._status.setText(res.get("error") or "사용자를 추가하지 못했습니다.")
            return
        m = res.get("member") or {}
        self._login.clear()
        self._name.clear()
        self._admin.setChecked(False)
        verb = "앱 전용 사용자로 전환했습니다" if res.get("converted") else "추가했습니다"
        QMessageBox.information(
            self, "사용자 추가",
            f"{m.get('name')} 님을 {verb}.\n\n"
            f"로그인 ID: {m.get('login_email')}\n"
            f"초기 비밀번호: {res.get('initial_password') or INITIAL_PASSWORD}\n\n"
            "첫 로그인 때 새 비밀번호로 변경하게 됩니다.")
        self._reload()

    def _reset(self):
        m = self._selected()
        if not m:
            return
        ok = QMessageBox.question(
            self, "비밀번호 초기화",
            f"{m.get('name')} 님의 비밀번호를 초기 비밀번호({INITIAL_PASSWORD})로 되돌릴까요?\n"
            "다음 로그인 때 새 비밀번호로 변경하게 됩니다.")
        if ok != QMessageBox.Yes:
            return
        self._status.setText("초기화하는 중…")
        self._run("member_admin.reset_password", m.get("auth_user_id"),
                  on_done=lambda res: self._after(res, f"비밀번호를 초기화했습니다({INITIAL_PASSWORD})."))

    def _toggle(self):
        m = self._selected()
        if not m:
            return
        disable = not m.get("disabled_at")
        if disable:
            ok = QMessageBox.question(
                self, "사용 중지",
                f"{m.get('name')} 님의 로그인을 막을까요?\n(언제든 '사용 재개'로 되돌릴 수 있습니다)")
            if ok != QMessageBox.Yes:
                return
        self._status.setText("상태를 바꾸는 중…")
        self._run("member_admin.set_disabled", m.get("auth_user_id"), disable,
                  on_done=lambda res: self._after(
                      res, "사용을 중지했습니다." if disable else "사용을 재개했습니다."))

    def _provision(self):
        if self._workers:
            return
        n_wait = sum(1 for m in self._members if m.get("kind") == "roster")
        ok = QMessageBox.question(
            self, "명단 계정 일괄 생성",
            f"명단에서 계정이 없는 {n_wait}명의 계정을 만들까요?\n\n"
            f"  · 초기 비밀번호: {INITIAL_PASSWORD} (첫 로그인 때 변경)\n"
            "  · ks-works 퇴사자는 같은 ID의 계정을 앱 전용으로 전환합니다.\n"
            "  · ks-works 재직자는 건너뜁니다(기존 계정으로 로그인).")
        if ok != QMessageBox.Yes:
            return
        self._status.setText("계정을 만드는 중…")
        self._run("member_admin.provision_roster", on_done=self._on_provisioned)

    def _on_provisioned(self, res: dict):
        if not res.get("ok"):
            self._status.setText(res.get("error") or "일괄 생성에 실패했습니다.")
            return
        failed = res.get("failed") or []
        lines = [f"새로 만듦 {res.get('created', 0)}명 · 퇴사자 전환 {res.get('converted', 0)}명"
                 f" · 재직자 건너뜀 {res.get('skipped_employee', 0)}명"]
        if failed:
            lines.append(f"\n실패 {len(failed)}명:")
            lines += [f"  · {f.get('name')} ({f.get('login_email')}) — {f.get('error')}"
                      for f in failed]
        lines.append(f"\n초기 비밀번호: {res.get('initial_password') or INITIAL_PASSWORD}")
        QMessageBox.information(self, "명단 계정 일괄 생성", "\n".join(lines))
        self._pending_msg = f"일괄 생성 완료 — 실패 {len(failed)}명" if failed else "일괄 생성 완료"
        self._reload()

    def _toggle_brain(self):
        m = self._selected()
        if not m:
            return
        enable = not m.get("brain_access")
        self._status.setText("공유 뇌 권한을 바꾸는 중…")
        self._run("member_admin.set_brain_access", m.get("login_email"), enable,
                  on_done=lambda res: self._after(
                      res, f"{m.get('name')} 님 공유 뇌 {'허용' if enable else '해제'}"))

    def _after(self, res: dict, ok_msg: str):
        if not res.get("ok"):
            self._status.setText(res.get("error") or "처리하지 못했습니다.")
            return
        self._pending_msg = ok_msg      # 목록을 다시 받은 뒤 함께 보여 준다
        self._reload()

    # ── 수명 ──────────────────────────────────────────
    def _wait_workers(self):
        for w in list(self._workers):
            try:
                w.done.disconnect()
            except Exception:
                pass
            w.wait()
        self._workers.clear()

    def closeEvent(self, event):
        self._wait_workers()
        event.accept()

    def done(self, r):
        self._wait_workers()
        super().done(r)
