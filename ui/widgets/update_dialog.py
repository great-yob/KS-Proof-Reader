"""
ui/widgets/update_dialog.py — 업데이트 안내·다운로드 모달
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`LightConfirmDialog`와 **같은 라이트 고정 표면**(build_light_card/LIGHT_CARD_QSS)을
쓴다 — 확인 팝업·토스트와 한 시스템으로 읽히게 하고, 프레임리스라 OS 다크
타이틀바가 새지 않는다. 그래서 refresh_theme()은 두지 않는다(테마 무관).

한 다이얼로그가 3단계를 순서대로 보여준다(창을 갈아 끼우지 않는다 — 위치가 튄다):
  ① 안내   : 새 버전·크기·릴리스 노트 + [나중에] [지금 설치]
  ② 진행률 : 다운로드 바 + 받은/전체 MB + [취소]
  ③ 완료   : 앱=재시작하면 교체 / 데이터=재시작하면 적용

⚠ 설치(install_app/install_data)는 **여기서 하지 않는다.** 다이얼로그는
  '다운로드된 zip 경로'까지만 책임지고, 실제 설치와 앱 종료는 MainWindow가
  한다 — 교정 작업 중인지 판단할 수 있는 건 MainWindow뿐이다.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)

from ui.styles.theme import LIGHT
from ui.widgets.review_panel import (
    LIGHT_CARD_QSS, build_light_card, set_card_msg_width,
)
from ui.workers.update_worker import UpdateDownloadWorker


_BTN_QSS = f"""
    QPushButton#lcdNo {{ color:{LIGHT['text_sub']}; background:{LIGHT['surface']};
                         border:1px solid {LIGHT['border_strong']}; border-radius:7px;
                         padding:8px 18px; font-size:13px; font-weight:500; }}
    QPushButton#lcdNo:hover {{ background:{LIGHT['surface_alt']}; }}
    QPushButton#lcdYes {{ color:{LIGHT['accent_fg']}; background:{LIGHT['accent']};
                          border:1px solid {LIGHT['accent']}; border-radius:7px;
                          padding:8px 22px; font-size:13px; font-weight:700; }}
    QPushButton#lcdYes:hover {{ background:{LIGHT['accent_hover']};
                                border-color:{LIGHT['accent_hover']}; }}
    QPushButton#lcdYes:pressed {{ background:{LIGHT['accent_press']};
                                  border-color:{LIGHT['accent_press']}; }}
    QPushButton#lcdYes:disabled {{ background:{LIGHT['border']};
                                   border-color:{LIGHT['border']};
                                   color:{LIGHT['text_dim']}; }}
    QLabel#lcdNote {{ color:{LIGHT['text_muted']}; background:transparent;
                      border:none; font-size:12px; }}
    QProgressBar#lcdBar {{ background:{LIGHT['surface_alt']};
                           border:1px solid {LIGHT['border']}; border-radius:6px;
                           height:10px; text-align:center; color:transparent; }}
    QProgressBar#lcdBar::chunk {{ background:{LIGHT['accent']}; border-radius:5px; }}
