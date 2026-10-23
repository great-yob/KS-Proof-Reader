# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **이 파일은 불변식·금지선만 담는다** (2026-10-18 재구성). 근거·실측·함정의 전체 서술은 아래 참조 문서로 **원문 그대로** 옮겼다.
> 각 절 끝의 `→ 상세:` 문서를 **그 영역을 고치기 전에** 읽을 것. 설계를 바꾸면 그 문서와 이 요약을 **함께** 고친다.
>
> | 영역 | 상세 참조 |
> |---|---|
> | 빌드 · 설치 파일 · 버전 · 업데이터 · 데이터 경로 | [docs/build-and-release.md](docs/build-and-release.md) |
> | 사전 DB 유지보수 | [docs/dictionary-data-maintenance.md](docs/dictionary-data-maintenance.md) |
> | HWP 브리지 (32비트 COM) | [docs/hwp-bridge.md](docs/hwp-bridge.md) |
> | 교정 파이프라인 · 사용자 첨부 사전 | [docs/pipeline-reference.md](docs/pipeline-reference.md) |
> | 결과물 · 적용 · 메모 · PDF · 정오표 | [docs/output-and-apply.md](docs/output-and-apply.md) |
> | 설정 · API 키 · 로그인 · 계정 | [docs/config-and-accounts.md](docs/config-and-accounts.md) |
> | UI 셸 · 로그 · 디자인 시스템 · 검토 · 결과 화면 | [docs/ui-reference.md](docs/ui-reference.md) |
> | 릴리스 절차 | `/release` 스킬 ([.claude/skills/release/SKILL.md](.claude/skills/release/SKILL.md)) |

## ⚠ 관련 작업 전 필독 — 메모리·설계도 먼저 읽기

교정교열 파이프라인/엔진/사전/형태소/검수 모드/KoGEC 등 이 앱의 **교정 로직 관련 작업을 시작하기 전에 반드시** 아래를 먼저 읽어 최근 결정과 맥락을 파악할 것:

