---
name: release
description: KS-Proof Reader 배포 릴리스를 처음부터 끝까지 수행한다 — 사전 점검(골드셋·시크릿 감사) → 버전 올림 → build_dist.py 빌드 → GitHub Releases 업로드 → 업데이터 검증. 사용자가 "릴리스", "배포", "새 버전 올려줘", "/release" 라고 할 때 사용.
user-invocable: true
---

# /release — 배포 릴리스 수행

이 저장소(`great-yob/KS-Proof-Reader`, **public**)의 릴리스 전 과정을 수행한다.
앱과 데이터는 **채널이 분리**돼 있으므로 먼저 무엇을 릴리스할지 판별한다.

| 채널 | 태그 | 자산 | 언제 |
|---|---|---|---|
| 앱 | `v{APP_VERSION}` | `…-Setup-{ver}.exe`(최초설치) + `…-app.zip`(업데이트) | 코드가 바뀌었을 때 |
| 데이터 | `data-{DATA_VERSION}` | `…-data-{YYYY.MM}.zip` | 사전이 갱신됐을 때 |

전제 도구: **Inno Setup 6** (`winget install --id JRSoftware.InnoSetup -e`)와
**32비트 Python 3.11 + pywin32 + pyinstaller**(HWP 브리지 빌드용). 둘 다 없으면
설치 파일이 안 나오거나(경고) 빌드가 중단된다(브리지).

## 0. 무엇을 릴리스할지 판별

```bash
git log --oneline $(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null)..HEAD  # 앱 변경분
.venv64/Scripts/python.exe -c "import sqlite3;print(dict(sqlite3.connect('data/stdict.db').execute('SELECT key,value FROM meta')))"
```
- 코드 커밋이 있으면 → **앱 채널**
- DB의 `meta.data_version`이 최신 `data-*` 릴리스보다 크면 → **데이터 채널**
- 둘 다면 둘 다. 애매하면 사용자에게 물어본다.

## 1. 사전 점검 (실패하면 여기서 멈춘다)

```bash
.venv64/Scripts/python.exe eval/ai_goldset/run_goldset.py
```
**모든 Phase가 통과해야 한다.** 하나라도 실패하면 릴리스하지 말고 원인을 보고한다.
특히 사전 DB를 갱신한 직후라면 `norm_map`이 살아 있는지가 핵심이다
(과거 `update_opendict.py`가 12,730건을 조용히 날린 전례 — 앱은 무오류로 동작한다).

```bash
.venv64/Scripts/python.exe -c "import sqlite3;c=sqlite3.connect('data/stdict.db');print('norm_map',c.execute('SELECT COUNT(*) FROM norm_map').fetchone()[0])"
```

**시크릿·고객정보 감사** — 이 저장소는 **공개**다:
```bash
git status --short
git ls-files | grep -iE "config\.ini|key\.txt|_org_keys|교정샘플|\.hwpx?$|\.db$"   # 결과가 있으면 중단
git ls-files -z | xargs -0 grep -lE "AIza[0-9A-Za-z_-]{30}|ghp_[0-9A-Za-z]{30}|github_pat_"
```
새로 추가된 문서에 **고객사명·원고 제목**이 없는지 확인한다. 실측 표는 `실파일A/B/C` 관례.

## 2. 버전 올림

- **앱**: `version.py`의 `APP_VERSION`을 semver로 올린다(수정=PATCH, 기능=MINOR).
- **데이터**: `version.py`의 `DATA_VERSION`을 DB의 `meta.data_version`과 **일치**시킨다.
  ⚠ 진짜 데이터 버전은 DB `meta` 쪽이다. `build_dist.py`가 불일치 시 경고하고 DB 값을 쓴다.

변경 후 커밋(공동저자 트레일러 포함).

## 3. 빌드

```powershell
.\.venv64\Scripts\python.exe build_dist.py --clean
```
- 산출: `dist/release/` 에 `Setup-{ver}.exe` / `app.zip` / `data.zip`
- 빌드가 `core/_org_keys.py`를 만들었다 **finally에서 지운다** — 끝난 뒤 잔존 여부 확인:
  `test -f core/_org_keys.py && echo "⚠ 평문 키 잔존"`
- 검증 단계가 확인하는 것: `data/stdict.db`·`data/kiwipiepy_model` 존재,
  `_internal/data/stdict.db` **부재**(분리 배포가 깨지지 않았는지),
  `_internal/assets/{icons,fonts,logo}` 존재(없으면 아이콘·폰트가 조용히 사라진다),
  `bridge32/hwp_bridge_worker.exe` 존재 **및 32비트**(PE 헤더). 실패하면 업로드하지 않는다.
