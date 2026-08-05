"""
core/morph.py — 형태소 분석 기반 기본형(lemma) 복원
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
kiwipiepy(순수 C++, JVM 불필요)로 표면형을 형태소 분석해 내용 형태소의
**기본형(사전 표제어 형태)** 을 복원한다.

목적: 사전은 표제어(겪다)만 담고 활용형(겪고·겪었던)은 없다. 표면형을 그대로
사전 조회하면 멀쩡한 활용형이 "미등재"로 잡힌다. 형태소 분석으로 기본형을
복원해 사전과 대조하면 이 거짓 미등재를 없앨 수 있다.

  · GUI-agnostic (PySide6 미사용) — core/ 규칙 준수
  · kiwipiepy 미설치/로드 실패 시 graceful: available()=False, 호출은 []/False 반환
  · Kiwi 인스턴스는 무겁게 1회 로드 후 재사용(스레드 안전한 analyze 사용)
"""

import re
import threading

# 내용 형태소(어휘 의미를 갖는) 태그 — 조사(JK*)·어미(E*)·기호(S*) 등은 제외.
#   V : 용언(동사 VV·형용사 VA·보조용언 VX, 불규칙 VV-I/VA-I 포함) → 기본형에 '다' 부착
#   N : 체언(일반/고유/의존 명사) → 표면형이 곧 기본형
#   X : 어근(XR) → 표면형 사용
_VERBAL = ("VV", "VA", "VX")
_NOMINAL = ("NNG", "NNP", "NNB", "NR", "NP")

_kiwi = None
_kiwi_lock = threading.Lock()
_unavailable = False


def _get_kiwi():
    """Kiwi 싱글턴 지연 로드. 실패 시 None (이후 재시도 안 함)."""
    global _kiwi, _unavailable
    if _unavailable:
        return None
    if _kiwi is None:
        with _kiwi_lock:
            if _kiwi is None and not _unavailable:
                try:
                    from kiwipiepy import Kiwi
                    # 모델(~105MB)은 데이터 패키지로 분리 배포된다(datapaths 참조).
                    #
                    # ⚠ **모델 경로를 검증한 뒤에만** Kiwi를 만든다. 경로가 틀리거나 파일이
                    #   빠진 채로 넘기면 파이썬 예외가 난 직후 **네이티브 힙 손상으로
                    #   프로세스가 통째로 죽는다**(실측 exit 0xC0000374). 아래 try/except는
                    #   그 크래시를 잡지 못하므로, 방어선은 호출 '전' 검증뿐이다.
                    #   실제 시나리오: 데이터 패키지 없이 앱만 설치/업데이트한 경우.
                    mp = None
                    try:
                        from datapaths import kiwi_model_dir, is_frozen
                        d = kiwi_model_dir()
                        if d is not None:
                            mp = str(d)
                        elif is_frozen():
                            # 빌드본엔 pip 기본 모델이 없다(번들에서 제외됨). 온전한 동봉
                            # 모델도 없으면 Kiwi를 만들면 안 된다 — 만들면 죽는다.
                            _unavailable = True
                            return None
                    except Exception:
                        mp = None
                    _kiwi = Kiwi(model_path=mp) if mp else Kiwi()
                except Exception:
                    _unavailable = True
                    return None
    return _kiwi


def available() -> bool:
    """형태소 분석 사용 가능 여부."""
    return _get_kiwi() is not None


def analyze_bases(word: str) -> list:
    """word의 내용 형태소 기본형 목록을 [(기본형, 'V'|'N'|'X')] 로 반환.

    분석 실패·미설치 시 []. 조사/어미는 버린다.
      "겪고"   → [("겪다", "V")]
      "먹었습니다" → [("먹다", "V")]
      "고독사"  → [("고독사", "N")]
      "키메세지" → [("키", "N"), ("메세지", "N")]   # 복합어는 분해됨
    """
    kiwi = _get_kiwi()
    if kiwi is None or not word:
        return []
    try:
        res = kiwi.analyze(word)
    except Exception:
        return []
    if not res:
        return []
    tokens = res[0][0]
    bases = []
    for t in tokens:
        tag = t.tag
        if tag.startswith(_VERBAL):
            # 용언 어간 → 기본형 = 어간 + '다'. Kiwi는 불규칙(VV-I 등)도
            # 어간을 표제어 형태로 정규화하므로 '다'만 붙이면 표제어가 된다.
            bases.append((t.form + "다", "V"))
        elif tag in _NOMINAL:
            bases.append((t.form, "N"))
        elif tag == "XR":
            bases.append((t.form, "X"))
    return bases


# ── URL·경로·이메일 어절 보호 ─────────────────────────────────────────────
# 실측(2026-07-30, 양육지원 연구 .hwp 참고문헌): find_spacing_suggestions가
#   'http://www.law.go.kr/법령/남녀고용평등과일･가정양립지원에관한법률.' 어절을 분석해
#   '…관한 법률.'로 띄우는 카드를 냈다. URL에 공백이 들어가면 **링크가 깨진다** — 그리고
#   교정문이 원문과 글자가 같아(공백만 추가) 어느 가드도 이를 이상하게 보지 않는다.
#   ⚠ 한글이 섞인 URL(국가법령정보센터 링크)이라 '한글 포함' 조건으로는 못 걸러진다.
#   spacing_rules._SKIP_TOKEN과 같은 부류의 판정이지만 그쪽은 토큰 내부 부호 규칙용이라
#   여기서 어절 단위로 따로 둔다(그 모듈은 이미 골드셋에 고정돼 있어 건드리지 않는다).
_URLISH = re.compile(
    r"(https?://|ftp://|www\.|[\w.+-]+@[\w-]+\.[a-z]{2,}"
    r"|\.(?:com|org|net|io|kr|co|gov|edu|go|or|ac|re|html?|pdf|hwp|docx?|xlsx?"
    r"|pptx?|py|js|json|md|txt|csv)(?:\b|/)|[A-Za-z]:[\\/]|/{2,})",
    re.I,
)


def is_urlish(word: str) -> bool:
    """URL·파일 경로·이메일처럼 보이는 어절인가 — 띄어쓰기 제안에서 통째로 제외한다.

    띄어쓰기 finder들은 '글자 불변(공백만 삽입)'이라 안전하다는 전제로 동작하는데,
    URL·경로에서는 공백 삽입 자체가 값을 파괴하므로 그 전제가 성립하지 않는다.
    """
    return bool(word) and bool(_URLISH.search(word))


# ── 제로폭·비가시 문자 어절 보호 ───────────────────────────────────────────
# 실측(2026-08-03, 사회보장 AI 가이드라인 .hwp): '적용된다.'가 **'적용된 다.'** 로 쪼개지는
#   카드가 나왔다(사용자 보고). 원인은 형태소 분석이 아니라 **원고에 박힌 U+200B(제로폭
#   공백)** 다 — 실제 어절은 `적용된<U+200B>다.` 이고, kiwi는 그 제로폭 문자를 **NNG(명사)**
#   로 태깅한다(`[적용/NNG, 되/XSV, ᆫ/ETM, <U+200B>/NNG, 이/VCP, 다/EF]`). 그래서
#   '관형형어미(ETM) + 명사' 규칙이 **유령 명사** 앞에서 발동해 어미 '-ㄴ다'를 갈라 놨다.
#   ⚠ 화면에서도 로그에서도 **보이지 않는 글자**라 카드만 보면 원인을 알 수 없다
#   (`적용된다`와 `적용된<U+200B>다`는 눈으로 구별 불가) — 이 문서에 13개가 박혀 있었다.
#   ⚠ 제거로 고칠 수 없다: 제로폭을 지우면 글자가 바뀌어 '공백만 삽입' 불변식이 깨지고,
#   `original`이 문서 실제 문자열과 달라져 적용 단계에서 조용히 실패한다
#   ([[hwp-gettext-boundary-verify]] '본문에 원문 없음' 부류). 그래서 **어절째 건너뛴다** —
#   `is_urlish`와 같은 처리(그 어절의 띄어쓰기는 미탐으로 남기고, 틀린 카드를 내지 않는다).
#   ⚠ 이 집합은 **반드시 ordinal(0x200B)로** 적을 것 — 문자를 리터럴로 넣으면 소스에서도
#   보이지 않아 유지보수가 불가능하고, 편집 중에 조용히 사라지거나 늘어난다(실제로 겪음).
_ZERO_WIDTH = frozenset((
    0x200B,           # ZERO WIDTH SPACE ← 실측된 원인
    0x200C, 0x200D,   # ZWNJ / ZWJ
    0x2060, 0xFEFF,   # WORD JOINER / ZERO WIDTH NO-BREAK SPACE(BOM)
    0x00AD,           # SOFT HYPHEN
    0x180E,           # MONGOLIAN VOWEL SEPARATOR
    0x200E, 0x200F,   # LRM / RLM
))


def has_zero_width(word: str) -> bool:
    """어절에 제로폭·비가시 문자가 섞였는가 — 띄어쓰기 제안에서 통째로 제외한다.

    kiwi가 이런 문자를 NNG로 읽어 **유령 명사**가 생기고, 그 앞뒤로 엉뚱한 띄어쓰기
    경계가 만들어진다(`적용된<U+200B>다.`→'적용된 다.', `할<U+200B>수`도 같은 모양).
    """
    return bool(word) and any(ord(ch) in _ZERO_WIDTH for ch in word)


