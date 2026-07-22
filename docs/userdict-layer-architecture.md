# 사용자 용어 뇌(공유 학습 사전) — 이식 아키텍처 (PDCA)

> 목적: 이 앱을 쓰는 **조직 구성원 전원(최소 10인)의 수락·거절·수정후수락 행위**를 하나의
> **공유 "뇌"** 로 수렴시켜, "쓸수록 똑똑해지는 조직 맞춤형 교정"을 만든다. 단, 상위 설계도의
> 철칙(과교정 억제 최우선, 사전=항상-on 결정론 인프라 / AI=생성, 프롬프트 컨텍스트 증강 금지)을
> **그대로 상속**한다.
>
> 이 문서는 [proofreading-architecture.md](proofreading-architecture.md)의 **하위 설계도**이며,
> [eomun-rule-layer-architecture.md](eomun-rule-layer-architecture.md)와 같은 자리(결정론 사전 레이어)에
> 합류한다. 작성: 2026-06-22.

---

## 0. 핵심 결론 — "어디에 꽂는가" & "무엇을 버렸는가"

조직 누적 용어 뇌는 **결정론 사용자 사전(`userdict.db`)** 으로 이식한다. 외부에서 제안된
"Obsidian + Vector DB + RAG 자가진화 루프"는 **전면 폐기**했다. 이유는 이 코드베이스가
이미 입증한 실패들과 정면충돌하기 때문이다:

| 제안 폐기 항목 | 폐기 사유 (이 앱의 실측 교훈) |
|---|---|
| **RAG/벡터 검색 → Gemini 프롬프트 주입** | KAGEC 주입이 **AI 오탈자 탐지를 분산**시켜 회귀(`고지사→고지서`·`훗가이도→홋카이도`)를 일으켜 2026-06-22 제거됨. 같은 메커니즘에 더 잡음 많은 데이터로 재진입 = 금지 |
| **LLM이 위키를 컴파일 → 다시 LLM 교정에 피드백** | 검증 게이트 없는 LLM 생성 지식의 환각 복리. "모든 생성은 사전 재검증 통과" 원칙 위반 |
| **Obsidian을 공유 "뇌"/저장소로** | Obsidian은 단일 사용자 로컬 파일 편집기. 다중 쓰기 = 충돌·손상. RLS·트랜잭션·합의·거버넌스 부재. (상세 리뷰는 §1.1) |
| **GraphRAG(Neo4j)·의미 벡터 검색** | 용어 교정은 정확매칭 룩업 문제. 의미유사도 불필요. 비결정성·지연·과설계 |

**채택하는 것 — 결정론 3-요소(어문 규범 레이어와 동형):**

| 역할 | 구현 | 항상-ON? | 위험 | 비고 |
|---|---|---|---|---|
| **P. 결정론 사용자 페어** (positive) | 합의·승인된 `원형→교정형`을 `norm_map`/`eomun_pairs`와 같은 경로로 적용 | 항상 | 중간 → 강가드+큐레이터 승인으로 억제 | `build_norm_map.py` 가드 상속 |
| **E. 조직 예외(무교정 화이트리스트)** | 조직이 승인한 표기는 재검증·안전망·띄어쓰기 백스톱에서 **교정/플래그 억제** | 항상 | 낮음(교정을 *억제* = 안전 방향) | 거절 다수 패턴에서 자동 후보화 |
| **C. 회귀 골드셋 게이트** | 후보 승격 전 무변경(과교정 0) 자동 검사 | 빌드/승인타임 | 0 | `eval/eomun_regression.py` 확장 |

> ⚠ **Gemini 프롬프트는 일절 건드리지 않는다.** 뇌는 *결정론 사전 인프라*로만 작동한다.
> in-document glossary([core/prompts.py](../core/prompts.py))처럼 작고 문서국소적인 안전한 주입은
> 현행 유지하되, 교차문서·검색기반 컨텍스트 주입은 KAGEC 금지선을 그대로 적용한다.

---

## 1. 다중 사용자 전제 — 무엇이 바뀌나

단일 사용자 로컬앱이 아니라 **조직(출판사) 1테넌트 안의 10인+** 가 전제다. 이는 둘을 바꾼다:

1. **중앙 백엔드가 필수** — 집계·합의·거버넌스·배포를 담당. 단, **교정 파이프라인은 클라이언트
   (PySide6)에 그대로 남는다.** 서버는 "뇌"의 동기화·거버넌스만 한다(파이프라인까지 서버로 올리면
   제거된 v4 웹앱 회귀).
