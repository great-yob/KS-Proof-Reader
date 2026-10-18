---
name: release
description: KS-Proof Reader 배포 릴리스를 중간 확인 없이 끝까지 완주한다 — release.py preflight(골드셋·Phase E·적용 경로 E2E·시크릿·.iss) → 버전·노트·커밋 → build_dist.py 빌드 → postbuild(평문 키·기동·설치 파일 백업) → GitHub Releases 업로드 → postrelease(업데이터 인식) → 구버전 정리. 성공 시 출력은 완료 문구와 링크 2줄뿐이며, 오류·확인 필요 사항·리마인드만 추가로 보고한다. 사용자가 "릴리스", "배포", "새 버전 올려줘", "/release" 라고 할 때 사용.
user-invocable: true
---

# /release — 배포 릴리스 수행

저장소 `great-yob/KS-Proof-Reader`(**public**)의 릴리스 전 과정이다. 결정론 점검은 전부
[release.py](release.py)에 있고, 이 문서는 **순서와 판단 규칙**만 담는다. 각 규칙의 근거·사고 경위·
실측은 [REFERENCE.md](REFERENCE.md) — 단계를 빼거나 바꾸고 싶어지면 먼저 그것을 읽는다.

| 채널 | 태그 | 자산 | 언제 |
|---|---|---|---|
| 앱 | `v{APP_VERSION}` | `KS-AI-Editor-Setup-{ver}.exe` + `KS-AI-Editor-{ver}-app.zip` | 코드가 바뀌었을 때 |
| 데이터 | `data-{DATA_VERSION}` | `KS-AI-Editor-data-{YYYY.MM}.zip` | 사전이 갱신됐을 때 |

모든 명령은 레포 루트에서 Bash로 실행한다(Bash 호출마다 셸 변수가 이어지지 않으니 명령을 매번 전체로 쓴다).
전제 도구: Inno Setup 6, 32비트 Python 3.11 + pywin32 + pyinstaller, `gh` 로그인, 중의성 데이터셋
(`KS_AMBIG_DATA`), E2E 원고(`KS_E2E_HWP` 또는 `교정샘플/05.hwp`).

## 실행 원칙

- **완주한다.** "릴리스" 지시가 끝까지 할 권한이다. "빌드할까요?" 같은 진행 승인은 묻지 않는다.
- 애매하면 **더 안전한 쪽**: 채널이 애매하면 앱 채널, 버전 폭이 애매하면 PATCH(새 기능·동작 변경만 MINOR).
- **멈추는 경우는 점검 실패(✗)뿐이다** — 즉시 보고하고 중단한다. 실패를 안고 진행하거나 점검을
  느슨하게 고쳐 통과시키지 않는다. 판단 필요(△)는 아래 규칙대로 **스스로 판단해** 처리한다.
- **출력은 최소.** 통과한 점검·중간 수치는 침묵한다. 내보내는 것은 ① 실패와 근거(로그 원문)
  ② 사용자 확인이 꼭 필요한 정책 판단 ③ 다음 작업에 영향을 주는 리마인드(필요할 때만)뿐.
- 승인이 필요한 파괴적 행위는 없다(설치 검증은 폐기됐다 — REFERENCE.md).

## 0. 자동 승인 잠금 — 다른 무엇보다 먼저

토씨 하나 바꾸지 말 것(허용 규칙이 이 문자열과 정확히 일치할 때만 통과한다):

```bash
touch .claude/.release-autoapprove
```

성공·실패·중단 **어느 경로든** 마지막에 `rm -f .claude/.release-autoapprove`. 잠금은 승인 창만 없앤다 —
아래 '하지 말 것'은 그대로이고, 사용자가 중간에 막아 줄 수 없으므로 더 엄격히 지킨다.

## 1. 사전 점검

```bash
.venv64/Scripts/python.exe .claude/skills/release/release.py preflight
```

워킹트리(깨끗해야 함)·채널 판별(`CHANNEL` 줄)·시크릿 감사·`.iss` 8항목·norm_map·**골드셋(항상, 22초)**·
Phase E·**적용 경로 E2E**(런타임 코드가 바뀌었을 때, 약 80초)를 돈다. 마지막 줄이 판정이다.

