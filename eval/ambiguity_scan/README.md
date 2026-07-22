# eval/ambiguity_scan — 바른 중의성 데이터셋 기반 개발 전용 스캐너

바른(bareun-nlp) **한국어 중의성 데이터셋**(35,396문장 / 8,285 표면형)을 써서
우리 결정론 finder와 kiwi 의존 가드를 **개발 PC에서만** 검증한다.

## ⚠ 라이선스 경계 — 반드시 지킬 것

데이터셋은 **CC BY-NC 4.0(비상업)** 이고, 원문 문장은 국립국어원/세종 말뭉치 출신이라
재배포에 별도 승인이 필요하다. 이 앱은 **사내 업무용(상업 맥락)** 이므로:

- ❌ **`data/`·`assets/` 안에는 절대 두지 않는다** — 이 둘은 PyInstaller가 통째로 번들해
  (`nikl_dict._resolve_db_path`의 `_MEIPASS/data` 참조) EXE로 배포된다. 비상업 자산이 EXE에
  실려 동료에게 전달되면 명백한 위반이다. **스캐너가 이 경우 실행을 거부한다.**
- ❌ 데이터셋과 그 파생 산출물(표면형 목록·문장)을 **커밋하지 않는다** → `.gitignore`
  `korean-ambiguity-data/` (경로 어디에 있든 매칭). 빌드 스크립트에 레포 전체를 include 하는
  규칙을 추가하지 말 것.
- ❌ 런타임(`core/`, `ui/`)에서 이 디렉터리를 import 하지 않는다. 앱은 이 도구를 모른다.
- ✅ 커밋되는 것은 **스캐너 코드와, 스캔으로 밝혀낸 사실을 우리 문장으로 재작성한 회귀
  케이스**뿐이다. (사실 자체는 저작물이 아니다 — 문장을 베끼지 말 것.)

## 준비

현재 개발 PC 설치 위치는 **레포 루트 바로 아래**다(`.gitignore`로 커밋 차단, 번들 대상 아님):

```powershell
git clone https://github.com/bareun-nlp/korean-ambiguity-data `
  "C:\Users\user9\Desktop\Work Utility\KS-Proof Reader\korean-ambiguity-data"
[Environment]::SetEnvironmentVariable("KS_AMBIG_DATA", `
  "C:\Users\user9\Desktop\Work Utility\KS-Proof Reader\korean-ambiguity-data", "User")
```

⚠ 환경변수를 영구 등록해도 **이미 떠 있던** 터미널·VS Code는 낡은 환경 블록을 물려받아
못 본다(Windows 특성). Phase E가 조용히 스킵되면 이걸 먼저 의심하고 재시작할 것.

`KS_AMBIG_DATA` 가 없으면 스캐너는 **graceful skip**(안내 후 종료)한다 — 기존
`run_goldset.py` 의 "kiwi/DB 미가용 시 스킵" 관행과 동일.

## 실행

```powershell
.\.venv64\Scripts\python.exe eval\ambiguity_scan\run_ambiguity_scan.py            # 전체(S-1/2/3)
.\.venv64\Scripts\python.exe eval\ambiguity_scan\run_ambiguity_scan.py --scan 1    # 무발화만
.\.venv64\Scripts\python.exe eval\ambiguity_scan\run_ambiguity_scan.py --limit 0   # 표본 제한 해제(느림)
```

## 스캔 3종

| | 내용 | 우리 코드와의 접점 |
|---|---|---|
| **S-1** | 정문 대량 무발화 — 결정론 finder 19종을 데이터셋 문장에 전수 적용. **발화 = 과교정 후보** | `run_goldset.py` Phase D-2(`_CLEAN_CORPUS` 32문장)의 수천 배 확장 |
| **S-2** | 중의성 표면형 ∩ 우리 치환 규칙(`norm_map` 키 / `spelling_pairs._STEM_PAIRS`) 충돌 심사 | D-1 페어 불변식과 같은 계열의 **기계적 사전 심사** |
| **S-3** | 정답 태그 대조로 **kiwi 용언 활용형 가드 정확도 실측** | `nikl_dict.is_verb_inflection_homograph` / D-4 `_NORM_VERB_CASES`(손수 11케이스) |

## 골드셋 연동 — Phase E (발화 델타 감시)

`run_goldset.py` 는 **`KS_AMBIG_DATA` 가 잡혀 있으면 자동으로 Phase E를 실행**한다.
데이터셋이 없으면 그냥 스킵하므로, 레포만으로도 Phase A~D는 완전히 재현된다.

```powershell
.\.venv64\Scripts\python.exe eval\ai_goldset\run_goldset.py                       # Phase E 자동 포함(+20초)
.\.venv64\Scripts\python.exe eval\ai_goldset\run_goldset.py --save-ambig-baseline # 기준선 저장/갱신
.\.venv64\Scripts\python.exe eval\ai_goldset\run_goldset.py --no-ambig            # 건너뛰기
```

**절대치가 아니라 델타를 본다.** 이 코퍼스는 세종·구어 전사 비중이 커서 발화의 상당수
(`'간게'`→`'간 게'`)는 구어체에 대한 **정상** 발화다. 발화 0을 요구하면 영구 FAIL이라 게이트로
쓸 수 없다. 그래서 **finder별 발화 수가 기준선보다 늘었을 때만 FAIL** — 늘었다는 건 새 규칙이
정문을 건드리기 시작했다는 뜻이다. 줄면 개선으로 표시된다.

기준선은 `<clone>/.ks_ambig_baseline.json` — **레포가 아니라 데이터셋 옆**에 둔다(라이선스 경계).
발화 증가가 정당하다고 판단되면 `--save-ambig-baseline` 으로 갱신한다.

## 스캔 결과를 반영하는 법 (중요)

스캐너는 **후보를 알려줄 뿐 자동으로 고치지 않는다.** 사람이 판단해서:

1. S-1 발화 → 진짜 과교정이면 해당 finder 가드 수정 + **우리가 새로 쓴 문장**을
   `run_goldset.py` `_CLEAN_CORPUS` 에 추가(데이터셋 원문 복사 금지).
2. S-2 충돌 → 그 norm_map 키/페어는 자동 치환에서 빼거나 검수(low) 카드로만.
3. S-3 오판 → 가드 조건 보강 + D-4 `_NORM_VERB_CASES` 에 우리 문장으로 케이스 추가.

즉 이 도구는 **회귀 게이트가 아니라 발견 도구**다. 게이트는 계속 `run_goldset.py` 가
레포 내부 자산만으로 돌아간다(외부 데이터 없이도 CI/재현 가능).
