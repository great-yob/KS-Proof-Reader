# 어문 규범 지식 레이어 — 이식 아키텍처 (PDCA)

> 목적: 국립국어원 4대 어문 규범(한글 맞춤법·표준어 규정·외래어 표기법·국어의 로마자 표기법)
> + 공식 해설서 + 온라인 가나다 상담사례를 **KS-Proof Reader의 역할 분리 아키텍처에 정확히 이식**한다.
> 바른AI(바이칼에이아이)가 같은 국립국어원 원본을 AI로 재가공한 2차 저작물을 긁는 대신,
> **원본(korean.go.kr/kornorms·공식 PDF)에서 직접** 구조화한다.
>
> 이 문서는 [proofreading-architecture.md](proofreading-architecture.md)의 **하위 설계도**다.
> 상위 설계도의 역할 분리(사전=항상-on 인프라 / AI=생성 엔진)·과교정 억제 원칙을 **그대로 상속**한다.
> 작성: 2026-06-22.

---

## 0. 핵심 결론 — "어디에 꽂는가"

어문 규범 자료는 **규칙·예시·해설의 지식 베이스**다. 상위 설계도의 철칙(과교정 억제 최우선,
KoGEC·거리기반 치환이 환각으로 영구 제거됨)에 비춰, 안전하게 이식되는 자리는 **딱 셋**이다:

| 역할 | 구현 | 항상-ON? | 위험 | 비고 |
|---|---|---|---|---|
| **A. KAGEC 규칙 컨텍스트** (주력) | 청크가 트리거한 규칙·예시·조항근거를 Gemini 프롬프트에 주입 | AI 모드일 때 | 낮음 — 생성 *보강*만, 직접 치환 안 함 | 상위 §0·§2-④ 명시 추천 |
| **B. 결정론 규범 페어** (보조) | `오류형→정답형` 중 **단일토큰·동형이의어 가드 통과**분만 `norm_map`과 같은 경로로 | 항상 | 중간 → 강가드로 억제 | `build_norm_map.py` 선례 |
| **C. 회귀 골드셋** (기반) | `correct`=무변경 테스트(과교정 0 측정), `incorrect`=재현율 측정 | 빌드타임(오프라인) | 0 | 상위 §6.5-2 선결조건 |

### ⛔ 채택 금지(안티패턴 — 상위 설계도가 이미 배제한 영역)
1. **두음법칙·구개음화·사이시옷의 규칙 기반 자동치환** — 위치·형태소 의존이 강해 예외가 많다
   (`냥`(의존명사)·단어 중간 본음 유지·합성어 경계…). 정규식 자동치환은 곧 과교정 = 설계도가
   금지한 "거리 기반 추측 치환"의 재림. → **KAGEC 컨텍스트(A)로만** 다룬다(AI가 문맥 판단).
2. **되/돼·안/않·데/대·로서/로써 등 맥락 의존 쌍** — 결정론 페어(B)에서 **반드시 제외**. AI 전담.
3. **bareun.ai/qa 스크랩** — 안내문에 "국립국어원 공개자료를 AI가 재구성한 2차 저작물"이라 명시.
   정보 손실·왜곡 + 타사 가공비용 무단사용. **원본 국립국어원에서 직접** 가져온다.
4. **mcfaq 상담사례를 런타임 규칙으로** — 문맥 의존·개별 질의라 결정론 규칙 부적합.
   → 회귀 골드셋(C) + (선택) 규칙 검색 보강용 코퍼스로만.

---

## 1. 데이터 소스 인벤토리와 라이선스