2. **누적의 가치가 합의로 격상** — 1인의 수락은 우연일 수 있으나 **N인 독립 합의**는 신호다. 양의
   피드백 폭주(상위 설계도 #1 리스크)가 **다수 합의 + 큐레이터 승인**으로 감쇠된다. 단 공유 규칙은
   영향 범위가 전원이므로 가드는 *더* 엄격히 한다.

### 1.1 Obsidian을 핵심에서 제외한 이유 (원론 리뷰 결론)

- Obsidian은 **DB가 아니라 로컬 마크다운 폴더 편집기**다. 동시 다중 쓰기·트랜잭션·접근통제·서버가
  태생적으로 없다. Sync(계정별 기기 동기화)·공유폴더(공식 경고: 손상 위험)·Git(머지 충돌)·CRDT
  플러그인(노트 공동편집용 3rd-party)은 모두 *앱이 붙는 거버넌스 DB*가 못 된다.
- 우리 설계에서 진짜 뇌는 **Supabase(Postgres)** 다. Obsidian이 주려던 (a)저장소·(d)RAG는 이미 폐기,
  (b)그래프·(c)마크다운 편집은 큐레이터의 **후보 큐 CRUD + 감사추적 + 구조적 질의** 요구에 Postgres가
  더 적합. → **핵심 경로에서 제거.**
- *선택 백로그*: 팀이 "위키 느낌"을 원하면 **Postgres → 읽기전용 마크다운/Obsidian vault를 단방향
  생성**(md는 일회용 뷰, 절대 쓰기 경로 아님)으로만 허용한다. 그래프가 필요하면 Postgres 데이터에서
  별도 렌더(D3/Graphviz) — 편집 경로와 분리.

---

## 2. 데이터 모델 (용어 단위 — 원고 문맥 미저장)

**프라이버시 결정: 용어 단위만 중앙 저장.** 미출간 기밀 원고의 문장 스니펫(`context_before/after`)은
**중앙에 절대 업로드하지 않는다.** 문맥은 비식별 거친 특징(`doc_type`, 제안 출처 `source`,
`category`)만 동반한다. 의미로만 갈리는 동형이의어(결재/결제)가 `doc_type`으로 안 갈리면 자연히
"의견 분열 → 문맥의존 → 결정론 제외"로 빠진다(상위 "맥락의존은 결정론 금지" 원칙과 정합).

```jsonc
// 클라이언트 → 서버로 올리는 교정 이벤트(용어 단위)
{
  "event_id":   "uuid",
  "org_id":     "org tenant id",          // RLS 격리 키
  "user_id":    "supabase auth uid",      // distinct-user 카운트 + 역할
  "original":   "매출액",                  // 어절 표면형(형태소 lemma 기준, morph.strip_josa 적용)
  "corrected":  "매출 액",                 // 제안된 교정형 (수정후수락이면 사용자가 고친 값)
  "action":     "accept | reject | edit_accept",
  "suggest_src":"ai | dict | spacing | userdict",   // 어떤 단계가 제안했나
  "category":   "맞춤법|띄어쓰기|표준어|외래어|규범표기",
  "doc_type":   "공문서|소설|논문|보고서|기타",        // 비식별 거친 문맥
  "snapshot_ver":"적용 당시 뇌 스냅샷 버전",
  "ts":         "ISO8601"
  // ❌ context_before / context_after / 원문 문장 — 저장하지 않음
}
```

승급 산출물(서버 → 클라이언트로 배포)은 두 종류:

```jsonc
// userdict_pairs (역할 P) — norm_map/eomun_pairs와 동일 형상
{ "nonstd": "...", "norm": "...", "rule_id": "USR-<id>", "category": "...", "scope_doc_type": "*" }
// userdict_exceptions (역할 E) — 무교정 화이트리스트
{ "term": "매출액", "scope": "spacing|all", "rule_id": "USR-<id>", "note": "사내 붙여쓰기 통일" }
```

---

## 3. 중앙 저장 — Supabase(Postgres) 스키마 + RLS

단일 진실원천 = Supabase. 추가전용 투표 로그 + 파생 큐레이트 사전 + 배포 스냅샷의 3구획.

```sql
-- 인증 사용자 ↔ 조직/역할
CREATE TABLE members (
  user_id  uuid PRIMARY KEY REFERENCES auth.users,
  org_id   uuid NOT NULL,
  role     text NOT NULL DEFAULT 'editor'   -- 'editor' | 'curator'
);

-- 추가전용 투표 로그(용어 단위, 문맥 스니펫 없음)
CREATE TABLE events (
  event_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL,
  user_id    uuid NOT NULL REFERENCES auth.users,
  original   text NOT NULL,
  corrected  text NOT NULL,
  action     text NOT NULL CHECK (action IN ('accept','reject','edit_accept')),
  suggest_src text, category text, doc_type text,
  snapshot_ver int, ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_term ON events(org_id, original, corrected);

-- 후보 큐(집계 산출 → 큐레이터 승인 대기). status로 거버넌스 상태 전이.
CREATE TABLE candidates (
  cand_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('pair','exception')),
  original   text, corrected text, term text,
  distinct_users int, accept_n int, reject_n int, agreement numeric,
  category text,
  guard_flags jsonb,             -- 동형이의어/표준표제어 가드 결과
  goldset_pass boolean,          -- 무회귀 게이트 결과(§6)
  status     text NOT NULL DEFAULT 'pending'
             CHECK (status IN ('pending','active','rejected','context_dependent')),
  decided_by uuid, decided_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- 배포 스냅샷(버전·해시). 클라이언트는 버전 비교 후 pull.
CREATE TABLE snapshots (
  ver        int PRIMARY KEY,
  org_id     uuid NOT NULL,
  payload    jsonb NOT NULL,     -- {pairs:[...], exceptions:[...]}
  sha256     text NOT NULL,
  built_at   timestamptz NOT NULL DEFAULT now()
);
```

**RLS(행 수준 보안) 원칙:**
- `events`: 본인(`user_id = auth.uid()`)만 INSERT, 같은 `org_id` 멤버만 집계 읽기(개별행은 비노출 권장 — 뷰로 합산만).
- `candidates`: 같은 org의 `curator`만 읽기·상태변경(승인/반려).
- `snapshots`: 같은 org 멤버 누구나 최신 버전 read(배포).
- 모든 테이블 org_id 테넌트 격리. 프라이버시 경계 = **조직 내부**(10인 모두 같은 출판사 직원이라는 전제).

**집계:** Edge Function 또는 SQL 뷰가 `events`를 `(original, corrected)`로 묶어 `distinct_users`·
`agreement` 계산 → 임계 통과분을 `candidates`에 upsert(가드·골드셋 결과 동반). 스케줄(예: 주기 cron)
또는 이벤트 트리거.

---

## 4. 합의·승격 알고리즘 (뇌의 심장)

```
이벤트 집계: 같은 (original, corrected)에 대해
  A = 수락한 distinct 사용자 수
  R = 거절한 distinct 사용자 수
  agreement = A / (A + R)          # 본 사람 기준 아님 — 행위(accept/reject)한 사람 기준

승격 후보(pair) 조건 — 전부 충족:
  1. distinct_users(A) >= K            # 기본 K = 3 (10인 중 3인 독립 합의)
  2. agreement >= θ                    # 기본 θ = 0.8 (8:2 이상)
  3. 동형이의어 가드 통과              # original이 stdict 표준 표제어면 제외(_VARIANT_SENSE 원리)
  4. corrected가 stdict 등재(정답이 비표준이면 폐기)
  5. norm_map/eomun_pairs와 토큰 충돌 시 → 국가 표준 우선, 충돌만 큐레이터에 표시
  → status='pending' 으로 candidates 큐 적재

분기:
  · θ_low <= agreement < θ  (예: 0.5~0.8 의견 분열) → status='context_dependent'
        (결정론화 ❌. 문맥의존이므로 AI 전담 — 우리는 컨텍스트 주입 안 하므로 단순 제외)
  · corrected가 대부분 거절(agreement 낮음) → kind='exception' 후보
        (original을 '무교정 화이트리스트'로: 사내 표기로 인정)

수정후수락(edit_accept) 처리:
  · 원 제안 확정 ❌
  · (original → edited_value) 를 신규 accept 이벤트로 → 새 pair 후보 시드
  · (original → 원래제안값) 에 reject 1표 가산
```

**거버넌스 = 큐레이터 승인제(확정).** 자동 승격 없음. `pending` 후보는 **선임 편집자(curator)** 가
인앱 패널에서 승인해야 `active` → 새 `snapshot` 버전에 포함. 잘못된 공유 규칙이 전원을 오염시키기
전에 사람이 차단. 큐레이터는 `context_dependent`/`rejected`로도 보낼 수 있다.

> 튜닝 기본값(조정 가능): **K=3, θ=0.8, θ_low=0.5.** 운영 데이터로 조정.

---

## 5. 클라이언트 통합 (정확한 좌표 — 파이프라인 결정론 구간)

현행 결정론 패스: `[5.7]` norm_map · `[5.8]` eomun_pairs · `[5.9]` josa_rules
([ui/workers/proofreading_worker.py](../ui/workers/proofreading_worker.py)). 사용자 사전은 같은 자리에 합류한다.

### P. 결정론 사용자 페어 (positive)
- **`core/userdict.py`** (신규, GUI-agnostic) — `eomun_rules.py` 미러.
  - `available() -> bool` (로컬 `data/userdict.db` 존재 + 스냅샷 적재)
  - `batch_lookup_pair(words: set) -> dict` — `batch_lookup_norm`/`batch_lookup_eomun_pair`와 동일 시그니처
  - DB 부재/오프라인 → 빈 결과(graceful, 사전·kiwi와 동일 규율)
- **`proofreading_worker [5.6]`** (신규) — norm_map 처리 직전에 `batch_lookup_pair`로 조회해
  `source="dict"`, `confidence="high"`, `category` 보존, `reason="[사내 용어] … (USR-<id>)"` 교정 추가.
  norm_map/eomun_pairs와 토큰 충돌 시 **국가 표준 우선**(런타임), 충돌 이벤트는 로깅.

### E. 조직 예외 (무교정 화이트리스트)
- `core/userdict.py` — `is_exception(term, scope) -> bool`.
- **재검증 ②**([nikl_dict.py](../nikl_dict.py) `KoreanDictValidator`) — 예외 표제어는 비표준이라도
  `confidence=low`로 강등하지 않음(조직 승인).
- **안전망 ⑤**([proofreading_worker](../ui/workers/proofreading_worker.py) `[6]`) — 예외 표제어는
  `dict_flag` 검수 카드로 띄우지 않음.
- **띄어쓰기 백스톱 ⑦**([core/spacing_rules.py](../core/spacing_rules.py)·[core/morph.py](../core/morph.py)) —
  예외(scope='spacing') 표제어는 분리/통일 제안에서 제외(예: '매출액' 붙여쓰기 사내 통일).

### 이벤트 캡처 & 동기화
- **이벤트 캡처** — 검토 패널의 등장별 accept/reject([ui/widgets/review_panel.py](../ui/widgets/review_panel.py))와
  `auto_apply` 결과에서 **용어 단위** 이벤트를 추출(문맥 스니펫 제외, `morph.strip_josa`로 lemma 정규화).
  로컬 큐 `data/event_queue.db`(SQLite)에 적재.
- **`core/userdict_sync.py`** (신규, GUI-agnostic) — push(큐→Supabase)·pull(최신 snapshot→로컬 빌드).
  - pull 시점: **앱 시작 + 수동 새로고침**. push: 이벤트 발생 시(오프라인이면 큐 보관, 온라인 시 flush).
  - 로컬 `userdict.db` 빌드는 `build_eomun_db.py` 패턴 재사용(스냅샷 payload → SQLite, 멱등).
  - 키/오프라인/placeholder → 전 구간 graceful(파이프라인 영향 0).

### 큐레이터 패널 (확정: 인앱 A)
- **`ui/widgets/curator_panel.py`** (신규) — `members.role='curator'` 일 때만 노출.
  - `candidates` 큐 뷰: status·category·`distinct_users`/`agreement`·가드·골드셋 결과 표시, 필터/정렬/배치 승인.
  - 액션: 승인(`active`)·반려(`rejected`)·문맥의존(`context_dependent`)·예외 전환.
  - 승인 시 **골드셋 무회귀 검사 결과를 함께 표시**(§6) — 위반이면 승인 차단/경고.
  - 기존 디자인 시스템 재사용([ui/styles/theme.py](../ui/styles/theme.py)·[ui/widgets/components.py](../ui/widgets/components.py)),
    `core`에서 PySide6 import 금지 규약 유지(패널은 ui 계층).

---

## 6. 회귀 골드셋 게이트 (과교정 0이 1순위)

공유 규칙은 영향이 전원에 미치므로 승격 전 자동 게이트를 통과해야 한다.

- **`eval/userdict_regression.py`** (신규, 오프라인) 또는 [eval/eomun_regression.py](../eval/eomun_regression.py) 확장.
- **무변경 테스트(HARD GATE)** — 카논 위험어(있다·없다·국가·외국인·전문가·사회문제 등) + 표준 표제어
  표본에 **신규 후보 규칙을 적용해도 단 하나도 바뀌지 않음**을 검증. 하나라도 바뀌면 과교정 회귀 →
  해당 후보 `goldset_pass=false`(승인 차단).
- **충돌 검사** — 후보가 norm_map/eomun_pairs와 모순되는지(같은 토큰 다른 결과) 표시.
- 큐레이터 승인 플로우와 결합: `pending` 후보는 골드셋 통과 표시가 있어야 승인 버튼 활성.

---

## 7. PDCA

### ▣ PLAN — 본 문서 (확정 사항)
- 뇌 = **결정론 사용자 사전(P 페어 + E 예외) + C 골드셋 게이트.** RAG/벡터/Obsidian-as-brain ❌.
- 중앙 = **Supabase(Postgres) + RLS**, 거버넌스 = **큐레이터 승인제**, 프라이버시 = **용어 단위만**.
- 큐레이터 surface = **인앱 PySide6 패널(A)**. Gemini 프롬프트 무변경.
- 전 구간 graceful no-op(DB/네트워크 부재 시 기존 단독 동작).

### ▣ DO — 구현 순서 (무위험 → 저위험)
1. **로컬 결정론 레이어(파이프라인 순수 추가)** ✅ *구현·검증 완료(2026-06-22, §7+ 로그)* —
   `core/userdict.py`(휴면 로더) + `build_userdict_db.py`(스냅샷 payload→SQLite). 아무도 호출 안 하므로 런타임 무영향.
2. **워커 합류** ✅ *구현·검증 완료(2026-06-23, §7+ 로그)* — `[5.6]` 페어(국가표준 우선 충돌처리) +
   예외(②/⑤/⑦ 억제). 동형이의어 가드 상속. 로컬 스냅샷만으로 동작. 빈 DB=무회귀(실측).
3. **이벤트 캡처 + 로컬 큐** ✅ *구현·검증 완료(2026-06-23, §7+ 로그)* — `core/event_queue.py`
   (data/event_queue.db) + `main_window._capture_correction_events`(`_start_apply` 부수효과). 아직 업로드 없음.
4. **Supabase 백엔드** ✅ *구현·검증 완료(2026-06-23, §7+ 로그)* — ks-works 프로젝트에 스키마·RLS·
   집계/스냅샷 함수(마이그레이션 3건) + 기존 Auth/employees 신원 재사용 + `core/userdict_sync.py`(push/pull)
   + `ui/workers/sync_worker.py` 배선(시작 시 pull · 캡처 후 push).
5. **큐레이터 패널** ✅ *구현·검증 완료(2026-06-23, §7+ 로그)* — `ui/widgets/curator_panel.py`(admin 전용) +
   `ui/workers/curator_worker.py` + `core/userdict_sync` 큐레이터 연산 + `build_userdict_db.guard_check_many`(가드 게이트).
6. **골드셋 하니스** ✅ *구현·검증 완료(2026-06-23, §7+ 로그)* — `eval/userdict_regression.py`
   (가드 게이트 + 무변경 HARD GATE + 충돌 검사, 오프라인, 위반 시 exit 1).

### ▣ CHECK — 수용 기준
- 무변경 테스트 **위반 0건**(승급 후보 적용 후에도).
- 합의 임계(K·θ) 시뮬레이션: 의견 분열 케이스가 `context_dependent`로 정확히 라우팅됨.
- 오프라인/키 부재 시 파이프라인 **무영향**(graceful) 확인.
- 실모델 1건: 승인된 사내 페어가 `reason`에 `USR-<id>` 근거와 함께 적용, 신규 과교정 0.
- RLS: 타 org/타 사용자 데이터 비노출, editor가 candidates 변경 불가.

### ▣ ACT — 표준화
- 수용 통과분만 항상-ON 표준 경로 승격. 미달분은 가드/임계 조정 후 재검증.
- 상위 [proofreading-architecture.md](proofreading-architecture.md) §6.7 포인터 + 프로젝트 메모리 기록.
- **재도입 금지선 유지** — RAG/프롬프트 컨텍스트 주입은 *정량 검증(precision/F0.5) 선결* 없이 도입 금지
  (KoGEC·KAGEC 금지선과 동일 규율).

---

## 7+. 구현·검증 로그

### 2026-06-22 — DO-1 (휴면 로더 + 빌더) 구현·검증 완료
- **신규(파이프라인 미변경·순수 추가):**
  - `core/userdict.py` — 휴면 로더(GUI-agnostic, 스레드 로컬 커넥션, `eomun_rules.py` 미러).
    `available()`·`lookup_pair`·`batch_lookup_pair`·`pair_info`·`is_exception`·`exception_set`(lru_cache)·
    `snapshot_version`·`status`. DB 부재/오류 시 빈 결과·None·False(graceful).
  - `build_userdict_db.py` — 스냅샷 JSON(`{version,pairs,exceptions}`)→SQLite. 멱등 DROP-재생성,
    stdout UTF-8 재설정. **동형이의어 가드 상속**(`build_eomun_db._is_standard_headword`/`_exists_in_stdict`):
    nonstd가 stdict 표준 표제어면 페어 제외, norm 미등재면 폐기. 예외(E)는 억제 전용이라 dedup만.
    스냅샷 부재 시 빈(유효 스키마) DB 생성(로더 graceful 보장).
  - `data/userdict/snapshot.example.json` — 형식 예시/빌드 검증용(자동 로드 ❌, 기본 경로는 `snapshot.json`).
- **★검증(실측):**
  - 가드 동작 — 예시의 `있다→이따`(실표준어)는 **가드가 제외**, `플랫홈→플랫폼`(국어사전에 단어로
    없음)은 **생존**. ⇒ "결정론 페어는 *모호하지 않은 비표준→표준*만, 동형이의어(결재/결제)·표준↔표준은
    context_dependent(AI) 또는 예외(E)로"가 코드로 강제됨을 확인.
  - 로더 — `batch_lookup_pair({플랫홈,있다,콘텐츠})={플랫홈:플랫폼}`, `is_exception('매출액','spacing')=True`,
    `exception_set('spacing')`이 'all' scope('표준국어대사전')를 포함. py_compile 통과.
  - **휴면 확인** — 기본 빌드(스냅샷 부재)로 빈 DB(페어 0·예외 0) 생성 시 모든 조회가 빈 결과 →
    DO-2 배선 전까지 런타임 영향 0. 어떤 기존 모듈도 `core/userdict`를 아직 import하지 않음.
- **다음(DO-2):** `proofreading_worker [5.6]` 페어 합류 + 예외의 ②/⑤/⑦ 억제 배선(저위험, 승인 후).

### 2026-06-23 — DO-2 (워커 합류 + 예외 억제) 구현·검증 완료
- **변경(기존 결정론 패스 자리에 합류 — graceful 순수 추가):**
  - **P. 사내 페어** `proofreading_worker [5.6]`(신규, `[5.7]` norm_map *직전*) — `userdict.batch_lookup_pair`로
    문서 어절(+`_strip_josa` 기본형) 조회 → `source="dict"`, `confidence="high"`, `category` 보존
    (없으면 '사내용어'), `reason="[사내 용어] '<key>'는 사내 표준 표기 '<norm>' 권장 (USR-id)"`.
    조사 보존(`norm+josa`). ⚠ **국가 표준 우선** — 매칭 키만 `batch_lookup_norm`/`batch_lookup_eomun_pair`로
    재조회해 *다른* 값이면 페어를 **양보(skip)+충돌 로깅**(후속 `[5.7]/[5.8]`이 표준 적용). `existing` 중복 가드.
  - **E. ② 재검증** `nikl_dict.KoreanDictValidator.validate` — `_get_userdict()`(휴면 로더 미러) 추가,
    토큰 검증 루프에서 `is_exception(clean,"all")`이면 invalid 수집 제외 → 조직 승인 표기는 비등재라도 **low 강등 안 함**.
  - **E. ⑤ 안전망** `proofreading_worker [6]` — `exception_set("all")` 로드, 예외 표제어는 `dict_flag` 검수카드 **억제**(로그에 'n건 제외' 표기).
  - **E. ⑦ 띄어쓰기** `proofreading_worker [7]` — `exception_set("spacing")` 로드, `find_spacing_suggestions`(분리)·
    `find_compound_spacing_consistency`(다수표기 통일) 양쪽에서 예외(scope spacing⊇all) 표제어 **억제**(조사형은 `_strip_josa`로도 매칭).
- **★검증(실측, 임시 스냅샷 빌드 후 모듈 직접 호출):**
  - 빌딩블록: 플랫홈→플랫폼 생존·있다→이따 **가드 제외**, `pair_info.rule_id`, `is_exception`/`exception_set` 스코프 포함관계 정확.
  - **②(실코드):** corrected에 사전 미등재 토큰 2개(예외 1·비예외 1) — 예외='케이에스프루프'는 **high 유지**,
    비예외='케이에스프루브'는 **low 강등**. (둘 다 미등재임을 사전조회로 전제 확인 → 변별은 예외뿐).
  - **[5.6] 알고리즘:** ⓐ 표준 무충돌 → '플랫홈을'→'플랫폼을'(조사 보존) ⓑ 표준이 다른 값('플랫포옴') → 페어 양보·충돌 1 로깅
    ⓒ 이미 merged에 있는 original → 중복 추가 0.
  - **⑤/⑦:** 케이에스프루프 검수카드 억제·비예외 통과 / 매출액·매출액을 띄어쓰기 제안 억제·비예외('정책보고서') 통과.
  - **★graceful 무회귀(빈 DB로 리셋 후):** pairs0/exc0 — `batch_lookup_pair={}`·`exception_set=∅`·`is_exception=False`,
    네 군데 삽입점 전부 무동작. 기존 validate 강등 동작은 그대로(control='케이에스프루브' low) → **무회귀 실증**.
    `py_compile` 통과(proofreading_worker·nikl_dict·userdict). userdict.db는 다시 휴면(v0).
- **다음(DO-3):** 이벤트 캡처 + 로컬 큐(`data/event_queue.db`) — review/auto_apply에서 용어 단위 이벤트 수집(오프라인, 업로드 없음).

### 2026-06-23 — DO-3 (이벤트 캡처 + 로컬 큐) 구현·검증 완료
- **신규 `core/event_queue.py`(GUI-agnostic, 전 구간 graceful·예외 무전파):**
  - `build_events(corrections, *, doc_type, snapshot_ver)` — **순수 함수**(DB 무관). status가
    accepted/reject인 항목만 **용어 단위** 이벤트로 추출. `dict_flag`(검수카드 original==corrected)·
    `ai_polish`(문장단위)·`pending`(미결정)·무변경 제외. **lemma 정규화** `_lemma_pair`: 단일 어절에
    `morph.strip_josa` 적용, original에서 뗀 조사가 corrected 끝에도 있으면 양쪽 동시 절단(정합) —
    '훗가이도현의→홋카이도현의'⇒'훗가이도현→홋카이도현'. `suggest_src`: reason `[사내 용어]`→**userdict**,
    `ai*`→ai, spacing→spacing, 그 외→dict. **문맥 스니펫 일절 미저장**(프라이버시 §2).
  - `record(events)`/`record_corrections(corrections, doc_type=)` — SQLite 적재(synced=0), 적용 당시
    `userdict.snapshot_version()` 동반. `pending(limit)`/`mark_synced(ids)`/`count_pending()`/`status()`(DO-4 push 준비).
  - DB는 **런타임 생성**(빌드 산출물 아님) — 쓰기 가능 위치(dev=레포 data/, frozen=exe 옆 data/, _MEIPASS 회피),
    멱등 스키마(CREATE IF NOT EXISTS). org_id/user_id는 DO-4에서 채움(현재 빈 값).
- **배선:** `main_window._capture_correction_events()`를 `_start_apply()`에서 호출(review·auto_apply **공통 경로**).
  **순수 부수효과** — try/except 이중 가드로 어떤 실패도 교정 적용을 막지 않음. n>0일 때만 activity 로그.
  검토 패널이 같은 dict 객체에 status/skip_occurrences를 in-place로 써서 `self._corrections`가 최종 결정 보유.
- **★검증(실측, 격리 임시 DB):** 16체크 PASS — ①추출/필터(3건만; dict_flag·polish·pending·무변경 제외)·거절 보존·
  userdict 출처·doc_type/snapshot 동반·**lemma 정규화 2건**(kiwi on) ②record/pending/mark_synced/status 라이프사이클
  ③graceful(빈/None 입력 0) ④record_corrections 편의(스냅샷 자동 동반). 레포 data/event_queue.db **무오염**(런타임 생성 전).
- **다음(DO-4):** Supabase 백엔드(스키마·RLS·Auth·집계) + `userdict_sync.py`(pending→push, snapshot→pull→build_userdict_db).

### 2026-06-23 — DO-4 (Supabase 백엔드 + 클라이언트 동기화) 구현·검증 완료
- **호스트 결정:** 무료 플랜은 활성 2프로젝트(계정 전역) 상한 → 신규 불가. **기존 `ks-works`
  프로젝트 재사용**(ref `ogcwpfkrimzdjsjledtv`). 이미 Supabase Auth(`auth.users` 12) + `public.employees`
  (활성 11=admin 1·employee 10)가 있어 신원/RLS를 그대로 상속. 모든 테이블 `userdict_` 접두(공유 격리).
  단일 조직 → `org_id`는 상수(`public.userdict_org_id()`), 멤버십/큐레이터는 employees로 판정.
- **DO-4a 마이그레이션 `userdict_shared_brain_schema` + `userdict_harden_helper_functions`:**
  테이블 `userdict_events`(추가전용 투표, 문맥 없음·user_id DEFAULT auth.uid())·`userdict_candidates`
  (후보 큐, 부분 unique: pair=(org,original,corrected)/exception=(org,term,scope))·`userdict_snapshots`
  (ver·payload·sha256). RLS: events=본인 INSERT(활성멤버)+큐레이터 SELECT(UPDATE/DELETE 정책없음=추가전용),
  candidates=큐레이터 SELECT+UPDATE, snapshots=멤버 SELECT. INSERT(candidates/snapshots)는 SECURITY DEFINER
  함수 전담. 헬퍼 `userdict_is_member()/is_curator()`(employees 조회, search_path=''), 큐레이터=`role='admin'`.
  하드닝: helper search_path 고정 + anon/public EXECUTE 회수(authenticated만). ★advisor: 신규 객체 관련 잔여
  경고는 authenticated가 RLS 평가에 EXECUTE가 필요한 구조적 항목뿐(기존 is_admin 등과 동일 패턴, 수용).
- **DO-4b/c 마이그레이션 `userdict_aggregate_and_snapshot_fns`:**
  `userdict_aggregate_candidates(K=3,θ=0.8,θ_low=0.5)` — events→candidates 합의 집계
  (A=수락distinct/R=거절distinct/agreement). pair: A≥K&agr≥θ→pending / θ_low≤agr<θ→context_dependent.
  exception(거절다수): R≥K&agr<θ_low→term=original(scope=띄어쓰기?spacing:all). 큐레이터 결정(active/rejected)
  은 재집계가 보존. `userdict_build_snapshot()` — active 후보→{version,pairs,exceptions} payload(빌더 입력과 동일)
  + ver(org별 max+1) + sha256(코어 sha256, pgcrypto 불필요). 둘 다 SECURITY DEFINER + 큐레이터/서비스만.
  ⚠ 동형이의어 가드·정답형 사전등재는 stdict가 있는 **클라이언트 빌드타임(build_userdict_db.py)** 전담
  (서버는 합의 수치만; guard_flags/goldset_pass는 DO-5/6에서 채움).
- **DO-4d `core/userdict_sync.py`(GUI-agnostic, stdlib urllib, graceful·예외무전파) + ConfigLoader.get_supabase():**
  url/anon_key는 배포본 내장(공개, RLS가 보호), email/password는 config.ini [SUPABASE]/env. `push()`(pending→
  POST userdict_events, user_id/org_id는 서버 기본값 위임·event_id 전송으로 멱등 ignore-duplicates·후 mark_synced),
  `pull()`(최신 snapshot→ver 비교→payload를 snapshot.json 저장→build_userdict_db 재빌드), `sync()`=push+pull.
  넷 중 하나라도 비면 available()=False(no-op). 배선 `ui/workers/sync_worker.py`(QThread): MainWindow 시작 시
  pull, `_capture_correction_events` 후 push(둘 다 백그라운드·미설정 시 즉시 종료). closeEvent에서 단명 워커 대기.
- **★검증(실측):**
  - **RLS(실사용자 임포nate, 롤백):** 6/6 PASS — 일반직원 본인 INSERT 허용·타인 user_id INSERT 거부·candidates
    가시 0행·snapshots SELECT 허용; admin은 is_curator=true·candidates SELECT 허용.
  - **집계+스냅샷 파이프라인(합성표 → 롤백):** 7/7 PASS — 3수락→pending(agr 1.0)·3수락1거절→context_dependent·
    2수락(<K)→후보없음·3거절→exception(term=original)·active 승인 후 build→ver1/pairs1/exc1/payload nonstd=플랫홈.
  - **클라이언트(graceful·monkeypatch·라이브):** push 2건 정확 payload(user_id/org_id 없음·event_id 있음·
    ignore-duplicates)·mark_synced로 큐비움; pull 가짜 v5→userdict.db 빌드(플랫홈→플랫폼·매출액[spacing]·ver5);
    **라이브 anon GET=200 [](엔드포인트 도달+RLS가 anon 비노출 실증)**; 미설정 시 전부 no-op. py_compile 통과.
  - 검증 후 서버 테이블 0/0/0·로컬 userdict.db 휴면(v0)으로 무오염 확인.
- **다음(DO-5):** `ui/widgets/curator_panel.py`(role='admin'만) — candidates 큐 뷰 + 승인(active)/반려/문맥의존 +
  골드셋 게이트 결합 + `userdict_build_snapshot()` 트리거. (DO-6: eval/userdict_regression.py.)

### 2026-06-23 — DO-5 (큐레이터 패널) 구현·검증 완료
- **코어 큐레이터 연산** `core/userdict_sync.py` 확장(인증 세션 전용, graceful·예외무전파):
  `is_curator()`(rpc), `list_candidates(statuses)`(GET), `set_candidate_status(cand_id,status)`(PATCH —
  decided_by=JWT sub·decided_at), `aggregate()`/`build_snapshot()`(rpc). `_jwt_sub()`로 토큰에서 uid 추출.
- **가드 게이트** `build_userdict_db.guard_check_many(pairs)` — stdict 1회 연결로 페어 일괄 검사(빌드타임과
  동일 기준: nonstd 표준표제어면 실패·norm 미등재면 실패). 패널이 승인 전 노출 + 가드 실패 페어는 **승인 버튼 비활성**
  (= #1 과교정 리스크의 사람-앞 방어선; 서버 집계엔 가드가 없으므로 클라이언트가 책임).
- **비동기 워커** `ui/workers/curator_worker.py`(QThread) — is_curator/load(집계+목록+페어 가드 부착)/set/snapshot.
  네트워크·stdict I/O 전부 UI 스레드 밖. 실패는 failed 시그널로만.
- **패널** `ui/widgets/curator_panel.py`(QDialog, 디자인시스템 components 재사용): 헤더(집계·새로고침/스냅샷 배포) +
  후보 카드(종류·원본→교정/term[scope]·카테고리·합의%/수락·거절/참여·가드칩·상태칩) + 승인/문맥의존/반려(낙관적 업데이트·
  실패 시 롤백) + 배포 확인 다이얼로그. 상태별 카드 틴트(승인 녹/반려 적/문맥의존 황).
- **진입** `app_header` 좌측 큐레이션 버튼(`clipboard-check`, 기본 숨김·`set_curator_visible`) + `curator_requested` 시그널.
  `main_window`: 시작 시 `_start_curator_check`(CuratorWorker is_curator)→admin이면 버튼 노출, `_open_curator_panel`로
  패널 1개 토글. closeEvent에서 `_curator_worker` 정리. 드래그존 히트테스트가 QAbstractButton 제외라 좌측 버튼 클릭 정상.
- **★검증(실측):** 코어 19체크 PASS — ①graceful no-op(5연산+잘못된status 거부) ②가드(실 stdict): 플랫홈→플랫폼 통과·
  있다→이따 실패(표준표제어)·정답형 미등재 실패 ③JWT sub 디코드 ④**라이브 anon: rpc/userdict_is_curator=401 permission
  denied(EXECUTE 회수 실증)·GET candidates=200 [](RLS 비노출)** ⑤모킹 구조(is_curator/list/set decided_by·cand_id=eq/
  aggregate/snapshot). 전 파일 py_compile 통과. 레포 무오염(userdict.db 휴면·event_queue.db 없음).
  ⚠ 패널 **육안/라이브-큐레이터 동작 검증은 관리자 credential 설정 후 사용자 실행 필요**(GUI·admin 세션).
- **다음(DO-6):** `eval/userdict_regression.py` — 카논 위험어 무변경(과교정 0) 골드셋 게이트(오프라인, 위반 시 exit 1).
  (현재 가드 게이트가 패널에 결합돼 1차 방어선은 가동. 골드셋은 자동 회귀 측정 보강.)

### 2026-06-23 — DO-6 (골드셋 하니스) 구현·검증 완료 ⇒ **DO 로드맵 1~6 전 단계 완료**
- **`eval/userdict_regression.py`(오프라인·AI 미호출):** 입력=후보/스냅샷 JSON(기본: data/userdict/snapshot.json 있으면
  그것·없으면 snapshot.example.json, `--snapshot`로 지정). 검사 4종:
  - **A. 가드 게이트** — `build_userdict_db.guard_check_many`로 각 페어 검사, 통과분만 배포 대상·탈락분 보고.
  - **B. 무변경 HARD GATE** — 카논 위험어(있다·국가·결제/결재·매출액 등 24) + **stdict 표준 표제어 표본 300**에
    가드 통과 페어 적용 → 단 하나라도 바뀌면 과교정 회귀로 **exit 1**.
  - **C. 가드 효과 입증** — 원시(미가드) 페어가 카논을 몇 건 망가뜨릴 뻔했는지(가드가 차단한 과교정 수).
  - **D. 충돌 검사** — 가드 통과 페어 vs norm_map/eomun_pairs 토큰 충돌(국가표준 우선·큐레이터 검토용).
- **★검증(실측):** 예시 스냅샷 — A: 플랫홈→플랫폼 통과·**있다→이따 탈락(표준표제어)**, B: 표본 **324개 위반 0 ✅ PASS**,
  C: 가드가 1건(있다→이따) 차단 입증, D: 충돌 0, exit 0. **음성 테스트**(stdict 경로를 무력화해 가드 우회):
  있다→이따가 통과해 카논 '있다' 손상 → **B 위반 1·exit≠0로 HARD GATE가 실제로 트립**함을 확인(공허한 통과 아님).
- **종합:** 공유 용어 뇌 DO-1~6 완료. 과교정 0 방어선은 **3중**(빌드타임 동형이의어 가드 = build_userdict_db,
  큐레이터 패널 승인 게이트 = DO-5, 골드셋 회귀 = DO-6) + 큐레이터 승인제 + K·θ 합의. 전 구간 graceful·런타임 휴면 유지.
  **활성화 조건:** 각 PC config.ini `[SUPABASE] EMAIL/PASSWORD`(사내 계정) 설정 시 push/pull·큐레이션 가동.

### 2026-06-23 — 선택적 로그인(ks-works 계정 통합) 구현·검증 완료
사용자 요청으로 **사내 근태앱 ks-works와 동일한 Supabase Auth 계정**으로 로그인하는 경로를 추가했다.
ks-works(`KS-Works/ks-works`, Vite+React)는 커스텀 인증이 아니라 **순정 Supabase Auth**
(`supabase.auth.signInWithPassword`, AuthContext.tsx)를 쓰고 `employee_credentials.password_plain`은
관리자용 비번 미러일 뿐임을 코드로 확인 → 동일 password grant 재사용이 안전.

**핵심 결정 — 로그인은 "선택"이지 게이트가 아니다.** 교정(HWP+Gemini)은 로그인 없이 100% 동작하고,
로그인은 **공유 용어 뇌(동기화·큐레이션)만 활성**한다(오프라인 로컬 도구를 회사계정·네트워크에 종속시키는
회귀 방지 — 단점 분석 §리뷰 결론).

- **`core/auth.py`(신규, GUI-agnostic·graceful):** `login(email_or_prefix, password)`(ks-works 규칙: '@' 없으면
  `@kyungsungmedia.com` 부착 → password grant → `employees` 프로필로 role·`terminated_at` 확인, 퇴사자 차단),
  `restore()`(저장 세션 refresh+프로필 재확인), `access_token()`(만료 시 refresh_token 로테이션, config 헤드리스 폴백),
  `current_user()/is_logged_in()/is_curator()`(세션 role), `logout()`. **보안: access_token은 메모리, refresh_token+
  프로필만 Windows DPAPI(현재 사용자·PC 한정)로 암호화 저장(`data/.ks_session`). 비Windows/실패 시 디스크 미저장(평문 금지).
  평문 비밀번호 미저장**(config [SUPABASE] EMAIL/PASSWORD는 선택적 헤드리스 폴백).
- **`core/userdict_sync.py` 전환:** 토큰 공급원을 `auth.access_token()`으로, `available()`=`auth.is_logged_in()`,
  `is_curator()`=세션 role, `set_candidate_status.decided_by`=세션 uid. (기존 config email/password 직접 사인인 제거.)
- **UI:** `ui/widgets/login_dialog.py`(ks-works 동일 UX: 이메일/사번+비번, 도메인 안내, 에러표시) +
  `ui/workers/login_worker.py`(LoginWorker·RestoreWorker). `app_header` 좌측 **로그인/계정 버튼**(로그인 시 이름+⏻=로그아웃)
  + `login_requested`/`logout_requested`. `main_window`: 시작 시 `_start_session_restore`(백그라운드 복원→로그인 상태·동기화·
  관리자면 큐레이션 버튼), `_open_login_dialog`/`_on_logged_in`(로그인 후 sync), `_logout`(세션 삭제·큐레이션 닫기).
- **★검증(실측 23체크 PASS):** ①graceful 미로그인(전부 no-op) ②이메일 정규화(사번→사내도메인) ③**DPAPI 암복호 라운드트립**
  ④모킹 로그인→DPAPI 저장→복원(refresh 토큰 로테이션 AT1→AT2)→로그아웃(파일 삭제) ⑤퇴사자 차단·미저장 ⑥오답 비번 메시지
  ⑦set_candidate_status decided_by=세션 uid + userdict_sync.available()가 로그인 상태 반영. py_compile 전체 통과. 레포 무오염.
- **활성화:** 이제 **config 설정 불필요** — 사용자가 헤더 "로그인"으로 사내 계정 입력. (url/anon은 배포본 내장.) 관리자
  로그인 시 큐레이션 버튼 노출. ⚠ UI 육안/라이브-로그인 동작은 실제 사내 계정으로 GUI 실행해 1회 확인 권장(코어는 실측 완료).
- **남은 단점(수용):** 같은 Supabase 프로젝트 공유라 토큰 탈취 시 ks-works 데이터 접근면 증가(RLS로 행 제한)·두 앱 인증 결합·
  데스크톱 토큰 저장 책임(DPAPI로 완화). 상세는 대화 로그의 단점 분석 참조.

### 2026-06-23 — 로그인 후속 버그픽스 + 검토 UI 3종 (실사용 지적 반영)
실제 로그인 검증(나승현·김정아·김대경 admin 성공) 후 발견된 4건 수정·검증 완료:
- **로그인 시 즉시 종료(크래시) 수정** — 원인 `QThread: Destroyed while running`: `LoginWorker`가 모달
  다이얼로그 소유라 `_on_done`의 `accept()`로 exec 반환→다이얼로그 GC와 함께 **워커 실행 중 파괴**.
  수정: ① login_dialog `_on_done`에서 `self._worker.wait()` 후 accept ② closeEvent 풀 wait + busy 시 취소 비활성
  ③ main_window `_open_login_dialog`가 모달 수명 동안 `self._login_dialog` 강참조 유지. (★헤드리스 repro는
  다이얼로그를 전역으로 잡아 GC가 안 돼 못 잡았음 → 실제는 지역변수라 GC됨. 실 성공경로+GC강제 재현으로 무크래시 확인.)
- **수정 후 적용(edit_accept) UI 배선** — review_panel 카드의 교정값을 **인라인 QLineEdit**로 직접 수정 가능
  (dict_flag 검수카드 제외, auto_apply는 readonly). 편집 시 `correction['corrected']` 갱신+`['_edited']=True`→미리보기
  반영. `event_queue.build_events`: accepted+_edited → **action='edit_accept'**(서버 집계는 accept로 카운트,
  original→수정값이 새 페어 시드). 그전엔 데이터모델만 있고 UI 미배선이었음.
- **검토 3단 그리드 1:1:1 기본크기 깨짐 수정** — 원인=카드 칸 집계 칩 묶음의 자연 너비가 칼럼 최소너비를 키워
  최대화 때만 균등했음. `_chips_box` 너비 제약 해제(`chips_v.SetNoConstraint`+`minWidth0`+`SizePolicy.Ignored`)
  + 3칸 동일 `minWidth(140)` + load 후 `_equalize_splitter` 너지. 실측 1000px→[306,307,307]/1600px→[506,507,507] 균등.
- **큐레이션 패널 미작동 체감** — 패널은 정상 생성(헤드리스 확인)이나 후보0(빈 상태)+창 뒤로 떠서 그렇게 보임.
  `_open_curator_panel`에 `raise_()`/`activateWindow()`/로그('[큐레이션] 패널 열기') 추가. 실제 후보는 이벤트 누적 후 표시.
- ⚠교훈: **단명 위젯(다이얼로그)이 QThread를 소유하면 finished 전 파괴로 크래시** — wait() 또는 장수명 소유+추적 필수.
  전 파일 py_compile + 헤드리스 실측 통과. MainWindow 정상 생성·종료 확인.

---

## 8. 위험과 완화

| 위험 | 완화 |
|---|---|
| 잘못된 공유 규칙이 전원 오염 | 큐레이터 승인제 + K·θ 합의 + 골드셋 무회귀 게이트(3중) |
| 양의 피드백 폭주(과교정) | 정확매칭 결정론(의미 일반화 없음) + 동형이의어 가드 + 거절 다수→예외(억제 방향) |
| 동형이의어 재앙('있다→이따') | original이 stdict 표준 표제어면 페어 제외(`build_norm_map` 가드 상속) |
| 기밀 원고 유출 | 용어 단위만 저장(문맥 스니펫 ❌) + org 테넌트 RLS + 민감 고유명사는 큐레이터 배제 |
| 컨텍스트 주입 유혹 | Gemini 프롬프트 무변경 못박음. KAGEC 금지선 적용 |
| 오프라인/서버 장애 | 로컬 스냅샷으로 동작, 이벤트 큐 보관 후 재동기화(graceful) |
| 국가표준 vs 사내표기 충돌 | 런타임 국가 표준 우선 + 충돌을 큐레이터에 표시(결정 위임) |
| Obsidian 다중쓰기 손상 | 핵심 경로에서 제외. 쓰면 Postgres발 읽기전용 단방향 생성만 |

---

## 9. 참고
- 상위 설계도: [proofreading-architecture.md](proofreading-architecture.md) — 역할 분리·과교정 억제·KoGEC/KAGEC 제거 경위
- 형제 설계도: [eomun-rule-layer-architecture.md](eomun-rule-layer-architecture.md) — 같은 결정론 레이어 자리·가드 선례
- 결정론 페어 가드 선례: `build_norm_map.py`(`_VARIANT_SENSE`)·`build_eomun_db.py`
- Supabase RLS/Auth: https://supabase.com/docs/guides/auth/row-level-security
- (참고·미채택) Obsidian 다중사용자 한계: 공식 문서의 외부 동기화/공유폴더 경고, Relay/Peerdraft(CRDT) 협업 플러그인

*이 문서는 살아있는 설계도다. DO/CHECK 진행 시 갱신한다.*