def strip_josa(word: str):
    """어절 끝의 조사(J*)와 계사(이다/아니다 VCP·VCN + 그 어미)만 잘라낸 base 반환.

    형태소의 char 위치(start)로 **표면 문자열을 슬라이스**하므로 내부 공백·표기를
    그대로 보존하고, 형태소를 재결합하지 않는다(공백 손실·정규화 왜곡 방지).

    정규식(_JOSA_RE)이 못 가르는 '단음절 조사 vs 명사 끝음절' 모호성을 해소한다:
      · '키메세지인' → '키메세지'   (인 = 계사 '이다'의 관형형 → 제거)
      · '외국인'·'디자인' → 그대로  (인 = 명사의 일부 NNG → 보존)
      · '국가'·'조사가'  → '국가'·'조사' (가 = 명사 일부 vs 주격조사, 정확 판별)
      · '키 메시지를' → '키 메시지'  (공백 보존)
      · '생각하고' → '생각하고'      (용언 어미는 보존 — 계사 꼬리만 제거)

    용언 어미(E*)는 **계사(VCP/VCN) 바로 뒤일 때만** 제거한다. 일반 용언의 활용
    어미는 보존해 '생각하다'류를 base로 오분해하지 않는다.

    kiwipiepy 미설치/분석 실패 시 None — 호출 측이 정규식으로 폴백한다.
    """
    kiwi = _get_kiwi()
    if kiwi is None or not word:
        return None
    try:
        tokens = kiwi.analyze(word)[0][0]
    except Exception:
        return None
    if not tokens:
        return None
    cut = len(word)
    i = len(tokens) - 1
    while i >= 0:
        tag = tokens[i].tag
        if tag.startswith("J"):              # 조사 (격/보조/접속/인용)
            cut = tokens[i].start
            i -= 1
            continue
        if tag in ("VCP", "VCN"):            # 계사 이다/아니다
            cut = tokens[i].start
            i -= 1
            continue
        if tag.startswith("E"):              # 어미 — 계사 꼬리(예: 이+ㄴ='인')일 때만 제거
            j = i
            while j >= 0 and tokens[j].tag.startswith("E"):
                j -= 1
            if j >= 0 and tokens[j].tag in ("VCP", "VCN"):
                cut = tokens[j].start
                i = j - 1
                continue
            break                            # 용언 활용 어미 → 보존
        break
    return word[:cut].strip()


# 용언 활용 어미(활용형 판정용) — ⚠ ETN(명사형 ㅁ/기)은 제외한다. 명사형은
#   '짜집기·바램' 같은 명사형 오표기와 동형이라, ETN까지 포함하면 그 규범표기
#   카드를 잘못 억제한다(2026-07-14 실측: ETN 제외로 짜집기/바램 카드 보존).
_INFL_ENDINGS = ("ETM", "EF", "EC", "EP")


def verb_inflection_lemma(text: str, eojeol: str, max_occurrences: int = 4):
    """eojeol이 문서 문맥에서 '용언의 활용형'으로 읽히면 그 기본형(어간+다)을 반환.

    규범표기(norm_map)류 '어절 표면 매칭' 교정의 **용언 활용형 동형이의 가드**용:
    '나올'(나오+ㄹ 관형형)이 명사 표제어 '나올(羅兀)→너울'에 오매칭되는 부류를
    문맥 형태소 분석으로 판별한다(norm_map 위험군 377건 실측, 2026-07-14).

    판정 — 통어절 등장( (?<![가-힣])eojeol(?![가-힣]) ) 표본 최대 max_occurrences개 중
    **하나라도** 용언으로 읽히면 그 기본형 반환(하나라도 용언이면 일괄 치환이 그 등장을
    훼손하므로 보수적으로 전체 보류가 옳다):
      · 등장 주변 ±48자 창을 kiwi로 분석해 eojeol 스팬이 [첫 형태소 V*(VV/VA/VX) +
        끝 어미 _INFL_ENDINGS]이면 용언 활용형.
      · 기본형 = 어간 + '다' (kiwi는 불규칙 어간도 표제어 형태로 정규화).

    ⚠ 조사 딸린 표면('찌게를')은 명사 사용 신호이므로 **호출 측이 가드 대상에서 제외**할 것.
    ⚠ 반환된 기본형이 그 자체로 비표준 용언(치루다·쳐지다 — norm_map 등재)인지 구분도
      호출 측 몫(nikl_dict.is_verb_inflection_homograph가 담당).
    kiwi 미가용·미발견 시 None (graceful — 가드 미적용).
    """
    kiwi = _get_kiwi()
    if kiwi is None or not text or not eojeol:
        return None
    try:
        pat = re.compile(r"(?<![가-힣])" + re.escape(eojeol) + r"(?![가-힣])")
    except re.error:
        return None
    n = 0
    for m in pat.finditer(text):
        if n >= max_occurrences:
            break
        n += 1
        lo = max(0, m.start() - 48)
        window = text[lo:min(len(text), m.end() + 48)]
        pos = m.start() - lo
        try:
            tokens = kiwi.analyze(window)[0][0]
        except Exception:
            continue
        span = [t for t in tokens if pos <= t.start < pos + len(eojeol)]
        if not span:
            continue
        first = span[0].tag.split("-")[0]
        last = span[-1].tag.split("-")[0]
        if first in _VERBAL and last in _INFL_ENDINGS:
            return span[0].form + "다"
    return None


# 체언 계열 태그(조사 앞에 올 수 있는 것) — noun_josa_split·nominal_boundaries 공용.
#   XSN(파생접미사 '-생·-님')·XPN(접두사)까지 포함해야 '과생이'=과/NNG+생/XSN+이/JKS 같은
#   분해도 '체언+조사'로 읽힌다. 용언(V*)·어미(E*)·계사(VCP/VCN)·파생용언(XSV/XSA)은 제외.
_NOMINAL = ("NNG", "NNP", "NNB", "NP", "NR", "XSN", "XPN")


def noun_josa_split(text: str, eojeol: str, max_occurrences: int = 4):
    """eojeol이 문서 문맥에서 '체언 + 조사'로 읽히면 그 **체언 표면형**을 반환.

    regularization(norm_map/어문 페어)류 '어절 표면 매칭' 교정의 **체언+조사 동형이의
    가드**용. verb_inflection_lemma(용언 활용형)와 같은 사각을 명사 쪽에서 막는다:
    '말이'(말+주격조사)가 방언 표제어 '말이→말니'에 오매칭돼 "하는 말이"를 "하는 말니"로
    바꾸는 부류(2026-08-03 사용자 보고). norm_map 전수 스캔 382건이 이 형상이고,
    그 중 173건은 base가 등재 표제어라 현대문에서 그대로 지뢰다(앞이→앞니, 바람이→
    바람니, 신경이→싱경이, 보통이→보퉁이, 손과→쑨커, 나로→나루 …).

    판정 — 통어절 등장( (?<![가-힣])eojeol(?![가-힣]) ) 표본 최대 max_occurrences개 중
    **하나라도** 체언+조사로 읽히면 그 체언을 반환(하나라도 그렇게 읽히면 일괄 치환이
    그 등장을 훼손하므로 보수적으로 전체 보류가 옳다 — verb_inflection_lemma와 동일 철학):
      · 등장 주변 ±48자 창을 kiwi로 분석해 스팬이 [체언(_NOMINAL) 런 + 조사(J*) 런]이면 해당.
      · ⚠ 문맥이 판별의 핵심이다. 낱말로 굳은 표기는 kiwi가 통째 한 형태소로 읽어
        가드가 발동하지 않는다(실측: '10키로 떨어진'의 '키로'=NNB 단일 → 킬로 교정 보존,
        '탱이 한 마리'의 '탱이'=NNG 단일 → 매기 교정 보존). 반면 '하는 말이,'는
        말/NNG+이/JKS, '차란 무엇인가'는 차/NNG+란/JX로 갈려 보류된다.
      · 불규칙 결합('과잘'=과자/NNG+ᆯ/JKO)은 표면 경계가 어긋나므로 판정 보류(카드 유지).

    ⚠ 조사 딸린 표면('찌게를')은 명사 사용 신호이므로 **호출 측이 가드 대상에서 제외**할 것.
    ⚠ 반환된 체언이 실제 등재 표제어인지 확인도 호출 측 몫
      (nikl_dict.is_noun_josa_homograph가 담당 — 체언이 헛것이면 방언 표제어 쪽이 옳다).
    kiwi 미가용·미발견 시 None (graceful — 가드 미적용).
    """
    kiwi = _get_kiwi()
    if kiwi is None or not text or not eojeol:
        return None
    try:
        pat = re.compile(r"(?<![가-힣])" + re.escape(eojeol) + r"(?![가-힣])")
    except re.error:
        return None
    n = 0
    for m in pat.finditer(text):
        if n >= max_occurrences:
            break
        n += 1
        lo = max(0, m.start() - 48)
        window = text[lo:min(len(text), m.end() + 48)]
        pos = m.start() - lo
        try:
            tokens = kiwi.analyze(window)[0][0]
        except Exception:
            continue
        span = [t for t in tokens if pos <= t.start < pos + len(eojeol)]
        if len(span) < 2:
            continue
        j = len(span)                       # 끝에서부터 조사 런을 떼어 낸다
        while j > 0 and span[j - 1].tag.startswith("J"):
            j -= 1
        if j == 0 or j == len(span):        # 조사가 없거나 전부 조사 → 해당 없음
            continue
        if any(t.tag.split("-")[0] not in _NOMINAL for t in span[:j]):
            continue
        prev, first_j = span[j - 1], span[j]
        if first_j.start != prev.start + prev.len:   # 불규칙 결합 → 보류
            continue
        base = eojeol[:first_j.start - pos]
        if base and len(base) < len(eojeol):
            return base
    return None


def nominal_boundaries(run: str):
    """`run`(한글 런)이 **체언 연쇄(+조사)** 로만 분석되면 형태소 경계 오프셋 집합을 반환.

    용언·어미·계사·파생용언이 하나라도 섞이면 None — 그 런 안의 **부분 치환은 안전하지
    않다**는 뜻이다. 검수 등장 필터(review_panel._mark_stem_boundary_skips 대상 ⑤)가
    규범표기 카드의 등장을 걸러내는 데 쓴다: '말이' 교정이 '말이에요'(말/NNG+이/VCP+
    에요/EF) 속에서 발동해 '말니에요'를 만드는 것은 막고, '컨텐츠'(→콘텐츠) 교정이
    '컨텐츠산업'·'컨텐츠를' 같은 **체언 연쇄** 안에서 발동하는 것은 살린다.

    경계 집합에는 0과 len(run)도 포함한다. 등장의 양끝이 모두 경계여야 같은 낱말이다
    ('말이야기'=말/NNG+이야기/NNG는 체언 연쇄지만 '말이'의 끝이 경계가 아니라 제외).
    kiwi 미가용·분석 실패 시 None(=보수적 제외).
    """
    kiwi = _get_kiwi()
    if kiwi is None or not run:
        return None
    try:
        tokens = kiwi.analyze(run)[0][0]
    except Exception:
        return None
    if not tokens:
        return None
    bounds = {0, len(run)}
    for t in tokens:
        tag = t.tag.split("-")[0]
        if tag not in _NOMINAL and not tag.startswith("J"):
            return None
        bounds.add(t.start)
        bounds.add(t.start + t.len)
    return bounds