- `PREFLIGHT OK` → 2단계.
- `PREFLIGHT FAIL` → ✗ 줄을 보고하고 중단. 단, ✗가 **`[E 발화증가]`뿐**이면 증가분을 전수 확인한다
  (`eval/ambiguity_scan`으로 수정 전/후 신규 발화를 하나씩 본다). 원고가 실제로 틀린 탐지면
  `run_goldset.py --save-ambig-baseline` 후 preflight 재실행, 과교정이면 중단·보고.
- `PREFLIGHT REVIEW` → △ 줄마다:
  - **바뀐 문서 확인** — 그 문서들의 diff에 고객사명·원고 제목·원고 파일명이 없는지 본다. 있으면 중단.
  - **Phase E 기준선이 낡았다**(`✅ 개선`) — 이번 변경으로 오발화가 준 것이 설명되면
    `.venv64/Scripts/python.exe eval/ai_goldset/run_goldset.py --save-ambig-baseline`.
  - **E2E 수치가 기준선과 다르다** — 이번 릴리스의 결정론 계층 변경으로 설명되면
    `.venv64/Scripts/python.exe eval/e2e_apply/run_e2e.py --save-baseline`. 설명되지 않으면 중단·보고.
  - 기준선을 갱신했으면 preflight를 **다시 돌려 OK를 확인**하고, 건수·사유를 3단계 커밋 메시지에 남긴다
    (두 기준선 파일 모두 레포 밖이라 커밋 메시지가 유일한 기록이다).
- 채널: 커밋이 있으면 앱, DB `meta.data_version`이 최신 `data-*` 태그보다 크면 데이터, 둘 다면 둘 다.

## 2. 버전 · 릴리스 노트

- 앱: `version.py`의 `APP_VERSION`을 올린다(수정=PATCH, 기능=MINOR). 직전 태그가 이미 있는 번호는 쓰지 않는다
  — 같은 번호면 그 버전 사용자에게 업데이트가 뜨지 않는다.
- 데이터: `DATA_VERSION`을 DB `meta.data_version`과 일치시킨다(정본은 DB 쪽).
- 노트: [notes-template.md](notes-template.md)를 복사해 직전 태그 이후 커밋으로 채운다(사용자 말로 ·
  내부 정리는 생략 · 고객 원고명 금지 · 템플릿의 주석은 지운다). 파일은 레포 밖(스크래치패드)에 둔다.

```bash
.venv64/Scripts/python.exe .claude/skills/release/release.py notes <노트.md>        # 제목·SmartScreen 안내 검사 + 업데이트 창에 보일 요약 출력
```

요약 줄이 이번 릴리스의 가장 중요한 변경을 말하지 않으면 노트 첫 불릿을 고친다.
그다음 `version.py`를 커밋한다(공동저자 트레일러, 기준선을 갱신했으면 그 기록 포함).

## 3. 빌드

```bash
.venv64/Scripts/python.exe build_dist.py --clean
.venv64/Scripts/python.exe .claude/skills/release/release.py postbuild {ver}            # 데이터 채널도 내면 --data
```

