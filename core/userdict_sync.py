"""
core/userdict_sync.py — 공유 용어 뇌 클라이언트 동기화 (push/pull)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계도: docs/userdict-layer-architecture.md §5(이벤트 캡처 & 동기화) · §3(Supabase).
DO-4d. 로컬 이벤트 큐(core.event_queue)와 Supabase(ks-works 프로젝트) 사이의 동기화만
담당한다 — 교정 파이프라인은 일절 건드리지 않는다(서버=동기화·거버넌스).

  · push() — 로컬 큐의 미전송(synced=0) 이벤트를 userdict_events에 업로드 후 synced 표시.
  · pull() — 최신 userdict_snapshots(ver 최댓값)를 받아 로컬 ver보다 높으면
             data/userdict/snapshot.json 으로 저장하고 build_userdict_db로 userdict.db 재빌드.
             (동형이의어 가드는 그 빌드타임에 stdict로 적용 — 서버 합의 ≠ 자동 신뢰.)
  · sync() — push 후 pull.

인증: Supabase Auth email/password(사내 계정) → access_token. RLS가 본인 이벤트만 insert·
      활성 멤버만 snapshot read를 강제한다. url/anon_key는 배포본 내장(공개), email/password는
      config.ini [SUPABASE] 또는 환경변수.

규율:
  · GUI-agnostic — PySide6 import 금지. UI는 워커 스레드에서 이 함수들을 호출.
  · graceful — 키/비밀번호 미설정·오프라인·서버 오류 시 0/no-op 반환, **예외 무전파**.
    이벤트는 로컬 큐에 보존되어 다음 온라인 시 재전송된다.
  · stdlib(urllib)만 사용 — 새 의존성 없음.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from core import auth

_TIMEOUT = 12                 # 초


def _cfg() -> dict:
    try:
        from core.config_loader import ConfigLoader
        return ConfigLoader().get_supabase()
    except Exception:
        return {}


def available() -> bool:
    """동기화 가능 여부 — 서버 설정이 있고 사내 계정으로 **로그인**돼 있는가.

    교정 기능은 로그인과 무관하게 동작하며, 동기화/큐레이션만 로그인 세션을 요구한다.
    """
    c = _cfg()
    return bool(c.get("url") and c.get("anon_key")) and auth.is_logged_in()


def _http(method: str, url: str, headers: dict, body=None) -> tuple:
    """(status, parsed_json|None) 반환. 어떤 실패도 (0, {...}) 로 흡수(예외 무전파)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = ""
        return e.code, {"_error": detail}
    except Exception as e:
        return 0, {"_error": str(e)}


def _auth_headers(c: dict, token: str, extra: Optional[dict] = None) -> dict:
    h = {"apikey": c["anon_key"], "Authorization": f"Bearer {token}"}
    if extra:
        h.update(extra)
    return h


# ══════════════════════════════════════════════════════
# ▌push — 로컬 큐 → userdict_events
# ══════════════════════════════════════════════════════

