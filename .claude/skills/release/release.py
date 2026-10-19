"""
release.py — /release 스킬의 결정론 점검을 한 곳에 모은 도구
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
왜 스크립트인가(2026-10-18):
  · 예전 SKILL.md는 점검 명령 30여 개를 인라인으로 실었고, 스킬 본문은 `/release` 인자가
    `$1`·`$2`에 **치환된 채** 주입된다 — 2026-08-05에 `.iss` 검사 8줄이 전부 같은 문자열을
    검사하는 장식이 됐다. 파일에 든 코드는 치환되지 않는다.
  · 손으로 관리하던 판별 목록(GUARD·DET)이 모듈을 놓쳤다(2026-10-16 감사: 4개). 판정을
    **실행 결과**로 한다 — 골드셋은 22초라 항상 돌리고, Phase E 기준선이 낡았는지는
    골드셋 출력의 '✅ 개선' 줄이 말해 준다.
  · 실원고 코퍼스 재생(2026-10-19)은 eval/corpus_harness/run_corpus.py gate가 판정하고 여기선
    종료 코드만 해석한다(점검 로직을 두 곳에 두지 않는다).

사용(레포 루트에서, .venv64 파이썬으로):
  release.py preflight                    점검 전부 → 채널·판정
  release.py notes FILE                   릴리스 노트 검사 + 업데이트 창에 보일 요약
  release.py postbuild VER [--data]       빌드 산출물·평문 키·기동·설치 파일 백업
  release.py postrelease VER [--data]     공개 API·업로드 상태·업데이터 인식
  release.py prune VER [--channel app|data]   같은 채널 구버전 릴리스 삭제(백업 확인 후)

종료 코드: 0 통과 · 1 실패(릴리스 중단) · 2 사람 판단 필요(출력의 △ 줄)
출력 규약: ✔ 통과 · ✗ 실패 · △ 판단 필요. 마지막 줄이 판정이다.
"""
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PY = sys.executable
ASSET_PREFIX = "KS-AI-Editor"
OWNER_REPO = "great-yob/KS-Proof-Reader"
BACKUP_DIR = REPO.parent / "백업"
LOCK = ".claude/.release-autoapprove"
ISS = REPO / "installer" / "KS-Proof-Reader.iss"

# 추적되면 안 되는 파일(공개 저장소 — 유출은 되돌릴 수 없다)
_FORBIDDEN_TRACKED = re.compile(r"config\.ini|key\.txt|_org_keys|교정샘플|\.hwpx?$|\.db$", re.I)
# ⚠ 접두사만으로 걸지 말 것 — 이 줄(과 문서) 자체가 걸린다. 뒤에 실제 토큰 글자가 이어져야 한다.
_KEY_PATTERN = r"AIza[0-9A-Za-z_-]{30}|ghp_[0-9A-Za-z]{30}|github_pat_[0-9A-Za-z_]{20,}"
# 런타임 코드 — 바뀌면 적용 경로 E2E를 돌린다(평가·빌드·문서만 바뀐 릴리스는 생략)
_RUNTIME = re.compile(r"^(?!eval/|tools/|installer/|docs/|server/|\.claude/|build_dist\.py$"
                      r"|setup_dict\.py$|update_\w+\.py$|build_\w+\.py$).*\.py$")
# `.iss` 정적 불변식 — 옛 '설치해 보는 검증'이 실제로 지키던 것 전부(REFERENCE.md §설치 검증)
_ISS_CHECKS = (
    ("AppId 고정(업그레이드를 같은 제품으로 인식)", "AppId={{32456C4A-71D7-46B8-A59C-60B51DD21734}"),
    ("설치 경로=LOCALAPPDATA\\Programs(업데이터 robocopy 전제)", "DefaultDirName={localappdata}\\Programs\\"),
    ("경로 선택 페이지 비활성(경로 이탈 방지)", "DisableDirPage=yes"),
    ("관리자 권한 불필요(UAC 없음)", "PrivilegesRequired=lowest"),
    ("[Files] dist 트리 통째 복사", 'Source: "{#SrcDir}\\*"; DestDir: "{app}"'),
    ("[Files] 하위 폴더 포함(bridge32/data)", "recursesubdirs"),
    ("[UninstallDelete] {app} 정리", 'Type: filesandordirs; Name: "{app}"'),
)

_fail = 0
_review = 0


def ok(msg):
    print(f"  ✔ {msg}")


def bad(msg):
    global _fail
    _fail += 1
    print(f"  ✗ {msg}")


