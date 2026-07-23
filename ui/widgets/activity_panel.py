"""
ui/widgets/activity_panel.py — 우측 영구 활동 로그 패널
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
전 단계(추출→AI→적용)의 로그를 한 곳에 누적 표시한다. 분석→적용 사이에
클리어하지 않으므로 성공/실패 흐름을 한눈에 추적 가능.

⚠ **파이프라인 원문 로그를 그대로 찍지 않는다.** 워커·엔진이 내보내는 메시지는
'왜 그렇게 판단했는지'까지 서술하는 긴 문장이고(로그 = 근거 기록), 거기에 COM
브리지 배관·청크별 진행·안내 문단이 섞여 300px 폭 패널에서는 글자 벽이 된다
(사용자 지적 2026-07-23). 그래서 **표시 계층에서만** 세 가지를 적용한다:
  ① 저정보 라인 숨김(_DROP·_EXAMPLE_RE)   — 배관/설명/개별 예시
  ② 핵심 키워드 요약(_RULES)              — 사유 괄호·부연 절 제거, 수치는 보존
  ③ 반복 진행 라인 합침(coalesce key)      — 'AI 분석 n/m 청크'는 한 행이 갱신
`self._entries`에는 **원문을 그대로** 쌓는다 — get_proofreading_log()(완료
대시보드)와 사후 디버깅이 원문에 의존하므로 요약은 화면에만 적용한다.
err 레벨은 숨김·요약·말끝 잘림에서 모두 제외한다(진단 정보 보존).

레이아웃은 QTextTable 2단 그리드(시각 | 내용)다. 과거엔 한 블록에 '시각 + 내용'을
&nbsp;로 이어 붙여서, 내용이 2줄 이상이면 둘째 줄이 시각 열 밑으로 흘러 정렬이
무너졌다. 표 셀은 자기 열 안에서만 줄바꿈되므로 시각 열을 침범할 수 없다.
"""

import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont, QFontMetrics, QTextBlockFormat, QTextCursor, QTextFrameFormat,
    QTextLength, QTextOption, QTextTableFormat,
)
from PySide6.QtWidgets import QFrame, QVBoxLayout, QTextEdit

from ui.styles.theme import current_palette


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


# ══════════════════════════════════════════════════════════════
# ▌표시 요약 — 원문 로그 → 한 줄 키워드
# ══════════════════════════════════════════════════════════════

# 화면에서 통째로 숨길 라인(부분 문자열). err 레벨은 예외적으로 표시한다.
_DROP = (
    # ── HWP COM 브리지 배관(사용자에게 의미 없음)
    "[Worker stderr]", "[Worker stdout]", "편집기:", "HWP 브리지",
    "Dispatch", "[보안]", "보안 모듈", "[변환]", "SaveAs", "python.exe", "재오픈",
    # ── 매 실행 동일한 안내 문단
    "대부분은 작가 의도",
    # ── 청크별 상세(‘AI 분석 n/m 청크’ 한 행에 흡수됨)
    "이 청크 의심 단어",
    # ── 내부 재시도 세부
    "재시도는 글로서리",
    # ── 파일명 중복(‘파일 ·’ 라인과 같은 정보)
    "적용 대상 파일",
)

# 개별 예시 라인 — "· '원문' → '교정'" / "· '토큰'". 부모 요약 라인에 건수가 있다.
_EXAMPLE_RE = re.compile(r"^·\s*['\"‘“]")

# 선두 마커 — 상태는 색(level)이 표현하므로 글자에서 뺀다.
_LEAD_CHARS = "→·※ℹ⚠✓✕★"