# `single_char_tail_ok=False`에서도 **정상으로 인정하는 1글자 꼬리** — 행정·기술 문서에서
#   자유롭게 새 낱말을 만드는 '라벨 접미사'다(서비스명·분포표·결합키·보안팀·접속일·카드망).
#   이 목록이 없으면 그런 정상 복합어가 모두 검수 카드가 된다(실측 3문서 순증 9/2/5건 중
#   대부분이 이 부류였다). ⚠ 이 규칙은 **낱말 전체가 미등재일 때만** 발동하므로, 등재어
#   ('도서관'·'박물관')는 애초에 이 경로에 오지 않는다 — 그래서 '관'은 넣지 않는다.
#   목록을 늘릴 때는 반드시 실문서 순증을 재측정할 것(늘리면 미탐이 늘어난다).
_LABEL_SUFFIX_1 = frozenset(
    "명표값키팀망일별용형율률급서처법점량회차료액비세층권호식")


def is_known_form(word: str, exists_fn, *, single_char_tail_ok: bool = True) -> bool:
    """word가 '사전 등재어들의 활용/복합 형태'인가? (탐지용 — 관대)

    원칙: **미등재 '내용 명사(NNG/NNP)·어근(XR)·용언 어간(VV/VA/VX)' 이 있을 때만**
    오타/미등재로 본다. 순수 문법 형태소만으로 이뤄진 표면형은 정상으로 인정한다.
      · 지정사(VCN 아니다 / VCP 이다), 관형사(MM), 부사(MAG/MAJ),
        의존명사(NNB), 파생접미사(XSV/XSA/XSN), 어미(E*)·조사(J*) → 사전 조회 불필요.
      · 하-파생 용언('뜻하다'의 '하'=XSV)·지정사 활용('아니라')·관형사('따른') 등은
        내용 명사가 없으므로 True(정상). 형태소 분석이 이를 정확히 구분한다.
      · 미등재 단일 명사('상담채녈'·'애니메니션'·'케릭터')는 NNG가 사전에 없어 False.

    길이 2+ 내용 형태소만 사전 조회한다(1글자 한자어 '권'·'일' 등은 제외).
    내용 형태소가 하나도 없으면(순수 문법형) True. 분석 실패/미설치 시 False.

    exists_fn(base) -> bool : 사전 등재 여부 콜백 (nikl_dict.lookup_word 등)

    ★`single_char_tail_ok=False`면 **끝이 1글자 NNG/NNP인 분해는 정상으로 인정하지
    않는다**(False). 위 '1글자는 조회 제외' 규칙의 사각을 막기 위한 것 — 표제어에 없는
    낱말이 '등재어 + 1글자 명사'로 쪼개지면 그 1글자가 검사에서 빠져 **무조건 정상**이
    돼 버린다. 실측(사용자 보고 2026-07-30): 오타 `재산관`('재산과'의 오타)이
    `재산/NNG + 관/NNG`으로 분해돼 미등재인데도 의심어에서 빠졌다. 미등재 낱말을
    걸러내는 **스크리닝 게이트에서만** 쓸 것 — 가드·재검증 경로는 관대해야 한다
    (거기서 엄격해지면 정상 복합어를 저신뢰로 강등한다).
    """
    kiwi = _get_kiwi()
    if kiwi is None or not word:
        return False
    try:
        tokens = kiwi.analyze(word)[0][0]
    except Exception:
        return False
    if not tokens:
        return False
    if not single_char_tail_ok:
        # ⚠ **명사 복합어 모양일 때만** 발동한다(앞 형태소가 전부 NNG/NNP/XR).
        #   낱말을 문맥 없이 분석하면 kiwi가 **용언 활용형을 관형사+명사로 오분석**한다
        #   — 실측 '세울'(세우다+ㄹ, "계획을 세울 여유")→`세/MM + 울/NNG`,
        #   '이룰'→`이/MM + 룰/NNG`. 이 제한이 없으면 정상 활용형이 오탈자 카드가 된다
        #   (이 저장소가 이미 고친 부류 — '세울·없는지·국인' 3종 회귀).
        last = tokens[-1]
        if (last.tag in ("NNG", "NNP") and len(last.form) == 1
                and len(tokens) >= 2 and last.form not in _LABEL_SUFFIX_1
                and all(t.tag in ("NNG", "NNP", "XR") for t in tokens[:-1])):
            return False
    for t in tokens:
        tag = t.tag
        if tag in ("NNG", "NNP") or tag == "XR":
            if len(t.form) >= 2 and not exists_fn(t.form):
                return False          # 미등재 내용 명사/어근 → 오타 의심
        elif tag.startswith(_VERBAL):
            base = t.form + "다"
            if len(base) >= 2 and not exists_fn(base):
                return False          # 미등재 용언 어간 → 오타 의심
    return True


def has_known_inflection(word: str, exists_fn) -> bool:
    """word가 '사전 등재 용언(동사·형용사)의 활용형'인가? (가드용 — 보수적)

    용언 기본형(겪다)이 사전에 있으면 True. 체언 복합어(키메세지)는 대상이
    아니므로(명사만으로 구성) False → 일관성 부분매칭 전파를 막지 않는다.
    """
    for base, kind in analyze_bases(word):
        if kind == "V" and len(base) >= 2 and exists_fn(base):
            return True
    return False


# ── 한국인 성씨(인명 휴리스틱용) ────────────────────────────────────────────
# 미등재 단일 토큰은 형태소만으론 '오타'와 '인명'을 못 가른다(예: '임세환'·'상담채녈'
#   둘 다 단일 NNG). 그래서 **성+이름 패턴**으로 인명을 가려낸다: 1글자 성씨(상위 ~100개,
#   인구의 99%+) 또는 2글자 복성으로 시작하는 짧은(2~4글자) 순한글 토큰. 단, 등재어가
#   접두로 들어 있으면(상담채녈=상담+…) 미등재 복합어/오타로 보고 인명에서 제외한다.
_SURNAMES = frozenset(
    "김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 전 홍 유 고 문 양 손 배 "
    "백 허 남 심 노 하 곽 성 차 주 우 구 민 류 나 진 지 엄 채 원 천 방 공 현 함 변 "
    "염 여 추 도 소 석 선 설 마 길 연 위 표 명 기 반 라 왕 금 옥 육 인 맹 제 모 탁 "
    "국 어 은 편 용 예 봉 경 사 부 가 복 태 빈 선 순 승 강 동".split()
)
_COMPOUND_SURNAMES = frozenset({"남궁", "황보", "제갈", "선우", "독고", "동방", "사공", "서문", "망절"})

import re as _re_name


def looks_like_korean_name(word: str) -> bool:
    """word가 '한국인 성명(성+이름)으로 보이는가'? (인명 오플래그 억제용 — 보수적).

    안전망/검수 카드가 저자명·인명을 '오탈자 가능성'으로 띄우면 교정 도구의 신뢰를
    해친다(사용자 피드백). kiwi는 미등재 인명을 NNP로 못 잡고 단일 NNG로 뭉뚱그려
    형태소로는 인명과 오타를 못 가른다(임세환·상담채녈 둘 다 단일 NNG). 그래서
    **성씨+짧은 길이** 표기 패턴으로 인명을 가린다(한국 NLP의 표준 휴리스틱):

      · 순한글 2~3글자 + 1글자 성씨 시작 → 인명 (임세환·김민).
      · 순한글 3~4글자 + 2글자 복성 시작 → 인명 (남궁민수).

    길이 상한이 핵심 가드다 — 미등재 '복합어 오타'는 대개 4글자 이상이고 성씨로
    시작하지 않는다('상담채녈'=4·상 비성씨, '애니메니션'=5, '케릭터'=케 비성씨)
    → 자연히 제외된다. 사전 조회는 하지 않는다: 2글자 표제어가 워낙 많아('임세'·
    '세환'·'남궁'이 전부 등재) 접두 등재 검사가 오히려 진짜 인명을 깨뜨린다(실측).

    ⚠ 한계(수용): 성씨로 시작하는 3글자 미등재 오타(예 '신쳥'=신청 오타)도 인명으로
    보고 안전망에서 뺄 수 있다. 그러나 (a) 카드만 안 띄울 뿐 자동수정과 무관하고,
    (b) 그런 오타는 AI가 독립적으로 검토하며, (c) 인명 오플래그의 신뢰 훼손이 더 크다.
    """
    w = _re_name.sub(r"[^가-힣]", "", word or "")
    n = len(w)
    if n < 2 or n > 4:
        return False
    if w[:2] in _COMPOUND_SURNAMES and n in (3, 4):
        given = w[2:]
    elif w[0] in _SURNAMES and n in (2, 3):
        given = w[1:]
    else:
        return False
    return bool(given)


def looks_like_typo(word: str, exists_fn) -> bool:
    """미등재어 word가 '오타로 보이는가'? (사전 안전망 필터용 — 보수적).

    안전망은 AI가 안 고친 미등재어를 검수 카드로 띄우는데, 그중 대부분은
    고유명사·외래어·정상 복합어(오탐)다. 진짜 오타만 남기기 위한 휴리스틱:

      · **한국인 성명 패턴**(성+이름)이면 오타 아님(False) — 저자명·인명 탈락
        (예: '임세환'·'남궁민수'). kiwi가 인명을 NNP로 못 잡고 NNG로 뭉뚱그리는
        구조적 빈틈을 표기 패턴으로 메운다.
      · 주성분에 고유명사(NNP)가 있으면 오타 아님(False) — 인명·지명 탈락.
      · 길이 2+ 기본형이 **2개 이상 사전에 있으면** 정상 복합어로 보고 False
        — 예: '지역사회보장협의체'(여러 등재 명사의 결합).
      · 그 외(미등재 단일 토큰 등)는 오타 가능성 → True
        — 예: '상담채녈'·'케릭터'·'애니메니션'은 분석 안 되는 단일 미등재어.

    형태소 미설치/분석 실패 시 True(필터 무력화 = 안전 측: 노출 유지). 단, 인명
    패턴 검사는 형태소와 무관(표기+사전)하므로 kiwi 없이도 동작한다.
    exists_fn(base) -> bool : 사전 등재 여부 콜백.
    """
    if looks_like_korean_name(word):
        return False
    kiwi = _get_kiwi()
    if kiwi is None or not word:
        return True
    try:
        tokens = kiwi.analyze(word)[0][0]
    except Exception:
        return True
    content = [t for t in tokens
               if t.tag.startswith(_VERBAL) or t.tag in _NOMINAL or t.tag == "XR"]
    if any(t.tag == "NNP" for t in content):
        return False
    known = 0
    for t in content:
        base = (t.form + "다") if t.tag.startswith(_VERBAL) else t.form
        if len(base) >= 2 and exists_fn(base):
            known += 1
    return known < 2