def judge_(msg):
    global _review
    _review += 1
    print(f"  △ {msg}")


def finish(name: str) -> int:
    if _fail:
        print(f"{name} FAIL — {_fail}건(릴리스 중단)")
        return 1
    if _review:
        print(f"{name} REVIEW — 판단 필요 {_review}건")
        return 2
    print(f"{name} OK")
    return 0


def sh(*cmd, check=False, timeout=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check, timeout=timeout)


def last_tag(prefix: str) -> str:
    r = sh("git", "describe", "--tags", "--abbrev=0", "--match", f"{prefix}*")
    return r.stdout.strip()


def db_data_version() -> str:
    try:
        c = sqlite3.connect(REPO / "data" / "stdict.db")
        row = c.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()
        return (row or [""])[0] or ""
    except Exception:
        return ""


# ══════════════════════════════════════════════════════
# ▌preflight
# ══════════════════════════════════════════════════════
def preflight() -> int:
    print("[점검] 워킹트리·채널")
    dirty = [ln for ln in sh("git", "status", "--porcelain").stdout.splitlines()
             if ln[3:].strip().strip('"') != LOCK]
    if dirty:
        bad(f"커밋되지 않은 변경 {len(dirty)}개 — 릴리스는 커밋된 코드로만 한다: "
            + ", ".join(ln[3:] for ln in dirty[:6]) + (" …" if len(dirty) > 6 else ""))
    else:
        ok("워킹트리 깨끗함")
    tag = last_tag("v")
    commits = sh("git", "log", "--oneline", f"{tag}..HEAD").stdout.strip().splitlines() if tag else []
    changed = sh("git", "diff", "--name-only", f"{tag}..HEAD").stdout.split() if tag else []
    dtag = last_tag("data-")
    dbv = db_data_version()
    app_ch = bool(commits)
    data_ch = bool(dbv) and (not dtag or dbv > dtag[len("data-"):])
    chans = [c for c, on in (("app", app_ch), ("data", data_ch)) if on]
    print(f"  · 직전 앱 태그 {tag or '없음'} 이후 커밋 {len(commits)}개 · 바뀐 파일 {len(changed)}개")
    print(f"  · DB data_version {dbv or '?'} · 직전 데이터 태그 {dtag or '없음'}")
    print(f"CHANNEL {' + '.join(chans) or '없음'}")
    if not chans:
        bad("릴리스할 변경이 없다(커밋 0 · 데이터 버전 동일)")

    print("[점검] 공개 저장소 유출")
    tracked = [p for p in sh("git", "ls-files").stdout.splitlines() if _FORBIDDEN_TRACKED.search(p)]
    if tracked:
        bad("추적되면 안 되는 파일: " + ", ".join(tracked[:8]))
    else:
        ok("비밀·고객 원고·DB 파일 미추적")
    hits = sh("git", "grep", "-lE", _KEY_PATTERN).stdout.split()
    if hits:
        bad("키 모양 문자열이 커밋돼 있다: " + ", ".join(hits))
    else:
        ok("키 모양 문자열 없음")
    docs = [p for p in changed if p.endswith(".md")]
    if docs:
        judge_(f"바뀐 문서 {len(docs)}개에 고객사명·원고 제목이 없는지 눈으로 확인"
               f"(실측 표는 실파일A/B/C): " + ", ".join(docs[:10]))

    print("[점검] 설치 파일(.iss) 정적 불변식")
    iss = ISS.read_bytes()
    text = iss.decode("utf-8-sig", errors="replace")
    n_ok = 0
    for label, pat in _ISS_CHECKS:
        if pat in text:
            n_ok += 1
        else:
            bad(f".iss: {label} — '{pat}' 없음")
    if iss[:3] == b"\xef\xbb\xbf":
        n_ok += 1
    else:
        bad(".iss: UTF-8 BOM 없음(Inno가 ANSI로 읽어 한글이 깨진다)")
    if n_ok == len(_ISS_CHECKS) + 1:
        ok(f".iss 불변식 {n_ok}/{n_ok}")

    print("[점검] 사전 DB")
    try:
        n = sqlite3.connect(REPO / "data" / "stdict.db").execute(
            "SELECT COUNT(*) FROM norm_map").fetchone()[0]
        (ok if n >= 10000 else bad)(f"norm_map {n:,}행")
    except Exception as e:
        bad(f"norm_map 조회 실패: {e}")

    print("[점검] 골드셋(결정론 전 Phase)")
    t0 = time.time()
    g = subprocess.run([PY, "eval/ai_goldset/run_goldset.py"], cwd=REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = g.stdout + g.stderr
    phase_lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("→")]
    fails = [ln.strip() for ln in out.splitlines() if "✗ FAIL" in ln or "❌" in ln]
    if g.returncode != 0 or fails:
        bad(f"골드셋 실패(exit {g.returncode}):")
        for ln in fails[:20]:
            print(f"      {ln}")
    else:
        ok(f"골드셋 통과 — Phase {len(phase_lines)}개 · {time.time() - t0:.0f}초")
    e_line = next((ln.strip() for ln in out.splitlines() if "× finder — 발화" in ln), "")
    if "Phase E" not in out or "KS_AMBIG_DATA 미설정" in out or "베이스라인 없음" in out:
        bad("Phase E가 돌지 않았다 — 중의성 데이터셋(KS_AMBIG_DATA)이 있는 PC에서 릴리스")
    else:
        better = [ln.strip() for ln in out.splitlines() if "✅ 개선" in ln]
        if better:
            judge_(f"Phase E 기준선이 낡았다({e_line}) — 발화가 준 finder {len(better)}종. "
                   "줄어든 게 정당하면 `run_goldset.py --save-ambig-baseline` 후 다시 preflight, "
                   "커밋 메시지에 건수·사유 기록:")
            for ln in better:
                print(f"      {ln}")
        elif e_line:
            ok(f"Phase E 기준선 최신 — {e_line}")

    runtime = [p for p in changed if _RUNTIME.match(p)]
    print("[점검] 적용 경로 E2E(실제 한/글)")
    if not runtime:
        ok("런타임 코드 변경 없음 — E2E 생략")
    else:
        t0 = time.time()
        e = subprocess.run([PY, "eval/e2e_apply/run_e2e.py"], cwd=REPO, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lines = [ln for ln in (e.stdout or "").splitlines() if ln.strip()]
        for ln in lines:
            if ln.startswith("[E2E]") or "FAIL" in ln or "변화" in ln:
                print(f"      {ln.strip()}")
        if e.returncode == 0:
            ok(f"E2E 통과 — {time.time() - t0:.0f}초")
        elif e.returncode == 2:
            judge_("E2E 수치가 기준선과 다르다 — 결정론 계층을 고친 릴리스면 정당할 수 있다. "
                   "확인 후 `eval/e2e_apply/run_e2e.py --save-baseline`")
        elif e.returncode == 4:
            bad("E2E 원고가 없다 — KS_E2E_HWP 또는 교정샘플/05.hwp가 있는 PC에서 릴리스")
        else:
            bad(f"E2E 실패(exit {e.returncode})" + ("" if lines else f": {(e.stderr or '')[-400:]}"))

    # 실원고 코퍼스 재생 게이트 — 골드셋(문장 단위)이 못 보는 계층 간 간섭·손실을 판정해 둔
    #   카드 집합과의 차이로 잡는다(REFERENCE.md §코퍼스 재생 게이트). 사전 데이터도 카드를 바꾼다.
    print("[점검] 실원고 코퍼스 재생(AI 켬·끔)")
    if not runtime and not data_ch:
        ok("런타임 코드·사전 데이터 변경 없음 — 코퍼스 재생 생략")
    else:
        t0 = time.time()
        c = subprocess.run([PY, "eval/corpus_harness/run_corpus.py", "gate"], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lines = [ln.rstrip() for ln in (c.stdout or "").splitlines() if ln.strip()]
        for ln in lines:
            print(f"      {ln.strip()}" if ln.startswith("[GATE]") else f"      {ln}")
        if c.returncode == 0:
            ok(f"코퍼스 카드 기준선과 같음 — {time.time() - t0:.0f}초")
        elif c.returncode == 2:
            judge_("코퍼스 카드가 판정해 둔 기준선과 다르다 — 바뀐 카드를 **한 장씩** 본다"
                   "(`run_corpus.py diff _gate_ai_base _gate_ai --order` · `_gate_det_base _gate_det`). "
                   "전부 이번 변경으로 설명되고 옳으면 `run_corpus.py gate --save-baseline` 후 다시 "
                   "preflight, 커밋 메시지에 건수·사유 기록")
        elif c.returncode == 4:
            bad("코퍼스 원고 캐시가 없다 — 교정샘플/.corpus가 있는 PC에서 릴리스")
        else:
            bad(f"코퍼스 재생 실패(exit {c.returncode})"
                + ("" if lines else f": {(c.stderr or '')[-400:]}"))
    return finish("PREFLIGHT")


# ══════════════════════════════════════════════════════
# ▌notes — 업데이트 창은 '변경 내역' 제목 아래 첫 블록을 요약한다
# ══════════════════════════════════════════════════════
def notes(path: str) -> int:
    sys.path.insert(0, str(REPO))
    from ui.widgets.update_dialog import _CHANGE_HEAD, _summarize_notes
    body = Path(path).read_text(encoding="utf-8")
    heads = [ln for ln in body.splitlines() if ln.strip().startswith("#")]
    if not any(_CHANGE_HEAD.search(h) for h in heads):
        bad("'## 변경 사항' 같은 변경 내역 제목이 없다 — 업데이트 창이 설치 안내를 요약한다")
    else:
        ok("변경 내역 제목 있음")
    if "SmartScreen" not in body or "추가 정보" not in body:
        bad("SmartScreen 안내(추가 정보 → 실행)가 없다")
    else:
        ok("SmartScreen 안내 있음")
    if re.search(r"교정샘플|\.hwpx?\b", body):
        bad("원고 파일명/경로로 보이는 문자열 — 공개 노트에 고객 원고명 금지")
    summary = _summarize_notes(body)
    print(f"  · 업데이트 창 요약: {summary or '(비어 있음)'}")
    if not summary:
        bad("업데이트 창 요약이 비었다")
    return finish("NOTES")


# ══════════════════════════════════════════════════════
# ▌postbuild
# ══════════════════════════════════════════════════════
def postbuild(ver: str, data: bool) -> int:
    print("[빌드 후]")
    if (REPO / "core" / "_org_keys.py").exists():
        bad("core/_org_keys.py 평문 키가 남았다 — 즉시 삭제하고 원인 확인")
    else:
        ok("평문 키 모듈 없음")
    rel = REPO / "dist" / "release"
    want = [f"{ASSET_PREFIX}-Setup-{ver}.exe", f"{ASSET_PREFIX}-{ver}-app.zip"]
    if data:
        want.append(f"{ASSET_PREFIX}-data-{db_data_version()}.zip")
    for name in want:
        p = rel / name
        if p.is_file() and p.stat().st_size > 10 * 1048576:
            ok(f"{name} ({p.stat().st_size / 1048576:.0f}MB)")
        else:
            bad(f"{name} 없음(Inno Setup 미설치·--no-zip 빌드면 업로드 금지)")
    exe = REPO / "dist" / "KS-AI Editor" / "KS-AI Editor.exe"
    if exe.is_file():
        # ⚠ 설치하지 않는다 — dist 트리에서 바로 띄워 20초 뒤 살아 있는지만 본다
        proc = subprocess.Popen([str(exe)], cwd=exe.parent)
        time.sleep(20)
        alive = proc.poll() is None
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        (ok if alive else bad)(f"빌드본 기동 {'유지' if alive else '실패(20초 안에 종료)'}")
    else:
        bad(f"실행 파일 없음: {exe}")
    setup = rel / want[0]
    if setup.is_file():
        BACKUP_DIR.mkdir(exist_ok=True)
        dst = BACKUP_DIR / setup.name
        shutil.copyfile(setup, dst)
        (ok if dst.stat().st_size == setup.stat().st_size else bad)(f"설치 파일 백업 → {dst}")
    return finish("POSTBUILD")


# ══════════════════════════════════════════════════════
# ▌postrelease
# ══════════════════════════════════════════════════════
def _installed_version() -> str:
    """개발 PC 설치본(자동 업데이트 실기 기준선)의 버전 — 제어판 등록 정보에서."""
    try:
        import winreg
        m = re.search(r"AppId=\{\{([0-9A-F-]+)\}", ISS.read_text(encoding="utf-8-sig"), re.I)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                             f"\\{{{m.group(1)}}}_is1")
        return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except Exception:
        return ""


def postrelease(ver: str, data: bool) -> int:
    print("[릴리스 후]")
    try:
        code = urllib.request.urlopen(
            f"https://api.github.com/repos/{OWNER_REPO}/releases", timeout=20).status
    except Exception as e:
        code = getattr(e, "code", 0)
    (ok if code == 200 else bad)(f"비인증 Releases API {code}(비공개로 바뀌면 업데이터가 죽는다)")
    tags = [f"v{ver}"] + ([f"data-{db_data_version()}"] if data else [])
    for tag in tags:
        r = sh("gh", "release", "view", tag, "--json", "assets")
        try:
            assets = json.loads(r.stdout)["assets"]
        except Exception:
            bad(f"{tag} 조회 실패: {r.stderr.strip()[:200]}")
            continue
        not_up = [a["name"] for a in assets if a.get("state") != "uploaded"]
        if not assets or not_up:
            bad(f"{tag} 자산 미완료: {not_up or '자산 0개'}")
        else:
            ok(f"{tag} 자산 {len(assets)}개 업로드 완료")
    sys.path.insert(0, str(REPO))
    from core import updater as u
    import version as v
    real = u._current
    try:
        u._current = lambda ch: "0.0.1" if ch == "app" else "2000.01"
        got = u.check("app")
        (ok if got and got.get("version") == ver else bad)(
            f"업데이터가 구버전에서 v{ver}를 본다: {got and got.get('version')}")
        if data:
            gd = u.check("data")
            (ok if gd else bad)(f"업데이터가 데이터 릴리스를 본다: {gd and gd.get('version')}")
        u._current = real
        now = u.check_all()
        (ok if now == {"app": None, "data": None} else bad)(f"현재 버전 기준 최신: {now}")
        inst = _installed_version()
        if inst and inst != ver:
            u._current = lambda ch: inst if ch == "app" else v.DATA_VERSION
            gi = u.check("app")
            (ok if gi and gi.get("version") == ver else bad)(
                f"개발 PC 설치본 {inst}이 v{ver} 갱신을 잡는다")
        else:
            judge_(f"개발 PC 설치본 버전 {inst or '알 수 없음'} — 실기 업데이트 기준선 확인 불가")
    finally:
        u._current = real
    return finish("POSTRELEASE")


# ══════════════════════════════════════════════════════
# ▌prune — 채널당 최신 하나만 남긴다(태그는 남긴다)
# ══════════════════════════════════════════════════════
def prune(ver: str, channel: str) -> int:
    keep = f"v{ver}" if channel == "app" else f"data-{ver}"
    prefix = "v" if channel == "app" else "data-"
    r = sh("gh", "release", "list", "--limit", "100", "--json", "tagName")
    try:
        tags = [x["tagName"] for x in json.loads(r.stdout)]
    except Exception:
        bad(f"릴리스 목록 조회 실패: {r.stderr.strip()[:200]}")
        return finish("PRUNE")
    if keep not in tags:
        bad(f"새 릴리스 {keep}가 목록에 없다 — 지우지 않는다")
        return finish("PRUNE")
    old = [t for t in tags if t.startswith(prefix) and t != keep
           and (channel != "app" or re.match(r"^v\d", t))]
    for t in old:
        if channel == "app":
            setup = BACKUP_DIR / f"{ASSET_PREFIX}-Setup-{t[1:]}.exe"
            if not setup.is_file():
                d = sh("gh", "release", "download", t, "--pattern", "*Setup*.exe",
                       "--dir", str(BACKUP_DIR))
                if not setup.is_file():
                    bad(f"{t} 설치 파일 백업 실패 — 삭제하지 않는다: {d.stderr.strip()[:200]}")
                    continue
                print(f"  · {t} 설치 파일을 백업에 채움(레거시 폴백)")
        # ⚠ --cleanup-tag 금지 — 태그는 그 버전이 어느 커밋이었는지의 기록이다
        d = sh("gh", "release", "delete", t, "--yes")
        (ok if d.returncode == 0 else bad)(f"{t} 릴리스 삭제" + ("" if d.returncode == 0
                                                             else f": {d.stderr.strip()[:200]}"))
    after = json.loads(sh("gh", "release", "list", "--limit", "100", "--json",
                          "tagName").stdout or "[]")
    left = [x["tagName"] for x in after if x["tagName"].startswith(prefix)]
    (ok if left == [keep] or (channel == "app" and [t for t in left if re.match(r"^v\d", t)] == [keep])
     else bad)(f"{channel} 채널에 남은 릴리스: {left}")
    return finish("PRUNE")


def main(argv) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    data = "--data" in rest
    pos = [a for a in rest if not a.startswith("--")]
    if cmd == "preflight":
        return preflight()
    if cmd == "notes" and pos:
        return notes(pos[0])
    if cmd == "postbuild" and pos:
        return postbuild(pos[0], data)
    if cmd == "postrelease" and pos:
        return postrelease(pos[0], data)
    if cmd == "prune" and pos:
        ch = rest[rest.index("--channel") + 1] if "--channel" in rest else "app"
        return prune(pos[0], ch)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