| 소스 | 형태 | 분량 | 본 아키텍처에서의 용도 | 라이선스/주의 |
|---|---|---|---|---|
| **4대 규범 원문** (`kornorms`, `regltn_code` 0001~0004) | 조항+공식해설+예시 | 4규정, 유한 | A 규칙카드·B 페어·조항근거 인용 | KOGL 제1유형(출처표시). 문체부 고시 = 저작권법 §7 보호제외 가능성 |
| **공식 해설서 PDF** `한글 맞춤법 표준어 규정 해설`(2018, ~280p) | `제N항→원문→해설→더알아보기` 반복 | 3규정 통합 | **A의 1차 소스**(원문+해설+예시) | 정부 발간물. 본문 전량 복제 금지 — *구조 파싱*만 |
| **온라인 가나다 상담사례** (`mcfaqList.do?mn_id=217`) | Q&A | 2,452건 | C 골드셋 + (선택) 검색 코퍼스 | KOGL, "자료 출처: 국립국어원" 병기 권장 |
| **공공데이터포털 Open API** "한국어 어문규범 시스템 규정 정보" | 구조화 파일 | — | A의 *대안 1차 소스*(스크랩 불필요 시 최우선) | KOGL 제1유형 |
| ~~bareun.ai/qa~~ | — | — | **사용 안 함**(2차 저작물) | — |

**준법 체크리스트(빌드 전 1회):**
- [ ] `korean.go.kr/robots.txt` 직접 확인 후 크롤러 간격(`SLEEP_SEC`) 준수.
- [ ] 공공데이터포털 Open API로 규정 원문을 *정식 채널*로 받을 수 있는지 우선 확인(스크랩 회피).
- [ ] 배포물·정오표 어딘가에 "어문 규범 데이터 출처: 국립국어원" 출처표시.
- [ ] mcfaq를 제품에 동봉 시 KOGL 범위 재확인(연구/내부평가 vs 재배포).

---

## 2. 데이터 모델 — 제공 스키마의 정밀 고도화

원본 제공 스키마(`source_doc/chapter/section/article_no/rule_text/gloss/examples{correct,incorrect}/source_url`)는
**검색 키와 라우팅 정보가 없다** — "문서의 이 어절에 어떤 규칙이 걸리는가"를 모른다. 다음을 추가한다.

```jsonc
{
  "rule_id":     "HM-0006",            // 안정 식별자 (규정코드-조항). 골드셋·로그·정오표 인용에 사용
  "regulation":  "한글 맞춤법",          // 0001 한글맞춤법 / 0002 표준어 / 0003 외래어 / 0004 로마자
  "notice_no":   "문화체육관광부 고시 제2017-12호",
  "chapter":     "제3장 소리에 관한 것",
  "section":     "제2절 구개음화",
  "article_no":  6,
  "rule_text":   "…조항 원문 1~2문장…",
  "gloss":       "…공식 해설 요약…",
  "examples":    { "correct": ["맏이","해돋이"], "incorrect": ["마지","해도지"] },

  // ── 고도화로 추가되는 필드 ───────────────────────────────
  "triggers":    ["마지","해도지"],     // 이 표면형이 청크에 나오면 규칙 활성(검색 인버티드 인덱스 키)
  "deterministic": false,             // true면 examples의 단일토큰 오류→정답을 B(결정론 페어)로 승격 가능
  "category":    "맞춤법",             // 맞춤법|띄어쓰기|표준어|외래어|로마자 (Correction.category와 정합)
  "context_dependent": true,          // true면 결정론 절대 금지(되/돼·두음 위치의존 등). 빌더가 강제 검증
  "priority":    2,                   // 1=핵심(자주 트리거)·2=일반·3=희귀. 프롬프트 주입 상한 시 정렬키
  "source_url":  "https://korean.go.kr/kornorms/regltn/regltnView.do?regltn_code=0001"
}
```

**고도화 핵심 4가지:**
1. **`triggers`(인버티드 인덱스 키)** — KAGEC 검색의 심장. 보통 `examples.incorrect` ∪ (필요 시 정답형 변형).
   런타임은 청크 텍스트에 이 표면형이 있을 때만 규칙을 주입 → 프롬프트 비대화·주의분산 방지(엔진의
   기존 "청크에 실제 등장하는 의심단어만 주입" 최적화와 동일 철학).
