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

    def __init__(self, file_path: str, corrections: list, options: dict,
                 occ_rows: list = None, parent=None):
        super().__init__(parent)
        self.file_path   = file_path
        self.corrections = corrections
        self.options     = options
        # 검수 패널의 등장(occurrence)별 결정 — 정오표를 '등장 1곳 = 1행'으로 쓰기 위한
        #   유일한 진실 원천이다(ReviewPanel.get_occurrence_rows 주석 참조).
        #   None이면 교정 단위 정보만으로 재구성한다(구버전 호출 폴백).
        self.occ_rows    = occ_rows
        # 결과물 축('정오표만') — _build_errata_rows가 outcome을 가르는 데 쓴다.
        self.errata_only = bool(options.get("errata_only"))
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

        # ★결과물 축 — '정오표만'이면 한글 파일을 **열되 고치지 않는다**(쪽 번호 수집만).
        #   사용자가 설정에서 고르는 값이다(setup_panel `_errata_only` 주석 참조).
        #   ⚠ 과거엔 이 상태가 '수락한 치환이 0건'일 때 우연히 도달했고, 그래서
        #     의도한 것인지 사고인지 구분할 수 없었다. 이제 둘은 다르다:
        #       errata_only=True  → 사용자가 고른 산출물. 수락 교정은 '반영 필요'로 기록.
        #       real_corrections=0 → 그냥 고칠 게 없는 실행.
        errata_only = bool(self.options.get("errata_only"))
        # 실제로 브리지에 보낼 목록. '정오표만'이면 비운다 — `real_corrections` 자체는
        #   '수락한 실제 교정'이라는 뜻 그대로 두어야 집계·로그가 진실을 말한다.
        apply_targets = [] if errata_only else real_corrections

        correction_objs = [
            Correction(
                original  = c["original"],
                corrected = c["corrected"],
                reason    = c.get("reason", ""),
                source    = c.get("source", "dict"),
                color     = c.get("color", HL_DICT),
                skip_occurrences = c.get("skip_occurrences", []),
            )
            for c in apply_targets
        ]

        stats, detail = {}, []
        out_hwp = ""
        pages_by_original = {}

        # ── 쪽 번호 수집 ────────────────────────────────────────────────────
        #   정오표는 '등장 1곳 = 1행'이고 각 행의 좌표가 **한/글 쪽 번호**다.
        #
        #   ★쪽은 **치환 전 문서 한 장면**에서 통째로 뜬다. 브리지 apply도 매치 자리에서
        #   쪽을 곁다리로 기록하지만(occ_pages) 그 값을 쓰면 안 된다 — 치환이 진행되면서
        #   글자 수가 바뀌어 **뒤 페이지가 밀리기 때문**이다(실측 2026-08-05: 같은 낱말
        #   12곳이 치환 전 [3,3,3,4,4,7,8,9,14,14,14,15] → 치환 중 [… 9,10,15,15,15,16]).
        #   게다가 apply는 '긴 원문 우선' 순서라 밀림이 문서 순서와도 무관하게 섞인다.
        #   그래서 apply의 occ_pages는 **어느 등장이 실제로 치환됐는가(ok)** 판정에만 쓰고,
        #   쪽 숫자는 여기서 미리 뜬 것을 쓴다(못 뜬 자리만 occ_pages로 폴백).
        #
        #   비용은 등장당 9ms(실측) — 등장 2,000곳이면 ~20초. 예산(_LOCATE_BUDGET·
        #   _LOCATE_SECONDS)으로 상한이 걸려 있고, 소진되면 남은 항목의 쪽만 빈다.
        want_pages = bool(self.options.get("gen_errata", True))
        locate_targets, _seen_loc = [], set()
        for c in self.corrections:
            orig = c.get("original", "")
            if not orig or orig in _seen_loc:
                continue
            _seen_loc.add(orig)
            locate_targets.append(orig)

        # ⚠ 검수 전용 실행(치환 0건)에서도 쪽 번호를 위해 HWP를 연다 — 그러지 않으면
        #   순수 검수 정오표만 쪽 칸이 통째로 비어 '기능이 고장난 것'처럼 보인다.
        need_editor = bool(apply_targets) or bool(want_pages and locate_targets)

        if need_editor:
            self.progress.emit(10, "HWP 파일 열기 중…")
            editor = None
            try:
                editor = HwpEditor(self.file_path, logger=log)
                editor.open()
                log(f"  적용 대상 파일: {os.path.basename(self.file_path)}")

                # 쪽 번호 — 치환 **전** 문서에서 한 번에 뜬다(치환 없음·문서 무변경)
                if want_pages and locate_targets and not self._stop.is_set():
                    self.progress.emit(12, f"쪽 번호 확인 중… ({len(locate_targets)}건)")
                    try:
                        locator = getattr(editor, "locate_originals", None)
                        if locator is not None:      # hwpx direct 백엔드엔 없다
                            pages_by_original = locator(locate_targets) or {}
                            if pages_by_original:
                                n_occ = sum(len(v) for v in pages_by_original.values())
                                log(f"  [정오표] 쪽 번호 확인 {len(pages_by_original)}건 "
                                    f"· 본문 {n_occ}항목")
                    except Exception as exc:
                        log(f"  [정오표] 쪽 번호 확인 스킵: {exc}")

                if apply_targets:
                    def progress_cb(current, total):
                        if total > 0:
                            pct = 15 + int((current / total) * 65)
                            self.progress.emit(min(pct, 80),
                                               f"교정 적용 중… {current}/{total}")

                    stats, detail = editor.apply_corrections(
                        correction_objs, progress_cb=progress_cb, stop_event=self._stop)

                    # 취소 — 저장하지 않고 종료(원본 무변경). finally가 HWP를 닫는다.
                    if self._stop.is_set():
                        log("  ⚠ 적용 취소 — 교정본을 저장하지 않았습니다 (원본 파일 무변경).")
                        self.error.emit("사용자에 의해 취소되었습니다.\n"
                                        "교정본은 저장되지 않았고 원본 파일은 변경되지 않았습니다.")
                        return

                    ok_count = (stats.get("dict", 0) + stats.get("ai_typo", 0)
                                + stats.get("ai_polish", 0))
                    occ_count = sum(d.get("replaced", 0) for d in detail if d.get("applied"))
                    log(f"  적용 결과: 적용 {ok_count}건 · 본문 {occ_count}항목 치환 "
                        f"· 실패 {stats.get('fail', 0)}건")

                    # 교정본 저장
                    base, ext = os.path.splitext(self.file_path)
                    out_hwp = base + "_교정본" + ext
                    log(f"  저장 중: {os.path.basename(out_hwp)}")
                    editor.save_as(out_hwp)
                    log(f"  ✓ 저장 완료")
                    self.progress.emit(85, "교정본 저장 완료")
                else:
                    # 치환은 없다. 문서를 연 것은 쪽 번호를 읽기 위해서일 뿐이다.
                    log(self._no_apply_log(errata_only, real_corrections, flag_accepted))
                    self.progress.emit(85, "정오표 정리 중…")
            finally:
                if editor is not None:
                    try:
                        editor.close()
                    except Exception:
                        pass
        else:
            # 적용할 치환도, 쪽 번호를 찾을 원문도 없다 — HWP를 아예 열지 않는다.
            log(self._no_apply_log(errata_only, real_corrections, flag_accepted))
            self.progress.emit(85, "정오표 정리 중…")

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
            d["error"] = (f"⚠ 부분 반영 — 수락 {exp}항목 중 {got}항목만 치환됨 "
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

        # 정오표 데이터 — 검수 패널의 '등장별 결정'과 브리지의 '등장별 실제 적용 결과'를
        #   병합한다. 과거엔 수락 여부만으로 applied를 채워, 문서 반영에 실패한 항목도
        #   '✔ 적용'으로, 사용자가 거절한 항목은 '✖ 실패'로 잘못 기록됐다(정합성 버그).
        #   gen_errata가 꺼져 있어도 만들어 결과에 동봉한다 — 완료 화면의 '정오표 생성'
        #   수동 버튼이 같은 데이터로 진실된 정오표를 만들 수 있게(main_window가 사용).
        errata_rows = self._build_errata_rows(detail, pages_by_original)

        errata_path = None
        if self.options.get("gen_errata", True) and errata_rows:
            try:
                self.progress.emit(90, "정오표 생성 중…")
                from output.errata_generator import generate_errata
                errata_path = generate_errata(
                    rows     = errata_rows,
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
            "flagged":      len(flag_accepted),   # 기록된 검수(치환 없음) 항목 수
            # ── '정오표만' 결과물 전용 수치 ──────────────────────────────
            #   ⚠ 이 모드에서 `applied`는 **0이어야 한다**. 완료 화면이 그 값으로
            #     '적용 N건'을 그리므로, 수락 건수를 여기 담으면 고치지도 않은 문서를
            #     고쳤다고 보고하게 된다. 수락분은 아래 두 값으로 따로 나른다.
            "errata_only":  errata_only,
            "to_apply":     len(real_corrections) if errata_only else 0,   # 항목(건)
            "to_apply_occ": (sum(1 for r in errata_rows if r["outcome"] == "todo")
                             if errata_only else 0),                        # 등장(곳)
            "hwp_path":     out_hwp,
            "errata_path":  errata_path or "",
            "fail_samples": fail_samples,
            # 완료 화면의 '정오표 생성' 수동 버튼용 — 실제 적용 결과·쪽 번호가 병합된
            #   **등장 단위** 행 데이터(정오표 한 줄 = 이 목록 한 항목).
            "errata_rows": errata_rows,
        })

    @staticmethod
    def _no_apply_log(errata_only: bool, real_corrections: list, flag_accepted: list) -> str:
        """치환 없이 끝나는 두 경우의 화면 로그 — **사유가 다르므로 문구도 다르다**.

        ⚠ 화면 로그 규약: `[태그]` + `n건`, 개별 예시 금지(activity_panel 참조).
        """
        if errata_only:
            return (f"  [정오표만] 한글 파일을 수정하지 않습니다 "
                    f"· 반영 필요 {len(real_corrections)}건 · 검수 {len(flag_accepted)}건")
        return (f"  [정오표만] 적용할 교정이 없습니다 "
                f"· 검수 {len(flag_accepted)}건 기록")

    # ══════════════════════════════════════════════════════
    # ▌정오표 행(= 등장 1곳) 구성
    # ══════════════════════════════════════════════════════
    def _build_errata_rows(self, detail: list, pages_by_original: dict) -> list:
        """교정 단위 결과를 **등장 단위 정오표 행**으로 펼친다.

        정오표는 '몇 쪽의 무엇을 무엇으로'가 한 줄이어야 하므로, 반복 등장하는 교정은
        등장 수만큼 행이 된다. 각 행의 판정은 세 출처를 합쳐 만든다:
          · 검수 패널의 등장별 결정(self.occ_rows)     → 수락/거절, 등장 아님(excluded)
          · 브리지 apply의 등장별 실적용(d["occ_pages"]) → 실제로 치환됐는지 + 쪽 번호
          · apply 전에 돈 찾기(pages_by_original)        → 미적용 항목의 쪽 번호

        ⚠ 세 좌표계가 같은 등장 인덱스를 쓴다는 것이 이 함수의 전제다(문서 등장 순 =
          브리지 RepeatFind 순 = skip_occurrences 좌표계 — 이 저장소가 부분 거절에서
          이미 기대고 있는 불변식과 동일하다).

        ⚠ `excluded`(등장이 아닌 부분문자열 자리)와 `shadowed`(더 긴 교정이 대표하는
          자리)는 행으로 만들지 않는다 — 전자는 사용자가 결정한 적 없는 항목이고,
          후자는 그 긴 교정의 행과 같은 자리를 두 번 적는 것이 된다.
        """
        detail_by_key = {}
        for d in detail:
            detail_by_key.setdefault(
                (d.get("original", ""), d.get("corrected", "")), d)

        by_ci = {}
        for r in (self.occ_rows or self._fallback_occ_rows()):
            by_ci.setdefault(r["ci"], []).append(r)

        rows = []
        for ci, c in enumerate(self.corrections):
            orig      = c.get("original", "")
            corr      = c.get("corrected", "")
            flag_only = (c.get("source") == "dict_flag" and corr == orig)
            accepted  = c.get("status") == "accepted"
            d         = detail_by_key.get((orig, corr))
            occ_pages = (d or {}).get("occ_pages") or []
            page_by_i = {e.get("i"): e.get("page") for e in occ_pages}
            replaced_i = {e.get("i") for e in occ_pages if e.get("ok")}
            located   = pages_by_original.get(orig) or []
            err       = (d or {}).get("error", "") or ""

            occs = by_ci.get(ci)
            if not occs:
                # 검수 패널이 이 교정의 등장을 만들지 않은 경우. **전자동 모드**가
                #   그렇다 — `_build_occurrences`가 거절 교정과 검수 플래그를 아예
                #   건너뛴다(화면에 카드를 그릴 일이 없으므로). 그래도 정오표에는
                #   등장마다 한 줄이 있어야 하므로 찾기로 확인한 등장 수를 쓴다.
                n = max(1, len(located))
                occs = [{"occ": k, "status": c.get("status", "pending"),
                         "excluded": False, "shadowed": False} for k in range(n)]

            for r in occs:
                if r.get("excluded") or r.get("shadowed"):
                    continue
                k = r.get("occ", 0)
                # 쪽은 **치환 전 스냅샷**이 정본이다 — apply 중 기록된 쪽은 치환이
                #   진행되며 밀린 값이라 폴백으로만 쓴다(위 '쪽 번호 수집' 주석).
                page = located[k] if k < len(located) else None
                if page is None:
                    page = page_by_i.get(k)

                note = ""
                if flag_only and accepted:
                    outcome = "review"
                elif r.get("status") != "accepted":
                    outcome = "rejected"
                elif self.errata_only:
                    # 수락했지만 문서에는 넣지 않았다 — 실패가 아니라 **사람이 반영할
                    #   지시**다. 이걸 'applied'로 적으면 '이미 고쳐졌다'는 거짓말이 되고
                    #   'failed'로 적으면 도구가 실패한 것처럼 읽힌다. 그래서 별도 값.
                    outcome = "todo"
                elif d is None:
                    outcome, note = "failed", "적용 목록에서 누락됨 (수동 확인 필요)"
                elif occ_pages:
                    # 브리지가 등장별로 기록을 남겼다 = 가장 정확한 판정.
                    outcome = "applied" if k in replaced_i else "failed"
                    if outcome == "failed":
                        note = err or ("이 등장을 본문에서 찾지 못해 치환되지 않음 "
                                       "(수동 확인 필요)")
                elif d.get("applied") or d.get("consumed"):
                    # 등장별 근거가 없는 경로(AllReplace 폴백·긴 교정에 포함 처리).
                    outcome = "applied"
                    note = err
                else:
                    outcome, note = "failed", err

                rows.append({
                    "page":      page,
                    "original":  orig,
                    "corrected": corr,
                    "reason":    c.get("reason", ""),
                    "category":  c.get("category", ""),
                    "source":    c.get("source", "dict"),
                    "color":     c.get("color", 0),
                    "outcome":   outcome,
                    "note":      note,
                })
        return rows

    def _fallback_occ_rows(self) -> list:
        """검수 패널의 등장 정보가 없을 때(구버전 호출) 교정 단위로 최소 복원.

        `occurrences`는 shadowed를 세지 않아 실제 등장 수보다 작을 수 있으므로
        `skip_occurrences`의 최댓값으로 보정한다 — 정확하진 않지만, 행이 통째로
        사라지는 것보다 낫다.
        """
        rows = []
        for ci, c in enumerate(self.corrections):
            skip = set(c.get("skip_occurrences") or [])
            n = c.get("occurrences")
            n = n if isinstance(n, int) and n > 0 else 1
            if skip:
                n = max(n, max(skip) + 1)
            acc = c.get("status") == "accepted"
            for k in range(n):
                rows.append({
                    "ci": ci, "occ": k,
                    "status": "accepted" if (acc and k not in skip) else "rejected",
                    "excluded": False, "shadowed": False,
                })
        return rows
