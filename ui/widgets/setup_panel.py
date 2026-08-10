"""
ui/widgets/setup_panel.py — 설정 패널 (업로드 + 옵션 통합)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
상단 드롭존/선택파일 카드 + 하단 교정 옵션(방식·범위·부가기능).
기존 upload_widget + options_widget을 한 화면으로 합쳐 화면 전환을 제거한다.
시작 버튼은 워크스페이스 하단 footer가 담당한다.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QFileDialog, QSizePolicy
)

from ui.widgets.components import (
    label, sub_label, badge, section_card, make_button, IconLabel, title_label,
    soft_breakable
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


def _wrap_card_policy() -> QSizePolicy:
    """줄바꿈(wrap) 라벨을 품은 카드용 size policy.

    ⚠ Qt 함정 **두 개**가 겹쳐 있고, 하나만 고치면 증상이 그대로다(실측).
      ① 컨테이너 위젯의 sizePolicy는 `hasHeightForWidth()`가 **기본 False**라,
         wordWrap 라벨이 안에 있어도 부모 레이아웃이 heightForWidth를 묻지 않는다.
      ② 세로 정책이 `Maximum`이면 위젯의 **최대 높이가 sizeHint 높이로 고정**된다.
         sizeHint는 폭을 모르는 값(한 줄 기준)이라, 부모가 접힌 높이(104px)를
         내줘도 카드는 90px에서 잘린다 — 실측으로 확인한 진짜 원인이 이쪽이다.
         (증상: 교정 범위 카드의 '2차 : AI (Gemini) 분석' 줄이 통째로 사라지고,
          부가 기능 제목이 설명을 덮었다. 폭이 넉넉하면 안 보여 넓은 창에서만
          확인하면 놓친다.)
      → 그래서 세로는 `Preferred`. 원래 `Maximum`을 쓴 의도(남는 세로 공간이
        카드 안으로 배분돼 제목과 설명 사이가 벌어지는 것 방지)는 카드 레이아웃
        **맨 끝의 stretch**가 대신 지킨다.
    """
    sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    sp.setHeightForWidth(True)
    return sp


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
        # ⚠ 세로 가운데 정렬은 **stretch로** 한다. `fc.setAlignment(Qt.AlignCenter)`를
        #   쓰면 각 줄이 제 sizeHint 폭으로 **줄어든 채** 가운데 놓인다 — 실측: 528px
        #   카드 안에서 파일명 라벨이 188px밖에 못 받아, 여백을 아무리 넓혀도 파일명이
        #   좁게 접혔다(줄바꿈 라벨의 sizeHint는 '보기 좋은 비율' 휴리스틱 값이라 더 작다).
        #   stretch로 바꾸면 각 줄이 카드 폭을 전부 쓰고, 글자는 라벨 자체의
        #   `setAlignment(Qt.AlignCenter)`가 가운데로 맞춘다.
        # 좌우 여백은 드롭존(40)보다 좁게 — 긴 파일명이 접힐 폭을 벌어 준다
        #   (사용자 지정 2026-08-06: "좌우로 더 여유있게 나가도 돼").
        fc.setContentsMargins(20, 40, 20, 40)
        fc.setSpacing(16)

        fc.addStretch(1)
        fc.addWidget(IconLabel("file-text", role="accent", size=80), alignment=Qt.AlignCenter)
        fc.addSpacing(12)   # 아이콘 ↔ 파일명 간격(기본 16 + 12 = 28)

        # 파일명은 **줄이지 않고 전부** 보여 준다(사용자 지정 2026-08-06) — 대신 줄바꿈.
        #   ⚠ 세 가지가 모두 있어야 실제로 접힌다. 하나라도 빠지면 증상이 다르게 나온다.
        #     ① 가로 정책 `Ignored`: 줄바꿈 라벨이라도 긴 파일명은 칸의 최소폭을 밀어
        #        올려 좌우 1:1 그리드를 깨뜨린다(실사고). 0으로 취급시켜 못 밀게 한다.
        #     ② `setHeightForWidth(True)`: 커스텀 정책을 주는 순간 heightForWidth 플래그가
        #        꺼져, 접힌 만큼 높이가 늘지 않고 아랫줄이 잘린다.
        #     ③ `soft_breakable`(ZWSP): 파일명엔 공백이 거의 없어 Qt의 줄바꿈 단위가
        #        문자열 전체가 된다 — 줄바꿈을 켜도 접히지 않는다.
        self._file_name_lbl = label("", role="h2", wrap=True)
        self._file_name_lbl.setAlignment(Qt.AlignCenter)
        self._file_name_lbl.setTextFormat(Qt.PlainText)
        _name_sp = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        _name_sp.setHeightForWidth(True)
        self._file_name_lbl.setSizePolicy(_name_sp)
        fc.addWidget(self._file_name_lbl)
        
        self._file_meta_lbl = sub_label("아래 버튼을 눌러 교정 분석을 시작하세요.")
        self._file_meta_lbl.setAlignment(Qt.AlignCenter)
        fc.addWidget(self._file_meta_lbl)
        
        change_btn = make_button("삭제", "ghost", on_click=lambda _: self.file_selected.emit(""))
        change_btn.setFixedWidth(100)
        change_btn.setFixedHeight(40)
        change_btn.setStyleSheet("padding: 6px 12px; margin-top: 20px;")
        fc.addWidget(change_btn, alignment=Qt.AlignCenter)
        fc.addStretch(1)

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
            self._file_name_lbl.setToolTip("")
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
        self._file_name_lbl.setText(soft_breakable(name))
        self._file_name_lbl.setToolTip(name)   # 복사용 원본(ZWSP 없는 이름)
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
        # ★결과물 축 — '한글 파일을 어떻게 돌려줄 것인가'(2026-08-05 도입 → 08-07 4값 확장).
        #     "hwp"    = 교정본: 본문 치환 + 빨강 표시 + `_교정본` 파일
        #     "errata" = 정오표만: 한글 파일 무수정(진짜 '검수 모드')
        #     "memo"   = 메모본: 글자는 그대로 두고 각 자리에 한/글 메모 + 앵커 강조
        #     "pdf"    = PDF 주석본: 원본을 PDF로 뽑아 그 위에만 형광+스티커 주석
        #   ⚠ 이건 '교정 방식'(항목별 검토/자동 일괄)의 값이 **아니라 직교 축**이다.
        #     어느 값이든 사용자는 검토 단계에서 수락/거절을 해야 한다 — 그래야 무엇을
        #     반영·기록·주석할지 정해진다. 두 축을 한 묶음으로 만들면 검토 단계의
        #     의미가 무너진다.
        #   ⚠ 과거엔 'errata'가 **우연히** 도달하는 상태였다(수락한 치환이 0건일 때
        #     apply_worker가 분기). 사용자가 의도할 수도 재현할 수도 없는 산출물이라
        #     옵션으로 끌어올렸다. docs/proofreading-architecture.md Phase 2b의
        #     'AI scope 0개 → 검수 모드' 진입 경로는 현행 UI에서 도달 불가능한 잔재다.
        #   ⚠ `errata_only`(bool)는 **파생값으로만** 남긴다 — 값이 넷이 된 뒤로는
        #     "한글 파일을 안 고친다"만 뜻하며, 어느 산출물인지는 구분하지 못한다.
        #     새 분기를 bool로 만들지 말고 `output_mode`를 볼 것.
        self._output_mode  = "hwp"
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
        main_lay.setSpacing(30)

        # 네 섹션 모두 **가로 2단**이라 세로로는 내용만큼만 차지하면 된다.
        #   ⚠ 여기에 stretch를 주면 남는 세로 공간이 카드 안으로 배분돼 제목과 설명
        #     사이가 휑하게 벌어진다(실측). 남는 공간은 **맨 아래 stretch**가 먹는다.
        for build in (self._build_apply_mode_section, self._build_scope_section,
                      self._build_output_section, self._build_extra_section):
            main_lay.addLayout(build(), 0)
        main_lay.addStretch(1)
        
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

        row = self._pair_row()

        self._card_review = self._make_choice_card(
            "list-checks", "항목별 검토", None,
            "교정 제안을 하나씩 확인하고\n사용자가 직접 수락 · 거절을 선택합니다.", True)
        self._card_auto = self._make_choice_card(
            "zap", "자동 일괄 적용",
            None if AUTO_APPLY_ENABLED else "추후 예정",
            "사용자의 검토 없이 모든 교정 제안을 즉시 적용합니다."
            if AUTO_APPLY_ENABLED else
            "교정 품질의 최적화 이후\n오픈할 예정입니다.",
            False, locked=not AUTO_APPLY_ENABLED)

        self._card_review.mousePressEvent = lambda _e: self._select_apply_mode(False)
        if AUTO_APPLY_ENABLED:
            self._card_auto.mousePressEvent = lambda _e: self._select_apply_mode(True)

        row.addWidget(self._card_review, 1)
        row.addWidget(self._card_auto, 1)
        lay.addLayout(row, 1)
        return lay

    def _pair_row(self) -> QHBoxLayout:
        """섹션 하나의 '가로 2단' 컨테이너 — 네 섹션이 같은 리듬을 갖게 한다.
        ⚠ 두 칸에 **같은 stretch(1)** 를 줘야 폭이 반반이 된다. 카드 안 글자 길이가
          달라도 칸 너비가 흔들리지 않게 하는 게 목적."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        return row

    def _make_choice_card(self, icon, title, badge_text, desc, selected,
                          locked: bool = False) -> QFrame:
        """선택 카드 — 세 섹션(교정 방식·교정 범위·결과물)이 모두 **둘 중 하나**다.

        ⚠ 선택 표시는 **테두리·배경만**으로 한다(사용자 지정 2026-08-06 — 체크 아이콘
          전면 삭제). 그래서 `_set_card_selected`는 QSS 속성만 갱신하면 되고, 카드에
          붙어 있던 체크 위젯·불투명도 애니메이션은 없다.
        ⚠ `badge_text`는 **잠금 표시('추후 예정') 전용**이다. 권장/기본/검수 같은
          강조 칩은 제거했다 — 칩 없이도 선택 상태는 테두리로 읽힌다.
        """
        card = QFrame()
        card.setProperty("role", "choice")
        card.setProperty("selected", "true" if selected else "false")
        # locked=선택 불가(추후 예정). QSS가 점선 테두리·흐린 글자로 그리고,
        #   손 모양 커서를 주지 않아 '누를 수 있는 것'으로 보이지 않게 한다.
        card.setProperty("locked", "true" if locked else "false")
        card.setCursor(Qt.ArrowCursor if locked else Qt.PointingHandCursor)
        # 세로로는 내용 높이만 — 늘어나면 제목과 설명 사이가 벌어진다(_wrap_card_policy
        #   독스트링 참조: 그 역할은 이제 아래 맨 끝 stretch가 맡는다).
        card.setSizePolicy(_wrap_card_policy())

        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 16, 22, 16)
        cl.setSpacing(6)

        # 아이콘 + 제목 (+ 잠금 칩) 한 줄.
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(IconLabel(icon, role="text_muted" if locked else "accent", size=20))
        trow.addWidget(label(title, role="h2"))
        if badge_text:
            trow.addWidget(badge(badge_text))
        trow.addStretch()
        cl.addLayout(trow)

        d = sub_label(desc, wrap=True)
        cl.addWidget(d)
        # ⚠ 남는 세로 공간은 **여기서** 먹는다. 한 행의 두 카드는 높이가 같아지는데
        #   (긴 쪽에 맞춰짐), 이 stretch가 없으면 짧은 카드의 제목과 설명 사이가
        #   벌어진다 — 예전 `Maximum` 정책이 막고 있던 그 증상이다.
        cl.addStretch(1)
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
        # 선택 표시 갱신은 세 섹션이 **한 함수**를 쓴다 — 예전엔 여기만 따로 구현돼
        #   있어서 카드 스타일을 바꿀 때 두 곳을 고쳐야 했다(_set_card_selected).
        self._set_card_selected(self._card_review, not auto)
        self._set_card_selected(self._card_auto, auto)
        self.options_changed.emit()

    # ── 결과물 ─────────────────────────────────
    #   카드 정의를 한곳에 모아 둔다 — 카드·선택·요약·옵션이 같은 목록을 보게 해서
    #   값을 하나 더 늘릴 때 고칠 자리가 하나로 유지되게 하는 것이 목적.
    _OUTPUT_CARDS = (
        # (mode, icon, 제목, 설명, 요약 문구)
        ("hwp",    "clipboard-check", "HWP (빨간색)",
         "한글 원고에 교정안을 반영하고\n해당 자리를 빨간색으로 표시합니다.",
         "HWP (빨간색)"),
        ("memo",   "file-text",       "HWP (메모)",
         "한글 원고에 교정안을 반영하지 않고\n해당 자리에 메모로 교정안을 표시합니다.",
         "HWP (메모)"),
        ("pdf",    "file-down",       "PDF (주석)",
         "한글 원고를 PDF로 변환한 뒤\n형광펜과 주석으로 교정안을 표시합니다.",
         "PDF (주석)"),
        ("errata", "table",           "Excel (정오표)",
         "한글 원고에 교정안을 반영하지 않고\n교정안과 해당 페이지만 기록합니다.",
         "Excel (정오표)"),
    )

    def _build_output_section(self) -> QVBoxLayout:
        """'한글 파일을 어떻게 돌려줄 것인가' 축 — 네 값 중 하나.

        ⚠ 교정 방식(항목별 검토/자동 일괄)과 **직교**한다 — 클래스 상단 `_output_mode`
          주석 참조. 그래서 그 섹션에 값을 더 붙이지 않고 섹션을 따로 둔다.
        ⚠ 다른 세 섹션은 한 줄 2칸인데 여기만 **2×2**다. `_pair_row()`를 두 번 써서
          두 줄을 만든다 — 한 줄에 4칸을 넣으면 카드 폭이 설명 두 줄을 못 버틴다
          (설정 칸 최소폭 함정: memory `setup-panel-equal-grid-qt-traps`).
        """
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        # ⚠ 번들에 없는 아이콘 이름을 쓰면 **예외도 로그도 없이 빈 픽스맵**이 된다
        #   (assets/icons/*.svg 에 실제로 있는 것만 쓸 것 — 'file-check'는 없다).
        hdr.addWidget(IconLabel("file-text", role="text_sub", size=16))
        hdr.addWidget(title_label("결과물"))
        hdr.addStretch()
        lay.addLayout(hdr)

        self._out_cards = {}
        row = None
        for i, (mode, icon, title, desc, _sum) in enumerate(self._OUTPUT_CARDS):
            if i % 2 == 0:
                row = self._pair_row()
                lay.addLayout(row, 1)
            card = self._make_choice_card(icon, title, None, desc,
                                          mode == self._output_mode)
            # ⚠ 람다 기본값으로 mode를 묶는다 — 늦은 바인딩이면 네 카드가 전부
            #   마지막 값을 고르게 된다.
            card.mousePressEvent = lambda _e, m=mode: self._select_output_mode(m)
            self._out_cards[mode] = card
            row.addWidget(card, 1)
        return lay

    def _select_output_mode(self, mode: str):
        if mode not in self._out_cards or self._output_mode == mode:
            return
        self._output_mode = mode
        for m, card in self._out_cards.items():
            self._set_card_selected(card, m == mode)
        self._sync_errata_lock()
        self.options_changed.emit()

    def _sync_errata_lock(self):
        """한글 파일을 고치지 않는 모드에서는 정오표 생성 토글을 강제 ON + 잠금.

        ⚠ 이 잠금이 없으면 **산출물이 반쪽인 조합**이 만들어진다. 'errata'는 아예
          산출물이 0개가 되고, 'memo'·'pdf'는 파일은 나오지만 **메모·주석을 달지 못한
          자리**(머리말·꼬리말처럼 한/글이 메모를 거부하는 스토리, PDF에서 못 찾은 원문)를
          확인할 곳이 사라진다. 윤문·자동 일괄 적용과 같은 잠금 관용구다 — 토글을 끄는
          경로를 UI에서 막고, `get_options`에서 한 번 더 곱해 새어 나가지 못하게 한다.
        """
        lock = self._output_mode != "hwp"
        tg = self._tog_errata
        if lock:
            tg.set_on(True, emit=False)
            self._gen_errata = True
        tg.setEnabled(not lock)
        tg.setCursor(Qt.ArrowCursor if lock else Qt.PointingHandCursor)
        row = getattr(tg, "_row", None)
        if row is not None:
            row.setProperty("locked", "true" if lock else "false")
            restyle(row)
        desc = getattr(tg, "_desc", None)
        if desc is not None:
            desc.setText("한글 미반영 결과물 선택\n- 항상 생성됩니다." if lock else
                         "교정내용을 Excel로 출력\n- 불필요시 끄세요.")

    # ── 교정 범위 ───────────────────────────────
    def _build_scope_section(self) -> QVBoxLayout:
        """교정 방식과 **같은 카드 · 같은 동작**(둘 중 하나) — 사용자 지정 2026-08-06.

        ★근거: 윤문에는 **오탈자·띄어쓰기가 이미 포함**된다. 그래서 둘은 더할 수 있는
          별개 범위가 아니라 '어디까지 볼 것인가'의 두 단계이고, 다중 선택으로 두면
          '윤문만 켜고 오탈자는 끈' 조합처럼 존재하지 않는 상태가 UI에 생긴다.
        ⚠ 그래서 윤문을 고르면 내부적으로 `_scope_basic`도 True를 유지한다 —
          `gemini_checker.check_scope`는 typo 패스와 polish 패스를 **더해서** 돌리므로,
          이렇게 해야 '윤문 = 오탈자·띄어쓰기 + 윤문'이 실제 동작과 일치한다.
        """
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(IconLabel("spell-check", role="text_sub", size=16))
        hdr.addWidget(title_label("교정 범위"))
        hdr.addStretch()
        lay.addLayout(hdr)

        row = self._pair_row()
        self._card_basic = self._make_choice_card(
            "spell-check", "오탈자 · 띄어쓰기", None,
            "1차 : 표준국어대사전 + 우리말샘 + 온용어\n2차 : AI (Gemini) 분석",
            not self._polish_on())
        self._card_polish = self._make_choice_card(
            "wand-sparkles", "윤문",
            None if POLISH_ENABLED else "추후 예정",
            "오탈자 · 띄어쓰기 + 문장 흐름 · 어미 · 중복 표현 개선" if POLISH_ENABLED else
            "교정 품질의 최적화 이후\n오픈할 예정입니다.",
            self._polish_on(), locked=not POLISH_ENABLED)

        self._card_basic.mousePressEvent = lambda _e: self._select_scope(False)
        if POLISH_ENABLED:
            self._card_polish.mousePressEvent = lambda _e: self._select_scope(True)

        row.addWidget(self._card_basic, 1)
        row.addWidget(self._card_polish, 1)
        lay.addLayout(row, 1)
        return lay

    def _select_scope(self, polish: bool):
        """교정 범위 선택 — 둘 중 하나(교정 방식·결과물과 같은 동작).

        ⚠ 잠금 시 윤문으로 갈 수 있는 경로를 여기서 끊는다 — 카드 클릭을 연결하지
          않는 것만으로는 부족하다(_select_apply_mode와 같은 '관문 하나' 원칙).
        """
        if polish and not POLISH_ENABLED:
            return
        if self._scope_polish == polish:
            return
        self._scope_polish = polish
        # ⚠ 윤문에도 오탈자·띄어쓰기가 포함된다 → basic은 끄지 않는다(위 독스트링).
        self._scope_basic = True
        self._set_card_selected(self._card_basic, not polish)
        self._set_card_selected(self._card_polish, polish)
        self.options_changed.emit()

    def _set_card_selected(self, card: QFrame, on: bool):
        """카드의 선택 표시 — 테두리·배경(QSS `selected` 속성)이 전부다."""
        card.setProperty("selected", "true" if on else "false")
        restyle(card)

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

        row = self._pair_row()
        self._tog_errata = self._add_toggle_row(
            row, True, "정오표 자동 생성 (.xlsx)",
            "교정내용을 Excel로 출력\n- 불필요시 끄세요.",
            lambda v: setattr(self, "_gen_errata", v))
        self._tog_no_ai = self._add_toggle_row(
            row, False, "AI 분석 제외 (대외비 문서용)",
            "Gemini 미호출로 보안강화\n- 오프라인 사용 가능",
            lambda v: self._set_flag("_no_ai", v))
        lay.addLayout(row, 1)
        return lay

    def _set_flag(self, attr: str, v: bool):
        setattr(self, attr, v)
        self.options_changed.emit()

    def _add_toggle_row(self, lay, on, title, desc, on_change,
                        locked: bool = False, badge_text: str = None) -> ToggleSwitch:
        row = QFrame()
        row.setProperty("role", "toggleRow")
        row.setProperty("locked", "true" if locked else "false")
        row.setSizePolicy(_wrap_card_policy())
        rl = QHBoxLayout(row)
        rl.setContentsMargins(22, 14, 22, 14)
        rl.setSpacing(10)
        # ⚠ 세로 가운데 정렬은 **토글에만** 준다(아래 addWidget). 레이아웃 전체에
        #   AlignVCenter를 걸면 글자 칸(col)까지 sizeHint 높이로 고정돼, 접힌 제목·설명이
        #   행 높이가 늘어나도 그대로 잘린다 — 카드 쪽 `Maximum` 함정과 같은 원리다.

        toggle = ToggleSwitch(on=False if locked else on)
        toggle.toggled.connect(on_change)
        # ⚠ setEnabled(False)면 ToggleSwitch가 클릭을 무시하고(mousePressEvent의
        #   isEnabled 검사) 트랙도 흐린 색으로 그린다 — 별도 잠금 처리 불필요.
        if locked:
            toggle.setEnabled(False)
            toggle.setCursor(Qt.ArrowCursor)
        rl.addWidget(toggle, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        trow = QHBoxLayout()
        trow.setSpacing(8)
        # ⚠ 제목은 **항상 한 줄**이다(사용자 지정 2026-08-06 — 두 줄로 접히면 안 됨).
        #   대가는 알고 쓰는 것: 이 제목들이 설정 칸 **전체의 최소폭**을 붙잡는다
        #   (실측: 'AI 분석 제외 (대외비 문서용)' 144px + '정오표 자동 생성 (.xlsx)'
        #   124px → body 최소폭 ~525px). 좌우 1:1 고정이라 창이 좁아 칸이 그보다
        #   작아지면 설정 칸에 가로 스크롤바가 생긴다(창 폭 ~1290 미만).
        #   줄바꿈으로 그걸 피하려던 시도는 사용자가 기각했다 — 되돌리지 말 것.
        trow.addWidget(label(title, role="title"))
        if badge_text:
            trow.addWidget(badge(badge_text))
        trow.addStretch()
        col.addLayout(trow)
        desc_lbl = sub_label(desc, wrap=True)
        col.addWidget(desc_lbl)
        rl.addLayout(col, 1)

        lay.addWidget(row, 1)
        # 잠금 상태를 나중에 바꾸려면 행 프레임(테두리·흐림)과 설명 라벨이 필요하다
        #   (_sync_errata_lock). 토글만 들고 있으면 스위치만 꺼지고 행은 멀쩡해 보인다.
        toggle._row = row
        toggle._desc = desc_lbl
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
        # 교정 범위는 둘 중 하나 — 고른 쪽 하나만 적는다. 윤문이 오탈자·띄어쓰기를
        #   포함하므로 '오탈자 · 띄어쓰기 / 윤문'처럼 둘 다 나열하면 중복이다.
        scope_text = "윤문" if self._polish_on() else "오탈자 · 띄어쓰기"
        mode_text = "자동 일괄 적용" if (self._auto_apply and AUTO_APPLY_ENABLED) else "항목별 검토"
        out_text = next((s for m, _i, _t, _d, s in self._OUTPUT_CARDS
                         if m == self._output_mode), "교정본 + 정오표")
        no_ai = " / AI 제외" if self._no_ai else ""
        return f"{scope_text} / {mode_text} / {out_text}{no_ai}"

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
            # ⚠ 한글 미반영 모드면 강제 True — 이게 없으면 산출물이 반쪽인 조합이
            #   생긴다(UI 잠금과 이중 안전장치. _sync_errata_lock 주석 참조).
            "gen_errata":     self._gen_errata or self._output_mode != "hwp",
            "deep_screening": True,   # 사전 원문 스크리닝은 항상 수행됨
            "auto_apply":     self._auto_apply and AUTO_APPLY_ENABLED,
            # ★결과물 축 — 새 분기는 **이 값**을 볼 것(클래스 상단 주석 참조).
            "output_mode":    self._output_mode,
            # 파생값(하위 호환) — "한글 파일을 고치지 않는다"만 뜻한다. 어느 산출물인지
            #   구분하지 못하므로 새 분기에 쓰지 말 것.
            "errata_only":    self._output_mode != "hwp",
        }

    def refresh_theme(self):
        # 교정 범위는 토글에서 선택 카드로 바뀌었다 — 카드는 전역 QSS로 그려지므로
        #   여기서 손댈 게 없고, 직접 그리는 ToggleSwitch만 다시 그린다.
        for tg in (self._tog_errata, self._tog_no_ai):
            tg.refresh_theme()
