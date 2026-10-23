"""
eval/e2e_apply/run_e2e.py — 적용 경로 실기 E2E (실제 MainWindow + 실제 한/글 COM)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
골드셋(eval/ai_goldset)은 교정 **판단**만 본다 — 검수 결정이 교정본·메모·PDF·정오표에
제대로 실리는지(적용 경로)는 한 줄도 시험하지 않는다. 2026-10-17의 치명 결함(교정본≠검수
결정·메모 절반 유실)이 전부 그쪽에 있었고, 둘 다 앱은 '실패 0'이라고 보고했다. 그래서
화면 없이(offscreen) 앱을 그대로 구동해 사람이 하는 순서대로 누른다:

  ① 분석(AI 제외 — 결정론 계층만이라 런마다 같은 카드) → ② 검토: **진짜 `_set_status`**로
  4장마다 1장 거절(팝업만 '예'로 대체) → ③ 표기 일관성 단계 → ④ 교정본 적용 →
  ⑤ **교정본 재추출 대조** — 기대 문서(원문에서 '보이는 수락 등장'만 카드 값으로 바꾼 것)와
  한 글자라도 다르면 실패 → ⑥ 결과물 추가(빨간색+메모 · 메모본 · PDF) → ⑥.5 **다시 적용**
  (완료 뒤 레일 '검토' → 수락 3장 거절 → 다시 적용 → 덮어쓴 교정본을 새 결정의 기대 문서와
  대조 — 절대 판정) → ⑦ 테마 토글 → ⑧ 업데이트 대화상자(미루면 받은 파일 삭제 · 적용하면 보존).
  검토 저장본(core/review_session)은 작업 폴더 안에만 쓴다 — 개발자의 실제 저장본과 섞이지 않고
  '이어서 검토?' 안내가 뜨지 않는다.

판정:
  · 절대 실패 — 미처리 예외 · 시간 초과 · 교정본 대조 불일치 · 업데이트 대화상자 검사
  · 기준선 대비 실패 — failed·partial·unmarked·memo_blocked·pdf_missing 증가
  · 수치 변화 — 그 밖의 수치(항목·적용·메모·주석 수)가 기준선과 다르면 **종료 코드 2**.
    결정론 계층을 고친 릴리스라면 정상일 수 있다 — 확인하고 `--save-baseline`.

기준선은 **원고 옆** `<원고>.e2e.json`에 둔다(원고와 함께 레포 밖 — 고객 원고는 커밋 금지).

실행:
  .venv64/Scripts/python.exe eval/e2e_apply/run_e2e.py [원고.hwp …] [--save-baseline] [--keep]
  원고를 생략하면 환경변수 KS_E2E_HWP(';'로 여러 개), 그것도 없으면 `교정샘플/05.hwp`.
종료 코드: 0 통과 · 1 실패 · 2 통과했으나 기준선과 다름/기준선 없음 · 4 원고 없음(스킵)

⚠ 한/글이 설치된 개발 PC에서만 돈다(실제 COM 브리지). 원고는 사본으로 작업한다 —
  `copyfile`(새 수정 시각)을 쓸 것. `copy2`로 옛 시각을 물려받은 사본은 임시 폴더 정리에
  지워진 적이 있다.
⚠ '전부 수락'을 상태 직접 쓰기로 흉내 내지 말 것 — 진짜 `_set_status`만 패널 쪽 회귀
  (합성 갱신·이음매 그룹·반복 전파)를 드러낸다.
"""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_FIXTURE = os.path.join(REPO, "교정샘플", "05.hwp")

# 기준선과 비교해 **늘면 실패**인 수치(나쁜 쪽 카운터).
_WORSE_IF_UP = ("failed", "partial",
                "hwp_memo.unmarked", "hwp_memo.memo_blocked",
                "memo.unmarked", "memo.memo_blocked",
                "pdf.unmarked", "pdf.pdf_missing")


def _fixtures(args):
    if args:
        return args
    env = os.environ.get("KS_E2E_HWP", "").strip().strip('"')
    if env:
        return [p.strip().strip('"') for p in env.split(";") if p.strip()]
    return [DEFAULT_FIXTURE]


