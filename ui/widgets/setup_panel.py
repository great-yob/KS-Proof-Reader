"""
ui/widgets/setup_panel.py — 설정 패널 (업로드 + 옵션 통합)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
상단 드롭존/선택파일 카드 + 하단 교정 옵션(방식·범위·부가기능).
기존 upload_widget + options_widget을 한 화면으로 합쳐 화면 전환을 제거한다.
시작 버튼은 워크스페이스 하단 footer가 담당한다.
"""

import os

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QFileDialog, QGraphicsOpacityEffect
)

from ui.widgets.components import (
    label, sub_label, badge, section_card, make_button, IconLabel, title_label
)
from ui.widgets._toggle import ToggleSwitch
from ui.styles.theme import restyle


# ══════════════════════════════════════════════════════════════
# 기능 잠금 플래그 — 되살릴 땐 **여기 한 곳만** True로 바꾼다.
# ══════════════════════════════════════════════════════════════
# ⚠ 전자동(자동 일괄 적용) 잠금 — 사용자 결정 2026-08-04.
#   현재 아키텍처에선 위험이 너무 크다는 판단. 근거(2026-08-04 실측):
#   전자동의 유일한 안전망은 `confidence=="low" 자동 거절` 하나뿐인데, AI가 오탈자를
#   고치면서 문장을 손대는 교정 중 일부가 **high로 통과한다**. 확인된 부류:
#     · '부고 있다.'→'두고 있다.' — 한 글자 치환이라 모양이 정상 오탈자 교정과 구별되지
#       않는데 낱말 뜻이 달라진다(기대는 '보고'). 구조 가드로는 원리적으로 못 가른다.
#   ㉘(문장 경계 변경) 가드로 '두었으다.'→'두었으나,' 부류는 막았고, '과정 등이'
#   부류는 원래 ㉕가 잡고 있었다. 그럼에도 위 잔여 부류가 남아 있어, 사람이 볼 기회가
#   0이 되는 전자동은 아직 이르다는 결론.
#   → 되살리는 조건: 실단어 오류 판별([[realword-error-detection]]의 kiwi LM 경로)이
#     생성 결과에도 적용돼 위 부류가 high로 새지 않게 된 뒤.
AUTO_APPLY_ENABLED = False

# ⚠ 윤문(polish) 잠금 — 사용자 결정 2026-08-04.
#   오탈자·띄어쓰기조차 완전히 커버되지 않은 상태라 윤문은 테스트 자체가 불가능하다는
#   판단. 기능이 없어서가 아니라 **검증 순서** 때문에 잠근 것 — scope_polish 경로
#   (prompts.build_polish_prompt / AI_CHUNK_POLISH 등)는 그대로 살려 둔다.
POLISH_ENABLED = False


