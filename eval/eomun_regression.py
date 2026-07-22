"""
eval/eomun_regression.py — 어문 규범 레이어 회귀 골드셋 하니스 (PDCA: CHECK)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/eomun-rule-layer-architecture.md §6 CHECK

이 프로젝트의 #1 리스크는 **과교정**이다. 이 하니스는 AI를 호출하지 않고(결정론·오프라인)
파이프라인의 결정론 구간만 검사한다:
  · norm_map        (nikl_dict.batch_lookup_norm)           — 워커 [5.7]
  · eomun_pairs     (core.eomun_rules.batch_lookup_eomun_pair) — 워커 [5.8]
  · KAGEC 검색      (core.eomun_rules.retrieve)               — DO-2(컨텍스트 회수)

검사 3종:
  A. 무변경 테스트(HARD GATE) — 시드 'correct' 예시 토큰 + 카논 위험어를 결정론 구간에
     통과시켜 **단 하나도 바뀌지 않음**을 검증. 하나라도 바뀌면 과교정 회귀 → exit 1.
  B. 결정론 재현율 — deterministic 규칙의 'incorrect' 토큰이 결정론 구간에서 교정되는 비율
     (eomun_pairs 또는 norm_map). "B는 norm_map에 양보" 설계가 실제로 커버하는지 확인.
  C. 컨텍스트 재현율 — 모든 'incorrect' 토큰이 retrieve()로 회수되어 AI에 규범이 주입되는 비율.

지표: precision / recall / F0.5(GEC는 정밀도 가중). A 위반 0이면 precision=1.0.

실행:  .\\.venv64\\Scripts\\python.exe eval\\eomun_regression.py
        (--verbose 로 케이스별 상세, --json 으로 기계 판독 결과)
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

SEED = ROOT / "data" / "eomun" / "eomun_seed.jsonl"

# 카논 회귀 — 절대 결정론 교정되면 안 되는 표준어(동형이의어 재앙 방지선).
#   설계도/메모리의 '있다→이따' 류 + 단음절 조사 충돌(외국인·국가·전문가) 케이스.
CANON_NO_CHANGE = [
    "있다", "없다", "갔다", "받다", "들다", "가치", "외국인", "국가", "전문가",
    "사회문제", "보고서", "생각하고",
]


def _load_strip_josa():
    try:
        from core.consistency_pass import _strip_josa
        return _strip_josa
    except Exception:
        return lambda w: w


def _deterministic_correct(word, *, norm_lookup, pair_lookup, strip_josa):
    """워커 [5.7]+[5.8]의 결정론 교정을 단어 1개에 대해 재현.

    반환: 교정형(str) 또는 None(변경 없음). norm_map 우선, 다음 eomun_pairs.
    """
    clean = re.sub(r"[^가-힣]", "", word)
    if len(clean) < 2:
        return None
    for key in (clean, strip_josa(clean)):
        if not key or len(key) < 2:
            continue
        for table in (norm_lookup, pair_lookup):
            norm = table.get(key)
            if norm and norm != key:
                josa = clean[len(key):] if clean.startswith(key) else ""
                return norm + josa
    return None


def run(verbose=False):
    rows = [json.loads(l) for l in SEED.open(encoding="utf-8") if l.strip()]

    import nikl_dict as nd
    from core import eomun_rules as er
    strip_josa = _load_strip_josa()

    if not er.available():
        print("[경고] eomun.db 없음 — build_eomun_db.py 먼저 실행. 결정론 페어 검사 제한적.")

    # ── 검사 대상 토큰 수집 ──────────────────────────────────────────
    #   · 무변경: 'correct' 예시의 모든 한글 토큰(공백 분해) — 바뀌면 안 됨.
    #   · 컨텍스트/결정론 재현: 규칙의 'triggers'(실제 인덱싱된 오류 표면형)를 계약으로 사용한다.
    #     (incorrect 예시를 재토큰화하면 '않 가다'의 무고한 '가다'까지 잡혀 거짓 미스가 난다.)
    no_change_tokens, det_bad, ctx_bad = [], [], []
    for r in rows:
        for ex in r.get("examples", {}).get("correct", []):
            for t in re.findall(r"[가-힣]{2,}", ex):
                no_change_tokens.append(t)
        for t in r.get("triggers", []):
            t = re.sub(r"[^가-힣]", "", t)
            if len(t) < 2:
                continue
            ctx_bad.append((t, r["rule_id"]))
            if r.get("deterministic"):
                det_bad.append((t, r["rule_id"]))
    no_change_tokens = list(dict.fromkeys(no_change_tokens + CANON_NO_CHANGE))

    # 결정론 조회 테이블을 배치로 한 번에 (성능)
    all_words = set(no_change_tokens) | {t for t, _ in ctx_bad}
    bases = set(all_words)
    for w in list(all_words):
        b = strip_josa(w)
        if b:
            bases.add(b)
    norm_lookup = nd.batch_lookup_norm(bases)
    pair_lookup = er.batch_lookup_eomun_pair(bases)

    def det(w):
        return _deterministic_correct(w, norm_lookup=norm_lookup,
                                      pair_lookup=pair_lookup, strip_josa=strip_josa)

    # ── A. 무변경 테스트 (HARD GATE) ─────────────────────────────────
    violations = []
    for t in no_change_tokens:
        out = det(t)
        if out is not None:
            violations.append((t, out))
    if verbose:
        print(f"[A] 무변경 후보 {len(no_change_tokens)}개 검사")

    # ── B. 결정론 재현율 ────────────────────────────────────────────
    det_hit = [(t, rid, det(t)) for t, rid in det_bad]
    det_corrected = [x for x in det_hit if x[2] is not None]

    # ── C. 컨텍스트 재현율 (retrieve로 규칙 회수) ────────────────────
    ctx_hit = 0
    ctx_detail = []
    for t, rid in ctx_bad:
        cards = er.retrieve(t) if er.available() else []
        found = any(c.rule_id == rid for c in cards)
        ctx_hit += 1 if found else 0
        ctx_detail.append((t, rid, found))

    # ── 지표 ────────────────────────────────────────────────────────
    tp = len(det_corrected)                 # 올바르게 바꾼 결정론 케이스
    fp = len(violations)                    # 바꾸면 안 되는데 바꿈(과교정)
    fn = len(det_bad) - tp                  # 결정론인데 못 바꿈
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    beta2 = 0.25
    f05 = ((1 + beta2) * precision * recall / (beta2 * precision + recall)
           if (precision + recall) else 0.0)

    result = {
        "no_change_checked": len(no_change_tokens),
        "over_correction_violations": len(violations),
        "deterministic_total": len(det_bad),
        "deterministic_corrected": tp,
        "context_total": len(ctx_bad),
        "context_retrieved": ctx_hit,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f0.5": round(f05, 4),
        "pass": len(violations) == 0,
    }

    # ── 리포트 ──────────────────────────────────────────────────────
    print("━" * 64)
    print("어문 규범 레이어 회귀 골드셋 (CHECK · 결정론 오프라인)")
    print("━" * 64)
    print(f"A. 무변경(과교정) 위반 : {len(violations)}건  "
          f"{'✅ PASS' if not violations else '❌ FAIL — 과교정 회귀!'}")
    for t, out in violations:
        print(f"     ✖ '{t}' → '{out}' (바뀌면 안 됨)")
    print(f"B. 결정론 재현        : {tp}/{len(det_bad)}  (eomun_pairs+norm_map가 교정)")
    if verbose:
        for t, rid, out in det_hit:
            print(f"     {'✔' if out else '·'} {t} → {out or '(변경 없음)'}  [{rid}]")
    print(f"C. 컨텍스트 재현      : {ctx_hit}/{len(ctx_bad)}  (retrieve로 규칙 회수→AI 주입)")
    if verbose:
        for t, rid, found in ctx_detail:
            if not found:
                print(f"     · 미회수: {t} [{rid}]")
    print(f"지표: precision={precision:.3f}  recall={recall:.3f}  F0.5={f05:.3f}")
    print("━" * 64)
    print("판정:", "✅ PASS (과교정 0)" if result["pass"] else "❌ FAIL")
    return result


def main():
    ap = argparse.ArgumentParser(description="어문 규범 레이어 회귀 골드셋")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로도 출력")
    args = ap.parse_args()
    result = run(verbose=args.verbose)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
