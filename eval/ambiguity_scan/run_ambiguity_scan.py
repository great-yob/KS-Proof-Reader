# -*- coding: utf-8 -*-
"""
eval/ambiguity_scan/run_ambiguity_scan.py — 바른 중의성 데이터셋 기반 개발 전용 스캐너
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
바른(bareun-nlp) 한국어 중의성 데이터셋(35,396문장 / 8,285 표면형, 어절별 정답 형태소
태그)을 **개발 PC 로컬에서만** 읽어, 우리 결정론 finder와 kiwi 의존 가드를 검증한다.

⚠ 라이선스(CC BY-NC 4.0 + 원문은 국립국어원/세종 말뭉치) — 이 앱은 사내 업무용(상업
  맥락)이므로 데이터셋도, 데이터셋에서 뽑은 목록·문장도 **레포에 넣지 않는다.**
  외부 clone 경로를 `KS_AMBIG_DATA` 환경변수로 받고, 없으면 graceful skip.
  런타임(core/·ui/)은 이 모듈을 절대 import 하지 않는다. 상세: 같은 폴더 README.md.

스캔 3종 — 전부 **발견 도구**(자동 수정 없음). 회귀 게이트는 계속 run_goldset.py 담당.
  S-1 정문 대량 무발화 : 결정론 finder 19종 × 데이터셋 문장 → 발화 = 과교정 후보.
                        (Phase D-2 `_CLEAN_CORPUS` 32문장의 수천 배 확장)
  S-2 중의성 충돌 심사  : 중의성 표면형 ∩ (norm_map 키 ∪ spelling_pairs 어간)
                        → 자동 치환하면 위험한 우리 규칙 목록.
  S-3 kiwi 가드 실측    : 정답 태그로 nikl_dict.is_verb_inflection_homograph 정확도 측정.
                        (D-4 `_NORM_VERB_CASES` 손수 11케이스의 정량적 뒷받침)

실행:
  $env:KS_AMBIG_DATA = "C:\\dev\\korean-ambiguity-data"
  .\\.venv64\\Scripts\\python.exe eval\\ambiguity_scan\\run_ambiguity_scan.py
  ... --scan 1        # 특정 스캔만 (1|2|3, 반복 지정 가능)
  ... --limit 0       # 문장 표본 제한 해제(느림)
"""
import sys, os, re, json, argparse, collections

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "eval"))

# 결정론 finder 목록은 **골드셋과 단일 출처**를 쓴다 — 거기 finder가 추가되면 이 스캔도
# 자동으로 따라간다(목록 이중 관리 금지).
#   ⚠ run_goldset은 import 시점에 sys.stdout을 UTF-8 래퍼로 교체한다. 여기서 또 래핑하면
#     우리 래퍼가 GC되며 밑단 buffer를 닫아 "I/O operation on closed file"이 난다
#     (원본 stdout은 sys.__stdout__이 붙들어 살아남지만 우리 래퍼는 아니다).
#     그래서 새 래퍼를 만들지 않고 reconfigure만 한다.
from ai_goldset.run_goldset import _det_finders

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── 데이터셋 로딩 ────────────────────────────────────────────────────────────
# 레이아웃: <clone>/data/<초성>/<형태소결합형>.json
#   각 파일 = [{"text": 문장, "answer": "가/VV+ㄴ/ETM", "split_answer": [["가","VV"],...]}]
#   ⚠ 파일명은 표면형이 아니라 **형태소 결합형**이다. 두 단계로 표면형을 복원한다:
#     ① 자모 합성 — '가ㄴ다'→'간다'. 규칙 활용은 대부분 여기서 해결.
#     ② 어절 교집합 복구 — 불규칙 활용·축약('가깝어'→가까워, '가르아'→갈라, '가시어'→가셔)은
#        합성으로 복원 불가(실측 1,276파일). 한 파일의 모든 문장이 **같은 표면형**을 공유한다는
#        데이터셋 성질을 이용해, 전 문장 공통 어절 ∩ 초성 일치 ∩ 길이 ±2 로 후보를 좁히고
#        합성형과의 편집거리 최소 유일값을 택한다(실측 1,112/1,276 복구, 표본 전건 정확).
#     어느 쪽이든 **문장에 통어절로 실재**해야 채택한다(자기검증).
#     ⚠ ②는 휴리스틱이다. 복구분 비중이 커지면 S-3 수치를 그대로 신뢰하지 말고 표본을 볼 것.

_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_HANGUL_RUN = re.compile(r"[가-힣]+")