- Inno Setup이 없으면 설치 파일만 **조용히 건너뛴다**(빌드는 성공). 로그에서
  `✔ 최초 설치  KS-Proof-Reader-Setup-…exe` 줄을 **눈으로 확인**할 것.

빌드본이 실제로 뜨는지 한 번 확인:
```powershell
$p=Start-Process "dist\KS-Proof Reader\KS-Proof Reader.exe" -PassThru; Start-Sleep 15
$a=Get-Process -Id $p.Id -EA SilentlyContinue; if($a){"OK $($a.MainWindowTitle)"; Stop-Process -Id $p.Id -Force}else{"기동 실패"}
```

**설치 파일 검증** — 무인 설치 → 실행 → 제거까지 한 번 돌린다(사용자가 겪을 경로):
```powershell
$s = Get-ChildItem dist\release\*Setup*.exe | Select-Object -First 1
Start-Process $s.FullName -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait
$app = "$env:LOCALAPPDATA\Programs\KS-Proof Reader\KS-Proof Reader.exe"
Test-Path $app; Test-Path "$env:LOCALAPPDATA\Programs\KS-Proof Reader\bridge32\hwp_bridge_worker.exe"
```
⚠ 브리지가 빠지면 설치는 멀쩡히 되고 **HWP 교정만 죽는다** — 반드시 같이 확인한다.
제거: `& "$env:LOCALAPPDATA\Programs\KS-Proof Reader\unins000.exe" /VERYSILENT`

## 4. GitHub 업로드

**토큰을 요구하지 않는다.** 인증은 이미 PC에 저장돼 있다:

| 작업 | 인증 주체 |
|---|---|
| `git push` | **Git Credential Manager** (`credential.helper=manager`, Windows 자격증명 관리자) |
| 릴리스 생성·업로드·삭제 | **`gh` CLI** (`gh auth login`으로 1회 로그인, OAuth 토큰을 OS가 보관) |

```bash
gh auth status      # 먼저 확인. 로그인돼 있지 않으면 사용자에게 `gh auth login` 실행을 요청한다
git push origin main
```
⚠ `gh auth login`은 브라우저 대화형이라 **에이전트가 대신 못 한다** — 사용자가 직접 1회 실행.
⚠ 토큰을 remote URL에 넣지 말 것(`.git/config`에 평문으로 남는다).
`git remote -v`는 토큰 없는 https만 있어야 한다.

**릴리스 생성 + 자산 업로드 (한 번에)** — 본문은 **마크다운 파일**로 넘긴다.
한글을 셸 인자로 직접 주면 이스케이프가 깨진다(과거 JSON 파일을 쓰던 이유이며,
`--notes-file`이 그 문제를 통째로 없앤다). gh가 Content-Type도 알아서 맞춘다.

```bash
gh release create v{APP_VERSION} \
  --title "KS-Proof Reader v{APP_VERSION}" \
  --notes-file <릴리스노트.md> \
  --target main \
  "dist/release/KS-Proof-Reader-Setup-{ver}.exe" \
  "dist/release/KS-Proof-Reader-{ver}-app.zip"
```
데이터 채널이면 태그 `data-{DATA_VERSION}`에 `…-data-{YYYY.MM}.zip` 하나만 올린다.

릴리스 본문에는 **설치 파일을 받으라고** 안내하고, 서명이 없어 SmartScreen 경고가 뜨니
`추가 정보 → 실행`을 눌러야 한다는 문구를 넣는다.

290MB급이라 수 분 걸린다. 업로드 후 **결과를 눈으로 재조회**할 것:
```bash
gh release view v{APP_VERSION} --json assets \
  --jq '.assets[] | "\(.name) \(.size) \(.state)"'      # state=uploaded 여야 한다
```

<details><summary>gh를 못 쓸 때의 폴백 (curl + 토큰)</summary>

사용자에게 토큰을 요청한다(스코프 `repo`+`workflow`). 한글 본문은 JSON 파일로:
```bash
curl -s -X POST -H "Authorization: token $GH_TOKEN" -H 'Accept: application/vnd.github+json' \
  https://api.github.com/repos/great-yob/KS-Proof-Reader/releases --data-binary @rel.json
curl -s -X POST -H "Authorization: token $GH_TOKEN" -H "Content-Type: application/octet-stream" \
  --data-binary @"dist/release/<파일>" \
  "https://uploads.github.com/repos/.../releases/<id>/assets?name=<파일명>"
```
zip은 `Content-Type: application/zip`, exe는 `application/octet-stream`.
끝나면 **토큰 폐기를 안내**한다.
</details>

## 4-b. 구버전 릴리스 삭제 (정책 — 생략 금지)

새 릴리스가 **검증까지 끝난 뒤**, 같은 채널의 **이전 릴리스를 삭제한다.** 채널당 하나만 남긴다.
결함 있는 구버전을 Releases 페이지에서 직접 받아 가는 경로를 없애기 위함이다
(자동 업데이터는 최신만 제시하므로 기존 사용자에겐 영향이 없다).