2. **`deterministic`/`context_dependent` 라우팅** — 빌더가 결정론 페어(B) 채택 여부를 **데이터에서** 결정.
   `context_dependent=true`면 `deterministic`은 무조건 false로 강제(빌더 검증). 과교정 안전판.
3. **`category` 정합** — `Correction.category`와 같은 어휘로 통일(맞춤법/띄어쓰기/표준어/외래어). 정오표·UI 일관.
4. **`rule_id`** — 골드셋 케이스·로그·정오표가 규칙을 안정적으로 인용. 회귀 추적 가능.

---

## 3. 저장 구조 — `data/eomun.db` (stdict.db와 분리)

`norm_map`은 `stdict.db` 안에 있지만(같은 우리말샘 소스 파생), 어문 규범은 **다른 소스(kornorms/PDF)**이고
`setup_dict.py`/`update_opendict.py`가 stdict를 DROP-교체하므로 **빌드 사이클을 독립**시킨다. 별도 `data/eomun.db`.

```sql
-- 규칙 카드 (A: KAGEC 컨텍스트)
CREATE TABLE rules (
  rule_id      TEXT PRIMARY KEY,
  regulation   TEXT, chapter TEXT, section TEXT, article_no INTEGER,
  rule_text    TEXT, gloss TEXT,
  examples_ok  TEXT,            -- JSON 배열
  examples_bad TEXT,            -- JSON 배열
  category     TEXT, priority INTEGER, source_url TEXT
);
-- 트리거 인버티드 인덱스 (A 검색)
CREATE TABLE triggers (
  surface  TEXT,                -- NFC 정규화된 오류 표면형
  rule_id  TEXT,
  PRIMARY KEY (surface, rule_id)
);
CREATE INDEX idx_triggers_surface ON triggers(surface);
-- 결정론 규범 페어 (B) — norm_map과 동일 형상, 동형이의어 가드 통과분만
CREATE TABLE eomun_pairs (
  nonstd  TEXT PRIMARY KEY,
  norm    TEXT NOT NULL,
  rule_id TEXT                  -- 근거 조항 (정오표 인용)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);   -- built_at, rule_count, source_rev
```

배포는 **`eomun.db`만** 동봉(JSONL·PDF는 빌드 전용). DB 없으면 모든 기능 graceful no-op.

---

## 4. 런타임 통합 지점 (정확한 좌표)

### A. KAGEC 규칙 컨텍스트 주입
- **`core/eomun_rules.py`** (신규, GUI-agnostic) — `eomun.db` 로더 + 검색.
  - `available() -> bool` (DB 존재)
  - `retrieve(chunk_text, *, limit=12) -> list[RuleCard]` — 청크에 등장한 `triggers` 표면형으로
    규칙 조회, `priority`·트리거 수로 정렬해 상한 `limit`만 반환(프롬프트 예산 보호).
  - `render(cards) -> str` — 규칙카드를 프롬프트용 간결 텍스트로(조항·rule_text·정/오 예시 2~3개·근거).
  - kiwipiepy/사전처럼 **미설치/미존재 시 빈 결과**(graceful).
- **`core/prompts.py`** — `PROMPT_INTEGRATED`에 `{rules}` 슬롯 신설(§"적용 어문 규범" 블록).
  `build_integrated_prompt(text, suspicious_words, glossary, rules="")` 시그니처 확장(기본값으로 하위호환).
- **`core/gemini_checker.py`** `check_typo_integrated.prompt_tmpl`(현 69~74행) — 청크별로
  `eomun_rules.retrieve(chunk)`→`render` 결과를 `build_integrated_prompt(..., rules=...)`에 전달.
  **워커(`proofreading_worker`)는 무변경** — 검색이 엔진 내부에서 일어나므로 시그널/흐름 안 건드림.

