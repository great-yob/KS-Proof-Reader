"""
ui/widgets/curator_panel.py — 사내 용어 큐레이션 패널 (DO-5)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
큐레이터(employees.role='admin')만 진입. Supabase 후보 큐(userdict_candidates)를
보고 승인(active)/반려(rejected)/문맥의존(context_dependent)으로 전이한 뒤,
'스냅샷 배포'로 승인분을 전원에 내려보낸다(userdict_build_snapshot → 각 클라이언트 pull).

설계: docs/userdict-layer-architecture.md §5(큐레이터 패널)·§6(골드셋 게이트).
  · 페어 후보엔 동형이의어/등재 **가드 결과**를 표시하고, 가드 실패 페어는 승인을
    막는다(빌드타임 가드의 사전 노출 = #1 과교정 리스크 방어선).
  · 네트워크 연산은 전부 CuratorWorker(QThread). 미설정/오프라인이면 빈 목록(graceful).
  · core import 금지 규약은 core 계층에만 적용 — 본 파일은 ui 계층이라 core.userdict_sync 사용 OK.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QFrame, QScrollArea, QMessageBox,
)

from ui.widgets.components import (
    label, sub_label, title_label, make_button, badge, IconLabel, divider,
)
from ui.styles.theme import current_palette
from ui.workers.curator_worker import CuratorWorker


_KIND_LABEL   = {"pair": "페어", "exception": "예외"}
_STATUS_LABEL = {"pending": "대기", "active": "승인됨",
                 "rejected": "반려", "context_dependent": "문맥의존"}


class CuratorPanel(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("사내 용어 큐레이션")
        self.setModal(False)
        self.resize(760, 660)
        self._candidates = []
        self._workers = []
        self._build_ui()
        self.refresh_theme()
        self._reload(aggregate=True)

    # ── UI ────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # 헤더
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(IconLabel("clipboard-check", role="accent", size=18))
        hdr.addWidget(title_label("사내 용어 큐레이션"))
        hdr.addStretch()
        self._refresh_btn = make_button("집계·새로고침", variant="ghost",
                                        icon="rotate-ccw", on_click=lambda: self._reload(aggregate=True))
        self._deploy_btn = make_button("스냅샷 배포", variant="primary",
                                       icon="zap", on_click=self._deploy)
        hdr.addWidget(self._refresh_btn)
        hdr.addWidget(self._deploy_btn)
        root.addLayout(hdr)

        self._status_lbl = sub_label("후보를 불러오는 중…", wrap=True)
        root.addWidget(self._status_lbl)
        root.addWidget(divider())

        # 후보 목록 스크롤
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QWidget#CuratorList{background:transparent;}")
        content = QWidget()
        content.setObjectName("CuratorList")
        self._list_lay = QVBoxLayout(content)
        self._list_lay.setContentsMargins(2, 2, 2, 2)
        self._list_lay.setSpacing(8)
        self._list_lay.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def refresh_theme(self):
        pal = current_palette()
        self.setStyleSheet(f"QDialog {{ background: {pal['bg']}; }}")

    # ── 데이터 로드 ───────────────────────────────────
    def _reload(self, aggregate=False):
        self._set_busy(True)
        self._status_lbl.setText("집계 및 후보 불러오는 중…" if aggregate else "후보 불러오는 중…")
        w = CuratorWorker("load", parent=self, aggregate=aggregate)
        w.done.connect(self._on_loaded)
        w.failed.connect(self._on_failed)
        self._track(w)
        w.start()

    def _on_loaded(self, _op, result):
        self._set_busy(False)
        agg = result.get("aggregate") or {}
        self._candidates = result.get("candidates") or []
        self._populate()
        msg = self._summary_text()
        if agg:
            msg += f"   · 집계 갱신(페어 {agg.get('pairs', 0)} · 예외 {agg.get('exceptions', 0)})"
        self._status_lbl.setText(msg)

    def _summary_text(self) -> str:
        from collections import Counter
        c = Counter(x.get("status") for x in self._candidates)
        return (f"전체 {len(self._candidates)}건 — 대기 {c.get('pending', 0)} · "
                f"승인 {c.get('active', 0)} · 문맥의존 {c.get('context_dependent', 0)} · "
                f"반려 {c.get('rejected', 0)}")

    def _populate(self):
        # 기존 카드 제거(끝 stretch 보존)
        while self._list_lay.count() > 1:
            it = self._list_lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        if not self._candidates:
            empty = sub_label("승인 대기 중인 후보가 없습니다. 사용자 교정이 쌓이면 자동 집계됩니다.",
                              wrap=True)
            self._list_lay.insertWidget(0, empty)
            return
        for c in self._candidates:
            self._list_lay.insertWidget(self._list_lay.count() - 1, self._make_card(c))

    def _make_card(self, c: dict) -> QFrame:
        pal = current_palette()
        kind = c.get("kind")
        card = QFrame()
        card.setObjectName("cand_card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        # 1행: 종류 + 본문 + 카테고리 + 상태
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(badge(_KIND_LABEL.get(kind, kind)))
        if kind == "exception":
            main = f"{c.get('term', '')}　·　무교정[{c.get('scope', 'all')}]"
        else:
            main = f"{c.get('original', '')}  →  {c.get('corrected', '')}"
        main_lbl = label(main)
        main_lbl.setStyleSheet(f"font-weight:700; color:{pal['text']}; background:transparent; border:none;")
        top.addWidget(main_lbl)
        top.addStretch()
        if c.get("category"):
            top.addWidget(badge(c["category"]))
        st = c.get("status", "pending")
        status_chip = label(_STATUS_LABEL.get(st, st))
        status_chip.setStyleSheet(self._status_pill(pal, st))
        top.addWidget(status_chip)
        cl.addLayout(top)

        # 2행: 합의 메타 + 가드
        try:
            agr = float(c.get("agreement")) if c.get("agreement") is not None else None
        except (TypeError, ValueError):
            agr = None
        meta = f"합의 {agr:.0%}" if agr is not None else "합의 –"
        meta += f"　·　수락 {c.get('accept_n', 0)} / 거절 {c.get('reject_n', 0)}　·　참여 {c.get('distinct_users', 0)}명"
        meta_lbl = sub_label(meta)
        cl.addWidget(meta_lbl)

        guard_ok = c.get("_guard_ok", True)
        if kind == "pair":
            greason = c.get("_guard_reason", "")
            g = label(("가드 ✓ " + greason) if guard_ok else ("가드 ✕ " + greason))
            g.setStyleSheet(
                f"font-size:12px; border:none; background:transparent; "
                f"color:{pal['success'] if guard_ok else pal['error']};")
            cl.addWidget(g)

        # 3행: 액션
        act = QHBoxLayout()
        act.setSpacing(6)
        act.addStretch()
        approve = make_button("승인", variant="success", icon="check",
                              on_click=lambda: self._set(c, card, "active"))
        if kind == "pair" and not guard_ok:
            approve.setEnabled(False)
            approve.setToolTip("동형이의어/등재 가드 실패 — 결정론 치환 위험으로 승인 불가")
        act.addWidget(approve)
        if kind == "pair":
            act.addWidget(make_button("문맥의존", variant="ghost", icon="info",
                                      on_click=lambda: self._set(c, card, "context_dependent")))
        act.addWidget(make_button("반려", variant="danger", icon="x",
                                  on_click=lambda: self._set(c, card, "rejected")))
        cl.addLayout(act)

        self._tint_card(card, st)
        return card

    @staticmethod
    def _status_pill(pal, st) -> str:
        m = {"active": ("success", "success_bg"), "rejected": ("error", "error_bg"),
             "context_dependent": ("warning", "warning_bg")}
        fg, bg = m.get(st, ("text_sub", "surface_alt"))
        return (f"color:{pal[fg]}; background:{pal.get(bg, pal['surface_alt'])}; "
                f"border:none; border-radius:10px; padding:3px 10px; font-size:11px; font-weight:700;")

    def _tint_card(self, card: QFrame, st: str):
        pal = current_palette()
        border, bg = pal["border"], pal["surface"]
        if st == "active":
            border, bg = pal["success"], pal.get("success_bg", pal["surface"])
        elif st == "rejected":
            border, bg = pal["error"], pal.get("error_bg", pal["surface"])
        elif st == "context_dependent":
            border, bg = pal.get("warning_border", pal["warning"]), pal.get("warning_bg", pal["surface"])
        card.setStyleSheet(
            f"QFrame#cand_card {{ background:{bg}; border:1px solid {border}; border-radius:8px; }}")

    # ── 액션 ──────────────────────────────────────────
    def _set(self, c: dict, card: QFrame, status: str):
        prev = c.get("status", "pending")
        if prev == status:
            return
        c["status"] = status                      # 낙관적 업데이트
        self._tint_card(card, status)
        self._status_lbl.setText(f"'{c.get('original') or c.get('term')}' → {_STATUS_LABEL.get(status)} 처리 중…")
        w = CuratorWorker("set", parent=self, cand_id=c.get("cand_id", ""), status=status)
        w.done.connect(lambda _o, _r: self._status_lbl.setText(self._summary_text()))
        w.failed.connect(lambda _o, m, cc=c, cd=card, pv=prev: self._revert(cc, cd, pv, m))
        self._track(w)
        w.start()
        # 카드 내 상태칩/요약 갱신을 위해 가벼운 리프레시
        QTimer.singleShot(0, self._refresh_status_chips)

    def _revert(self, c, card, prev, msg):
        c["status"] = prev
        self._tint_card(card, prev)
        self._refresh_status_chips()
        self._toast_error(f"상태 변경 실패: {msg}")

    def _refresh_status_chips(self):
        # 간단히 요약만 갱신(칩 개별 갱신은 재로드 시 반영)
        self._status_lbl.setText(self._summary_text())

    def _deploy(self):
        n_active = sum(1 for c in self._candidates if c.get("status") == "active")
        ok = QMessageBox.question(
            self, "스냅샷 배포",
            f"승인(active) 후보 {n_active}건으로 새 사내 용어 스냅샷을 배포합니다.\n"
            f"배포 후 각 사용자는 앱 시작 시 자동으로 내려받습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._set_busy(True)
        self._status_lbl.setText("스냅샷 배포 중…")
        w = CuratorWorker("snapshot", parent=self)
        w.done.connect(self._on_deployed)
        w.failed.connect(self._on_failed)
        self._track(w)
        w.start()

    def _on_deployed(self, _op, res):
        self._set_busy(False)
        self._status_lbl.setText(
            f"✓ 스냅샷 v{res.get('ver')} 배포 완료 — 페어 {res.get('pairs', 0)} · 예외 {res.get('exceptions', 0)}")

    def _on_failed(self, _op, msg):
        self._set_busy(False)
        self._toast_error(msg)
        self._status_lbl.setText(self._summary_text() if self._candidates else "불러오기 실패")

    def _toast_error(self, msg: str):
        QMessageBox.warning(self, "큐레이션", msg)

    def _set_busy(self, busy: bool):
        self._refresh_btn.setEnabled(not busy)
        self._deploy_btn.setEnabled(not busy)

    # ── 워커 수명 관리 ─────────────────────────────────
    def _track(self, w):
        self._workers = [x for x in self._workers if x.isRunning()]
        self._workers.append(w)
        w.finished.connect(self._prune)

    def _prune(self):
        self._workers = [x for x in self._workers if x.isRunning()]

    def closeEvent(self, event):
        for w in self._workers:
            try:
                if w.isRunning():
                    w.wait(2000)
                    if w.isRunning():
                        w.terminate()
            except Exception:
                pass
        self._workers = []
        event.accept()