⚠ **채널을 섞지 말 것.** 앱 릴리스를 냈다면 `v*` 태그만 지운다 —
`data-*`·`stdict-*`는 독립적으로 살아 있는 현행 릴리스다.

⚠ **git 태그는 지우지 않는다.** 릴리스 삭제는 태그를 건드리지 않으며(둘은 별개),
태그는 그 버전이 어느 커밋이었는지의 기록이라 남긴다.

```bash
gh release list --limit 30                 # 삭제 대상 확인
gh release delete v{옛버전} --yes          # ⚠ --cleanup-tag 쓰지 말 것(태그까지 지운다)
gh release list --limit 30                 # 사라졌는지 재확인
```

⚠ **삭제 직후 비인증 API GET으로 확인하면 캐시된 옛 목록이 그대로 보인다**(실측).
`gh`는 인증 조회라 문제없지만, curl로 확인한다면 인증 헤더를 붙이거나 개별 릴리스
GET(404여야 함)으로 확인할 것.

## 5. 릴리스 후 검증 (생략 금지)

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://api.github.com/repos/great-yob/KS-Proof-Reader/releases
```
비인증 200이어야 한다(비공개로 바뀌면 404 → 업데이터가 죽는다).

업데이터가 새 릴리스를 실제로 보는지 구버전인 척 확인:
```bash
.venv64/Scripts/python.exe -c "
import sys,os; sys.path.insert(0,os.getcwd())
from core import updater as u
u._current = lambda ch: '0.0.1' if ch=='app' else '2000.01'
print(u.check('app')); print(u.check('data'))"
```
그리고 **현재 버전에서는** `check_all()`이 `{'app': None, 'data': None}`이어야 정상(=최신).

## 6. 마무리

- **폴백 경로로 토큰을 받았다면** 폐기를 안내한다(쓰기 권한이 있고 대화에 남는다).
  `gh`로 진행했다면 해당 없음.
- 릴리스 URL과 자산 크기를 보고한다.
- 데이터 릴리스였다면 `version.py`의 `DATA_VERSION`과 DB `meta.data_version`이
  일치하는지 최종 확인.

## 부록 — kiwipiepy(형태소 분석기) 업그레이드

**주기적 갱신 항목이 아니다.** 사전 데이터와 달리 kiwipiepy는 **의존성 업그레이드**이고
`core/morph.py`가 교정 가드 수십 곳에서 쓰므로, 토크나이저 동작이 바뀌면 가드 판정이 흔들린다.
→ 달력이 아니라 **필요할 때만**(버그 수정·정확도 개선이 실익일 때) 올리고,
**반드시 `run_goldset.py` before/after 비교**로 게이트한다. 발화 수가 늘면 되돌린다.
릴리스 이력 참고: 마이너는 대략 분기, 패치는 그 사이(0.22.0 '25-11 → 0.23.0 '26-03 → 0.23.2 '26-06).

⚠ **올릴 때는 `kiwipiepy`와 `kiwipiepy_model`을 같은 마이너로 함께 올린다.**
모델은 앱이 아니라 **데이터 패키지**에 들어가므로:

| 변경 | 릴리스해야 할 것 |
|---|---|
| 패치만(0.23.2 → 0.23.3, 모델 동일) | **앱만** |
| 마이너 이상(0.23.x → 0.24.0, 모델도 바뀜) | **앱 + 데이터 둘 다** |

마이너를 올리면서 앱만 릴리스하면, 사용자는 새 라이브러리 + 옛 모델 조합이 된다.
`datapaths.kiwi_model_ok()`가 마이너 불일치를 감지해 **형태소 분석을 비활성**시키므로
크래시는 안 나지만(과거엔 힙 손상으로 프로세스가 죽었다), 활용형 복원·띄어쓰기 백스톱·
인명 가드가 전부 빠져 **교정 품질이 조용히 무너진다**. `build_dist.py`의 verify가
빌드 시점에 이 불일치를 잡아 실패시킨다.

## 하지 말 것

- **골드셋 실패 상태로 릴리스** — 조용한 교정 품질 저하가 사용자에게 나간다.
- **`dist/` 산출물 커밋** — `.gitignore` 대상. 릴리스 자산으로만 배포한다.
- **`--no-zip`으로 만든 빌드를 업로드** — 자산이 없다.
- **과금·쓰기·개인정보 접근 키를 `collect_keys()`에 추가** — 이 저장소는 공개이고
  내장 키는 추출 가능하다. 무료·읽기전용·폐기가능 키만 허용(CLAUDE.md 참조).
- **고객 식별 정보를 문서에 남기기** — 실측 표는 `실파일A/B/C`.