# ══════════════════════════════════════════════════════
# ▌원고 하나 — 자식 프로세스에서 돈다(MainWindow·QApplication을 원고마다 새로)
# ══════════════════════════════════════════════════════
def run_single(src: str, out_json: str, keep: bool) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["KS_SKIP_LOGIN"] = "1"
    sys.path.insert(0, REPO)
    os.chdir(REPO)

    m = {"timeouts": [], "exceptions": 0, "secs": {}}
    errors = []

    def hook(t, v, tb):
        errors.append("".join(traceback.format_exception(t, v, tb)))
        sys.__stderr__.write(errors[-1])
    sys.excepthook = hook

    work = tempfile.mkdtemp(prefix="ks-e2e-")
    try:
        _drive(src, work, m)
    except Exception:
        errors.append(traceback.format_exc())
        sys.__stderr__.write(errors[-1])
    finally:
        m["exceptions"] = len(errors)
        m["exception_heads"] = [e.strip().splitlines()[-1][:200] for e in errors]
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        if keep:
            print(f"  작업 폴더 보존: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def _drive(src: str, work: str, m: dict) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from ui.styles.fonts import load_fonts
    from ui.styles.theme import apply_theme
    load_fonts()
    apply_theme(app, "light")

    import ui.main_window as mw
    from ui.widgets import review_panel as rpmod
    from core import HwpEditor
    from core.models import occurrence_skipped

    # 네트워크로 나가는 시작 작업 차단(세션 복원·업데이트 확인·공유 뇌 동기화)
    mw.MainWindow._start_session_restore = lambda self: None
    mw.MainWindow._start_update_check = lambda self: None
    mw.MainWindow._start_sync = lambda self, mode: None
    # 검토 저장본(core/review_session)은 작업 폴더 안에만 — 개발자의 실제 저장본을 읽지도
    #   덮지도 않는다. 매 실행이 빈 폴더라 '이어서 검토?' 안내가 뜨지 않고 늘 새로 분석한다
    #   (같은 원고 사본을 매번 쓰므로, 공유 폴더면 두 번째 실행부터 안내 모달에서 멈춘다).
    from core import review_session
    _sess_dir = os.path.join(work, "_review_sessions")
    os.makedirs(_sess_dir, exist_ok=True)
    from pathlib import Path
    review_session.sessions_dir = lambda: Path(_sess_dir)
    mw.MainWindow._offer_last_session = lambda self: None
    # 모달 자동 응답 — 사람이 '예'를 누른 것과 같은 경로를 탄다
    rpmod.LightConfirmDialog.ask = classmethod(lambda cls, *a, **k: True)
    rpmod.ReviewPanel._warn_popup = lambda self, *a, **k: None
    rpmod.ReviewPanel._confirm_popup = lambda self, *a, **k: True
    mw.MainWindow._warn_popup = lambda self, *a, **k: None
    for name in ("warning", "information", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    def pump(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents(QEventLoop.AllEvents, 50)
            time.sleep(0.01)

    def wait(pred, timeout, label):
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.processEvents(QEventLoop.AllEvents, 100)
            if pred():
                m["secs"][label] = round(time.time() - t0, 1)
                return True
            time.sleep(0.02)
        m["timeouts"].append(label)
        print(f"  ✗ 시간 초과: {label} ({timeout}s)", flush=True)
        return False

    path = os.path.join(work, os.path.basename(src))
    shutil.copyfile(src, path)              # ⚠ copy2 금지(모듈 docstring)

    w = mw.MainWindow()
    w.show()
    pump(0.5)
    w._on_file_selected(path)
    w.setup_panel._set_flag("_no_ai", True)     # AI 분석 제외 — 결정론 계층만

    # ① 분석
    w._start_analysis()
    if not wait(lambda: w._worker is not None and w._worker.isFinished(), 1200, "분석"):
        return
    pump(1.0)
    m["items"] = len(w._corrections or [])

    # ② 검토 — 진짜 _set_status, 4장마다 1장 거절
    if w._phase == "running":
        w._on_primary()
    pump(0.8)
    rp = w.review_panel

    def card_for(occ):
        while True:
            c = next((cd for cd in rp._cards if cd._occ is occ), None)
            if c is not None or rp._loaded_card_count >= len(rp._occ):
                return c
            rp._load_more_cards(50)

    k = 0
    for occ in list(rp._occ):
        if occ.get("shadowed") or occ["status"] != "pending":
            continue
        status = "rejected" if k % 4 == 3 else "accepted"
        k += 1
        card = card_for(occ)
        if card is not None:
            rp._set_status(occ, card, status)
            pump(0.02)
    pump(1.0)
    pending, accepted, total = rp.get_counts()
    m.update(pending=pending, accepted=accepted, occ_total=total)

    # ③ 표기 일관성 단계(있으면) — 계열 수락, 낱말은 다수 표기
    w._on_primary()
    pump(0.8)
    if rp.in_consistency_mode():
        for f in list(rp._families):
            rp._set_family_status(f, "accepted")
        for wd in list(getattr(rp, "_fam_words", [])):
            d = "spaced" if wd.get("n_spaced", 0) >= wd.get("n_joined", 0) else "joined"
            rp._set_word_choice(wd, d)
        w._on_primary()          # 일관성 적용 → 교정 제안 그리드 복귀
        pump(1.0)
        w._on_primary()          # 교정 적용 시작

    # ④ 교정본 적용
    if not wait(lambda: w._phase == "result", 900, "적용"):
        return
    pump(1.5)
    r = w._result or {}
    for key in ("applied", "occurrences", "failed", "partial", "style_marked"):
        m[key] = r.get(key)

    # ⑤ 교정본 재추출 대조 — 기대 문서는 앱의 적용 계획과 **독립**으로 만든다
    expected, overlap, changed = _expected_text(w._extracted_text, w._applied_occ_rows or [],
                                                w._corrections or [], occurrence_skipped)
    m["oracle_overlap"] = overlap
    m["oracle_spans"] = changed          # 기대 문서에서 실제로 바뀐 자리 — 0이면 대조가 무의미
    hwp_out = r.get("hwp_path") or ""
    if hwp_out and os.path.isfile(hwp_out):
        ed = HwpEditor(hwp_out, logger=lambda *_: None)
        try:
            ed.open()
            actual = unicodedata.normalize("NFC", ed.get_text())
        finally:
            try:
                ed.close()
            except Exception:
                pass
        m["oracle_diff"], m["oracle_lines"] = _diff_count(expected, actual)
    else:
        m["oracle_diff"], m["oracle_lines"] = -1, 0      # 교정본이 없다 = 실패

    # ⑥ 결과물 추가 — 빨간색+메모(교정본 위)·메모본·PDF
    for mode in ("hwp_memo", "memo", "pdf"):
        before = len((w._result or {}).get("extra_outputs") or [])
        w._start_extra_output(mode)
        ok = wait(lambda: len((w._result or {}).get("extra_outputs") or []) > before
                  and not (w._extra_worker is not None and w._extra_worker.isRunning()),
                  900, f"추가 {mode}")
        pump(1.0)
        if not ok:
            continue
        ex = ((w._result or {}).get("extra_outputs") or [])[-1]
        for key in ("memoed", "memo_blocked", "annotated", "pdf_missing", "unmarked"):
            m[f"{mode}.{key}"] = ex.get(key)

    # ⑥.5 다시 적용 — 완료 뒤 레일 '검토' → 수락 카드 몇 장을 거절로 → 다시 적용 →
    #   덮어쓴 교정본을 **새 결정**의 기대 문서와 대조(사용자 요구 2026-10-23).
    w._on_rail_click("review")
    pump(0.8)
    m["reapply_phase_ok"] = (w._phase == "review"
                             and "다시 적용" in w.footer._primary.text())
    flipped = 0
    for occ in list(rp._occ):
        if flipped >= 3:
            break
        c = occ["c"]
        # 통일·이음매 카드는 한 장이 여러 낱말을 움직인다 — 여기선 낱장 결정만 뒤집는다.
        if (occ.get("shadowed") or occ["status"] != "accepted" or occ.get("pos") is None
                or c.get("consistency_flip") or c.get("junction_group")
                or c.get("source") == "dict_flag"):
            continue
        card = card_for(occ)
        if card is not None:
            rp._set_status(occ, card, "rejected")
            pump(0.05)
            flipped += 1
    pump(0.5)
    m["reapply_changed"] = w._decisions_changed()
    w._on_primary()
    pump(0.8)
    if rp.in_consistency_mode():         # 뒤집은 결정으로 계열이 새로 갈렸으면 ③과 같이
        for f in list(rp._families):
            rp._set_family_status(f, "accepted")
        for wd in list(getattr(rp, "_fam_words", [])):
            d = "spaced" if wd.get("n_spaced", 0) >= wd.get("n_joined", 0) else "joined"
            rp._set_word_choice(wd, d)
        w._on_primary()
        pump(1.0)
        w._on_primary()
    if wait(lambda: w._phase == "result"
            and not (w._apply_worker is not None and w._apply_worker.isRunning()),
            900, "다시 적용"):
        pump(1.5)
        r2 = w._result or {}
        exp2, _ov2, _ch2 = _expected_text(w._extracted_text, w._applied_occ_rows or [],
                                          w._applied_corrections or [], occurrence_skipped)
        hwp2 = r2.get("hwp_path") or ""
        if hwp2 and os.path.isfile(hwp2):
            ed = HwpEditor(hwp2, logger=lambda *_: None)
            try:
                ed.open()
                actual2 = unicodedata.normalize("NFC", ed.get_text())
            finally:
                try:
                    ed.close()
                except Exception:
                    pass
            m["reapply_diff"], m["reapply_lines"] = _diff_count(exp2, actual2)
        else:
            m["reapply_diff"], m["reapply_lines"] = -1, 0

    # ⑦ 테마 토글(전 위젯 refresh_theme)
    w._toggle_theme()
    pump(1.0)
    w._toggle_theme()
    pump(1.0)

    # ⑧ 업데이트 대화상자 — 미루면 받은 파일을 지우고, 적용하면 남긴다
    from ui.widgets.update_dialog import UpdateDialog

    def fake_download():
        d = tempfile.mkdtemp(prefix="ksproof-upd-")
        p = os.path.join(d, "package.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("x.txt", "x")
        return p
    info = {"version": "9.9.9", "current": "0.0.1", "size": 123, "notes": ""}
    p1 = fake_download()
    dlg = UpdateDialog(w, info, channel="app")
    dlg._on_downloaded(p1)
    dlg._on_no()                               # '나중에'
    gone = not os.path.exists(os.path.dirname(p1))
    p2 = fake_download()
    dlg2 = UpdateDialog(w, info, channel="app")
    got = []
    dlg2.apply_requested.connect(lambda ch, z: got.append(z))
    dlg2._on_downloaded(p2)
    dlg2._on_yes()                             # '지금 재시작' → MainWindow가 설치(수신만 확인)
    kept = os.path.exists(p2) and got == [p2]
    shutil.rmtree(os.path.dirname(p2), ignore_errors=True)
    m["update_dialog_ok"] = bool(gone and kept)

    w.close()
    pump(1.0)


def _expected_text(text, rows, corrections, occurrence_skipped):
    """원문에서 **보이는 수락 등장**(숨김·건너뜀 제외)만 카드 값으로 바꾼 문서.

    앱의 적용 계획(`ApplyWorker._plan_by_replay`)을 쓰지 않는다 — 계획이 틀리면 같이 틀리는
    판정은 판정이 아니다. 보이는 등장끼리는 겹치지 않아야 하므로, 겹치면 세어 보고한다.
    """
    spans = []
    for r in rows:
        if (r.get("status") != "accepted" or r.get("shadowed") or occurrence_skipped(r)
                or r.get("pos") is None or r.get("end") is None):
            continue
        ci = r.get("ci")
        if ci is None or not (0 <= ci < len(corrections)):
            continue
        spans.append((r["pos"], r["end"], corrections[ci].get("corrected", "")))
    spans.sort()
    out, last, overlap, changed = [], 0, 0, 0
    for a, b, new in spans:
        if a < last:
            overlap += 1
            continue
        changed += text[a:b] != new
        out += (text[last:a], new)
        last = b
    out.append(text[last:])
    return unicodedata.normalize("NFC", "".join(out)), overlap, changed


def _diff_count(expected: str, actual: str):
    """다른 구간 수와 다른 줄 수. 0이면 교정본이 검수 결정 그대로다."""
    if expected == actual:
        return 0, 0
    a, b = expected.split("\n"), actual.split("\n")
    hunks = lines = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            hunks += 1
            lines += max(i2 - i1, j2 - j1)
    return max(hunks, 1), lines


# ══════════════════════════════════════════════════════
# ▌판정 — 부모 프로세스
# ══════════════════════════════════════════════════════
# 기준선과 비교하지 않는 키 — 실행 정보이거나, 그 자체로 절대 판정하는 값.
_NO_BASELINE = ("timeouts", "exceptions", "exception_heads", "secs", "update_dialog_ok",
                "reapply_phase_ok", "reapply_changed", "reapply_diff", "reapply_lines")


def judge(m: dict, base: dict | None):
    fails, changes = [], []
    if m.get("exceptions"):
        fails.append(f"미처리 예외 {m['exceptions']}건: " + " | ".join(m.get("exception_heads", [])))
    if m.get("timeouts"):
        fails.append("시간 초과: " + ", ".join(m["timeouts"]))
    if m.get("oracle_diff") is None:
        fails.append("교정본 대조를 못 했다(적용 전 단계에서 멈춤)")
    elif not m.get("oracle_spans"):
        fails.append("기대 문서에 바뀐 자리가 0곳 — 대조가 아무것도 검사하지 않았다")
    elif m["oracle_diff"]:
        fails.append(f"교정본이 검수 결정과 다르다: {m['oracle_diff']}구간 · {m['oracle_lines']}줄"
                     " (기대 = 보이는 수락 등장만 카드 값으로)")
    if m.get("pending"):
        fails.append(f"검토 후 대기 카드 {m['pending']}장이 남았다")
    if m.get("update_dialog_ok") is False:
        fails.append("업데이트 대화상자: 미루기→삭제 / 적용→보존 검사 실패")
    # 다시 적용(⑥.5) — 절대 판정이라 기준선 비교에서는 뺀다(_NO_BASELINE).
    if m.get("reapply_phase_ok") is False:
        fails.append("완료 뒤 레일 '검토'가 검토 단계('다시 적용')로 돌아가지 않았다")
    if not m.get("reapply_changed"):
        fails.append("다시 적용: 뒤집은 결정이 0곳 — 재적용 대조가 아무것도 검사하지 않았다")
    if m.get("reapply_diff") is None:
        fails.append("다시 적용 대조를 못 했다")
    elif m["reapply_diff"]:
        fails.append(f"다시 적용한 교정본이 바뀐 결정과 다르다: {m['reapply_diff']}구간 · "
                     f"{m['reapply_lines']}줄")
    keys = [k for k in m if k not in _NO_BASELINE]
    if base is None:
        changes.append("기준선 없음 — 확인 후 --save-baseline")
    else:
        for k in sorted(set(keys) | set(base)):
            if k in _NO_BASELINE:
                continue
            o, n = base.get(k), m.get(k)
            if o == n:
                continue
            if k in _WORSE_IF_UP and isinstance(o, int) and isinstance(n, int) and n > o:
                fails.append(f"{k}: {o} → {n} (늘었다)")
            else:
                changes.append(f"{k}: {o} → {n}")
    return fails, changes


def main(argv) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if "--single" in argv:
        i = argv.index("--single")
        run_single(argv[i + 1], argv[i + 2], "--keep" in argv)
        return 0

    save = "--save-baseline" in argv
    keep = "--keep" in argv
    files = _fixtures([a for a in argv if not a.startswith("--")])
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        print("E2E 원고 없음 — 스킵: " + ", ".join(missing))
        print("  원고 경로를 인자나 KS_E2E_HWP로 주세요(고객 원고는 레포 밖·gitignore).")
        return 4

    worst = 0
    for src in files:
        name = os.path.basename(src)
        t0 = time.time()
        fd, out_json = tempfile.mkstemp(prefix="ks-e2e-", suffix=".json")
        os.close(fd)
        cmd = [sys.executable, os.path.abspath(__file__), "--single", src, out_json]
        if keep:
            cmd.append("--keep")
        rc = subprocess.call(cmd, cwd=REPO)
        try:
            with open(out_json, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            m = {"exceptions": 1, "exception_heads": [f"자식 프로세스 비정상 종료(exit {rc})"],
                 "timeouts": [], "secs": {}}
        finally:
            try:
                os.remove(out_json)
            except OSError:
                pass

        bpath = os.path.splitext(src)[0] + ".e2e.json"
        base = None
        if os.path.isfile(bpath):
            with open(bpath, encoding="utf-8") as f:
                base = json.load(f)
        fails, changes = judge(m, base)
        secs = time.time() - t0
        print(f"[E2E] {name} — {secs:.0f}초 · 항목 {m.get('items')} · 적용 {m.get('applied')}건/"
              f"{m.get('occurrences')}곳 · 실패 {m.get('failed')} · 부분 {m.get('partial')} · "
              f"대조 불일치 {m.get('oracle_diff')} · 메모 {m.get('hwp_memo.memoed')}/"
              f"{m.get('memo.memoed')} · 주석 {m.get('pdf.annotated')}")
        for f in fails:
            print(f"  ✗ FAIL {f}")
        for c in changes:
            print(f"  △ 변화 {c}")
        if fails:
            worst = 1
        elif changes and worst == 0:
            worst = 2
        if save and not fails:
            snap = {k: v for k, v in m.items()
                    if k not in ("timeouts", "exceptions", "exception_heads", "secs",
                                 "update_dialog_ok")}
            with open(bpath, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=1)
            print(f"  기준선 저장 → {bpath}")
        elif save and fails:
            print("  ⚠ 실패한 실행은 기준선으로 저장하지 않는다")
    if worst == 2 and save:
        print("  → E2E ✅ 통과 · 기준선 갱신")
    elif worst == 0:
        print("  → E2E ✅ 통과")
    elif worst == 2:
        print("  → E2E △ 통과 · 기준선과 다름 — 원인 확인 후 정당하면 --save-baseline")
    else:
        print("  → E2E ❌ 실패")
    return 0 if (save and worst == 2) else worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