# 관형어(다른 품사) 뒤에 와서 띄어 써야 하는 명사 태그.
#   kiwi는 '때·데·뿐' 등 일부 의존명사를 NNG로, '수·것·개'는 NNB로 태깅하므로 둘 다 받는다.
_DEP_NOUN = ("NNB", "NNG", "NNP")
# 명사를 '띄어 써야 하는' 앞 관형어 태그:
#   ETM = 관형형 전성어미(-ㄴ/-ㄹ/-는/-던), MM = 관형사(각·전·본·새·이), NR = 수사(다섯).
# (아라비아 숫자 SN 뒤 단위는 붙여쓰기 허용 → 제외. 명사 뒤 명사(복합명사)·고유명사
#  오분석은 앞이 관형어가 아니므로 자연히 제외 — 예 '녹번'의 '번', '정책보고서'.)
_ADNOMINAL = ("ETM", "MM", "NR")
# 의존명사 '뒤'에서 새 단어를 시작하는(띄어 쓸) 품사 — '갈 수 있다'의 '있다' 등.
_AFTER_NNB_STEM = ("VV", "VA", "VX", "NNG", "NNP", "MAG")

# 고유어 수관형사(단위 나열 '한 개·두 개·세 명'에 쓰임) — 사전-명사 가드에서 제외한다.
#   '두개(頭蓋)'·'한개'(부사)처럼 동형 표제어가 있어도 정상 단위 띄어쓰기('두 개')를 막지 않도록.
_NATIVE_CARDINAL = frozenset({"한", "두", "세", "서", "석", "네", "너", "넉", "닷", "댓", "엿"})


def _is_noun_headword(word: str) -> bool:
    """`word`가 사전에 '명사/대명사' 표제어로 정확 등재됐는가(nikl_dict, graceful)."""
    try:
        import nikl_dict
        return nikl_dict.is_exact_noun_headword(word)
    except Exception:
        return False


def _is_headword(word: str) -> bool:
    """`word`가 **품사 불문** 정확 등재 표제어인가(nikl_dict, graceful).

    _is_noun_headword와 나눠 쓰는 이유: 우리말샘 전문용어 행은 pos가 비어 있어
    ('한부모가족'·'한부모가정' 실측 pos='') 명사 필터를 통과하지 못한다. 복합어
    **경계 판정**엔 품사보다 '한 낱말로 등재됐다'는 사실이 중요하므로 여기선 품사를 묻지
    않는다. 반대로 _is_noun_headword는 '한개'(부사) 같은 비명사를 걸러야 해서 필터가 필요.
    """
    try:
        import nikl_dict
        return nikl_dict._is_exact_headword(word)
    except Exception:
        return False

# ── 체언 뒤에서도 반드시 띄어 쓰는 '엄선된' 의존명사 ─────────────────────────
# ⚠ 2026-06-22 정책은 노이즈를 막으려 '관형어+명사'만 띄웠다(find_spacing_suggestions).
#   그래서 '명사+의존명사'(리플릿등·9월말)는 구조적으로 놓쳤다. 이를 **과교정 없이** 메우려고
#   '체언 뒤에서 거의 항상 띄는' 의존명사만 화이트리스트로 좁혀 다룬다(한글 맞춤법 제42항).
#   넓은 명사+의존명사 분리는 금지(복합명사 오분리=294건 노이즈 재림).
_ENUM_DEP = frozenset({"등", "등등", "따위"})           # 열거: '리플릿 등', '자료 등을'
_TIME_PREV = frozenset({"월", "년", "일", "주", "분기", "학기", "세기", "반기"})
_TIME_DEP = frozenset({"말", "초", "중", "경", "초순", "중순", "하순", "말경", "무렵"})  # '9월 말'
# 열거 의존명사 앞에 올 수 있는(=띄어 쓸) 선행 토큰 태그.
_BEFORE_ENUM = ("NNG", "NNP", "NNB", "SL", "SN")


def _eojeol_base(w: str, tokens) -> str:
    """어절 w에서 **끝에 붙은 조사(J*)·기호(S*)만** 떼어낸 표면(사전 조회용 base).

    사전-명사 가드는 반드시 이 **어절 전체** base로 판정해야 한다. 토큰까지의 '앞부분'으로
    보면 '갈수있다'의 앞조각 '갈수'가 등재 명사(渴水)라 정상 교정('갈 수 있다')까지 막힌다
    (2026-07-22 실측 — 골드셋 A+++ 가 잡아냄). '지난주'처럼 **어절 전체가 등재 명사**일 때만
    띄어쓰기 경계가 아니다. tokens는 이미 분석된 결과를 재사용(추가 kiwi 호출 없음).
    """
    end = len(w)
    for tk in reversed(tokens):
        if tk.tag and tk.tag[0] in ("J", "S"):
            end = tk.start
        else:
            break
    return w[:end]


def _insert_spaces(w: str, positions) -> str:
    """w의 지정 char 위치(positions) '앞'에 공백을 삽입한 문자열 반환."""
    pos = set(positions)
    out = []
    for i, ch in enumerate(w):
        if i in pos:
            out.append(" ")
        out.append(ch)
    return "".join(out)


# 컷을 가로지르는 등재 복합어를 찾을 때 훑는 최대 길이(DB 조회 상한).
_MAX_COMPOUND = 8


def _longer_headword_over_cut(w: str, tokens, pv, t):
    """컷(t.start)을 가로지르며 **prev+t보다 더 긴** 등재 표제어가 어절 안에 있는가.

    kiwi가 **미등재 복합어의 첫 음절을 관형사로 오분석**했을 때만 참이 된다. 실제 사례
    (2026-07-27 사용자 보고): '한부모가족지원'을 한(MM)+부모(NNG)+…로 봐 '한 부모가족지원'
    으로 쪼갰다. 어절 전체('한부모가족지원')도 prev+t('한부모')도 미등재라 기존 가드가 전부
    뚫렸지만, 사전엔 '한부모가족'·'한부모가정'이 있다 — 컷(1)을 지나 명사 t의 끝(3)을
    **넘어서** 덮는 등재어가 있다는 건 그 경계가 낱말 내부라는 뜻이다.

    ⚠ **반드시 run(=prev+t)보다 긴 후보만** 본다(`t_end + 1`부터 시작). 길이가 같은 후보까지
    받으면 '갈수있다'의 '갈수'(渴水)·'한개'(부사)·'두개'(頭蓋)에 걸려 정상 교정('갈 수 있다'·
    '한 개'·'두 개')을 통째로 막는다(골드셋 A+++가 잡는 함정). 같은 길이 판정은 위쪽
    _is_noun_headword 가드가 품사까지 보며 담당한다.
    ⚠ 끝에 붙은 조사·기호는 후보에서 제외(_eojeol_base) — '한개는'의 '개는'처럼 조사가
    우연히 낱말을 이루는 오매칭을 막는다.
    """
    t_end = t.start + len(t.form)
    base_end = len(_eojeol_base(w, tokens))
    run_end = t_end
    while run_end < len(w) and re.match(r"[가-힣]", w[run_end]):
        run_end += 1
    limit = min(run_end, base_end, pv.start + _MAX_COMPOUND)
    for end in range(t_end + 1, limit + 1):
        cand = w[pv.start:end]
        if _is_headword(cand):
            return cand
    return None


# 어미 '-는지/-ㄹ지/-는데'와 **동형인** 1글자 의존명사.
#   kiwi는 어절 단독 분석에서 '없는지'를 없(VA)+는(ETM)+지(NNB)로 본다(문맥을 줘도 동일 —
#   실측). 그대로 두면 '없는 지'로 쪼갠다. 시간 경과의 '지'('떠난 지 3년')와 장소·경우의
#   '데'('간 데 없다')는 **과거 관형형('-ㄴ/-은') 뒤**에만 오므로, 그 외 관형형('-는/-ㄹ/-던')
#   뒤는 어미로 보고 컷하지 않는다. '푸는 데 시간이'류를 놓치지만 억제 방향이라 안전하다.
_EOMI_AMBIG_DEP = frozenset({"지", "데"})
_PAST_ETM = frozenset({"ᆫ", "은"})

# 어절이 '용언 활용형'으로도 읽히는 근소차 대안을 인정할 점수 차 상한.
#   실측: '세울' = 세(MM)+울(NNG) -24.248 vs 세우(VV)+ᆯ(ETM) -24.831 → 차 0.58.
#         '본가구' = 본(MM)+가구(NNG) -24.949 vs 보(VV)+ᆫ(ETM)+가구(NNG) -27.408 → 차 2.46.
_INFLECT_GAP = 1.5


def _reads_as_inflected_verb(w: str) -> bool:
    """어절 전체가 **용언 활용형 하나**로도 읽히는가(근소차 대안 분석).

    `find_spacing_suggestions`는 어절을 **문맥 없이 단독 분석**하므로 관형사 오분석이 난다.
    실측: '세울'(세우다의 활용)을 세(MM)+울(NNG)로 봐 '세 울'로 쪼갰다 — 같은 어절을
    **문장 문맥**에 넣으면 kiwi도 세우(VV)+ᆯ(ETM)로 옳게 분석한다. 문맥 재분석은 비용이
    크므로, 대신 top_n 대안 중 **어간 하나 + 어미들**로만 이뤄지고 그 기본형이 사전에 있는
    분석이 근소차로 존재하면 '용언 활용형일 수 있음'으로 보고 컷을 포기한다(억제 방향).

    '본가구'(보/VV+ᆫ/ETM+**가구/NNG**)·'타가구'(어간 2개)처럼 대안에 체언이 남거나 어간이
    여럿이면 해당 없음 — 정상 교정('본 가구'·'타 가구')은 그대로 살아난다.
    """
    kiwi = _get_kiwi()
    if kiwi is None:
        return False
    try:
        cands = kiwi.analyze(w, top_n=3)
    except Exception:
        return False
    if not cands:
        return False
    best = cands[0][1]
    for toks, score in cands[1:]:
        if best - score > _INFLECT_GAP:
            break                      # 점수순 정렬 — 더 볼 필요 없음
        if not toks:
            continue
        head = toks[0]
        if not head.tag.startswith(_VERBAL):
            continue
        if any(not t.tag.startswith("E") for t in toks[1:]):
            continue                   # 어간1 + 어미들만 (체언이 남으면 활용형 아님)
        if _is_headword(head.form + "다"):
            return True
    return False


