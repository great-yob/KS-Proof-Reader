"""
정오표(正誤表) Excel 생성 모듈
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
교정 적용 결과를 출판사 실무 수준의 Excel 정오표로 출력.

시트 구성:
  📋 정오표  — 항목별 상세 내역 (교정 유형·수정 전후·이유·적용 결과)
  📊 요약    — 소스별 통계, 교정 옵션 기록

색상 범례 (행 배경):
  노란색   ← 사전검증 기본
  연두색   ← AI 오탈자 보완
  연보라   ← AI 윤문
  주황색   ← 사전 미등재 주의 항목
  연분홍   ← HWP 매칭 실패 (미적용)

설치:
  pip install openpyxl
"""

import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side,
)

# ── 색상 팔레트 (ARGB) ─────────────────────────────────
_C = {
    # 헤더 배경
    "header_bg":     "FF1F3864",   # 네이비
    "header_fg":     "FFFFFFFF",   # 흰색

    # 타이틀 배경
    "title_bg":      "FF2E4D7B",   # 짙은 파랑
    "title_fg":      "FFFFFFFF",

    # 소계 행
    "sub_bg":        "FFD9E1F2",   # 연파랑

    # 교정 유형별 행 배경
    "dict":          "FFFFF9C4",   # 연노랑   (사전검증 기본)
    "ai_typo":       "FFE8F5E9",   # 연초록   (AI 오탈자)
    "ai_polish":     "FFEDE7F6",   # 연보라   (AI 윤문)
    "unverified":    "FFFFF3E0",   # 연주황   (사전 미등재 주의)
    "dict_flag":     "FFFFF8E1",   # 연앰버   (사전 검수 — 미등재어 점검)
    "spacing":       "FFE0F2F1",   # 연청록   (띄어쓰기 검수 제안 — 자동수정 아님)
    "punct":         "FFF3E5F5",   # 연자주   (문장부호 검수 제안 — 괄호 짝 등, 자동수정 아님)
    "fail":          "FFFFEBEE",   # 연분홍   (매칭 실패)
    "rejected":      "FFF2F2F2",   # 연회색   (사용자 거절 — 적용 대상 아님)

    # 적용 결과 텍스트
    "ok_fg":         "FF1B5E20",   # 진초록
    "fail_fg":       "FFB71C1C",   # 진빨강
    "warn_fg":       "FFE65100",   # 진주황
    "rej_fg":        "FF9E9E9E",   # 회색     (거절)

    # 테두리
    "border":        "FFB0BEC5",
}

# ── 소스 레이블 ────────────────────────────────────────
_SOURCE_LABEL = {
    "dict":      "사전검증",
    "ai_typo":   "AI 오탈자",
    "ai_polish": "AI 윤문",
    "dict_flag": "사전 검수",
    "spacing":   "띄어쓰기",
    "punct":     "문장부호",
}

# HWP 미등재어 경고 색 (BGR 0x0055FF → 구분 목적)
HL_UNVERIFIED = 0x0055FF


def generate_errata(
    detail: list,        # apply_corrections()가 반환하는 per-item 결과 리스트
    hwp_path: str,       # 원본 HWP 파일 경로 (출력 파일명 생성용)
    options: dict,       # 교정 옵션 {"used_ai", "mode", "used_dict"}
    output_path: str = None,  # None이면 HWP 파일과 같은 폴더에 자동 저장
) -> str:
    """
    정오표 Excel 파일 생성.

    Args:
        detail:      [{"original", "corrected", "reason", "source",
                       "color", "applied", "error"}, ...]
        hwp_path:    원본 HWP 경로
        options:     {"used_ai": bool, "mode": "typo"|"polish", "used_dict": bool}
        output_path: 저장 경로. None이면 자동 결정.

    Returns:
        생성된 xlsx 파일 경로
    """
    if output_path is None:
        base, _ = os.path.splitext(hwp_path)
        output_path = base + "_정오표.xlsx"

    wb = Workbook()
    wb.remove(wb.active)  # 기본 시트 제거

    _build_errata_sheet(wb, detail, hwp_path, options)
    _build_summary_sheet(wb, detail, hwp_path, options)

    wb.save(output_path)
    return output_path


