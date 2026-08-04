# -*- coding: utf-8 -*-
"""
eval/ai_goldset/run_goldset.py — AI 생성 경로 골드셋(회귀 안전벨트)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI 입력(청커·프롬프트·의심단어 주입·모델 파라미터)을 바꿀 때 **기존 교정이 흔들리는지**
(특히 과교정이 가드를 새는지) 정량 검증한다. 두 단계:

  Phase A — 가드 단위(결정론·API 불필요·즉시): core.ai_guards가 알려진 입력을 정확히
            제외/보존하는지. 가드 로직 자체의 회귀 자물쇠. **항상 실행.**
  Phase B — AI 경로(실 Gemini·청크 호출): 실문서 스니펫에서 engine.check_scope + 가드를
            거친 **최종 출력에 과교정이 새지 않는지**(forbid). 진짜 오타는 잡는지(expect, recall).
            `--full` 일 때만 실행(API 키 필요).

판정 철학(이 앱): **precision(과교정 0) 최우선.** forbid 위반은 곧 회귀(FAIL). expect(recall)은
비결정 AI라 런마다 출렁이므로 정보성 지표(경고만). baseline.json과 비교해 forbid 위반 증가 시 빨간불.

실행:
  결정론만(빠름):   .\.venv64\Scripts\python.exe eval\ai_goldset\run_goldset.py
  AI 포함(느림):    .\.venv64\Scripts\python.exe eval\ai_goldset\run_goldset.py --full
  baseline 저장:    ... --full --save-baseline
"""
import sys, os, io, json, time, threading, argparse

# ⚠ 새 래퍼를 만들지 말 것. Phase E가 eval/ambiguity_scan을 import하면 이 모듈이
#   `ai_goldset.run_goldset` 이름으로 **한 번 더** 로드되는데, 그때 새 TextIOWrapper가
#   sys.stdout을 교체하면 앞 래퍼가 GC되며 밑단 buffer를 닫아 이후 print가 전부
#   "I/O operation on closed file"로 죽는다. reconfigure는 객체를 바꾸지 않아 재진입 안전.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))

from core import ai_guards
from core.models import Correction, HL_TYPO


# ── Phase A: 가드 단위 (결정론) ─────────────────────────────
# (original, corrected, source, 기대: 제외되어야 하는가) — 이번 세션에서 확정된 가드 동작.
GUARD_CASES = [
    ("Microseparometer,P", "Microseparometer,", "ai_typo", True,   "① 비한글 잘라내기"),
    ("Lubricity,P",        "Lubricity,",        "ai_typo", True,   "① 비한글 잘라내기"),
    ("Desitiy",            "Density",           "ai_typo", False,  "① 영문 오타치환 보존"),
    ("재머",                "재머(jammer)",       "ai_typo", True,   "③ 영문 병기 추가"),
    ("재마",                "재머(jammer)",       "ai_typo", False,  "③ 글자교정+병기 보존"),
    ("GNSS",               "GNSS(위성항법)",      "ai_typo", False,  "③ 원문에 영문 보존"),
    ("안정성(열산화안정성",   "안정성(열산화안정성)",  "ai_typo", True,   "② 괄호 구조 변경"),
    ("Figure 1 Figure 1. Proposed system configuration 과 같은",
                            "Figure 1과 같은",     "ai_typo", True,   "④ 캡션 대량삭제"),
    ("구조는 구조는 같다",    "구조는 같다",         "ai_typo", False,  "④ 소량 중복어 보존"),
    ("데이타베이스",         "데이터베이스",        "ai_typo", False,  "국소 오타 보존"),
    ("algorism",           "algorithm",         "ai_typo", False,  "영문 오타 보존"),
    ("이 문장 윤문 합니다",   "이 문장을 윤문 합니다", "ai_polish", False, "윤문 보존(대상 아님)"),
    # ⑤ 숫자 값 변경 — 수치 임의 변경 차단(내용 편집), 공백/부호 변경은 보존
    ("관리 범주에서 11개",   "관리 범주에서 9개",    "ai_typo", True,   "⑤ 숫자 값 변경"),
    ("2,000원",            "2000원",             "ai_typo", False,  "⑤ 쉼표 제거는 숫자값 동일→보존"),
    ("제3장",              "제 3장",              "ai_typo", False,  "⑤ 숫자 띄어쓰기는 보존"),
    # ⑥ 영문→한글 음역 — 의도된 영문 표기 존중. 영문 오타치환(Density)은 보존.
    ("One-Stop",           "원스톱",              "ai_typo", True,   "⑥ 영문→한글 음역"),
    ("Desitiy",            "Density",            "ai_typo", False,  "⑥ 영문 오타치환 보존(라틴 잔존)"),
    # ⑦ 하이픈↔공백 치환 — 하이픈 합성어 표기 존중
    ("최소-후보자",         "최소 후보자",          "ai_typo", True,   "⑦ 하이픈↔공백 치환"),
    ("비용-효과",           "비용 효과",            "ai_typo", True,   "⑦ 하이픈↔공백 치환"),
    ("3-4명",              "3-4명",              "ai_typo", False,  "⑦ 변경 없음 보존"),
    # ⑧ 붙임표(-/–)→가운뎃점(·) — 저자의 나열·과정·경로 의도 존중(가운뎃점 강제 차단)
    ("식별–유치–채용–육성–유지", "식별·유치·채용·육성·유지", "ai_typo", True, "⑧ 붙임표→가운뎃점"),
    ("트레이닝–채용–온보딩",  "트레이닝·채용·온보딩",  "ai_typo", True,   "⑧ 붙임표→가운뎃점"),
    ("인턴-정규",           "인턴·정규",            "ai_typo", True,   "⑧ 하이픈-마이너스도"),
    ("인턴–정규",           "인턴·정규직",          "ai_typo", False,  "⑧ 실단어 변경(정규직)은 보존"),
    ("사과·배",             "사과-배",             "ai_typo", False,  "⑧ 역방향(middot→dash)은 대상 아님"),
    # ⑨ (저자, 연도) 인용 표기 재배치 — 인용 양식은 저자·학회 스타일 존중(괄호·부호·공백만 이동 차단)
    ("(Startup Genome, 2024;2025)", "Startup Genome(2024; 2025)", "ai_typo", True, "⑨ 인용 재배치"),
    ("(Kim, 2020)",        "Kim(2020)",          "ai_typo", True,   "⑨ 괄호 밖 저자 재배치"),
    ("(Startup Genome, 2024)", "(Startup Genome, 2025)", "ai_typo", True,  "⑨/⑤ 연도 값 변경은 ⑤가 차단(내용 편집)"),
    ("(Startub Genome, 2024)", "(Startup Genome, 2024)", "ai_typo", False, "⑨ 저자명 오타 교정은 보존"),
    ("OECD, 2024/2025",     "OECD(2024; 2025)",     "ai_typo", True,   "⑨ 연도구분 /→; 재배치(알맹이 동일)"),
    ("(WEF, 2020-2021)",    "WEF(2020; 2021)",      "ai_typo", True,   "⑨ 붙임표 연도범위 재배치도"),
    ("OECD, 2024/2026",     "OECD(2024; 2025)",     "ai_typo", False,  "⑨ 연도 숫자 변경(2026→2025)은 보존"),
    # ⑩ 쌍점(:)→가운뎃점(·) — 비율·대비·점수·시각 등 쌍점 의도 존중(가운뎃점 강제 차단)
    ("국내:해외",           "국내·해외",            "ai_typo", True,   "⑩ 쌍점→가운뎃점(비율/대비)"),
    ("6:4",                "6·4",                "ai_typo", True,   "⑩ 비율 쌍점→가운뎃점"),
    ("국내：해외",          "국내·해외",            "ai_polish", True, "⑩ 전각 쌍점도"),
    ("품질:비용",           "품질·비용·납기",        "ai_typo", False,  "⑩ 실단어 추가(납기)는 보존"),
    ("사과·배",             "사과:배",             "ai_typo", False,  "⑩ 역방향(middot→colon)은 대상 아님"),
    # ⑪ 복합명사 분리(순수 재띄어쓰기)→결정론 다수결 위임(드롭). 조사join·외래어병기는 보존.
    ("인재전략",            "인재 전략",            "ai_typo", True,   "⑪ 복합명사 분리(보고)"),
    ("성장단계",            "성장 단계",            "ai_polish", True, "⑪ 복합명사 분리"),
    ("인재전략을",          "인재 전략을",          "ai_typo", True,   "⑪ 조사 붙은 복합명사 분리도"),
    ("분야 보다도",         "분야보다도",           "ai_typo", False,  "⑪ 조사 붙이기(join)는 AI 담당 — 보존"),
    ("A모델",              "A 모델",              "ai_typo", False,  "⑪ 외래어 병기 띄어쓰기는 대상 아님"),
    ("AI 인재확보",         "AI 인재 확보",         "ai_typo", True,   "⑪ 라틴 '별도어절'+한글어절 분리(글로서리 통일 보고)"),
    ("2025 정책방향",       "2025 정책 방향",       "ai_polish", True, "⑪ 숫자 '별도어절'+한글어절 분리는 드롭"),
    ("2차전지",             "2차 전지",             "ai_typo", False,  "⑪ 숫자'융합' 어절 분리는 보존 — 결정론이 못 받음(회귀수정)"),
    ("3세대",               "3 세대",               "ai_typo", False,  "⑪ 숫자융합 어절 분리 보존"),
    ("케릭터",              "캐릭터",              "ai_typo", False,  "⑪ 글자 교정(재띄어쓰기 아님) 보존"),
    # ⑪-b 복합명사 **붙이기**도 같은 이유로 결정론 위임(드롭). 조사·어미·의존명사 붙이기는 보존.
    #   ⚠ 판정은 '붙인 형태 전체'를 형태소 분석해야 한다 — 조각만 보면 '보다도'가 MAG로 읽힌다.
    ("개인 정보",           "개인정보",             "ai_typo", True,   "⑪-b 복합명사 붙이기(보고 2026-07-31)"),
    ("정책 보고서",         "정책보고서",           "ai_polish", True, "⑪-b 복합명사 붙이기"),
    ("지역 사랑 상품권",     "지역사랑상품권",       "ai_typo", True,   "⑪-b 3어절 붙이기(단일 NNG로 분석돼도 위임)"),
    ("학교 에서",           "학교에서",             "ai_typo", False,  "⑪-b 조사 붙이기는 정당한 맞춤법 — 보존"),
    ("할 수 있다",          "할수 있다",            "ai_typo", False,  "⑪-b 의존명사 붙이기(오히려 오류) — 보존"),
    ("보고 하였다",         "보고하였다",           "ai_typo", False,  "⑪-b 용언(하다) 붙이기 — 결정론이 못 받음, 보존"),
    ("한 부모",             "한부모",               "ai_typo", False,  "⑪-b 3글자는 결정론 min_len 미만 — 보존"),
    ("A 모델",              "A모델",                "ai_typo", False,  "⑪-b 라틴 병기 붙이기는 대상 아님"),
    # ⑫ 쉼표 가감(글자 불변)→저자 문장부호 존중(드롭). 자릿수 쉼표(⑤)·단어변경 동반은 보존.
    ("적용되지 않아, 초기",  "적용되지 않아 초기",   "ai_typo", True,   "⑫ 절 쉼표 삭제(보고)"),
    ("사과, 배, 감을",       "사과 배 감을",         "ai_polish", True, "⑫ 열거 쉼표 삭제"),
    ("정책 개선",           "정책, 개선",           "ai_typo", True,   "⑫ 쉼표 추가도 저자 문장부호"),
    ("매출 1,500명",        "매출 1500명",          "ai_typo", False,  "⑫ 자릿수 쉼표는 ⑤ 영역 — ⑫ 미발동"),
    ("않아,",              "않아서",              "ai_typo", False,  "⑫ 단어 변경 동반 시 보존(알맹이 다름)"),
    # ⑬ 아라비아 숫자→한자어 수사 표기 변환(값 동일·표기만)→저자 표기 존중(드롭). ⑤값·⑪띄어쓰기·고유어는 보존.
    ("2차전지",             "이차전지",             "ai_typo", True,   "⑬ 아라비아→한자어 수사(보고)"),
    ("3세대",               "삼세대",               "ai_polish", True, "⑬ 숫자→한글 수사"),
    ("21세기",              "이십일세기",           "ai_typo", True,   "⑬ 두 자리 수사 변환"),
    ("2세대차량",           "차세대차량",           "ai_typo", False,  "⑬ 오탈자 치환(2→차)은 읽기 불일치 — 보존"),
    ("2개",                 "두 개",                "ai_typo", False,  "⑬ 고유어 수사(두)는 대상 아님 — 보존"),
    # ⑭ 라틴 대소문자만 변경(환각 '출판 관례')→저자 영문 표기 존중(드롭). 실제 오타·음역은 보존.
    ("유지(Retention)",     "유지(retention)",      "ai_typo", True,   "⑭ 괄호 병기 소문자화 환각(보고)"),
    ("ai",                  "AI",                   "ai_polish", True, "⑭ 대문자화 방향도 차단(양방향)"),
    ("McKinsey",            "Mckinsey",             "ai_typo", True,   "⑭ 고유명사 케이스 왜곡 차단"),
    ("Desity",              "Density",              "ai_typo", False,  "⑭ 실제 영문 오타(글자 변경) 보존"),
    ("유지(Retention)",     "유지(리텐션)",          "ai_typo", False,  "⑭ 음역은 ⑥ 영역 — ⑭ 미발동"),
    # ⑮ 문장 재구성(어간 보존·조사/어미 2곳+ 일괄 변형, ai_typo만)→저자 문장 존중(드롭).
    #    단일 조사 교정(빠짐/중복/받침)·오타 수정·윤문 스코프는 보존.
    ("사전 구직 활동이 허용된다.", "사전 구직 활동을 허용한다.", "ai_typo", True,  "⑮ 피동→능동 문장 재구성(보고)"),
    ("학교을 간다",          "학교를 간다",           "ai_typo", False,  "⑮ 단일 조사 받침 교정 보존(carve-out)"),
    ("활동 허용된다",        "활동이 허용된다",        "ai_typo", False,  "⑮ 빠진 조사 추가(1곳) 보존(carve-out)"),
    ("있읍니다 있읍니다",     "있습니다 있습니다",      "ai_typo", False,  "⑮ 같은 오타 반복 수정(동일 변형) 보존"),
    ("몇일 후에 갔다",       "며칠 후에 갔다",         "ai_typo", False,  "⑮ 오타(어간 불일치) 보존"),
    # ⑯ 한글 음절 재배열(멀티셋 동일·순서만 변경)→저자 어순·명칭 존중(드롭). 오타·띄어쓰기·윤문은 보존.
    ("과학혁신기술부",        "과학기술혁신부",         "ai_typo", True,   "⑯ 음절 재배열(영국 DSIT 번역명 훼손 보고)"),
    ("과학혁신기술부(DSIT)간", "과학기술혁신부(DSIT)간", "ai_typo", True,   "⑯ 부호·조사 딸린 재배열도 차단"),
    ("빠르게 매우 달렸다",    "매우 빠르게 달렸다",      "ai_typo", True,   "⑯ 어절 순서 재배열도 오탈자 스코프 밖"),
    ("과확혁신기술부",        "과학혁신기술부",         "ai_typo", False,  "⑯ 실제 오타(음절 치환·멀티셋 상이)는 보존"),
    ("빠르게 매우 달렸다",    "매우 빠르게 달렸다",      "ai_polish", False, "⑯ 윤문의 어순 다듬기는 그 스코프 권한 — 보존"),
]