def find_spacing_suggestions(text: str, min_len: int = 2) -> list:
    """붙여 쓴 어절에서 **관형어 뒤 의존명사(NNB) 띄어쓰기 누락**만 찾는다.

    ⚠ 2026-06-22 정책(도메인 지침): 실무 교정은 복합명사 띄어쓰기를 대부분 스킵하고
    '명백한 다른 품사 간' 띄어쓰기만 본다. 그 핵심은 **관형어(다른 품사) + 명사** 경계다
    (한글 맞춤법 제42항 의존명사 띄어쓰기 등). 그래서 어절을 형태소 분석해
    **앞에 관형어(ETM 관형형어미 / MM 관형사 / NR 수사)가 온 명사(NNB/NNG/NNP)만** 띄운다
    (kiwi는 '때·데'를 NNG, '수·것·개'를 NNB로 태깅하므로 둘 다 받는다):
      · '갈수있다'→'갈 수 있다', '할때'→'할 때', '한개'→'한 개', '먹을만하다'→'먹을 만하다',
        '전세계'→'전 세계'. 명사+명사 복합어는 앞이 관형어가 아니라 자연히 스킵된다.
    다음은 모두 **건드리지 않는다**(NNB 조건 미충족 → 스킵):
      · 복합명사('정책보고서'·'데이터베이스'), 보조용언('가고싶다'·'읽고있다'),
        기호 섞인 것('지원/사회적'·'이웃애·발견'), 고유명사 오분석('녹번종합…'의 '번'),
        명사 파생('내포함'=내포+하+ㅁ), 숫자+단위('5개')·순서('제3장', SN 뒤).

    글자는 안 바뀌고(환각 0) 공백만 삽입. 탐지 전용 '검수 카드' 후보다(자동수정 아님).
    반환: [(eojeol, spaced), ...] (등장 순, 중복 제거). 미설치/실패 시 [].

    ⚠ **2패스 구조**(2026-07-27): 1패스에서 어절을 분석하며 '컷을 가로지르는 등재 복합어'
    (_longer_headword_over_cut)가 발견된 접두를 문서 단위로 모으고, 2패스에서 그 접두가
    걸린 컷을 전부 취소한다. 사전이 '한부모가족'만 알고 '한부모'는 모르는 상황에서
    '한부모가구'·단독 '한부모'까지 구제하려면 **같은 문서의 다른 어절이 낸 증거**가 필요하다.
    """
    import re as _re
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []

    # ── 1패스: 어절 분석(kiwi 호출은 여기서만) + 문서 스코프 '안전 접두' 수집 ──────
    words = []
    seen = set()
    for w in text.split():
        if len(w) < min_len or w in seen:
            continue
        seen.add(w)
        if not _re.search(r"[가-힣]", w):
            continue
        if is_urlish(w) or has_zero_width(w):        # URL·경로에 공백을 넣으면 링크가 깨진다(is_urlish 주석)
            continue
        try:
            words.append((w, kiwi.analyze(w)[0][0]))
        except Exception:
            continue

    straddle = {}          # (어절, 토큰 인덱스) → 가로지른 등재어 (2패스 재조회 방지)
    safe_prefix = set()    # 문서에서 '낱말 내부'로 판명된 관형어+명사 접두('한부모')
    for w, tokens in words:
        for i, t in enumerate(tokens):
            if i == 0 or t.tag not in _DEP_NOUN:
                continue
            pv = tokens[i - 1]
            if pv.tag not in ("MM", "NR"):
                continue
            hit = _longer_headword_over_cut(w, tokens, pv, t)
            if hit:
                straddle[(w, i)] = hit
                safe_prefix.add(w[pv.start:t.start + len(t.form)])

    # ── 2패스: 컷 결정 ──────────────────────────────────────────────────────────
    out = []
    for w, tokens in words:
        cuts = set()
        for i, t in enumerate(tokens):
            if i == 0 or t.tag not in _DEP_NOUN:
                continue
            if tokens[i - 1].tag not in _ADNOMINAL:
                continue                      # 관형어 뒤가 아니면 띄어쓰기 경계 아님 → 스킵
            # 인명 오분석 가드 — kiwi는 미등재 성명을 '성(1글자 MM)+이름음절(1글자 NNG)'로
            #   잘못 본다('한규진'=한(MM)+규+진). 그 경계를 띄우면 저자명을 '한 규진'으로 쪼갠다
            #   (사용자 보고). 1글자 성씨 MM 뒤 1글자 NNG/NNP는 이름 음절로 보고 띄우지 않는다.
            #   ('전 세계'(세계 2글자)·'한 개'(개=NNB)·잘 알려진 인명(NNP 단일)은 조건 불충족이라 무영향.)
            pv = tokens[i - 1]
            if (pv.tag == "MM" and len(pv.form) == 1 and pv.form in _SURNAMES
                    and t.tag in ("NNG", "NNP") and len(t.form) == 1):
                continue
            if pv.tag in ("MM", "NR"):
                # 사전-명사 가드 — kiwi가 등재 명사를 관형사+의존명사로 오분석한 경우 스킵.
                #   '이중과제'를 이(MM)+중(NNB)+과제로 봐 '이 중 과제'로 쪼개지만 '이중(二重)'은
                #   등재 명사다. prev(MM/NR)+t 표면이 '명사/대명사' 표제어면 경계가 아니다.
                #   ⚠ 고유어 수관형사(한/두/세…) 예외는 **단위성 의존명사(NNB)에만** 적용한다 —
                #   '두개(頭蓋)'에 걸려 '두 개'를 막지 않으려는 예외였는데, 태그를 안 보면
                #   '한'으로 시작하는 **모든 복합어**('한부모…'·'세대원'·'세분류')가 이 가드를
                #   통째로 우회한다(2026-07-27 사용자 보고의 1차 원인).
                #   ⚠ 수 문맥 예외(2026-07-28) — '수사(NR) 뒤 단위성 의존명사(NNB)'이고 그
                #   수사 앞이 또 수(SN/NR)면 등재어 거부권을 쓰지 않는다. '10만명'의 컷 표면
                #   '만명', '3만원'의 '만원'(滿員), '10만개'의 '만개'(滿開), '100만대'(萬代),
                #   '10만년'(萬年), '20만엔'이 전부 **무관한 뜻의 동형 등재어**라 이 가드에
                #   걸려 조용히 스킵됐다(실측). 같은 구조인 '5억원'·'1억명'은 '억원·억명'이
                #   미등재라 발화 — 규정이 아니라 사전 등재 우연이 결과를 갈랐다.
                #   수(數)가 앞에 붙은 NR+NNB는 정의상 수량 표현이라 복합명사 오분석이 아니다.
                #   ⚠ 단, **수사가 뒤로 더 이어지면** 이 예외를 쓰지 않는다. 구어 전사
                #   '십사점구조원까지'(=14.9조 원)는 십/사/점/구/조/원으로 분석돼 '점'에서
                #   자르면 '십사 점구조 원까지'라는 엉뚱한 덩어리가 생긴다(수 전체를 옳게
                #   띄우려면 '십사 점 구 조 원'이라야 하는데 NR끼리는 컷 대상이 아니다).
                #   뒤에 NR이 또 오면 수 표기가 끝나지 않은 것이므로 손대지 않는다.
                _num_unit = (pv.tag == "NR" and t.tag == "NNB"
                             and i >= 2 and tokens[i - 2].tag in ("SN", "NR")
                             and not (i + 1 < len(tokens) and tokens[i + 1].tag == "NR"))
                if not (pv.form in _NATIVE_CARDINAL and t.tag == "NNB") and not _num_unit:
                    run = w[pv.start:t.start + len(t.form)]
                    if len(run) >= 2 and _is_noun_headword(run):
                        continue
                # 복합어 경계 가드 — 컷을 가로지르는 더 긴 등재어(어절 내부 증거),
                #   또는 같은 문서의 다른 어절이 그 접두를 낱말 내부로 판정한 경우.
                if (w, i) in straddle:
                    continue
                if w[pv.start:t.start + len(t.form)] in safe_prefix:
                    continue
            # 사전-명사 가드 ② — 관형형 어미(ETM) 뒤에도 등재 명사가 온다: '지난주'를 kiwi가
            #   지나(VV)+ㄴ(ETM)+주(NNB)로 봐 '지난 주'로 쪼갰다(등재 명사인데도. '지난달'·
            #   '지난해'는 kiwi가 통낱말로 봐 무발화 — 비대칭이 곧 오류의 증거, 2026-07-22 실측).
            #   ⚠ ETM은 어미라 표면이 앞 음절에 섞여(pv.start='난') 위 분기의 run 계산이 무의미하다.
            #   그래서 **어절 전체**(_eojeol_base)로 검사한다 — 앞조각으로 보면 '갈수있다'의
            #   '갈수'(渴水, 등재)에 걸려 정상 교정 '갈 수 있다'까지 막힌다(골드셋 A+++가 잡음).
            #   '갈수있다'·'할때'·'먹을만하다'는 어절 전체가 미등재라 무영향.
            #   고유어 수관형사 예외(_NATIVE_CARDINAL)는 MM/NR 전용이라 여기선 불필요('두 개'는 MM 경로).
            if pv.tag == "ETM":
                base = _eojeol_base(w, tokens)
                if len(base) >= 2 and _is_noun_headword(base):
                    continue
                # 어미 동형 의존명사 가드 — '없는지'(어미 -는지)를 '없는 지'로 쪼개던 문제.
                #   '지·데'가 의존명사인 건 과거 관형형('-ㄴ/-은') 뒤뿐이다(_EOMI_AMBIG_DEP).
                if t.form in _EOMI_AMBIG_DEP and pv.form not in _PAST_ETM:
                    continue
                # 서술격조사 오분석 가드 — '외국인자격상실의심자'의 잘린 조각
                #   '국인자격상실의심자'를 국(NNG)+이(VCP)+ㄴ(ETM)+자격상실…로 봐 쪼갰다.
                #   'X이다'의 활용 앞 체언이 1글자면 낱말 조각일 확률이 높다('학생인 경우'처럼
                #   체언이 2글자 이상인 정상 케이스는 그대로 통과).
                if (i >= 3 and tokens[i - 2].tag == "VCP"
                        and tokens[i - 3].tag.startswith("N")
                        and len(tokens[i - 3].form) == 1):
                    continue
            if 0 < t.start < len(w):
                cuts.add(t.start)             # 관형어 뒤 명사 '앞' 띄움
            # 의존명사(NNB) 뒤에서 새 단어가 이어지면 그것도 띄움('갈 수 있다'의 '있다').
            # NNG/NNP(예 '전 세계')는 뒤 명사를 또 떼면 복합명사를 쪼개므로 적용 안 함.
            if t.tag == "NNB" and i + 1 < len(tokens):
                nt = tokens[i + 1]
                if nt.tag in _AFTER_NNB_STEM and 0 < nt.start < len(w):
                    cuts.add(nt.start)
        if not cuts:
            continue
        # 용언 활용형 가드 — 어절 단독 분석이 만든 관형사 오분석('세울'=세/MM+울/NNG)을
        #   근소차 대안('세우'/VV+'ᆯ'/ETM)으로 되돌린다. 컷이 잡힌 어절에만 재분석(비용 최소).
        if _reads_as_inflected_verb(w):
            continue
        spaced = _insert_spaces(w, cuts)
        if spaced != w and spaced.replace(" ", "") == w:
            out.append((w, spaced))
    return out