class FilePanel(QFrame):
    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._file_path = ""
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        
        frame, lay = section_card("문서 선택", "file-text")

        # 드롭존
        self._dropzone = QFrame()
        self._dropzone.setProperty("role", "dropzone")
        dz = QVBoxLayout(self._dropzone)
        dz.setAlignment(Qt.AlignCenter)
        dz.setContentsMargins(40, 40, 40, 40)
        dz.setSpacing(16)

        self._dz_icon = IconLabel("file-down", role="text_muted", size=80, stroke_width=1.2)
        dz.addWidget(self._dz_icon, alignment=Qt.AlignCenter)

        t = label("한글 원고 파일을 여기에 드래그하세요", role="h2")
        t.setAlignment(Qt.AlignCenter)
        dz.addWidget(t)
        s = sub_label(".hwp / .hwpx 지원", wrap=True)
        s.setAlignment(Qt.AlignCenter)
        dz.addWidget(s)

        browse = make_button("파일 선택", "primary", on_click=self._browse)
        browse.setFixedWidth(100)
        browse.setFixedHeight(40)
        browse.setStyleSheet("padding: 6px 12px; margin-top: 20px;")
        dz.addWidget(browse, alignment=Qt.AlignCenter)

        lay.addWidget(self._dropzone, 1)

        # 선택된 파일 카드 (초기 숨김)
        self._file_card = QFrame()
        self._file_card.setProperty("role", "dropzone_selected")
        fc = QVBoxLayout(self._file_card)
        fc.setAlignment(Qt.AlignCenter)
        fc.setContentsMargins(40, 40, 40, 40)
        fc.setSpacing(16)
        
        fc.addWidget(IconLabel("file-text", role="accent", size=80), alignment=Qt.AlignCenter)
        
        self._file_name_lbl = label("", role="h2")
        self._file_name_lbl.setAlignment(Qt.AlignCenter)
        fc.addWidget(self._file_name_lbl)
        
        self._file_meta_lbl = sub_label("아래 버튼을 눌러 교정 분석을 시작하세요.")
        self._file_meta_lbl.setAlignment(Qt.AlignCenter)
        fc.addWidget(self._file_meta_lbl)
        
        change_btn = make_button("삭제", "ghost", on_click=lambda _: self.file_selected.emit(""))
        change_btn.setFixedWidth(100)
        change_btn.setFixedHeight(40)
        change_btn.setStyleSheet("padding: 6px 12px; margin-top: 20px;")
        fc.addWidget(change_btn, alignment=Qt.AlignCenter)
        
        self._file_card.setVisible(False)
        lay.addWidget(self._file_card, 1)

        root.addWidget(frame)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "HWP 파일 선택", "", "한글 문서 (*.hwp *.hwpx)")
        if path:
            self.file_selected.emit(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                ext = urls[0].toLocalFile().lower()
                if ext.endswith(".hwp") or ext.endswith(".hwpx"):
                    self._dropzone.setProperty("active", "true")
                    restyle(self._dropzone)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._dropzone.setProperty("active", "false")
        restyle(self._dropzone)

    def dropEvent(self, event):
        self._dropzone.setProperty("active", "false")
        restyle(self._dropzone)
        path = event.mimeData().urls()[0].toLocalFile()
        self.file_selected.emit(path)

    def set_file(self, file_path: str):
        self._file_path = file_path
        if not file_path:
            self._file_name_lbl.setText("")
            self._file_meta_lbl.setText("아래 버튼을 눌러 교정 분석을 시작하세요.")
            self._file_card.setVisible(False)
            self._dropzone.setVisible(True)
            return

        name = os.path.basename(file_path)
        try:
            size_mb = os.path.getsize(file_path) / 1_048_576
            meta = f"{size_mb:.1f} MB"
        except OSError:
            meta = ""
        self._file_name_lbl.setText(name)
        self._file_meta_lbl.setText("아래 버튼을 눌러 교정 분석을 시작하세요.")
        self._dropzone.setVisible(False)
        self._file_card.setVisible(True)

    def has_file(self) -> bool:
        return bool(self._file_path)


class SetupPanel(QWidget):
    options_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 옵션 상태
        self._auto_apply   = False
        # 오탈자·띄어쓰기는 분리 교정하는 경우가 없어 하나의 범위로 통합한다.
        self._scope_basic  = True
        self._scope_polish = False
        self._gen_errata   = True
        # AI 분석 제외 — Gemini 호출 없이 사전·규칙 파이프라인만 수행(오프라인 가능).
        self._no_ai        = False
        # 사전 원문 스크리닝은 이제 항상 켜지는 기본 동작이다(옵트인 토글 폐지).
        self._build_ui()

    # ══════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        from ui.widgets.components import card
        main_card = card("section")
        main_lay = QVBoxLayout(main_card)
        main_lay.setContentsMargins(27, 21, 27, 21)
        main_lay.setSpacing(40)

        main_lay.addLayout(self._build_apply_mode_section(), 3)
        main_lay.addLayout(self._build_scope_section(), 2)
        main_lay.addLayout(self._build_extra_section(), 1)
        
        col.addWidget(main_card)

    # ── 교정 방식 ───────────────────────────────
    def _build_apply_mode_section(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(IconLabel("list-checks", role="text_sub", size=16))
        hdr.addWidget(title_label("교정 방식"))
        hdr.addStretch()
        lay.addLayout(hdr)

        row = QVBoxLayout()
        row.setSpacing(12)

        self._card_review = self._make_choice_card(
            "list-checks", "항목별 검토", "권장",
            "교정 제안을 하나씩 확인하고\n사용자가 직접 수락-거절을 선택합니다.", True)
        self._card_auto = self._make_choice_card(
            "zap", "자동 일괄 적용",
            "빠름" if AUTO_APPLY_ENABLED else "추후 예정",
            "모든 교정 제안을 사용자의 검토 없이\n즉시 원본 파일에 적용합니다."
            if AUTO_APPLY_ENABLED else
            "교정 품질이 검토 없이 적용해도 될 수준에 이르면 오픈할 예정입니다",
            False, locked=not AUTO_APPLY_ENABLED)

        self._card_review.mousePressEvent = lambda _e: self._select_apply_mode(False)
        if AUTO_APPLY_ENABLED:
            self._card_auto.mousePressEvent = lambda _e: self._select_apply_mode(True)

        row.addWidget(self._card_review, 1)
        row.addWidget(self._card_auto, 1)
        lay.addLayout(row, 1)
        return lay

    def _make_choice_card(self, icon, title, badge_text, desc, selected,
                          locked: bool = False) -> QFrame:
        card = QFrame()
        card.setProperty("role", "choice")
        card.setProperty("selected", "true" if selected else "false")
        # locked=선택 불가(추후 예정). QSS가 점선 테두리·흐린 글자로 그리고,
        #   손 모양 커서를 주지 않아 '누를 수 있는 것'으로 보이지 않게 한다.
        card.setProperty("locked", "true" if locked else "false")
        card.setCursor(Qt.ArrowCursor if locked else Qt.PointingHandCursor)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 16, 22, 16)
        cl.setSpacing(6)

        # 아이콘 + 제목 + 배지를 한 줄에(세로 높이 축소), 선택 체크는 같은 줄 오른쪽 끝.
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(IconLabel(icon, role="text_muted" if locked else "accent", size=20))
        trow.addWidget(label(title, role="h2"))
        tone = "primary" if badge_text == "권장" else ""
        trow.addWidget(badge(badge_text, tone=tone))
        trow.addStretch()
        check = IconLabel("circle-check", role="accent", size=18)
        eff = QGraphicsOpacityEffect(check)
        eff.setOpacity(1.0 if selected else 0.0)
        check.setGraphicsEffect(eff)
        trow.addWidget(check)
        cl.addLayout(trow)

        d = sub_label(desc, wrap=True)
        cl.addWidget(d)

        card._check = check
        card._check_eff = eff
        return card

    def _select_apply_mode(self, auto: bool):
        # ⚠ 잠금 시 전자동으로 갈 수 있는 경로를 여기서 끊는다 — 카드 클릭을 연결하지
        #   않는 것만으로는 부족하고(다른 호출처가 생길 수 있다), 상태 변경 지점이
        #   하나뿐이라 여기가 유일한 관문이다.
        if auto and not AUTO_APPLY_ENABLED:
            return
        if self._auto_apply == auto:
            return
        self._auto_apply = auto
        self._card_review.setProperty("selected", "false" if auto else "true")
        self._card_auto.setProperty("selected", "true" if auto else "false")
        
        restyle(self._card_review)
        restyle(self._card_auto)
        
        self._anim_group = QParallelAnimationGroup(self)
        
        anim1 = QPropertyAnimation(self._card_review._check_eff, b"opacity")
        anim1.setDuration(250)
        anim1.setStartValue(self._card_review._check_eff.opacity())
        anim1.setEndValue(0.0 if auto else 1.0)
        anim1.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim_group.addAnimation(anim1)
        
        anim2 = QPropertyAnimation(self._card_auto._check_eff, b"opacity")
        anim2.setDuration(250)
        anim2.setStartValue(self._card_auto._check_eff.opacity())
        anim2.setEndValue(1.0 if auto else 0.0)
        anim2.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim_group.addAnimation(anim2)
        
        self._anim_group.start()
        
        self.options_changed.emit()

    # ── 교정 범위 ───────────────────────────────
    def _build_scope_section(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(IconLabel("spell-check", role="text_sub", size=16))
        hdr.addWidget(title_label("교정 범위"))
        hdr.addStretch()
        lay.addLayout(hdr)

        self._tog_basic = self._add_toggle_row(
            lay, True, "오탈자 · 띄어쓰기",
            "국립국어원 표준국어대사전 + 우리말샘 사전 + AI 교정",
            lambda v: self._set_scope("_scope_basic", v))
        self._tog_polish = self._add_toggle_row(
            lay, False, "윤문",
            "문장 흐름 · 어미 · 중복 표현 개선" if POLISH_ENABLED else
            "오탈자·띄어쓰기 교정이 안정화된 후 오픈할 예정입니다",
            lambda v: self._set_scope("_scope_polish", v),
            locked=not POLISH_ENABLED, badge_text=None if POLISH_ENABLED else "추후 예정")
        return lay

    def _set_scope(self, attr: str, v: bool):
        setattr(self, attr, v)
        self.options_changed.emit()

    # ── 부가 기능 ───────────────────────────────
    def _build_extra_section(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(IconLabel("sliders-horizontal", role="text_sub", size=16))
        hdr.addWidget(title_label("부가 기능"))
        hdr.addStretch()
        lay.addLayout(hdr)

        self._tog_errata = self._add_toggle_row(
            lay, True, "정오표 자동 생성 (.xlsx)",
            "교정 전-후, 교정 이유, 적용 결과를 Excel로 출력",
            lambda v: setattr(self, "_gen_errata", v))
        self._tog_no_ai = self._add_toggle_row(
            lay, False, "AI 분석 제외 (대외비 문서용)",
            "Gemini 호출 없이 사전·규칙 검사만 수행 — 오프라인 가능",
            lambda v: self._set_scope("_no_ai", v))
        return lay

    def _add_toggle_row(self, lay, on, title, desc, on_change,
                        locked: bool = False, badge_text: str = None) -> ToggleSwitch:
        row = QFrame()
        row.setProperty("role", "toggleRow")
        row.setProperty("locked", "true" if locked else "false")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(22, 14, 22, 14)
        rl.setSpacing(10)
        rl.setAlignment(Qt.AlignVCenter)

        toggle = ToggleSwitch(on=False if locked else on)
        toggle.toggled.connect(on_change)
        # ⚠ setEnabled(False)면 ToggleSwitch가 클릭을 무시하고(mousePressEvent의
        #   isEnabled 검사) 트랙도 흐린 색으로 그린다 — 별도 잠금 처리 불필요.
        if locked:
            toggle.setEnabled(False)
            toggle.setCursor(Qt.ArrowCursor)
        rl.addWidget(toggle)

        col = QVBoxLayout()
        col.setSpacing(2)
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(label(title, role="title"))
        if badge_text:
            trow.addWidget(badge(badge_text))
        trow.addStretch()
        col.addLayout(trow)
        col.addWidget(sub_label(desc, wrap=True))
        rl.addLayout(col, 1)

        lay.addWidget(row, 1)
        return toggle

    # ══════════════════════════════════════════════
    # 공개 API
    # ══════════════════════════════════════════════
    # (파일 선택/드래그앤드롭은 FilePanel로 이관 완료 — 이곳의 잔재 핸들러들은
    #  존재하지 않는 self._dropzone 등을 참조하는 죽은 코드였으므로 삭제됨.)

    def _polish_on(self) -> bool:
        """윤문의 **유효** 상태 — 잠금이면 내부 필드와 무관하게 꺼진 것으로 본다.
        get_options와 같은 기준을 쓰지 않으면 '시작은 되는데 아무것도 안 하는' 조합이 생긴다."""
        return self._scope_polish and POLISH_ENABLED

    def scopes_selected(self) -> bool:
        return any([self._scope_basic, self._polish_on()])

    def summary_text(self) -> str:
        scopes = []
        if self._scope_basic:  scopes.append("오탈자 · 띄어쓰기")
        if self._polish_on():  scopes.append("윤문")
        mode_text = "자동 일괄 적용" if (self._auto_apply and AUTO_APPLY_ENABLED) else "항목별 검토"
        no_ai = " / AI 제외" if self._no_ai else ""
        return f"{' / '.join(scopes)} / {mode_text}{no_ai}"

    def get_options(self) -> dict:
        return {
            # 'AI 분석 제외'가 켜지면 Gemini 호출만 빠진다 — 사전 스크리닝·결정론
            #   패스·검수 카드·적용·정오표는 그대로(워커 [4]~[7]은 항상 수행).
            "use_ai":         self.scopes_selected() and not self._no_ai,
            # 오탈자·띄어쓰기는 단일 범위로 통합 — 두 플래그를 함께 전달한다.
            "scope_typo":     self._scope_basic,
            "scope_spacing":  self._scope_basic,
            # ⚠ 잠금 플래그를 여기서 한 번 더 곱한다(이중 안전장치). UI 상태가 어떤 경로로
            #   틀어져도 워커·적용 단계로 True가 새어 나가지 않게 하는 마지막 관문이다.
            "scope_polish":   self._scope_polish and POLISH_ENABLED,
            "gen_errata":     self._gen_errata,
            "deep_screening": True,   # 사전 원문 스크리닝은 항상 수행됨
            "auto_apply":     self._auto_apply and AUTO_APPLY_ENABLED,
        }

    def refresh_theme(self):
        for tg in (self._tog_basic, self._tog_polish, self._tog_errata, self._tog_no_ai):
            tg.refresh_theme()