def _compose_jamo(name: str) -> str:
    """'가ㄴ다'→'간다' — 홑자음 자모를 앞 음절의 종성으로 합성."""
    out = []
    for ch in name:
        idx = _JONG.find(ch)
        if idx > 0 and out:
            prev = out[-1]
            if "가" <= prev <= "힣" and (ord(prev) - 0xAC00) % 28 == 0:
                out[-1] = chr(ord(prev) + idx)
                continue
        out.append(ch)
    return "".join(out)


def _choseong(ch: str) -> int:
    return (ord(ch) - 0xAC00) // 588 if "가" <= ch <= "힣" else -1


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _recover_surface(composed: str, texts: list):
    """불규칙 활용·축약 표면형 복구 — 전 문장 공통 어절에서 유일 최근접을 고른다."""
    sets = [set(_HANGUL_RUN.findall(t)) for t in texts if t]
    if not sets or not composed:
        return None
    cand = [w for w in set.intersection(*sets)
            if w and _choseong(w[0]) == _choseong(composed[0])
            and abs(len(w) - len(composed)) <= 2]
    if not cand:
        return None
    scored = sorted((_edit_distance(composed, w), w) for w in cand)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None                      # 동점 = 판별 불가 → 채택하지 않음
    return scored[0][1]


def _resolve_data_dir():
    """외부 clone의 data/ 경로. 없으면 None (graceful skip)."""
    raw = os.environ.get("KS_AMBIG_DATA", "").strip().strip('"')
    if not raw:
        return None
    p = os.path.abspath(raw)
    cand = p if os.path.basename(p).lower() == "data" else os.path.join(p, "data")
    return cand if os.path.isdir(cand) else None


def _eojeol_pattern(surface: str):
    try:
        return re.compile(r"(?<![가-힣])" + re.escape(surface) + r"(?![가-힣])")
    except re.error:
        return None