def _tail_is_boundary(tokens, i) -> bool:
    """tokens[i] 다음이 어절 끝이거나 기호(S*)·조사(J*)·어미(E*)면 True.

    의존명사 뒤에 **또 다른 명사가 붙지 않을 때만** 띄어 쓴다.
      · '9월말'        → '말' 뒤 끝          → 띄움
      · '9월말~10월'   → '말' 뒤 '~'(SO 기호) → 띄움(부호 경계)
      · '학기말고사'   → '말' 뒤 '고사'(NNG) → **스킵**(복합명사 '학기말+고사')
    """
    if i + 1 >= len(tokens):
        return True
    return tokens[i + 1].tag[0] in ("S", "J", "E")


def find_dependent_noun_spacing(text: str) -> list:
    """체언 뒤에서 거의 항상 띄어 쓰는 **엄선된 의존명사**의 띄어쓰기 누락만 찾는다.

    find_spacing_suggestions('관형어+명사')가 구조적으로 못 잡는 '명사+의존명사'를
    과교정 없이 보완한다. 대상은 두 갈래뿐(화이트리스트 — 한글 맞춤법 제42항):
      · 열거 의존명사 '등/등등/따위' — 앞이 체언일 때: '리플릿등'→'리플릿 등', '자료등을'→'자료 등을'
      · 시간 의존명사 '말/초/중/경…'  — 앞이 월/년/일/주… 일 때: '9월말'→'9월 말', '10월초'→'10월 초'
    의존명사 뒤에 다른 명사가 붙으면(복합명사) 스킵한다(_tail_is_boundary). 글자 불변·공백만
    삽입(환각 0). 탐지 전용 저신뢰 '검수 카드' 후보. 반환 [(eojeol, spaced), …]. 미설치/실패 시 [].
    """
    import re as _re
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for w in text.split():
        if (w in seen or not _re.search(r"[가-힣]", w)
                or is_urlish(w) or has_zero_width(w)):
            continue
        seen.add(w)
        try:
            tokens = kiwi.analyze(w)[0][0]
        except Exception:
            continue
        cuts = set()
        for i in range(1, len(tokens)):
            cur, prev = tokens[i], tokens[i - 1]
            hit = (
                (cur.form in _ENUM_DEP and prev.tag in _BEFORE_ENUM)
                or (cur.form in _TIME_DEP and prev.form in _TIME_PREV)
            )
            if hit and _tail_is_boundary(tokens, i) and 0 < cur.start < len(w):
                # 사전-명사 가드 — 어절 자체가 등재 명사면 의존명사 경계가 아니다:
                #   '차등(差等)'을 kiwi가 차(NNG)+등(NNB)으로 봐 '차 등'으로 쪼갰다(2026-07-22 실측).
                #   find_spacing_suggestions의 [I] 가드와 같은 원리인데 이 finder엔 없어서 샜다.
                #   검사 범위는 **어절 전체**에서 끝 조사만 뗀 base(_eojeol_base) — 앞조각으로
                #   보면 등재 앞말에 걸려 정상 교정이 막힌다(ETM 가드에서 '갈수'로 실측된 함정).
                #   '자료등을'→'자료등'(미등재)이라 정상 발화('리플릿등'·'9월말'도 무영향).
                base = _eojeol_base(w, tokens)
                if len(base) >= 2 and _is_noun_headword(base):
                    continue
                cuts.add(cur.start)
        if not cuts:
            continue
        spaced = _insert_spaces(w, cuts)
        if spaced != w and spaced.replace(" ", "") == w:
            out.append((w, spaced))
    return out


# ── 닫는 기호 뒤 체언 띄어쓰기 ─────────────────────────────────────────────
# 사용자(30년 출판 교정) 규칙: **닫는 기호(' " ) ] 등) 뒤에 명사가 오면 띄어 쓰고,
#   조사가 오면 붙여 쓴다.** kiwi 품사로 체언/조사를 가르면 정확히 구현된다(조사는 J* →
#   애초에 후보가 아니므로 '기호+조사 붙임'은 자동 충족, 별도 제거 불필요).
# 닫는 '괄호'류 — 방향이 명확한 닫음 기호. (따옴표는 _QUOTE_SYM에서 짝 위치로 별도 판정)
_CLOSE_SYM = frozenset(")]}』」】》〉）］｝")
_OPEN_SYM  = frozenset("([{『「【《〈（［｛")
# ⚠ 따옴표(직선·굽은)는 **방향이 모호**하다 — 직선(' ")은 여닫이가 같은 글자이고, 굽은 따옴표도
#   ”…“처럼 뒤집어 쓰는 문서가 있다(사용자 보고: ”준비되면“의 **여는** ”를 '닫는 기호'로 오인해
#   '” 준비되면'으로 띄움). 그래서 **글자 종류로 여닫이를 판정하지 않고**, 토큰 내 따옴표의 **짝 위치**
#   (앞에 나온 따옴표 개수의 홀짝)로 판정해 **닫는 짝(앞 따옴표 수가 홀수)일 때만** 뒤 명사를 띄운다.
#   → 여는 따옴표·홑따옴표(짝 없음)는 자연 제외(직선·굽은·뒤집힌 따옴표 모두 안전).
_QUOTE_SYM = frozenset("'\"‘’“”")
# 기호 뒤 1글자 체언은 **접미사(者·用·別…)와 의존명사(時·等·中…)가 섞여** kiwi가 NNB로
#   뭉뚱그린다('귀농(귀어)자'의 '자'=접미사 → 띄우면 오교정). 그래서 1글자 NNB는 **확실히
#   띄어 쓰는 의존명사 화이트리스트**만 받고, 2글자+ 일반/고유명사(운영·채널·포스터)는 자유롭게 띄운다.
_SPACED_NNB = frozenset({
    "시", "때", "중", "외", "수", "것", "데", "바", "줄", "만큼", "대로", "뿐", "들",
    "등", "등등", "따위", "년", "월", "일", "말", "초", "경", "무렵",
})
# 진짜 '닫는' 따옴표는 **앞말(내용 글자·닫는괄호)에 붙는다** — 여는 따옴표(어절 맨 앞이라 앞이
#   공백/문두)와 이 위치로 구분한다. 짝 없는 아포스트로피('19 연도 약물 등)가 줄 스택을
#   오염시켜 여는 따옴표가 'close'로 오판돼도 이 로컬 조건이 뒤 명사 띄우기를 차단한다.
_CONTENT_CH_RE = re.compile(r"[0-9A-Za-z가-힣㐀-鿿]")


def _spaceable_after_symbol(tok) -> bool:
    """닫는 기호 뒤 토큰이 '띄어 쓸 체언'인가? (접미사·1글자 모호 체언 배제)"""
    if tok.tag in ("NNG", "NNP", "NR", "NP") and len(tok.form) >= 2:
        return True
    if tok.tag == "NNB" and tok.form in _SPACED_NNB:
        return True
    return False


def find_symbol_noun_spacing(text: str) -> list:
    """닫는 기호 바로 뒤에 **명사(체언)** 가 붙어 있으면 띄어쓰기 후보로 만든다.

    '‘표준’규칙'→'‘표준’ 규칙', '(센터)운영'→'(센터) 운영', '[붙임]내용'→'[붙임] 내용'.
    · 기호 뒤가 **조사(J*)** 면 후보가 아니다 → '(센터)에서'는 그대로(붙임 유지).
    · 기호 뒤가 **용언** 이면 후보가 아니다 → '볼까’하는'은 그대로(관형어+명사 띄어쓰기는
      find_spacing_suggestions가 '하는 생각'으로 따로 처리).
    · 괄호 라벨/약어 예외: 닫는 괄호의 짝 안 내용이 1글자면 스킵 — '(주)경성미디어'를
      '(주) 경성미디어'로 끊지 않는다(따옴표는 예외 없음).
    글자 불변·공백만 삽입(환각 0). 탐지 전용 저신뢰 '검수 카드'. 미설치/실패 시 [].
    """
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []
    from core import quote_rules as _qr   # 줄 문맥 따옴표 역할 판정(순수 규칙 — 순환 없음)
    out, seen = [], set()
    for line in text.split("\n"):
        if not any((ch in _CLOSE_SYM or ch in _QUOTE_SYM) for ch in line):
            continue
        qroles = None    # 줄에 따옴표 후보가 실제로 나올 때만 계산(지연)
        for wm in re.finditer(r"\S+", line):
            w = wm.group()
            if is_urlish(w) or has_zero_width(w):     # URL의 ')' 등을 '닫는 기호'로 보고 띄우면 링크가 깨진다
                continue
            if w in seen or not any((ch in _CLOSE_SYM or ch in _QUOTE_SYM) for ch in w):
                continue
            seen.add(w)
            try:
                toks = kiwi.analyze(w)[0][0]
            except Exception:
                continue
            cuts = set()
            for i in range(len(toks) - 1):
                t, nx = toks[i], toks[i + 1]
                is_close = t.form in _CLOSE_SYM
                is_quote = t.form in _QUOTE_SYM
                if not (is_close or is_quote) or not _spaceable_after_symbol(nx):
                    continue
                if nx.start != t.start + len(t.form):   # 이미 공백이 있음 → 스킵
                    continue
                if is_quote:
                    # 따옴표는 방향이 모호 → **줄 문맥 스택**(quote_rules)으로 닫는 짝일 때만
                    #   뒤 명사를 띄운다. ⚠ 과거엔 '토큰 안' 따옴표 홀짝으로 판정해, 여러
                    #   어절짜리 인용구('글로벌 … 커넥트'행사를)의 닫는 따옴표(토큰 안에선
                    #   첫 번째)를 여는 것으로 오인해 미탐했다(사용자 보고 2026-07-03).
                    if qroles is None:
                        qroles = _qr.quote_roles(line)
                    qpos = wm.start() + t.start
                    if qroles.get(qpos) not in ("close", "close_orphan"):
                        continue
                    # ⚠ 여는 따옴표 방어(스택 오염 견고화, 사용자 보고 2026-07-21): 진짜 닫는
                    #   따옴표는 **앞이 내용 글자(또는 닫는괄호)** 다. 앞이 공백/문두/여는괄호면
                    #   어절 맨 앞의 **여는** 따옴표('건강보험료)이므로 뒤 명사를 띄우지 않는다.
                    #   짝 없는 아포스트로피('19 연도)가 줄 스택을 오염시켜 여는 따옴표가 close로
                    #   오판되던 거짓 '기호 뒤 띄어쓰기' 카드를 스택과 무관하게 로컬로 차단한다.
                    pch = line[qpos - 1] if qpos > 0 else ""
                    if not (pch and (_CONTENT_CH_RE.match(pch) or pch in _CLOSE_SYM)):
                        continue
                else:
                    # 닫는 괄호 라벨/약어 예외((주)·(사)·(1) 등 짝 안 내용 1글자)
                    if t.form in ")]}）］｝":
                        depth, oi = 0, None
                        for j in range(i - 1, -1, -1):
                            if toks[j].form in _CLOSE_SYM:
                                depth += 1
                            elif toks[j].form in _OPEN_SYM:
                                if depth == 0:
                                    oi = j
                                    break
                                depth -= 1
                        if oi is not None:
                            content = w[toks[oi].start + len(toks[oi].form):t.start]
                            if len(content.strip()) <= 1:
                                continue
                if 0 < nx.start < len(w):
                    cuts.add(nx.start)
            if not cuts:
                continue
            spaced = _insert_spaces(w, cuts)
            if spaced != w and spaced.replace(" ", "") == w:
                out.append((w, spaced))
    return out