빌드 로그의 `✗`는 verify 실패다 — 업로드하지 않는다. postbuild는 평문 키 잔존·자산 3종(Inno가 없으면
설치 파일만 조용히 빠진다)·dist 트리 기동 20초(**설치하지 않는다**)·설치 파일 백업(`Work Utility\백업\`)을
확인한다. ⚠ 폴더·EXE는 공백형 `KS-AI Editor`, 자산 파일명만 하이픈형 `KS-AI-Editor-…`.

## 4. 업로드

```bash
gh auth status            # 로그인 안 돼 있으면 사용자에게 `gh auth login`을 요청(대화형 — 대신 못 한다)
git push origin main
gh release create v{ver} --title "KS-AI Editor v{ver}" --notes-file <노트.md> --target main \
  "dist/release/KS-AI-Editor-Setup-{ver}.exe" "dist/release/KS-AI-Editor-{ver}-app.zip"
```

데이터 채널이면 태그 `data-{YYYY.MM}`(제목 "사전 데이터 {YYYY.MM}")에 data zip 하나만 올린다.
제목·자산은 제품명 KS-AI Editor, 저장소·태그는 프로젝트명 KS-Proof-Reader다. 토큰을 요구하지 않는다
(gh를 못 쓸 때의 폴백은 REFERENCE.md).

## 5. 검증 · 구버전 정리

```bash
.venv64/Scripts/python.exe .claude/skills/release/release.py postrelease {ver}          # 데이터 채널도 냈으면 --data
.venv64/Scripts/python.exe .claude/skills/release/release.py prune {ver}                # 데이터 채널이면 추가로: .venv64/Scripts/python.exe .claude/skills/release/release.py prune {YYYY.MM} --channel data
```

postrelease: 비인증 API 200(비공개면 업데이터가 죽는다) · 자산 `uploaded` · 구버전인 척한 업데이터가 새
릴리스를 본다 · 현재 버전은 최신 · **개발 PC 설치본 버전이 새 릴리스를 잡는다**(실기 업데이트 기준선).
prune: 같은 채널의 다른 릴리스를 지운다 — 설치 파일이 백업에 없으면 먼저 받아 채우고, 태그는 남긴다.
prune은 postrelease가 통과한 뒤에만 돌린다.

## 6. 마무리

- `rm -f .claude/.release-autoapprove`
- 데이터 릴리스였다면 `version.py`의 `DATA_VERSION` = DB `meta.data_version`(어긋날 때만 보고).
- 폴백으로 토큰을 받았다면 폐기를 안내한다.

**성공 시 출력은 정확히 이 2줄이다** — 자산·크기·통과한 점검을 덧붙이지 않는다. 리마인드가 있으면 그 뒤에 짧게.

```
릴리스 완료했습니다.
https://github.com/great-yob/KS-Proof-Reader/releases/tag/v{APP_VERSION}
```

## 하지 말 것

- **설치 파일을 이 PC에 설치하거나 `unins000.exe` 실행** — AppId가 고정이라 테스트 설치가 곧 실사용
  설치본 업그레이드이고, 이어지는 제거가 그것을 지운다(2026-07-23 실사고). 그 설치본은 자동 업데이트의
  유일한 실기 기준선이다. 설치 검증을 되살리지 말 것.
- **점검 실패 상태로 릴리스**, 또는 점검을 느슨하게 고쳐 통과시키기.
- **Phase E FAIL·E2E 변화를 확인 없이 기준선 갱신으로 덮기** — 갱신은 "보고 정당하다고 판정했다"는 서명이다.
- **같은 번호로 다시 릴리스** — 이미 있는 태그를 옮기지 않는다(그 버전 사용자는 업데이트를 못 받는다).
- **설치 파일 백업을 '지우기 직전'으로 미루기**, 백업에 없는 구버전 릴리스 삭제 — 되돌아갈 배포본이 사라진다.
- **`gh release delete --cleanup-tag`** — 태그까지 지운다. 채널을 섞어 지우기(`data-*`·`stdict-*`는 독립 현행).
- **`dist/` 산출물 커밋**, **`--no-zip` 빌드 업로드**.
- **과금·쓰기·개인정보 접근 키를 `collect_keys()`에 추가** — 공개 저장소의 내장 키는 추출 가능하다(CLAUDE.md).
- **고객 식별 정보를 노트·커밋 메시지·문서에 남기기** — 실측은 `실파일A/B/C`.
- **진행 승인을 묻느라 멈추기**, 단계별 진행 상황 늘어놓기, **자동 승인 잠금을 남긴 채 끝내기**,
  잠금을 릴리스 아닌 작업에 쓰기.
- **SKILL.md에 인라인 점검 스크립트를 다시 쌓기** — 스킬 본문은 `/release` 인자가 위치 변수에 치환된 채
  주입된다(2026-08-05 `.iss` 검사 전체가 장식이 된 사고). 점검은 `release.py`에 넣는다.
