"""
core/review_session.py — 검토 작업 저장 · 이어서 하기
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
분석이 끝난 뒤의 검토 상태(교정 제안 + 등장별 수락/거절)를 **원고별로 자동 저장**한다.
앱이 꺼지거나 초기화해도 같은 원고로 다시 시작하면 Gemini를 다시 부르지 않고 저장된
검토를 그대로 이어서 한다(사용자 요구 2026-10-23 — 재분석은 호출 비용과 시간을 다시 쓴다).

· 원고 식별 = 파일 **내용**의 SHA-256. 경로·이름이 바뀌어도 같은 원고이고, 한 글자라도
  고치면 다른 원고다 — 등장 좌표가 추출 텍스트에 묶여 있어 내용이 바뀐 원고에 옛 검토를
  얹으면 엉뚱한 자리가 치환된다.
· 저장 위치 = `datapaths.cache_dir()/review_sessions/`(런타임 쓰기 규칙).
  ⚠ **고객 원고 전문이 담긴다.** 개발 PC에선 레포 `data/` 아래이므로 `.gitignore`와
    `build_dist._EXCLUDE_PATTERNS`·`verify()`에 함께 올려 두었다. `.db`로 바꾸지 말 것 —
    `build_dist.stage_data()`는 `data/*.db`를 전부 배포본에 싣는다.
· 원고 하나에 파일 둘: `<sid>.json.gz`(본문) + `<sid>.meta.json`(요약). 시작 안내·목록은
  요약만 읽는다(본문을 매번 풀지 않으려고).
· 값은 **태그 JSON**으로 쓴다. 교정 dict에는 튜플이 섞여 있어(`_unify_base` 등) 평범한
  JSON은 그것을 리스트로 바꾸고, 복원 뒤 같은 값이 다른 타입이 된다. pickle은 쓰지 않는다
  (파일을 읽는 것이 곧 코드 실행 경로가 된다). 표현할 수 없는 값이 있으면 저장을 **실패**로
  돌려준다 — 틀리게 복원되는 저장본보다 없는 편이 낫다.
· 쓰기는 임시 파일 → `os.replace`(원자적). 쓰는 도중 앱이 죽어도 이전 저장본이 남는다.
· `SCHEMA` — 저장 구조(검토 패널의 등장 필드·교정 dict의 사적 필드 등)를 바꾸면 올린다.
  다른 버전의 저장본은 읽지 않는다(어긋난 구조를 억지로 맞추면 결정이 조용히 틀어진다).

GUI 비의존 — 저장할 내용을 모으는 일은 호출부(ui)가 한다.
"""

import gzip
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import datapaths

SCHEMA = 1
KEEP = 20              # 원고 수 상한 — 넘으면 오래된 것부터 지운다
MAX_AGE_DAYS = 30      # 이보다 오래 손대지 않은 저장본은 지운다(고객 원고를 무기한 두지 않는다)

_DIR_NAME = "review_sessions"
_fp_cache = {}         # (절대 경로, 크기, 수정 시각 ns) → 지문