# ── 보조용언 '-어야 하다/되다' 띄어쓰기 ─────────────────────────────────────
# 당위·필요의 '···해야 한다/된다'(고려해야 한다·협력해야 한다)는 한글 맞춤법 제47항상
#   **띄어 씀이 원칙**이다 — 붙여쓰기 허용은 '-아/-어' 연결어미에 한하고 '-어야'는 미허용.
#   AI(Gemini)는 청크별 비결정성으로 같은 패턴을 한 곳은 잡고 다른 곳은 놓쳐(사용자 보고:
#   '협력해야한다'는 교정·'고려해야한다'는 미탐) 일관성이 깨진다 → 결정론 백스톱이 **모든
#   등장**을 일관되게 잡는다. 공백 위치가 kiwi 형태소 경계로 정해지므로(휴리스틱 아님)
#   글자 불변·환각 0. ⚠ 과거 '보조용언 전면 분리'(가고싶다·읽고있다 → 294건 노이즈, 제외)와
#   무관 — **연결어미 '-어야' 뒤 보조용언 '하/되'** 라는 좁고 명확한 당위 패턴만 다룬다.
_AUX_EOMI = frozenset({"어야", "아야", "여야"})   # 당위 연결어미(kiwi 정규화형은 '어야')
_AUX_VERB = frozenset({"하", "되"})              # 보조용언 하다/되다


def find_auxiliary_verb_spacing(text: str) -> list:
    """'···어야' 연결어미 뒤에 붙은 보조용언 '하/되'(···해야한다)의 띄어쓰기 누락을 찾는다.

    어절을 형태소 분석해 **연결어미 EC('어야'류) 바로 뒤의 보조용언 '하'(VX)/'되'(VV·VX)**
    경계에 공백을 삽입한다('고려해야한다'→'고려해야 한다', '공부해야된다'→'공부해야 된다').
    '이야기한다'·'생각한다'(하=XSV 파생, '-어야' 없음)는 매칭되지 않아 안전하다.
    글자 불변·공백만 삽입. 반환 [(eojeol, spaced), …]. 미설치/실패 시 [].
    """
    import re as _re
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for w in text.split():
        if (w in seen or not _re.search(r"[가-힣]", w)
                or is_urlish(w) or has_zero_width(w)):
            continue
        seen.add(w)
        try:
            toks = kiwi.analyze(w)[0][0]
        except Exception:
            continue
        cuts = set()
        for i in range(1, len(toks)):
            cur, prev = toks[i], toks[i - 1]
            if (cur.form in _AUX_VERB and cur.tag in ("VX", "VV")
                    and prev.tag == "EC" and prev.form in _AUX_EOMI
                    and 0 < cur.start < len(w)):
                cuts.add(cur.start)
        if not cuts:
            continue
        spaced = _insert_spaces(w, cuts)
        if spaced != w and spaced.replace(" ", "") == w:
            out.append((w, spaced))
    return out


# ── 본용언 '-아/-어' + 보조용언 띄어쓰기 (보상해 주다 / 고려해 보다 / 조율해 나가다) ──
# 한글 맞춤법 제47항: 보조 용언은 **띄어 씀이 원칙**(붙여 씀도 허용). 바른ai·네이버 검사기는
#   띄어 씀을 권장한다(사용자 보고 13·14·15). 붙여 씀도 맞으므로 **저신뢰 검수 카드**(자동수정
#   아님 — 사람 검토). 글자 불변·공백만 삽입.
# ⚠ **두 가지 함정(실문서 검증으로 확인)**:
#   (1) '-어지다' 피동(작아지다·알려지다·정해지다)의 '지'(VX)는 **붙여 쓴다** → 분리 금지.
#   (2) '예뻐하다'의 '하'(VX)도 붙여 씀 → 분리 금지.
#   그래서 VX 태그만으론 부족하고 **보조용언 표제 화이트리스트**(지·하 제외)로 좁힌다. '-고 싶다/
#   있다'(가고싶다)는 연결어미가 '-고'라 '-아/-어' 조건에서 자연 제외(과거 294건 노이즈와 무관).
_AUX_CONNECTIVE = frozenset({"아", "어", "여"})              # -아/-어 연결어미(kiwi 정규화)
_AUX_BOJO = frozenset({"주", "보", "나가", "내", "두", "놓", "버리", "대", "드리"})  # 띄어 쓰는 보조용언(지·하 제외)


def find_aux_connective_spacing(text: str) -> list:
    """'-아/-어' 뒤에 붙은 보조용언(주/보/나가 등)의 띄어쓰기 누락을 찾는다.

    어절을 형태소 분석해 **연결어미 EC('아/어/여') 바로 뒤의 보조용언(VX, 화이트리스트)** 경계에
    공백을 삽입한다('보상해주는'→'보상해 주는', '고려해볼'→'고려해 볼', '조율해나가는'→'조율해 나가는').
    '-어지다' 피동('작아지고'·'알려져')과 '예뻐하다'의 '하'는 화이트리스트에서 빠져 분리되지 않는다.
    글자 불변·공백만 삽입. 반환 [(eojeol, spaced), …]. 미설치/실패 시 [].
    """
    import re as _re
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for w in text.split():
        if (w in seen or not _re.search(r"[가-힣]", w)
                or is_urlish(w) or has_zero_width(w)):
            continue
        seen.add(w)
        try:
            toks = kiwi.analyze(w)[0][0]
        except Exception:
            continue
        cuts = set()
        for i in range(1, len(toks)):
            cur, prev = toks[i], toks[i - 1]
            if (cur.tag == "VX" and cur.form in _AUX_BOJO
                    and prev.tag.startswith("E") and prev.form in _AUX_CONNECTIVE
                    and 0 < cur.start < len(w)):
                cuts.add(cur.start)
        if not cuts:
            continue
        spaced = _insert_spaces(w, cuts)
        if spaced != w and spaced.replace(" ", "") == w:
            out.append((w, spaced))
    return out


# ── 피동 '-어지다' 붙여쓰기 (이루어 졌다 → 이루어졌다) ─────────────────────────
# '-어지다' 피동(이루어지다·만들어지다·작아지다)은 **한 단어**라 붙여 쓴다(보조용언 띄어쓰기의
#   예외 — find_aux_connective_spacing이 '지'를 화이트리스트에서 제외한 것과 짝). AI(Gemini)는
#   같은 문서에서 '이루어 졌다'는 붙이고 '이루어 졌고'는 놓치는 비결정성을 보였다(사용자 보고).
#   → 결정론 백스톱으로 **모든 등장**을 일관 붙임. JOIN(공백 제거)이라 일반화하면 거짓양성이
#   크지만(통해 물을·한다. 이를 등), 여기선 **'-아/-어' + 피동 '지' 계열** 로 좁히고 kiwi가
#   그 경계를 한 용언으로 보는 경우만 붙인다(실문서 거짓양성 0 검증). 공백만 제거(글자 불변).
_JI_HEAD = "지졌져질진"   # 피동 '지'(VX) 계열로 시작하는 후행 어절(지고/졌고/져서/질/진다)

# ⚠ 후행 어절 안의 **체언** 태그 — 이게 있으면 '지'는 피동 보조용언이 아니다(아래 가드).
_JI_NOUN_TAGS = ("NNG", "NNP", "NNB", "NP", "NR", "SL", "SH", "SN")