> 효과: AI가 "왜 콘텐츠인가"를 *조항 근거와 함께* 판단 → 교정 정확도↑ + `reason`에 "한글 맞춤법 제N항"
> 형식 근거가 실제로 채워짐(현재는 AI가 추정). 과교정 위험은 낮음 — 컨텍스트는 *생성을 안내*할 뿐 강제 치환이 아님.

### B. 결정론 규범 페어 (norm_map과 합류)
- **`nikl_dict.py` 또는 `core/eomun_rules.py`** — `lookup_eomun_pair(word)`/`batch_lookup_eomun_pair(set)`,
  `lookup_norm`/`batch_lookup_norm`(현 175~217행)과 동일 시그니처.
- **`ui/workers/proofreading_worker.py` `[5.7]`**(현 253~288행) — norm_map 처리 직후/직전에
  `eomun_pairs`도 동일 루프로 조회해 `source="dict"`, `category="규범표기"`, `confidence="high"`,
  `reason="[규범표기] '…' — {규정} 제{N}항"` Correction 추가. **동형이의어 가드는 빌드타임에 이미 적용**됨.
- **신규 자동치환 표면을 늘리지 않는 게 원칙** — B는 사실상 norm_map의 *근거 보강·소폭 확장*이다.
  외래어(컨텐츠→콘텐츠 등)는 norm_map이 이미 커버 → 중복은 무해(같은 결과), eomun_pairs는 *조항근거*를 더한다.

### C. 회귀 골드셋
- **`eval/eomun_regression.py`** (신규, 오프라인) — §6 참조. 파이프라인 변경 전후 정밀도/재현율/F0.5 측정.

---

## 5. 빌드 파이프라인 (소스 → eomun.db)

```
[원본]                          [파서/빌더]                    [산출물]
공식 해설 PDF ───────────────▶ parse_haeseol_pdf.py(개선) ──┐
kornorms 4대 규정 ───────────▶ (수기 시드 + Open API 우선)  ─┼─▶ data/eomun/*.jsonl
mcfaq 2,452건 ───────────────▶ crawl_korean_go_kr_mcfaq.py ─┘        │
                                                                     ▼
                                                          build_eomun_db.py
                                                          · NFC 정규화·dedup
                                                          · triggers 인버티드 인덱스 생성
                                                          · 동형이의어/맥락의존 가드로
                                                            deterministic 페어 선별
                                                                     │
                                                                     ▼
                                                              data/eomun.db
```

**`build_eomun_db.py` 채택 규칙(안전 우선 — `build_norm_map.py` 가드 상속):**
- `eomun_pairs`(B) 채택 조건(전부 충족):
  1. `examples`에서 추출한 단일토큰 1:1(`오류형`,`정답형`), 둘 다 한글 포함, `len≥2`, 공백 없음.
  2. `context_dependent != true` **그리고** `deterministic == true`(데이터 명시 동의).
  3. **동형이의어 가드** — `오류형`이 `stdict.db`에 *독립 표준 표제어*로 존재하면 제외
     (`nikl_dict.lookup_word` + `_VARIANT_SENSE` 원리). '있다→이따' 류 재앙 차단.
  4. `정답형`이 `stdict.db`에 등재(정답이 비표준이면 페어 폐기).
- `triggers`(A): 모든 규칙의 `triggers`(또는 `examples.incorrect`)를 NFC로 인덱싱. 맥락의존 규칙도 **A에는 포함**(컨텍스트는 안전).
- 멱등(idempotent): 매 실행 DROP-재생성. `meta.source_rev`로 소스 버전 기록.

**제공 스크립트 개선분(정밀 고도화):**
- `parse_haeseol_pdf.py`: 표(ㄱ/ㄴ 비교표) 깨짐 → `--review` 외에 *2열 병합 휴리스틱* 추가, `rule_id` 부여,
  NFC 정규화, `triggers` 자동 추출(예시 incorrect→triggers).
- `crawl_korean_go_kr_mcfaq.py`: `robots.txt` 자동 점검 게이트, User-Agent 연락처 실값, 재개(resume) 유지,
  KOGL 출처 필드 자동 부착.

