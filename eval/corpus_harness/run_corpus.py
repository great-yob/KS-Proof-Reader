"""
eval/corpus_harness/run_corpus.py — 실원고 코퍼스 결정론 파이프라인 하네스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
골드셋(eval/ai_goldset)은 **문장 단위** 회귀 자물쇠다. 결정론 규칙을 고칠 때 "실제 원고에서
카드가 몇 장·어떻게 바뀌나"는 재지 못한다. 이 하네스가 그 공백을 메운다:
워커 `ProofreadingWorker._execute`를 **그대로** 돌리되(AI 제외·온라인 사전 조회 차단) 원고 텍스트는
미리 추출해 둔 캐시에서 읽는다 — 한/글 없이 20원고를 몇 분에 돈다.

  extract           교정샘플/ 원고를 **사본으로** 열어 텍스트 추출 → 캐시(한/글 필요, 원고당 수 초)
  run NAME          결정론 파이프라인 실행 → 캐시/runs/NAME/<tag>.json (카드·로그·오류·초)
                    --seed N 이면 PYTHONHASHSEED=N으로 다시 실행한다(결정론 검사용)
  diff A B          두 실행의 카드 비교(내용 집합 + --order 시 순서까지). 바뀐 카드를 전부 출력
  summary NAME      (source, category, confidence) 분포
  events            data/event_queue.db의 사용자 결정을 **현행 finder로 재분류**해 거절률 산출

전형적 사용(규칙을 고치기 전/후):
  python eval/corpus_harness/run_corpus.py run before
  (코드 수정)
  python eval/corpus_harness/run_corpus.py run after
  python eval/corpus_harness/run_corpus.py diff before after
  → 사라진/생긴 카드를 **한 장씩** 보고 오탐 제거만 일어났는지 판정한다.
  결정론: run s0 --seed 0 / run s777 --seed 777 / diff s0 s777 --order → differing_docs=0이어야 한다.

⚠ 고객 원고다. 캐시는 **교정샘플/.corpus/**(교정샘플/ 전체가 .gitignore)에만 둔다 — 레포에 커밋 금지.
⚠ AI 경로(Gemini)는 돌지 않는다. covered(안전망)·일관성 패스처럼 **AI 결과가 있어야만 도는 단계**는
  여기서 재현되지 않으니 합성 입력으로 따로 검사할 것(memory layer-order-silent-drop-audit 원칙 6).
⚠ 오프라인 차단 때문에 우리말샘·온용어 폴백이 꺼져 실제 앱보다 '검수 필요' 카드가 조금 많다.
  수정 전/후 **비교**용이지 절대치용이 아니다.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC_DIR = os.path.join(REPO, "교정샘플")
CACHE = os.environ.get("KS_CORPUS_DIR") or os.path.join(SRC_DIR, ".corpus")
RUNS = os.path.join(CACHE, "runs")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, REPO)


def _tag(name: str) -> str:
    """원고 파일명 → 안정 태그(파일이 늘거나 순서가 바뀌어도 그대로)."""
    return "d" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def _sources():
    if not os.path.isdir(SRC_DIR):
        return []
    return [f for f in sorted(os.listdir(SRC_DIR))
            if f.lower().endswith((".hwp", ".hwpx"))
            and "교정본" not in f and "메모본" not in f and "__cvt__" not in f]


def _docs():
    if not os.path.isdir(CACHE):
        return []
    return sorted(f[:-5] for f in os.listdir(CACHE) if f.endswith(".json"))


# ── extract ─────────────────────────────────────────────
def cmd_extract(args):
    os.chdir(REPO)
    from core import HwpEditor
    os.makedirs(CACHE, exist_ok=True)
    work = tempfile.mkdtemp(prefix="ks-corpus-", dir=CACHE)
    try:
        for name in _sources():
            tag = _tag(name)
            out = os.path.join(CACHE, tag + ".json")
            if os.path.exists(out) and not args.force:
                print(f"skip {tag}  {name}")
                continue
            cp = os.path.join(work, tag + os.path.splitext(name)[1])
            shutil.copyfile(os.path.join(SRC_DIR, name), cp)   # ⚠ 원본은 열지 않는다(사본만)
            ed = HwpEditor(cp, logger=lambda m: None)
            try:
                ed.open()
                rec = {"tag": tag, "name": name, "text": ed.get_text(),
                       "note_lines": list(getattr(ed, "last_note_lines", None) or []),
                       "footnote_lines": list(getattr(ed, "last_footnote_lines", None) or []),
                       "page_count": getattr(ed, "last_page_count", None)}
            except Exception as e:
                print(f"FAIL {tag}  {name}: {e}")
                continue
            finally:
                try:
                    ed.close()
                except Exception:
                    pass
            with open(out, "w", encoding="utf-8") as fp:
                json.dump(rec, fp, ensure_ascii=False)
            print(f"{tag}  {len(rec['text']):>8,}자  {name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── run ─────────────────────────────────────────────────
def cmd_run(args):
    if args.seed is not None and os.environ.get("PYTHONHASHSEED") != str(args.seed):
        env = dict(os.environ, PYTHONHASHSEED=str(args.seed))
        cmd = [sys.executable, os.path.abspath(__file__), "run", args.name, *args.tags]
        sys.exit(subprocess.call(cmd, env=env))
    os.chdir(REPO)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import nikl_api
    import onterm_api
    nikl_api.available = lambda: False          # 오프라인·결정론 — 네트워크·캐시 쓰기 차단
    onterm_api.available = lambda: False
    import ui.workers.proofreading_worker as pw

    class FakeEditor:
        cur = None

        def __init__(self, path, logger=None):
            rec = FakeEditor.cur
            self.last_page_count = rec.get("page_count")
            self.last_note_lines = rec.get("note_lines") or []
            self.last_footnote_lines = rec.get("footnote_lines") or []
            self.last_linebreaks = 0

        def open(self):
            pass

        def close(self):
            pass

        def get_text(self):
            return FakeEditor.cur["text"]

        def verify_originals(self, originals):
            # 브리지 RepeatFind 근사 — 추출 텍스트에서 '\n'을 문단 나눔으로 보고 그대로 찾는다.
            t = FakeEditor.cur["text"].replace("\n", "\r")
            return {o: (o.replace("\n", "\r") in t) for o in originals}

    pw.HwpEditor = FakeEditor
    out_dir = os.path.join(RUNS, args.name)
    os.makedirs(out_dir, exist_ok=True)
    tags = args.tags or _docs()
    if not tags:
        print("캐시에 원고가 없습니다 — 먼저 extract를 실행하세요.")
        return
    opts = {"use_ai": False, "scope_typo": True, "scope_spacing": True, "scope_polish": False}
    total = 0
    for tag in tags:
        with open(os.path.join(CACHE, tag + ".json"), encoding="utf-8") as fp:
            FakeEditor.cur = json.load(fp)
        w = pw.ProofreadingWorker("corpus.hwp", dict(opts))
        cards, logs, errs = [], [], []
        w.finished.connect(lambda r: cards.extend(r))
        w.log_message.connect(logs.append)
        w.error.connect(errs.append)
        t0 = time.time()
        w._execute()
        secs = round(time.time() - t0, 1)
        with open(os.path.join(out_dir, tag + ".json"), "w", encoding="utf-8") as fp:
            json.dump({"tag": tag, "cards": cards, "logs": logs, "errors": errs, "secs": secs},
                      fp, ensure_ascii=False, indent=0)
        total += len(cards)
        print(f"{tag} 카드 {len(cards):4d} · 오류 {len(errs)} · {secs}s", flush=True)
    print(f"합계 카드 {total}")


# ── diff / summary ──────────────────────────────────────
_KEYS = ("original", "corrected", "reason", "source", "category", "confidence",
         "consistency_flip", "spacing_family", "junction_group", "status")


def _load_run(name):
    d = os.path.join(RUNS, name)
    out = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            with open(os.path.join(d, f), encoding="utf-8") as fp:
                out[f[:-5]] = json.load(fp)["cards"]
    return out


def _key(c):
    return tuple(json.dumps(c.get(k), ensure_ascii=False) for k in _KEYS)


def cmd_diff(args):
    a, b = _load_run(args.a), _load_run(args.b)
    n_a = n_b = differ = 0
    for tag in sorted(set(a) & set(b)):
        ka, kb = [_key(c) for c in a[tag]], [_key(c) for c in b[tag]]
        n_a += len(ka)
        n_b += len(kb)
        sa, sb = set(ka), set(kb)
        if sa == sb and (not args.order or ka == kb):
            continue
        differ += 1
        print(f"{tag}: 집합 {'같음' if sa == sb else '다름'} · 순서 {'같음' if ka == kb else '다름'} "
              f"· −{len(sa - sb)} +{len(sb - sa)}")
        for sign, keys in (("−", sorted(sa - sb)), ("+", sorted(sb - sa))):
            for k in keys:
                print(f"   {sign} {json.loads(k[0])!r} → {json.loads(k[1])!r} | "
                      f"{json.loads(k[3])} {json.loads(k[5])} | {json.loads(k[2])[:60]!r}")
    print(f"합계 A={n_a} B={n_b} · 달라진 원고 {differ}")


def cmd_summary(args):
    dist = Counter()
    for cards in _load_run(args.name).values():
        for c in cards:
            dist[(c["source"], c["category"], c["confidence"])] += 1
    for k, n in dist.most_common():
        print(f"{n:6d}  {k}")
    print(f"합계 {sum(dist.values())}")


# ── events ──────────────────────────────────────────────
def cmd_events(args):
    """사용자 결정(accept/reject)을 현행 결정론 finder로 재분류한 finder별 거절률.

    ⚠ 이벤트는 용어 단위(문맥 없음)라 어절 하나를 finder에 넣어 '같은 카드가 나오는가'로 분류한다.
      같은 원고의 반복 테스트가 섞여 있으니 절대값보다 **finder 간 상대 순위**로 볼 것.
    """
    os.chdir(REPO)
    import sqlite3
    from core import morph, spacing_rules as sr, quote_rules as qr, bracket_rules as br
    finders = [
        ("보조용언(-어 주다·보다)", morph.find_aux_connective_spacing),
        ("보조용언(-어야 하다)", morph.find_auxiliary_verb_spacing),
        ("관형어+의존명사", morph.find_spacing_suggestions),
        ("의존명사(등·말)", morph.find_dependent_noun_spacing),
        ("기호 뒤 명사", morph.find_symbol_noun_spacing),
        ("피동 -어지다", morph.find_eojida_join),
        ("문장부호·영문", sr.find_punct_spacing),
        ("인용부호", sr.find_quote_spacing),
        ("숫자 단위", sr.find_unit_spacing),
        ("부호↔따옴표", qr.find_quote_punct_spacing),
        ("괄호 붙임", br.find_paren_attach),
    ]
    db = os.path.join(REPO, "data", "event_queue.db")
    if not os.path.exists(db):
        print("data/event_queue.db 없음")
        return
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    stat = defaultdict(Counter)
    memo = {}
    for o, cr, src, cat, act in con.execute(
            "select original, corrected, suggest_src, category, action from events"):
        if src == "spacing":
            if (o, cr) not in memo:
                lab = "그 밖(복합명사 일관성·이음매 등)"
                for name, fn in finders:
                    try:
                        if any(x[0] == o and x[1] == cr for x in fn(o)):
                            lab = name
                            break
                    except Exception:
                        continue
                memo[(o, cr)] = lab
            lab = "spacing · " + memo[(o, cr)]
        else:
            lab = f"{src} · {cat}"
        stat[lab][act] += 1
    for lab, cnt in sorted(stat.items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(cnt.values())
        print(f"{lab:40s} {tot:5d}건 · 거절 {cnt['reject']:4d} ({100 * cnt['reject'] / tot:3.0f}%)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--force", action="store_true", help="이미 추출한 원고도 다시 추출")
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("run")
    p.add_argument("name")
    p.add_argument("tags", nargs="*", help="일부 원고만(태그). 생략하면 전부")
    p.add_argument("--seed", type=int, default=None, help="PYTHONHASHSEED(결정론 검사)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--order", action="store_true", help="카드 순서까지 비교")
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("summary")
    p.add_argument("name")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("events")
    p.set_defaults(fn=cmd_events)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
