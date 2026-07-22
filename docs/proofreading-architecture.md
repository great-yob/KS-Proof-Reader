# KS-Proof Reader — 교정교열 아키텍처 설계도

> 목적: "AI 보조 + 사전 옵션" 구조를 **사전(항상-on 인프라) + AI(생성 엔진)** 의 역할 분리형
> 하이브리드로 진화시킨다. 이 문서는 결정 배경·목표 구조·단계별 로드맵을 담는다.

---

## ⚠️ 2026-06-17 — KoGEC / 앙상블 / 엔진선택 전면 제거 (현행 = Gemini 단독)

아래 Phase 3·3.5·§5(4)·§6의 **KoGEC(NLLB GEC)·앙상블 교차검증·엔진 선택은 모두 제거됐다.**
실모델 테스트에서 KoGEC가 어절 단위로 **환각·과교정**을 일으켰고('사회문제에'→'사회문제제에서',
'홍보전략을'→'전략을', '위한'→'홍보'), 앙상블 교차검증의 합치율도 **1/39** 수준이라 검증 도구로서
노이즈가 압도적이었다. 출판 교정은 정확성·신뢰도가 최우선 → 미검증 생성모델을 파이프라인에서 뺐다.

- **제거 항목**: `core/kogec_engine.py`(파일 삭제), `correction_engine`의 `EnsembleEngine`·
  `cross_validate`·`_make_kogec`, `config_loader`의 `get/set_engine_provider`·`get_kogec_*`,
  설정 UI 엔진 콤보, `morph.split_sentences`, `requirements`의 torch/transformers, `config.ini [ENGINE]`
  섹션, source `ai_kogec` 라벨/색.
- **현행 생성 엔진 = Gemini 단독** — `GeminiEngine` + `build_engine(api_key)`. AI scope 0개면 사전 검수 모드.
- 아래 Phase 3·§6 본문은 **역사적 기록**으로 보존(왜 시도했고 왜 뺐는지). **재도입 금지** — 재도입하려면
  *출판 도메인 파인튜닝 + 정량 검증(precision/F0.5)* 이 선결 조건이다. (kiwipiepy 형태소 분석은 사전
  검증용으로 계속 사용 — KoGEC와 무관.)

---

## 0. 결론 요약 (왜 이 구조인가)

국내·해외 교정교열/GEC 연구와 실무가 수렴하는 지점:

- **사전/규칙 기반**은 정밀도·설명가능성·일관성이 높지만 **real-word error(붇고/묻고/겪고처럼 둘 다 사전에 있는 단어)** 와
  띄어쓰기·비문·문맥을 못 잡는다. → *탐지·검증엔 강하고, "무엇으로 바꿀지(생성)"엔 약하다.*
- **LLM**은 문맥·생성에 강하지만 **과교정(overcorrection)** 이 구조적 약점이다(우리가 실제로 겪음: 고독사→고지서, 겪고→묻고).
- 현재 주류는 **지식 증강 하이브리드(KAGEC)**: 사전/검색을 LLM 프롬프트에 주입 → 한국어 GEC 성능 향상.
  전용 모델(KoGEC, NLLB 파인튜닝)은 BLEU 85.73으로 GPT-4o(75.03)·HCX-3(71.24)을 능가.

→ **사전을 끄고 켜는 건 본말전도다. 사전은 항상 켜는 토대로 두고, "생성"을 담당하는 AI를 선택형으로 둔다.**

