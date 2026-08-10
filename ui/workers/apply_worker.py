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
                 occ_rows: list = None, known_pages: dict = None, parent=None):
        super().__init__(parent)
        self.file_path   = file_path
        self.corrections = corrections
        self.options     = options
        # 검수 패널의 등장(occurrence)별 결정 — 정오표를 '등장 1곳 = 1행'으로 쓰기 위한
        #   유일한 진실 원천이다(ReviewPanel.get_occurrence_rows 주석 참조).
        #   None이면 교정 단위 정보만으로 재구성한다(구버전 호출 폴백).
        self.occ_rows    = occ_rows
        # ★이미 떠 둔 쪽 번호({원문: [쪽, …]}) — **추가 산출물** 실행 전용.
        #   같은 원본 파일의 **같은 장면**에서 뜬 값이므로 다시 뜰 이유가 없다. 재수집은
        #   등장당 9ms(실측)의 순수 낭비이고, 더 나쁘게는 1차 실행의 정오표와 추가
        #   산출물의 정오표가 서로 다른 쪽을 적을 여지를 만든다(원본은 무변경이라
        #   달라질 리 없지만, 두 번 재는 순간 '다를 수 있는 값'이 된다).
        self.known_pages = known_pages or {}
        # 등장 좌표계 보정표 — `_plan_occurrences`가 채운다(교정본 모드에서만).
        self._occ_plan = {}
        # ★결과물 축 — "hwp" | "errata" | "memo" | "pdf" (setup_panel `_output_mode`).
        #   ⚠ 구버전 옵션(`errata_only` bool)만 오는 호출도 살려 둔다 — 그 값은 "한글
        #     파일을 고치지 않는다"만 뜻하므로 'errata'로 해석한다.
        self.output_mode = self._resolve_mode(options)
        # 파생값 — 한글 파일 무수정 여부. 새 분기는 `output_mode`를 볼 것.
        self.errata_only = self.output_mode != "hwp"
        self._stop       = threading.Event()

    @staticmethod
    def _resolve_mode(options: dict) -> str:
        mode = (options or {}).get("output_mode")
        if mode in ("hwp", "errata", "memo", "pdf"):
            return mode
        return "errata" if (options or {}).get("errata_only") else "hwp"

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

        # ★결과물 축 — 'hwp'가 아니면 한글 파일을 **열되 고치지 않는다**.
        #   사용자가 설정에서 고르는 값이다(setup_panel `_output_mode` 주석 참조).
        #   ⚠ 과거엔 '한글 무수정' 상태가 '수락한 치환이 0건'일 때 우연히 도달했고,
        #     그래서 의도한 것인지 사고인지 구분할 수 없었다. 이제 둘은 다르다:
        #       output_mode != "hwp" → 사용자가 고른 산출물. 수락 교정은 '반영 필요'로 기록.
        #       real_corrections=0   → 그냥 고칠 게 없는 실행.
        mode = self.output_mode
        errata_only = self.errata_only
        # 실제로 '치환'을 보낼 목록. 교정본 모드가 아니면 비운다 — `real_corrections`
        #   자체는 '수락한 실제 교정'이라는 뜻 그대로 두어야 집계·로그가 진실을 말한다.
        apply_targets = real_corrections if mode == "hwp" else []
        # 메모·PDF 주석의 대상 — 치환은 안 하지만 '어디에 무엇을 적을지'는 같은 목록이다.
        #   ⚠ 검수 플래그(dict_flag)도 주석 대상에 넣는다. 고칠 말은 없어도 "여기 확인
        #     하세요"가 편집자에게 필요한 정보이고, 정오표에는 이미 '확인 필요'로 실린다.
        annot_targets = (real_corrections + flag_accepted) if mode in ("memo", "pdf") else []

        # ★등장 좌표계 보정 — **교정본 모드에서만**(아래 `_plan_occurrences` 주석).
        self._occ_plan = self._plan_occurrences(apply_targets) if mode == "hwp" else {}

        correction_objs = [
            Correction(
                original  = c["original"],
                corrected = c["corrected"],
                reason    = c.get("reason", ""),
                source    = c.get("source", "dict"),
                color     = c.get("color", HL_DICT),
                skip_occurrences = self._skip_for(c),
            )
            for c in apply_targets
        ]

        stats, detail = {}, []
        out_hwp = ""
        out_pdf = ""
        pdf_report = {}
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

        # 추가 산출물 실행 — 1차 실행이 이미 뜬 쪽 번호를 그대로 쓴다(위 known_pages
        #   주석). 그러면 '정오표만' 추가는 한/글을 **아예 열지 않고** 끝난다.
        if want_pages and self.known_pages:
            pages_by_original = dict(self.known_pages)
            log(f"  [정오표] 쪽 번호 재사용 {len(pages_by_original)}건 "
                "· 원본이 그대로라 다시 찾지 않음")

        # ⚠ 검수 전용 실행(치환 0건)에서도 쪽 번호를 위해 HWP를 연다 — 그러지 않으면
        #   순수 검수 정오표만 쪽 칸이 통째로 비어 '기능이 고장난 것'처럼 보인다.
        #   메모·PDF 모드는 치환이 없어도 **항상** 열어야 한다(메모를 달고 PDF를 뽑는
        #   주체가 한/글이다).
        need_pages_scan = bool(want_pages and locate_targets and not pages_by_original)
        need_editor = (bool(apply_targets) or bool(annot_targets)
                       or mode == "pdf" or need_pages_scan)

        if need_editor:
            self.progress.emit(10, "HWP 파일 열기 중…")
            editor = None
            try:
                editor = HwpEditor(self.file_path, logger=log)
                editor.open()
                log(f"  적용 대상 파일: {os.path.basename(self.file_path)}")

                # 쪽 번호 — 치환 **전** 문서에서 한 번에 뜬다(치환 없음·문서 무변경)
                if need_pages_scan and not self._stop.is_set():
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

                elif mode == "memo" and annot_targets and not self._stop.is_set():
                    stats, detail, out_hwp = self._run_memo(
                        editor, annot_targets, log, pages_by_original)
                    if self._stop.is_set():
                        return

                elif mode == "pdf" and not self._stop.is_set():
                    out_pdf = self._run_pdf_export(editor, log)

                else:
                    # 치환은 없다. 문서를 연 것은 쪽 번호를 읽기 위해서일 뿐이다.
                    log(self._no_apply_log(mode, real_corrections, flag_accepted))
                    self.progress.emit(85, "정오표 정리 중…")
            finally:
                if editor is not None:
                    try:
                        editor.close()
                    except Exception:
                        pass
        else:
            # 적용할 치환도, 쪽 번호를 찾을 원문도 없다 — HWP를 아예 열지 않는다.
            log(self._no_apply_log(mode, real_corrections, flag_accepted))
            self.progress.emit(85, "정오표 정리 중…")

        # ── PDF 주석 — 한/글을 닫은 **뒤에** 판다 ────────────────────────────
        #   PDF 파일이 완전히 닫힌 상태에서 열어야 하고, 이 단계는 순수 파이썬이라
        #   COM 세션을 붙들고 있을 이유가 없다.
        if mode == "pdf" and out_pdf and not self._stop.is_set():
            pdf_report = self._run_pdf_annotate(out_pdf, annot_targets, log,
                                                pages_by_original)

        # 정오표 머리말에 적을 교정 범위 — 윤문 옵션이 켜져있으면 polish, 아니면 typo.
        #   ⚠ 이름이 `mode`였는데 결과물 축(`mode`)과 겹쳐 조용히 덮어쓰고 있었다.
        #     둘은 완전히 다른 축이므로 이름을 갈라 둔다.
        errata_mode = "polish" if self.options.get("scope_polish") else "typo"
        # ⚠ 정오표 생성은 S3(포함 처리)/S4(부분 반영) 재분류 **이후**에 수행한다 —
        #   실제 적용 결과(applied/error/consumed/partial)를 정오표에 반영하기 위함.

        # ⚠ S3·S4는 **치환한 모드에서만** 의미가 있다. 메모·PDF는 글자를 바꾸지 않아
        #   '긴 교정에 먹힘'도 '부분 반영'도 성립하지 않는다 — 그 판정을 돌리면 메모를
        #   못 단 자리(머리말 등)가 '부분 반영 실패'로 둔갑해 거짓 경고가 된다.
        consumed_cnt, partial_cnt, partial_samples = (
            self._reclassify(detail, real_corrections, log) if mode == "hwp"
            else (0, 0, []))

        # 정오표 데이터 — 검수 패널의 '등장별 결정'과 브리지의 '등장별 실제 적용 결과'를
        #   병합한다. 과거엔 수락 여부만으로 applied를 채워, 문서 반영에 실패한 항목도
        #   '✔ 적용'으로, 사용자가 거절한 항목은 '✖ 실패'로 잘못 기록됐다(정합성 버그).
        #   gen_errata가 꺼져 있어도 만들어 결과에 동봉한다 — 완료 화면의 '정오표 생성'
        #   수동 버튼이 같은 데이터로 진실된 정오표를 만들 수 있게(main_window가 사용).
        errata_rows = self._build_errata_rows(
            detail, pages_by_original,
            pdf_pages=(pdf_report or {}).get("pages_by_original") or {},
            pdf_marked=(pdf_report or {}).get("marked_occ") or {})

        errata_path = None
        if self.options.get("gen_errata", True) and errata_rows:
            try:
                self.progress.emit(90, "정오표 생성 중…")
                from output.errata_generator import generate_errata
                errata_path = generate_errata(
                    rows     = errata_rows,
                    hwp_path = self.file_path,
                    # ⚠ 추가 산출물 실행은 **기존 정오표를 덮어쓰지 않는다** — 호출부가
                    #   모드 이름을 붙인 경로를 지정한다(main_window._extra_errata_path).
                    #   None이면 종전대로 `{원본}_정오표.xlsx`.
                    output_path = self.options.get("errata_output_path") or None,
                    options  = {
                        "used_ai":         self.options.get("use_ai", True),
                        "mode":            errata_mode,
                        # 사전 재검증·가드는 항상 동작 → 정오표에도 항상 표기
                        "used_dict":       True,
                        "deep_screening":  self.options.get("deep_screening", False),
                        "output_mode":     mode,
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

        # ── 모드별 산출 수치 ────────────────────────────────────────────────
        #   ⚠ `applied`는 **한/글 본문 글자를 실제로 바꾼 건수**라는 뜻을 넓히지 않는다.
        #     완료 화면이 그 값으로 '적용 N건'을 그리므로, 메모·주석 수를 여기 담으면
        #     고치지도 않은 문서를 고쳤다고 보고하게 된다. 각자 자기 칸에 싣는다.
        n_memo     = sum(d.get("memoed", 0) for d in detail) if mode == "memo" else 0
        n_blocked  = sum(d.get("blocked", 0) for d in detail) if mode == "memo" else 0
        n_annot    = int((pdf_report or {}).get("annotated") or 0)
        # ★수락했는데 **표시하지 못한 자리** — 교정본의 '부분 반영'에 해당하는 수치다.
        #   메모 불가(머리말)·PDF 미탐·문서에서 못 찾은 등장을 한 숫자로 모은다.
        #   이게 없으면 세 산출물의 표시 자리 수 차이가 화면 어디에도 안 나온다.
        n_unmarked = (sum(1 for r in errata_rows
                          if r["outcome"] == "todo" and r["note"])
                      if mode in ("memo", "pdf") else 0)
        if n_unmarked:
            log(f"  [{'메모' if mode == 'memo' else 'PDF'}] 표시하지 못한 자리 "
                f"{n_unmarked}곳 · 정오표에 사유 기록")

        self.finished.emit({
            "applied":      ok_cnt if mode == "hwp" else 0,
            "occurrences":  (sum(d.get("replaced", 0) for d in detail if d.get("applied"))
                             if mode == "hwp" else 0),
            "failed":       fail_cnt,
            "consumed":     consumed_cnt,
            "partial":      partial_cnt,
            "flagged":      len(flag_accepted),   # 기록된 검수(치환 없음) 항목 수
            # ── 한글 미반영 결과물 전용 수치 ─────────────────────────────
            "output_mode":  mode,
            "errata_only":  errata_only,          # 파생값(하위 호환)
            "to_apply":     len(real_corrections) if errata_only else 0,   # 항목(건)
            "to_apply_occ": (sum(1 for r in errata_rows if r["outcome"] == "todo")
                             if errata_only else 0),                        # 등장(곳)
            "memoed":       n_memo,               # 메모를 단 자리(곳)
            "memo_blocked": n_blocked,            # 머리말 등 메모 불가 자리(곳)
            "annotated":    n_annot,              # PDF 주석을 단 자리(곳)
            "unmarked":     n_unmarked,           # 수락했으나 표시 못 한 자리(곳)
            "pdf_path":     out_pdf,
            "pdf_missing":  len((pdf_report or {}).get("missing") or []),
            "hwp_path":     out_hwp,
            "errata_path":  errata_path or "",
            "fail_samples": fail_samples,
            # 완료 화면의 '정오표 생성' 수동 버튼용 — 실제 적용 결과·쪽 번호가 병합된
            #   **등장 단위** 행 데이터(정오표 한 줄 = 이 목록 한 항목).
            "errata_rows": errata_rows,
            # 완료 화면의 '추가 산출물'용 — 이 실행에서 뜬 쪽 번호 스냅숏.
            #   다음 실행에 known_pages로 되돌려 주면 한/글을 다시 뒤지지 않는다.
            "pages_by_original": pages_by_original,
        })

    # ══════════════════════════════════════════════════════
    # ▌등장 좌표계 보정 (교정본 모드 전용)
    # ══════════════════════════════════════════════════════
    def _ci_of(self) -> dict:
        """교정 dict → `self.corrections` 안의 인덱스. occ_rows가 ci로 말하기 때문.

        ⚠ 한 번만 만들어 재사용한다 — 교정 항목마다 새로 만들면 항목 수의 제곱이 된다.
        """
        cache = getattr(self, "_ci_cache", None)
        if cache is None:
            cache = {id(c): i for i, c in enumerate(self.corrections)}
            self._ci_cache = cache
        return cache

    def _plan_occurrences(self, apply_targets: list) -> dict:
        """치환하면 **사라지는 등장**을 걷어내고, 남은 것을 다시 번호 매긴다.

        ★풀려는 문제(실측 2026-08-08 · 사용자 보고 "교정본에만 '해야한다' 미반영"):
          apply는 **긴 원문 우선**으로 치환한다. 그래서 '고려해야한다'가 먼저 바뀌면
          그 안에 있던 '해야한다' 5곳이 문서에서 **사라진다**. 그런데
          `skip_occurrences`는 **치환 전** 좌표계(0..13)의 인덱스라, 남은 9곳(0..8)에
          그대로 들이대면 5·8번이 애먼 자리를 가리켜 **멀쩡한 2곳을 건너뛴다**.
          실측: 현행 치환 [0,1,2,3,4,6,7] → 문서에 '해야한다' 2곳 잔존 /
                보정 후 [0..8] 전부 → 0곳 잔존.

        ⚠ `skip_occurrences`를 그냥 비우면 안 된다 — 그 안에는 성격이 다른 셋이 섞여 있다:
          ① 사용자가 거절한 자리 — 치환 후에도 **그대로 있다**. 계속 건너뛰어야 한다
             (지우면 **거절한 자리가 치환되는** 더 나쁜 사고).
          ② `excluded`(긴 낱말 속 조각) — 그 긴 낱말이 교정 대상이 아니면 그대로 있다.
          ③ 더 긴 **적용 교정**에 먹히는 자리 — 이것만 사라진다.
          셋을 가르는 유일한 근거가 **구간 겹침**이라, 검수 패널이 실어 보내는
          `pos`/`end`로 판정한다.

        ⚠ **메모·PDF에는 적용하지 않는다.** 그 두 모드는 문서 글자를 바꾸지 않으므로
          등장이 사라지지 않는다 — 보정하면 오히려 좌표계가 어긋난다.

        반환: {ci: {"skip": [보정된 인덱스…], "survivors": [원래 등장 번호…]}}
              (occ_rows가 없거나 위치를 모르면 그 교정은 아예 담지 않는다 = 종전 동작)
        """
        rows = self.occ_rows or []
        if not rows or not apply_targets:
            return {}
        ci_of = self._ci_of()
        apply_ci = {ci_of.get(id(c)) for c in apply_targets}
        apply_ci.discard(None)

        by_ci = {}
        for r in rows:
            by_ci.setdefault(r["ci"], []).append(r)
        for v in by_ci.values():
            v.sort(key=lambda r: r.get("occ", 0))

        # 실제로 치환될 구간 — 수락됐고, 자기가 가려진 쪽이 아니며, 위치를 아는 등장.
        #   (가려진 등장은 그 자리를 긴 교정이 대신 치환하므로 스스로 구간을 내지 않는다.)
        winners = []          # (ci, start, end)
        for ci in apply_ci:
            for r in by_ci.get(ci, []):
                if r.get("status") != "accepted" or r.get("excluded") \
                        or r.get("shadowed"):
                    continue
                s, e = r.get("pos"), r.get("end")
                if isinstance(s, int) and isinstance(e, int) and e > s:
                    winners.append((ci, s, e))

        plan = {}
        for ci in apply_ci:
            occs = by_ci.get(ci) or []
            if not occs:
                continue
            survivors, vanished = [], 0
            for r in occs:
                s, e = r.get("pos"), r.get("end")
                gone = False
                if isinstance(s, int) and isinstance(e, int):
                    # 다른 교정의 치환 구간이 이 등장을 **통째로 품으면** 사라진다.
                    gone = any(wci != ci and ws <= s and e <= we
                               for wci, ws, we in winners)
                if gone:
                    vanished += 1
                    continue
                survivors.append(r)
            if not vanished:
                continue          # 밀림 없음 — 종전 그대로 두는 것이 가장 안전하다
            plan[ci] = {
                "skip": [j for j, r in enumerate(survivors)
                         if r.get("status") != "accepted"],
                "survivors": [r.get("occ", j) for j, r in enumerate(survivors)],
            }
        return plan

    def _skip_for(self, c: dict) -> list:
        """이 교정에 실제로 보낼 skip 인덱스 — 보정본이 있으면 그것, 없으면 원본."""
        plan = getattr(self, "_occ_plan", None) or {}
        ci = self._ci_of().get(id(c))
        if ci is not None and ci in plan:
            return list(plan[ci]["skip"])
        return list(c.get("skip_occurrences") or [])

    # ══════════════════════════════════════════════════════
    # ▌S3·S4 — 치환 결과 재분류 (교정본 모드 전용)
    # ══════════════════════════════════════════════════════
    def _reclassify(self, detail: list, real_corrections: list, log) -> tuple:
        """실패 항목 재분류(S3) + 부분 반영 감지(S4). 반환 (consumed, partial, samples).

        ⚠ **치환한 모드에서만** 의미가 있다. 메모·PDF는 글자를 바꾸지 않아 '긴 교정에
          먹힘'도 '부분 반영'도 성립하지 않고, 그 판정을 돌리면 메모를 못 단 자리
          (머리말 등)가 '부분 반영 실패'로 둔갑해 거짓 경고가 된다.
        """
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
        # ⚠ 기대 치환 수는 **보정 후 좌표계**로 센다 — 긴 교정에 먹혀 사라진 등장까지
        #   기대치에 넣으면 정상 실행이 '부분 반영'으로 오탐된다(`_plan_occurrences`).
        plan = getattr(self, "_occ_plan", None) or {}
        ci_of = self._ci_of()
        expected_by_key = {}
        for c in real_corrections:
            ci = ci_of.get(id(c))
            if ci is not None and ci in plan:
                occ_n = len(plan[ci]["survivors"])
                skip_n = len(plan[ci]["skip"])
            else:
                occ_n = c.get("occurrences")
                if not isinstance(occ_n, int) or occ_n <= 0:
                    continue
                skip_n = len(c.get("skip_occurrences") or [])
            exp = occ_n - skip_n
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

        return consumed_cnt, partial_cnt, partial_samples

    # ══════════════════════════════════════════════════════
    # ▌결과물 모드별 실행
    # ══════════════════════════════════════════════════════
    def _memo_text(self, c: dict) -> str:
        """메모 한 장에 들어갈 본문 — 설계 문서 §3.1의 서식.

        ⚠ 화면 로그 규약(개별 예시 금지)은 여기 적용되지 않는다 — 이건 로그가 아니라
          **산출물**이고, 편집자가 그 자리에서 무엇을 무엇으로 고칠지 읽어야 한다.
        """
        orig = (c.get("original") or "").strip()
        corr = (c.get("corrected") or "").strip()
        head = f"‘{orig}’ → ‘{corr}’" if corr and corr != orig else f"‘{orig}’ 확인 필요"
        cat    = (c.get("category") or "").strip()
        reason = " ".join((c.get("reason") or "").split())[:300]
        if cat and reason:
            second = f"[{cat}] {reason}"
        else:
            second = f"[{cat}]" if cat else reason
        return f"{head}\n{second}" if second else head

    def _run_memo(self, editor, targets: list, log,
                  pages_by_original: dict = None) -> tuple:
        """메모 모드 — 원고 글자를 바꾸지 않고 각 자리에 한/글 메모를 단다.

        ★`pages_by_original`(치환 전 문서에서 실제로 찾은 등장)을 함께 넘긴다 —
          브리지가 등장 상한을 그 수로 잡아 교정본·PDF와 **같은 자리 집합**을 돈다
          (hwp_bridge_worker.memo의 occ_cap 주석).

        ⚠ hwpx direct 백엔드에는 `insert_memos`가 없다(XML만 만지는 백엔드라 메모
          컨트롤을 만들 수 없다). 조용히 넘어가면 '메모본이라는데 메모가 없는 파일'이
          나오므로 명시적으로 알리고 정오표만 남긴다.
        """
        inserter = getattr(editor, "insert_memos", None)
        if inserter is None:
            log("  [메모] 이 백엔드는 메모를 달 수 없습니다 — 정오표만 생성합니다")
            return {}, [], ""

        # ⚠ `color`를 넘기지 않는다 — 메모 모드는 앵커 글자색도 바꾸지 않는다
        #   (사용자 지정 2026-08-07). 원고는 글자도 서식도 그대로다.
        located = pages_by_original or {}
        memo_items = [{
            "original":  c["original"],
            "corrected": c.get("corrected", ""),
            "reason":    c.get("reason", ""),
            "source":    c.get("source", "dict"),
            "skip_occurrences": c.get("skip_occurrences", []),
            "memo_text": self._memo_text(c),
            "occ_total": int(c.get("occurrences") or 0),
            "doc_occ":   len(located.get(c["original"]) or ()),
        } for c in targets]

        def progress_cb(current, total):
            if total > 0:
                pct = 15 + int((current / total) * 65)
                self.progress.emit(min(pct, 80), f"메모 다는 중… {current}/{total}")

        stats, detail = inserter(memo_items, progress_cb=progress_cb,
                                 stop_event=self._stop)

        if self._stop.is_set():
            log("  ⚠ 취소 — 메모본을 저장하지 않았습니다 (원본 파일 무변경).")
            self.error.emit("사용자에 의해 취소되었습니다.\n"
                            "메모본은 저장되지 않았고 원본 파일은 변경되지 않았습니다.")
            return stats, detail, ""

        n_memo    = sum(d.get("memoed", 0) for d in detail)
        n_blocked = sum(d.get("blocked", 0) for d in detail)
        log(f"  [메모] 메모 {n_memo}곳 · 본문 글자 변경 없음")
        if n_blocked:
            # ⚠ 조용히 사라지면 안 된다 — 머리말·꼬리말 스토리는 한/글이 메모를 거부한다.
            log(f"  [메모] 메모 불가 위치 {n_blocked}곳 — 머리말·꼬리말 등 "
                f"(정오표에 기록)")

        base, ext = os.path.splitext(self.file_path)
        out_hwp = base + "_메모본" + ext
        log(f"  저장 중: {os.path.basename(out_hwp)}")
        editor.save_as(out_hwp)
        log("  ✓ 저장 완료")
        self.progress.emit(85, "메모본 저장 완료")
        return stats, detail, out_hwp

    def _run_pdf_export(self, editor, log) -> str:
        """PDF 모드 1단계 — 원본을 PDF로 뽑는다(한/글 문서 무변경).

        ⚠ hwpx direct 백엔드에는 `export_pdf`가 없다(레이아웃 엔진이 없어 PDF를 만들 수
          없다). 그때는 빈 문자열을 돌려 정오표만 남긴다.
        """
        exporter = getattr(editor, "export_pdf", None)
        if exporter is None:
            log("  [PDF] 이 백엔드는 PDF 변환을 지원하지 않습니다 — 정오표만 생성합니다")
            return ""
        base, _ext = os.path.splitext(self.file_path)
        out_pdf = base + "_주석.pdf"
        self.progress.emit(40, "PDF 변환 중…")
        log("  [PDF] 한/글에서 PDF 변환 중…")
        try:
            path = exporter(out_pdf)
        except Exception as exc:
            log(f"  [PDF] 변환 실패: {exc} — 정오표만 생성합니다")
            self.error.emit(f"PDF 변환 실패 (정오표는 생성됩니다): {exc}")
            return ""
        try:
            size_mb = os.path.getsize(path) / 1_048_576
            log(f"  [PDF] 변환 완료 {size_mb:.1f} MB")
        except OSError:
            pass
        self.progress.emit(60, "PDF 주석 준비 중…")
        return path

    def _run_pdf_annotate(self, pdf_path: str, targets: list, log,
                          pages_by_original: dict = None) -> dict:
        """PDF 모드 2단계 — 뽑아 둔 PDF 위에만 주석을 얹는다.

        ★`pages_by_original`(치환 전 문서에서 실제로 찾은 등장)을 함께 넘긴다 —
          PDF 매치 수를 무엇과 대조할지의 기준이다. 추출 텍스트로 센 `occ_total`은
          문서 찾기 결과와 다를 수 있고, `skip_occurrences`가 사는 좌표계는
          **문서 찾기 쪽**이다(pdf_annotator._align_occurrences 주석).
        """
        try:
            from output import pdf_annotator
        except Exception as exc:
            log(f"  [PDF] 주석 모듈을 불러오지 못했습니다: {exc}")
            return {}
        if not pdf_annotator.available():
            log("  [PDF] 주석 라이브러리가 없어 주석을 달지 못했습니다 "
                "(PDF 변환본은 그대로 남습니다)")
            self.error.emit(
                "PDF 주석 라이브러리를 불러오지 못해 주석 없이 변환본만 저장했습니다.\n"
                f"({pdf_annotator.unavailable_reason()})")
            return {}

        located = pages_by_original or {}
        items = [{
            "original":  c["original"],
            "corrected": c.get("corrected", ""),
            "reason":    c.get("reason", ""),
            "category":  c.get("category", ""),
            "occ_total": int(c.get("occurrences") or 0),
            "doc_occ":   len(located.get(c["original"]) or ()),
            "skip_occurrences": c.get("skip_occurrences", []),
        } for c in targets]

        self.progress.emit(70, "PDF 주석 다는 중…")
        try:
            rep = pdf_annotator.annotate(pdf_path, items, logger=log)
        except Exception as exc:
            log(f"  [PDF] 주석 실패: {exc} (변환본은 그대로 남습니다)")
            self.error.emit(f"PDF 주석 실패 (변환본은 저장됨): {exc}")
            return {}

        log(f"  [PDF] 주석 {rep.get('annotated', 0)}곳 · 항목 {rep.get('items', 0)}건")
        n_miss = len(rep.get("missing") or [])
        if n_miss:
            # ⚠ 조용한 드롭 금지 — PDF에서 못 찾은 원문은 반드시 드러낸다.
            log(f"  [PDF] PDF에서 찾지 못한 원문 {n_miss}건 — 정오표로 확인 필요")
        n_warn = len(rep.get("warnings") or [])
        if n_warn:
            log(f"  [PDF] 위치 자동 대조 실패 {n_warn}건 — 주석에 경고 표시")
        self.progress.emit(85, "PDF 주석 완료")
        return rep

    @staticmethod
    def _no_apply_log(mode: str, real_corrections: list, flag_accepted: list) -> str:
        """치환 없이 끝나는 경우의 화면 로그 — **사유가 다르므로 문구도 다르다**.

        ⚠ 화면 로그 규약: `[태그]` + `n건`, 개별 예시 금지(activity_panel 참조).
        """
        tag = {"errata": "정오표만", "memo": "메모", "pdf": "PDF 주석"}.get(mode, "정오표만")
        if mode != "hwp":
            return (f"  [{tag}] 한글 파일을 수정하지 않습니다 "
                    f"· 반영 필요 {len(real_corrections)}건 · 검수 {len(flag_accepted)}건")
        return (f"  [{tag}] 적용할 교정이 없습니다 "
                f"· 검수 {len(flag_accepted)}건 기록")

    # ══════════════════════════════════════════════════════
    # ▌정오표 행(= 등장 1곳) 구성
    # ══════════════════════════════════════════════════════
    def _build_errata_rows(self, detail: list, pages_by_original: dict,
                           pdf_pages: dict = None, pdf_marked: dict = None) -> list:
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
        pdf_pages = pdf_pages or {}
        pdf_marked = {k: set(v) for k, v in (pdf_marked or {}).items()}
        mode = self.output_mode

        detail_by_key = {}
        for d in detail:
            detail_by_key.setdefault(
                (d.get("original", ""), d.get("corrected", "")), d)

        by_ci = {}
        for r in (self.occ_rows or self._fallback_occ_rows()):
            by_ci.setdefault(r["ci"], []).append(r)

        occ_plan = getattr(self, "_occ_plan", None) or {}

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
            # ★브리지가 말하는 등장 번호는 **보정 후 좌표계**다(교정본 모드에서 긴 교정에
            #   먹혀 사라진 등장이 빠진 순서). 검수 패널의 번호(k)로 조회하려면 되돌려야
            #   한다 — 안 하면 정오표가 엉뚱한 등장을 '실패'로 적는다.
            #   `pages_by_original`(치환 전 스냅숏)은 패널 좌표계 그대로이므로 k를 쓴다.
            surv = (occ_plan.get(ci) or {}).get("survivors")
            bridge_of = {k: j for j, k in enumerate(surv)} if surv else None

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
                # 브리지 좌표계로의 번역(보정이 없으면 k 그대로). None이면 이 등장은
                #   긴 교정에 먹혀 치환 시점에 존재하지 않았다는 뜻이다.
                bk = bridge_of.get(k) if bridge_of is not None else k
                # 쪽은 **치환 전 스냅샷**이 정본이다 — apply 중 기록된 쪽은 치환이
                #   진행되며 밀린 값이라 폴백으로만 쓴다(위 '쪽 번호 수집' 주석).
                #   ⚠ located는 패널 좌표계라 k로 찾고, page_by_i는 브리지 좌표계라 bk로.
                page = located[k] if k < len(located) else None
                if page is None and bk is not None:
                    page = page_by_i.get(bk)

                note = ""
                if flag_only and accepted:
                    outcome = "review"
                elif r.get("status") != "accepted":
                    outcome = "rejected"
                elif self.errata_only:
                    # 수락했지만 문서에는 넣지 않았다 — 실패가 아니라 **사람이 반영할
                    #   지시**다. 이걸 'applied'로 적으면 '이미 고쳐졌다'는 거짓말이 되고
                    #   'failed'로 적으면 도구가 실패한 것처럼 읽힌다. 그래서 별도 값.
                    #   메모·PDF 모드도 같다 — 표시만 했을 뿐 글자는 그대로다.
                    outcome = "todo"
                    # ⚠ 다만 **표시조차 못 한 자리**는 반드시 드러낸다. 메모는 머리말·
                    #   꼬리말 스토리에서 한/글이 거부하고, PDF는 원문을 못 찾을 수 있다.
                    #   조용히 'todo'로만 적으면 편집자는 메모가 달린 줄 알고 지나친다.
                    #
                    # ★교정본에는 S4 '부분 반영' 안전망이 있어 "수락 4곳 중 3곳만
                    #   치환"이 드러난다. 메모·PDF에는 그 판정을 돌리지 않으므로
                    #   (글자를 안 바꾸니 성립하지 않는다) **여기가 유일한 통로**다.
                    #   등장별 실적(memo=occ_pages의 ok / pdf=marked_occ)을 그대로 본다
                    #   — 안 그러면 세 산출물의 표시 자리 수가 말없이 갈린다
                    #   (사용자 보고 2026-08-08: "반영된 항목이 다 다른 것 같다").
                    if mode == "memo":
                        e = next((x for x in occ_pages if x.get("i") == k), None)
                        if e is not None and e.get("blocked"):
                            note = e.get("note") or "이 자리에는 메모를 달 수 없습니다"
                        elif e is not None and not e.get("ok"):
                            note = ("이 자리에 메모를 달지 못했습니다 (수동 확인 필요)")
                        elif e is None:
                            note = ("이 등장을 문서에서 찾지 못해 메모를 달지 못했습니다 "
                                    "(수동 확인 필요)")
                    elif mode == "pdf":
                        if orig not in pdf_pages:
                            note = "PDF에서 이 원문을 찾지 못해 주석을 달지 못했습니다"
                        elif k not in (pdf_marked.get(orig) or ()):
                            note = ("PDF에서 이 등장을 찾지 못해 주석을 달지 못했습니다 "
                                    "(수동 확인 필요)")
                elif d is None:
                    outcome, note = "failed", "적용 목록에서 누락됨 (수동 확인 필요)"
                elif bk is None:
                    # 치환 시점에 이 자리는 **더 긴 교정이 이미 바꿔 놓았다**. 실패가
                    #   아니라 정상이며, S3가 항목 단위로 세는 것과 같은 판정이다.
                    outcome = "applied"
                    note = "긴 교정 항목에 포함되어 함께 처리됨 (정상)"
                elif occ_pages:
                    # 브리지가 등장별로 기록을 남겼다 = 가장 정확한 판정.
                    outcome = "applied" if bk in replaced_i else "failed"
                    if outcome == "failed":
                        note = err or ("이 등장을 본문에서 찾지 못해 치환되지 않음 "
                                       "(수동 확인 필요)")
                elif d.get("applied") or d.get("consumed"):
                    # 등장별 근거가 없는 경로(AllReplace 폴백·긴 교정에 포함 처리).
                    outcome = "applied"
                    note = err
                else:
                    outcome, note = "failed", err

                # PDF 물리 쪽 — ⚠ 위 `page`(prnpageno)와 **다른 좌표계**다. 구역마다
                #   쪽 번호를 새로 시작할 수 있어 둘 사이에 고정 오프셋도 없다(실측:
                #   PageCount 17인데 문서 끝 prnpageno 18). 그래서 계산하지 않고
                #   PDF에서 직접 찾은 값을 **별도 칸**에 싣는다.
                plist = pdf_pages.get(orig) or []
                pdf_page = plist[k] if k < len(plist) else None

                rows.append({
                    "page":      page,
                    "pdf_page":  pdf_page,
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
