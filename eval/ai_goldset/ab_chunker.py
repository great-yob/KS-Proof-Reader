# -*- coding: utf-8 -*-
"""
eval/ai_goldset/ab_chunker.py — 청커 A/B 검증(보류됐던 '줄바꿈 보존' 재시도)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
원본 청커(현행 baseline) vs 보존 청커(문단 경계 유지)를 같은 골드셋 케이스에 **다중 런** 돌려
  · forbid(precision/과교정 누수) — 둘 다 0이어야(보존 청커가 더 낫거나 같아야)
  · expect(recall) — 보존 청커가 원본 대비 떨어지지 않아야(원복의 진짜 이유였던 회수 출렁 검증)
를 비교한다. 보존 청커는 **monkeypatch**로만 주입(프로덕션 코드 변경 없음 — 실험).

실행: .\.venv64\Scripts\python.exe eval\ai_goldset\ab_chunker.py [--runs N]
"""
import sys, os, io, json, time, threading, argparse, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))

from core import ai_guards
from core.gemini_checker import GeminiChecker
from core.correction_engine import build_engine
from core.config_loader import ConfigLoader
from core.models import AI_CALL_DELAY
import nikl_dict

_ORIG_SPLIT = GeminiChecker._split_sentences   # 현행(원본) 청커 (staticmethod → plain func)


def _preserve_split(text, max_chars):
    """보존 청커 — 문단(줄바꿈) 경계 유지. (보류됐던 수정안)"""
    quote = re.compile(r'([""＂"][^""＂"]*[""＂"])')

    def _split_long(para):
        ph = {}
        def _mask(m):
            k = f"\x00Q{len(ph)}\x00"; ph[k] = m.group(0); return k
        def _unmask(s):
            for k, o in ph.items():
                s = s.replace(k, o)
            return s
        masked = quote.sub(_mask, para)
        sents = [_unmask(s).strip() for s in re.split(r'(?<=[.!?。])\s+', masked)]
        out, b = [], ""
        for s in sents:
            if not s:
                continue
            if len(s) > max_chars:
                if b:
                    out.append(b); b = ""
                for part in s.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if len(b) + len(part) + 2 <= max_chars:
                        b += ((", " if b else "") + part)
                    else:
                        if b:
                            out.append(b)
                        b = part
            elif len(b) + len(s) + 1 <= max_chars:
                b += ((" " if b else "") + s)
            else:
                if b:
                    out.append(b)
                b = s
        if b:
            out.append(b)
        return out

    pieces = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            pieces.extend(_split_long(para))
    chunks, buf = [], ""
    for p in pieces:
        if len(buf) + len(p) + 1 <= max_chars:
            buf += (("\n" if buf else "") + p)
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks or [text[:max_chars]]


def _load_cases():
    out = []
    with io.open(os.path.join(_HERE, "cases.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _eval_case(engine, val, stop, case):
    text = case["text"]
    susp = val.extract_suspicious_words(text, stop_event=stop) if val.available else []
    ai = engine.check_scope(text, susp, scope_typo=True, scope_spacing=True,
                            scope_polish=False, stop_event=stop)
    ai, _ = ai_guards.filter_overcorrections(ai)
    viol = 0
    for s in case.get("forbid_delete", []):
        viol += sum(1 for c in ai if s in c.original and s not in c.corrected)
    for s in case.get("forbid_corrected_contains", []):
        viol += sum(1 for c in ai if s in c.corrected)
    exp_total = len(case.get("expect", []))
    exp_hit = 0
    for e in case.get("expect", []):
        if any(e["orig"] in c.original and e["corr"] in c.corrected for c in ai):
            exp_hit += 1
    return viol, exp_hit, exp_total


def run_version(name, split_func, cases, engine, val, runs):
    GeminiChecker._split_sentences = staticmethod(split_func)
    stop = threading.Event()
    agg = {}   # case_id -> {viol, hit, exp, runs}
    print(f"\n=== [{name}] {runs} run(s) ===")
    for r in range(runs):
        for case in cases:
            viol, hit, exp = _eval_case(engine, val, stop, case)
            a = agg.setdefault(case["id"], {"viol": 0, "hit": 0, "exp": 0})
            a["viol"] += viol; a["hit"] += hit; a["exp"] += exp
            time.sleep(AI_CALL_DELAY)
        print(f"  run {r+1}/{runs} 완료")
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    key = ConfigLoader().get_gemini_key()
    engine = build_engine(key)
    if engine is None:
        print("Gemini 키 없음 — 중단"); sys.exit(2)
    val = nikl_dict.KoreanDictValidator()
    cases = _load_cases()

    try:
        orig = run_version("원본 청커(baseline)", _ORIG_SPLIT, cases, engine, val, args.runs)
        pres = run_version("보존 청커(줄바꿈 유지)", _preserve_split, cases, engine, val, args.runs)
    finally:
        GeminiChecker._split_sentences = staticmethod(_ORIG_SPLIT)   # 원복

    # 집계
    def tot(agg, k):
        return sum(a[k] for a in agg.values())
    print("\n" + "═" * 64)
    print(f"{'케이스':<22}{'원본 viol/recall':>20}{'보존 viol/recall':>20}")
    for case in cases:
        cid = case["id"]
        o, p = orig[cid], pres[cid]
        orec = f"{o['hit']}/{o['exp']}" if o['exp'] else "-"
        prec = f"{p['hit']}/{p['exp']}" if p['exp'] else "-"
        print(f"{cid:<22}{(str(o['viol'])+' / '+orec):>20}{(str(p['viol'])+' / '+prec):>20}")
    print("─" * 64)
    ov, pv = tot(orig, "viol"), tot(pres, "viol")
    oh, oe = tot(orig, "hit"), tot(orig, "exp")
    ph, pe = tot(pres, "hit"), tot(pres, "exp")
    print(f"{'합계':<22}{(str(ov)+' / '+str(oh)+'/'+str(oe)):>20}{(str(pv)+' / '+str(ph)+'/'+str(pe)):>20}")
    print("═" * 64)
    print(f"과교정 누수(forbid):  원본 {ov}  vs  보존 {pv}   "
          + ("✅ 보존 안전(≤원본)" if pv <= ov else "❌ 보존이 더 샘"))
    print(f"회수(recall):         원본 {oh}/{oe}  vs  보존 {ph}/{pe}   "
          + ("✅ 보존 비회귀" if ph >= oh else "⚠ 보존 회수 감소 — 추가 런/검토"))


if __name__ == "__main__":
    main()
