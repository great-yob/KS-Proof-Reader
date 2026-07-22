"""
eval/userdict_regression.py — 사용자 용어 뇌 회귀 골드셋 하니스 (PDCA: CHECK)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/userdict-layer-architecture.md §6 (골드셋 게이트).

공유 규칙은 영향 범위가 **전원**이므로, 배포(스냅샷) 전에 과교정 0을 자동으로 보장해야 한다.
이 하니스는 AI를 호출하지 않고(결정론·오프라인) 후보/스냅샷 페어를 검사한다:

  A. 가드 게이트 — 각 페어를 build_userdict_db.guard_check_many로 검사. 통과분만 배포 대상이며,
     탈락분(예: '있다→이따')은 '가드가 잡아낸 과교정 후보'로 보고한다.
  B. 무변경 테스트(HARD GATE) — 카논 위험어 + stdict 표준 표제어 표본에 **가드 통과 페어**를
     적용해 **단 하나도 바뀌지 않음**을 검증. 하나라도 바뀌면 과교정 회귀 → exit 1.
  C. 가드 효과 입증 — 가드를 끄면(원시 페어 전체) 카논이 몇 건 망가지는지(잡아낸 과교정 수).
  D. 충돌 검사 — 가드 통과 페어가 norm_map/eomun_pairs와 같은 토큰을 *다른* 값으로 교정하는지
     (국가 표준 우선 — 충돌은 큐레이터 판단용 경고).

입력: 후보/스냅샷 JSON({version,pairs,exceptions}). 기본은 운영 스냅샷(data/userdict/snapshot.json)
      이 있으면 그것, 없으면 형식 예시(data/userdict/snapshot.example.json).

실행:  .\\.venv64\\Scripts\\python.exe eval\\userdict_regression.py
        (--snapshot PATH · --verbose · --json)
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

# 카논 회귀 — 절대 결정론 교정되면 안 되는 표준어(동형이의어 재앙 방지선).
#   eomun 하니스와 동일 코어 + 사내 페어가 노릴 법한 표준 어휘 확장.
CANON_NO_CHANGE = [
    "있다", "없다", "갔다", "받다", "들다", "가치", "외국인", "국가", "전문가",
    "사회문제", "보고서", "생각하고", "결제", "결재", "사이즈", "매출액", "내용",
    "정책", "지원", "사업", "관리", "서비스", "센터", "기업",
]

STANDARD_SAMPLE_N = 300   # stdict에서 뽑는 표준 표제어 무변경 표본 크기


def _strip_josa_fn():
    try:
        from core.consistency_pass import _strip_josa
        return _strip_josa
    except Exception:
        return lambda w: w


def _default_snapshot() -> Path:
    real = ROOT / "data" / "userdict" / "snapshot.json"
    return real if real.exists() else ROOT / "data" / "userdict" / "snapshot.example.json"


def _load_pairs(path: Path):
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"스냅샷 읽기 실패: {path} ({e})")
    pairs = [(p.get("nonstd", ""), p.get("norm", "")) for p in data.get("pairs", [])
             if p.get("nonstd") and p.get("norm")]
    exceptions = data.get("exceptions", [])
    return pairs, exceptions


def _sample_standard_headwords(n: int) -> list:
    """stdict.db에서 표준 표제어(2자+, 비표준 register 제외)를 표본 추출. 없으면 []."""
    import build_userdict_db as b
    if not b.STDICT_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(b.STDICT_PATH))
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT word FROM words "
            "WHERE word GLOB '[가-힣][가-힣]*' "
            "  AND (register IS NULL OR register NOT IN "
            "       ('방언','북한어','옛말','일본어식','비표준어')) "
            "LIMIT ?", (n * 3,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    seen = set()
    for (w,) in rows:
        clean = re.sub(r"[^가-힣]", "", w or "")
        if len(clean) >= 2 and clean not in seen:
            seen.add(clean)
            out.append(clean)
        if len(out) >= n:
            break
    return out


def _apply(word, pair_map, strip_josa):
    """결정론 페어를 단어 1개에 적용(조사 보존). 변경형 또는 None."""
    clean = re.sub(r"[^가-힣]", "", word)
    if len(clean) < 2:
        return None
    for key in (clean, strip_josa(clean)):
        if not key or len(key) < 2:
            continue
        norm = pair_map.get(key)
        if norm and norm != key:
            josa = clean[len(key):] if clean.startswith(key) else ""
            return norm + josa
    return None


def run(snapshot_path: Path, verbose=False):
    import build_userdict_db as b
    import nikl_dict as nd
    from core import eomun_rules as er
    strip_josa = _strip_josa_fn()

    pairs, exceptions = _load_pairs(snapshot_path)

    # ── A. 가드 게이트 ───────────────────────────────────────────────
    guard = b.guard_check_many(pairs) if pairs else {}
    pass_pairs, fail_pairs = {}, []
    for (nonstd, norm), (okg, reason) in guard.items():
        if okg:
            pass_pairs[nonstd] = norm
        else:
            fail_pairs.append((nonstd, norm, reason))
    raw_pairs = {ns: nm for ns, nm in pairs}   # 가드 미적용(효과 입증용)

    # ── 무변경 표본 ──────────────────────────────────────────────────
    sample = _sample_standard_headwords(STANDARD_SAMPLE_N)
    no_change = list(dict.fromkeys(CANON_NO_CHANGE + sample))

    # ── B. 무변경 테스트 (HARD GATE) — 가드 통과 페어 적용 ───────────
    violations = [(t, out) for t in no_change
                  if (out := _apply(t, pass_pairs, strip_josa)) is not None]

    # ── C. 가드 효과 입증 — 원시(미가드) 페어가 카논을 망가뜨리는 수 ──
    raw_over = [(t, out) for t in no_change
                if (out := _apply(t, raw_pairs, strip_josa)) is not None]

    # ── D. 충돌 검사 — 가드 통과 페어 vs 국가 표준(norm_map/eomun) ────
    bases = set(pass_pairs)
    norm_map = nd.batch_lookup_norm(bases) if bases else {}
    eomun_map = er.batch_lookup_eomun_pair(bases) if bases else {}
    conflicts = []
    for ns, nm in pass_pairs.items():
        std = norm_map.get(ns) or eomun_map.get(ns)
        if std and std != nm:
            conflicts.append((ns, nm, std))

    result = {
        "snapshot": str(snapshot_path.name),
        "pairs_total": len(pairs),
        "pairs_guard_pass": len(pass_pairs),
        "pairs_guard_fail": len(fail_pairs),
        "exceptions": len(exceptions),
        "no_change_checked": len(no_change),
        "over_correction_violations": len(violations),
        "guard_caught_over_corrections": len(raw_over),
        "standard_conflicts": len(conflicts),
        "pass": len(violations) == 0,
    }

    # ── 리포트 ───────────────────────────────────────────────────────
    print("━" * 64)
    print("사용자 용어 뇌 회귀 골드셋 (CHECK · 결정론 오프라인)")
    print("━" * 64)
    print(f"스냅샷: {snapshot_path.name}  (페어 {len(pairs)} · 예외 {len(exceptions)})")
    print(f"A. 가드 게이트        : 통과 {len(pass_pairs)} · 탈락 {len(fail_pairs)}")
    if verbose or fail_pairs:
        for ns, nm, why in fail_pairs:
            print(f"     ✕ '{ns}→{nm}' 탈락 — {why}")
    print(f"B. 무변경(과교정) 위반: {len(violations)}건  (표본 {len(no_change)}개) "
          f"{'✅ PASS' if not violations else '❌ FAIL — 과교정 회귀!'}")
    for t, out in violations:
        print(f"     ✖ '{t}' → '{out}' (바뀌면 안 됨)")
    print(f"C. 가드가 잡은 과교정 : {len(raw_over)}건  (가드 없으면 카논 손상될 뻔)")
    if verbose:
        for t, out in raw_over:
            print(f"     ⚠ '{t}' → '{out}' (가드가 차단)")
    print(f"D. 국가표준 충돌      : {len(conflicts)}건  (런타임 국가표준 우선·큐레이터 검토용)")
    for ns, nm, std in conflicts:
        print(f"     ⚠ '{ns}': 사내 '{nm}' vs 표준 '{std}'")
    print("━" * 64)
    print("판정:", "✅ PASS (과교정 0)" if result["pass"] else "❌ FAIL (과교정 회귀)")
    return result


def main():
    ap = argparse.ArgumentParser(description="사용자 용어 뇌 회귀 골드셋")
    ap.add_argument("--snapshot", default="", help="후보/스냅샷 JSON 경로(기본: 운영 또는 예시)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    path = Path(args.snapshot) if args.snapshot else _default_snapshot()
    result = run(path, verbose=args.verbose)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