1. **프로젝트 메모리** — `C:\Users\user9\.claude\projects\c--Users-user9-Desktop-Work-Utility-KS-Proof-Reader\memory\` (인덱스 `MEMORY.md` + 개별 파일). 아키텍처 결정·제거된 로직·의존성·검증 결과가 기록돼 있음.
2. **설계도** — [docs/proofreading-architecture.md](docs/proofreading-architecture.md). 역할 분리 아키텍처(사전=항상-on 인프라, AI=교체형 생성 엔진), 단계별 로드맵, 과교정 억제 전략, 제거된 갈래(KoGEC·KAGEC 주입·검수 모드)와 재도입 조건.

핵심 요약(상세는 위 문서): 사전(stdict.db)은 토글로 끄지 않는 항상-on 검증/가드 인프라이며, 생성은 `core/correction_engine.py`의 `GeminiEngine`(Gemini 단독)이 담당한다. **KoGEC/앙상블/엔진선택은 2026-06-17 전면 제거됨** — 실측에서 KoGEC(NLLB GEC)가 어절 단위 환각·과교정(예: '사회문제에'→'사회문제제에서', '홍보전략을'→'전략을')을 일으켰고 앙상블 합치율이 1/39라 출판 교정 신뢰도에 부적합. **재도입 금지**(검증된 대체 모델·출판 도메인 파인튜닝 전엔). 그 의존성(torch·transformers 등)과 모델 캐시도 2026-10-18 개발 환경에서 삭제했다. "거리 기반 추측 치환"(과거 consistency Case B)도 치명적 오교정으로 **영구 제거**됨 — 재도입 금지. 형태소 분석(kiwipiepy)으로 활용형을 기본형 복원해 사전 검증을 정밀화한다(kiwipiepy는 유지 — KoGEC와 무관).

## Project overview

KS-Proof Reader — Korean-language proofreading tool for HWP (한/글) documents. It uses a modern 3-stage pipeline (Local NIKL Dictionary Screening → Google Gemini AI → Dictionary Re-validation) and writes corrections back into the HWP file via COM automation. Code, comments, and UI strings are predominantly Korean.

⚠ **Product display name vs project/repo name are deliberately split.** The shipped product is **"KS-AI Editor"** — that string is the EXE name, the `dist/` and install folders (`%LOCALAPPDATA%\Programs\KS-AI Editor`), the user-data folder (`%LOCALAPPDATA%\KS-AI Editor`), the window title, and the release-asset prefix. It lives in exactly two constants: `build_dist.APP_NAME` and `datapaths.APP_DIR_NAME` (the `.iss` `[Code]` uninstall path must match the latter). The **project/repo stays "KS-Proof Reader" / `great-yob/KS-Proof-Reader`** — the on-disk repo folder, the GitHub repo, the release tags (`v1.0.1`), and the `.iss` filename. That name is the updater's polling URL, so changing it breaks auto-update; never rename it to match the product. Docs that say "KS-Proof Reader" mean the project.

## Application Architecture

- **v5 (current)** — PySide6 native desktop app. Entry point: `main.py`. UI in `ui/`, engine in `core/`. This is the only codebase; the legacy v4 FastAPI + React webapp (`_archive_v4/`) was removed on 2026-06-17.

## Running the App

```powershell
# v5 desktop (current) — uses the bundled .venv64
.\.venv64\Scripts\python.exe main.py
```

## Building for distribution

```powershell
.\.venv64\Scripts\python.exe build_dist.py --clean      # → dist/KS-AI Editor/ (~540MB, v1.8.5) · 전체 약 2분
```

전제: **Inno Setup 6**(없으면 설치 파일만 경고 후 생략) + **32비트 Python 3.11 + pywin32 + pyinstaller**(없으면 빌드 **실패** — 의도적). 반복 중 플래그: `--skip-bridge` · `--no-installer` · `--no-zip`.

- `--onefile` 금지 — 160MB `stdict.db`가 실행마다 temp로 풀린다.
- ⚠ **저장소·릴리스는 PUBLIC이고, 내장 키는 복호 키와 같은 파일에 실려 누구나 추출할 수 있다**(2026-07-22 수용한 절충). 그러므로 `collect_keys()`에는 **무료·읽기 전용·한도 제한·개별 폐기 가능한 키만** 넣는다. 과금·쓰기·고객/개인정보 접근 키는 서버 프록시로. 빌드가 만든 `core/_org_keys.py`는 `finally`에서 지운다 — 평문이 레포에 남으면 안 된다.
- ⚠ **고객 식별 정보 커밋 금지** — `교정샘플/`은 gitignore, 실측 표의 원고는 `실파일A/B/C`로 익명화한다.
- 번들에서 비밀·고객 데이터를 빼고 사후 검증한다 — **레포 루트를 통째로 포함하는 규칙 금지**. `torch`·`transformers`·`tokenizers`·`huggingface_hub` exclude는 KoGEC 잔재 방지용 안전망으로 남긴다.
- 빌드는 어느 셸에서 해도 같은 번들이 나와야 한다 — `build_dist._build_env()`가 PyInstaller에 넘기는 PATH에서 Git·MSYS 경로를 뺀다(Git Bash에서 Git의 OpenSSL DLL이 실렸던 사고). `verify()`가 그 DLL을 발견하면 빌드를 무효로 본다.
- 산출물은 `dist/release/`에 3종: `…-Setup-{ver}.exe`(최초 설치) · `…-{ver}-app.zip`(앱 채널) · `…-data-{YYYY.MM}.zip`(데이터 채널). 앱과 데이터는 **채널이 분리**돼 있다.
- 설치 파일은 **사용자 전용** `%LOCALAPPDATA%\Programs\KS-AI Editor`, `PrivilegesRequired=lowest`, `DisableDirPage=yes`. ⚠ Program Files 금지 — `updater.install_app()`의 robocopy가 권한 때문에 죽는다. `AppId`(고정 GUID) 변경 금지 — 업그레이드가 별개 제품이 된다. `.iss`는 **UTF-8 with BOM** 유지. 서명이 없어 SmartScreen 경고가 뜬다.
- 데이터(`stdict.db`·`eomun.db`·`userdict.db`·`kiwipiepy_model/`)는 **EXE 옆 `data/`**에 둔다. `_internal/`에 넣으면 교체할 수 없다(`verify()`가 막는다).
- ⚠ **`Kiwi(model_path=…)`를 검증 없이 부르지 말 것** — 모델이 없거나 어긋나면 네이티브 힙 손상(`0xC0000374`)으로 프로세스가 죽고 `try/except`로 못 막는다. 항상 `datapaths.kiwi_model_ok()`를 거친다. kiwipiepy는 주기 갱신 대상이 아니라 의존성 업그레이드다 — 골드셋으로 게이트하고, **마이너가 바뀌면 앱과 데이터를 함께 릴리스**한다(`requirements.txt` 고정값도 같이).
- 버전 둘(`version.py`): `APP_VERSION`(semver) · `DATA_VERSION`(`YYYY.MM`)은 서로 독립이다. 데이터 버전의 정본은 DB `meta.data_version`(`nikl_dict.data_version()`). 태그 규약: `v1.0.0` · `data-2026.07` · `stdict-2026.07`.
- 업데이터: **확인은 자동, 설치는 절대 자동이 아니다**(교정 중 강제 재시작은 작업을 날린다). 설치를 부르는 곳은 `MainWindow._on_update_apply` 하나뿐이고, 교정 중에는 거부한다. `install_app`가 True를 돌려주면 창은 반드시 `close()`해야 한다.
  - ⚠⚠ 헬퍼 `.bat`의 `rmdir`은 **그 업데이트의 `mkdtemp` 작업 폴더만** 지운다. `%TEMP%` 전체를 지운 사고(2026-10-16)를 되풀이하지 말 것. `discard_download`도 `gettempdir()` 바로 아래 `ksproof-upd-*`만 지운다.
  - 실행 중에는 데이터 폴더를 교체할 수 없다. 그래서 `data.new`에 `_PENDING_READY`를 찍어 보류하고, `main.py`가 **DB를 열기 전에** `apply_pending_data()`를 부른다. `data_dir()`는 `meta.data_version`이 가장 큰 후보를 고른다("먼저 있는 것" 규칙 금지). `install_data`는 dev에서 `dest` 없이 거부한다.
  - 확인 워커는 `_cleanup_worker`로 보내지 말 것(죽은 연결에서 5초 정지). 다운로드 워커는 Esc 때 **실행 중인 QThread를 파괴하지 말 것** — `_ORPHANS`로 떼어 둔다. 파괴하면 앱 전체가 `0xC0000409`로 죽는다.
  - 업데이트 안내의 변경 요약(`_summarize_notes`)은 릴리스 본문에서 **변경 내역 제목을 찾아** 만든다. 본문이 설치 안내로 시작하므로 "앞 몇 줄 자르기"로 되돌리지 말 것.
- 데이터 패키지 DB는 **전부 `datapaths`를 거쳐** 찾는다(하드코딩 경로 금지 — 인앱 데이터 업데이트가 닿지 않는다). `datapaths`는 의존성 없는 최상위 모듈로 유지한다(`core` import 금지). 런타임 쓰기는 `cache_dir()`에 한다. 예외 둘은 `data/event_queue.db`·`data/.ks_session`(EXE 옆 — 옮기려면 기존 사용자 이전이 필요해 보류). frozen일 때 `config.ini`는 EXE 옆과 사용자 폴더를 함께 읽고, 쓰기는 사용자 폴더에만 한다.

→ 상세: [docs/build-and-release.md](docs/build-and-release.md)

## Dictionary data maintenance

- `data/stdict.db`의 두 반쪽은 **다른 스크립트로** 따로 갱신한다: 표준국어대사전(`source='stdict'`)은 CI의 `build_stdict_part.py` → `update_stdict.py`(반기·자동), 우리말샘(`source='opendict'`)은 공식 전체 내려받기 → `update_opendict.py`(수동). 한쪽을 다시 만들면서 다른 쪽을 덮지 말 것.
- `setup_dict.py`는 **부트스트랩 전용**이다 — 기존 DB는 `--force` 없이 거부한다(`norm_map`·meta·공식 export 반쪽이 사라진다).
- ⚠ 같은 파일에 있는 `norm_map`은 조용히 사라지기 쉽다(규범표기 교정 전멸·오류 0). **DB를 갱신한 뒤에는 반드시 `eval/ai_goldset/run_goldset.py`를 돌린다.**
- 데이터 릴리스 주기는 반기 수준이다(월·분기 갱신은 실익이 없다 — 실측 근거는 문서에).

→ 상세: [docs/dictionary-data-maintenance.md](docs/dictionary-data-maintenance.md)

## Architecture you must understand before editing

### The 64-bit ↔ 32-bit HWP bridge (critical)

- 한/글 COM(`HWPFrame.HwpObject`)은 **32비트 전용**이다. 64비트 앱이 32비트 워커([core/hwp_bridge_worker.py](core/hwp_bridge_worker.py))를 subprocess로 띄우고 줄 단위 JSON(stdin/stdout)으로 주고받는다. **서브프로세스 제거 금지.** 바꿀 때는 양쪽과 프로토콜을 함께 맞춘다.
- 배포본에는 `bridge32/`(32비트 PE · `--console` 필수 — stdout이 곧 프로토콜 · `pyhwpx` exclude)가 실려야 하고, `verify()`가 이를 지킨다. frozen 워커는 gen_py 재생성을 끈다(stdout 오염).
- ⚠ `SaveAs`/PDF 내보내기는 **예외가 아니라 반환값으로 실패**한다. 반환값과 파일 스탬프를 **둘 다** 확인하고, `close`는 `Clear(1)` 뒤 `Quit()`를 부른다. 이걸 안 하면 원본을 덮어쓰는 '저장할까요?' 모달이 뜬다.
- 매치 상한은 **실제로 치환·표시한 매치만** 센다(건너뛴 매치를 세면 뒤쪽 등장이 조용히 미적용). 긴 작업에는 heartbeat를 보낸다.
- ★**모달 숨김 교착**(2026-08-21 실사고): `_WindowHider`는 한/글 모달까지 숨긴다. 보안 모듈 등록 위치는 `HKCU\Software\HNC\HwpAutomation\Modules\FilePathCheckerModule`이다(COM ProgID·`regsvr32` 아님 · 관리자 권한 불필요). `_StallWatch` 경고는 **1회만** 낸다(반복하면 부모의 유휴 타임아웃이 영원히 리셋된다). 단계 로그는 **호출 전에** 남긴다. 사용자가 봐야 할 라인은 `_DROP` 금칙어(`[Worker stderr]`·`Dispatch`·`[보안]`)를 피하고 전용 `[태그]`를 단다. 현장 진단: `KS_HWP_VISIBLE=1`, [tools/hwp_bridge_diag.bat](tools/hwp_bridge_diag.bat).
- `HwpEditor`는 팩토리다. 기본은 COM 브리지로, 빨강 표시를 할 수 있는 유일한 백엔드다. `KS_HWP_BACKEND=direct` + `.hwpx`는 XML을 직접 편집한다(글자만, 색 없음 · `skip_occurrences` 무시 · 쪽 번호 없음).

→ 상세: [docs/hwp-bridge.md](docs/hwp-bridge.md)

### Engine pipeline (v5)

[ui/workers/proofreading_worker.py](ui/workers/proofreading_worker.py) (`QThread`): ① 추출 → ② 로컬 사전 스크리닝(`nikl_dict.KoreanDictValidator` + `core/morph` 기본형 복원 + 우리말샘·온용어 캐시 폴백) → ③ Gemini(`core/correction_engine.build_engine()` → `GeminiEngine`) → ④ 병합 → ⑤ 일관성 후처리(`consistency_pass`) → ⑥ 사전 재검증(비표준 = `low`) → ⑥.5 이음매 정합(`junction_pass`, 워커 `[9.2]`) → ⑦ 적용(`ApplyWorker`) + 정오표.

- ⚠ 우리말샘은 잘못된 키에 **HTTP 200 + `<error>`**로 답한다. 결과 모양이 확인될 때만 숫자를 돌려준다 — `(None, [])` 확인 불가(캐시 안 함)와 `(0, [])` 미등재 확정(캐시)을 섞지 말 것. 캐시 테이블 `cache`·`onterm_cache`는 합치지 않는다. 온용어에는 비표준 표기가 있어 `norm_map`/`spelling_pairs` 거부권 가드가 필수다.
- ⚠ `AI_CHUNK_TYPO`를 재현율 레버로 쓰지 말 것. 청크 크기도, 경계도, 구조 고정 청커도 실측으로 폐쇄됐다 — **재시도 금지**. 튜닝 상수는 `core/models.py`에서만 바꾼다.
- 모델 체인 `3.5-flash-lite` → `3.1-flash-lite` → `3.5-flash`(무료 RPD 순)는 사용 불가 · 한도 소진 · 일시 오류에서 다음 모델로 넘어간다. **일간 429만** 그 모델을 차단한다. 분당 429는 차단도 고정 해제도 없이 넘어간다(RPM을 RPD로 취급하던 옛 동작으로 되돌리지 말 것).
- Case B(편집거리 1 낱말 치환)는 재도입 금지다. 실단어 탐지([7.5] `core/realword.py`)는 후보 경로 둘(A 빈출어 치환 · B **오타 채널**)이 같은 불변식(low 고정·검수 카드·AI 검증 필수·자동 적용 금지) 아래에서만 산다. 오타 채널은 `models.REALWORD_TYPO_PAIRS` 맞춤 자모 쌍만 쓴다 — **kiwi 내장 `basic_typos` 금지**(구어체용 — '상담채녈을'을 망가뜨렸다). B를 A의 상위 N 경쟁에 섞지 말 것(gap 눈금이 다르다).
- 결정론 계층에서 문자열 `set`을 순회하며 카드를 만들지 말 것 — 순회 순서가 프로세스마다 바뀌는 해시를 따라가 같은 원고의 카드 **순서**가 실행마다 달라진다. 등장 순 `dict.fromkeys(...)`를 쓴다.
- ★원고 속 '그 자리'를 **문맥으로 읽는 판정은 `core/doc_analysis`(공유 분석) 하나**를 쓴다 — 문맥 재확인·동형이의 가드(`verb_inflection_lemma`·`noun_josa_split`)·실단어 오타 채널. 블록은 **앞뒤 48자 이상 문맥을 덧붙여** 읽는다(블록만 떼면 줄 끝·줄 머리 낱말을 오독 — '갈월동'→갈/VV+월동). 읽기의 정본은 `top_n=1`이다(`top_n=5`의 1순위와 3.4% 다르다). 어절 단독 분석(`strip_josa`·`is_known_form`·finder의 1차 제안)은 옮기지 않는다.
- **분석 확신도**: 근접 대안 읽기(`doc_analysis.TIE_DELTA`)는 **high를 low로 내리는 데만** 쓴다(규범표기·어문규범·보조용언 '-어야'·피동 '-어지다'). 지우는 데 쓰지 말 것(kiwi 흔들림에 정탐을 잃는다). 강등 사유는 `[검수]`로 시작한다(신뢰도 통일이 되올리지 못하게).
- 띄어쓰기 finder는 어절 단독 kiwi 분석의 컷을 **`morph._cuts_hold_in_context`(공유 문맥 읽기)로 재확인**한다. 조건에 앞 품사까지 넣지 말 것(관형어+명사 — 목록형 문맥에서 '전(全)'이 NNG로 읽혀 정탐을 잃는다). 보조용언에 일괄 '사전 단일어 가드' 금지(해보다·들어주다는 동형 — `_AUX_SINGLE_WORDS` 목록만).
- 쉼표 뒤에 글자가 붙으면 전부 띄움 카드다(표 칸 압축 나열 포함 — 사용자 결정). 쉼표 자체가 군더더기인 두 자리는 띄우지 않고 `spacing_rules.find_stray_commas`가 고친다(문장부호 low — 이 모듈에서 유일하게 글자를 바꾼다): 닫는 부호 뒤 조사 앞 쉼표 삭제('묻다”,와'→'묻다”와') · 열거 '등' 앞 쉼표를 띄어쓰기로('Gemini,등'→'Gemini 등'). 낱말로도 쓰이는 한 글자(이·가·나·도·만·라)는 닫는 부호 뒤가 아니면 낱말로 본다('가,나,다'·'(2024),이 연구').
- 체언 뒤 띄운 괄호 붙임은 **앞말이 체언**일 때만(앞말이 조사·어미면 끼워 넣은 괄호 — '가진 (많은 경우 여성) 사람') 그리고 **저자 증거**(그 원고에서 같은 부류 괄호를 80% 이상 붙여 씀 · 또는 같은 문단 같은 모양)가 있을 때만 낸다. 저자가 섞어 쓰거나 띄워 쓴 원고(법령 조문 제목·한자 원문 병기)는 건드리지 않는다(사용자 결정). 저자 다수는 후보와 같은 모집단(체언 뒤 · 인용 구간 밖)으로만 센다.
- 띄어쓰기 **일관성**은 인용 구간(남의 제목 괄호·참고문헌/출처 줄)을 보지 않는다 — 판정은 `core/citation_spans` 하나이고, [7] 다수결·[9.2] 변이표(`mask()` — 한글만 가린다)와 검수 패널(인용 속 등장 excluded)이 같은 구간을 본다. 규칙·오탈자 카드는 인용 속에서도 그대로.
- 사전 안전망의 '이미 다룸' 판정은 워커 **`_flag_cover` 하나**([6]·[9.6] 공용)다. 다어절 카드의 1글자 런으로 덮지 말 것(진짜 오타가 숨는다). 안전망 카드(dict_flag)는 [7] 띄어쓰기 일관성을 막지 않는다. 원어 병기 억제(㉚ `author_marked_names`)는 **음역만** 뺀다 — 번역·한자어 본문은 계속 검사한다. 정상 복합어 판정(`looks_like_typo`)은 kiwi 2·3순위의 빈틈없는 분절로 하고, 1음절 조각을 허용하거나 사전만으로 자르지 말 것(복합어 속 오타가 숨는다).
- AI 신뢰도 `medium`은 **가드 체인 맨 끝**(`demote_uncertain_confidence`)에서 low로 내린다 — `_tag`에서 접으면 `[검수]` 표지가 빠져 신뢰도 통일이 되올린다. 신뢰도 통일 묶음은 (원문 어간, 교정 어간) 쌍이다.
- 이음매 충돌은 자동 해결이 원리적으로 불가능하다 → 계층(낱말 자신의 다수 > 이음매 > 규범)으로 처리한다. 불변식: `original`은 절대 고치지 않는다 · 글자는 바꾸지 않는다(공백만) · **띄운 구를 붙이기 금지** — 미탐 회복의 붙이는 방향은 저자가 붙임형을 낱말로 쓴 경우에만, 언제나 검수(low)로. **'저자 다수' 권한은 일관성 카드에만** 있다 — 문법 규칙 카드(보조용언·의존명사·관형어+명사)는 저자 다수와 어긋나도 지우지 말고 검수로 내린다. 자동 정합은 **충돌 분류 뒤**에, 근거 카드가 그룹이거나 근거끼리 갈리면 정합하지 말고 그룹에 합류시킨다(순서 의존 금지). [7] 동률 방향은 낱말 > **겹치는 낱말의 이음매** > 가족 > 규범.
- ★**사용자 첨부 사전**([core/user_dict_file.py](core/user_dict_file.py), 엑셀 A열 원문 · B열 교정문)은 **모든 계층보다 위**에 있다. 첨부가 없으면(`udict is None`) 계층 전체가 잠들어 기존 동작이 한 글자도 달라지지 않는다. 조직 공유 사전인 [core/userdict.py](core/userdict.py)와는 다른 것이다.
  - 진짜 보증은 직렬화 직전의 **`[9.7]` 최종 관문**이다 — 계층마다 검사를 심지 말 것. 판정식은 `violates` 한 곳뿐이다.
  - 로더는 셀 값을 **`strip()` 하지 않는다** — 앞뒤 공백이 행의 뜻(경계 규칙)을 가른다. 행 판정은 `classify_row` 하나다.
  - 경계 규칙은 **어절 쌍으로 펼치고**, 양옆에 `morph.is_joinable_left/right` 가드를 반드시 둔다(앞말과 뒷말의 기준이 다르다). kiwi가 없으면 붙이지 않는다. 띄우는 방향은 `is_word_split_point`로 형태소 경계에서만 띄운다.
  - 1글자 표제어는 보호에서 뺀다. 경계 규칙의 낱말 토큰은 보호 집합에 넣지 않는다. 다른 계층의 교정은 드롭하지 않고 `harmonize`로 맞춘다.
  - 카드 표시값은 고정(`source="user_dict"`, 수락 상태, `auto=False`)이고, `nikl_dict.validate`와 `event_queue._SKIP_SOURCES`에서 제외된다. **AI 프롬프트는 건드리지 않는다**(순서만 바꿔도 정밀도 88→75%).

→ 상세: [docs/pipeline-reference.md](docs/pipeline-reference.md)

### 결과물 · 적용 (output / apply)

| `output_mode` | HWP 원본 | 산출물 |
|---|---|---|
| `"hwp"` (default) | 치환 + 빨강 표시 | `_교정본.hwp` + 정오표 |
| `"hwp_memo"` | 치환 + 빨강 + 같은 자리에 메모 | `_교정본(메모).hwp` + 정오표 |
| `"errata"` | 무수정 (쪽 번호만 읽는다) | 정오표 |
| `"memo"` | 글자 불변, 메모 필드 | `_메모본.hwp` + 정오표 |
| `"pdf"` | 무수정 (PDF 내보내기) | `_주석.pdf` + 정오표 |

- 결과물 축은 교정 방식과 **직교**한다(모든 모드에 검토 단계가 있다). `hwp`가 아닌 모드는 정오표를 강제로 켠다. 분기는 `errata_only`가 아니라 `output_mode`로 한다. 글자를 바꾸는 모드는 **`ApplyWorker.MODIFY_MODES` 한 곳**에만 적는다.
- `result["applied"]`는 "HWP에서 실제로 바뀐 글자"이고 **뜻을 넓히지 않는다**(다른 모드에선 0). `flagged > 0 and applied == 0` 휴리스틱을 되살리지 말 것.
- ★`hwp_memo`의 메모 앵커는 **교정문**이다 → 메모는 치환 단계가 확인한 **좌표(`spots`)**로 바로 간다. 빨간 글자(`_match_is_red`)로 찾는 길은 좌표가 없는 항목의 폴백일 뿐이다. 검수 카드는 원문 앵커에 `red_only`를 끄고, 다른 교정이 새로 만든 앵커는 등장으로 세지 않는다.
- 결과 화면의 '빨간색+메모' 추가는 **진짜 교정본 위에** 단다(불변식①의 유일한 예외). 입력은 `MainWindow._corrected_hwp`에서 가져온다 — `result["hwp_path"]`를 쓰지 말 것. `base_spots`·`base_errata_rows`를 함께 넘긴다. 결합 모드의 정오표는 메모 결과를 겹친다(`_overlay_memo_notes`, `red_rank`).
- '결과물 추가'의 불변식 네 가지: ① 원본에서 만든다 ② 결정을 다시 묻지 않는다 ③ 1차 실행의 수치를 덮어쓰지 않는다 ④ 기존 정오표를 덮어쓰지 않는다(`_정오표(메모).xlsx` 접미사는 무조건). 오류는 `_on_error`가 아니라 `_on_extra_error`로 받는다. 이미 만든 버튼은 지우지 말고 비활성화한다.
- `ApplyWorker.error`는 "결과 없이 끝났다"는 뜻이다. 비치명 문제는 `warning`(그 뒤에 항상 `finished`)으로 보낸다. 취소는 공통 관문 한 곳에서 처리한다.
- 완료 단계의 산출물은 **산출물 카드 한 곳**에만 둔다(푸터에는 초기화만). 버튼 높이는 버튼 자신의 QSS `min-height`로 정한다(`setMinimumHeight`는 진다).
- ★빨강(·형광펜)은 교정문 전체가 아니라 **바뀐 곳에만** 칠한다(띄어쓰기는 양옆 한 글자 — '술 개'). 판정은 브리지 `_red_spans` 하나이고, '교정본+메모'의 빨간 글자 찾기도 같은 함수로 **칠한 글자 하나를 골라** 읽는다(선택 없이 읽으면 한/글은 커서 **앞** 글자 모양을 준다). 칠하기는 실패하면 **더 칠하는 쪽**으로만 무너지게 한다(전체를 칠한 뒤 밖 조각을 되돌림 · 형광펜은 조각 실패 시 전체). 치환 방식·좌표·`_BridgeReplay`는 건드리지 않는다 — 칠한 뒤 커서를 되돌린다.
- ★빨강은 원고의 **글자 스타일을 해제**한다. 그래서 스타일이 걸린 자리는 **빨강 형광펜**으로 표시한다. 지문은 파일을 직접 판독해 만든다([core/hwp_styles.py](core/hwp_styles.py) — COM HWPML 금지). 지문 필드는 빼지 말 것. 함정 셋: 매치 **시작 커서**에서 읽는다 · `MoveSelRight` 뒤 커서 위치로 확인한다(선택 없는 `InsertText`는 원고 오염) · 떠 둔 **파라미터셋 객체 그대로** Execute한다. `_match_is_red`가 형광펜을 인정하게 만들지 말 것(`HMarkpenShape.Color`는 마지막 펜 색이다). 치환 직후 그 자리에 인라인 메모를 다는 방식은 기각됐다.
- 메모 모드는 2단계다: `memo_scan` → `memo_keys` → `memo_place`. `HAction.Run("InsertFieldMemo")`를 쓴다(최상위 `hwp.Run`은 no-op이라 사유가 원고에 박힌다). 성공은 `GetPos()` story 변화로 확인한다. 머리말·캡션류는 거부(`blocked`)로 친다. ★**첫 메모를 단 뒤에는 찾기를 하지 않는다**(메모 박힌 문서에서 대량 찾기 → 세션 오염 → 이후 메모 전멸). 실패 자리 재스캔도, 주기적 재열기도 다시 넣지 말 것 — 장애 시 `_reopen_document`만 쓴다. 메모는 뒤에서 앞으로 달고, 달기 전에 자리를 확인한다. 검증은 메모 개수가 아니라 **서로 다른 자리 수**로 한다. 이미 메모가 달린 원고의 재교정은 범위 밖이다(폐기된 과제). 앵커 글자색은 바꾸지 않는다. 메모·PDF 본문은 `output/annot_text.annotation_body` 하나에서 만든다('이 낱말 N곳' 줄과 `[검수]` 태그는 넣지 않는다).
- memo/PDF는 `shadowed` 등장을 건너뛰고, 교정본은 건너뛰지 않는다.
- PDF: 내보내기 전에 선택을 해제한다(안 하면 그 범위만 저장된다) · 검색은 `pypdfium2`, 쓰기는 `pypdf`(둘 다 BSD) — **PyMuPDF 금지**(AGPL) · `FreeText` 금지(한글이 빈칸) · **한 자리에 주석 하나** · 머리말 반복은 걸러 낸다 · 대조에 실패하면 전부 표시하고 경고한다. `PDF 쪽`과 `쪽`은 서로 다른 좌표계다.
- ★**세 산출물은 같은 자리를 표시한다.** 등장 좌표계의 정본은 무수정 문서에서 센 `locate`(`doc_occ`)다. pdfium 검색은 공백을 무시하므로 `_spacing_ok`로 거르고, 줄바꿈 매치는 증거가 있을 때만 `_drop_ambiguous`한다. 정당한 차이 둘(검수 카드 · 머리말 메모 거부)은 정오표에 기록한다. 개수를 맞춰서 "고치지" 말 것.
- 정오표는 **등장 1곳당 1행**이고, 시트는 적용 / 거절 / 확인 필요로 나눈다. 원고 문자열은 `_wcell`을 거친다(제어문자 · `=` 시작). 쪽은 `KeyIndicator()[3]`(prnpageno)이다 — **정렬·산술 금지**, 치환 전 단일 스냅샷에서만 모은다. 예산(`_LOCATE_BUDGET`)이 소진되면 쪽 칸은 '—'로 두고, 행은 버리지 않는다.
- ★`skip_occurrences`는 **치환 전 좌표계**다 — **그냥 비우지 말 것**(사용자가 거절한 자리가 치환된다). 브리지가 찾은 등장이 적으면 쪽으로 재정렬한다(`_realign_by_page`, 위치 대응 금지). "건너뛰는가" 판정은 **`core.models.occurrence_skipped` 하나**다.
- ★★교정본 계획은 **브리지 루프의 재현**이다(`ApplyWorker._plan_by_replay`). `_BridgeReplay`는 브리지 루프와, `_BRIDGE_INVISIBLE`은 `hwp_bridge_worker._INVISIBLE_RE`와 함께 바꾼다. 자기 재매칭(교정문이 원문을 품음) 판정은 양쪽 모두 **영문 대소문자를 접어서** 한다(한/글 찾기 기준 — 가리면 'TRaining'→'Training'이 한 자리를 1000번 치환한다). `MainWindow`는 두 ApplyWorker 생성 모두에 `full_text`를 넘긴다. 이 재계획은 **글자를 바꾸는 모드(`MODIFY_MODES`) 전용**이다 — memo/PDF는 문서를 바꾸지 않으므로 재계획하면 오히려 어긋난다. S4는 과다 치환도 잡는다. 실기 검증은 진짜 클릭으로 한다(`_accept_all`은 존재하지 않는다).
- ★겹치는 교정은 경쟁시키지 않고 **자리 단위로 합성**한다(합성하지 않는 선택지는 없다 — 오탈자가 남는다). 범위는 수락 구간 안쪽이고, 결과가 갈리면 합성을 포기한다. **카드 값이 정본**이다(`ReviewPanel._compose_overlaps`) — `ApplyWorker._compose_spots`는 다시 합성하지 않는다. 겉 카드가 `dict_flag`이면 excluded 조각은 합성하지 않는다. `_edited` 카드는 덮어쓰지 않는다. 상태나 값이 바뀔 때마다 다시 합성한다.
- 등장별 결정의 유일한 진실은 `ReviewPanel.get_occurrence_rows()`다 — `skip_occurrences`를 역산하지 말 것. excluded·shadowed 등장은 정오표에서 뺀다. 옛 검수 모드(AI 범위 0개)는 폐기됐다 — 그 후계는 `errata` 모드와 'AI 분석 제외'다.
- `Correction`과 강조 색(COLORREF), API·청크·스로틀 상수는 [core/models.py](core/models.py)에 있다.

→ 상세: [docs/output-and-apply.md](docs/output-and-apply.md) (설계 당시 기록: [docs/output-mode-annotation-design.md](docs/output-mode-annotation-design.md))

### Config and API key resolution

- 키 셋(Gemini · 우리말샘 `NIKL_API_KEY` · 온용어 `ONTERM_API_KEY`)은 모두 env → `config.ini [API]` → 암호화된 조직 키 순서로 찾는다. 우리말샘 키와 온용어 키는 **서로 바꿔 쓸 수 없다**.
- `config.ini`는 `utf-8-sig`로 읽고, 손상된 파일은 무시한다(BOM 때문에 앱이 안 뜨던 사고). 사용자 폴더의 `config.ini`에는 **읽은 값과 앱이 바꾼 값만** 쓴다(`_user_cfg`). 병합 뷰를 쓰지 말 것 — 배포자의 값이 영구히 고정된다.

### 실행 게이트 — 사내 계정 로그인

- 로그인을 통과하기 **전에는 `MainWindow`를 만들지 않는다.** 개발 우회 `KS_SKIP_LOGIN=1`은 frozen이 아닐 때만 통한다.
- 신원은 서버 함수 `ksai_my_profile()` 하나가 판정한다. 판정 순서는 **재직 직원 → 회원 → 퇴사 직원(거부)**이다. ks-works 퇴사자는 같은 auth 계정을 회원으로 전환한다.
- **서비스 키를 앱에 넣지 말 것** — 관리 작업은 Edge Function이 하고, 정본은 [server/supabase/](server/supabase/)다. 직원 비밀번호는 앱에서 바꿀 수 없다(ks-works와 공유하므로 403).
- ⚠ `employees`를 직접 SELECT하지 말 것(전 직원의 개인정보가 읽힌다). 명단 데이터는 저장소에 두지 않는다(DB에만). 공유 뇌 기여자가 아닌 사용자는 이벤트를 **기록부터 하지 않는다**.
- ⚠ 오프라인 유예 7일은 **실패의 종류를 구분**해야 성립한다. 명시적 거부만 세션을 폐기하고, 네트워크·5xx 오류는 유예한다. 모든 실패에 세션을 비우는 코드로 되돌리지 말 것. 토큰은 프로필 조회보다 **먼저** 반영한다.
- 로그인 게이트는 키 유출 대책이 아니다(키 유출을 막으려면 서버 프록시가 필요하다).
- Supabase MCP의 `declined`는 사용자가 거절한 게 아니라 **파괴적 SQL을 자동으로 거절**한 것이다 → `DELETE … WHERE true` · `ALTER POLICY`로 대체한다.

→ 상세: [docs/config-and-accounts.md](docs/config-and-accounts.md)

### UI flow (v5) — 단일 워크스페이스 셸

- [ui/main_window.py](ui/main_window.py)는 위자드가 아니다. 영역은 한 번 만들고 파괴하지 않으며, 가운데 `stage`(설정/분석/검토/완료)만 바꾼다. 창은 프레임리스이고, 리사이즈·이동은 `nativeEvent`의 `WM_NCHITTEST`로 처리한다. 워커는 창이 소유하고, `_cleanup_worker`(stop 요청 + 최대 5초 대기) 패턴을 지킨다.
- 설정 화면: 결과물 카드는 2칸 × 3줄이다(빈 칸은 stretch로 채운다). 세로 여백 상수만 조정했고, **가로 여백은 건드리지 말 것**. 첨부 칸 2:1은 두 칸 모두 세로 정책 `Ignored`로 만든다(stretch만으로는 비율이 안 된다).
- ⚠ **화면 로그 규약**(모든 새 로그 라인에 적용): `[태그]` + `n건`만 쓰고, 원고 예시는 금지한다. 상세는 `      · ` 불릿으로 원문 로그에만 남긴다. 집계 줄은 `·`로 시작하지 말 것(사라진다). 로그 문구는 **`activity_panel._RULES` 한 곳**에서만 바꾼다. `_entries`에는 원문을 그대로 둔다.
- 디자인 시스템: 전역 QSS의 objectName/property 셀렉터 + `restyle()`로 스타일한다. 지속 위젯에 인스턴스 `setStyleSheet`는 피한다. 직접 그리는 위젯은 `refresh_theme()`를 구현한다. 툴팁 토큰은 테마별로 나누지 말 것. 셀렉터 없는 인스턴스 스타일시트는 `theme.local_qss()`로 쓴다(그렇지 않으면 툴팁까지 덮는다). 선언과 규칙을 한 문자열에 섞지 말 것. 툴팁은 **실화면 캡처**로 검증한다(`grab()`은 거짓말한다).
- 자산: `assets.base_dir()`은 `sys._MEIPASS`다(데이터 규칙과 정반대 — 자산은 번들에 들어간다). 이모지 글리프 금지 → Lucide SVG를 쓰고, 아이콘은 코드가 참조하는 것만 둔다. Pretendard는 TTF로, `font-family`에는 단일 패밀리를 쓴다.
- 검토 화면: 카드는 **등장 단위**이고, 정체성은 `uid`다(리스트 인덱스 아님). 반복 일괄 처리는 손대지 않은 반복 카드에만 전파한다(`occ["auto"]`). 사용자가 직접 뒤집은 카드(`occ["by_user"]`)는 이후 전파로 덮어쓰지 않는다. 일관성 카드(`consistency_flip`)는 문서 전체 표기를 고르는 것이라 부분 수락이 없고, 결정 단위는 **낱말**이다(같은 낱말의 형제 카드까지 `_apply_unify` 한 번 — 원문이 같은 카드를 두 장 만들지 말 것). 수락은 팝업 없이 토스트, 거절은 바뀌는 표기를 팝업으로 확인한다. 카드는 접지 않는다(교정자에게 모두 제시 — 사용자 결정). 토스트는 상·중·하 3칸을 차례로 돈다. 그 플래그는 워커 [7]만 켠다(규범 교정에는 켜지 말 것). 다른 카드에는 역방향 카드를 합성하지 말 것. 상태만 바뀌면 `_restyle_dirty()`, 등장이 늘면 `_sync_cards()`, 목록 구성이 바뀔 때만 `_rebuild_cards()`를 쓴다. **등장을 다시 만들지 말 것**(부분 결정이 사라진다). 지운 앵커 함수(`_capture/_restore_card_anchor`)는 다시 넣지 않는다. `setWidget()` 전에 `_cards`를 비운다. `_load_more_cards`에서 `_emit_counts()`를 부르지 말 것. 새 카드 위치는 `_place_card`로 잡는다. `_start_apply`는 `get_corrections()`로 다시 동기화한다. `LightConfirmDialog`·토스트에는 `refresh_theme()`를 넣지 않는다. `frameWidth()`는 쓰지 말 것.
- 결과 화면: 같은 실행의 변경은 `update_result`로 정적 재렌더한다. 로그는 활동 패널의 표시 행을 그대로 쓴다(두 번째 큐레이션 금지). 설정 대화상자는 없다.
- ★**검토 저장·이어서 하기·다시 적용**([core/review_session.py](core/review_session.py)): 분석이 끝나면 검토 상태를 원고별(파일 **내용** 해시)로 자동 저장하고, 같은 원고로 시작하면 AI를 부르기 **전에** 이어서 할지 묻는다(Esc는 새 분석이 아니다). 저장본엔 **원고 전문**이 담긴다 — `cache_dir()/review_sessions/`, `.gitignore`·빌드 제외에 올려 두었고 `.db`로 바꾸지 말 것(`stage_data`가 `data/*.db`를 싣는다). pickle 금지(태그 JSON) · 구조를 바꾸면 `SCHEMA`를 올린다. 등장은 본문에서 다시 찾지 말고 **그대로** 되살린다(`restore_state` — 다시 만들면 부분 결정이 사라진다). 완료 뒤 레일 '검토'는 검토 단계로 되돌려 **다시 적용**하게 한다. 적용·결과물 추가는 교정 목록의 **깊은 사본**(`_applied_corrections`)을 쓴다. 적용 실패는 검토 화면으로 돌아간다. 학습 큐는 한 검토에 한 번만 적는다.

→ 상세: [docs/ui-reference.md](docs/ui-reference.md)

## Conventions

- Korean docstrings/comments are the norm. Keep new code consistent.
- `core/` is GUI-agnostic — never import `PySide6` from `core/`. UI threading lives in `ui/workers/`.
- Long-running checkers accept a `threading.Event` named `stop_event` and a `logger` callable; honor both.
- 자동 검증은 셋이다: **골드셋** `eval/ai_goldset/run_goldset.py`(교정 판단 · 결정론 · 22초), **적용 경로 E2E** `eval/e2e_apply/run_e2e.py`(실제 한/글 · 진짜 클릭 · 교정본 재추출을 기대 문서와 대조 · 약 80초 · 기준선은 원고 옆 `<원고>.e2e.json`), **실원고 코퍼스 재생 게이트** `eval/corpus_harness/run_corpus.py gate`(20원고 × AI 켬(캐시 재생)·AI 끔 · 판정해 둔 카드 집합과 비교 · 약 3분 · **계층 간 불변식** 10종을 매번 함께 검사 — 위반은 기준선 차이가 아니라 실패라 서명으로 덮지 못한다, `invariants.py` · **추출 충실도**도 함께 — 원고 파일을 한/글 없이 직접 판독한 문단이 추출 텍스트에 다 있는지, 기준선 밖 새 누락 후보만 △, `coverage.py`). 셋 다 `/release`가 돌린다. 불변식은 문서에 근거가 있고 현 코퍼스에서 위반 0일 때만 더하고, 더할 땐 `selftest()`에 일부러 어긴 카드도 함께 넣는다. 그 밖에는 앱(`.\.venv64\Scripts\python.exe main.py`)을 실제 `.hwp`/`.hwpx`로 돌려 확인한다. `core/` 모듈은 GUI 비의존이라 REPL에서 바로 돌릴 수 있다.
- 결정론 규칙을 바꿀 때는 **실원고 코퍼스 하네스**(run before/after → diff)로 카드 변화를 AI 켬·끔 둘 다 한 장씩 판정하고, 판정이 끝나면 `gate --save-baseline`으로 서명한다(판정하지 않은 변화가 있으면 릴리스가 멈춘다). 원고 캐시·기준선은 `교정샘플/.corpus/`에만 둔다. 게이트는 AI 캐시가 빗나가면(= AI 입력 변경) 실패한다 — `--ai-live`(Gemini 호출)는 사용자 확인 후에만.
