# 설정 · API 키 · 로그인 게이트 · 계정

> CLAUDE.md에서 옮긴 **상세 참조**다(2026-10-18 재구성 — 서술은 원문 그대로 옮기고 절 제목만 붙였다). CLAUDE.md에는 이 영역의 불변식·금지선 요약만 남았다.
> 설계·동작을 바꾸면 **이 문서와 CLAUDE.md 요약을 함께** 고칠 것(메모리 `dead-code-and-stale-memory-hygiene`).

## Config and API key resolution

[core/config_loader.py](../core/config_loader.py) `ConfigLoader.get_gemini_key()` resolution order:
1. `GEMINI_API_KEY` env var
2. `[API] GEMINI_API_KEY` in `config.ini` (next to the EXE, or repo root in dev)
3. Encrypted org key from the build-injected `core/_org_keys.py` (see [build-and-release.md](build-and-release.md)).

⚠ `config.ini` is read with **`utf-8-sig`** and a malformed file is ignored with a stderr note (`_load_ini`) — a BOM (Notepad adds one) used to raise `MissingSectionHeaderError` inside `MainWindow()` and the app never opened. ⚠ The user-folder `config.ini` receives **only what was read from it plus what the app set** (`_user_cfg`); the merged view `_cfg` is read-only. Writing the merged view (the old behaviour) copied the deployer's EXE-side values into the user file on the first theme toggle, and since the user file wins, later deployer changes were ignored forever. `_save` also drops non-[APP] entries identical to the EXE-side value, which cleans up copies made by older versions without changing any effective value.

All three keys resolve identically (env → `config.ini [API]` → encrypted org key): `get_gemini_key()`, `get_nikl_key()` (`NIKL_API_KEY`, 우리말샘), `get_onterm_key()` (`ONTERM_API_KEY`, 온용어). 우리말샘 and 온용어 are **different services — their keys are not interchangeable** (verified: the 우리말샘 key returns `returnCode 100` on 온용어). A missing Gemini key disables the app's core function; a missing dictionary key silently disables just that fallback.

## 실행 게이트 — 사내 계정 로그인 (2026-08-05)

`main.py`는 테마 적용 후 [ui/widgets/login_dialog.py](../ui/widgets/login_dialog.py) `require_login()`을 호출하고, None이면 `sys.exit(0)` — **통과 전에는 `MainWindow`를 생성하지 않는다.** 창을 띄운 뒤 기능만 잠그면 해제 경로가 UI 전역으로 번지기 때문. 저장된 세션(DPAPI `data/.ks_session`)이 있으면 `SessionRestoreWorker`가 먼저 검증해 입력 없이 통과하고, 게이트 모드에서는 취소 문구가 '종료'다. 로그아웃(`MainWindow._logout`)은 게이트를 다시 세우고, 재로그인을 취소하면 창을 닫는다.

### 계정 두 종류 · 명단 · 공유 뇌 기여자