# (정규식, 치환, 합침키) — 위에서부터 첫 매칭 적용. 합침키가 같은 라인이 연달아
#   오면 새 행을 쌓지 않고 마지막 행을 갱신한다(청크 진행 등).
_RULES = tuple((re.compile(p), r, k) for p, r, k in (
    # ── 파일·단계 경계 ─────────────────────────────
    (r"^파일 선택: (.+)$",                       r"파일 · \1", ""),
    (r"^파일 선택 취소됨$",                       "파일 선택 취소", ""),
    (r"^교정 분석을 시작합니다$",                  "교정 분석 시작", ""),
    (r"^교정 적용을 시작합니다$",                  "교정 적용 시작", ""),
    (r"^취소 요청 —.*$",                         "취소 요청 · 작업 중단", ""),

    # ── 추출 ──────────────────────────────────────
    (r"^추출 완료 — (.+?)\s*글자$",               r"추출 완료 · \1자", ""),
    (r"^문서가 큽니다 \(([\d,]+)자\).*$",          r"대용량 문서 \1자 · 분석 지연 가능", ""),

    # ── 사전 스크리닝 ──────────────────────────────
    (r"^사전 미등재/비표준 어휘 ([\d,]+)개 발견 \(전체 유니크 ([\d,?]+)개 중\)$",
     r"사전 스크리닝 · 미등재 \1 / 전체 \2", ""),
    (r"^사전 미등재/비표준 어휘 ([\d,]+)개 발견.*$",
     r"사전 스크리닝 · 미등재 \1", ""),
    (r"^우리말샘 API: .*?실재어 ([\d,]+)건.*$",     r"우리말샘 API · 실재어 \1건 제외", ""),
    (r"^온용어 API: .*?전문용어 ([\d,]+)건.*$",     r"온용어 API · 전문용어 \1건 제외", ""),
    (r"^온용어 API 일일 한도.*$",                  "온용어 API 일일 한도 초과", ""),
    (r"^사전 등재어 오플래그 억제:.*$",             "사전 등재어 오플래그 억제", ""),

    # ── AI 호출 ───────────────────────────────────
    (r"^\[AI\] (.+?)\s*(?:통합\s*)?분석 시작$",     r"AI 분석 시작 · \1", ""),
    (r"^\[AI\] 분석 중…?\s*(\d+)/(\d+) 청크$",     r"AI 분석 \1/\2 청크", "ai_chunk"),
    (r"^\[AI\] AI 분석 제외 모드.*$",              "AI 분석 제외 · 사전·규칙만 수행", ""),
    (r"^\[AI\] 취소 신호 감지.*$",                 "AI 분석 중단(취소)", ""),
    (r"^\[AI\] 모델 자동 전환: (.+)$",             r"AI 모델 전환 · \1", ""),
    (r"^\[AI\] '(.+?)' 사용 불가.*$",              r"AI 모델 '\1' 사용 불가", ""),
    (r"^\[AI\] '(.+?)' 한도 소진.*$",              r"AI 모델 '\1' 한도 소진", ""),
    (r"^\[AI\] 모든 후보 모델의 한도.*$",           "AI 전 모델 한도 소진 · 재시도", ""),
    (r"^\[AI\] 일시 오류.*$",                     "AI 일시 오류 · 재시도", ""),
    (r"^\[AI\] 호출 타임아웃.*$",                  "AI 호출 타임아웃", ""),
    (r"^\[AI\] API 호출 오류.*$",                 "AI 호출 오류 · 재시도 소진", ""),
    (r"^\[AI\] 출력 상한 도달.*$",                 "AI 출력 상한 도달 · 재구성 호출", ""),
    (r"^\[AI\] (?:JSON .*?복구|절단 응답 부분 복구).*$", "AI 응답 부분 복구", ""),
    (r"^\[AI\] 올바른 JSON 응답 없음.*$",           "AI 응답 없음 · 청크 스킵", ""),
    (r"^\[AI\] JSON 파싱 오류.*$",                "AI JSON 파싱 오류", ""),
    (r"^AI 교정 제안: ([\d,]+)건$",               r"AI 제안 \1건", ""),
    (r"^오류: AI 분석 전체 실패 — (\d+)/(\d+) 청크.*$", r"AI 분석 전체 실패 · \1/\2 청크", ""),
    (r"^AI 청크 (\d+)/(\d+) 실패.*$",             r"AI 청크 \1/\2 실패", ""),

    # ── 과교정 가드(먼저: 아래 일반 '교정 N건 제외' 규칙보다 구체적) ──
    (r"^본문 대조: .*?([\d,]+)건 제외.*$",          r"본문 대조 · 불일치 \1건 제외", ""),
    (r"^문서 대조: .*?([\d,]+)건 제외.*$",          r"문서 대조 · 미검출 \1건 제외", ""),
    (r"^(.+?)\s*AI 교정 ([\d,]+)건 검수 카드로 강등.*$", r"AI 강등 · \1 \2건 검수", ""),
    (r"^(.+?)\s*(?:AI )?교정 ([\d,]+)건 제외.*$",   r"AI 필터 · \1 \2건 제외", ""),
    (r"^괄호 뒤 조사 받침 호응 보정 ([\d,]+)건.*$",   r"괄호 뒤 조사 보정 \1건", ""),

    # ── 결정론 보강·정합성 ─────────────────────────
    (r"^1차 확정 교정 항목: ([\d,]+)건$",           r"1차 확정 \1건", ""),
    (r"^일관성 보정: 변형 단어 ([\d,]+)건.*$",       r"일관성 보정 +\1건", ""),
    (r"^\[사내 용어\] 충돌 — '(.+?)':.*$",          r"사내 용어 충돌 · '\1' 국가 표준 우선", ""),
    (r"^사내 용어 충돌 ([\d,]+)건.*$",              r"사내 용어 충돌 \1건 · 국가 표준 우선", ""),
    (r"^빈출 미등재어 ([\d,]+)건 검수 카드 제외.*$",  r"빈출 미등재어 \1건 제외", ""),
    (r"^적용 정합성: 조사 변형 교정 ([\d,]+)건.*$",   r"조사 변형 \1건 정리", ""),
    (r"^교정 합성: .*?([\d,]+)건을 한 카드로.*$",     r"교정 합성 \1건", ""),

    # ── 분석 결산 ─────────────────────────────────
    (r"^사전·규칙 자동 교정 (.+?) = ([\d,]+)건$",    r"자동 교정 \2건 · \1", ""),
    (r"^검수 카드\(검토 필요\) (.+?) = ([\d,]+)건$", r"검수 카드 \2건 · \1", ""),
    (r"^분석 완료 — 교정 ([\d,]+)건$",              r"분석 완료 · 교정 \1건", ""),
    (r"^본문 ([\d,]+)곳에 해당.*$",                 r"본문 \1곳 등장 (반복 포함)", ""),
    (r"^신뢰도 낮음 ([\d,]+)건은 자동 적용에서 제외.*$", r"저신뢰 \1건 자동 적용 제외", ""),

    # ── 적용 ──────────────────────────────────────
    (r"^적용 결과: 적용 ([\d,]+)건 · 본문 ([\d,]+)곳 치환 · 실패 ([\d,]+)건$",
     r"적용 \1건 · 본문 \2곳 · 실패 \3건", ""),
    (r"^부분 반영 ([\d,]+)건 —.*$",                r"부분 반영 \1건 · 원문 위치 확인", ""),
    (r"^실패 ([\d,]+)건 중 ([\d,]+)건은 이미 반영된.*?실제 실패 ([\d,]+)건.*$",
     r"실패 \1건 중 \2건 기반영 · 실제 실패 \3건", ""),
    (r"^검수 모드 —.*?검수 ([\d,]+)건.*$",          r"검수 모드 · 검수 \1건 정오표만", ""),
    (r"^적용 취소 —.*$",                          "적용 취소 · 원본 무변경", ""),
    (r"^저장 중: (.+)$",                          r"저장 중 · \1", ""),
    (r"^저장 완료$",                              "저장 완료", ""),
    (r"^완료 — (.+)$",                            r"완료 · \1", ""),
    (r"^검수 완료 — 검수 ([\d,]+)건.*$",            r"검수 완료 · 검수 \1건 정오표 기록", ""),
    (r"^정오표 수동 생성 완료: (.+)$",              r"정오표 생성 · \1", ""),

    # ── 부가 기능(학습·계정·큐레이션) ───────────────
    (r"^\[학습\] 교정 결정 ([\d,]+)건을.*$",        r"학습 큐 기록 \1건", ""),
    (r"^\[학습\] 이벤트 기록 스킵.*$",              "학습 기록 스킵", ""),
    # 파이프라인 단계 스킵 — '[X] 후처리 실패 (스킵): 예외' → 'X · 후처리 실패 스킵'
    (r"^\[([^\]]+)\]\s*(.+?)\s*\(?(스킵|건너뜀)\)?[:：]?.*$", r"\1 · \2 \3", ""),
    # 남은 [태그] 접두 일반화
    (r"^\[([^\]]+)\]\s*(.+)$",                    r"\1 · \2", ""),
))