"""

_CHANNEL_LABEL = {"app": "앱", "data": "사전 데이터"}

# 릴리스 노트는 마크다운 원문이라 그대로 붙이면 모달이 세로로 폭주한다.
#   ⚠ 맨 앞에서 자르면 안 된다 — 이 저장소의 릴리스 본문은 **설치 안내로 시작**하므로
#   "아래 …Setup.exe를 받아 실행하세요"만 요약돼 정작 무엇이 바뀌었는지가 안 보인다
#   (실측). 그래서 '변경 내역' 제목을 찾아 그 아래 항목만 뽑는다.
import re as _re

_NOTE_ITEMS = 3
_NOTE_CHARS = 200
_CHANGE_HEAD = _re.compile(r"(바뀐|변경|고친|수정|개선|새로운|이번 버전|무엇이|what'?s|change)", _re.I)
_STOP_HEAD   = _re.compile(r"(자산 안내|다운로드|설치 방법|asset|install)", _re.I)
_BOILERPLATE = ("받아 실행", "압축을 풀", "SmartScreen", "알 수 없는 게시자",
                "추가 정보", "관리자 권한", "자동 업데이트로도")


def _summarize_notes(notes: str) -> str:
    lines = (notes or "").splitlines()
    # ① 변경 내역 제목 위치 — 없으면 처음부터(구버전 노트 호환)
    start = 0
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#") and _CHANGE_HEAD.search(s):
            start = i + 1
            break

    # ② **첫 내용 블록만** 본다. 내용이 시작된 뒤 다음 제목을 만나면 거기서 끊는다.
    #    ⚠ 이게 없으면 불릿 우선 규칙이 섹션을 뛰어넘는다 — 주요 변경이 문단이고
    #    뒤쪽에 '그대로 유지되는 것' 같은 불릿 목록이 있으면 **그쪽**을 요약해 버렸다
    #    (v1.0.6 릴리스에서 실제 발생). 사람이 읽는 순서를 그대로 따른다.
    items, plain, started = [], [], False
    for raw in lines[start:]:
        s = raw.strip()
        if not s or s.startswith(("---", ">", "|", "```")):
            continue
        if s.startswith("#"):
            if started or _STOP_HEAD.search(s):
                break                      # 내용이 시작된 뒤의 제목 = 다음 섹션
            continue                       # 아직 내용 전이면 하위 제목은 건너뛴다
        if any(b in s for b in _BOILERPLATE):
            continue
        bullet = s.startswith(("-", "*", "•"))
        s = s.lstrip("-*•").strip().replace("**", "").replace("`", "")
        # '예) …' 같은 부연 예시는 빼고 항목 제목만 남긴다
        s = s.split(" — ")[0].split("예) ")[0].strip(" —·")
        if not s:
            continue
        started = True
        (items if bullet else plain).append(s)
        if len(items) >= _NOTE_ITEMS:
            break

    # 같은 블록 안에서는 불릿(변경 목록)이 문단(도입 설명)보다 정보 밀도가 높다.
    picked = (items or plain)[:_NOTE_ITEMS]
    text = " · ".join(picked)
    return (text[:_NOTE_CHARS] + "…") if len(text) > _NOTE_CHARS else text


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}MB"


class UpdateDialog(QDialog):
    """새 버전 안내 → 다운로드 → 설치 준비 완료까지의 한 창.

    다운로드가 끝나면 `ready(channel, zip_path)`를 내보내고 창은 열린 채
    '재시작' 확인 상태로 바뀐다. 사용자가 재시작을 누르면 `apply_requested`.
    """

    ready           = Signal(str, object)   # (channel, zip_path)
    apply_requested = Signal(str, object)   # (channel, zip_path)

    def __init__(self, parent, info: dict, channel: str = "app"):
        super().__init__(parent)
        self._info = info
        self._channel = channel
        self._worker = None
        self._zip = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle("업데이트")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)     # 그림자 여백
        outer.setSpacing(0)

        kind = _CHANNEL_LABEL.get(channel, channel)
        title = f"새 {kind} 버전 {info.get('version', '')}"
        msg = (f"현재 {info.get('current', '?')} · 내려받을 용량 "
               f"{_mb(int(info.get('size') or 0))}")
        box, _t, self._msg = build_light_card(title, msg)
        outer.addWidget(box)
        lay = box.layout()

        note = _summarize_notes(info.get("notes", ""))
        if note:
            lay.addSpacing(8)
            self._note = QLabel(note)
            self._note.setObjectName("lcdNote")
            self._note.setWordWrap(True)
            self._note.setTextFormat(Qt.PlainText)
            nf = self._note.font()
            nf.setPixelSize(12)
            self._note.setFont(nf)
            set_card_msg_width(self._note, note)
            lay.addWidget(self._note)
        else:
            self._note = None

        # ── 진행률(다운로드 중에만 보인다) ───────────
        lay.addSpacing(14)
        self._bar = QProgressBar()
        self._bar.setObjectName("lcdBar")
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 100)
        self._bar.setVisible(False)
        lay.addWidget(self._bar)

        # ── 버튼 행 ──────────────────────────────────
        lay.addSpacing(20)
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)
        btns.addStretch()
        self._no = QPushButton("나중에")
        self._no.setObjectName("lcdNo")
        self._no.setCursor(Qt.PointingHandCursor)
        self._no.setAutoDefault(False)
        self._no.clicked.connect(self._on_no)
        self._yes = QPushButton("지금 설치")
        self._yes.setObjectName("lcdYes")
        self._yes.setCursor(Qt.PointingHandCursor)
        self._yes.setDefault(True)
        self._yes.clicked.connect(self._on_yes)
        btns.addWidget(self._no)
        btns.addWidget(self._yes)
        lay.addLayout(btns)

        self.setStyleSheet(LIGHT_CARD_QSS + _BTN_QSS)
        self.adjustSize()

    # ── 상태 전이 ────────────────────────────────────
    def _set_msg(self, text: str):
        self._msg.setText(text)
        set_card_msg_width(self._msg, text)

    def _on_yes(self):
        if self._zip is not None:            # ③ 완료 상태 → 적용 요청
            self.apply_requested.emit(self._channel, self._zip)
            self.accept()
            return
        # ① → ② 다운로드 시작
        self._yes.setEnabled(False)
        self._no.setText("취소")
        self._bar.setValue(0)
        self._bar.setVisible(True)
        if self._note is not None:
            self._note.setVisible(False)
        self._set_msg("다운로드 중…")
        self.adjustSize()

        self._worker = UpdateDownloadWorker(self._info, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_downloaded)
        self._worker.start()

    def _on_no(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()      # 취소 — done(None)이 뒤따른다
            self._no.setEnabled(False)
            self._set_msg("취소하는 중…")
            return
        self.reject()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            self._bar.setValue(int(done * 100 / total))
            self._set_msg(f"다운로드 중… {_mb(done)} / {_mb(total)}")
        else:
            self._set_msg(f"다운로드 중… {_mb(done)}")

    def _on_downloaded(self, path):
        if path is None:
            # 취소했으면 조용히 닫고, 실패면 사유를 남긴 채 다시 시도할 수 있게 둔다.
            if self._no.isEnabled():
                self._bar.setVisible(False)
                self._set_msg("다운로드에 실패했습니다. 네트워크를 확인한 뒤 다시 시도하세요.")
                self._yes.setEnabled(True)
                self._yes.setText("다시 시도")
                self._no.setText("닫기")
                self.adjustSize()
            else:
                self.reject()
            return

        self._zip = path
        self.ready.emit(self._channel, path)
        self._bar.setValue(100)
        self._bar.setVisible(False)
        if self._channel == "data":
            self._set_msg("내려받았습니다. 앱을 다시 시작하면 새 사전이 적용됩니다.")
            self._yes.setText("지금 재시작")
        else:
            self._set_msg("내려받았습니다. 앱을 종료하면 교체 후 자동으로 다시 시작합니다.")
            self._yes.setText("지금 재시작")
        self._yes.setEnabled(True)
        self._no.setText("나중에")
        self._no.setEnabled(True)
        self.adjustSize()

    # ── 창 ───────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        host = self.parent().window() if self.parent() else None
        if host is not None:
            geo = host.frameGeometry()
            self.move(geo.center() - self.rect().center())

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(3000)
        super().reject()
