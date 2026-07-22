# -*- coding: utf-8 -*-
"""풀문서 청커 A/B — z 출렁임이 나타났던 다중청크 조건에서 원본 vs 보존 청커 비교.
   (PYTHONIOENCODING=utf-8 로 실행. 자체완결형 — ab_chunker 미import.)"""
import sys, os, io, time, threading, re, argparse
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)

from core import ai_guards
from core.gemini_checker import GeminiChecker
from core.correction_engine import build_engine
from core.config_loader import ConfigLoader
from core.models import AI_CALL_DELAY
import nikl_dict

_ORIG_SPLIT = GeminiChecker._split_sentences


def _preserve_split(text, max_chars):
    """보존 청커 — 문단(줄바꿈) 경계 유지."""
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
                    if part and len(b) + len(part) + 2 <= max_chars:
                        b += ((", " if b else "") + part)
                    elif part:
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
        pieces.extend([para] if len(para) <= max_chars else _split_long(para))
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


SCR = (r"C:/Users/user9/AppData/Local/Temp/claude/"
       r"c--Users-user9-Desktop-Work-Utility-KS-Proof-Reader/"
       r"848f7ded-5490-47da-8083-c4fb331fb0f0/scratchpad/")

DOCS = {
    "doc24": {"path": SCR + "doc24_text.txt",
              "forbid_delete": ["Proposed system configuration", "Proposed sequence diagram"],
              "expect": [("무기체계와z", "무기체계와"), ("algorism", "algorithm"),
                         ("Duplication", "Duplicate")]},
    "doc06": {"path": SCR + "doc06_text.txt",
              "forbid_delete": [],
              "expect": [("과정으로이", "과정이"), ("두었으다", "두었다"), ("Filed", "Field")]},
}


def run_doc(engine, val, text, spec):
    stop = threading.Event()
    susp = val.extract_suspicious_words(text, stop_event=stop) if val.available else []
    ai = engine.check_scope(text, susp, scope_typo=True, scope_spacing=True,
                            scope_polish=False, stop_event=stop)
    ai, _ = ai_guards.filter_overcorrections(ai)
    viol = sum(1 for s in spec["forbid_delete"] for c in ai
               if s in c.original and s not in c.corrected)
    hits = sum(1 for o, cc in spec["expect"]
               if any(o in c.original and cc in c.corrected for c in ai))
    return len(ai), viol, hits


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--runs", type=int, default=3)
    runs = ap.parse_args().runs
    engine = build_engine(ConfigLoader().get_gemini_key())
    if engine is None:
        print("키 없음"); sys.exit(2)
    val = nikl_dict.KoreanDictValidator()
    missing = [v["path"] for v in DOCS.values() if not os.path.exists(v["path"])]
    if missing:
        print("⚠ 추출 텍스트 fixture가 없습니다(scratchpad가 비워짐):")
        for m in missing:
            print("   -", m)
        print("  → HwpEditor로 doc06/24를 재추출하거나, DOCS의 path를 fixture로 바꾸세요.")
        print("  (풀문서 A/B 결론은 memory [[ai-goldset-regression]]에 기록됨: 보존 청커 recall 회귀 → 미채택)")
        sys.exit(2)
    texts = {k: io.open(v["path"], encoding="utf-8").read() for k, v in DOCS.items()}

    agg = {v: {d: {"n": 0, "viol": 0, "hit": 0, "exp": 0} for d in DOCS} for v in ("orig", "pres")}
    try:
        for vname, split in (("orig", _ORIG_SPLIT), ("pres", _preserve_split)):
            GeminiChecker._split_sentences = staticmethod(split)
            print(f"\n=== {vname} {runs}런 ===")
            for r in range(runs):
                for d, spec in DOCS.items():
                    n, viol, hit = run_doc(engine, val, texts[d], spec)
                    a = agg[vname][d]
                    a["n"] += n; a["viol"] += viol; a["hit"] += hit; a["exp"] += len(spec["expect"])
                    print(f"  run{r+1} {d}: 교정 {n} · 누수 {viol} · 회수 {hit}/{len(spec['expect'])}")
                    time.sleep(AI_CALL_DELAY)
    finally:
        GeminiChecker._split_sentences = staticmethod(_ORIG_SPLIT)

    print("\n" + "=" * 60)
    for d in DOCS:
        o, p = agg["orig"][d], agg["pres"][d]
        print(f"[{d}]  원본: 누수{o['viol']} 회수{o['hit']}/{o['exp']} 교정평균{o['n']/runs:.1f}"
              f"   |   보존: 누수{p['viol']} 회수{p['hit']}/{p['exp']} 교정평균{p['n']/runs:.1f}")
    ov = sum(agg['orig'][d]['viol'] for d in DOCS); pv = sum(agg['pres'][d]['viol'] for d in DOCS)
    oh = sum(agg['orig'][d]['hit'] for d in DOCS); ph = sum(agg['pres'][d]['hit'] for d in DOCS)
    oe = sum(agg['orig'][d]['exp'] for d in DOCS)
    print("=" * 60)
    print(f"과교정 누수:  원본 {ov} vs 보존 {pv}  " + ("OK" if pv <= ov else "FAIL 보존이 더 샘"))
    print(f"회수:         원본 {oh}/{oe} vs 보존 {ph}/{oe}  " + ("OK 비회귀" if ph >= oh else "WARN 보존 회수 감소"))


if __name__ == "__main__":
    main()