---

## 6. PDCA 4단계

### ▣ PLAN — 설계·합의 (현 문서)
**산출물:** 본 설계도, 고도화 스키마(§2), 저장구조(§3), 통합 좌표(§4), 빌드 파이프라인(§5).
**의사결정 고정:**
- 어문 규범 = **A(KAGEC 컨텍스트) 주력 + B(강가드 결정론) 보조 + C(골드셋) 기반**. 자동치환 규칙엔진 ❌.
- 저장은 `data/eomun.db` 분리. 배포는 DB만 동봉. 전 구간 graceful no-op.
- 소스는 **국립국어원 원본**(bareun 2차 저작물 ❌). KOGL 출처표시 준수.
**완료 기준:** 사용자 승인 + §4 통합 좌표가 현 코드와 1:1 매칭됨을 확인(완료 — 4.x 행 모두 실재).

### ▣ DO — 구현 (순서: 무위험 → 저위험)
1. **시드·골드셋·로더(파이프라인 미변경, 순수 추가)** ← *이번 패스에서 구현*
   - `data/eomun/eomun_seed.jsonl` — 고도화 스키마 부트스트랩 시드(외래어·두음·의존명사·맥락쌍 등 검증 가능 핵심).
   - `build_eomun_db.py` — §5 빌더(가드 포함). 실행 시 `data/eomun.db` 생성.
   - `core/eomun_rules.py` — 로더/검색/렌더(휴면 — 아무도 아직 호출 안 함 → 런타임 무영향).
2. **KAGEC 주입 배선(저위험, 가역)** ← *승인 후*
   - `prompts.PROMPT_INTEGRATED` `{rules}` 슬롯 + `build_integrated_prompt` 인자 + `gemini_checker` 청크별 검색.
3. **결정론 페어 합류(저위험, 강가드)** ← *승인 후*
   - `lookup_eomun_pair` + 워커 `[5.7]` 합류. 외래어는 norm_map과 중복(무해), 근거조항만 추가.
4. **본 데이터 확보** ← *승인 후, 사용자 머신*
   - 공식 PDF 다운로드→`parse_haeseol_pdf.py`(개선)→`eomun.db` 재빌드. (선택) mcfaq 크롤→골드셋 보강.