# 규칙에 걸리지 않은 긴 문장은 첫 절(' — ' 앞)만 남긴다. err는 예외(원문 유지).
_TAIL_TRIM_OVER = 34


def _condense(msg: str, lvl: str):
    """원문 로그 한 줄 → (표시 문자열, 합침키). 숨길 라인이면 None."""
    raw = (msg or "").strip()
    if not raw:
        return None
    if lvl != "err":
        if any(kw in raw for kw in _DROP):
            return None
        if _EXAMPLE_RE.match(raw):
            return None

    m = raw.lstrip()
    while m and m[0] in _LEAD_CHARS:
        m = m[1:].lstrip()
    m = m.rstrip("…")
    m = re.sub(r"([가-힣])\.$", r"\1", m)

    for rx, repl, key in _RULES:
        if rx.match(m):
            return rx.sub(repl, m).strip(), key

    if lvl != "err":
        # 꼬리 사유 괄호 제거 — '…제외 (거짓 검수 방지)'
        m = re.sub(r"\s*\([^()]{6,}\)\s*$", "", m).strip()
        if len(m) > _TAIL_TRIM_OVER:
            head = re.split(r"\s—\s", m, 1)[0].strip()
            if len(head) >= 8:
                m = head
    return m, ""


# ══════════════════════════════════════════════════════════════

