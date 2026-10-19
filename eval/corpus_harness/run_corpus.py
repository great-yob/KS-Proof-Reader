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
                    --ai 면 AI 경로까지 — Gemini 응답을 캐시/ai_cache/에 요청 단위로 저장·재생한다
                    (_install_ai_cache). 첫 실행만 RPD를 쓰고(20원고 약 200회), AI **뒤**만 고친
                    비교는 몇 번이든 무료. --ai-offline 은 캐시에 없는 요청을 부르지 않고 실패로 둔다.
                    ⚠ 실단어 판정 호출은 앞 단계 카드가 바뀌면 후보가 달라져 빗나간다(정상) — 그땐
                    --ai 로 그 몇 건만 실제로 부른다. 생성 호출이 빗나가면 AI 입력이 바뀐 것이다.
  diff A B          두 실행의 카드 비교(내용 집합 + --order 시 순서까지). 바뀐 카드를 전부 출력
  summary NAME      (source, category, confidence) 분포
  events            data/event_queue.db의 사용자 결정을 **현행 finder로 재분류**해 거절률 산출
  gate              **릴리스 게이트** — AI 켬(캐시 재생·오프라인)·AI 끔 두 방식으로 병렬 실행해
                    판정해 둔 기준선 카드 집합과 비교한다(release.py preflight가 부른다). 코드·사전·
                    원고 캐시의 지문이 지난 실행과 같으면 다시 돌리지 않는다.
                    --save-baseline: 바뀐 카드를 한 장씩 판정한 **뒤에만** 현재 결과를 기준선으로 저장
                    --ai-live: 빗나간 AI 요청을 실제로 부른다(무료 한도·원고 전송 — 사용자 확인 후에만)
                    종료 코드 0 같음 · 1 실패(실행 오류·AI 캐시 빗나감) · 2 다름/기준선 없음 · 4 원고 없음

전형적 사용(규칙을 고치기 전/후):
  python eval/corpus_harness/run_corpus.py run before
  (코드 수정)
  python eval/corpus_harness/run_corpus.py run after
  python eval/corpus_harness/run_corpus.py diff before after
  → 사라진/생긴 카드를 **한 장씩** 보고 오탐 제거만 일어났는지 판정한다.
  결정론: run s0 --seed 0 / run s777 --seed 777 / diff s0 s777 --order → differing_docs=0이어야 한다.