# ══════════════════════════════════════════════════════
# ▌Sheet 1: 정오표
# ══════════════════════════════════════════════════════

def _build_errata_sheet(wb: Workbook, detail: list, hwp_path: str, options: dict):
    ws = wb.create_sheet("정오표")

    # ── 열 너비 ───────────────────────────────────────
    col_widths = {
        "A": 6,    # 순번
        "B": 16,   # 교정 유형
        "C": 38,   # 수정 전
        "D": 38,   # 수정 후
        "E": 30,   # 교정 이유
        "F": 10,   # 출처
        "G": 12,   # 사전검증
        "H": 12,   # 적용 결과
    }
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width

    # ── 타이틀 블록 (행 1~3) ──────────────────────────
    _write_title_block(ws, hwp_path, options)

    # ── 헤더 행 (행 4) ────────────────────────────────
    headers = ["순번", "교정 유형", "수정 전", "수정 후", "교정 이유", "출처", "사전검증", "적용 결과"]
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx, value=h)
        cell.font      = Font(name="맑은 고딕", bold=True, color=_C["header_fg"], size=10)
        cell.fill      = PatternFill("solid", fgColor=_C["header_bg"])
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = _thin_border()
    ws.row_dimensions[4].height = 24

    # 헤더 행 고정
    ws.freeze_panes = "A5"

    # 자동 필터
    ws.auto_filter.ref = f"A4:H{4 + len(detail)}"

    # ── 데이터 행 (행 5~) ─────────────────────────────
    for row_offset, item in enumerate(detail):
        row = 5 + row_offset

        applied     = item.get("applied", False)
        source      = item.get("source", "")
        color_val   = item.get("color", 0)
        is_unverif  = (color_val == HL_UNVERIFIED)
        is_flag     = (source == "dict_flag")   # 사전 검수(치환 없음)
        # 검수 플래그여도 사용자가 값을 직접 고쳤다면(corrected != original) 실제 교정으로
        #   적용된다. '검수(치환 없음)' 표기는 미편집 플래그에만 적용한다.
        is_flag_review = is_flag and item.get("corrected", "") == item.get("original", "")
        is_spacing  = (source == "spacing")     # 띄어쓰기 검수 제안(자동수정 아님)
        is_punct    = (source == "punct")       # 문장부호 검수 제안(괄호 짝 등 — 자동수정 아님)
        is_review_only = is_spacing or is_punct  # 자동수정 아닌 '검수' 제안(실패 아님)
        # 실제 적용 결과 병합 필드(apply_worker가 채움) — 구버전 호출(decision 부재)은
        #   기존 동작(수락=적용 가정)으로 폴백한다.
        decision    = item.get("decision", "")
        is_rejected = (decision == "rejected")            # 사용자 거절(적용 대상 아님)
        is_consumed = bool(item.get("consumed"))          # 긴/동일 교정에 포함 처리(정상)
        is_partial  = bool(item.get("partial"))           # 수락 등장 일부만 치환됨

        # 행 배경 결정
        if is_rejected:
            row_bg = _C["rejected"]
        elif is_flag_review:
            row_bg = _C["dict_flag"]
        elif decision == "accepted" and not applied and not is_consumed:
            row_bg = _C["fail"]          # 수락했는데 문서 반영 실패 — 소스 무관 실패색
        elif is_spacing:
            row_bg = _C["spacing"]
        elif is_punct:
            row_bg = _C["punct"]
        elif not applied and not is_consumed:
            row_bg = _C["fail"]          # 구버전 호출 폴백(결정 정보 없음)
        elif is_unverif:
            row_bg = _C["unverified"]
        else:
            row_bg = _C.get(source, "FFFFFFFF")

        fill = PatternFill("solid", fgColor=row_bg)
        border = _thin_border()

        # ① 순번
        _wcell(ws, row, 1, row_offset + 1, fill, border,
               align=Alignment(horizontal="center", vertical="top"))

        # ② 교정 유형
        type_label = _type_label(source, is_unverif)
        _wcell(ws, row, 2, type_label, fill, border,
               align=Alignment(horizontal="center", vertical="top"))

        # ③ 수정 전
        _wcell(ws, row, 3, item.get("original", ""), fill, border,
               align=Alignment(horizontal="left", vertical="top", wrap_text=True))

        # ④ 수정 후 (미편집 검수 플래그는 치환이 없으므로 안내 문구 / 편집된 플래그는 교정값)
        corrected_disp = "(검수 필요 — 표제어 확인)" if is_flag_review else item.get("corrected", "")
        _wcell(ws, row, 4, corrected_disp, fill, border,
               align=Alignment(horizontal="left", vertical="top", wrap_text=True))

        # ⑤ 교정 이유 — 실패/부분 반영이면 실제 적용 오류 사유를 함께 남긴다
        reason = item.get("reason", "") or ""
        err = item.get("error", "") or ""
        if err and not is_rejected and (is_partial or (not applied and not is_consumed)):
            reason = f"{reason} ◆ {err}" if reason else err
        elif not reason:
            reason = err
        _wcell(ws, row, 5, reason, fill, border,
               align=Alignment(horizontal="left", vertical="top", wrap_text=True))

        # ⑥ 출처
        src_label = _SOURCE_LABEL.get(source, source)
        _wcell(ws, row, 6, src_label, fill, border,
               align=Alignment(horizontal="center", vertical="top"))

        # ⑦ 사전검증
        dict_val = ("⚠ 주의" if is_unverif
                    else "—" if (is_rejected or is_review_only or is_flag_review)
                    else "확인" if (not applied and not is_consumed) else "✔")
        dict_fg  = _C["warn_fg"] if is_unverif else _C["ok_fg"]
        cell = _wcell(ws, row, 7, dict_val, fill, border,
                      align=Alignment(horizontal="center", vertical="top"))
        cell.font = Font(name="맑은 고딕", size=9,
                         color=dict_fg if not is_unverif else _C["warn_fg"],
                         bold=is_unverif)

        # ⑧ 적용 결과 — 검수 패널 결정(수락/거절)과 브리지 실적용 결과를 그대로 반영
        if is_rejected:
            result_text, result_fg = "— 거절", _C["rej_fg"]
        elif is_flag_review:
            result_text, result_fg = "🔍 검수", _C["warn_fg"]
        elif is_partial:
            result_text, result_fg = "⚠ 부분 반영", _C["warn_fg"]
        elif applied:
            result_text, result_fg = "✔ 적용", _C["ok_fg"]
        elif is_consumed:
            result_text, result_fg = "✔ 포함 적용", _C["ok_fg"]   # 긴 교정에 함께 처리(정상)
        elif is_review_only and not decision:
            result_text, result_fg = "🔍 검수", _C["warn_fg"]   # 구버전 호출 폴백(결정 정보 없음)
        else:
            result_text, result_fg = "✖ 실패", _C["fail_fg"]
        cell = _wcell(ws, row, 8, result_text, fill, border,
                      align=Alignment(horizontal="center", vertical="top"))
        cell.font = Font(name="맑은 고딕", size=9, color=result_fg, bold=True)

        ws.row_dimensions[row].height = max(
            18, min(60, 15 + (len(item.get("original","")) // 20) * 5)
        )

    # ── 범례 (데이터 아래 2행 공백 후) ─────────────────
    legend_row = 5 + len(detail) + 2
    ws.cell(row=legend_row, column=1, value="[색상 범례]").font = \
        Font(name="맑은 고딕", bold=True, size=9, color="FF555555")

    legends = [
        (_C["dict"],       "사전검증 기본"),
        (_C["ai_typo"],    "AI 오탈자 보완"),
        (_C["ai_polish"],  "AI 윤문"),
        (_C["unverified"], "⚠ 사전 미등재 주의 — 사람 검토 필요"),
        (_C["fail"],       "적용 실패 — 수락했으나 문서에 반영되지 않음 (수동 확인 필요)"),
        (_C["rejected"],   "사용자 거절 — 적용 대상 아님"),
    ]
    for i, (color, label) in enumerate(legends):
        r = legend_row + 1 + i
        cell_color = ws.cell(row=r, column=1)
        cell_color.fill      = PatternFill("solid", fgColor=color)
        cell_color.border    = _thin_border()
        cell_label = ws.cell(row=r, column=2, value=label)
        cell_label.font      = Font(name="맑은 고딕", size=8, color="FF333333")
        cell_label.alignment = Alignment(vertical="center")
        ws.row_dimensions[r].height = 16


def _write_title_block(ws, hwp_path: str, options: dict):
    """타이틀 블록 (행 1~3) 작성"""
    doc_name  = os.path.basename(hwp_path)
    now_str   = datetime.now().strftime("%Y년 %m월 %d일  %H:%M")
    mode_str  = "오탈자·띄어쓰기" if options.get("mode") == "typo" else "전체 윤문"
    stages    = []
    if options.get("deep_screening"): stages.append("표준국어대사전 1차 심층 스크리닝")
    if options.get("used_ai"):        stages.append(f"Gemini AI ({mode_str})")
    if options.get("used_dict"):      stages.append("표준국어대사전 재검증·일관성 가드")

    # 행 1: 정오표 제목
    ws.merge_cells("A1:H1")
    cell = ws["A1"]
    cell.value     = "정  오  표  (正誤表)"
    cell.font      = Font(name="맑은 고딕", bold=True, size=16, color=_C["title_fg"])
    cell.fill      = PatternFill("solid", fgColor=_C["title_bg"])
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 36

    # 행 2: 문서 정보
    ws.merge_cells("A2:H2")
    cell = ws["A2"]
    cell.value     = (
        f"  대상 파일: {doc_name}      "
        f"교정 일시: {now_str}      "
        f"교정 단계: {' → '.join(stages)}"
    )
    cell.font      = Font(name="맑은 고딕", size=9, color="FF333333")
    cell.fill      = PatternFill("solid", fgColor="FFE8EDF5")
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 20

    # 행 3: 빈 구분행
    ws.merge_cells("A3:H3")
    ws["A3"].fill = PatternFill("solid", fgColor="FFFFFFFF")
    ws.row_dimensions[3].height = 6


# ══════════════════════════════════════════════════════
# ▌Sheet 2: 요약
# ══════════════════════════════════════════════════════

def _build_summary_sheet(wb: Workbook, detail: list, hwp_path: str, options: dict):
    ws = wb.create_sheet("요약")

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 14

    # 제목
    ws.merge_cells("A1:C1")
    cell = ws["A1"]
    cell.value     = "교정 결과 요약"
    cell.font      = Font(name="맑은 고딕", bold=True, size=14, color=_C["title_fg"])
    cell.fill      = PatternFill("solid", fgColor=_C["title_bg"])
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # ── 기본 정보 ─────────────────────────────────────
    now_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode_str = "오탈자·띄어쓰기 보완" if options.get("mode") == "typo" else "전체 윤문"

    info_rows = [
        ("대상 파일",    os.path.basename(hwp_path)),
        ("교정 일시",    now_str),
        ("AI 교정 모드", mode_str),
        ("AI 사용",      "✔" if options.get("used_ai")   else "—"),
        ("사전검증 사용","✔" if options.get("used_dict") else "—"),
    ]

    for r, (label, value) in enumerate(info_rows, start=3):
        lc = ws.cell(row=r, column=1, value=label)
        lc.font      = Font(name="맑은 고딕", bold=True, size=10)
        lc.fill      = PatternFill("solid", fgColor="FFE8EDF5")
        lc.alignment = Alignment(vertical="center")
        lc.border    = _thin_border()

        vc = ws.cell(row=r, column=2, value=value)
        vc.font      = Font(name="맑은 고딕", size=10)
        vc.alignment = Alignment(vertical="center")
        vc.border    = _thin_border()
        ws.row_dimensions[r].height = 20

    # ── 통계 테이블 ───────────────────────────────────
    total        = len(detail)
    applied_all  = [d for d in detail if d.get("applied") or d.get("consumed")]
    rejected_all = [d for d in detail if d.get("decision") == "rejected"]

    def _is_real_fail(d):
        """'수락했는데 문서 반영 실패'만 실패로 집계 — 거절/포함 처리/검수 플래그 제외.
        decision 필드가 없는 구버전 호출은 기존 규칙(검수 소스 제외)으로 폴백."""
        if d.get("applied") or d.get("consumed"):
            return False
        if d.get("decision") == "rejected":
            return False
        if d.get("source") == "dict_flag" and d.get("corrected", "") == d.get("original", ""):
            return False
        if "decision" not in d and d.get("source") in ("dict_flag", "spacing", "punct"):
            return False
        return True

    failed_all  = [d for d in detail if _is_real_fail(d)]
    unverif     = [d for d in detail if d.get("color") == HL_UNVERIFIED]

    by_source = {}
    for src in ("dict", "ai_typo", "ai_polish"):
        items = [d for d in detail if d.get("source") == src]
        ok    = [d for d in items if d.get("applied") or d.get("consumed")]
        fails = [d for d in items if _is_real_fail(d)]
        by_source[src] = (len(items), len(ok), len(fails))

    stat_start = len(info_rows) + 5

    # 통계 헤더
    _stat_header(ws, stat_start, ["교정 소스", "전체", "적용 성공", "매칭 실패"])

    stat_data = [
        ("사전검증",  *by_source["dict"]),
        ("AI 오탈자", *by_source["ai_typo"]),
        ("AI 윤문",   *by_source["ai_polish"]),
        ("합계",      total, len(applied_all), len(failed_all)),
    ]

    for r_off, (label, total_c, ok_c, fail_c) in enumerate(stat_data):
        r = stat_start + 1 + r_off
        is_total = (label == "합계")
        bg = "FFD9E1F2" if is_total else "FFFFFFFF"

        for c_idx, val in enumerate([label, total_c, ok_c, fail_c], start=1):
            cell = ws.cell(row=r, column=c_idx, value=val)
            cell.font      = Font(name="맑은 고딕",
                                  bold=is_total, size=10,
                                  color=(_C["ok_fg"] if c_idx == 3 and val > 0
                                         else _C["fail_fg"] if c_idx == 4 and val > 0
                                         else "FF000000"))
            cell.fill      = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(horizontal="center" if c_idx > 1 else "left",
                                       vertical="center")
            cell.border    = _thin_border()
        ws.row_dimensions[r].height = 20

    # 사전검증 주의 건수
    warn_row = stat_start + 1 + len(stat_data) + 1
    warn_cell = ws.cell(row=warn_row, column=1,
                        value=f"⚠ 사전검증 주의 항목:  {len(unverif)}건 — 직접 검토 후 확인 필요")
    warn_cell.font      = Font(name="맑은 고딕", size=9,
                               color=_C["warn_fg"], bold=len(unverif) > 0)
    warn_cell.alignment = Alignment(vertical="center")
    ws.merge_cells(f"A{warn_row}:C{warn_row}")
    ws.row_dimensions[warn_row].height = 18

    # 사용자 거절 건수 — 적용 대상이 아니므로 실패와 구분해 별도 표기
    rej_row = warn_row + 1
    rej_cell = ws.cell(row=rej_row, column=1,
                       value=f"ℹ 사용자 거절 항목:  {len(rejected_all)}건 — 검토 단계에서 제외됨 (적용 대상 아님)")
    rej_cell.font      = Font(name="맑은 고딕", size=9, color=_C["rej_fg"])
    rej_cell.alignment = Alignment(vertical="center")
    ws.merge_cells(f"A{rej_row}:C{rej_row}")
    ws.row_dimensions[rej_row].height = 18


# ══════════════════════════════════════════════════════
# ▌공통 헬퍼
# ══════════════════════════════════════════════════════

def _wcell(ws, row, col, value, fill, border, align=None, font=None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.fill   = fill
    cell.border = border
    if align:
        cell.alignment = align
    if font:
        cell.font = font
    else:
        cell.font = Font(name="맑은 고딕", size=9)
    return cell


def _thin_border():
    side = Side(style="thin", color=_C["border"])
    return Border(left=side, right=side, top=side, bottom=side)


def _stat_header(ws, row, labels):
    for c_idx, label in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=c_idx, value=label)
        cell.font      = Font(name="맑은 고딕", bold=True,
                               size=10, color=_C["header_fg"])
        cell.fill      = PatternFill("solid", fgColor=_C["header_bg"])
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border    = _thin_border()
    ws.row_dimensions[row].height = 22


def _type_label(source: str, is_unverif: bool) -> str:
    base = _SOURCE_LABEL.get(source, source)
    return f"⚠ {base}" if is_unverif else base