**계정은 두 종류이고, 신원은 서버 함수 `ksai_my_profile()` 하나가 판정한다** (2026-10-16 분리 — 사용자 결정: 무료 프로젝트 상한 때문에 ks-works와 **같은 Supabase 프로젝트 안에서** 분리, 기존 직원 계정은 무변경). ① `employee` — ks-works 직원(`employees`), 지금처럼 그 계정으로 로그인. ② `member` — 앱 전용 회원(`ksai_members`: 로그인 ID·이름·권한·상태만, 인사 정보·평문 비밀번호 없음). 회원은 employees 행이 없으므로 ks-works가 로그인 직후 로그아웃시키고 ks-works RLS(전부 `current_employee_id()`/`is_admin()`)도 막는다. 관리자가 헤더 계정 메뉴 → **사용자 관리**([ui/widgets/member_admin_dialog.py](../ui/widgets/member_admin_dialog.py))에서 추가하며 초기 비밀번호는 **ks2026**(서버 Edge Function 상수가 정본, `core/member_admin.INITIAL_PASSWORD`는 표시용), 첫 로그인 때 `require_login`이 **게이트 안에서** 변경을 강제한다(오프라인 유예 통과 시엔 강제 안 함 — 서버에 못 닿으니 바꿀 수도 없다). ★**ks-works 퇴사자는 같은 ID로 회원 전환**한다: auth.users 이메일은 유일해 새 계정을 만들 수 없으므로, 확인(`code=former_employee` → `allow_former_employee`) 후 **그 auth 계정을** 차단 해제·비밀번호 초기화하고 `ksai_members`에 등록한다. employees의 퇴사 기록은 읽기만 하므로 ks-works는 계속 막힌다(퇴사자는 `current_employee_id()`가 null). 그래서 `ksai_my_profile()`의 판정 순서가 **재직 직원 → 회원 → 퇴사 직원(거부)**이다 — 직원 행이 무조건 이기면 퇴사 행이 회원 행을 가린다. ⚠ ks-works에서 그 사람을 다시 '퇴사' 처리하면 auth 차단이 걸려 앱 로그인도 막힌다(사용자 관리 '사용 재개'로 해제). 서비스 키가 필요한 일(생성·초기화·중지·비밀번호 변경)은 전부 Edge Function(`ksai-admin-members`·`ksai-change-password`)이 하고, 소스 정본은 [server/supabase/](../server/supabase/)다 — **서비스 키를 앱에 넣지 말 것.** ⚠ **앱에서 비밀번호를 바꿀 수 있는 건 회원뿐이다**: 직원은 auth.users를 ks-works와 공유하므로 여기서 바꾸면 ks-works 비밀번호와 관리자용 평문 미러(`employee_credentials`)가 어긋난다 — 서버가 403으로 거부하고 메뉴는 ks-works 마이페이지로 안내한다. ★**명단과 공유 뇌 기여자** (2026-10-16): 추가 대상 직원 명단의 인사 정보는 `ksai_roster`(로그인 이메일 PK — 계정 생성 **전에도** 존재해야 해서 `ksai_members`와 분리)에, 공유 뇌 **이벤트 기여자**는 `ksai_brain_contributors`(이메일 목록 — 명단 밖 ks-works 직원도 지정하므로 명단의 열이 아니라 별도 테이블) + **관리자는 규칙**(`userdict_can_contribute()` = 활성 AND (큐레이터 OR 목록))으로 정한다. `userdict_events` INSERT 정책이 이 함수를 요구하고, `ksai_my_profile()`의 `brain_access`로 앱이 비기여자의 이벤트를 **기록부터 하지 않는다**(`auth.can_contribute()` — 정책만 막으면 업로드가 매번 거부돼 로컬 큐만 자란다). 스냅샷 수신은 전원 유지(승인된 결과물은 노이즈가 아니다). 둘 다 개인정보/권한 테이블이라 RLS on·정책 0 — Edge Function `list`도 전화번호·성별은 내보내지 않는다. ⚠ **명단 데이터는 저장소에 두지 말 것**(퍼블릭) — DB에만 있다. 관리 화면의 '명단 계정 일괄 생성'(`provision_roster`)이 계정 없는 명단 인원을 만든다(퇴사자는 전환, 재직자는 건너뜀).
⚠ **Supabase MCP는 파괴적 SQL(`TRUNCATE`·`DROP POLICY`)을 사용자 확인 없이 자동 거절한다**(`status: declined` — 사용자가 거절한 게 아니다, 3회 실측). `DELETE … WHERE true`·`ALTER POLICY`는 통과한다.

### 금지선 · 오프라인 유예

⚠ **`employees`를 직접 SELECT하지 말 것**: ks-works RLS상 직원 토큰은 전 직원의 전 열(휴대폰·생년월일·휴직 여부…)을 읽을 수 있다. 앱이 받는 신원은 이름·권한·상태뿐이어야 한다.

⚠ **게이트가 막는 건 "릴리스를 받아서 그냥 쓰는" 비사용자다** — 내장 키 자체는 퍼블릭 릴리스에서 추출 가능하므로([build-and-release.md](build-and-release.md) 참조) 키 유출 대책이 아니다. 그 목적이면 서버 프록시가 필요하다.

⚠ **오프라인 유예(`core/auth.GRACE_DAYS=7`)는 게이트가 전사 단일 장애점이 되지 않게 하는 장치이고, 그 전제는 "실패의 종류 구분"이다.** 서버가 **명시적으로 거부**하면(refresh 400/401/403·`terminated_at`·employees 미등록) 세션을 폐기하고 재로그인을 강제하지만, **네트워크/서버 오류**(status 0·5xx·타임아웃)는 마지막 성공 검증(`verified_at`)이 7일 이내면 통과시킨다. 예전 `restore()`처럼 *모든* 실패에 세션을 비우는 코드로 되돌리면 유예가 원리적으로 불가능해진다 — `_fetch_profile`이 `(status, prof)`를 돌려주는 것도, `_apply_token_response`가 프로필 조회보다 **토큰 반영을 먼저** 하는 것도(회전된 refresh_token 유실 방지) 같은 이유다. offline 세션은 유효한 access_token이 없어 동기화·큐레이션이 자동 no-op이고, 교정은 로컬 사전만 쓰므로 정상 동작한다. 개발 우회 `KS_SKIP_LOGIN=1`은 **frozen이 아닐 때만** 통한다.