_TS_FONT_PX  = 10
_MSG_FONT_PX = 11
_TS_GUTTER   = 12      # 시각 열 우측 여백(px)
_ROW_PAD     = 3       # 셀 상하 여백 → 행 간격
_LINE_HEIGHT = 138     # 내용 줄간격(%) — 2줄 이상 wrap될 때 가독성

# ⚠ PySide6의 setLineHeight(height, heightType)는 heightType을 **int**로 받는다
#   (LineHeightTypes Enum 객체를 그대로 넘기면 TypeError, int()도 불가) → .value.
_PROPORTIONAL = QTextBlockFormat.ProportionalHeight.value


class ActivityPanel(QFrame):
    EXPANDED_WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []     # (time_str, level, msg) — **원문 그대로**
        self._rows    = []     # (time_str, level, text, key) — 화면 표시본
        self._table   = None   # QTextTable(2단 그리드) — 첫 로그에서 생성
        self._build_ui()

    def _build_ui(self):
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        from ui.widgets.components import section_card
        frame, body = section_card("진행 및 결과", "clipboard-check")

        # 로그 영역 — 2단 그리드(QTextTable)로 렌더한다.
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("작업을 시작하면 여기에 진행 내역이 누적됩니다.")
        self._log.setLineWrapMode(QTextEdit.WidgetWidth)
        # 한글·긴 영문 토큰이 열 최소폭을 밀어 가로 스크롤을 만들지 않도록 어디서나 줄바꿈.
        self._log.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(self._log, 1)

        self._root.addWidget(frame)

    # ══════════════════════════════════════════════
    # 2단 그리드 렌더
    # ══════════════════════════════════════════════
    def _ts_column_width(self) -> int:
        f = QFont(self._log.font())
        f.setPixelSize(_TS_FONT_PX)
        return QFontMetrics(f).horizontalAdvance("00:00:00") + _TS_GUTTER

    def _ensure_table(self):
        if self._table is not None:
            return self._table
        fmt = QTextTableFormat()
        fmt.setBorder(0)
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_None)
        fmt.setCellSpacing(0)
        fmt.setCellPadding(_ROW_PAD)
        fmt.setMargin(0)
        fmt.setPadding(0)
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        # 시각=고정폭, 내용=나머지 전부(VariableLength). 내용은 자기 셀 안에서만
        #   줄바꿈되므로 여러 줄이 돼도 시각 열을 침범하지 않는다.
        fmt.setColumnWidthConstraints([
            QTextLength(QTextLength.FixedLength, self._ts_column_width()),
            QTextLength(QTextLength.VariableLength, 0),
        ])
        cur = self._log.textCursor()
        cur.movePosition(QTextCursor.End)
        self._table = cur.insertTable(1, 2, fmt)
        self._table_filled = 0     # 표는 빈 행 1개로 시작 → 첫 항목이 그 행을 씀
        return self._table

    def _write_row(self, row: int, ts: str, lvl: str, text: str):
        pal = current_palette()
        color = {
            "err":  pal["log_err"], "warn": pal["log_warn"],
            "ok":   pal["log_ok"],  "info": pal["text_sub"],
        }.get(lvl, pal["text_sub"])
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        cells = (
            f'<span style="color:{pal["text_dim"]}; font-size:{_TS_FONT_PX}px;">{ts}</span>',
            f'<span style="color:{color}; font-size:{_MSG_FONT_PX}px;">{safe}</span>',
        )
        bf = QTextBlockFormat()
        bf.setLineHeight(_LINE_HEIGHT, _PROPORTIONAL)
        for col, html in enumerate(cells):
            cell = self._table.cellAt(row, col)
            cur = cell.firstCursorPosition()
            cur.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            cur.insertHtml(html)
            cur.mergeBlockFormat(bf)

    def _append_row(self, ts: str, lvl: str, text: str):
        table = self._ensure_table()
        if self._table_filled:
            table.appendRows(1)
        row = table.rows() - 1
        self._table_filled += 1
        self._write_row(row, ts, lvl, text)

    def _scroll_to_end(self):
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ══════════════════════════════════════════════
    # 로그 API
    # ══════════════════════════════════════════════
    def log(self, msg: str, level: str = None):
        msg = (msg or "").rstrip()
        if not msg:
            return
        lvl = level or _infer_level(msg)
        ts = time.strftime("%H:%M:%S")
        self._entries.append((ts, lvl, msg))      # 원문 보존(완료 보고서·디버깅용)

        shown = _condense(msg, lvl)
        if shown is None:
            return
        text, key = shown
        if not text:
            return
        # 같은 종류의 진행 라인은 새 행을 쌓지 않고 마지막 행을 갱신
        if key and self._rows and self._rows[-1][3] == key and self._table is not None:
            self._rows[-1] = (ts, lvl, text, key)
            self._write_row(self._table.rows() - 1, ts, lvl, text)
        else:
            self._rows.append((ts, lvl, text, key))
            self._append_row(ts, lvl, text)
        self._scroll_to_end()

    def _render_all(self):
        """테마 전환 등 전체 재렌더 — 이미 요약된 표시본(_rows)만 다시 그린다."""
        rows = list(self._rows)
        self._log.clear()
        self._table = None
        for ts, lvl, text, _key in rows:
            self._append_row(ts, lvl, text)
        self._scroll_to_end()

    def get_proofreading_log(self):
        """완료 보고서용 — HWP 배관 로그를 제외한 교정교열 관련 **원문** 로그.
        반환: [(time_str, level, msg), ...]"""
        out = []
        for ts, lvl, msg in self._entries:
            if any(kw in msg for kw in _PLUMBING_KW):
                continue
            out.append((ts, lvl, msg))
        return out

    def clear(self):
        """새 파일에서만 호출 — 로그 초기화."""
        self._entries.clear()
        self._rows.clear()
        self._table = None
        self._log.clear()

    # ══════════════════════════════════════════════
    # 테마
    # ══════════════════════════════════════════════
    def refresh_theme(self):
        self._render_all()
