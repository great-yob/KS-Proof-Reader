"""
ui/workers/apply_worker.py — HWP 교정 적용 QThread 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
수락된 교정 항목을 HWP 문서에 적용하고 교정본/정오표를 생성.
"""

import os
import threading

from PySide6.QtCore import QThread, Signal

from core import Correction, HwpEditor
from core.models import HL_DICT


class ApplyWorker(QThread):
    """교정 적용 백그라운드 워커"""

    progress = Signal(int, str)
    log_message = Signal(str)
    finished = Signal(dict)    # {"applied", "failed", "hwp_path", "errata_path", "fail_samples"}
    error    = Signal(str)

    def __init__(self, file_path: str, corrections: list, options: dict, parent=None):
        super().__init__(parent)
        self.file_path   = file_path
        self.corrections = corrections
        self.options     = options
        self._stop       = threading.Event()

    def request_stop(self):
        """적용 취소 — HwpEditor가 배치 사이에서 감지해 중단한다.
        중단 시 교정본을 저장하지 않으므로 원본 파일은 변경되지 않는다."""
        self._stop.set()

    def run(self):
        try:
            self._execute()
        except Exception as exc:
            self.error.emit(f"적용 중 오류: {exc}")

    def _execute(self):
        log = self.log_message.emit

        # 수락된 항목 → 실제 교정(치환)과 검수 플래그를 분리.
        #   검수 플래그(source=="dict_flag")는 치환 후보가 없어 HWP를 수정하지 않는다(정오표 전용).
        #   단, 사용자가 검수 카드 값을 직접 고쳤다면(corrected != original) 더 이상 단순
        #   '검수'가 아니라 실제 교정이므로 본문에 적용한다.
        accepted = [c for c in self.corrections if c.get("status") == "accepted"]

        def _flag_only(c):
            return (c.get("source") == "dict_flag"
                    and c.get("corrected", "") == c.get("original", ""))

        real_corrections = [c for c in accepted if not _flag_only(c)]
        flag_accepted    = [c for c in accepted if _flag_only(c)]

        correction_objs = [
            Correction(
                original  = c["original"],
                corrected = c["corrected"],
                reason    = c.get("reason", ""),
                source    = c.get("source", "dict"),
                color     = c.get("color", HL_DICT),
                skip_occurrences = c.get("skip_occurrences", []),
            )
            for c in real_corrections
        ]

        stats, detail = {}, []
        out_hwp = ""

        if real_corrections:
            self.progress.emit(10, "HWP 파일 열기 중…")
            editor = None
            try:
                editor = HwpEditor(self.file_path, logger=log)
                editor.open()
                log(f"  적용 대상 파일: {os.path.basename(self.file_path)}")

                def progress_cb(current, total):
                    if total > 0:
                        pct = 10 + int((current / total) * 70)
                        self.progress.emit(min(pct, 80), f"교정 적용 중… {current}/{total}")

                stats, detail = editor.apply_corrections(
                    correction_objs, progress_cb=progress_cb, stop_event=self._stop)

                # 취소 — 저장하지 않고 종료(원본 무변경). finally가 HWP를 닫는다.
                if self._stop.is_set():
                    log("  ⚠ 적용 취소 — 교정본을 저장하지 않았습니다 (원본 파일 무변경).")
                    self.error.emit("사용자에 의해 취소되었습니다.\n"
                                    "교정본은 저장되지 않았고 원본 파일은 변경되지 않았습니다.")
                    return

                ok_count = stats.get("dict", 0) + stats.get("ai_typo", 0) + stats.get("ai_polish", 0)
                occ_count = sum(d.get("replaced", 0) for d in detail if d.get("applied"))
                log(f"  적용 결과: 적용 {ok_count}건 · 본문 {occ_count}곳 치환 · 실패 {stats.get('fail', 0)}건")

                # 교정본 저장
                base, ext = os.path.splitext(self.file_path)
                out_hwp = base + "_교정본" + ext
                log(f"  저장 중: {os.path.basename(out_hwp)}")
                editor.save_as(out_hwp)
                log(f"  ✓ 저장 완료")
                self.progress.emit(85, "교정본 저장 완료")
            finally:
                if editor is not None:
                    try:
                        editor.close()
                    except Exception:
                        pass
        else:
            # 검수 모드 — 적용할 치환이 없다. HWP를 열지 않고 정오표만 만든다.
            log(f"  검수 모드 — HWP를 수정하지 않습니다 (검수 {len(flag_accepted)}건 · 정오표만 생성).")
            self.progress.emit(85, "검수 결과 정리 중…")

        # 모드 결정: 윤문 옵션이 켜져있으면 polish, 아니면 typo
        mode = "polish" if self.options.get("scope_polish") else "typo"
        # ⚠ 정오표 생성은 S3(포함 처리)/S4(부분 반영) 재분류 **이후**에 수행한다 —
        #   실제 적용 결과(applied/error/consumed/partial)를 정오표에 반영하기 위함.

        # S3: 실패 항목 중 "이미 반영된 것"을 재분류해 실패 집계에서 제외.
        #   (a) 짧은 원문이 긴 성공 교정에 통째로 포함돼 함께 치환된 경우
        #       (긴 교정이 먼저 적용되면 그 안의 짧은 원문은 변형되어 "본문에 없음"이 됨).
        #   (b) 같은 단어가 이미 성공 교정된 경우(일관성 변형 중복).
        #       어간이 본문 부분문자열을 먼저 바꿔, 남은 조사 변형이 0건 매칭된 케이스다.
        #       판정 기준은 '교정 결과 base(조사 제거)'다. 원문 base는 비문 조사형
        #       (예: '홋가이도현와' — 받침 뒤 '와'는 비문이라 형태소 분석이 조사로 못
        #       가름)에서 어긋날 수 있으나, 교정 결과(목표어)는 항상 문법적이라 같은
        #       단어끼리 안정적으로 묶인다. cb != ob 조건으로 '조사만 바꾼 교정'은 제외.
        from core.consistency_pass import _strip_josa

        applied_originals = [d.get("original", "") for d in detail if d.get("applied")]
        # 성공 교정의 '교정 결과 base' 집합 — 같은 단어가 이미 반영됐는지 판단용
        applied_corrected_bases = set()
        for d in detail:
            if not d.get("applied"):
                continue
            ob, cb = _strip_josa(d.get("original", "")), _strip_josa(d.get("corrected", ""))
            if cb and cb != ob:
                applied_corrected_bases.add(cb)

        consumed_cnt = 0
        for d in detail:
            if d.get("applied"):
                continue
            orig = d.get("original", "")
            corr = d.get("corrected", "")
            # (a) 긴 성공 교정에 포함되어 함께 치환됨
            if orig and any(orig in a and orig != a for a in applied_originals):
                d["error"] = "긴 교정 항목에 포함되어 함께 처리됨 (정상)"
                d["consumed"] = True
                consumed_cnt += 1
                continue
            # (b) 같은 단어가 다른 조사형으로 이미 교정됨
            ob, cb = _strip_josa(orig), _strip_josa(corr)
            if cb and cb != ob and cb in applied_corrected_bases:
                d["error"] = "같은 단어의 다른 조사형으로 함께 교정됨 (정상)"
                d["consumed"] = True
                consumed_cnt += 1
                continue

        # S4: 부분 반영 감지 — '수락한 등장 수'와 '실제 치환 수' 대조 (치명 오류 안전망).
        #   검수 패널이 계산한 본문 실등장 수(occurrences)에서 skip(부분 거절·제외)을 뺀
        #   기대 치환 수보다 실제 치환(replaced)이 적으면, 수락한 교정 일부가 문서에
        #   반영되지 않은 것이다(보이지 않는 조판 문자·찾기 누락 등). 과거엔 replaced≥1이면
        #   '적용 성공'으로만 집계돼 **조용히 누락**됐다(사용자 보고 2026-07-03 — 신뢰성
        #   치명). 이제 부족분을 실패 항목으로 표출해 편집자가 해당 위치를 확인할 수 있다.
        expected_by_key = {}
        for c in real_corrections:
            occ_n = c.get("occurrences")
            if not isinstance(occ_n, int) or occ_n <= 0:
                continue
            exp = occ_n - len(c.get("skip_occurrences") or [])
            if exp > 0:
                expected_by_key[(c["original"], c["corrected"])] = exp
        partial_samples, partial_cnt = [], 0
        for d in detail:
            if not d.get("applied"):
                continue
            exp = expected_by_key.get((d.get("original", ""), d.get("corrected", "")))
            got = d.get("replaced", 0)
            if exp is None or got >= exp:
                continue
            orig = d.get("original", "")
            # 긴 성공 교정이 이 원문을 포함하면 부족분은 그 교정이 함께 처리한 것(정상)
            if orig and any(orig in a and orig != a for a in applied_originals):
                continue
            partial_cnt += 1
            d["partial"] = True
            d["error"] = (f"⚠ 부분 반영 — 수락 {exp}곳 중 {got}곳만 치환됨 "
                          "(나머지 위치는 본문에서 자동으로 찾지 못함 — 해당 원문 수동 확인 필요)")
            if len(partial_samples) < 10:
                partial_samples.append({
                    "original":  (d.get("original")  or "")[:60],
                    "corrected": (d.get("corrected") or "")[:60],
                    "error":     d["error"],
                })
        if partial_cnt:
            log(f"  ⚠ 부분 반영 {partial_cnt}건 — 수락한 등장 수보다 적게 치환됨 "
                "(실패 항목에 표시, 해당 원문 위치 확인 필요)")

        # 정오표 데이터 — 검수 패널의 '결정'(수락/거절)과 브리지의 '실제 적용 결과'를
        #   병합한다. 과거엔 수락 여부만으로 applied를 채워, 문서 반영에 실패한 항목도
        #   '✔ 적용'으로, 사용자가 거절한 항목은 '✖ 실패'로 잘못 기록됐다(정합성 버그).
        #   gen_errata가 꺼져 있어도 만들어 결과에 동봉한다 — 완료 화면의 '정오표 생성'
        #   수동 버튼이 같은 데이터로 진실된 정오표를 만들 수 있게(main_window가 사용).
        detail_by_key = {}
        for d in detail:
            detail_by_key.setdefault(
                (d.get("original", ""), d.get("corrected", "")), d)

        errata_detail = []
        for c in self.corrections:
            accepted = c.get("status") == "accepted"
            d = detail_by_key.get((c.get("original", ""), c.get("corrected", "")))
            errata_detail.append({
                "original":  c.get("original", ""),
                "corrected": c.get("corrected", ""),
                "reason":    c.get("reason", ""),
                "source":    c.get("source", "dict"),
                "color":     c.get("color", 0),
                "decision":  "accepted" if accepted else "rejected",
                "applied":   bool(accepted and d and d.get("applied")),
                "consumed":  bool(accepted and d and d.get("consumed")),
                "partial":   bool(accepted and d and d.get("partial")),
                "error":     (d.get("error", "") if (accepted and d) else ""),
            })

        errata_path = None
        if self.options.get("gen_errata", True) and errata_detail:
            try:
                self.progress.emit(90, "정오표 생성 중…")
                from output.errata_generator import generate_errata
                errata_path = generate_errata(
                    detail   = errata_detail,
                    hwp_path = self.file_path,
                    options  = {
                        "used_ai":         self.options.get("use_ai", True),
                        "mode":            mode,
                        # 사전 재검증·가드는 항상 동작 → 정오표에도 항상 표기
                        "used_dict":       True,
                        "deep_screening":  self.options.get("deep_screening", False),
                    },
                )
            except Exception as exc:
                # I5: 무음 실패 방지 — 오류를 사용자에게 알림(교정 적용 자체는 완료됨)
                self.error.emit(f"정오표 생성 실패 (교정은 적용됨): {exc}")

        self.progress.emit(100, "완료")

        ok_cnt = stats.get("dict", 0) + stats.get("ai_typo", 0) + stats.get("ai_polish", 0)
        # 부분 반영도 실패로 집계 — '적용 성공' 뒤에 숨은 누락을 반드시 드러낸다(S4).
        fail_cnt = max(0, stats.get("fail", 0) - consumed_cnt) + partial_cnt
        if consumed_cnt:
            log(f"  → 실패 {stats.get('fail', 0)}건 중 {consumed_cnt}건은 이미 반영된 "
                f"중복(정상)으로 제외 → 실제 실패 {fail_cnt}건"
                + (f" (부분 반영 {partial_cnt}건 포함)" if partial_cnt else ""))

        # 진단: (진짜) 실패 사유 샘플 + 부분 반영 항목
        fail_samples = []
        for d in detail:
            if d.get("applied") or d.get("consumed"):
                continue
            fail_samples.append({
                "original":  (d.get("original")  or "")[:60],
                "corrected": (d.get("corrected") or "")[:60],
                "error":     d.get("error", ""),
            })
            if len(fail_samples) >= 5:
                break
        fail_samples.extend(partial_samples)

        self.finished.emit({
            "applied":      ok_cnt,
            "occurrences":  sum(d.get("replaced", 0) for d in detail if d.get("applied")),
            "failed":       fail_cnt,
            "consumed":     consumed_cnt,
            "partial":      partial_cnt,
            "flagged":      len(flag_accepted),   # 검수 모드: 기록된 검수 항목 수
            "hwp_path":     out_hwp,
            "errata_path":  errata_path or "",
            "fail_samples": fail_samples,
            # 완료 화면의 '정오표 생성' 수동 버튼용 — 실제 적용 결과가 병합된 행 데이터
            "errata_detail": errata_detail,
        })