def load_dataset(data_dir):
    """[(표면형, 문장, [(형태소, 태그), ...]), ...] — 표면형이 통어절로 실재하는 항목만.

    반환: (items, 총 항목 수, 스킵 수, 복구로 살린 항목 수) — 커버리지를 숨기지 않는다.
    """
    items, total, skipped, recovered = [], 0, 0, 0
    for root, _dirs, files in os.walk(data_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    rows = json.load(f)
            except Exception:
                continue
            if not isinstance(rows, list) or not rows:
                continue
            texts = [(r or {}).get("text") or "" for r in rows]

            surface = _compose_jamo(fn[:-5])          # ① 자모 합성
            pat = _eojeol_pattern(surface)
            was_recovered = False
            if pat is None or not any(pat.search(t) for t in texts):
                alt = _recover_surface(surface, texts)  # ② 불규칙·축약 복구
                pat = _eojeol_pattern(alt) if alt else None
                if pat is None:
                    total += len(rows)
                    skipped += len(rows)
                    continue
                surface, was_recovered = alt, True

            for r in rows:
                total += 1
                text = (r or {}).get("text") or ""
                tags = [(m[0], m[1]) for m in ((r or {}).get("split_answer") or [])
                        if isinstance(m, (list, tuple)) and len(m) >= 2]
                if not text or not tags or not pat.search(text):
                    skipped += 1        # 표면형 부재 → 정렬 불가라 제외
                    continue
                items.append((surface, text, tags))
                recovered += was_recovered
    return items, total, skipped, recovered


# ── S-1 정문 대량 무발화 ─────────────────────────────────────────────────────

def scan_no_fire(items, limit, quiet=False):
    """데이터셋 정문에서 결정론 finder가 발화하면 과교정 후보로 보고.

    quiet=True면 출력 없이 {finder: [(문장, 원문, 교정), ...]} 만 돌려준다
    (골드셋 Phase E 델타 감시가 이 경로를 쓴다).
    """
    def say(*a):
        if not quiet:
            print(*a)

    say("─" * 72)
    say("S-1 정문 대량 무발화 — 결정론 finder 19종 × 데이터셋 문장")
    from core import morph
    kiwi_ok = False
    try:
        kiwi_ok = morph.available()
    except Exception:
        pass
    if not kiwi_ok:
        say("  (kiwi 미가용 — 형태소 기반 finder 스킵)")

    _m, finders = _det_finders()
    seen, sents = set(), []
    for _surface, text, _tags in items:
        if text in seen:
            continue
        seen.add(text)
        sents.append(text)
    if limit:
        sents = sents[:limit]

    hits_by = collections.defaultdict(list)
    n_checks = 0
    for text in sents:
        for name, fn, needs_kiwi in finders:
            if needs_kiwi and not kiwi_ok:
                continue
            n_checks += 1
            try:
                hits = list(fn(text) or [])
            except Exception as e:
                hits_by[name + " <예외>"].append((text, str(e), ""))
                continue
            for h in hits:
                if len(h) >= 2:
                    hits_by[name].append((text, h[0], h[1]))

    say(f"  정문 {len(sents):,}문장 × finder = {n_checks:,}회 검사")
    if not hits_by:
        say("  ✅ 발화 0 — 과교정 후보 없음")
        return {}
    total = sum(len(v) for v in hits_by.values())
    say(f"  ⚠ 발화 {total:,}건 / finder {len(hits_by)}종 — 아래는 사람이 판정할 후보:")
    for name in sorted(hits_by, key=lambda k: -len(hits_by[k])):
        rows = hits_by[name]
        rate = len(rows) / max(1, len(sents)) * 100
        say(f"\n  [{name}] {len(rows):,}건 (문장당 {rate:.2f}%)")
        for text, o, c in rows[:5]:
            snippet = text if len(text) <= 60 else text[:57] + "…"
            say(f"      {o!r} → {c!r}   | {snippet}")
        if len(rows) > 5:
            say(f"      … 외 {len(rows) - 5:,}건")
    return hits_by


# ── 골드셋 Phase E 연동 API ──────────────────────────────────────────────────

BASELINE_NAME = ".ks_ambig_baseline.json"   # ⚠ 레포가 아니라 **데이터셋 옆**에 둔다


def firing_counts(limit=3000):
    """(finder별 발화 수, 검사 문장 수, 데이터셋 경로) — 출력 없음.

    골드셋 Phase E의 델타 감시용. 데이터셋 미설정이면 (None, 0, None).
    문장 순서는 os.walk + 파일명 순이라 같은 clone에서 **결정적**이다(델타 비교의 전제).
    """
    data_dir = _resolve_data_dir()
    if not data_dir:
        return None, 0, None
    items, _total, _skipped, _rec = load_dataset(data_dir)
    if not items:
        return None, 0, data_dir
    hits = scan_no_fire(items, limit, quiet=True)
    n_sents = len({t for _s, t, _g in items})
    if limit:
        n_sents = min(n_sents, limit)
    return {k: len(v) for k, v in hits.items()}, n_sents, data_dir


def baseline_path():
    """발화 베이스라인 파일 경로 — **데이터셋 clone 옆**(레포 밖). 미설정 시 None."""
    data_dir = _resolve_data_dir()
    return os.path.join(os.path.dirname(data_dir), BASELINE_NAME) if data_dir else None


# ── S-2 중의성 표면형 ∩ 우리 치환 규칙 ───────────────────────────────────────

def _norm_map_keys():
    """norm_map의 비표준 표기 키 집합. 테이블 부재 시 set() (graceful)."""
    import sqlite3
    import nikl_dict as nd
    try:
        db = nd._resolve_db_path()
    except Exception:
        return set()
    if not db or not os.path.exists(str(db)):
        return set()
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT nonstd FROM norm_map").fetchall()
        conn.close()
        return {r[0] for r in rows if r and r[0]}
    except Exception:
        return set()


def scan_rule_collision(items):
    """중의성 표면형과 우리 자동 치환 규칙이 겹치는 지점 = 오교정 위험 목록."""
    print("─" * 72)
    print("S-2 중의성 충돌 심사 — 중의성 표면형 ∩ (norm_map 키 ∪ spelling_pairs 어간)")

    # 표면형별 정답 종류 수 — 2가지 이상이면 '문맥 없이는 확정 불가'
    by_surface = collections.defaultdict(set)
    for surface, _text, tags in items:
        by_surface[surface].add("+".join(f"{m}/{t}" for m, t in tags))
    ambiguous = {s for s, v in by_surface.items() if len(v) >= 2}
    print(f"  데이터셋 표면형 {len(by_surface):,}개 (정답 2종 이상 = 진짜 중의성 {len(ambiguous):,}개)")

    keys = _norm_map_keys()
    if not keys:
        print("  (norm_map 미가용 — 키 대조 스킵)")
    else:
        collide = sorted(ambiguous & keys)
        print(f"\n  ▸ norm_map 키 {len(keys):,}개 중 중의성 표면형과 충돌: {len(collide):,}건")
        if collide:
            print("    → 이 키들은 문맥 없이 치환하면 오교정. 용언 활용형 가드"
                  "(is_verb_inflection_homograph)가 실제로 막는지 S-3에서 확인할 것.")
            for k in collide[:40]:
                readings = sorted(by_surface[k])[:3]
                print(f"      {k!r} : {' | '.join(readings)}")
            if len(collide) > 40:
                print(f"      … 외 {len(collide) - 40:,}건")

    # spelling_pairs 어간은 **부분문자열** 치환이라, 중의성 표면형에 substring으로
    # 박히기만 해도 오염된다(D-1 페어 불변식과 같은 위험 구조).
    try:
        from core.spelling_pairs import _STEM_PAIRS
    except Exception:
        _STEM_PAIRS = {}
    if _STEM_PAIRS:
        bad = []
        for stem in _STEM_PAIRS:
            for s in ambiguous:
                if stem in s:
                    bad.append((stem, s))
                    break
        print(f"\n  ▸ spelling_pairs 어간 {len(_STEM_PAIRS)}개 중 중의성 표면형에 "
              f"부분문자열로 포함: {len(bad)}건")
        for stem, s in bad[:20]:
            print(f"      {stem!r} ⊂ {s!r}  ({' | '.join(sorted(by_surface[s])[:2])})")
        if not bad:
            print("      ✅ 없음 — 페어 substring 오염 위험 미검출")
    return ambiguous


# ── S-3 kiwi 용언 활용형 가드 정확도 실측 ────────────────────────────────────

_VERBAL = ("VV", "VA", "VX")
_INFL_ENDINGS = ("ETM", "EF", "EC", "EP")


def _gold_is_verb_inflection(tags) -> bool:
    """정답 태그가 '용언 어간 + 활용 어미' 인가? (core/morph.py 판정과 동일 기준)"""
    if not tags:
        return False
    first = tags[0][1].split("-")[0]
    last = tags[-1][1].split("-")[0]
    return first in _VERBAL and last in _INFL_ENDINGS


def scan_guard_accuracy(items, limit):
    """정답 태그를 기준으로 우리 가드/kiwi의 용언 활용형 판별을 채점."""
    print("─" * 72)
    print("S-3 kiwi 용언 활용형 가드 실측 — 정답 태그 대조")
    from core import morph
    try:
        if not morph.available():
            print("  (kiwi 미가용 — 스킵)")
            return
    except Exception:
        print("  (kiwi 미가용 — 스킵)")
        return

    # S-3a: 우리 코드 직결 — norm_map 키인 표면형만. 여기서 틀리면 곧 실제 오교정.
    import nikl_dict as nd
    keys = _norm_map_keys()
    if keys:
        tp = fp = tn = fn_ = 0
        misses = []
        for surface, text, tags in items:
            if surface not in keys:
                continue
            gold = _gold_is_verb_inflection(tags)
            try:
                got = bool(nd.is_verb_inflection_homograph(surface, surface, text))
            except Exception:
                continue
            if gold and got:
                tp += 1
            elif gold and not got:
                fn_ += 1
                misses.append((surface, text, tags))     # ★ 치환 강행 = 오교정
            elif not gold and got:
                fp += 1                                   # 정당한 카드를 놓침(과소교정)
            else:
                tn += 1
        n = tp + fp + tn + fn_
        print(f"\n  ▸ S-3a norm_map 키 ∩ 데이터셋: {n:,}건 채점")
        if n:
            print(f"      보류해야 하는데 보류함(정상)      TP={tp:,}")
            print(f"      ★보류해야 하는데 치환함(오교정)  FN={fn_:,}")
            print(f"      치환해도 되는데 보류함(과소교정)  FP={fp:,}")
            print(f"      치환해도 되는 걸 치환함(정상)    TN={tn:,}")
            if tp + fn_:
                print(f"      → 용언 활용형 방어율(recall) = {tp / (tp + fn_) * 100:.1f}%")
            for surface, text, tags in misses[:15]:
                gold = "+".join(f"{m}/{t}" for m, t in tags)
                snippet = text if len(text) <= 56 else text[:53] + "…"
                print(f"      ✗ {surface!r} 정답 {gold} | {snippet}")
            if len(misses) > 15:
                print(f"      … 오교정 후보 외 {len(misses) - 15:,}건")
        else:
            print("      (교집합 없음 — norm_map 키가 데이터셋 표면형과 겹치지 않음)")
    else:
        print("  (norm_map 미가용 — S-3a 스킵)")

    # S-3b: kiwi 자체의 중의성 판별 상한 — 가드를 어디까지 믿어도 되는지의 근거.
    sample = items if not limit else items[:limit]
    ok = tot = 0
    for surface, text, tags in sample:
        gold = _gold_is_verb_inflection(tags)
        try:
            got = morph.verb_inflection_lemma(text, surface) is not None
        except Exception:
            continue
        tot += 1
        ok += (gold == got)
    print(f"\n  ▸ S-3b kiwi 용언 활용형 판별 일치도(표본 {tot:,}건): "
          f"{ok / tot * 100:.1f}%" if tot else "\n  ▸ S-3b 표본 없음")
    print("      → 이 수치가 가드 신뢰 상한. 낮으면 자동 치환 대신 검수(low) 카드로.")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="바른 중의성 데이터셋 기반 개발 전용 스캐너")
    ap.add_argument("--scan", action="append", type=int, choices=[1, 2, 3],
                    help="실행할 스캔(미지정 시 전부)")
    ap.add_argument("--limit", type=int, default=3000,
                    help="문장 표본 상한(기본 3000, 0=제한 없음)")
    args = ap.parse_args()
    scans = set(args.scan or [1, 2, 3])

    print("=" * 72)
    print("바른 중의성 데이터셋 스캐너 — 개발 전용 / 산출물 레포 반입 금지(CC BY-NC 4.0)")
    print("=" * 72)

    data_dir = _resolve_data_dir()
    if not data_dir:
        print("KS_AMBIG_DATA 미설정 또는 data/ 없음 — 스킵.")
        print("  $env:KS_AMBIG_DATA = \"C:\\dev\\korean-ambiguity-data\"")
        print("  (clone 위치는 반드시 이 프로젝트 폴더 **밖**)")
        return 0
    # 라이선스 방어선 — 막아야 할 건 '레포 안'이 아니라 **배포물에 실리는 위치**다.
    #   data/·assets/ 는 PyInstaller가 통째로 번들한다(nikl_dict._resolve_db_path의 _MEIPASS/data
    #   경로 참조). 거기 두면 비상업 데이터가 EXE에 실려 동료에게 배포된다 = 명백한 위반 → 거부.
    #   레포 루트 바로 아래는 번들 대상이 아니므로 **.gitignore로 커밋만 막으면** 허용한다
    #   (사용자 선택 2026-07-22 — 프로젝트 옆에 두는 편이 운용상 편하다는 판단).
    abs_dir = os.path.abspath(data_dir)
    for shipped in ("data", "assets"):
        bundled = os.path.join(_ROOT, shipped)
        # ⚠ 경로가 번들 폴더와 **정확히 같은** 경우도 잡아야 한다(startswith(bundled+sep)만
        #   쓰면 '<repo>\data' 자기 자신이 빠져나간다 — 2026-07-22 실측으로 확인).
        if abs_dir == bundled or abs_dir.startswith(bundled + os.sep):
            print(f"✗ 중단: 데이터셋이 **빌드 번들 폴더**({shipped}/) 안에 있습니다.\n"
                  f"    {abs_dir}\n"
                  "    이 위치는 EXE에 실려 배포되므로 비상업(CC BY-NC) 자산을 둘 수 없습니다.")
            return 2
    if abs_dir.startswith(_ROOT + os.sep):
        print("⚠ 데이터셋이 레포 안에 있습니다 — .gitignore로 커밋이 막혀 있는지,\n"
              "  빌드 스크립트가 이 폴더를 번들하지 않는지 확인하세요.\n")

    items, total, skipped, recovered = load_dataset(data_dir)
    print(f"데이터셋 로드: 항목 {total:,} → 정렬 성공 {len(items):,} "
          f"(그중 불규칙·축약 복구 {recovered:,} / 미정렬 스킵 {skipped:,})\n")
    if not items:
        print("✗ 정렬된 항목 0 — 데이터 레이아웃을 확인하세요.")
        return 2

    if 1 in scans:
        scan_no_fire(items, args.limit)
    if 2 in scans:
        scan_rule_collision(items)
    if 3 in scans:
        scan_guard_accuracy(items, args.limit)

    print("\n" + "─" * 72)
    print("끝. 발견 사항은 사람이 판정한 뒤, **우리가 새로 쓴 문장**으로")
    print("run_goldset.py 의 _CLEAN_CORPUS / _NORM_VERB_CASES 에 회귀 케이스를 추가하세요.")
    print("데이터셋 원문 문장을 레포에 복사하지 마세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