### ▣ CHECK — 검증 (과교정 0이 1순위 지표)
- **무변경 테스트(가장 중요)** — `eval/eomun_regression.py`가 골드셋의 **`correct` 예시**를 파이프라인의
  결정론 구간(사전+norm_map+eomun_pairs, AI 제외=결정론)에 통과시켜 **단 하나도 바꾸지 않음**을 검증.
  하나라도 바뀌면 과교정 회귀 → 빌드 실패 처리. (이 프로젝트 #1 리스크의 자동 방어선)
- **재현율 테스트** — `incorrect` 예시를 넣어 해당 오류가 (결정론이면) 고쳐지거나 (맥락의존이면) 최소한
  KAGEC 트리거로 *검색됨*을 확인.
- **지표** — precision / recall / **F0.5**(GEC는 정밀도 가중). KAGEC 주입 *전/후* 동일 골드셋 비교로
  "AI 정확도가 실제로 올랐는가 & 과교정이 늘지 않았는가"를 정량화.
- **회귀 카논** — 상위 설계도가 추적해온 실측 케이스(상담채녈→상담채널, 고독사≠고지서, 겪고≠묻고,
  있다↛이따)를 골드셋에 **반드시 포함** → 모든 변경이 이들을 깨지 않는지 상시 확인.
- **수동 실모델 1회** — `.\.venv64\Scripts\python.exe main.py`로 실제 `.hwp` 1건 교정, `reason`에
  조항근거가 실제로 채워지는지·신규 과교정이 없는지 육안 확인(상위 설계도 검증 관행).

**수용 기준(전부 충족 시 ACT 승격):**
- 무변경 테스트 **위반 0건**.
- KAGEC 주입 후 골드셋 F0.5 **하락 없음**(가급적 상승), 과교정 카운트 **불변 또는 감소**.
- 실모델 1건에서 신규 환각/과교정 **0건**.

### ▣ ACT — 표준화·확장
- 수용 기준 통과분만 **항상-ON 표준 경로로 승격**. 미달분은 데이터/가드 수정 후 Check 재실행.
- **문서·메모리 갱신** — 상위 [proofreading-architecture.md](proofreading-architecture.md)에 본 레이어 1줄 포인터,
  프로젝트 메모리에 결정·가드·검증결과 1파일 기록(CLAUDE.md의 "작업 전 메모리 필독" 관행 유지).
- **확장 백로그** — 외래어 표기법 언어별 세칙(표 다수 → 별도 파서), 로마자 표기법 변환 검증기,
  mcfaq 검색 보강(임베딩) — 각각 Check 게이트를 다시 통과해야 함.
- **재도입 금지선 유지** — 두음/구개음화 자동치환·규칙엔진 자동적용은 *정량 검증 없이는* 도입 금지
  (상위 설계도의 KoGEC 재도입 금지선과 동일한 규율).

---

## 6+. 구현·검증 로그

### ⚠️ 2026-06-22 — DO-2 KAGEC 컨텍스트 주입 **제거**(역할 A 폐기, B·C만 유지)
KAGEC 규칙 컨텍스트를 AI 프롬프트에 주입한 DO-2가 **AI 오탈자 탐지를 분산**시켜, 사전엔 멀쩡히
잡던 오타(`고지사→고지서`·`훗가이도→홋카이도`)를 놓치는 회귀를 유발했다. 사용자가 `data/eomun.db`
비활성화 시 회귀가 복귀함을 확인해 **인과 입증**. 출판 교정은 정확성 최우선 → **KoGEC 제거와 동일
원칙으로 KAGEC 주입을 제거**(`prompts.PROMPT_INTEGRATED {rules}` 블록·`gemini_checker._retrieve_eomun_rules`
삭제, `build_integrated_prompt`에서 rules 제거). 어문 규범은 **결정론 페어(B)·골드셋(C)으로만** 사용.
재도입은 *정량 검증(precision/F0.5)* 선결. 아래 DO-2 기록은 역사적 보존.



### 2026-06-22 — DO-1 (시드·빌더·로더) 구현·검증 완료
- **신규(파이프라인 미변경·순수 추가):**
  - `data/eomun/eomun_seed.jsonl` — 고도화 스키마 부트스트랩 26규칙(외래어·표준어·두음·구개음화·
    사이시옷·되돼·안않·의존명사/단위 띄어쓰기·로마자). 결정론/맥락의존 라우팅 플래그 포함.
  - `build_eomun_db.py` — §5 빌더(동형이의어·맥락의존 이중 가드, 멱등, stdout UTF-8 재설정).
  - `core/eomun_rules.py` — 휴면 로더/검색/렌더 + 결정론 페어 조회(graceful, GUI-agnostic).
- **빌드 결과:** 규칙 26 · 트리거 35 · **결정론 페어 1**(가드 제외 13).
- **★실증 발견 — "B는 norm_map에 양보한다"가 확인됨.** 동형이의어 가드가 외래어·표준어 비표준형
  (`컨텐츠·메세지·설겆이·강남콩·일찌기` 등 13건)을 결정론 페어에서 제외했다. 이들은 우리말샘에
  *비표준 변이형으로 등재*돼 가드(보수)가 걸렀지만, **이미 `norm_map`이 정의(定義) 기반의 더 정밀한
  가드로 교정**하므로(상위 메모리 ③) 교정 능력 손실 0. 유일 생존 페어 `플랫홈→플랫폼`은 stdict에
  아예 없어 안전하게 신규 채택됐다. → **결정론 페어(B)는 의도대로 최소·보조이며, 시드의 실가치는
  역할 A(KAGEC 컨텍스트)와 C(골드셋)에 있다.** 가드가 #1 리스크(과교정)에 대해 안전 방향(과배제)으로
  실패함을 확인.
- **로더 검증:** `retrieve("…컨텐츠…갈수있다…등교길…")`가 의존명사(제42항)·외래어·사이시옷(제30항)
  규칙을 priority 순으로 정확히 회수, `render()`가 조항 근거 포함 프롬프트 텍스트 생성. 결정론 조회는
  맥락의존/가드제외 항목에 대해 `{}` 반환(정상).
### 2026-06-22 — DO-2 / DO-3 (KAGEC 배선·결정론 페어 합류) 구현·검증 완료
- **DO-2 KAGEC 주입(역할 A):**
  - `core/prompts.py` — `PROMPT_INTEGRATED`에 `═══ 적용 어문 규범 ═══`/`{rules}` 블록 신설,
    `build_integrated_prompt(..., rules="")` 시그니처 확장(빈 값이면 "관련 규범 조항 없음" — 하위호환).
  - `core/gemini_checker.py` — `_retrieve_eomun_rules(chunk)`(graceful) 추가, `check_typo_integrated`의
    `prompt_tmpl`이 청크별로 `eomun_rules.retrieve→render`를 주입. **워커 무변경.**
  - 검증: `build_integrated_prompt`이 청크 관련 조항(제42항·외래어·제30항)을 `{rules}`에 정확 주입,
    빈 입력 하위호환 확인, 무관 문장엔 `_retrieve_eomun_rules`가 `""` 반환(잡음 0).
- **DO-3 결정론 페어 합류(역할 B):** `proofreading_worker [5.8]` 신설 — `eomun_rules.batch_lookup_eomun_pair`로
  norm_map과 동일 패턴의 high-confidence 교정 추가, 근거 rule_id를 reason에 인용. 자기완결적·graceful.
- **DO-4 공식 해설 PDF 파서:** `parse_haeseol_pdf.py`(고도화) — 규정/장/절/항 추적, rule_text/해설 분리,
  **자동 파싱분은 전부 컨텍스트 전용(deterministic=false·context_dependent=true)으로 안전 고정**(표 추출
  깨짐→자동치환 금지 원칙), 예시는 인용부호 안 토큰만 보수 추출→triggers, `--review`. 출력은
  `data/eomun/haeseol.jsonl`(build_eomun_db가 자동 적재). 부 경계는 헤더 줄에서만 전환(본문 오인 버그 수정).
  ⚠ **PDF·pdfplumber 미보유로 실행은 사용자 머신에서** — `pip install pdfplumber` 후 PDF 내려받아 실행.
  로직은 합성 입력으로 검증(한글맞춤법·표준발음법 항 정확 분리, 컨텍스트 전용 플래그 확인).
- **모든 코어 편집 컴파일 통과, eomun.db 부재 시 전 구간 graceful(파이프라인 영향 0).**

### 2026-06-22 — DO-4 본 데이터(공식 해설 PDF) 파싱·적재 완료
- 사용자가 `data/한글맞춤법 표준어규정 해설.pdf`(1.78MB, 264쪽) 제공. `pdfplumber` 설치 후 파싱.
- **목차(TOC) 오염 버그 발견·수정(중요):** 앞 목차/각 부 미니목차의 `장/절` 항목이 part/chapter 상태를
  오염시켜 한글 맞춤법 본문이 **전부 표준어/발음법으로 오분류**됐다(HM 0건). 원인 3종 수정 —
  ① **점선 리더(`·····`) 줄을 잡음 처리**(목차 항목 식별·제거), ② **`PART_HANGEUL` 복원**(본문이 직전
  목차의 발음법 경계에 오염되는 것 차단), ③ part 정규식을 전체줄 앵커+따옴표 허용으로 강화. part는
  '마지막 설정 우선'이라 본문 divider가 (미니)목차를 자동으로 덮어쓴다.
- **결과(정확):** 113항 = **한글 맞춤법 57 + 표준어 규정 26 + 표준 발음법 30**. 장/절 정확 귀속,
  rule_text 깨끗. 트리거 추출 32항/162개(보수적 — 인용부호 토큰만). `data/eomun/haeseol.jsonl`.
- **통합 재빌드:** `eomun.db` = 규칙 139 · 트리거 197 · 결정론 페어 1(가드 제외 13, 불변). 자동 파싱분은
  전부 컨텍스트 전용이라 결정론 페어를 늘리지 않음(설계대로). 두음·구개음화·의존명사·외래어 혼재 문장에서
  priority 순 회수 확인. `pdfplumber`는 빌드타임 전용(requirements.txt 주석, 런타임 불필요).

### 2026-06-22 — CHECK 골드셋 하니스 구현·통과
- **`eval/eomun_regression.py`** — AI 미호출(결정론·오프라인). 3종 검사 + F0.5 + 과교정 위반 시 exit 1(게이트).
- **결과: ✅ PASS** — `precision/recall/F0.5 = 1.000`.
  - **A. 무변경(과교정) 위반 0건** — 시드 `correct` 토큰 + 카논 위험어(있다·없다·국가·외국인·전문가·
    사회문제 등 56개) 중 결정론 교정되는 것 **0** = #1 리스크 자동 방어선 작동.
  - **B. 결정론 재현 14/14** — `컨텐츠→콘텐츠`·`메세지→메시지`·`설겆이→설거지` 등은 eomun_pairs에서
    가드로 제외됐지만 **norm_map이 그대로 교정**(플랫홈→플랫폼만 eomun_pairs) → **"B는 norm_map에 양보,
    능력 손실 0"이 정량 실증**.
  - **C. 컨텍스트 재현 30/30** — 인덱싱된 트리거가 모두 `retrieve()`로 왕복(인덱스 정합).
- **남음:** 실모델 1건 육안 검증(실제 `.hwp`에서 `reason` 조항근거 채움·신규 과교정 0 확인)은 사용자 실행 권장.

---

## 7. 위험과 완화 요약

| 위험 | 완화 |
|---|---|
| 규칙 기반 과교정(두음·구개음화 예외) | 자동치환 금지, KAGEC 컨텍스트(A)로만. 결정론(B)은 동형이의어+맥락의존 이중 가드 |
| 동형이의어 재앙('있다→이따') | 빌더가 `stdict.db` 표준 표제어 존재 시 페어 제외(`build_norm_map` 가드 상속) |
| 프롬프트 비대화·주의분산 | 청크 트리거 매칭분만, `priority` 정렬 상한(`limit`) 주입 |
| 데이터 부정확(PDF 파싱 깨짐) | `--review` + 무변경 테스트(C)로 잘못된 페어가 과교정 내면 빌드 실패 |
| 라이선스 | 원본 사용·KOGL 출처표시·robots 준수·동봉 범위 사전확인(§1) |
| DB 부재/구버전 | 전 구간 graceful no-op(사전·kiwi와 동일 degradation 규율) |

---

## 8. 참고
- 국립국어원 어문 규범: https://korean.go.kr/kornorms (regltn_code 0001~0004)
- 공식 해설 PDF: `한글 맞춤법 표준어 규정 해설`(국립국어원, 2018, 11-1371028-000712-01)
- 온라인 가나다 상담사례: https://korean.go.kr/front/mcfaq/mcfaqList.do?mn_id=217
- 공공데이터포털: "문화체육관광부 국립국어원_한국어 어문규범 시스템 규정 정보"(data.go.kr)
- 상위 설계도: [proofreading-architecture.md](proofreading-architecture.md) — 역할 분리·과교정 억제·KoGEC 제거 경위

*이 문서는 살아있는 설계도다. DO/CHECK 진행 시 갱신한다.*