def push(logger=None) -> int:
    """미전송 이벤트를 업로드하고 synced 표시. 업로드 건수 반환(0=비활성/오프라인/없음)."""
    log = logger or (lambda *_: None)
    if not available():
        return 0
    try:
        from core import event_queue
        pend = event_queue.pending(limit=500)
    except Exception:
        return 0
    if not pend:
        return 0
    c = _cfg()
    token = auth.access_token()
    if not token:
        log("  [동기화] 로그인 실패 — 이벤트는 로컬 큐에 보관(다음 접속 시 재전송)")
        return 0
    # user_id/org_id/ts는 서버 기본값(auth.uid()/상수/now)에 맡긴다. event_id는 보내
    #   재전송 멱등성 확보(ignore-duplicates).
    rows = [{
        "event_id":     p["event_id"],
        "original":     p["original"],
        "corrected":    p["corrected"],
        "action":       p["action"],
        "suggest_src":  p.get("suggest_src") or None,
        "category":     p.get("category") or None,
        "doc_type":     p.get("doc_type"),
        "snapshot_ver": p.get("snapshot_ver"),
    } for p in pend]
    status, js = _http(
        "POST", f"{c['url']}/rest/v1/userdict_events",
        _auth_headers(c, token, {
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal"}),
        rows)
    if status in (200, 201, 204):
        try:
            from core import event_queue
            event_queue.mark_synced([p["event_id"] for p in pend])
        except Exception:
            pass
        log(f"  [동기화] 학습 이벤트 {len(rows)}건 업로드 완료")
        return len(rows)
    log(f"  [동기화] 이벤트 업로드 실패(status={status}) — 로컬 큐 보관")
    return 0


# ══════════════════════════════════════════════════════
# ▌pull — userdict_snapshots → 로컬 userdict.db 재빌드
# ══════════════════════════════════════════════════════

def pull(logger=None) -> dict:
    """최신 스냅샷을 받아 로컬 ver보다 높으면 userdict.db 재빌드. 결과 dict."""
    log = logger or (lambda *_: None)
    if not available():
        return {"pulled": False}
    c = _cfg()
    token = auth.access_token()
    if not token:
        return {"pulled": False}
    status, js = _http(
        "GET",
        f"{c['url']}/rest/v1/userdict_snapshots"
        f"?select=ver,payload,sha256&order=ver.desc&limit=1",
        _auth_headers(c, token, {"Accept": "application/json"}))
    if status != 200 or not isinstance(js, list) or not js:
        return {"pulled": False}
    snap = js[0]
    ver = snap.get("ver")
    payload = snap.get("payload")
    try:
        from core import userdict
        local_ver = userdict.snapshot_version() or 0
    except Exception:
        local_ver = 0
    if ver is None or payload is None or ver <= local_ver:
        return {"pulled": False, "ver": local_ver}
    # payload({version,pairs,exceptions})를 빌더 입력 경로에 저장 후 재빌드.
    try:
        import build_userdict_db as b
        path = b.DEFAULT_SNAPSHOT
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        b.build(path)
    except Exception as e:
        log(f"  [동기화] 스냅샷 v{ver} 빌드 실패: {e}")
        return {"pulled": False, "ver": local_ver}
    log(f"  [동기화] 사내 용어 사전 v{ver} 적용 (이전 v{local_ver})")
    return {"pulled": True, "ver": ver, "prev": local_ver}


def sync(logger=None) -> dict:
    """push 후 pull. 결과 요약 dict. 전 구간 graceful."""
    pushed = push(logger)
    res = pull(logger)
    res["pushed"] = pushed
    return res


def status() -> dict:
    """진단용 — 설정 가용성(비밀은 노출하지 않음)."""
    c = _cfg()
    return {
        "available":   available(),
        "url":         c.get("url", ""),
        "has_anon":    bool(c.get("anon_key")),
        "has_email":   bool(c.get("email")),
        "has_password": bool(c.get("password")),
    }


# ══════════════════════════════════════════════════════
# ▌큐레이터 연산 (DO-5) — role='admin' 세션 전용. RLS/RPC가 권한을 강제한다.
# ══════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_curator() -> bool:
    """로그인 사용자가 큐레이터(employees.role='admin')인가(세션 role 기준).

    UI 노출용 — 실제 권한은 서버 RLS가 강제한다. 세션에서 즉시 판정(네트워크 무발생).
    """
    return auth.is_curator()


def list_candidates(statuses=None, limit: int = 500) -> list:
    """후보 큐를 dict 목록으로 반환(큐레이터만 — RLS). 실패 시 []."""
    if not available():
        return []
    c = _cfg()
    token = auth.access_token()
    if not token:
        return []
    url = (f"{c['url']}/rest/v1/userdict_candidates"
           f"?select=*&order=kind.asc,distinct_users.desc&limit={int(limit)}")
    if statuses:
        url += f"&status=in.({','.join(statuses)})"
    status, js = _http("GET", url, _auth_headers(c, token, {"Accept": "application/json"}))
    return js if status == 200 and isinstance(js, list) else []


def set_candidate_status(cand_id: str, new_status: str) -> bool:
    """후보 상태 전이(승인 active / 반려 rejected / 문맥의존 context_dependent / 보류 pending).

    큐레이터만(RLS UPDATE). decided_by/decided_at 기록. 성공 시 True.
    """
    if not available() or new_status not in (
            "pending", "active", "rejected", "context_dependent"):
        return False
    c = _cfg()
    token = auth.access_token()
    if not token:
        return False
    body = {"status": new_status, "decided_at": _now_iso()}
    u = auth.current_user()
    if u and u.get("uid"):
        body["decided_by"] = u["uid"]
    url = f"{c['url']}/rest/v1/userdict_candidates?cand_id=eq.{cand_id}"
    status, _ = _http(
        "PATCH", url,
        _auth_headers(c, token, {"Content-Type": "application/json",
                                 "Prefer": "return=minimal"}),
        body)
    return status in (200, 204)


def aggregate() -> dict:
    """서버 집계(events→candidates) 트리거. 반환 {pairs, exceptions}. 실패 시 {}."""
    if not available():
        return {}
    c = _cfg()
    token = auth.access_token()
    if not token:
        return {}
    status, js = _http(
        "POST", f"{c['url']}/rest/v1/rpc/userdict_aggregate_candidates",
        _auth_headers(c, token, {"Content-Type": "application/json"}), {})
    if status == 200:
        if isinstance(js, list) and js:
            return js[0]
        if isinstance(js, dict):
            return js
    return {}


def build_snapshot() -> dict:
    """승인(active) 후보로 스냅샷 배포. 반환 {ver, pairs, exceptions, sha}. 실패 시 {}."""
    if not available():
        return {}
    c = _cfg()
    token = auth.access_token()
    if not token:
        return {}
    status, js = _http(
        "POST", f"{c['url']}/rest/v1/rpc/userdict_build_snapshot",
        _auth_headers(c, token, {"Content-Type": "application/json"}), {})
    if status == 200:
        if isinstance(js, list) and js:
            return js[0]
        if isinstance(js, dict):
            return js
    return {}


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except Exception:
            pass
    print("userdict_sync 상태:", status())
    if available():
        print("동기화 실행:", sync(logger=print))
    else:
        print("(email/password 미설정 — 동기화 비활성. config.ini [SUPABASE] 설정 필요)")