def sessions_dir() -> Path:
    d = datapaths.cache_dir() / _DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def fingerprint(path: str) -> str:
    """원고 파일 내용의 지문(SHA-256 앞 32자). 읽지 못하면 ""."""
    try:
        ap = os.path.abspath(path)
        st = os.stat(ap)
    except OSError:
        return ""
    key = (ap, st.st_size, st.st_mtime_ns)
    hit = _fp_cache.get(key)
    if hit:
        return hit
    h = hashlib.sha256()
    try:
        with open(ap, "rb") as fp:
            for chunk in iter(lambda: fp.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    fp_ = h.hexdigest()[:32]
    _fp_cache[key] = fp_
    return fp_


# ══════════════════════════════════════════════════════
# ▌태그 JSON — 튜플·집합·문자열 아닌 키를 타입 그대로
# ══════════════════════════════════════════════════════
_T, _S, _D = "__tuple__", "__set__", "__dict__"


def encode(v):
    """파이썬 값 → JSON으로 쓸 수 있는 값. 표현할 수 없는 타입이면 TypeError."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, list):
        return [encode(x) for x in v]
    if isinstance(v, tuple):
        return {_T: [encode(x) for x in v]}
    if isinstance(v, (set, frozenset)):
        # 순서는 뜻이 없지만 같은 상태가 같은 파일이 되도록 정렬한다(정렬 불가면 그대로).
        items = [encode(x) for x in v]
        try:
            items.sort(key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
        except TypeError:
            pass
        return {_S: items}
    if isinstance(v, dict):
        if all(isinstance(k, str) for k in v) and not (v.keys() & {_T, _S, _D}):
            return {k: encode(x) for k, x in v.items()}
        return {_D: [[encode(k), encode(x)] for k, x in v.items()]}
    raise TypeError(f"저장할 수 없는 값: {type(v).__name__}")


def decode(v):
    if isinstance(v, list):
        return [decode(x) for x in v]
    if isinstance(v, dict):
        if len(v) == 1:
            if _T in v:
                return tuple(decode(x) for x in v[_T])
            if _S in v:
                return {decode(x) for x in v[_S]}
            if _D in v:
                return {decode(k): decode(x) for k, x in v[_D]}
        return {k: decode(x) for k, x in v.items()}
    return v


# ══════════════════════════════════════════════════════
# ▌저장 · 읽기
# ══════════════════════════════════════════════════════
def _paths(sid: str):
    d = sessions_dir()
    return d / f"{sid}.json.gz", d / f"{sid}.meta.json"


def _atomic_write(path: Path, data: bytes):
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save(sid: str, payload: dict, meta: dict):
    """본문과 요약을 쓴다. 성공하면 None, 실패하면 사유 문자열.

    본문을 먼저 쓰고 요약을 나중에 쓴다 — 요약은 '읽을 수 있는 본문이 있다'는 표지다.
    """
    if not sid:
        return "원고 지문 없음"
    body_p, meta_p = _paths(sid)
    try:
        raw = json.dumps({"schema": SCHEMA, "payload": encode(payload)},
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # 압축 수준 1 — 본문(원고 전문)이 대부분이라 빠른 쪽으로도 크기가 1/4 안팎이 된다.
        _atomic_write(body_p, gzip.compress(raw, compresslevel=1))
        m = dict(meta)
        m.update({"sid": sid, "schema": SCHEMA, "saved_at": time.time()})
        _atomic_write(meta_p, json.dumps(m, ensure_ascii=False).encode("utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        return str(exc) or type(exc).__name__
    return None


def load(sid: str):
    """저장본의 payload — 없거나 깨졌거나 구조 버전이 다르면 None."""
    body_p, _ = _paths(sid)
    try:
        with open(body_p, "rb") as fp:
            doc = json.loads(gzip.decompress(fp.read()).decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    payload = decode(doc.get("payload"))
    return payload if isinstance(payload, dict) else None


def meta(sid: str):
    """요약 — 본문이 없거나 구조 버전이 다르면 None."""
    if not sid:
        return None
    body_p, meta_p = _paths(sid)
    try:
        with open(meta_p, encoding="utf-8") as fp:
            m = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("schema") != SCHEMA or not body_p.exists():
        return None
    return m


def list_meta() -> list:
    """읽을 수 있는 저장본 요약 전부 — 최근 저장 순."""
    out = []
    try:
        names = os.listdir(sessions_dir())
    except OSError:
        return out
    for n in names:
        if n.endswith(".meta.json"):
            m = meta(n[:-len(".meta.json")])
            if m:
                out.append(m)
    out.sort(key=lambda m: m.get("saved_at") or 0, reverse=True)
    return out


def update_meta(sid: str, **fields) -> bool:
    """요약 필드만 고친다(`saved_at`은 그대로 — 내용이 바뀐 게 아니다)."""
    m = meta(sid)
    if m is None:
        return False
    m.update(fields)
    try:
        _atomic_write(_paths(sid)[1], json.dumps(m, ensure_ascii=False).encode("utf-8"))
    except OSError:
        return False
    return True


def delete(sid: str):
    for p in _paths(sid):
        try:
            p.unlink()
        except OSError:
            pass


def prune(keep: int = KEEP, max_age_days: int = MAX_AGE_DAYS) -> int:
    """오래된 저장본 정리 — 지운 원고 수. 요약 없는 본문·남은 임시 파일도 지운다."""
    d = sessions_dir()
    now = time.time()
    metas = list_meta()
    alive = {m["sid"] for m in metas[:keep]
             if now - (m.get("saved_at") or 0) <= max_age_days * 86400}
    gone = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for n in names:
        if n.endswith(".tmp"):
            # 쓰다 죽은 임시 파일 — 지금 쓰는 중일 수 있으니 1시간 지난 것만.
            try:
                if now - os.path.getmtime(d / n) > 3600:
                    os.remove(d / n)
            except OSError:
                pass
            continue
        sid = n.split(".", 1)[0]
        if sid and sid not in alive and n.endswith((".json.gz", ".meta.json")):
            try:
                os.remove(d / n)
                gone += n.endswith(".json.gz")
            except OSError:
                pass
    return gone