참고 문헌은 문서 끝 [§7](#7-참고-references) 참조.

---

## 1. 설계 원칙

1. **역할 분리(Separation of Concerns)** — 각 엔진을 *잘하는 일에만* 배치한다.
   - 사전 = 탐지(detection) · 재검증(validation) · 안전 가드(guard) · AI 컨텍스트(context)
   - AI(Gemini/KoGEC) = 생성(generation) · 문맥 판단(context reasoning)
2. **사전은 인프라, AI는 기능** — 사전 3대 역할은 토글 없이 항상 동작. 사용자 토글은 "AI/심층 스크리닝"에만.
3. **생성은 위험하다, 보수적으로** — "거리 기반 추측 치환"(과거 Case B)은 금지. 생성 결과는 반드시 사전 재검증을 거친다.
4. **설명가능성** — 모든 교정은 근거(사전 등재/미등재, 규칙, AI 사유)를 정오표에 남긴다.
5. **오프라인 degradation** — API 키가 없거나 끊겨도, 사전 기반 "검수 모드"로 동작 가능해야 한다.

---

## 2. 사전의 3대 역할 (항상 ON)

| 역할 | 구현 위치 | 설명 | 비용 |
|---|---|---|---|
| ① 탐지/플래그 | `nikl_dict.extract_suspicious_words` | 원문 미등재·비표준 어휘 검출. 사전의 **기본 베이스** 역할 | 무거움(전 어휘 대조) → **항상**(성능 튜닝 후속) |
| ② 재검증 | `nikl_dict.KoreanDictValidator.validate` | AI 생성 결과의 *목표어*가 비표준이면 `confidence=low` | 저렴 → **항상** |
| ③ 안전 가드 | `core/consistency_pass._build_known_words` | 일관성 전파가 표제어를 훼손하지 않게 차단 | 저렴(배치 1회) → **항상** |
| (④ AI 컨텍스트) | ①의 결과를 프롬프트에 주입(KAGEC) | LLM 정밀도 보강 | ①에 종속 |
| ⑤ 사전 안전망 | `proofreading_worker` `[6]` | ①이 잡은 미등재어 중 **AI가 안 고친 것**을 `dict_flag` 검수 카드로 노출 | 저렴 → **항상** |

> **①·②·③·⑤ 모두 항상 ON.** 사전은 옵션이 아니라 매 실행 도는 기본 도구다. AI(생성)가 선택 레이어.
> ⚠ **2026-06-16 갱신 — ① 탐지를 옵트인에서 항상-ON으로 되돌림.** 이전 결정은 ①이 비싸고(~47초)
> 효과 변동성이 커서 `deep_screening` 옵션(기본 OFF)으로 뒀으나, 이로 인해 사전이 원문을 직접 훑는
> 유일한 단계가 기본 실행에서 꺼져 **치명적 미탐**이 발생했다(예: '상담채녈'→'상담채널'을 AI가 놓치자
> 안전망 0개로 통과). 사용자 의도("사전이 기본, AI가 옵션")에 맞춰 ①을 항상-ON 베이스로 복권하고,
> AI가 놓친 미등재어를 검수 카드로 띄우는 ⑤ 안전망을 추가했다. **성능 튜닝(캐싱·병렬화)은 후속 과제 — 정확성 우선.**

---

## 3. 목표 데이터 흐름

```
                ┌─────────────────────────────────────────────────────────┐
                │  HWP 추출 (32bit 브리지)                                  │
                └───────────────┬─────────────────────────────────────────┘
                                │ text
          ┌─────────────────────┴───────────────────────┐
          │  사전 인프라 (항상 로드)                       │
          │  · validator = KoreanDictValidator()         │
          └──────┬──────────────────────┬────────────────┘
                 │   (항상 — 기본 베이스) │
                 ▼                        │
        ① 탐지: 원문 미등재어 추출        │
                 │ suspicious_words      │
   ┌─────────────┼────────────────────────────────────────────┐
   │  생성 엔진 (선택)                                          │
   │   A) Gemini (기본·온라인) ── suspicious_words 컨텍스트(KAGEC) │
   │   B) [KoGEC 제거됨 2026-06-17 — 현행 생성은 Gemini 단독]    │
   │   C) (둘 다 off) → 검수 모드: 탐지 결과만 플래그 카드로      │
   └─────────────┬─────────────────────────────────────────────┘
                 │ corrections
                 ▼
        일관성 보정 (Case A 조사 / Case 4 부분매칭) + ③ 가드
                 │
                 ▼
        ② 사전 재검증 → confidence 부여 (과교정 억제)
                 │
                 ▼
        ⑤ 사전 안전망 → ①의 미등재어 중 AI 미처리분을 dict_flag 검수 카드로
                 │
                 ▼
        검토(항목별/등장별) 또는 자동 적용 → HWP 반영 + 정오표
```

---

## 4. 단계별 로드맵

### Phase 1 — 역할 분리 (구현 완료)
- **재검증(②)·가드(③) 항상 ON** — `use_dict` 토글에서 분리.
- **`use_dict`("표준국어대사전 검증") 토글 제거** → 사전은 자동 인프라.
- ~~심층 스크리닝(①)을 `deep_screening` 옵션으로 (기본 OFF)~~ → **2026-06-16 철회**(아래 Phase 1.5 참조).
- 정오표/안내 문구를 새 역할에 맞게 갱신.

### Phase 1.5 — 사전을 기본 베이스로 복권 (2026-06-16, 구현 완료)
- **계기**: '상담채녈'(상담채널 오타) 미탐. `deep_screening` 기본 OFF라 원문 탐지(①)가 안 돌고,
  재검증·가드는 *AI 출력만* 검사하므로, AI가 미묘한 오타를 놓치자 잡을 안전망이 없었다.
- **① 탐지 항상-ON** — `proofreading_worker [2]`에서 `deep_screening` 게이트 제거. 매 실행 `extract_suspicious_words` 수행.
- **⑤ 사전 안전망 추가** — `proofreading_worker [6]`. ①이 잡은 미등재어 중 AI가 교정 대상으로 다루지
  않은 것을 `source="dict_flag"`(original==corrected, HWP 미수정) 검수 카드로 노출. AI 모드·검수 모드 공용 파싱.
- **접두 LIKE 폴백 제거** — `nikl_dict.lookup_word`의 `WHERE word LIKE clean[:3]+'%'`는 4번째 글자 이후
  오타를 마스킹하던 구조적 미탐 원인 → 삭제(예: '애니메니션'이 '애니메이션'에 가려지던 문제 해소).
- **형태소 구제(`morph.is_known_form`) 재작성** — 접두 폴백이 사실은 형태소 분석의 빈틈을 가려주고 있었음
  (제거하니 '아니라'(VCN 지정사)·'따른'(MM 관형사)·'뜻하는/가득한'(XSV/XSA 하-파생)이 거짓 미등재로 노출).
  원칙을 *"미등재 **내용 명사(NNG/NNP)·어근·용언 어간**이 있을 때만 오타로 본다"* 로 바꿈 — 순수 문법형태소
  (지정사·관형사·의존명사·하-파생·어미·조사)는 사전 조회 불필요. 의심 104→75건 감소(회귀 0, 카논 케이스 검증).
- **사전 안전망 노이즈 필터(`nikl_dict.is_likely_typo`)** — ⑤가 검수 카드를 띄울 때 보수적 필터 적용:
  비한글 포함(외래어·코드·고유명사 결합)·NNP·등재명사 2+ 정상복합어는 제외. 실측: 검수 카드 95→25건
  (진짜 오타는 대부분 AI가 직접 교정해 ⑤에서 자동 제외, 남는 25는 외래어·고유명사·미등재 복합어).
- **UI 재정렬** — `setup_panel`의 "심층 사전 스크리닝(느림)" 옵트인 토글 제거, "항상 켜짐" 안내로 대체. 파이프라인 안내 갱신.
- **bare형 중복 카드 제거(`consistency_pass`)** — AI가 조사 없는 형태('상담채녈')를 냈는데 본문엔 '상담채녈을'만
  있으면, Case A가 조사형을 만들고 bare형은 본문 부분문자열로만 매칭돼 앵커 경합에서 밀려 클릭 시 최상단으로
  튀는 **유령 중복 카드**가 됐다. → bare 교정이 단독 토큰으로 본문에 없고 조사형이 대체할 때 bare를 제거
  (review·auto_apply 양쪽 커버). bare가 단독으로도 등장하면 보존.
- **등장 겹침 shadowing(`review_panel`)** — bare('키메세지')가 본문에 단독으로도 있고 조사형('키메세지를')도
  있을 때, bare는 `text.find` 부분문자열로 조사형 토큰 **안에서도** 매칭돼 같은 위치에 카드가 둘 떴다.
  → `_resolve_overlaps`로 겹치는 등장 중 **가장 긴 매치만 카드로** 남기고(나머지 `shadowed`) 카운트·하이라이트
  제외 + 적용 시 skip(긴 교정이 그 자리 담당, 이중 치환 방지). 등장 인덱스는 보존돼 부분거절 skip 정합 유지.
- **조사 변형 신뢰도 통일(`consistency_pass.reconcile_variant_confidence`)** — kiwi가 '홋카이도현'(통째 미등재)과
  '홋카이도현의'(홋카이도+현)를 다르게 분석해 같은 단어인데 bare=low/조사형=high로 갈려, **자동 일괄 적용 시
  조사형만 교정**되던 불일치를 보정. base(조사 제거 corrected)가 같으면 하나라도 high면 모두 high로 통일.
  worker에서 validate 직후 호출.
- **조사 base 추출을 형태소 분석 기반으로 정밀화(`morph.strip_josa` 신규, `consistency_pass._strip_josa` morph-first, 2026-06-17)** —
  계기: '키메세지인'의 **서술격조사 '이다'의 관형형 '인'** 을 조사로 인식 못 해, 같은 단어인데도 조사 변형
  (Case A, 高신뢰)이 아닌 **저신뢰 부분매칭(Case 4)** 으로 빠졌다(사용자 보고). 단순히 정규식에 조사를 추가하면
  '인'·'가' 같은 **단음절 조사가 등재 명사의 끝음절('외국인'·'국가'·'전문가')과 충돌**해 과제거를 일으킨다.
  → **kiwipiepy(이미 탐지·검증에 상시 동작)** 로 어절 끝의 조사(J*)·계사(VCP/VCN+그 어미)만 char 위치로 슬라이스하는
  `morph.strip_josa`를 추가. 형태소를 재결합하지 않고 표면 문자열을 자르므로 내부 공백('키 메시지를'→'키 메시지')도
  보존한다. 판별이 정확하다: '키메세지인'→'키메세지'(계사 제거)·'외국인'→'외국인'(NNG 보존)·'조사가'→'조사'(주격조사)·
  '국가'→'국가'(보존)·'생각하고'→'생각하고'(용언 어미는 계사 꼬리가 아니라 보존). `_strip_josa`는 이를 1순위로 쓰고
  대량 어절 반복을 `lru_cache`로 흡수한다.
- **정규식 폴백 보강 + Case A 표제어 가드(`_JOSA_RE` / `_build_known_words`)** — kiwipiepy 미설치/실패 시 폴백하는
  `_JOSA_RE`에도 계사 '이다' 활용형(인·인데·이야…), 누락 격조사(으로서/에게서…), 보조사(만큼/밖에/커녕…)를 보강하고,
  오류 항목 `보고서`(조사 아님 — '결과보고서'를 ''로 만들던 버그)를 제거. 폴백 정규식은 단음절 조사 과제거 위험이
  남으므로(외국인→외국), `_build_known_words`가 어절 표면형 등재 집합(`known_surfaces`)을 함께 반환하고 Case A·Case 4가
  후보 어절이 표제어이면 전파를 건너뛴다(이중 방어; 사전 없으면 무영향). 동사 어미와 모호한 bare 조사(하고·고·다·라·
  치고·따라)는 폴백 정규식에서도 의도적으로 제외. **`nikl_dict._JOSA_RE`는 별도 목적**(어미 포함 사전 stem 폴백)이라
  동기화하지 않음. *검증*: '키메세지인'→'키메시지인'이 高신뢰 조사 변형으로 승격; '전문→전믄'·'외국→왜국'·'국→귝'
  교정에도 '전문가'·'외국인'·'국가'는 무변형; morph 강제 비활성화 시 정규식 폴백+가드 정상 동작 확인.
- *효과*: 사전이 원문을 항상 직접 검사 → AI가 놓친 명백한 오타도 검수 카드로 반드시 노출. **성능 튜닝은 후속.**
- *실모델 검증(2026-06-16, 실제 test.hwp)*: 추출 26,772자 → '상담채녈을' 탐지 → AI가 '상담채널을'로 교정 확인.
  잔여 한계: 어미 오타('먹었슺니다')는 내용 명사가 아니라 사전이 못 잡음(설계상 AI 담당). 외래어/고유명사 일부는 검수 카드로 남음(보수적 필터의 precision 한계).
- ⚠ **DB 완전성 한계(중요)**: `stdict.db`는 `spellcheck-ko/korean-dict-nikl` **스냅샷**(opendict 2025-12-02)이라 라이브
  우리말샘/표준대사전에 있는 단어가 빠질 수 있다. 실측: '대행사'(代行社)·'돌봄'이 라이브엔 있으나 우리 DB엔 **아예 없음**.
  **재빌드 실행으로 반증**: 빌드는 이미 완전(누락 0, 카운트 동일)했고 두 단어는 스냅샷 자체에 미수록 → 같은 소스 재빌드는 무의미.
- **DB 최신화 완료(2026-06-17)**: 우리말샘 공식 '전체 내려받기'(opendict.korean.go.kr, JSON 2026-06-03)로 갱신.
  `update_opendict.py`가 opendict 부분을 최신 export로 교체하고 stdict(434,240)는 보존 → 총 1,638,799 rows,
  **신규 단어 33,288개 추가**(대행사·돌봄·가족돌봄휴가 등). 대행사·돌봄 거짓 검수 근절(DB 직접 등재). register는
  옛 동작대로 '' 통일(방언/북한어 플래깅은 동형이의어 거짓플래그 우려로 휴면 유지). 신선한 export 받을 때마다 재실행 가능.
- **우리말샘 OpenAPI 캐싱 폴백 (구현, 2026-06-16)**: 위 스냅샷 갭의 실질 해결책. `nikl_api.exists_online(word)`가
  로컬 DB가 놓친 의심어를 **라이브 우리말샘**(method=exact)으로 확인하고 결과를 `data/api_cache.db`에 **영구 캐시**(단어당
  평생 1회 → 자동 성장하는 보강 사전). 워커 [2]에서 `is_likely_typo` 통과 후보만 조회(호출 최소화). **graceful**: 키 없음/
  placeholder/오프라인 → 기존 동작. 키는 `NIKL_API_KEY` env 또는 `config.ini [API] NIKL_API_KEY`(opendict.korean.go.kr 무료 발급).
  형태소 휴리스틱만으론 '실재하나 DB에 없는 단어'와 '오타'를 못 가르므로, 이 라이브 대조가 그 갭을 메운다.

### Phase 2 — 형태소 분석 + 사전 전용 "검수 모드"

**2a. 형태소 분석 통합 (구현 완료)**
- `core/morph.py` 신규 — kiwipiepy(JVM 불필요, Windows 휠) 래퍼. 표면형 → 내용 형태소 **기본형** 복원.
  활용형(겪고→겪다), 불규칙(도와서→돕다·추워서→춥다)까지 정확. kiwipiepy 미설치 시 graceful no-op.
- 사전 검증 3곳을 **기본형 인식**으로 정밀화:
  - `nikl_dict.extract_suspicious_words` (탐지) — 활용형을 미등재로 오인하지 않음.
  - `nikl_dict.validate` (3차 재검증) — 활용형 교정을 거짓 저신뢰로 떨구지 않음.
  - `consistency_pass` 가드 — 표제어 용언의 활용형을 부분매칭 전파로부터 보호.
- *효과*: 검수/스크리닝의 거짓 미등재 대량 제거 → 2b의 전제 조건 충족.
- 의존성: `kiwipiepy`(+`kiwipiepy_model` ~88MB). 빌드 시 동봉 필요(requirements.txt 주석 참조).

**2b. 사전 전용 "검수 모드" (구현 완료 — 검토카드 + 정오표, HWP 미수정)**
- AI scope 0개 선택 시 자동 진입. 푸터 버튼이 "사전 검수 시작 (AI 미사용)"으로 바뀜.
- `nikl_dict.extract_flags` → 미등재/비표준 어휘를 `source="dict_flag"`(original==corrected) Correction으로.
- 검토 패널: 플래그 카드("· 검수 필요", 치환 화살표 없음) + 미리보기 하이라이트.
- 적용 단계: 플래그는 HWP를 **열지도 수정하지도 않음**(apply_worker가 분기). 정오표 "사전 검수" 행만 생성.
- 결과/완료: "검수 N건" 보고. auto_apply 시 플래그는 저신뢰여도 수락(HWP 무위험).
- **부수 수정(중요)**: `lookup_word`에 동형이의어 번호 접미사 매칭 추가. 사전이 `등장01·등장02`처럼
  번호와 함께만 저장한 표제어를 bare 조회로 못 찾아 `등장·대한` 등이 거짓 미등재로 잡히던 **체계적
  오탐을 제거**(베이스라인 탐지·재검증 전반 개선).

### Phase 3 — KoGEC 통합 (❌ 2026-06-17 제거 — 상단 배너 참조. 이하 역사적 기록) → [§6](#6-kogec-통합안)
- **엔진 추상화** `core/correction_engine.py` — `GeminiEngine`/`KoGecEngine`/`EnsembleEngine` + `build_engine()`(폴백).
- **KoGEC 엔진** `core/kogec_engine.py` — NLLB GEC 로컬 추론. 문장 분리(kiwipiepy) → 생성 → **diff로 변경 어절만**
  추출(과교정 가드: 문장 유사도 < 0.55 또는 큰 블록은 버림). torch/transformers·모델 미존재 시 graceful → Gemini 폴백.
- **앙상블 교차검증** — Gemini ∩ KoGEC 합치=high, 단일/불일치=low (과교정 억제). `cross_validate()`.
- ~~**선택 UI** — 설정 다이얼로그 "교정 엔진" 콤보(Gemini/KoGEC/앙상블)~~ → **2026-06-17 내재화로 철회**(아래 Phase 3.5).
- **검증(실모델 완료, 2026-06-16)**: torch 2.12+cpu·transformers 5.12 설치, `nllb-200-ko-gec-600M` 실제 로드(CPU ~45s)·추론 확인.
  - **언어코드 확정**: 토크나이저는 `NllbTokenizer`, `ko_Hang`/`co_Hang`은 **unk(id 3)** — 표준 **`kor_Hang`(id 256098)** 이 정답.
    기본값을 kor_Hang으로 수정함(config_loader·kogec_engine).
  - **정상 교정**: `할려고→하려고`, `읽고있다→읽고 있다`, `먹었따→먹었다`. diff·앙상블·폴백 단위테스트 통과.
  - ⚠ **품질 특성(주의)**: (1) NLLB가 **문장 끝 마침표를 떼는 습성** → diff에서 문장부호-only 차이 무시하도록 보정.
    (2) **패러프레이즈/과교정 경향** — 단순 오류 수정을 넘어 어미·문체를 바꾸거나 조사를 떼기도 함(예: `갓다→갔어`, `학교에→학교`).
    사전 재검증은 결과어가 표준어면 못 거른다. → **권장: 단독(kogec)보다 앙상블(ensemble) 모드** 또는 항목별 검토 필수.

**KoGEC 활성화 방법** (사용자 머신):
1. `pip install torch transformers sentencepiece` (torch는 https://pytorch.org 플랫폼별 휠 권장)
2. 설정 → 교정 엔진 → "KoGEC" 또는 "앙상블" 선택 (또는 `config.ini [ENGINE] PROVIDER=kogec`)
3. 첫 실행 시 모델(`sionic-ai/nllb-200-ko-gec-600M`, ~1.2GB) 자동 다운로드(HF 캐시)
4. 교정 품질이 이상하면 `config.ini [ENGINE] KOGEC_SRC_LANG/KOGEC_TGT_LANG`로 언어코드 조정(예: `kor_Hang`)

### Phase 3.5 — 앙상블 내재화 / 디폴트 고정 (❌ 같은 날 철회 — KoGEC 제거로 무효. 역사적 기록)
- **결정(사용자)**: 교정교열은 **정확성·신뢰도가 최우선** → "사용할 수 있는 최선의 검증 도구가
  항상 디폴트여야 한다"에 의심 없음. 따라서 엔진 *선택*을 없애고 앙상블을 고정 디폴트로 둔다.
- **기본값 gemini→ensemble** — `ConfigLoader.get_engine_provider` fallback을 `ensemble`로
  변경(`_ENGINE_DEFAULT`). config.ini가 없거나 `[ENGINE]`이 비어도 앙상블로 동작.
- **설정 UI 제거** — `settings_dialog.py`의 "교정 엔진" 콤보 카드(`_build_engine_card` 등) 삭제.
  설정 다이얼로그는 다시 **HWP 우선 버전 등록 전용**. `config.ini [ENGINE] PROVIDER`는
  디버깅·특수 상황용 강제 override로만 잔존(UI 비노출). KoGEC 의존성/모델 없으면
  `build_engine`이 Gemini 단독으로 graceful 폴백 → 디폴트 앙상블이 안전.
- **합치 탐지 견고화(정확도 향상 고도화)** — `cross_validate`가 **정확 문자열 일치**만 합치로
  인정해, KoGEC의 어절 diff·문장 끝 마침표 제거 습성 때문에 *같은 교정*도 공백/부호만 달라
  '불일치(low)'로 오판되곤 했다(→ 내재화해도 합치=high가 거의 발화 안 함 = 실익 소멸).
  비교를 `_norm()`(공백·문장부호 제거) 기준으로 바꿔, 표기 미세차에 무관하게 합치를 인정.
  secondary는 정규화 original→{정규화 corrected} **집합**으로 색인(한 단어 복수 교정안 흡수).
- *효과*: 앙상블이 항상 기본 동작 → **두 엔진 합치 항목만 자동적용(auto_apply)**되어 과교정
  억제가 극대화되고, 합치 탐지가 실제로 발화해 자동적용 가능 항목(high)이 늘어난다.
  검토 모드에선 단일/불일치 항목도 low 카드로 노출돼 사용자가 최종 판단.
- **KoGEC 교차검증 타깃팅(성능)** — 계기: 실측에서 KoGEC가 19,392자(281문장)를 문장당 CPU
  beam search로 전수 스캔해 **~15~20분** 소요(Gemini는 청크 API라 빠름). 앙상블에서 KoGEC의
  역할은 'Gemini 교정 지점을 동의/반박'이므로, `EnsembleEngine`이 Gemini 결과의 `original`
  토큰을 `focus_terms`로 `KoGecEngine.check_scope`에 넘겨 **그 토큰을 포함한 문장만** 재생성한다
  (281→수십 문장, ~10배 단축). 합치=high 탐지는 유지되고, Gemini 미접촉 문장의 KoGEC 단독
  제안(과교정 노이즈)은 자연히 빠진다(사전 안전망 ⑤가 AI 미스를 보완). `focus_terms=None`이면
  KoGEC 단독 모드로 전수 스캔(기존 동작 유지).
- **KoGEC 경고 2건 수정** — (1) `batch_decode(clean_up_tokenization_spaces=False)`: NLLB는 BPE
  토크나이저라 기본 후처리가 문장부호 앞 공백을 지워 출력을 망침(transformers 경고) → 끔.
  (2) 모델 로드를 `local_files_only=True` 우선(캐시) → 실패 시 온라인 폴백: 매 실행 HF Hub
  미접속으로 'unauthenticated requests' 경고 제거 + 진짜 오프라인 동작(설계 의도 부합).
- *검증(2026-06-17)*: config 없을 때 `get_engine_provider()==ensemble` 확인. `cross_validate`
  공백/마침표 차이('읽고있다'→'읽고 있다' vs '…다.')를 high로 인정, 실제 불일치는 low 유지.
  `EnsembleEngine`이 Gemini originals를 focus_terms로 KoGEC에 전달함을 스텁으로 확인(합치=high·
  단독=low). settings_dialog import·전 파일 py_compile 통과.

### 공통 — 과교정 억제 전략 → [§5](#5-과교정overcorrection-억제-전략)

---

## 5. 과교정(overcorrection) 억제 전략

LLM GEC의 1순위 리스크. 다층 방어:

1. **거리 기반 통째 치환 금지** — 과거 Case B 제거 완료. 일관성 보정은 같은 단어의 변형(조사·부분매칭)만.
2. **사전 재검증(②)** — AI가 비표준어로 바꾸면 `confidence=low` → 자동적용 시 거절, 검토 시 경고.
3. **표제어 가드(③)** — 멀쩡한 등재어를 변형 전파로 훼손 금지.
4. ~~**(Phase 3) 교차검증** — Gemini ∩ KoGEC 합치 시 high~~ → ❌ KoGEC 제거(2026-06-17)로 폐기(상단 배너).
5. **글자 불변 '삽입형' 결정론 규칙** — 띄어쓰기 백스톱(공백 삽입)·괄호 짝 맞추기(괄호 삽입)는
   **기존 글자를 치환하지 않고 공백/부호 한 짝만 더한다**(환각 0). 거리기반 추측치환과 무관해
   안전하지만, '어디에' 넣을지가 휴리스틱이므로 **저신뢰 검수 카드**로만 노출하고 맥락 의존 예외
   (글머리표 '예)·1)', 복합명사 '학기말고사')는 **보수적 화이트리스트**로 거른다. 구현: `core/
   spacing_rules.py`·`core/morph.find_spacing_suggestions`/`find_dependent_noun_spacing`·`core/
   bracket_rules.py`(source="punct"). 상세는 메모리 `bracket-and-dependent-noun-spacing`·
   `validation-delta-and-spacing-backstop`.
6. **(향후) post-correction** — "과교정된 부분을 원복"하는 후처리(연구: *Leveraging What's Overfixed*). 원문 보존 우선.

---

## 6. KoGEC 통합안 (❌ 2026-06-17 제거 — 역사적 기록. 상단 배너 참조)

### 6.1 KoGEC란
- `sionic-ai/nllb-200-ko-gec-3.3B` (Meta **NLLB-200** 번역모델을 한국어 GEC로 파인튜닝). 600M 변형도 존재.
- **문장 단위 seq2seq**(번역식): 오류 문장 → 교정 문장. 학습 시 특수토큰 `<ko_Hang>`(원문)→`<co_Hang>`(교정).
- BLEU 85.73 (GPT-4o 75.03, HCX-3 71.24 대비 우위). HuggingFace 공개.

### 6.2 우리 앱에서의 활용 시나리오

| 시나리오 | 설명 | 장점 | 비용/리스크 |
|---|---|---|---|
| A. **오프라인 생성 엔진** | Gemini 대체. API 없이 로컬 추론 | 무료·오프라인·개인정보 보호·과교정 적음 | 모델 용량(3.3B≈6~13GB, 600M≈2.4GB), CPU 추론 느림(GPU 권장) |
| B. **교차검증(앙상블)** | Gemini 제안을 KoGEC로 재생성→비교 | 과교정 억제(불일치=low) | 두 엔진 비용 합산 |
| C. **문장 정밀 윤문** | 윤문 모드에서 문장 단위 강점 활용 | 비문/어미 교정 품질 | 문서 전체엔 느림 |

→ **권장 도입 순서: A(가장 가치 큼: 오프라인 모드 완성) → B(품질·신뢰도) → C.**
   현실적으로 데스크톱 배포엔 **600M 변형 + CPU/ONNX 양자화**가 적합. 3.3B는 서버/GPU 환경에서.

### 6.3 통합 설계 — 엔진 추상화

`core/`에 생성 엔진 인터페이스를 두고 Gemini/KoGEC를 교체 가능하게 한다. (현 `GeminiChecker`와 시그니처 정합)

```python
# core/correction_engine.py (신규)
class CorrectionEngine(Protocol):
    def check_scope(self, text, suspicious_words, *,
                    scope_typo, scope_spacing, scope_polish,
                    logger=None, stop_event=None) -> list[Correction]: ...

# core/kogec_engine.py (신규, Phase 3)
class KoGecEngine:
    """sionic-ai/nllb-200-ko-gec-* 로컬 추론.
    transformers + torch. 문장 분할 → batch 번역식 교정 → diff로 Correction 추출."""
    MODEL_ID = "sionic-ai/nllb-200-ko-gec-600M"  # 데스크톱 기본
    def __init__(self, model_dir=None, device="cpu"): ...
    def check_scope(self, text, suspicious_words, **kw):
        sents = split_sentences(text)
        fixed = self._batch_generate(sents)        # <ko_Hang>..→<co_Hang>..
        return diff_to_corrections(sents, fixed)   # 문장 diff → 단어 단위 Correction
```

- **diff_to_corrections**: 원문장 vs 교정문장을 토큰 diff → `Correction(original, corrected, source="kogec")`.
  과교정 억제를 위해 **변경 토큰만** 추출하고, 변경폭이 과도한 문장은 버린다(원문 보존 우선).
- **모델 배포**: 빌드에 동봉하면 용량 급증 → *최초 실행 시 다운로드* 또는 *별도 설치 패키지* 권장. `assets/models/` 캐시.
- **의존성**: `transformers`, `torch`(또는 `optimum`+ONNX Runtime로 경량화). 64bit 메인 프로세스에서 실행(브리지 불필요).
- **스레딩**: 기존 `ProofreadingWorker`(QThread)에서 호출. `stop_event`/`logger` 규약 준수.

### 6.4 설정
- `[ENGINE] PROVIDER = gemini | kogec | ensemble` (config.ini), UI에서 선택.
- KoGEC 미설치 시 자동으로 Gemini 폴백 + 안내.

### 6.5 학습·평가 데이터: 국립국어원 모두의 말뭉치 (조사 결과)

**중요 — "오픈 API"는 교정 API가 아니라 *데이터셋 다운로드 API*다.** (`GET kli.korean.go.kr/restapi/v1/corpus/download?keyVal=…` → 다운로드 URL 반환). 따라서 사전(stdict.db)처럼 런타임에 직접 꽂는 자원이 아니라, **모델을 만들고 검증하는 오프라인 데이터**다.

활용 가능성(런타임❌ / 빌드타임⭕):
1. **GEC 학습·파인튜닝 데이터** — *맞춤법 교정 말뭉치 2021/2022*(온라인·대화 자료의 오탈자 교정 = 오류↔교정 병렬)로 KoGEC를 우리 도메인(공문서·정책보고서)에 파인튜닝. → Phase 3와 직접 시너지.
2. **평가 골드셋** — 현재 "겪고→묻고" 류 회귀를 *수동*으로 발견 중. 이 말뭉치를 회귀 테스트 벤치마크로 쓰면 정밀도/재현율 자동 측정 가능.
3. **활용형↔표제어 보강** — POS 태깅 말뭉치로 활용형 사전을 만들면 "겪고(활용형)는 겪다(표제어)" 매핑이 생겨 가드 정밀도↑. *단, 더 가벼운 대안은 형태소 분석기(mecab-ko/khaiii/KoNLPy) 도입* — 라이선스 부담 없이 활용형 문제 해결.

제약(게이팅):
- **다운로드형·오프라인** — 실시간 호출 불가. 신청→승인→약정서 서명→인증키 절차.
- **라이선스 = 연구 중심, 재배포 제한** (Korpora도 라이선스 문제로 자동 다운로드 미제공). **상업 배포 제품에 학습 데이터로 쓰려면 약정서상 허용 범위를 국립국어원에 반드시 사전 확인.** 모델 가중치 배포 가부도 별도 확인.
- 데이터 자체는 27억 어절·82종이라 전량 학습엔 인프라 필요. 도메인 서브셋만 선별 권장.

→ **결론: 모두의 말뭉치는 Phase 3(KoGEC)의 "학습·평가 연료"로 둔다. 런타임 아키텍처에는 포함하지 않는다. 채택 전 라이선스 확인이 선결 조건.**

---

## 6.6 하위 설계도 — 어문 규범 지식 레이어 (2026-06-22 신설)

국립국어원 4대 어문 규범·공식 해설서·상담사례를 **이 역할 분리 아키텍처에 이식**하는 하위 설계도:
[eomun-rule-layer-architecture.md](eomun-rule-layer-architecture.md). 요지: 어문 규범 지식은
**A. KAGEC 규칙 컨텍스트(주력·생성 보강)** + **B. 결정론 규범 페어(보조·강가드, norm_map에 양보)** +
**C. 회귀 골드셋(과교정 0 측정)** 으로만 안전 이식한다. **두음·구개음화·사이시옷의 규칙 자동치환은
금지**(맥락 의존 → 과교정 = 거리기반 치환 재림). 소스는 국립국어원 원본(bareun.ai 2차 저작물 ❌).
DO-1(시드·빌더·로더, `data/eomun.db`·`core/eomun_rules.py`) 구현·검증 완료, 파이프라인 무변경.

---

## 6.7 하위 설계도 — 사용자 용어 뇌(공유 학습 사전) (2026-06-22 신설)

조직 구성원 전원(10인+)의 수락·거절·수정후수락을 하나의 **공유 "뇌"** 로 수렴시키는 하위 설계도:
[userdict-layer-architecture.md](userdict-layer-architecture.md). 요지: 뇌는 **결정론 사용자 사전
(`data/userdict.db`)** 으로만 이식한다 — **P. 사용자 페어**(합의·큐레이터 승인분을 norm_map과 같은
경로 `[5.6]`로) + **E. 조직 예외**(무교정 화이트리스트, 재검증②/안전망⑤/띄어쓰기⑦에서 억제) +
**C. 골드셋 게이트**(승급 전 과교정 0 검사). 중앙=**Supabase(Postgres)+RLS**, 거버넌스=**큐레이터
승인제**, 프라이버시=**용어 단위만(원고 문맥 미저장)**, 큐레이터 surface=**인앱 PySide6 패널**.
⚠ **RAG·벡터DB·LLM 위키 컴파일·Obsidian-as-brain·Gemini 프롬프트 컨텍스트 주입은 전면 폐기**
(KAGEC 제거 교훈 — Obsidian은 단일사용자 로컬 편집기라 다중쓰기 부적합). 전 구간 graceful.

---

## 7. 참고 (References)

- 부산대·나라인포테크 한국어 맞춤법/문법 검사기(국내 사실상 표준): https://nara-speller.co.kr/speller/ · https://namu.wiki/w/부산대학교%20한국어%20맞춤법%20검사기
- 교정/교열 정의·실무: https://ko.wikipedia.org/wiki/교정_(출판) · https://brunch.co.kr/@soomgo/283
- KAGEC(지식 증강 한국어 GEC): https://aclanthology.org/2024.findings-emnlp.6/
- ChatGPT 한국어 GEC: https://doi.org/10.3390/app14083195
- KoGEC(NLLB 파인튜닝): https://arxiv.org/pdf/2506.11432 · https://blog-en.sionic.ai/ko-gec
- LLM 과교정/후처리: https://arxiv.org/pdf/2509.20811
- 사전 기반 검사기의 real-word error 한계: https://plagly.com/blog/spell-checker-writing-limitations
- 국립국어원 모두의 말뭉치(데이터 다운로드 API): https://kli.korean.go.kr/corpus/main/requestMain.do · https://kli.korean.go.kr/openapi/openapiguide.do
- 모두의 말뭉치 라이선스 주의(연구 중심): https://ko-nlp.github.io/Korpora/ko-docs/corpuslist/modu_web.html

---

*이 문서는 살아있는 설계도다. Phase가 진행될 때마다 갱신한다. (작성: 2026-06-16)*