# 부분조각 확장(문서 필요)·외래어 순화(사전 필요) 가드 — filter_overcorrections 밖이라 별도 검증.
def phase_a_doc_dict():
    print("Phase A+ — 문서/사전 의존 가드 (결정론)")
    fails = 0

    # filter_redundant_expansions: 문서에 '독립 단어'로 있는 더 긴 표기의 조각을 부풀린 과교정 제외.
    doc = ("소프트웨어 개발과 소프트웨어 공급. 소규모·저매출 단계. 문제 해결력·소프트 스킬(소통). "
           "제1절 키메시지 대상과 목적, 키메시지 내용. "
           "이사는 7년간 선임·책임연구원으로 근무한 뒤 창업했다. 개발도상국의 시장. "
           "제2절 미생성코드 유형의 구조화. 미생성코드가 발생하는 경로와 미생성의 핵심 이슈.")
    exp_cases = [
        ("소프트", "소프트웨어", "ai_typo", True),        # '소프트웨어'가 독립 단어로 존재 → 제외
        ("소규모·저", "소규모·저매출", "ai_typo", True),    # 독립 등장 → 제외
        ("문제 해결력·소프트", "문제 해결력·소프트 스킬", "ai_typo", True),  # 구 독립 등장 → 제외
        # ★실보고(2026-07-03): '책임연구원'이 문서에 조사형('책임연구원으로')으로만 존재 —
        #   뒤 조사 런은 독립 사용으로 판정해 조각 확장을 차단('책임연구원구원' 오염 방지).
        ("책임연", "책임연구원", "ai_typo", True),
        ("메시", "메시지", "ai_typo", False),              # ★'메시지'는 '키메시지'에 붙어서만 → 보존(실보고)
        # '개발도상'은 문서에 '개발도상국(+의)' 안에만 존재 — 뒤 런 '국의'는 조사 연쇄가
        #   아니므로(국≠조사) 비독립 유지 → 확장 아님으로 보존(합성어 조각 판정 불변).
        ("개발", "개발도상", "ai_typo", False),
        ("데이타", "데이터", "ai_typo", False),            # 확장 아님(치환) → 보존
        ("신기술", "신기술 동향", "ai_typo", False),        # 문서에 '신기술 동향' 없음 → 보존
        # ★실보고(2026-07-15, 과오지급 보고서): AI 조사형 확장 '미생성이'→'미생성 코드가' —
        #   original이 corrected의 부분문자열이 아니고(이→가), 교정문은 띄어 쓴 '미생성 코드'인데
        #   문서 표기는 붙여 쓴 '미생성코드'라 기존 판정을 2중으로 비껴갔다. 일관성 Case A가
        #   bare 카드로 전파해 '미생성코드' 524곳이 '미생성 코드코드' 오염 위기(치명 보고).
        ("미생성이", "미생성 코드가", "ai_typo", True),     # 조사형 확장 + 공백 무시 독립 판정 → 제외
        ("미생성", "미생성 코드", "ai_typo", True),         # bare 확장 + 공백 무시 독립 판정 → 제외
        ("미생성를", "미생성을", "ai_typo", False),          # 조사만 교정(base 동일) → 보존
    ]
    for o, c, src, expect_drop in exp_cases:
        cor = Correction(original=o, corrected=c, reason="", source=src, color=HL_TYPO)
        kept = ai_guards.filter_redundant_expansions([cor], doc)
        dropped = (len(kept) == 0)
        if dropped != expect_drop:
            fails += 1
            print(f"  ✗ FAIL [확장] {o!r}→{c!r} 기대제외={expect_drop} 실제={dropped}")

    # demote_hedged_corrections: AI 사유에 헤지(보이나·권장·추정 등) → low 강등(자동 적용 금지).
    hd_cases = [
        # (reason, source, 입력 confidence, 기대 confidence)
        ("문맥상 '책임연구원'의 줄임말로 보이나, 출판물에서는 명확한 표기를 권장함",
         "ai_typo", "high", "low"),                          # ★실보고 — 모호 판단 → 강등
        ("오탈자로 추정됨", "ai_typo", "high", "low"),          # 추정 → 강등
        ("'컨텐츠'는 '콘텐츠'가 표준 표기", "ai_typo", "high", "high"),   # 단정 사실 → 유지
        ("띄어쓰기 규정(제43항)에 따름", "ai_typo", "high", "high"),      # 규정 근거 → 유지
        ("규범 표기 '콘텐츠' 권장", "dict", "high", "high"),     # AI 아님(dict) → 무간섭
        ("줄임말일 수도 있어 보임", "ai_typo", "low", "low"),    # 이미 low → 유지
    ]
    for reason, src, conf_in, conf_want in hd_cases:
        cor = Correction(original="가나", corrected="가나다", reason=reason, source=src,
                         color=HL_TYPO, confidence=conf_in)
        ai_guards.demote_hedged_corrections([cor])
        if cor.confidence != conf_want:
            fails += 1
            print(f"  ✗ FAIL [헤지] reason={reason!r} 기대={conf_want} 실제={cor.confidence}")

    # demote_convention_claims ⑱: 근거가 규범 조항이 아닌 '관용 표기·관례·관행' 주장인 AI 교정
    #   → low 강등(★실보고 2026-07-14 기관명 개명 2건의 공통 사유 패턴 — 병기 괄호 없는
    #   명칭 치환 변종의 마지막 그물). 규정·표준 근거와 dict 소스는 무간섭.
    cv_cases = [
        # (reason, source, 입력 confidence, 기대 confidence)
        ("호주 정부 부처명(Department of Industry, Science and Resources)의 "
         "한국어 관용 표기 반영", "ai_typo", "high", "low"),          # ★실보고 2
        ("대한민국 정부 조직 명칭 및 일반적인 부처 명칭 관례에 따른 수정",
         "ai_typo", "high", "low"),                                  # ★실보고 1
        ("업계에서 널리 통용되는 표기로 통일", "ai_polish", "high", "low"),  # 통용 주장 → 강등
        ("출판 관행에 따른 표기", "ai_typo", "high", "low"),            # 관행 주장 → 강등
        ("한글 맞춤법 제40항에 따름", "ai_typo", "high", "high"),        # 규정 근거 → 유지
        ("'컨텐츠'는 '콘텐츠'가 표준 표기", "ai_typo", "high", "high"),   # 표준 근거 → 유지
        ("관용 표기 반영", "dict", "high", "high"),                     # AI 아님(dict) → 무간섭
        ("관례에 따른 수정", "ai_typo", "low", "low"),                  # 이미 low → 유지
    ]
    for reason, src, conf_in, conf_want in cv_cases:
        cor = Correction(original="가나", corrected="가나다", reason=reason, source=src,
                         color=HL_TYPO, confidence=conf_in)
        ai_guards.demote_convention_claims([cor])
        if cor.confidence != conf_want:
            fails += 1
            print(f"  ✗ FAIL [관용주장] reason={reason!r} 기대={conf_want} 실제={cor.confidence}")

    # demote_org_name_substitution ⑤: 기관·부처 명칭 치환(AI가 모르는 개편·개명 되돌림)
    #   → low 강등(자동 적용 제외, 방향은 편집자 몫). ★괄호 부가부가 붙은 스팬 변형까지
    #   포함(2026-07-24 갭 수정 — '성평등가족부(고립·은둔 청소년)'이 공백 때문에 강등을 새던 버그).
    on_cases = [
        # (original, corrected, source, 입력 conf, 기대 conf)
        ("성평등가족부", "여성가족부", "ai_typo", "high", "low"),                 # 맨몸 명칭
        ("성평등가족부의", "여성가족부의", "ai_typo", "high", "low"),               # 조사형
        ("성평등가족부장관", "여성가족부장관", "ai_typo", "high", "low"),           # 직위 접미사
        ("성평등가족부(고립·은둔 청소년)", "여성가족부(고립·은둔 청소년)",
         "ai_typo", "high", "low"),                                              # ★괄호 스팬(갭)
        ("성평등가족부 (고립·은둔 청소년)", "여성가족부 (고립·은둔 청소년)",
         "ai_typo", "high", "low"),                                              # ★공백+괄호 스팬(갭)
        ("여상가족부", "여성가족부", "ai_typo", "high", "high"),                   # 오탈자(거리1) → 보존
        ("여성가족부(2020)", "여성가족부(2021)", "ai_typo", "high", "high"),        # 꼬리 다름(연도) → 보존
        ("여성가족부의 자료", "여성가족부의 방침", "ai_typo", "high", "high"),        # 명칭 외 편집 → 보존
        ("컨텐츠", "콘텐츠", "ai_typo", "high", "high"),                          # 근접·짧음 → 보존
        ("성평등가족부", "여성가족부", "dict", "high", "high"),                    # AI 아님(dict) → 무간섭
        ("성평등가족부", "여성가족부", "ai_typo", "low", "low"),                   # 이미 low → 유지
    ]
    for o, cr, src, conf_in, conf_want in on_cases:
        cor = Correction(original=o, corrected=cr, reason="'여성가족부'가 정확한 부처 명칭",
                         source=src, color=HL_TYPO, confidence=conf_in)
        ai_guards.demote_org_name_substitution([cor])
        if cor.confidence != conf_want:
            fails += 1
            print(f"  ✗ FAIL [기관명치환] {o!r}→{cr!r} 기대={conf_want} 실제={cor.confidence}")

    # drop_loanword_paraphrase: 외래어 순화(목표어=등재어, 글자 완전상이)는 제외, 철자교정은 보존.
    #   ⚠ 원문은 비표준 표기('파라메터')일 수 있어 _DICT에 없음 — 목표어만 등재면 순화로 판정.
    _DICT = {"파라미터", "매개변수", "결제", "결재", "지향", "지양", "인터페이스", "접속", "위험"}
    exists = lambda w: w in _DICT
    dm_cases = [
        ("파라메터가", "매개변수가", True), # ★실보고: 원문 비표준(파라메터,미등재)→순화어 = 제외
        ("파라미터", "매개변수", True),     # 등재어→완전 다른 등재어 = 순화 → 제외
        ("파라미터가", "매개변수가", True), # 조사 포함 — base로 떼고 검사
        ("인터페이스", "접속", True),       # 순화 → 제외
        ("리스크", "위험", True),           # 외래어→고유어 순화 → 제외
        ("파라메터", "파라미터", False),    # ★외래어 표기 정규화(2-gram '파라' 공유) → 보존(진짜 교정)
        ("결제", "결재", False),            # 최소대립쌍(편집거리1·글자공유) → 보존
        ("지향", "지양", False),            # 최소대립쌍 → 보존
    ]
    for o, c, expect_drop in dm_cases:
        cor = Correction(original=o, corrected=c, reason="r", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        kept = ai_guards.drop_loanword_paraphrase([cor], exists)
        dropped = (len(kept) == 0)
        if dropped != expect_drop:
            fails += 1
            print(f"  ✗ FAIL [순화] {o!r}→{c!r} 기대제외={expect_drop} 실제={dropped}")

    # drop_glossed_name_substitution ⑰: 원어 병기 괄호로 정체가 고정된 명칭의 대규모 치환
    #   차단(★실보고 2026-07-14: 호주 DISR '과학산업자원부'→대한민국 '산업통상자원부' 개명,
    #   자모거리≥4 + 병기 앵커). 소규모 표기 교정·조사 변경·병기 없는 낱말·다어절은 보존.
    gn_doc = ("호주 과학산업자원부(Department of Industry, Science and Resources, DISR)는 "
              "2019년 최초로 AI 윤리원칙(AI Ethics Principle)을 발표하였고 2024년 개정했다. "
              "영국 과학혁신기술부(DSIT)간 역할 분담과 플랫홈(platform) 전략, "
              "가시성(visibility)가 확보됐다. 기획재정부는 예산을 편성했다.")
    gn_cases = [
        ("과학산업자원부", "산업통상자원부", True),      # ★보고 — 병기 앵커 + 대규모 치환 → 드롭
        ("과학산업자원부는", "산업통상자원부는", True),   # 조사형 카드도 base로 앵커 → 드롭
        ("과학혁신기술부(DSIT)간", "과학기술혁신부(DSIT)간", True),  # 1차 보고(재배열)도 겹으로 차단
        ("플랫홈(platform)", "플랫폼(platform)", False),  # 소규모 표기 교정(자모 1) → 보존
        ("가시성(visibility)가", "가시성(visibility)을", False),  # 조사 변경(㉒ 영역) → 보존
        ("기획재정부는", "재정기획부는", False),          # 병기 앵커 없음 → ⑰ 미발동(⑯이 별도 차단)
        ("윤리원칙", "윤리 원칙", False),                # 한글 시퀀스 동일(띄어쓰기) → 미발동
    ]
    for o, c, expect_drop in gn_cases:
        cor = Correction(original=o, corrected=c, reason="r", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        kept = ai_guards.drop_glossed_name_substitution([cor], gn_doc)
        dropped = (len(kept) == 0)
        if dropped != expect_drop:
            fails += 1
            print(f"  ✗ FAIL [병기명칭] {o!r}→{c!r} 기대제외={expect_drop} 실제={dropped}")

    # ㉗ demote_registered_term_substitution: 저자가 고른 등재 용어를 같은 계열의 다른 등재
    #   용어로 '통일'하는 AI 교정을 low 강등(★실보고 2026-08-03: '지방정부'→'지방자치단체',
    #   사유 "글로서리 규칙에 따른 용어 통일", `표준어`·high·7곳). [K]는 '지'·'방' 공유 음절
    #   때문에 오탈자로 간주해 면제했다. ⚠ **드롭이 아니라 강등** — 같은 모양의 정당한
    #   규범표기 교정이 norm_map 12,750쌍 중 387쌍이라 드롭하면 그 부류가 조용히 죽는다.
    _TDICT = {"지방정부", "지방자치단체", "중앙정부", "성평등가족부", "여성가족부",
              "컨텐츠", "콘텐츠", "역활", "역할", "세수그릇", "세숫그릇", "줄장미", "덩굴장미"}
    t_exists = lambda w: w in _TDICT
    ts_cases = [
        # (원문, 교정, 기대_강등)
        ("지방정부", "지방자치단체", True),      # ★보고 — 공통 접두 '지방' + 자모거리 8
        ("지방정부는", "지방자치단체는", True),   # 조사형 카드도 base로 판정
        ("성평등가족부", "여성가족부", True),     # 공통 접미 '가족부' + 거리 7
        ("중앙정부", "지방정부", False),          # 거리 3 < 4 → 보존(임계값 경계)
        ("컨텐츠", "콘텐츠", False),              # 외래어 표기 교정(거리 1) → 보존
        ("역활", "역할", False),                  # 오탈자(거리 1) → 보존
        ("세수그릇", "세숫그릇", False),          # 사이시옷(거리 1) → 보존
        ("지방정부", "지방정부론", False),        # 부분문자열 = 확장([R] 소관) → 보존
        ("지방정부", "지방행정기관", False),      # 원문만 등재(교정문 미등재) → 보존
        ("지방정부", "지방 정부", False),         # 띄어쓰기(순한글 단일어절 아님) → 보존
    ]
    for o, c, expect in ts_cases:
        cor = Correction(original=o, corrected=c, reason="r", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        ai_guards.demote_registered_term_substitution([cor], t_exists)
        got = (cor.confidence == "low")
        if got != expect:
            fails += 1
            print(f"  ✗ FAIL [㉗용어치환] {o!r}→{c!r} 기대강등={expect} 실제={got}")
    #   강등이지 드롭이 아님을 명시적으로 고정한다.
    _cor = Correction(original="지방정부", corrected="지방자치단체", reason="r",
                      source="ai_typo", color=HL_TYPO, confidence="high")
    _out = ai_guards.demote_registered_term_substitution([_cor], t_exists)
    if len(_out) != 1 or "[검수]" not in _cor.reason:
        fails += 1
        print("  ✗ FAIL [㉗용어치환] 강등이 아니라 드롭됐거나 [검수] 표기가 없음")

    # ★가드 강등이 뒤 단계에서 되올려지지 않는가(실보고 2026-08-03 후속) —
    #   ㉗이 low로 내린 카드를 `reconcile_variant_confidence`가 **high로 되돌려** 놨다.
    #   `replace()`가 reason은 그대로 두고 confidence만 바꾸는 탓에, 화면엔 '[검수] …'
    #   사유가 붙은 채 **자동 적용되는 high 카드**가 떠서 가드가 안 먹은 것처럼 보였다.
    #   ⚠ 반대로 validate()發 강등(reason 무마커)은 계속 통일돼야 한다(이 함수의 본래 목적).
    from core.consistency_pass import reconcile_variant_confidence as _rvc
    rvc_cases = [
        ("가드 강등 유지", "[검수] 용어 치환 — 확인 필요", "low"),
        ("validate 강등은 통일", "일본 지명 표기", "high"),
    ]
    for name, reason, want in rvc_cases:
        pair = [Correction(original="지방정부", corrected="지방자치단체", reason=reason,
                           source="ai_typo", color=HL_TYPO, confidence="low"),
                Correction(original="지방자치단쳬", corrected="지방자치단체", reason="오탈자",
                           source="ai_typo", color=HL_TYPO, confidence="high")]
        got = _rvc(pair)[0].confidence
        if got != want:
            fails += 1
            print(f"  ✗ FAIL [신뢰도통일] {name}: 기대 {want} 실제 {got}")

    # drop_word_substitution_paraphrase: 문맥 윤문의 '단어 교체'(하에→아래) 차단 —
    #   조사 추가·오탈자·최소대립쌍은 보존. 실 사전이 필요해 _WDICT로 등재 여부 모사.
    _WDICT = {"하", "아래", "위험", "리스크", "몇일", "며칠", "역활", "역할",
              "지향", "지양", "정부", "정책",
              # 용언 기본형 — 어미가 음절을 공유하는 유의어 치환 판정용(아래 ws_cases)
              "통하다", "거치다", "되다", "제공하다", "이용하다", "활용하다"}
    w_exists = lambda w: (len(w) < 2) or (w in _WDICT)   # lookup_word의 1글자 자동-True 모사
    ws_cases = [
        # (원문, 교정, 기대_제외)
        ("연방교육연구부(BMBF) 지원 하에", "연방교육연구부(BMBF)의 지원 아래", True),  # 보고: 하에→아래
        ("지원 하에", "지원 아래", True),          # 단어 교체
        ("리스크 관리", "위험 관리", True),        # 유의어 치환
        ("BMBF 지원", "BMBF의 지원", False),       # 조사 추가만 → 보존
        ("정부 정책", "정부의 정책", False),       # 조사 추가 → 보존
        ("몇일 후", "며칠 후", False),             # 오탈자(자모2) 보존
        ("역활 분담", "역할 분담", False),         # 오탈자(공유 '역') 보존
        ("지향 한다", "지양 한다", False),         # 최소대립쌍 보존
        # ★용언 활용형 유의어 치환(2026-07-30 보고) — 어미 '서'를 공유해 표면 비교로는
        #   오탈자로 통과했다. 어간(통하/거치) 비교로 잡는다.
        ("재배열 과정을 통해서", "재배열 과정을 거쳐서", True),
        # 무발화: 한쪽이 단일 용언이 아니거나 기본형을 못 얻는 경우는 현행 동작 유지
        ("되어졌다", "되었다", False),             # 이중피동 교정 — 보존
        ("정보를 제공하고", "정보를 제공하며", False),  # 복합용언(제공+하) → 경로 미진입
    ]
    for o, c, expect_drop in ws_cases:
        cor = Correction(original=o, corrected=c, reason="r", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        kept = ai_guards.drop_word_substitution_paraphrase([cor], w_exists)
        dropped = (len(kept) == 0)
        if dropped != expect_drop:
            fails += 1
            print(f"  ✗ FAIL [단어교체] {o!r}→{c!r} 기대제외={expect_drop} 실제={dropped}")

    # fix_paren_josa_agreement: 닫는 괄호 뒤 조사를 바꾼 AI 교정의 받침 호응을 **괄호 앞
    #   체언**으로 보정(★실보고 2026-07-06: '가시성(visibility)가'→AI '…를' — 옳은 형태는 '을').
    #   잘린 앵커('visibility)가…')는 문서 문맥으로 host를 찾는다. 라벨 괄호·비괄호는 미개입.
    pj_doc = ("군이 공급사슬 상류(upstream)에 대한 가시성(visibility)가 확보하고 있거나, "
              "홍보 영상(15초, 30초)은 좋다. 검토(review)가 진행됐다. "
              "식 (4)와 같이 정의한다. 서울(Seoul)로 갔다. 필드을 정리했다.")
    pj_cases = [
        # (원문, AI 교정, 기대 결과: 문자열=보정/유지된 corrected, None=카드 드롭)
        ("visibility)가 확보하고", "visibility)를 확보하고",
         "visibility)을 확보하고"),                       # ★보고 — 잘린 앵커, host='가시성'(받침)
        ("가시성(visibility)가 확보하고", "가시성(visibility)를 확보하고",
         "가시성(visibility)을 확보하고"),                 # 온전 앵커 — o 안에서 host 판정
        ("검토(review)가 진행", "검토(review)를 진행",
         "검토(review)를 진행"),                          # host '토' 무받침 → '를' 옳음(무변경)
        ("영상(15초, 30초)은 좋다", "영상(15초, 30초)는 좋다",
         None),                                          # 받침 방향만 뒤집은 무의미 교체 → 드롭
        ("서울(Seoul)로 갔다", "서울(Seoul)으로 갔다",
         None),                                          # 서울 ㄹ받침 → '로' 옳음 → 드롭
        ("식 (4)와 같이", "식 (4)과 같이",
         "식 (4)과 같이"),                                # 순수 번호/라벨 괄호 → 미개입(⑯ 정책)
        ("필드을 정리", "필드를 정리",
         "필드를 정리"),                                  # 괄호 문맥 아님 → 미개입
    ]
    for o, c, want in pj_cases:
        cor = Correction(original=o, corrected=c, reason="r", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        kept = ai_guards.fix_paren_josa_agreement([cor], pj_doc)
        got = kept[0].corrected if kept else None
        if got != want:
            fails += 1
            print(f"  ✗ FAIL [괄호조사] {o!r}→{c!r} 기대={want!r} 실제={got!r}")

    # ── 조판부호(각주) 절단 조각 앞 덧붙임 드롭 ────────────────────────────────
    #   HWP 추출은 각주 텍스트를 본문 문장 한가운데 실어 오고 경계 개행 때문에 한 문장이
    #   쪼개진다. AI는 '을 적용해…' 조각을 주어 없는 문장으로 보고 앞 조각 끝('TLS 1.3')을
    #   덧붙이는 오탐을 낸다(사용자 보고 2026-07-30). 문서에 그 말이 이미 있으므로 중복이다.
    #   ⚠ '덧붙인 말 == 각주 앞 조각의 끝'까지 확인해야 한다 — 안 하면 짧은 원문이 우연히
    #   어떤 조각의 접두사인 경우까지 드롭된다(실측 '복지지갑'→'한국 복지지갑' 오드롭).
    fr_lines = [
        "검증하고, TLS 1.3",                      # 0 본문(끊긴 앞부분)
        "",                                       # 1
        " 전송 구간 암호화를 위한 최신 프로토콜.",   # 2 ★각주
        "",                                       # 3
        "을 적용해 전진 기밀성을 보장한다.",         # 4 본문(이어짐)
        "",                                       # 5
        "복지지갑은 이용자에게 제공된다.",           # 6 본문(각주 무관)
    ]
    fr_doc = "\n".join(fr_lines)
    fr_notes = [2]
    fr_cases = [
        # (원문, 교정문, source, 드롭 기대)
        ("을 적용해 전진 기밀성을 보장한다.",
         "TLS 1.3을 적용해 전진 기밀성을 보장한다.", "ai_typo", True),
        # 같은 조각의 중간 수정 → 앞 덧붙임 아님 → 보존
        ("을 적용해 전진 기밀성을 보장한다.",
         "을 적용해 전진 기밀성을 보장하였다.", "ai_typo", False),
        # 덧붙였지만 앞 조각 끝과 불일치 → 보존(진짜 보충일 수 있다)
        ("을 적용해 전진 기밀성을 보장한다.",
         "표준을 적용해 전진 기밀성을 보장한다.", "ai_typo", False),
        # 각주와 무관한 조각의 앞 덧붙임 → 보존
        ("복지지갑은 이용자에게 제공된다.",
         "한국 복지지갑은 이용자에게 제공된다.", "ai_typo", False),
        # 결정론 카드는 대상 아님
        ("을 적용해 전진 기밀성을 보장한다.",
         "TLS 1.3을 적용해 전진 기밀성을 보장한다.", "spacing", False),
    ]
    for o, c, src, want_drop in fr_cases:
        cor = Correction(original=o, corrected=c, source=src, color=0,
                         category="맞춤법", confidence="high")
        kept = ai_guards.drop_fragment_prefix_addition(
            [cor], fr_doc, note_lines=fr_notes)
        if (len(kept) == 0) != want_drop:
            fails += 1
            print(f"  ✗ FAIL [각주절단] {o[:14]!r}→{c[:18]!r} "
                  f"기대드롭={want_drop} 실제드롭={len(kept) == 0}")
    # note_lines 없으면 무회귀(구버전 브리지·hwpx 직접 백엔드)
    cor = Correction(original=fr_cases[0][0], corrected=fr_cases[0][1],
                     source="ai_typo", color=0, category="맞춤법")
    if len(ai_guards.drop_fragment_prefix_addition([cor], fr_doc, note_lines=[])) != 1:
        fails += 1
        print("  ✗ FAIL [각주절단] note_lines 없을 때 무회귀 실패")

    n = (len(exp_cases) + len(hd_cases) + len(dm_cases) + len(ws_cases) + len(pj_cases)
         + len(gn_cases) + len(cv_cases) + len(fr_cases) + len(ts_cases)
         + len(rvc_cases) + 1 + 1)
    print(f"  → {n - fails}/{n} 통과" + ("  ✅" if fails == 0 else "  ❌"))
    return fails


def phase_a_norm_guard():
    """규범표기 등재복합어 성분 가드(nikl_dict, 실 DB) — 티어 ⊂ 톱티어."""
    print("Phase A++ — 규범표기 동형이의/복합어 가드 (실 stdict.db)")
    import re as _re
    try:
        import nikl_dict as _nd
        if not _nd.db_status().get("available"):
            print("  (stdict.db 없음 — 스킵)")
            return 0
    except Exception as e:
        print(f"  (nikl_dict 로드 실패 — 스킵: {e})")
        return 0
    eoj = lambda t: set(_re.findall(r"[가-힣]+", t))
    # (norm key, 문서, 기대_억제)
    cases = [
        ("티어",   "글로벌 톱티어 인재 풀. 상위 티어는 별도 관리.", True),   # 톱티어 등재 → 억제
        ("티어",   "톱티어를 확보. 티어 관리.", True),                       # 조사형 상위어
        ("컨텐츠", "컨텐츠 제작. 컨텐츠를 배포. 좋은 컨텐츠 기획.", False),   # 상위 등재복합어 없음 → 순화유지
        ("로동청년", "로동청년 동맹. 로동청년의 역할.", False),             # 두음법칙 순화유지
        ("세수그릇", "세수그릇 준비. 세수그릇을 씻다.", False),             # 사이시옷 순화유지
        ("수퍼마켓", "수퍼마켓 방문. 수퍼마켓에서 구매.", False),           # 외래어 순화유지
    ]
    fails = 0
    for key, txt, exp in cases:
        got = _nd.is_registered_compound_component(key, eoj(txt))
        if got != exp:
            fails += 1
            print(f"  ✗ FAIL [복합어성분] {key!r} 기대억제={exp} 실제={got}")
    # ── 온용어 화이트리스트 거부권 가드(_norm_pair_components, 네트워크 불필요) ──
    #   온용어엔 비표준 표기가 등재돼 있다(실측: '컨텐츠' 조회 → '디지털^컨텐츠' 등재).
    #   규범표기 교정 대상을 성분으로 품은 어절은 온용어가 승인해도 화이트리스트 금지.
    #   설계도: docs/onterm-integration-design.md §5-2
    veto_cases = [
        ("디지털컨텐츠", True),    # ★근본 사례 — '컨텐츠'(norm_map 키)를 성분으로 포함
        ("컨텐츠",      True),    # norm_map 키 자체
        ("파라메터",    True),    # spelling_pairs 비표준 어간
        ("아니였음",    True),    # spelling_pairs 비표준 어간
        ("스마트그리드", False),   # 정상 전문용어 — 거부하면 8% 효과를 깎는다
        ("제로트러스트", False),
        ("하이퍼파라미터", False),  # ⚠ '파라메터'가 아니라 '파라미터' — 오탐하면 안 됨
        ("할루시네이션", False),
    ]
    for word, exp in veto_cases:
        try:
            got = _nd._norm_pair_components(word)
        except Exception as e:
            fails += 1
            print(f"  ✗ FAIL [온용어거부권] {word!r} 예외 {e}")
            continue
        if got != exp:
            fails += 1
            print(f"  ✗ FAIL [온용어거부권] {word!r} 기대거부={exp} 실제={got}")
    n = len(cases) + len(veto_cases)
    print(f"  → {n - fails}/{n} 통과" + ("  ✅" if fails == 0 else "  ❌"))
    return fails


def phase_a_grammar_demote():
    """㉕ 문법·표현 재구성 AI 교정 강등 가드(kiwi) — 발화/무발화 양방향.

    실파일 보고 5건(오탈자·띄어쓰기 경계를 넘은 AI 문맥 교정이 전부 high로 나옴)을
    회귀 케이스로 고정한다. **무발화 쪽이 더 중요하다** — 정상 오탈자·띄어쓰기 교정을
    강등하면 자동 적용이 통째로 무력해진다.
    """
    print("Phase A++++ — 문법·표현 재구성 강등 가드 (kiwi)")
    fails = 0
    demote_cases = [   # 강등돼야 함(사용자 보고 2026-07-30, 실파일 10)
        ("결제하는 기능은 「전자금융거래법」상 전자금융업에 속한다.",
         "결제 기능은 「전자금융거래법」상 전자금융업에 속한다."),
        ("행복이음에서 카드 신청이 되면", "행복이음에서 카드가 신청되면"),
        ("부과하지 않는 방향이 전제로 되어야 할 필요가 있다.",
         "부과하지 않는 방향을 전제로 해야 할 필요가 있다."),
        ("이때 복지 멤버십 제안하는 양의 문제가 아닌",
         "이때 복지멤버십이 제안하는 양의 문제가 아니라"),
        ("보관을 원칙으로 하며", "보관하는 것을 원칙으로 하며"),
    ]
    keep_cases = [     # high 유지돼야 함(오탈자=내용 형태소 철자 / 띄어쓰기)
        ("상담채녈", "상담채널"), ("컨텐츠", "콘텐츠"), ("메세지를", "메시지를"),
        ("어플리케이션", "애플리케이션"), ("데이타베이스에", "데이터베이스에"),
        ("수용성이 재고된다", "수용성이 제고된다"),
        ("사용율", "사용률"),                    # 접미사 '철자'만 — form 비교면 오발화
        ("복지 지갑", "복지지갑"), ("이상거래탐지", "이상거래 탐지"),
        ("행상활동", "행상 활동"),
        # ⚠ **오탈자가 형태소 분석을 깨뜨려 '문법 재구성'으로 오판되던 부류**
        #   (사용자 보고 2026-07-31, 실파일 고독사 원고). kiwi가 분석 불가 조각에
        #   영형태 지정사(이/VCP, t.len==0)를 끼워 넣어 태그 열이 달라졌다 —
        #   즉 **오탈자일수록 강등되는 역설**. 교정 내용과 사유가 정면 모순이었다.
        ("예를 드어", "예를 들어"),              # 드/NNG+이/VCP+어/EF ← 정상은 들/VV+어/EC
        ("해소히고", "해소하고"),                # 히/NNG+이/VCP+고/EC ← 정상은 하/XSV+고/EC
        ("들어가 드어", "들어가 들어"),          # 교정문이 불규칙(듣/VV-I) — 면제가 죽으면 안 됨
        # 재보고 2026-07-31 — 분석이 '깨지지' 않고 **재분절**되는 오탈자(len0 신호로는 못 잡음)
        ("상담채녈을", "상담 채널을"),           # 상담채녈/NNG → 상담/채널 2형태소 + 띄어쓰기
        ("자속될", "지속될"),                   # 자/IC+속되/VA → 지속/NNG+되/XSV (품사가 통째로 바뀜)
    ]
    # '분석 실패' 판정 자체의 불변식 — 위 keep_cases는 결과만 보므로 여기서 신호를 직접 고정.
    #   ⚠ 표면 문자열 비교(초기 구현)로 되돌리면 불규칙·축약이 전부 '실패'로 오검출된다.
    broken_cases = [
        ("드어", True), ("해소히고", True),          # 영형태 지정사 폴백 = 오탈자 신호
        ("들어", False), ("해야", False), ("봐서", False), ("돼요", False),  # 불규칙·축약
        ("아닌", False),                              # 자모 어미 ᆫ/ETM
        ("문제다", False), ("고독사다", False),        # 정상 영형태 지정사(체언 + (이)다)
        ("해소하고", False), ("캠페인", False),
    ]
    import nikl_dict as _nd
    _ex = lambda x: _nd.lookup_word(x)["exists"]
    for w, exp in broken_cases:
        got = ai_guards._analysis_broken(w, _ex)
        if got != exp:
            fails += 1
            print(f"  ✗ FAIL [분석실패 판정] {w!r} 기대={exp} 실제={got}")

    # ★철자 수정 carve-out은 ㉕·㉖ **공용 단일 출처**다(재보고 2026-07-31: ㉕만 고치자
    #   같은 카드를 ㉖가 잡았다). 아래 두 축을 함께 고정한다 —
    #   (a) 오탈자는 **두 가드 모두** 통과, (b) 거리 1이어도 조사·어미 교체는 면제 금지.
    repair_cases = [
        ("상담채녈을", "상담 채널을", True), ("자속될", "지속될", True),
        ("해소히고", "해소하고", True),      ("예를 드어", "예를 들어", True),
        ("괌심을", "관심을", True),          ("켐페인의", "캠페인의", True),
        ("방향이", "방향을", False),         # 조사 교체 — 거리 1이지만 철자 수정 아님
        ("제도가", "제도를", False),
        ("하였다", "하겠다", False),         # 어미 교체
        ("결제하는 기능은", "결제 기능은", False),
        ("카드 신청이 되면", "카드가 신청되면", False),
        ("인증위험 기반 인증의", "위험 기반 인증의", False),
    ]
    for o, c, exp in repair_cases:
        if ai_guards.is_spelling_repair(o, c, _ex) != exp:
            fails += 1
            print(f"  ✗ FAIL [철자수정 판정] {o!r}→{c!r} 기대={exp}")
    # ㉖도 같은 carve-out을 쓰는지 — 오탈자를 '낱말 삭제'로 강등하면 안 된다
    for o, c in [("해소히고", "해소하고"), ("상담채녈을", "상담 채널을"),
                 ("예를 드어", "예를 들어")]:
        cor = Correction(original=o, corrected=c, reason="교정", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        ai_guards.demote_word_deletion([cor], _ex)
        if cor.confidence != "high":
            fails += 1
            print(f"  ✗ FAIL [㉖가 오탈자 강등] {o!r}→{c!r}")
    for o, c in demote_cases:
        cor = Correction(original=o, corrected=c, reason="교정", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        ai_guards.demote_grammar_restructuring([cor])
        if cor.confidence != "low":
            fails += 1
            print(f"  ✗ FAIL [강등 누락] {o[:30]!r} → {c[:30]!r}")
    for o, c in keep_cases:
        cor = Correction(original=o, corrected=c, reason="교정", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        ai_guards.demote_grammar_restructuring([cor])
        if cor.confidence != "high":
            fails += 1
            print(f"  ✗ FAIL [과강등] {o!r} → {c!r}")
    # 타 source(결정론 finder)는 건드리지 않는다
    det = Correction(original="가족관계", corrected="가족 관계", reason="", source="spacing",
                     color=HL_TYPO, confidence="high")
    ai_guards.demote_grammar_restructuring([det])
    if det.confidence != "high":
        fails += 1
        print("  ✗ FAIL [타 source 강등] source=spacing 이 강등됐다")

    # ㉖ 낱말 삭제 강등 — 중복인지 저자 의도인지 판단 불가한 삭제만 강등한다.
    del_cases = [
        # (원문, 교정, 강등기대)
        ("인증위험 기반 인증의 도입은", "위험 기반 인증의 도입은", True),   # 보고 2026-07-30
        ("LH 주택공사의 사업", "LH의 사업", True),                      # 중복어 삭제
        ("그리고 그리고 이어서", "그리고 이어서", False),                 # 인접 완전중복 = 명백한 오타
        ("정보를 정보를 제공한다", "정보를 제공한다", False),              # 인접 완전중복
        ("상담채녈을", "상담채널을", False),      # kiwi 분절 변화(1→2) — 삭제 아님
        ("컨텐츠를", "콘텐츠를", False),          # 철자만
        ("복지 지갑", "복지지갑", False),         # 띄어쓰기만
        ("이상거래탐지", "이상거래 탐지", False),  # 띄어쓰기만
        ("수용성이 재고된다", "수용성이 제고된다", False),   # 철자만
        ("보관을 원칙으로 하며", "보관하는 것을 원칙으로 하며", False),  # 추가(삭제 아님)
    ]
    for o, c, expect in del_cases:
        cor = Correction(original=o, corrected=c, reason="교정", source="ai_typo",
                         color=HL_TYPO, confidence="high")
        ai_guards.demote_word_deletion([cor])
        got = (cor.confidence == "low")
        if got != expect:
            fails += 1
            print(f"  ✗ FAIL [낱말삭제] {o!r}→{c!r} 기대강등={expect} 실제={got}")
    n = (len(demote_cases) + len(keep_cases) + len(broken_cases)
         + len(repair_cases) + 3 + 1 + len(del_cases))
    print(f"  → {n - fails}/{n} 통과" + ("  ✅" if fails == 0 else "  ❌"))
    return fails


def phase_a_spacing_guard():
    """띄어쓰기 백스톱 사전-명사 가드(morph+실 DB) + 숫자 단위 '원' 규칙(spacing_rules)."""
    print("Phase A+++ — 띄어쓰기 백스톱 사전-명사 가드 (kiwi+stdict.db)")
    fails = 0
    total = 0
    # 숫자 큰수단위 뒤 '원' 띄어쓰기(순수 정규식 — kiwi/DB 불필요) — 13.6억원→13.6억 원
    try:
        from core import spacing_rules as _sr
        unit_cases = [
            ("평균 13.6억원이고", [("13.6억원이고", "13.6억 원이고")]),  # 보고: 미탐이던 것
            ("500만원짜리", [("500만원짜리", "500만 원짜리")]),
            ("7000억원 손실", [("7000억원", "7000억 원")]),
            ("12,9억원 규모", []),   # 자릿점 오식 → AI 영역(제외)
            ("5000원 커피", []),     # 순수 숫자+원 → 붙여쓰기 허용(미발동)
            ("만원버스를", []),      # 滿員 → 숫자 앞 아님(미발동)
        ]
        total += len(unit_cases)
        for txt, exp in unit_cases:
            got = _sr.find_unit_spacing(txt)
            if got != exp:
                fails += 1
                print(f"  ✗ FAIL [단위원] {txt!r} 기대={exp} 실제={got}")
    except Exception as e:
        print(f"  (spacing_rules 단위 규칙 스킵: {e})")
    # 사전-명사 가드(kiwi+DB 필요) — 없으면 이 부분만 스킵하고 단위원 결과는 반영
    try:
        from core import morph
        import nikl_dict as _nd
        if morph._get_kiwi() is None or not _nd.db_status().get("available"):
            print(f"  (kiwi/stdict.db 없음 — 사전-명사 가드 스킵) → 단위원 {total - fails}/{total}")
            return fails
        # (문구, 분리제안이_있어야_하나) — False=등재 명사라 분리 금지, True=정상 띄어쓰기 유지
        cases = [
            ("이중과제에 직면", False),   # 이중(二重) 등재 명사 → '이 중 과제' 금지(사용자 보고)
            ("이중잣대가", False),        # 이중 보호
            ("삼중고에", False),          # 삼중 보호
            ("전세계 시장", True),        # 전 세계 분리 유지(전세계 비등재)
            ("갈수있다", True),           # 갈 수 있다 유지(prev=ETM)
            ("할때가 되면", True),        # 할 때 유지
            ("한개를 샀다", True),        # 한 개 유지(한개=부사)
            ("두개를 샀다", True),        # 두 개 유지(頭蓋 동형이나 수관형사 '두' 제외)
            # ── 2026-07-27 사용자 보고: '한부모…' 계열 오분리(실문서 카드 48건 중 31건) ──
            #   원인 3중 — ① _NATIVE_CARDINAL('한') 예외가 사전 가드를 통째로 우회
            #   ② 사전에 '한부모' 단독 표제어가 없음(우리말샘 전체 내려받기 export 결손)
            #   ③ '한부모가족'은 등재됐지만 pos='' 라 명사 필터를 통과 못 함.
            ("한부모가족지원 사업", False),      # '한부모가족'(등재)이 컷을 가로지름
            ("한부모가정 아동양육비", False),
            ("사유(한부모가족지원)에", False),   # 괄호 안이어도 동일
            ("한부모가족지원 대상과 한부모가구 현황", False),   # 문서 접두 전파('한부모')
            ("한부모가족 지원과 청소년한부모모자 양육비", False),
            ("세대원 정보", False),              # 수관형사 예외 축소 — 세(MM)+대원 오분리
            ("세분류는 다음과 같다", False),     # 細分類
            # ── 잔여 오분리 3종(같은 보고, 실문서 실측) ──
            ("대책을 세울 수 있도록", False),    # 세우다 활용 — 세(MM)+울(NNG) 오분석
            ("적용가능한 부분이 없는지 탐색", False),   # 어미 '-는지'
            ("실제 수령이 없는데도 감액", False),       # 어미 '-는데'
            ("국인자격상실의심자 변동알림", False),     # 잘린 조각 '국'+이다 오분석
            # ── 위 가드가 정상 케이스를 먹지 않는지(억제 과잉 방지) ──
            ("떠난지 3년이 되었다", True),       # 과거 관형형 뒤 '지'=의존명사 → 분리 유지
            ("본가구 주거급여", True),           # 본(MM)+가구 — 용언 대안 있으나 점수차 큼
            ("과오지급에 관한지자체 인터뷰", True),
            ("학생인경우에는", True),            # VCP 앞 체언 2글자 → 정상 분리 유지
        ]
        total += len(cases)
        for txt, exp in cases:
            got = len(morph.find_spacing_suggestions(txt)) > 0
            if got != exp:
                fails += 1
                print(f"  ✗ FAIL [띄어쓰기] {txt!r} 분리제안기대={exp} 실제={got}")
    except Exception as e:
        print(f"  (morph/nikl_dict 로드 실패 — 사전-명사 가드 스킵: {e})")
    print(f"  → {total - fails}/{total} 통과" + ("  ✅" if fails == 0 else "  ❌"))
    return fails


# ── Phase D: 결정론 규칙 레이어 통합 게이트 ─────────────────────────────
# 모든 결정론 finder(spacing/josa/bracket/quote/morph/spelling_pairs)를 한 하니스로
# 상시 게이트한다(2026-07-06 — 선제 고도화·바른AI 벤치마크 흡수의 안전벨트):
#   D-1 페어 불변식 — spelling_pairs._STEM_PAIRS의 **모든** 비표준 어간이 실 stdict.db
#       표제어(1.6M)에 부분문자열로 존재하지 않아야 한다. '역활'⊂'지역활동' 같은 치명
#       오염을 기계적으로 차단 — 새 페어는 사전에 추가만 하면 여기서 자동 검증된다.
#   D-2 무발화(clean corpus) — 규정상 올바른 문장 + 과거 회귀(거짓양성) 사례 문장에서
#       **모든 finder의 발화 0**을 보증(과교정 0 원칙의 실행 가능한 게이트).
#   D-3 발화(positive) — finder별 대표 오류를 정확히 잡는지 확인(커버리지 회귀 감지).
#   D-4 규범표기 용언 활용형 가드 — norm_map [5.7]의 용언 활용형 동형이의 가드
#       (nikl_dict.is_verb_inflection_homograph)가 '나올'(나오+ㄹ)류를 보류하고
#       정당한 카드(컨텐츠·치루다·짜집기·조사 딸린 매칭)는 보존하는지(2026-07-14).
# kiwi/사전 미가용 시 해당 finder는 스킵(graceful — 워커 관행과 동일).

def _det_finders():
    """(이름, finder, kiwi 필요 여부) 목록 — 워커가 쓰는 결정론 finder 전수."""
    from core import spacing_rules as sr, josa_rules as jr, bracket_rules as br
    from core import quote_rules as qr, spelling_pairs as sp, morph
    return morph, [
        ("punct_spacing",       sr.find_punct_spacing,               False),
        ("quote_spacing",       sr.find_quote_spacing,               False),
        ("unit_spacing",        sr.find_unit_spacing,                False),
        ("paren_josa",          jr.find_paren_josa,                  False),
        ("batchim_josa",        jr.find_batchim_josa,                True),
        ("orphan_josa",         jr.find_orphan_josa,                 False),
        ("dup_comitative",      jr.find_duplicate_comitative_josa,   True),
        ("unbalanced_brackets", br.find_unbalanced_brackets,         False),
        ("paren_attach",        br.find_paren_attach,                True),
        ("unpaired_quotes",     qr.find_unpaired_quotes,             False),
        ("quote_punct_spacing", qr.find_quote_punct_spacing,         False),
        ("spelling_pairs",      sp.find_spelling_fixes,              False),
        ("spacing_suggestions", morph.find_spacing_suggestions,      True),
        ("dependent_noun",      morph.find_dependent_noun_spacing,   True),
        ("symbol_noun",         morph.find_symbol_noun_spacing,      True),
        ("aux_verb",            morph.find_auxiliary_verb_spacing,   True),
        ("aux_connective",      morph.find_aux_connective_spacing,   True),
        ("eojida_join",         morph.find_eojida_join,              True),
        ("compound_consistency", morph.find_compound_spacing_consistency, True),
    ]


# 무발화 코퍼스 — 전부 규정상 올바른 문장(과거 거짓양성 회귀 사례 포함). 한 문장이라도
#   finder가 발화하면 FAIL. ⚠ 여기 문장을 지우지 말 것 — 각 문장이 특정 가드의 회귀 감시다.
_CLEAN_CORPUS = [
    "필드를 정리했다.",                                  # 받침조사 정상
    "가을 하늘과 마을 회관은 있는 그대로 어려운 문제였다.",   # 가을/마을/-는/-은 어미 가드
    "그게 아니라 그것이 문제다.",                          # 준말 '그게' 표면일치 가드
    "제미나이 모델을 사용했다.",                           # NNP+XSN '이' 가드
    "결과(표 1)가 나왔다.",                               # 괄호조사 — 이미 호응
    "식 (4)와 같이 정의한다.",                            # 순수 번호 라벨 스킵
    "홍보 영상(15초, 30초)은 좋다.",                       # 괄호조사 — 이미 호응
    "서울(특별시)로 간다.",                               # ㄹ받침 '로' 정상
    "물리학과 학생과 함께 결과와 원인을 논의했다.",           # 공동격 — NNG 통낱말
    "크기가 작아지고 사실이 널리 알려졌다.",                 # '-어지다' 피동 붙임 정상
    "정책보고서와 데이터베이스를 검토했다.",                  # 복합명사 붙임 정상
    "이중과제 수행 능력을 측정했다.",                        # 등재명사 '이중' 사전 가드
    "사과 두 개와 배 한 개를 샀다.",                        # 수관형사+단위 정상 분리
    "갈 수 있다. 할 때가 되었다.",                         # 의존명사 정상 분리
    "전 세계가 주목했다.",                                # 정상 분리(붙임 제안 금지)
    "입장료는 5000원이다.",                               # 큰수단위 없음 — 단위 규칙 무발화
    "원주율은 3.14이다. 예산은 2,000원이다.",               # 소수점·자릿수 쉼표 예외
    "국립국어원 '맞춤법 규칙'에 따라 '천인계획'과 같은 사례를 검토했다.",  # 짝 맞는 인용
    # 중첩 인용(★실보고 2026-07-15): 바깥 “…” 안 “재인용” — 여는 모양 “가 스택 톱의
    #   여는 모양 위에 오면 닫힘이 아니라 중첩(pop하면 진짜 닫는 ”들이 고아로 밀려
    #   '있다”라고' 보완 카드 + '“ 부정수급자' 기호 뒤 띄어쓰기 카드 오발화).
    "법 제46조에서는 “급여를 받게 한 자(이하 “부정수급자”라 한다)로부터 징수할 수 있다”라고 하고 있다.",
    "\"AI\"를 활용했다.",                                # 닫는 따옴표 뒤 조사 붙임 정상
    "연구했다(경향신문, 2024).",                           # EF 뒤 괄호 이미 붙음
    "협력해야 한다. 검토해야 할 과제다.",                    # 보조용언 정상 분리
    "이루어졌고 마무리되었다.",                             # 피동 붙임 정상
    "그 일은 이미 됐다. 왠지 웬만하면 될 것 같다.",           # 페어 정상형 무발화
    "비용을 치러야 했고 김치를 담가 두었다.",                 # 치르다/담그다 정상 활용
    "생각하건대 사업은 넉넉지 않았다.",                      # 제40항 정상형
    "13.6억 원 규모다.",                                  # 이미 띄어 씀
    "재산세 고지서를 확인했다.",
    "수요를 반영했다. 세종으로 이전했다.",                    # 고아 조사 무발화
    "리플릿 등 홍보물을 배포했다.",                          # 의존명사 '등' 정상 분리
    "회의를 마치고 보고서를 제출했다.",
    "먹어 치웠다. 물을 통해 공급했다.",                      # 보조용언 화이트리스트 밖
    "Age’의 특징과 don’t 표현을 검토했다.",                 # 아포스트로피 제외
    # 사전-명사 가드 회귀(2026-07-22 — 바른 중의성 데이터셋 스캔 S-1이 발견, 문장은 자체 작성):
    #   '지난주'는 등재 명사인데 kiwi가 지나+ㄴ(ETM)+주(NNB)로 봐 '지난 주'로 쪼갰다.
    #   ETM 경로에 사전 가드가 없던 것이 원인(MM/NR 분기에만 있었음). 지난달/지난해는 대조군.
    "지난주 회의에서 지난달 실적과 지난해 목표를 검토했다.",
    #   '차등'은 등재 명사인데 find_dependent_noun_spacing이 차(NNG)+등(NNB)으로 봐 '차 등'으로
    #   쪼갰다. 이 finder엔 사전 가드가 아예 없었다. '일중'도 같은 부류.
    "차등 지급 방안을 검토했다.",
]

# 발화 케이스 — (finder 이름, 오류 문장, 기대 원문 부분, 기대 교정 부분)
_POS_CASES = [
    ("batchim_josa",        "필드을 정리했다.",               "필드을", "필드를"),
    ("paren_josa",          "홍보 영상(15초, 30초)는 좋다.",   "영상(15초, 30초)는", "영상(15초, 30초)은"),
    ("orphan_josa",         "수요 를 반영했다.",              "수요 를", "수요를"),
    ("orphan_josa",         "세종 으로 이전했다.",             "세종 으로", "세종으로"),
    ("unit_spacing",        "13.6억원 규모다.",               "13.6억원", "13.6억 원"),
    ("punct_spacing",       "결과다.그러므로 진행한다.",        "결과다.그러므로", "결과다. 그러므로"),
    ("aux_verb",            "각 기관이 협력해야한다.",          "협력해야한다", "협력해야 한다"),
    ("aux_connective",      "손실을 보상해주는 제도다.",         "보상해주는", "보상해 주는"),
    ("eojida_join",         "합의가 이루어 졌고 끝났다.",        "이루어 졌고", "이루어졌고"),
    ("spacing_suggestions", "전세계가 주목했다.",              "전세계", "전 세계"),
    ("dependent_noun",      "리플렛등 홍보물을 배포했다.",       "리플렛등", "리플렛 등"),
    ("spelling_pairs",      "사실이 아니였으며 예산이 됬다.",    "아니였으며", "아니었으며"),
    ("spelling_pairs",      "사실이 아니였으며 예산이 됬다.",    "됬다", "됐다"),
    ("spelling_pairs",      "왠만하면 곰곰히 생각컨대 위험을 무릎쓰고 갔다.", "왠만하면", "웬만하면"),
    ("spelling_pairs",      "왠만하면 곰곰히 생각컨대 위험을 무릎쓰고 갔다.", "곰곰히", "곰곰이"),
    ("spelling_pairs",      "왠만하면 곰곰히 생각컨대 위험을 무릎쓰고 갔다.", "생각컨대", "생각건대"),
    ("spelling_pairs",      "왠만하면 곰곰히 생각컨대 위험을 무릎쓰고 갔다.", "무릎쓰고", "무릅쓰고"),
    ("unbalanced_brackets", "여기서 살펴본다(참고 자료 누락\n다음 항목이다.", "(", ")"),
]

# D-4 규범표기 용언 활용형 동형이의 가드 — (문장, 어절 w, norm_map 키, 기대 보류 여부, 설명).
#   워커 [5.7]/[5.8]과 동일 호출( is_verb_inflection_homograph(key, w, 문장) ). 3조건:
#   ①key=통어절 ②문맥 kiwi 용언 활용형(ETN 제외) ③기본형이 norm_map에 없음(표준 용언).
#   ⚠ 케이스 삭제 금지 — SKIP 4건은 과교정 회귀 감시, KEEP 7건은 정당 카드 보존 감시.
_NORM_VERB_CASES = [
    ("화면에 부정확한 답변이 나올 수 있다는 경고 문구를 달았다.", "나올", "나올", True,
     "나오+ㄹ 관형형(보고 사례) — 명사 '나올(羅兀)→너울' 치환 보류"),
    ("이 문제를 짚고 넘어가야 한다.",       "짚고",   "짚고",   True,  "짚+고 연결형 — '집고' 치환 보류"),
    ("살이 찌게 만드는 음식은 피해야 한다.", "찌게",   "찌게",   True,  "찌+게 연결형 — '찌개' 치환 보류"),
    ("이번에는 뭘 할래 물었다.",            "할래",   "할래",   True,  "하+ㄹ래 종결형 — '까지' 치환 보류"),
    ("점심으로 된장 찌게를 끓여 먹었다.",    "찌게를", "찌게",   False, "①조사 딸린 매칭=명사 사용 — 찌개 카드 보존"),
    ("소풍이 취소되어 큰 바램으로 남았다.",  "바램으로", "바램", False, "①조사 딸린 매칭 — 바람 카드 보존"),
    ("공에 발이 채일 뻔했다.",              "채일",   "채일",   False, "③기본형 채이다=norm_map 등재(비표준 용언) — 카드 유지"),
    ("큰 행사를 치루다 보면 실수가 생긴다.",  "치루다", "치루다", False, "③기본형 치루다=norm_map 등재 — 카드 유지"),
    ("문서를 짜집기 형태로 만들었다.",       "짜집기", "짜집기", False, "②명사형 ETN 제외 — 짜깁기 카드 보존"),
    ("컨텐츠 산업이 성장하고 있다.",         "컨텐츠", "컨텐츠", False, "명사(비활용형) — 콘텐츠 카드 보존"),
    ("메세지 전달이 중요하다.",             "메세지", "메세지", False, "명사(비활용형) — 메시지 카드 보존"),
]

# D-5 규범표기 **체언+조사** 동형이의 가드 — (문장, 어절 w, norm_map 키, 기대 보류 여부, 설명).
#   D-4의 명사 쪽 짝. 워커 [5.7]/[5.8]과 동일 호출( is_noun_josa_homograph(key, w, 문장) ). 2조건:
#   ①key=통어절 ②문맥 kiwi가 [체언+조사]로 가르고 그 체언이 정확 등재 표제어(1글자 허용).
#   계기(2026-08-03 사용자 보고): '하는 말이'·'많단 말이에요'의 '말이'(말+주격조사)가 방언
#   표제어 '말이→말니'로 오매칭. norm_map 전수 스캔 결과 같은 형상 382건(base 등재 173건).
#   ⚠ 케이스 삭제 금지 — SKIP은 과교정 회귀 감시, KEEP은 정당 카드 보존 감시.
#   ⚠ KEEP의 '키로'·'탱이'가 핵심 — 낱말로 굳은 표기는 kiwi가 통째로 읽어야 가드가 안 터진다.
#     이게 깨지면 가드가 '체언+조사 모양이면 무조건 보류'로 퇴화한 것이다.
_NORM_NOUN_CASES = [
    ('애들이 주로 하는 말이, "어차피 자립수당 받잖아요"라고 한다.', "말이", "말이", True,
     "말+주격조사(보고 사례) — 방언 '말이→말니' 치환 보류"),
    ("그건 말이 안 된다는 뜻이다.",          "말이",   "말이",   True,  "말+이 — 말니 보류"),
    ("건물 앞이 넓어서 주차가 편하다.",       "앞이",   "앞이",   True,  "앞+이 — '앞니' 보류"),
    ("창문을 여니 바람이 시원하게 들어온다.", "바람이", "바람이", True,  "바람+이 — '바람니' 보류"),
    ("요즘 그 일에 신경이 많이 쓰인다.",      "신경이", "신경이", True,  "신경+이 — '싱경이' 보류"),
    ("그 사람의 실력은 보통이 아니다.",       "보통이", "보통이", True,  "보통+이 — '보퉁이' 보류"),
    ("아이의 손과 발이 모두 차가웠다.",       "손과",   "손과",   True,  "손+과(1글자 base) — '쑨커' 보류"),
    ("이번 사고는 나로 인해 벌어진 일이다.",  "나로",   "나로",   True,  "나+로 — '나루' 보류"),
    ("여기서 10키로 떨어진 곳에 있다.",       "키로",   "키로",   False, "kiwi가 NNB 단일 형태소 — 킬로 카드 보존"),
    ("탱이 한 마리를 잡아 올렸다.",           "탱이",   "탱이",   False, "kiwi가 NNG 단일 형태소 — 매기 카드 보존"),
    ("두 사람이 실강이를 벌이고 있었다.",     "실강이를", "실강이", False, "①조사 딸린 매칭=명사 사용 — 실랑이 카드 보존"),
    ("컨텐츠 산업이 성장하고 있다.",          "컨텐츠", "컨텐츠", False, "체언+조사 아님 — 콘텐츠 카드 보존"),
    ("메세지 전달이 중요하다.",              "메세지", "메세지", False, "체언+조사 아님 — 메시지 카드 보존"),
]

# D-6 규범표기 카드의 **등장 경계** — (한글 런, 카드 원문, 기대 유지 여부).
#   review_panel._mark_stem_boundary_skips 대상 ⑤가 core.morph.nominal_boundaries로 판정한다.
#   같은 보고에서 '말이' 카드가 '말이에요' **속**을 반복 1/2로 잡았다(수락 시 '말니에요').
#   유지 조건: 등장 양끝이 형태소 경계 + 런에 용언·어미·계사 없음(순수 체언 연쇄).
_NORM_OCC_CASES = [
    ("말이에요",   "말이",   False, "말/NNG+이/VCP+에요/EF — 용언 꼬리(보고 사례)"),
    ("말이다",     "말이",   False, "계사 활용"),
    ("말이야기",   "말이",   False, "체언 연쇄지만 등장 끝이 형태소 경계 아님"),
    ("앞이라도",   "앞이",   False, "앞/NNG+이/VCP+라도/EC"),
    ("탱이었다",   "탱이",   False, "탱/NNG+이/VCP+었/EP+다/EF"),
    ("컨텐츠를",   "컨텐츠", True,  "체언+조사 — 조사형 등장 보존"),
    ("컨텐츠산업", "컨텐츠", True,  "복합명사 성분 — 정당한 등장 보존"),
    ("메세지창에서", "메세지", True, "복합명사+조사 — 보존"),
]


def phase_f_realword():
    """실단어 오류 finder(core/realword.py) — 결정론 후보 생성부만 게이트.

    ⚠ AI 검증(engine.verify_realword)은 실호출이 필요해 여기서 재지 않는다. 이 Phase가
      지키는 것은 **후보 생성의 결정론적 성질**이다:
        F-1 발화   — 실단어 오타를 gap 상한 안에 후보로 올리는가
        F-2 무발화 — 정상 문장에서 후보를 만들지 않는가(과탐 방지)
        F-3 불변식 — 설계 가드가 살아 있는가(같은 길이 치환만·희소성·신뢰도 low 고정)
      AI 검증을 붙인 최종 정밀도는 별도 실측(문서 12건)으로 관리한다.
    """
    print("Phase F — 실단어 오류 finder (kiwi 문맥 점수, 결정론 후보 생성)")
    fails = 0
    total = 0
    try:
        from core import realword as _rw
        from core.models import (REALWORD_GAP_MIN, REALWORD_RARE_MAX,
                                 REALWORD_TOP_PER_WORD)
    except Exception as e:
        print(f"  (realword 스킵: {e})")
        return 0
    if not _rw.available():
        print("  (kiwi 비활성 — 스킵)")
        return 0

    # ── F-1 발화 — 오타형과 정답형이 같은 문서에 있을 때 후보로 올라오는가 ──────
    #   실파일에서 확인된 실제 오류를 축약 재현한다(빈출 조건 충족을 위해 정답형 반복).
    fire = [
        # (문서, 기대 (오타, 정답))
        ("보고 있다. 보고 있다. 보고 있다. 보고 있다. 보고 있다. "
         "목적 외 사용으로 부고 있다.", ("부고", "보고")),
        ("활용하는 방안. 활용하는 사례. 활용하는 기준. 활용하는 절차. 활용하는 원칙. "
         "공공정책 영역에서 이를 적극 확용하는 방향을 제시한다.", ("확용하는", "활용하는")),
        ("검증 절차. 검증 기준. 검증 대상. 검증 방법. 검증 체계. "
         "서비스 결과의 겁증 및 현업 통제를 담당한다.", ("겁증", "검증")),
    ]
    total += len(fire)
    for doc, (bad, good) in fire:
        got = _rw.find_candidates(doc)
        if not any(r["original"] == bad and r["corrected"] == good for r in got):
            fails += 1
            print(f"  ✗ FAIL [F-1 발화] {bad!r}→{good!r} 미탐"
                  f" (후보: {[(r['original'], r['corrected']) for r in got][:5]})")

    # ── F-2 무발화 — 정상 문장에서 후보를 만들면 안 된다 ─────────────────────
    quiet = [
        # 전문용어가 희소하게 등장 — 회계 원고 오탐의 원형(안분·이연·차손)
        "배분 기준. 배분 원칙. 배분 방법. 배분 절차. 배분 체계. "
        "해당 비용은 합리적인 기준에 따라 안분하여 기능별로 배분한다.",
        # 같은 길이 치환이 아닌 관계(파생/합성) — 삭제·삽입 편집은 후보가 되면 안 된다
        "업무 절차. 업무 기준. 업무 방법. 업무 체계. 업무 원칙. "
        "부서별 업무량 편차가 크다.",
        # 정상 문장 — 애초에 희소어-빈출어 편집거리1 쌍이 없어야 한다
        "사회보장급여의 지급 기준과 절차를 정비하여 과오지급을 예방한다.",
    ]
    total += len(quiet)
    for doc in quiet:
        got = _rw.find_candidates(doc)
        if got:
            fails += 1
            print(f"  ✗ FAIL [F-2 무발화] {doc[:30]!r}… 에서 후보 발생: "
                  f"{[(r['original'], r['corrected'], r['gap']) for r in got][:5]}")

    # ── F-3 불변식 — 설계 가드가 살아 있는가 ────────────────────────────────
    inv = []
    inv.append(("같은 길이 치환만", _rw._sub1("부고", "보고") and
                not _rw._sub1("업무량", "업무") and not _rw._sub1("업무", "업무량")))
    inv.append(("gap 임계값 유지", REALWORD_GAP_MIN >= 12.0))
    inv.append(("희소성 조건 유지", REALWORD_RARE_MAX == 1))
    inv.append(("희소어당 후보 2개", REALWORD_TOP_PER_WORD == 2))
    # ⚠ 이 카드는 반드시 저신뢰다 — high가 되는 순간 금지된 '거리 기반 추측 치환'이 된다
    src = io.open(os.path.join(_ROOT, "ui", "workers", "proofreading_worker.py"),
                  encoding="utf-8").read()
    # ⚠ 마커는 유일해야 한다 — "[7.5]"만으로 자르면 [3]의 상호참조 주석에 먼저 걸린다.
    seg = src.split("[7.5] 실단어 오류 검수 카드", 1)[-1].split("[8] 적용 정합성", 1)[0]
    inv.append(("검수 카드 low 고정", 'confidence="low"' in seg
                and 'confidence="high"' not in seg))
    inv.append(("검증 없는 경로 부재", "verify_realword" in seg))
    total += len(inv)
    for name, ok in inv:
        if not ok:
            fails += 1
            print(f"  ✗ FAIL [F-3 불변식] {name}")

    print(f"  F-1 발화 {len(fire)}케이스 · F-2 무발화 {len(quiet)}케이스 · "
          f"F-3 불변식 {len(inv)}항목 검사 완료")
    print(f"  → Phase F {'✅ 통과' if fails == 0 else f'❌ {fails}건 실패'}")
    return fails


def phase_g_junction():
    """이음매 정합 패스(core/junction_pass.py) 게이트.

    이 패스는 중첩 낱말이 같은 띄어쓰기 이음매를 서로 반대로 결정하는 문제를 다룬다
    (사용자 보고 2026-07-30). 자동 판정이 원리적으로 불가능한 영역이 있어서
    (정당한 구분 vs 결함을 사전·빈도로 못 가름 — 실측) 계층이 지키는 성질을 게이트한다:

      G-1 우선순위 계층 — 낱말 자체 근거가 이음매보다 강한가(정당한 구분 보존)
      G-2 자동 정합    — 상대 근거가 **전무할 때만** 정합하는가
      G-3 불변식       — original 불변 · 글자 불변 · URL 미개입 · 결합 방향 미생성
      G-4 전파 산술    — propagate/governed 가 시뮬레이션대로 동작하는가
    """
    print("Phase G — 이음매(junction) 정합 계층 (결정론)")
    fails = 0
    try:
        from core import junction_pass as _jp
        from core.models import Correction, HL_TYPO
        from core import morph as _morph
    except Exception as e:
        print(f"  (junction_pass 스킵: {e})")
        return 0
    if not _morph.available():
        print("  (kiwi 비활성 — 스킵)")
        return 0

    def card(o, c, conf="low", src="spacing"):
        return Correction(original=o, corrected=c, reason="띄어쓰기", source=src,
                          color=HL_TYPO, category="띄어쓰기", confidence=conf,
                          consistency_flip=True)

    # ── G-1 정당한 구분 보존 ────────────────────────────────────────────────
    #   '출산 전후'(일반)와 '출산전후휴가'(고용보험법 용어)는 **달라야 맞다**. 양쪽
    #   낱말이 각자 문서 다수 표기를 가지면 이음매 빈도로 눌러선 안 된다(맞춤법 제50항).
    doc1 = ("출산 전후 " * 6 + "출산전후 " * 2
            + "출산전후휴가 " * 4 + "출산전후 휴가 " * 1)
    cards1 = [card("출산전후", "출산 전후"), card("출산전후 휴가", "출산전후휴가")]
    d1 = _jp.resolve(cards1, doc1)
    if d1["preserved"] < 1 or d1["harmonized"]:
        fails += 1
        print(f"  ✗ FAIL [G-1] 정당한 구분이 보존되지 않음 {d1} "
              f"→ {[(c.original, c.corrected) for c in cards1]}")
    if any(c.junction_group for c in cards1):
        fails += 1
        print("  ✗ FAIL [G-1] 정당한 구분 쌍이 사용자 결정 그룹으로 묶임")

    # ── G-1b '정당한 구분'은 **서로 덮어쓰지 않을 때만** ──────────────────────
    #   양쪽 다 다수 근거가 명확해도, 한쪽 원문이 다른 쪽 **교정문 안에 어절째** 들어 있으면
    #   적용 후 뒤에 도는 카드가 앞 카드의 결과를 되돌린다(사용자 보고 2026-07-31:
    #   '전자문서지갑 기능 → 전자문서 지갑 기능 → 전자문서지갑 기능'으로 12곳 전량 무효화).
    #   G-1과 갈리는 지점은 **원문의 공백 유무**다 — '출산전후'(공백 없음)는 적용 단계의
    #   어절 경계 필터에 걸려 '출산전후휴가' 속 조각으로 치환되지 않지만,
    #   '전자문서 지갑'(공백 있음)은 그 필터를 받지 않아 그대로 치환된다.
    doc1b = ("전자문서지갑 " * 43 + "전자문서 지갑 " * 33
             + "전자문서 지갑 기능 " * 7 + "전자문서지갑 기능 " * 5)
    cards1b = [card("전자문서 지갑", "전자문서지갑", conf="high"),
               card("전자문서지갑 기능", "전자문서 지갑 기능")]
    d1b = _jp.resolve(cards1b, doc1b)
    if d1b["preserved"]:
        fails += 1
        print(f"  ✗ FAIL [G-1b] 상호 무효화 쌍이 '정당한 구분'으로 보존됨 {d1b}")
    if not all(c.junction_group for c in cards1b):
        fails += 1
        print(f"  ✗ FAIL [G-1b] 상호 무효화 쌍이 이음매 그룹으로 안 묶임 "
              f"→ {[(c.original, c.junction_group) for c in cards1b]}")
    if any(c.confidence != "low" for c in cards1b):
        fails += 1
        print("  ✗ FAIL [G-1b] 상호 무효화 쌍이 자동 적용(high)으로 남음")

    # ── G-2 자동 정합은 '상대 근거 전무'일 때만 ───────────────────────────────
    #   '수급자 확인서'가 문서 다수(근거 명확)이고, 괄호가 섞인 낱말은 변이가 없어
    #   근거 0 → 그 카드의 이음매를 다수 표기에 맞춘다.
    doc2 = ("수급자 확인서 " * 6 + "수급자확인서 " * 1
            + "급여(현금)수급자확인서 ")
    cards2 = [card("수급자확인서", "수급자 확인서"),
              card("급여(현금)수급자확인서", "급여(현금) 수급자확인서")]
    d2 = _jp.resolve(cards2, doc2)
    tgt = next((c for c in cards2 if c.original.startswith("급여(")), None)
    if tgt is None or tgt.corrected != "급여(현금) 수급자 확인서":
        fails += 1
        print(f"  ✗ FAIL [G-2] 자동 정합 실패 {d2} "
              f"→ {tgt.corrected if tgt else None!r}")
    #   반대로 양쪽 근거가 '약함'이면 정합하지 말고 사용자 결정 그룹으로.
    doc3 = "수당수급자 수당 수급자 수당수급자확인서 수당수급자 확인서 "
    cards3 = [card("수당수급자", "수당 수급자"),
              card("수당수급자확인서", "수당수급자 확인서")]
    d3 = _jp.resolve(cards3, doc3)
    if d3["grouped"] != 1 or d3["harmonized"]:
        fails += 1
        print(f"  ✗ FAIL [G-2] 근거 약한 충돌이 그룹화되지 않음 {d3}")
    if not all(c.junction_group and c.confidence == "low" for c in cards3):
        fails += 1
        print("  ✗ FAIL [G-2] 그룹 카드에 junction_group·low 강등 누락")

    # ── G-3 불변식 ─────────────────────────────────────────────────────────
    inv = []
    #   (a) original 불변 · 글자 불변 — 모든 시나리오에서
    inv.append(("original 불변·글자 불변", all(
        c.original.replace(" ", "") == c.corrected.replace(" ", "")
        for c in cards1 + cards2 + cards3)))
    #   (b) URL 미개입 — 참고문헌 URL에 공백을 넣으면 링크가 깨진다
    url = "http://www.law.go.kr/법령/남녀고용평등과일･가정양립지원에관한법률."
    doc4 = "가정 양립 " * 6 + "가정양립 " * 2 + url
    cards4 = [card("가정양립", "가정 양립"),
              card(url, url.replace("관한법률", "관한 법률"))]
    _jp.resolve(cards4, doc4)
    inv.append(("URL 카드 미개입",
                cards4[1].corrected == url.replace("관한법률", "관한 법률")))
    #   (c) 결합 방향 미탐 보완 금지 — '띄어 쓴 구를 붙임'은 만들지 않는다(⑩ 노이즈 클래스)
    doc5 = "학교밖청소년지원 학교밖청소년지원 학교 밖 청소년 지원 "
    cards5 = []
    d5 = _jp.resolve(cards5, doc5)
    inv.append(("결합 방향 보완 미생성",
                all(" " not in c.original for c in cards5)))
    #   (d) 보완 카드의 원문은 반드시 문서에 실재(브리지 적용 가능성)
    doc6 = ("기초연금 수급자 확인서 " * 6 + "기초연금수급자확인서 ")
    cards6 = []
    _jp.resolve(cards6, doc6)
    inv.append(("보완 카드 원문 실재",
                bool(cards6) and all(c.original in doc6 for c in cards6)))
    inv.append(("3조각 다수형 보완 동작",
                any(c.corrected == "기초연금 수급자 확인서" for c in cards6)))
    for name, ok in inv:
        if not ok:
            fails += 1
            print(f"  ✗ FAIL [G-3 불변식] {name}")

    # ── G-4 전파 산술 — 사용자 시뮬레이션 3종 ────────────────────────────────
    W1, W2 = "수당수급자", "수당수급자확인서"
    sims = [
        # (결정 낱말, 결정 공백, 대상, 대상 현재 공백, 기대 결과, 기대 확정여부)
        ("수락 A", W1, {2}, W2, {5}, {2, 5}, False),   # 남은 이음매 5 → 미확정
        ("거절 B", W2, set(), W1, {2}, set(), True),   # 전부 지배 → 확정
        ("거절 C", W1, set(), W2, {5}, {5}, False),    # 5는 미지배 → 미확정
    ]
    for name, dw, ds, tw, ts, want, want_settled in sims:
        got = _jp.propagate(dw, ds, tw, ts)
        settled = not (set(got) - _jp.governed(dw, tw))
        if set(got) != want or settled != want_settled:
            fails += 1
            print(f"  ✗ FAIL [G-4 전파] {name}: {sorted(got)} "
                  f"(기대 {sorted(want)}) settled={settled}/{want_settled}")

    # ── G-5 조사·기호가 붙은 낱말에서의 전파 산술 ─────────────────────────────
    #   이음매 산술은 '공백 제거 좌표의 문자열 오프셋'이라 조사·괄호·따옴표가 붙어도
    #   그대로 성립한다. 다만 **결정 카드가 결정하지 않은 이음매는 건드리지 않는다** —
    #   '가정양립'{2}를 '일가정양립을'에 옮기면 '일가정 양립을'이지 '일 가정 양립을'이
    #   아니다(일|가정은 다른 이음매다). 실측 doc11에서 '일 가정 양립을'이 나온 것은
    #   그 카드의 corrected가 이미 '일 가정양립을'이었기 때문이다.
    josa = [
        ("조사", "수급자확인서", {3}, "수급자확인서를", set(), "수급자 확인서를"),
        ("복합조사", "수급자확인서", {3}, "수급자확인서에서의", set(), "수급자 확인서에서의"),
        ("괄호", "수급자확인서", {3}, "급여(현금)수급자확인서", {6},
         "급여(현금) 수급자 확인서"),
        ("따옴표", "수당수급자", {2}, "‘수당수급자확인서’", {6}, "‘수당 수급자 확인서’"),
        ("미결정 이음매 불간섭", "가정양립", {2}, "일가정양립을", set(), "일가정 양립을"),
        ("기존 공백 누적", "가정양립", {2}, "일가정양립을", {1}, "일 가정 양립을"),
    ]
    for name, dw, ds, tw, ts, want in josa:
        got = _jp.apply_spaces(tw, _jp.propagate(dw, ds, tw, ts))
        if got != want:
            fails += 1
            print(f"  ✗ FAIL [G-5 조사·기호] {name}: {tw!r} → {got!r} (기대 {want!r})")

    # ── G-6 URL·경로 보존 ──────────────────────────────────────────────────
    #   띄어쓰기 finder는 '공백만 삽입=안전'을 전제로 하는데 URL·경로에서는 공백 삽입이
    #   값을 파괴한다(실측: 참고문헌 국가법령정보센터 링크가 '…관한 법률.'로 쪼개짐).
    urlish = [
        ("http://www.law.go.kr/법령/남녀고용평등과일･가정양립지원에관한법률.", True),
        ("www.moel.go.kr/policy", True), ("hong@example.co.kr", True),
        ("C:\\Users\\a.hwp", True), ("보고서.hwp", True),
        ("일가정양립을", False), ("급여(현금)수급자확인서", False), ("3.14", False),
    ]
    for w, want in urlish:
        if _morph.is_urlish(w) != want:
            fails += 1
            print(f"  ✗ FAIL [G-6 URL] is_urlish({w!r}) != {want}")
    #   finder 산출물에 URL 어절이 섞이지 않는가(실 텍스트)
    doc_url = ("가정 양립 " * 6 + "가정양립 " * 2
               + "http://www.law.go.kr/법령/남녀고용평등과일･가정양립지원에관한법률. "
               + "자료는 보고서.hwp 및 www.moel.go.kr/notice 참조. ")
    leaked = [o for f in (_morph.find_spacing_suggestions,
                          _morph.find_symbol_noun_spacing,
                          _morph.find_dependent_noun_spacing)
              for o, _c in f(doc_url) if _morph.is_urlish(o)]
    if leaked:
        fails += 1
        print(f"  ✗ FAIL [G-6 URL] finder가 URL 어절 카드를 냄: {leaked[:3]}")

    # ── G-8 제로폭 문자 어절 보호 ──────────────────────────────────
    #   ★실보고 2026-08-03: 멀젠한 '적용된다.'가 '적용된 다.'로 쪠개졌다. 원인은 원고에 박힐
    #   U+200B(제로폭 공백) — 실제 어절은 `적용된<U+200B>다.` 이고 kiwi가 그 문자를 NNG로
    #   태깅해 '관형형어미 + 명사' 규칙이 **유령 명사** 앞에서 발동했다. 눈에 보이지 않아
    #   카드만으로는 원인을 알 수 없는 부류라 회귀 게이트로 고정한다.
    #   ⚠ 테스트 데이터도 **ordinal로** 만든다 — 리터럴로 적으면 소스에서 안 보여 유지보수 불가.
    _ZWSP, _BOM = chr(0x200B), chr(0xFEFF)
    zw_doc = ("이는 공·사 부문 모두에 적용된" + _ZWSP + "다. "
              "그러므로 할" + _ZWSP + "수 있다. 정상 문장은 적용된다. 할 수 있다.")
    zw_leaked = []
    for f in (_morph.find_spacing_suggestions, _morph.find_symbol_noun_spacing,
              _morph.find_dependent_noun_spacing, _morph.find_auxiliary_verb_spacing,
              _morph.find_aux_connective_spacing, _morph.find_eojida_join):
        for o, c in f(zw_doc):
            if _morph.has_zero_width(o) or _morph.has_zero_width(c):
                zw_leaked.append((f.__name__, o, c))
    if zw_leaked:
        fails += 1
        print(f"  ✗ FAIL [G-8 제로폭] finder가 제로폭 어절 카드를 냄: {zw_leaked[:3]}")
    #   ⚠ 제로폭을 **지우는** 카드도 내서는 안 된다 — original이 문서 실제 문자열과
    #   달라져 적용 단계에서 조용히 실패한다('본문에 원문 없음' 부류).
    for o, _c in _morph.find_spacing_suggestions(zw_doc):
        if o not in zw_doc:
            fails += 1
            print(f"  ✗ FAIL [G-8 제로폭] 문서에 없는 원문 카드: {o!r}")
    zw_cases = [("적용된" + _ZWSP + "다.", True), ("적용된다.", False),
                ("할" + _ZWSP + "수", True), ("할 수", False),
                (_BOM + "머리말", True), ("머리말", False)]
    for w, want in zw_cases:
        if _morph.has_zero_width(w) != want:
            fails += 1
            print(f"  ✗ FAIL [G-8 제로폭] has_zero_width({w!r}) != {want}")

    # ── G-7 어절 꼬리 조각이 낱말의 머리가 되지 않는가 ─────────────────────────
    #   한글 런은 어절이 아니다 — 앞이 영문·숫자·한자면 그 런은 어절의 **꼬리 조각**이다.
    #   'EU AI Act의 주요내용'의 런은 ['의','주요내용']이라, 막지 않으면 조사 '의'가 낱말의
    #   머리로 승격돼 `의주요내용`이라는 없는 낱말이 서고 '의 주요내용'→'의 주요 내용'
    #   카드가 사용자에게 나갔다(사용자 보고 2026-08-03).
    #   ⚠ 그렇다고 '앞이 공백일 때만'으로 막으면 안 된다 — 이 저장소는 괄호·가운뎃점을
    #   낱말 경계로 세는 것이 원칙이라 '(현금)수급자 확인서'의 근거가 함께 날아간다
    #   (실측 실파일D: 잡음 5건과 함께 정당한 카드 6건 소실). 그래서 부호 뒤는 그 런이
    #   **어절의 조사 꼬리**일 때만 거부한다 — 아래 두 '부호 뒤' 케이스가 그 경계선이다.
    frag = [
        ("영문+조사", "EU AI Act의 주요 내용\nEU AI Act의 주요내용", "의주요내용", False),
        ("영문+조사2", "EU AI Act의 위험 기반\nEU AI Act의 위험기반", "의위험기반", False),
        ("숫자+꼬리", "2024년 주요 내용\n2024년 주요내용", "년주요내용", False),
        ("숫자+단위", "3대 윤리 원칙\n3대 윤리원칙", "대윤리원칙", False),
        ("부호 뒤 조사", "｢AI 추진법｣의 주요 내용\n｢AI 추진법｣의 주요내용",
         "의주요내용", False),
        ("본 낱말 보존", "EU AI Act의 주요 내용\nEU AI Act의 주요내용", "주요내용", True),
        ("문두", "주요 내용 정리\n주요 내용 요약", "주요내용정리", True),
        ("부호 뒤 명사", "급여(현금)수급자 확인서와 급여(현금)수급자 확인서를",
         "수급자확인서", True),
        ("따옴표 뒤", "‘수급자 확인서’와 ‘수급자 확인서’를", "수급자확인서", True),
        ("가운뎃점 뒤", "보조금·수급자 확인서 및 보조금·수급자 확인서를",
         "수급자확인서", True),
        ("각주번호 뒤따름", "주요 내용1) 정리\n주요 내용2) 요약", "주요내용", True),
    ]
    for name, doc, key, want in frag:
        if (key in _jp.build_variants(doc)) != want:
            fails += 1
            print(f"  ✗ FAIL [G-7 꼬리조각] {name}: {key!r} "
                  f"{'가 사라짐' if want else '가 생성됨'}")
    #   실제 카드로도 새지 않는가 — 원문이 조사로 시작하는 띄어쓰기 카드는 있을 수 없다.
    doc_frag = ("EU AI Act의 주요 내용 " * 6 + "EU AI Act의 주요내용 " * 3)
    _cards = []
    _jp.resolve(_cards, doc_frag)
    _bad = [c.original for c in _cards
            if (c.original or " ").split(" ")[0] in ("의", "은", "는", "이", "가",
                                                     "을", "를", "와", "과", "로")]
    if _bad:
        fails += 1
        print(f"  ✗ FAIL [G-7 꼬리조각] 조사로 시작하는 카드 생성: {_bad[:3]}")

    print(f"  G-1 보존 · G-2 정합/그룹 · G-3 불변식 {len(inv)}항목 · "
          f"G-4 전파 {len(sims)}케이스 · G-5 조사·기호 {len(josa)}케이스 · "
          f"G-6 URL {len(urlish)}판정+누출검사 · G-7 꼬리조각 {len(frag)}케이스 · "
          f"G-8 제로폭 {len(zw_cases)}판정+누출검사 완료")
    print(f"  → Phase G {'✅ 통과' if fails == 0 else f'❌ {fails}건 실패'}")
    return fails


def phase_d_rules():
    print("Phase D — 결정론 규칙 레이어 통합 게이트 (무발화/발화/페어 불변식)")
    fails = 0

    # D-1 페어 불변식 — 실 stdict.db 표제어에 비표준 어간 부분문자열 0건
    import sqlite3
    from core.spelling_pairs import _STEM_PAIRS
    db_path = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "data", "stdict.db")
    n_pairs = 0
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        for bad in _STEM_PAIRS:
            rows = conn.execute("SELECT word FROM words WHERE word LIKE ? LIMIT 3",
                                (f"%{bad}%",)).fetchall()
            n_pairs += 1
            if rows:
                fails += 1
                print(f"  ✗ FAIL [D-1 페어] {bad!r} 가 표제어에 존재: "
                      f"{', '.join(w for (w,) in rows)} — substring 치환 오염 위험")
        conn.close()
        print(f"  D-1 페어 불변식: {n_pairs}개 어간 표제어 충돌 검사 완료")
    else:
        print("  (stdict.db 없음 — D-1 스킵)")

    morph, finders = _det_finders()
    kiwi_ok = False
    try:
        kiwi_ok = morph.available()
    except Exception:
        pass
    if not kiwi_ok:
        print("  (kiwi 미가용 — 형태소 기반 finder 스킵)")

    def run_finder(fn, text):
        try:
            return [r for r in (fn(text) or [])]
        except Exception as e:
            return [("<예외>", str(e))]

    # D-2 무발화 — 정문 코퍼스에서 어떤 finder도 발화하면 안 된다
    n_neg = 0
    for sent in _CLEAN_CORPUS:
        for name, fn, needs_kiwi in finders:
            if needs_kiwi and not kiwi_ok:
                continue
            n_neg += 1
            hits = run_finder(fn, sent)
            if hits:
                fails += 1
                h = hits[0]
                print(f"  ✗ FAIL [D-2 무발화] {name} 이 정문에서 발화: {sent!r} → "
                      f"{h[0]!r}→{h[1]!r}")
    print(f"  D-2 무발화: 정문 {len(_CLEAN_CORPUS)}문장 × finder 검사 {n_neg}회 완료")

    # D-3 발화 — 대표 오류 포착(커버리지 회귀 감지)
    by_name = {name: (fn, nk) for name, fn, nk in finders}
    n_pos = 0
    for name, text, exp_o, exp_c in _POS_CASES:
        fn, needs_kiwi = by_name[name]
        if needs_kiwi and not kiwi_ok:
            continue
        n_pos += 1
        hits = run_finder(fn, text)
        ok = any(exp_o in h[0] and exp_c in h[1] for h in hits if len(h) >= 2)
        if not ok:
            fails += 1
            got = [(h[0], h[1]) for h in hits[:3] if len(h) >= 2]
            print(f"  ✗ FAIL [D-3 발화] {name}: {text!r} 기대 {exp_o!r}→{exp_c!r} "
                  f"실제={got}")
    print(f"  D-3 발화: {n_pos}케이스 완료")

    # D-3b 부호·단위 띄어쓰기 **앵커 최소화** — 같은 교정이 카드 N장으로 갈리지 않는가.
    #   계기(2026-08-03 사용자 보고): 'OECD.AI' 한 교정이 주변 글자에 따라 'OECD.AI,'·
    #   '추진한다(OECD.AI,'·'OECD.AI.(n.d.c),' 등 **12장**으로 갈렸다(실파일E 268K자,
    #   그 문서 문장부호 카드 20장 중 12장). 앵커를 삽입 지점 양옆 낱말 런으로 좁혀 1장(반복 14)으로.
    #   ⚠ 안전 복귀 2케이스가 핵심 — 좁힌 문자열이 규칙 밖 자리(URL 안)에도 있으면 토큰
    #     앵커로 되돌려야 한다. 깨지면 URL을 쪼개는 교정이 나간다.
    from core import spacing_rules as _sr2
    n_anc = 0
    for label, text, fn, expect in (
        ("동일 교정 1장 통합",   # ⚠ 좌측이 소문자여야 규칙이 발동한다(D-3c 참조 — 보고 원본
                              #    'OECD.AI'는 이제 모양 가드가 통째로 억제하므로 표본으로 못 쓴다)
         "정책이다(oecd.AI, n.d.a). 직결되며(oecd.AI, n.d.c), oecd.AI 또한 발표했다. oecd.AI.(n.d.c), “A”",
         "punct", [("oecd.AI", "oecd. AI")]),
        ("한글 문장 경계 앵커 유지", "결과다.그러므로 진행한다.",
         "punct", [("결과다.그러므로", "결과다. 그러므로")]),
        ("괄호·쉼표만 벗김", "이는 사실이다(life.Let us go), 그리고 끝.",
         "punct", [("life.Let", "life. Let")]),
        ("교차 스크립트 인용 앞말 보존", "그는 캐나가\"Say it 라고 했다.",
         "punct", [("캐나가\"Say", "캐나가 \"Say")]),
        ("★안전 복귀 — URL 안 등장", "자료는 oecd.AI, n.d.c 참고. 주소는 https://www.oecd.AI/data 이다.",
         "punct", [("oecd.AI,", "oecd. AI,")]),
        ("단위 부호 벗김", "예산은 13.6억원이고 끝났다.",
         "unit", [("13.6억원이고", "13.6억 원이고")]),
        ("★안전 복귀 — 앵커가 다른 앵커의 접두", "예산은 13.6억원이고, 추가로 13.6억원(추정)이 든다.",
         "unit", [("13.6억원이고", "13.6억 원이고"), ("13.6억원(추정)이", "13.6억 원(추정)이")]),
    ):
        n_anc += 1
        got = (_sr2.find_punct_spacing(text) if fn == "punct"
               else _sr2.find_unit_spacing(text))
        if got != expect:
            fails += 1
            print(f"  ✗ FAIL [D-3b 앵커] {label}: 기대={expect} 실제={got}")
        for o, f in got:                       # 불변식 — 글자 불변·공백만 삽입·본문 탐색 가능
            if (o.replace(" ", "") != f.replace(" ", "")
                    or f.count(" ") <= o.count(" ") or o not in text):
                fails += 1
                print(f"  ✗ FAIL [D-3b 불변식] {label}: {o!r} → {f!r}")
    print(f"  D-3b 앵커 최소화: {n_anc}케이스 완료")

    # D-3c 라틴 약칭·도메인 **모양 가드**(spacing_rules._is_abbrev_dot ③) — 예외 목록을
    #   열거하지 않고 '마침표 왼쪽 라틴 낱말에 대문자가 있으면 문장 종결이 아니다'로 판정한다
    #   (사용자 결정 2026-08-03). 실원고 5종 774K자의 문장부호 오탐 3건이 전부 이 형상이었다.
    #   ⚠ 무발화 케이스를 지우지 말 것 — 지우면 도메인·학위·법인 약칭 오교정이 되살아난다.
    #   ⚠ 발화 케이스도 지우지 말 것 — 가드가 라틴 문장 경계를 통째로 죽이는 퇴화를 잡는다.
    n_shape = 0
    for label, text, expect in (
        ("무발화 학위 약칭  Ph.D",   "산학 공동 Ph.D. 및 글로벌 펠로우십을 제도화한다.",   []),
        ("무발화 기관 약칭  OECD.AI", "정책 기조를 보이며(OECD.AI, n.d.a), 이는 변화다.",  []),
        ("무발화 법인 약칭  Inc.",    "스리랑카 기업 WSO2 Inc.이 개발한 미들웨어다.",      []),
        ("무발화 대문자 약칭 U.S.",   "그는 U.S.News 기사를 인용했다.",                  []),
        ("★발화 소문자 영문 문장 경계", "이는 사실이다(life.Let us go), 그리고 끝.",
         [("life.Let", "life. Let")]),
        ("★발화 한글 문장 경계",       "제시하였다.그러나 한계가 있다.",
         [("제시하였다.그러나", "제시하였다. 그러나")]),
        ("★발화 한글 뒤 영문 문장",    "중요한 점이다.AI 기술은 빠르다.",
         [("점이다.AI", "점이다. AI")]),
    ):
        n_shape += 1
        got = _sr2.find_punct_spacing(text)
        if got != expect:
            fails += 1
            print(f"  ✗ FAIL [D-3c 모양가드] {label}: 기대={expect} 실제={got}")
    print(f"  D-3c 라틴 약칭 모양 가드: {n_shape}케이스 완료")

    # D-4 규범표기 용언 활용형 동형이의 가드 — 워커 [5.7]/[5.8]과 동일 판정 함수를 게이트
    import nikl_dict as _nd
    norm_ok = False
    try:
        norm_ok = bool(_nd.lookup_norm("컨텐츠"))   # norm_map 테이블 가용성 프로브
    except Exception:
        pass
    if not (kiwi_ok and norm_ok):
        print("  (kiwi/norm_map 미가용 — D-4 스킵)")
    else:
        n_vh = 0
        for sent, w, key, expect_skip, label in _NORM_VERB_CASES:
            n_vh += 1
            try:
                got = _nd.is_verb_inflection_homograph(key, w, sent)
            except Exception as e:
                got = f"<예외 {e}>"
            if got != expect_skip:
                fails += 1
                print(f"  ✗ FAIL [D-4 용언가드] {key!r}(어절 {w!r}): 기대 "
                      f"{'보류' if expect_skip else '유지'} 실제={got} — {label}")
        print(f"  D-4 용언 활용형 가드: {n_vh}케이스 완료")

        # D-5 체언+조사 동형이의 가드(D-4의 명사 쪽 짝 — 말이/앞이/손과류)
        n_nh = 0
        for sent, w, key, expect_skip, label in _NORM_NOUN_CASES:
            n_nh += 1
            try:
                got = _nd.is_noun_josa_homograph(key, w, sent)
            except Exception as e:
                got = f"<예외 {e}>"
            if got != expect_skip:
                fails += 1
                print(f"  ✗ FAIL [D-5 체언가드] {key!r}(어절 {w!r}): 기대 "
                      f"{'보류' if expect_skip else '유지'} 실제={got} — {label}")
        print(f"  D-5 체언+조사 가드: {n_nh}케이스 완료")

        # D-6 규범표기 카드 등장 경계(검수 패널 대상 ⑤와 동일 판정)
        from core import morph as _mp
        n_oc = 0
        for run, orig, expect_keep, label in _NORM_OCC_CASES:
            n_oc += 1
            try:
                b = _mp.nominal_boundaries(run)
                p = run.find(orig)
                got = bool(b is not None and p in b and (p + len(orig)) in b)
            except Exception as e:
                got = f"<예외 {e}>"
            if got != expect_keep:
                fails += 1
                print(f"  ✗ FAIL [D-6 등장경계] {orig!r}⊂{run!r}: 기대 "
                      f"{'유지' if expect_keep else '제외'} 실제={got} — {label}")
        print(f"  D-6 규범표기 등장 경계: {n_oc}케이스 완료")

        # D-7 복합명사 **머리낱말 가족** 정합(2026-08-03 사용자 보고 — '수익 모델' ↔ '사업모델').
        #   ⓐ 동률의 '가족 우선' 타이브레이크(morph): 자기 다수가 없는 낱말만 가족을 따르고,
        #      자기 다수가 뚜렷한 낱말은 **절대** 안 끌려간다('산학협력' 51:10을 지키는 불변식).
        #   ⓑ 가족 충돌 산출(core/consistency_family): 수락된 카드만·방향이 갈릴 때만.
        from core import consistency_family as _cfam
        n_fam = 0

        def _fam_card(o, c, st="accepted"):
            return {"original": o, "corrected": c, "status": st,
                    "consistency_flip": True,
                    "spacing_family": list(_cfam.evidence_of(o, c, 1, 2))}

        # ⓐ 타이브레이크 — '가 나'/'가나'가 1:1 동률이고 같은 가족의 '다나'가 붙임 다수면 붙임.
        _tb_text = ("정책 자료 정리는 정책자료 정리와 같다. 정책 자료 목록과 정책자료 목록. "
                    "기술자료 정리, 기술자료 목록, 기술자료 검토, 기술 자료 검토.")
        try:
            _tb = _mp.find_compound_spacing_consistency(
                _tb_text, exists_fn=lambda w: False)
        except Exception as e:
            _tb = f"<예외 {e}>"
        n_fam += 1
        _tb_hit = [(a, b) for a, b in [(x[0], x[1]) for x in _tb]] if isinstance(_tb, list) else _tb
        if not isinstance(_tb, list) or not any(
                m == "정책 자료" and j == "정책자료" for m, j, *_ in _tb):
            fails += 1
            print(f"  ✗ FAIL [D-7 가족 타이브레이크] 동률 '정책자료'가 가족('자료') 다수="
                  f"붙임을 따라가야 한다 — 실제={_tb_hit}")

        # ⓑ 충돌 산출 — 단위는 **카드가 아니라 낱말(base)**. ★거절 카드도 반드시 포함한다
        #   (거절 = 앱이 아무것도 안 바꿈 = 문서에 두 표기가 그대로 남음. 사용자 지적
        #   2026-08-03 "거절 카드를 빼면 정당한 비교가 불가능하다"). 지우지 말 것.
        for label, cards, expect_heads in (
            ("방향 갈림 → 충돌",
             [_fam_card("수익모델", "수익 모델"), _fam_card("사업 모델", "사업모델")], ["모델"]),
            ("방향 같음 → 충돌 아님",
             [_fam_card("수익모델", "수익 모델"), _fam_card("사업모델", "사업 모델")], []),
            ("★거절 카드도 포함(혼재로 남음)",
             [_fam_card("수익모델", "수익 모델"),
              _fam_card("사업모델", "사업 모델", "rejected")], ["모델"]),
            ("★전부 거절 → 전부 혼재 = 충돌",
             [_fam_card("수익모델", "수익 모델", "rejected"),
              _fam_card("사업모델", "사업 모델", "rejected")], ["모델"]),
            # 검수 중 '반대 표기로 통일'을 고르면 원 카드는 거절되고 반대 교정이 합성·수락된다.
            #   낱말 단위로 안 보면 그 거절 카드가 '혼재'로 잡혀 방금 정리한 낱말이 다시 올라온다.
            ("★뒤집기 잔재는 혼재 아님(같은 낱말의 거절+수락 쌍)",
             [_fam_card("수익모델", "수익 모델", "rejected"),
              _fam_card("수익 모델", "수익모델"),      # 합성된 반대 교정(수락) → 붙임 확정
              _fam_card("사업 모델", "사업모델")], []),  # 같은 붙임 방향 → 충돌 아님
            ("낱말 하나뿐인 계열 → 대상 아님",
             [_fam_card("수익모델", "수익 모델", "rejected")], []),
            ("머리낱말 다름 → 무관",
             [_fam_card("수익모델", "수익 모델"), _fam_card("사업 전략", "사업전략")], []),
        ):
            n_fam += 1
            got = [f["head"] for f in _cfam.find_conflicts(cards)]
            if got != expect_heads:
                fails += 1
                print(f"  ✗ FAIL [D-7 가족 충돌] {label}: 기대={expect_heads} 실제={got}")

        # ⓒ 통일 결과 불변식 — 글자 불변(공백만 가감) + 대상은 '그 방향이 아닌 낱말'뿐 +
        #   거절(혼재) 낱말은 어느 방향이든 반드시 손대야 한다(그대로 두면 혼재가 남는다).
        n_fam += 1
        _cards = [_fam_card("수익모델", "수익 모델"),
                  _fam_card("사업모델", "사업 모델", "rejected")]
        _f = _cfam.find_conflicts(_cards)[0]
        for _d in ("spaced", "joined"):
            tg = _cfam.targets_for(_f, _d)
            if any(m["direction"] == _d for m in tg):
                fails += 1
                print(f"  ✗ FAIL [D-7 통일 대상] {_d}: 이미 그 방향인 낱말이 대상에 들어갔다")
            if not any(m["direction"] == _cfam.MIXED for m in tg):
                fails += 1
                print(f"  ✗ FAIL [D-7 혼재 누락] {_d}: 거절(혼재) 낱말이 대상에서 빠졌다")
            for m in _f["members"]:
                u = _cfam.unified_form(m, _d)
                if u.replace(" ", "") != m["joined"]:
                    fails += 1
                    print(f"  ✗ FAIL [D-7 글자 불변] {m['base']!r} → {u!r}")

        # ⓓ **머리 축**(`xx…` — 첫 성분이 같은 계열) 산출. 2026-08-03 사용자 지적:
        #   꼬리 축만 보고 있었다. 실측(사용자 지적 파일 26.8만 자) 머리 축 충돌 26계열·
        #   낱말 120 중 **81개가 머리 축에서만** 갈렸다(꼬리 축과 겹치는 건 39뿐).
        #   ⚠ 기존 꼬리 축 케이스(ⓑ)의 기대값이 그대로여야 한다 — 축 추가가 기존 산출을
        #     늘리거나 줄이면 안 된다(그건 회귀다).
        for label, cards, expect in (
            ("머리 축 방향 갈림 → 충돌",
             [_fam_card("기술개발", "기술 개발"), _fam_card("기술 격차", "기술격차")],
             [("lead", "기술")]),
            ("머리 축 방향 같음 → 충돌 아님",
             [_fam_card("기술개발", "기술 개발"), _fam_card("기술격차", "기술 격차")], []),
            ("머리 낱말 다름 → 무관",
             [_fam_card("기술개발", "기술 개발"), _fam_card("사업 격차", "사업격차")], []),
            ("두 축 동시 산출(꼬리 먼저)",
             [_fam_card("지원 정책", "지원정책"), _fam_card("보상정책", "보상 정책"),
              _fam_card("지원사업", "지원 사업")],
             [("tail", "정책"), ("lead", "지원")]),
        ):
            n_fam += 1
            got = [(f["axis"], f["head"]) for f in _cfam.find_conflicts(cards)]
            if got != expect:
                fails += 1
                print(f"  ✗ FAIL [D-7 머리 축] {label}: 기대={expect} 실제={got}")

        # ⓔ **두 축이 같은 낱말을 반대로 요구**할 때(=한 낱말이 두 계열에 속함).
        #   ⚠ 계약(2026-08-04 사용자 결정 '두 단계 검토'): **사용자가 먼저 수락한 계열이
        #     가져간다**(seq). 앱이 몰래 고르는 게 아니라 ① 순서는 사용자가 만들고
        #     ② 잠김으로 화면에 보이고 ③ override로 되돌릴 수 있어야 성립한다. 셋 중 하나라도
        #     빠지면 조용한 오통일이므로, 이 세 케이스를 지우지 말 것.
        _x_cards = [_fam_card("지원 정책", "지원정책"), _fam_card("보상정책", "보상 정책"),
                    _fam_card("지원사업", "지원 사업")]
        _x_fams = _cfam.find_conflicts(_x_cards)
        _tail_f = next(f for f in _x_fams if f["axis"] == "tail")
        _lead_f = next(f for f in _x_fams if f["axis"] == "lead")

        n_fam += 1
        _tail_f.update(status="accepted", choice="joined", seq=1)   # 먼저: …정책 → 붙임
        _lead_f.update(status="accepted", choice="spaced", seq=2)   # 나중: 지원… → 띄어쓰기
        _p = _cfam.plan(_x_fams, _x_fams)
        _own = _p["locked"].get("지원정책", (None, None))[0]
        if _own is not _tail_f or _p["want"].get("지원정책") != "joined":
            fails += 1
            print(f"  ✗ FAIL [D-7 잠김] 먼저 수락한 계열이 낱말을 가져가야 한다 — "
                  f"주인={_own and _own['key']} want={_p['want'].get('지원정책')}")

        n_fam += 1
        _p = _cfam.plan(_x_fams, _x_fams, {"지원정책": _lead_f["key"]})
        if _p["want"].get("지원정책") != "spaced" or \
                _p["locked"].get("지원정책", (None,))[0] is not _lead_f:
            fails += 1
            print(f"  ✗ FAIL [D-7 잠김 되돌리기] override가 주인을 바꿔야 한다 — "
                  f"실제 want={_p['want'].get('지원정책')}")

        n_fam += 1
        _tail_f["choice"] = _lead_f["choice"] = "spaced"       # 두 계열이 **같은** 방향
        _jobs, _bl = _cfam.resolve_jobs(_x_fams, _x_fams)
        _n_hit = sum(1 for _a, _ci, o, _r in _jobs if o == "지원 정책")
        if _bl or _n_hit != 1:
            fails += 1
            print(f"  ✗ FAIL [D-7 같은 방향 중복] 같은 낱말을 두 번 뒤집으면 원위치가 된다 "
                  f"— 동작 {_n_hit}회·blocked={_bl}")

        # ⓖ **두 축 공동 기본 제안**(align_proposals) — 이웃 계열과 부딪히는 기본값을 미리
        #   맞춘다. ⚠ 근거가 뚜렷한 계열(strong)은 절대 안 뒤집는다. 전부 한 방향으로 모는
        #   '강제 통일'이 되면 '산학 협력'·'개인 정보'가 나온다(실측으로 닫힌 금지 사항).
        n_fam += 1
        _a_cards = [_fam_card("지원 정책", "지원정책"), _fam_card("보상정책", "보상 정책"),
                    _fam_card("지원사업", "지원 사업")]
        _a_fams = _cfam.find_conflicts(_a_cards)
        _before = {f["key"]: f["proposal"] for f in _a_fams}
        _cfam.align_proposals(_a_fams)
        _after = {f["key"]: f["proposal"] for f in _a_fams}
        _moved_strong = [f["key"] for f in _a_fams
                         if f["strong"] and _before[f["key"]] != _after[f["key"]]]
        if _moved_strong:
            fails += 1
            print(f"  ✗ FAIL [D-7 공동 제안] 근거가 뚜렷한 계열을 뒤집었다 — {_moved_strong}")

        n_fam += 1
        for f in _a_fams:
            f.update(status="accepted", choice=f["proposal"], seq=1)
        if _cfam.plan(_a_fams, _a_fams)["locked"]:
            fails += 1
            print(f"  ✗ FAIL [D-7 공동 제안] 기본 제안대로 전부 수락했는데 낱말이 부딪힌다 "
                  f"— 공동 풀이가 안 먹었다")

        n_fam += 1
        if (_cfam.family_label(_tail_f), _cfam.family_label(_lead_f)) != ("…정책", "지원…"):
            fails += 1
            print(f"  ✗ FAIL [D-7 계열 라벨] 축 없이 키만 쓰면 '…지원'과 '지원…'이 화면에서 "
                  f"구분되지 않는다")

        # ⓕ ★**멀쩡한 계열 깨뜨리기 금지**(불변식: 이 단계는 새 혼재를 만들지 않는다).
        #   실측(사용자 지적 파일): 가드 없이 두 축을 통일하면 **새로 갈린 계열 16건**이
        #   생겨 총 충돌이 28→30으로 늘었다 — 축 추가의 의미가 사라진다. 그 계열은 갈리지
        #   않아 카드로도 안 나오니 사용자는 볼 기회조차 없다. 지우지 말 것.
        #   구성: 머리 계열 '현장…'은 갈렸고(기반=띄어쓰기 / 실습=붙임),
        #        꼬리 계열 '…기반'은 **맞아 있다**(현장 기반·공급 기반 둘 다 띄어쓰기).
        _g_cards = [_fam_card("현장기반", "현장 기반"), _fam_card("공급기반", "공급 기반"),
                    _fam_card("현장 실습", "현장실습")]
        _g_all = _cfam.analyze(_g_cards)
        _g_intact = [f for f in _g_all if not f["split"]]
        _g_lead = next(f for f in _g_all if f["axis"] == "lead" and f["head"] == "현장")
        n_fam += 1
        if not any(f["axis"] == "tail" and f["head"] == "기반" for f in _g_intact):
            fails += 1
            print(f"  ✗ FAIL [D-7 전체 계열] analyze()는 **갈리지 않은 계열도** 돌려줘야 "
                  f"한다(가드가 그걸 검사한다) — 실제={[f['head'] for f in _g_intact]}")

        n_fam += 1
        _g_lead.update(status="accepted", choice="joined")     # 현장… → 붙임
        _gp = _cfam.plan([_g_lead], _g_all)
        if "현장기반" not in _gp["blocked"] or _gp["jobs"]:
            fails += 1
            print(f"  ✗ FAIL [D-7 계열 보호] '현장기반'을 붙이면 맞아 있던 '…기반' 계열이 "
                  f"갈린다 → 손대지 않아야 한다 — blocked={list(_gp['blocked'])} "
                  f"jobs={_gp['jobs']}")

        n_fam += 1
        _g_lead["choice"] = "spaced"                           # 반대 방향은 아무것도 안 깨뜨린다
        _gp = _cfam.plan([_g_lead], _g_all)
        if _gp["blocked"] or len(_gp["jobs"]) != 1:
            fails += 1
            print(f"  ✗ FAIL [D-7 계열 보호] 가드는 **깨뜨리는 방향에만** 걸려야 한다"
                  f"(전면 차단이면 통일이 죽는다) — blocked={list(_gp['blocked'])} "
                  f"jobs={_gp['jobs']}")
        print(f"  D-7 복합명사 가족 정합: {n_fam}케이스 완료")

    print(f"  → Phase D {'✅ 통과' if fails == 0 else f'❌ {fails}건 실패'}")
    return fails


# ── Phase E: 중의성 데이터셋 발화 델타 감시 (외부 자산·선택) ────────────────
# 바른 중의성 데이터셋(35,396문장 정문)에 결정론 finder를 전수 적용해 **finder별 발화 수**가
# 직전 베이스라인보다 **늘었는지**만 본다. 늘었다 = 새 규칙이 정문을 건드리기 시작했다 = 과교정 회귀.
#
# ⚠ 왜 pass/fail 절대치가 아니라 '델타'인가: 이 코퍼스는 세종·구어 전사 비중이 커서 발화의
#   상당수('간게'→'간 게')는 구어체에 대한 **정상** 발화다. 절대 0을 요구하면 영구 FAIL이라
#   게이트로 쓸 수 없다. 그래서 '증가 감시'만 한다.
# ⚠ 라이선스: 데이터셋은 CC BY-NC(사내=상업 맥락)이라 레포에 못 넣는다. 그래서
#   ① KS_AMBIG_DATA 미설정이면 **스킵**(레포만으로 A~D는 그대로 재현 가능) ②베이스라인 파일도
#   레포가 아니라 **데이터셋 clone 옆**에 저장한다. 상세: eval/ambiguity_scan/README.md.

def phase_e_ambiguity(save_baseline=False, limit=3000):
    print("─" * 64)
    print("Phase E — 중의성 데이터셋 발화 델타 (외부 자산)")
    try:
        sys.path.insert(0, os.path.join(_ROOT, "eval", "ambiguity_scan"))
        import run_ambiguity_scan as amb            # 지연 import (순환 방지)
    except Exception as e:
        print(f"  (스캐너 로드 실패 — 스킵: {e})")
        return 0

    counts, n_sents, data_dir = amb.firing_counts(limit=limit)
    if counts is None:
        print("  (KS_AMBIG_DATA 미설정 — 스킵. 데이터셋 없이도 Phase A~D는 완전히 재현된다)")
        return 0

    bp = amb.baseline_path()
    if save_baseline:
        with io.open(bp, "w", encoding="utf-8") as f:
            json.dump({"limit": limit, "n_sents": n_sents, "counts": counts},
                      f, ensure_ascii=False, indent=2)
        print(f"  정문 {n_sents:,}문장 발화 베이스라인 저장 → {bp}")
        for name in sorted(counts, key=lambda k: -counts[k]):
            print(f"      {name}: {counts[name]:,}")
        return 0

    if not os.path.exists(bp):
        print(f"  (베이스라인 없음 — `--save-ambig-baseline` 으로 먼저 기준선을 잡으세요)")
        print(f"  현재 발화 총 {sum(counts.values()):,}건 / 정문 {n_sents:,}문장")
        return 0

    # ⚠ utf-8-sig + try — 베이스라인은 레포 밖 파일이라 손으로 편집되기 쉽고(메모장/PowerShell
    #   `Out-File -Encoding utf8`은 BOM을 붙인다) BOM 하나로 json.load가 터진다. Phase E는
    #   **선택적 외부 자산** 단계이므로 무슨 일이 있어도 Phase A~D 결과를 죽이면 안 된다.
    try:
        with io.open(bp, encoding="utf-8-sig") as f:
            base = json.load(f)
    except Exception as e:
        print(f"  (베이스라인 읽기 실패 — 스킵: {e})")
        return 0
    if base.get("limit") != limit:
        print(f"  (표본 크기 불일치: 기준 {base.get('limit')} vs 현재 {limit} — 비교 스킵)")
        return 0
    old = base.get("counts", {})
    fails, worse, better = 0, [], []
    for name in sorted(set(old) | set(counts)):
        o, n = old.get(name, 0), counts.get(name, 0)
        if n > o:
            worse.append((name, o, n))
            fails += 1
        elif n < o:
            better.append((name, o, n))
    print(f"  정문 {n_sents:,}문장 × finder — 발화 {sum(old.values()):,} → {sum(counts.values()):,}")
    for name, o, n in better:
        print(f"      ✅ 개선 {name}: {o:,} → {n:,}")
    for name, o, n in worse:
        print(f"      ✗ FAIL [E 발화증가] {name}: {o:,} → {n:,} — 정문 발화가 늘었다(과교정 회귀 의심)")
    if not worse:
        print("  → Phase E ✅ 통과 (발화 증가 없음)")
    else:
        print(f"  → Phase E ❌ {fails}종 증가 — 원인 확인 후 정당하면 "
              f"`--save-ambig-baseline` 으로 기준선 갱신")
    return fails


def phase_a():
    print("─" * 64)
    print("Phase A — 가드 단위 (결정론, API 불필요)")
    fails = 0
    for o, c, src, expect_drop, label in GUARD_CASES:
        cor = Correction(original=o, corrected=c, reason="", source=src, color=HL_TYPO)
        kept, _ = ai_guards.filter_overcorrections([cor])
        dropped = (len(kept) == 0)
        ok = (dropped == expect_drop)
        if not ok:
            fails += 1
            print(f"  ✗ FAIL [{label}] {o[:34]!r}→{c[:20]!r}  기대제외={expect_drop} 실제={dropped}")
    print(f"  → {len(GUARD_CASES) - fails}/{len(GUARD_CASES)} 통과" + ("  ✅" if fails == 0 else "  ❌"))
    return fails


# ── Phase B: AI 경로 (실 Gemini) ────────────────────────────
def _load_cases():
    cases = []
    with io.open(os.path.join(_HERE, "cases.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def phase_b():
    from core.correction_engine import build_engine
    from core.config_loader import ConfigLoader
    from core.models import AI_CALL_DELAY
    import nikl_dict

    key = ConfigLoader().get_gemini_key()
    engine = build_engine(key)
    if engine is None:
        print("  ⚠ Gemini 키 없음 — Phase B 건너뜀")
        return None
    val = nikl_dict.KoreanDictValidator()
    stop = threading.Event()
    cases = _load_cases()

    print("─" * 64)
    print(f"Phase B — AI 경로 (실 Gemini, {len(cases)}개 케이스)")
    results = []
    for i, case in enumerate(cases):
        text = case["text"]
        susp = val.extract_suspicious_words(text, stop_event=stop) if val.available else []
        ai = engine.check_scope(text, susp, scope_typo=True, scope_spacing=True,
                                scope_polish=False, stop_event=stop)
        ai, _ = ai_guards.filter_overcorrections(ai)   # 워커와 동일 가드

        violations = []
        for s in case.get("forbid_delete", []):
            for c in ai:
                if s in c.original and s not in c.corrected:
                    violations.append(f"삭제금지 '{s}' 위반: {c.original[:34]!r}→{c.corrected[:22]!r}")
        for s in case.get("forbid_corrected_contains", []):
            for c in ai:
                if s in c.corrected:
                    violations.append(f"교정문 금지문자열 '{s}': {c.corrected[:34]!r}")

        caught = []
        for e in case.get("expect", []):
            hit = any(e["orig"] in c.original and e["corr"] in c.corrected for c in ai)
            caught.append({"orig": e["orig"], "corr": e["corr"], "hit": hit})

        status = "✅" if not violations else "❌"
        rec = ""
        if caught:
            rec = "  recall " + "".join("○" if x["hit"] else "✗" for x in caught)
        print(f"  [{case['id']}] AI {len(ai)}건 · 위반 {len(violations)} {status}{rec}")
        for v in violations:
            print(f"        ⚠ {v}")
        results.append({"id": case["id"], "n_ai": len(ai),
                        "violations": violations, "caught": caught})
        if i < len(cases) - 1:
            time.sleep(AI_CALL_DELAY)
    return results


def _summary(b_results):
    total_viol = sum(len(r["violations"]) for r in b_results)
    exp = [c for r in b_results for c in r["caught"]]
    rec_hit = sum(1 for c in exp if c["hit"])
    print("─" * 64)
    print(f"요약: 과교정 누수(forbid 위반) = {total_viol}건 "
          + ("✅ (precision 안전)" if total_viol == 0 else "❌ (회귀!)"))
    if exp:
        print(f"      recall(expect) = {rec_hit}/{len(exp)} (비결정 — 정보성)")
    return total_viol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="Phase B(실 Gemini) 포함")
    ap.add_argument("--save-baseline", action="store_true", help="결과를 baseline.json으로 저장")
    ap.add_argument("--save-ambig-baseline", action="store_true",
                    help="Phase E 발화 기준선을 (데이터셋 옆에) 저장/갱신")
    ap.add_argument("--no-ambig", action="store_true",
                    help="Phase E(중의성 데이터셋) 건너뛰기")
    args = ap.parse_args()

    a_fails = phase_a()
    a_fails += phase_a_doc_dict()
    a_fails += phase_a_norm_guard()
    a_fails += phase_a_spacing_guard()
    a_fails += phase_a_grammar_demote()
    a_fails += phase_f_realword()
    a_fails += phase_g_junction()
    a_fails += phase_d_rules()
    if not args.no_ambig:
        a_fails += phase_e_ambiguity(save_baseline=args.save_ambig_baseline)
    b_results = None
    if args.full:
        b_results = phase_b()
        if b_results is not None:
            total_viol = _summary(b_results)
        else:
            total_viol = None
    else:
        print("  (Phase B는 --full 로 실행)")
        total_viol = None

    if args.save_baseline and b_results is not None:
        base = {"phase_a_fails": a_fails, "phase_b": b_results}
        with io.open(os.path.join(_HERE, "baseline.json"), "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        print("baseline.json 저장 완료.")
    else:
        # baseline과 비교(있으면)
        bp = os.path.join(_HERE, "baseline.json")
        if b_results is not None and os.path.exists(bp):
            base = json.load(io.open(bp, encoding="utf-8"))
            base_viol = sum(len(r["violations"]) for r in base.get("phase_b", []))
            now_viol = sum(len(r["violations"]) for r in b_results)
            print("─" * 64)
            print(f"baseline 대비 과교정 누수: {base_viol} → {now_viol}",
                  "✅ 유지/개선" if now_viol <= base_viol else "❌ 회귀 — 변경 재검토 필요")

    # 종료 코드: 가드 단위 실패 또는 과교정 누수면 비정상(CI 게이트용)
    bad = a_fails + (total_viol or 0)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
