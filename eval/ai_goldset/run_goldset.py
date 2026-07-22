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

    # drop_word_substitution_paraphrase: 문맥 윤문의 '단어 교체'(하에→아래) 차단 —
    #   조사 추가·오탈자·최소대립쌍은 보존. 실 사전이 필요해 _WDICT로 등재 여부 모사.
    _WDICT = {"하", "아래", "위험", "리스크", "몇일", "며칠", "역활", "역할",
              "지향", "지양", "정부", "정책"}
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

    n = (len(exp_cases) + len(hd_cases) + len(dm_cases) + len(ws_cases) + len(pj_cases)
         + len(gn_cases) + len(cv_cases))
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