⚠ 고객 원고다. 캐시는 **교정샘플/.corpus/**(교정샘플/ 전체가 .gitignore)에만 둔다 — 레포에 커밋 금지.
⚠ 기본 실행은 AI 경로(Gemini)를 돌리지 않는다. covered(안전망)·일관성 패스처럼 **AI 결과가 있어야만
  도는 단계**는 `--ai`로 재거나 합성 입력으로 따로 검사할 것(memory layer-order-silent-drop-audit 원칙 6).
⚠ 오프라인 차단 때문에 우리말샘·온용어 폴백이 꺼져 실제 앱보다 '검수 필요' 카드가 조금 많다.
  수정 전/후 **비교**용이지 절대치용이 아니다.
"""
import argparse
import hashlib
import json
import os
import re
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
    return sorted(f[:-5] for f in os.listdir(CACHE) if re.fullmatch(r"d[0-9a-f]{8}\.json", f))


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


# ── AI 응답 캐시(--ai) ──────────────────────────────────
AI_CACHE = os.path.join(CACHE, "ai_cache")


class _CachedResp:
    """generate_content 응답 흉내 — _call_and_parse가 읽는 .text·finish_reason만."""

    def __init__(self, rec):
        self.text = rec["text"]
        fr = type("FR", (), {"name": rec.get("finish_reason") or ""})()
        self.candidates = [type("C", (), {"finish_reason": fr})()]


def _install_ai_cache(offline: bool):
    """Gemini 호출을 **요청 단위로** 캐시한다 — 처음 한 번만 실제로 부르고 이후는 재생한다.

    키 = (contents, system_instruction, seed, max_output_tokens). 모델은 키에 넣지 않는다 —
    한도 소진으로 백업 모델이 받은 응답도 재생 때 그대로 나와야 실행이 결정론이 된다.
    그래서 AI **입력**(프롬프트·청커·글로서리·seed)을 바꾸면 캐시가 빗나가 실제로 부르고,
    AI **뒤**(가드·병합·안전망·일관성)만 바꾸면 RPD 0으로 몇 번이고 다시 돌릴 수 있다.
    offline=True면 빗나간 요청은 실패로 처리한다(청크 실패로 집계 — 로그에 남는다).
    ⚠ 고객 원고가 담긴다 — 교정샘플/.corpus/ai_cache/(gitignore)에만 둔다.
    """
    import core.gemini_checker as gc
    os.makedirs(AI_CACHE, exist_ok=True)
    real_client_cls = gc.genai.Client
    gc.AI_CALL_DELAY = 0            # 스로틀은 실제 호출에만(아래 _last) — 재생은 즉시
    gap = 4.1
    _last = [0.0]
    stat = Counter()

    class _Models:
        def __init__(self, real):
            self._real = real

        def generate_content(self, model, contents, config):
            raw = json.dumps([contents, config.system_instruction, config.seed,
                              config.max_output_tokens], ensure_ascii=False)
            path = os.path.join(AI_CACHE, hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json")
            if os.path.exists(path):
                stat["hit"] += 1
                with open(path, encoding="utf-8") as fp:
                    return _CachedResp(json.load(fp))
            if offline:
                stat["miss"] += 1
                raise RuntimeError("corpus ai_cache miss (offline)")
            wait = gap - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self._real.models.generate_content(
                    model=model, contents=contents, config=config)
            finally:
                _last[0] = time.time()
            try:
                fr = getattr(resp.candidates[0].finish_reason, "name",
                             str(resp.candidates[0].finish_reason))
            except Exception:
                fr = ""
            rec = {"model": model, "seed": config.seed, "finish_reason": fr,
                   "text": resp.text or ""}
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(rec, fp, ensure_ascii=False)
            stat["live"] += 1
            return _CachedResp(rec)

    class _Client:
        def __init__(self, api_key=None, **kw):
            real = None if offline else real_client_cls(api_key=api_key, **kw)
            self.models = _Models(real)

    gc.genai.Client = _Client
    import atexit
    atexit.register(lambda: print(f"[ai_cache] 재생 {stat['hit']} · 실제 호출 {stat['live']}"
                                  f" · 빗나감 {stat['miss']}"))


# ── run ─────────────────────────────────────────────────
def cmd_run(args):
    if args.seed is not None and os.environ.get("PYTHONHASHSEED") != str(args.seed):
        env = dict(os.environ, PYTHONHASHSEED=str(args.seed))
        cmd = [sys.executable, os.path.abspath(__file__), "run", args.name, *args.tags,
               *(["--ai"] if args.ai else []), *(["--ai-offline"] if args.ai_offline else [])]
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
    if args.ai:
        _install_ai_cache(offline=args.ai_offline)
    out_dir = os.path.join(RUNS, args.name)
    os.makedirs(out_dir, exist_ok=True)
    tags = args.tags or _docs()
    if not tags:
        print("캐시에 원고가 없습니다 — 먼저 extract를 실행하세요.")
        return
    opts = {"use_ai": bool(args.ai), "scope_typo": True, "scope_spacing": True,
            "scope_polish": False}
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


# ── gate ────────────────────────────────────────────────
# 골드셋은 문장 단위라 계층 간 간섭을 못 본다 — 2026-10-19 두 세션 내내 골드셋은 통과했는데 결함
# (안전망 1글자 덮임·dict_flag가 [7] 통일을 막음·'훗가이도현'·'금용소득에' 손실)은 전부 이 코퍼스의
# diff에서만 나왔다. 그 diff를 릴리스 점검으로 고정한다: 판정해 둔 카드 집합이 기준선이고, 판정하지
# 않은 변화가 있으면 릴리스가 멈춰 사람(에이전트)이 한 장씩 본다. ⚠ AI 끔도 함께 돈다 — AI가 고친
# 오타 뒤에 숨는 손실은 AI 끔에서만 보인다.
_GATE_MODES = (("ai", "AI 켬", ["--ai", "--ai-offline"]), ("det", "AI 끔", []))
_GATE_STATE = os.path.join(RUNS, "_gate_state.json")   # ⚠ CACHE 바로 아래 두지 말 것(원고로 읽힌다)
# 런타임이 아닌 경로 — release.py의 _RUNTIME과 같은 경계(이 하네스 자체는 지문에 넣는다)
_NOT_RUNTIME = re.compile(r"^(eval/|tools/|installer/|docs/|server/|\.claude/)|^(build_dist|setup_dict"
                          r"|update_\w+|build_\w+)\.py$")
_DATA_SKIP = {"event_queue.db", "api_cache.db"}     # 앱 사용으로 바뀌지만 하네스 결과와 무관
_GATE_SHOW = 12                                      # 방식당 화면에 펼칠 바뀐 카드 수


def _fingerprint() -> str:
    """결과를 정하는 입력 전부의 지문 — 같으면 다시 돌려도 바이트까지 같다(카드 순서 결정론)."""
    h = hashlib.sha1(b"gate-v1")
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8").stdout.split()
    for p in sorted(files):
        if _NOT_RUNTIME.search(p) and p != "eval/corpus_harness/run_corpus.py":
            continue
        try:
            with open(os.path.join(REPO, p), "rb") as fp:
                h.update(p.encode() + b"\0" + fp.read())
        except OSError:
            h.update(p.encode() + b"\0missing")
    sys.path.insert(0, REPO)

    def _stat(path):
        st = os.stat(path)
        h.update(f"{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}".encode())

    try:
        import datapaths
        ddir, kdir = str(datapaths.data_dir()), datapaths.kiwi_model_dir()
    except Exception:
        ddir, kdir = os.path.join(REPO, "data"), None
    # 사전 DB는 최상위 *.db만 — data/에는 원본 내려받기 폴더(수천 파일)가 함께 있다
    for f in sorted(os.listdir(ddir)):
        if f.endswith(".db") and f not in _DATA_SKIP:
            _stat(os.path.join(ddir, f))
    if kdir:
        for dp, _dns, fns in os.walk(str(kdir)):
            for f in sorted(fns):
                _stat(os.path.join(dp, f))
    for tag in _docs():
        _stat(os.path.join(CACHE, tag + ".json"))
    h.update(str(len(os.listdir(AI_CACHE)) if os.path.isdir(AI_CACHE) else 0).encode())
    try:
        import kiwipiepy
        h.update(kiwipiepy.__version__.encode())
    except Exception:
        pass
    return h.hexdigest()


def _gate_state() -> dict:
    try:
        with open(_GATE_STATE, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


def _write_gate_state(st: dict):
    with open(_GATE_STATE, "w", encoding="utf-8") as fp:
        json.dump(st, fp, ensure_ascii=False, indent=1)


def _gate_weights(tags) -> dict:
    """원고별 예상 소요(지난 게이트 실행의 초, 없으면 글자 수) — 작업 묶음을 고르게 나눈다."""
    w = {}
    for tag in tags:
        p = os.path.join(RUNS, "_gate_ai", tag + ".json")
        try:
            with open(p, encoding="utf-8") as fp:
                w[tag] = float(json.load(fp).get("secs") or 0) or 1.0
            continue
        except (OSError, ValueError):
            pass
        w[tag] = os.path.getsize(os.path.join(CACHE, tag + ".json")) / 20000
    return w


def _gate_run(tags, jobs: int, ai_live: bool) -> dict:
    """두 방식을 원고 묶음별 하위 프로세스로 한꺼번에 돌린다. 반환: 방식별 통계."""
    w = _gate_weights(tags)
    order = sorted(tags, key=lambda t: -w[t])
    procs = []
    for mode, _label, flags in _GATE_MODES:
        live = ai_live and mode == "ai"
        if live:
            flags = ["--ai"]            # 실제 호출은 한 프로세스에서만 — 스로틀(RPM)이 프로세스마다 따로다
        n = 1 if live else max(1, min(jobs, len(order)))
        groups, load = [[] for _ in range(n)], [0.0] * n
        for t in order:                  # 긴 것부터 가장 가벼운 묶음에(LPT)
            i = load.index(min(load))
            groups[i].append(t)
            load[i] += w[t]
        out_dir = os.path.join(RUNS, "_gate_" + mode)
        shutil.rmtree(out_dir, ignore_errors=True)   # 지난 실행의 원고가 남아 섞이지 않게
        os.makedirs(out_dir)
        for i, g in enumerate(groups):
            if not g:
                continue
            log = open(os.path.join(out_dir + f".{i}.log"), "w", encoding="utf-8")
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", "_gate_" + mode,
                                  *g, *flags], cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            procs.append((mode, p, log))
    stats = {m: Counter() for m, _l, _f in _GATE_MODES}
    for mode, p, log in procs:
        p.wait()
        log.close()
        if p.returncode != 0:
            stats[mode]["proc_fail"] += 1
        with open(log.name, encoding="utf-8", errors="replace") as fp:
            for ln in fp:
                m = re.search(r"재생 (\d+) · 실제 호출 (\d+) · 빗나감 (\d+)", ln)
                if m:
                    stats[mode]["hit"] += int(m.group(1))
                    stats[mode]["live"] += int(m.group(2))
                    stats[mode]["miss"] += int(m.group(3))
    for mode, _l, _f in _GATE_MODES:
        got = _load_run("_gate_" + mode)
        stats[mode]["docs"] = len(got)
        stats[mode]["missing_docs"] = len(set(tags) - set(got))
        stats[mode]["cards"] = sum(len(c) for c in got.values())
        for tag in got:
            with open(os.path.join(RUNS, "_gate_" + mode, tag + ".json"), encoding="utf-8") as fp:
                if json.load(fp).get("errors"):
                    stats[mode]["doc_errors"] += 1
    return {m: dict(c) for m, c in stats.items()}


def _gate_diff(mode: str):
    """기준선과 현재의 카드 차이 — (바뀐 원고 수, 빠진 카드, 생긴 카드, 펼칠 줄, 원고 구성 차이, 기준 장수)."""
    a, b = _load_run("_gate_" + mode + "_base"), _load_run("_gate_" + mode)
    lines, rm, add, differ = [], 0, 0, 0
    for tag in sorted(set(a) & set(b)):
        ka, kb = [_key(c) for c in a[tag]], [_key(c) for c in b[tag]]
        sa, sb = set(ka), set(kb)
        if sa == sb and ka == kb:
            continue
        differ += 1
        rm += len(sa - sb)
        add += len(sb - sa)
        if sa == sb:
            lines.append(f"      {tag}: 카드는 같고 순서만 다름")
        for sign, keys in (("−", sorted(sa - sb)), ("+", sorted(sb - sa))):
            for k in keys:
                lines.append(f"      {tag} {sign} {json.loads(k[0])!r} → {json.loads(k[1])!r} | "
                             f"{json.loads(k[3])} {json.loads(k[5])}")
    docs = (len(set(b) - set(a)), len(set(a) - set(b)))
    return differ, rm, add, lines, docs, sum(len(c) for c in a.values())


def cmd_gate(args):
    tags = _docs()
    if not tags:
        print("[GATE] 원고 캐시가 없다 — 한/글이 있는 PC에서 `run_corpus.py extract`")
        sys.exit(4)
    fp = _fingerprint()
    st = _gate_state()
    last = st.get("last") or {}
    have = all(os.path.isdir(os.path.join(RUNS, "_gate_" + m)) for m, _l, _f in _GATE_MODES)
    if last.get("fingerprint") == fp and have and not args.ai_live:
        stats = last["stats"]
        print("[GATE] 지난 실행 재사용 — 코드·사전·원고 캐시가 같다")
    else:
        t0 = time.time()
        stats = _gate_run(tags, args.jobs, args.ai_live)
        fp = _fingerprint()              # --ai-live가 캐시를 채웠으면 지문이 바뀐다
        st["last"] = {"fingerprint": fp, "stats": stats, "secs": round(time.time() - t0),
                      "at": time.strftime("%Y-%m-%d %H:%M")}
        _write_gate_state(st)
        print(f"[GATE] 원고 {len(tags)}개 × 2방식 실행 — {time.time() - t0:.0f}초")
    broken = False
    for mode, label, _f in _GATE_MODES:
        s = stats[mode]
        bad = []
        if s.get("proc_fail") or s.get("missing_docs"):
            bad.append(f"실행 실패(프로세스 {s.get('proc_fail', 0)} · 결과 없는 원고 "
                       f"{s.get('missing_docs', 0)}) — runs/_gate_{mode}.*.log")
        if s.get("doc_errors"):
            bad.append(f"워커 오류가 난 원고 {s['doc_errors']}개")
        if s.get("miss"):
            bad.append(f"AI 캐시 빗나감 {s['miss']}건 — AI 입력(프롬프트·청커·글로서리·seed)이나 실단어 "
                       "판정 후보가 바뀌었다. 사용자 확인 후 `run_corpus.py gate --ai-live`로 채운다")
        for msg in bad:
            print(f"[GATE] ✗ {label}: {msg}")
        broken = broken or bool(bad)
    if broken:
        sys.exit(1)
    if args.save_baseline:
        for mode, _l, _f in _GATE_MODES:
            dst = os.path.join(RUNS, "_gate_" + mode + "_base")
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(os.path.join(RUNS, "_gate_" + mode), dst)
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                              text=True).stdout.strip()
        st["base"] = {"fingerprint": fp, "head": head, "at": time.strftime("%Y-%m-%d %H:%M"),
                      "cards": {m: stats[m]["cards"] for m, _l, _f in _GATE_MODES}}
        _write_gate_state(st)
        print(f"[GATE] 기준선 저장 — AI 켬 {stats['ai']['cards']:,}장 · AI 끔 {stats['det']['cards']:,}장"
              f" (커밋 {head})")
        return
    if not all(os.path.isdir(os.path.join(RUNS, "_gate_" + m + "_base")) for m, _l, _f in _GATE_MODES):
        print("[GATE] 기준선이 없다 — 카드를 판정한 뒤 `run_corpus.py gate --save-baseline`")
        sys.exit(2)
    changed = False
    for mode, label, _f in _GATE_MODES:
        differ, rm, add, lines, (new_docs, gone_docs), base_n = _gate_diff(mode)
        if not differ and not new_docs and not gone_docs:
            print(f"[GATE] {label} {stats[mode]['cards']:,}장 · 기준선과 같음")
            continue
        changed = True
        extra = f" · 원고 구성 +{new_docs} −{gone_docs}" if new_docs or gone_docs else ""
        print(f"[GATE] {label} {stats[mode]['cards']:,}장(기준 {base_n:,}) · 바뀐 원고 {differ} · "
              f"−{rm} +{add}{extra}")
        for ln in lines[:_GATE_SHOW]:
            print(ln)
        if len(lines) > _GATE_SHOW:
            print(f"      … 외 {len(lines) - _GATE_SHOW}줄 — 전부: "
                  f"`run_corpus.py diff _gate_{mode}_base _gate_{mode} --order`")
    sys.exit(2 if changed else 0)


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
    p.add_argument("--ai", action="store_true",
                   help="AI 경로 포함(Gemini 응답을 ai_cache에 저장·재생 — 첫 실행만 RPD 소모)")
    p.add_argument("--ai-offline", action="store_true",
                   help="--ai 재생 전용: 캐시에 없는 요청은 호출하지 않고 실패로 둔다")
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
    p = sub.add_parser("gate")
    p.add_argument("--save-baseline", action="store_true",
                   help="현재 결과를 기준선으로(바뀐 카드를 전부 판정한 뒤에만)")
    p.add_argument("--ai-live", action="store_true",
                   help="빗나간 AI 요청을 실제로 부른다(무료 한도·원고 전송 — 사용자 확인 후에만)")
    p.add_argument("--jobs", type=int, default=4, help="방식당 병렬 프로세스 수(기본 4)")
    p.set_defaults(fn=cmd_gate)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