def find_eojida_join(text: str) -> list:
    """잘못 띄어 쓴 피동 '-어지다'를 붙인 후보를 [(original, joined), …]로 반환한다.

    인접 두 어절(공백 1칸) A·B가 **A는 '-아/-어/-여'로 끝나고 B는 '지' 계열로 시작**할 때,
    A+B를 형태소 분석해 (a) 용언 형태소가 경계를 가로지르거나(이루어지) (b) 경계에서 연결어미
    '-아/-어'가 끝나고 보조용언 '지'(VX)가 시작하면(작아 지) 한 단어로 보고 공백을 제거한다.
    피동 '지' 계열 + 용언 경계라는 좁은 조건이라 일반 JOIN의 거짓양성(통해 물을·한다. 이를)을
    피한다. 미설치/실패 시 [].
    """
    import re as _re
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []
    out, seen = [], set()
    for line in text.split("\n"):
        toks = list(_re.finditer(r"\S+", line))
        for i in range(len(toks) - 1):
            a, b = toks[i], toks[i + 1]
            if b.start() - a.end() != 1:           # 정확히 공백 1칸으로 분리
                continue
            A, B = a.group(), b.group()
            if (not A or not B or not ("가" <= A[-1] <= "힣")
                    or not ("가" <= B[0] <= "힣")):
                continue
            if has_zero_width(A) or has_zero_width(B):
                continue          # 유령 명사가 용언 경계 판정을 흔든다(has_zero_width 주석)
            if A[-1] not in "아어여" or B[0] not in _JI_HEAD:
                continue
            try:
                mt = kiwi.analyze(A + B)[0][0]
            except Exception:
                continue
            la = len(A)
            straddle = any(t.start < la < t.end and t.tag.startswith("V") for t in mt)
            ec = any(t.tag.startswith("E") and t.form in ("아", "어", "여")
                     and t.start < la <= t.end for t in mt)
            vx = any(t.tag == "VX" and t.form == "지" and t.start <= la < t.end for t in mt)
            if not (straddle or (ec and vx)):
                continue
            # ⚠⚠ **후행 어절에 체언이 있으면 붙이지 않는다**(2026-08-05 사용자 보고).
            #   kiwi는 붙인 문자열만 받으면 멀쩡한 '명사'를 피동으로 읽어 버린다 —
            #   '고려하여 질적,' → '고려하여질적,'을 분석하면 지/VX + ᆯ/ETM + **적/NNB**
            #   가 나와 위 (ec and vx) 분기를 그대로 통과했다(실파일: 中國科學院 보고서,
            #   `high`라 **자동 적용**됐고 사유는 '피동 -어지다'였다). 띄어 쓴 원형은
            #   질/NNG + 적/XSN 으로 정상 분석되지만, 이 규칙은 붙인 형태만 보므로
            #   그 사실을 알지 못한다. ⚠ kiwi 점수 비교로는 못 가른다 — 실측상 kiwi가
            #   내부적으로 자동 띄어쓰기를 해 두 형태의 점수가 **완전히 동일**하다
            #   (작아 졌다·만들어 지고도 delta 0). 갈라지는 지점은 하나뿐이다:
            #   진짜 피동 '-어지다'의 후행 어절은 **전부 용언 형태소**(지/VV·었/EP·다/EF)
            #   이고, 오탐은 그 안에 **체언**이 들어 있다(적/NNB·지원/NNG·지역/NNG).
            #   → 경계 이후 토큰에 체언 태그가 하나라도 있으면 스킵.
            #   수용한 미탐: '만들어 진것'처럼 후행 어절에 의존명사가 붙은 경우(억제 방향).
            if any(t.start >= la and t.tag in _JI_NOUN_TAGS for t in mt):
                continue
            orig = A + " " + B
            if orig in seen:
                continue
            seen.add(orig)
            out.append((orig, A + B))
    return out


def find_compound_spacing_consistency(text: str, *, min_len: int = 4, max_len: int = 14,
                                      exists_fn=None) -> list:
    """복합명사의 띄어쓰기 혼재를 **저자 다수 표기로 통일**하는 후보를 만든다.

    실무 교정: 복합명사 띄어쓰기는 옳고 그름을 따지기보다, 한 문서 안에서 저자가 더
    자주 쓴 형태로 통일한다. 예) '정책보고서'(다수)와 '정책 보고서'(소수) 혼재 →
    '정책 보고서' → '정책보고서'.

    방법(문서 주도·안전):
      · 붙여 쓴 어절(조사 제거 base, len>=min_len, **순수 명사**: 분석 결과가 NNG/NNP
        로만 구성)을 후보로 모아 붙여 쓴 등장 수(n_joined)를 센다.
      · base를 모든 내부 위치에서 둘로 쪼개 보며, **문서에 실제로 등장하는 띄어쓴 형태**
        (예: '데이터 베이스')를 정규식으로 찾아 n_spaced를 얻는다(kiwi가 안 쪼개는
        복합어도 문서 실제 표기로 잡힌다). 가장 많이 등장한 분리 지점을 채택.
      · 두 형태가 모두 등장하고(혼재) 한쪽이 **엄격히 우세**하면 소수→다수 후보로 반환.
      · **동률**(1:1 등)은 다수결이 불가하다 — `exists_fn`(사전 존재 검사)이 주어지면
        **규범 기본 방향**으로 후보를 낸다: 붙임형이 사전 등재 복합어면 붙임으로, 아니면
        띄어쓰기 원칙(한글 맞춤법 제2항 — 단어별 띄어 씀)으로. exists_fn=None이면 기존대로
        제외(무회귀). 호출부는 동률(n_min==n_maj)을 반드시 저신뢰 검수 카드로 만들 것
        (사용자 정책 결정 2026-07-02: '읍면동담당' 1:1 혼재도 검수 카드로 표출).

    반환: [(minority, majority, n_minority, n_majority), ...]. 미설치/실패 시 [].
    교정은 호출 측(워커)이 '검수 필요'(저신뢰) 카드로 만든다 — 자동수정 아님.
    """
    import re as _re
    from collections import Counter
    kiwi = _get_kiwi()
    if kiwi is None or not text:
        return []

    # 1) 붙여 쓴 **한글 런**의 조사 제거 base 등장 수(유니크 런만 분석).
    #    ⚠ 공백 split 토큰으로 세면 가운뎃점·괄호·쉼표가 낱말을 이어붙여
    #    ('보조금·핵심인재'→키 '보조금핵심인재', '귀국유치(천인계획'→'귀국유치천인계획')
    #    붙임형 등장이 다른 키로 새고 **과소 카운트**된다(2026-07-15 실측: ·나열이 많은
    #    문서에서 '귀국유치' 토큰 4회 vs 실제 런 8회 — 동률 오판·다수결 방향 왜곡 +
    #    검수 반복 수(런 단위 필터)와 근거 수치 불일치 보고). 사전 스크리닝의 런 단위
    #    검사 원칙과 동일하게 한글 런으로 센다(순수 한글 입력이라 strip_josa도 정확).
    joined = Counter()
    for run, c in Counter(_re.findall(r"[가-힣]+", text)).items():
        base = strip_josa(run) or run
        if min_len <= len(base) <= max_len:
            joined[base] += c

    out, cand = [], []
    for base, n_joined in joined.items():
        try:
            tokens = kiwi.analyze(base)[0][0]
        except Exception:
            continue
        if any(t.tag not in ("NNG", "NNP") for t in tokens):
            continue   # 순수 명사 덩어리만(용언·의존명사·조사 섞이면 제외)
        # 2) 문서에 실제 등장하는 띄어쓴 분리 지점을 탐색(최다 등장 채택).
        best_n, best_sp = 0, None
        for k in range(2, len(base) - 1):
            left, right = base[:k], base[k:]
            if len(right) < 2:
                continue
            #   ⚠ 카운트 정합(2026-07-15 사용자 보고 — 근거 수치와 검수 반복 수·실치환 불일치):
            #   ① 공백은 **단일 스페이스**만 센다 — 카드 원문/브리지 RepeatFind가 'left right'
            #      (한 칸) 리터럴로 탐색·치환하므로, 개행·다중 공백 등장을 세면 보이지도
            #      치환되지도 않는 유령 근거가 된다.
            #   ② **오른쪽도 어절 경계**를 판정한다(왼쪽 (?<![가-힣])와 대칭) — '개선 방안연구'
            #      는 pair('개선','방안연구')이지 ('개선','방안')이 아니다. 뒤 한글 런이 조사
            #      연쇄면 같은 낱말('적정 급여를' 인정), 아니면 다른 낱말로 제외.
            pat = _re.compile(r"(?<![가-힣])" + _re.escape(left) + " " + _re.escape(right))
            n = 0
            for m in pat.finditer(text):
                j = m.end()
                while j < len(text) and "가" <= text[j] <= "힣":
                    j += 1
                run = text[m.end():j]
                if not run or (strip_josa(right + run) or "") == right:
                    n += 1
            if n > best_n:
                best_n, best_sp = n, f"{left} {right}"
        if not best_sp:
            continue
        cand.append((base, best_sp, n_joined, best_n))

    # 3) **가족(머리낱말) 다수** 집계 — 동률 낱말의 방향을 정할 때 규범 기본값보다 먼저 본다.
    #    가족 = 마지막 성분이 같은 복합명사들('수익모델·사업모델·복합모델' → 가족 '모델').
    #    ⚠ 투표권은 **자기 다수가 뚜렷한 낱말에만** 준다(동률 낱말끼리는 서로 못 정한다).
    fam_vote = Counter()
    for base, best_sp, n_j, n_s in cand:
        if n_j == n_s:
            continue
        fam_vote[(best_sp.split(" ")[-1], "joined" if n_j > n_s else "spaced")] += 1

    for base, best_sp, n_joined, n_spaced in cand:
        if n_joined == n_spaced:
            if exists_fn is None:
                continue   # 동률 → 제외(기존 동작 유지 — 호출부가 옵트인)
            # 동률 — 자기 다수결이 불가하다. 계층 **낱말 자신의 다수 > 가족의 다수 >
            #   규범 기본값**으로 방향을 정한다(2026-08-03 사용자 결정 — junction_pass의
            #   '낱말 > 이음매 > 규범' 계층에 가족 한 층을 끼운 것).
            #   ⚠ 왜 필요한가: 예전엔 동률을 **낱말별로** 규범 기본값으로 정해서, 같은
            #   가족의 다른 낱말이 어느 방향이든 무시했다 → 규칙 스스로 계열 충돌을
            #   만들어냈다(실원고 5종 실측: 방향 충돌 가족 57개 중 **16개**가 이 원인).
            #   ⚠ 자기 다수가 있는 낱말은 절대 건드리지 않는다 — '산학협력'(51:10 붙임,
            #   산학협력법 용어)이 '국제 협력'(3:1 띄움)에 끌려가 깨지는 일은 없다.
            head = best_sp.split(" ")[-1]
            n_fj, n_fs = fam_vote[(head, "joined")], fam_vote[(head, "spaced")]
            if n_fj != n_fs:
                joined_wins = n_fj > n_fs                       # 가족의 다수
            else:
                try:
                    joined_wins = bool(exists_fn(base))          # 규범 기본값
                except Exception:
                    continue
            if joined_wins:
                out.append((best_sp, base, n_spaced, n_joined))   # 붙임 방향
            else:
                out.append((base, best_sp, n_joined, n_spaced))   # 띄어쓰기 방향
        elif n_joined > n_spaced:
            out.append((best_sp, base, n_spaced, n_joined))   # 소수(띄어쓴)→다수(붙인)
        else:
            out.append((base, best_sp, n_joined, n_spaced))   # 소수(붙인)→다수(띄어쓴)
    return out
