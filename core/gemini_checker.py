"""
core/gemini_checker.py — Gemini AI 교정교열 엔진
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
오탈자 보완 / 띄어쓰기 / 윤문 모드 지원.
"""

import json
import re
import threading
import time

from google import genai
from google.genai import types as genai_types

from .models import (
    Correction,
    HL_TYPO,
    HL_POLISH,
    AI_CHUNK_TYPO,
    AI_CHUNK_POLISH,
    AI_CALL_DELAY,
    AI_REQUEST_TIMEOUT,
    AI_MAX_OUT_TYPO,
    AI_MAX_OUT_POLISH,
)
from .prompts import (
    SYSTEM_INSTRUCTION,
    build_polish_prompt,
    build_integrated_prompt,
)


# S7: 모델 ID 우선순위 — 첫 번째가 실패(deprecated/unavailable)하면 다음으로 폴백
# 모델 후보 체인 — 앞에서부터 시도한다. 순서 기준은 **무료 티어 한도**(AI Studio 콘솔 실측,
#   2026-07-22)다. 교정은 문서 한 건에 청크 수십 개를 던지므로 RPD(일일 요청)가 병목이다.
#     gemini-3.5-flash-lite : 15 RPM / 250K TPM / 500 RPD  ← 기본
#     gemini-3.1-flash-lite : 15 RPM / 250K TPM / 500 RPD  ← 백업(한도 소진 시)
#     gemini-2.5-flash-lite : 10 RPM / 250K TPM /  20 RPD  ← 비상용(RPD가 작아 곧 소진)
#     gemini-3.5-flash      :  5 RPM / 250K TPM /  20 RPD  ← 최후
#   ⚠ AI_CALL_DELAY(4.1s)는 15 RPM 기준이다. 앞 두 모델이 15 RPM이라 그대로 유효하다.
#   ⚠ gemini-2.0-flash는 콘솔에서 0/0(할당 없음)으로 확인돼 체인에서 제외했다.
_MODEL_CANDIDATES = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
)

# 한도 소진(429/RESOURCE_EXHAUSTED) 판별 — _TRANSIENT_KEYWORDS보다 **좁다**.
#   5xx·타임아웃까지 '한도'로 오인하면 멀쩡한 모델을 버리게 되므로 분리해 둔다.
_QUOTA_KEYWORDS = (
    "429", "resource_exhausted", "quota", "exceeded your current quota",
    "rate limit", "ratelimit",
)


def _is_quota_error(msg: str) -> bool:
    return any(k in msg for k in _QUOTA_KEYWORDS)

# S9: 일시 오류(429/5xx/타임아웃/네트워크) 재시도 백오프(초). 무료 티어 15 RPM의
#   분당 할당량 회복을 고려해 두 번째 대기를 길게 둔다. 총 시도 = 1 + len(딜레이).
_RETRY_DELAYS = (5.0, 15.0)

# 재시도 대상 '일시 오류' 판별 키워드 (예외 메시지 소문자 대조).
#   모델 부재/권한(폴백 대상)과 구분 — 그쪽은 _call_and_parse의 모델 전환이 처리.
_TRANSIENT_KEYWORDS = (
    "429", "rate", "quota", "exhausted", "resource_exhausted",
    "timeout", "timed out", "deadline",
    "500", "502", "503", "unavailable", "overloaded", "internal",
    "connection", "network", "temporarily",
)


def _is_transient_error(msg: str) -> bool:
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


# ⚠ response_schema(구조화 출력)는 **재현성을 깨서 제거**(2026-06-25). Gemini의
#   controlled generation(스키마 강제 디코딩)은 temperature=0·seed를 줘도 호출마다 결과가
#   달라진다(실측: 동일 test.hwp 3회 → 72/75/68건). 교정교열은 정확성·일관성이 최우선이라
#   비결정성은 치명적. JSON 견고성은 response_mime_type=application/json + _parse_json_response의
#   _repair_json/_salvage_json_objects(백스톱)로 충분히 확보되므로 스키마 없이도 파싱오류는 안 난다.
#   재도입 금지(결정론을 보장하는 구조화 출력 옵션이 검증되기 전엔).


def _repair_json(s: str) -> str:
    """LLM JSON의 흔한 깨짐을 '유효 JSON은 안 건드리는' 안전한 보정으로 고친다.

    - 후행 콤마(,] ,}) 제거
    - 객체 사이 누락 콤마 (}{ → },{)
    - 문자열 값과 다음 키 사이 누락 콤마 ("…값"\\n"키" → "…값",\\n"키")
      정상 JSON은 값 뒤에 콤마가 있어 '"' 바로 뒤가 ','라 이 패턴에 안 걸린다 → 무해.
      ('Expecting , delimiter' 오류의 전형적 원인이 이 줄바꿈-누락콤마다.)
    """
    s = re.sub(r',\s*([}\]])', r'\1', s)
    s = re.sub(r'}\s*{', '},{', s)
    s = re.sub(r'"\s*\n\s*"', '",\n"', s)
    return s


def _salvage_json_objects(s: str) -> list:
    """배열 안의 균형 잡힌 {…} 객체를 하나씩 떼어 개별 파싱한다.

    한 객체가 깨져도 나머지는 회수 — 'JSON 1곳 깨짐 → 청크 전체 손실'을 막는다.
    문자열 리터럴 안의 중괄호/따옴표는 상태 추적으로 무시한다.
    """
    out, depth, start = [], 0, None
    in_str, esc = False, False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    frag = s[start:i + 1]
                    for cand in (frag, _repair_json(frag)):
                        try:
                            out.append(json.loads(cand, strict=False))
                            break
                        except json.JSONDecodeError:
                            continue
                    start = None
    return out


# ⚠ 어문 규범(KAGEC) 컨텍스트 주입은 제거됨(2026-06-22). 규범 블록이 AI 오탈자 탐지를
#   분산시켜 회귀를 유발함(eomun.db 비활성 시 복귀로 입증). 어문 규범은 결정론 페어로만 사용.


class GeminiChecker:
    """Google Gemini 기반 AI 교정교열 (오탈자 보완 / 윤문)"""

    def __init__(self, api_key: str):
        self.client    = genai.Client(api_key=api_key)
        # S7: 최초 호출 성공 시 그 모델을 자동 고정
        self._model_id = _MODEL_CANDIDATES[0]
        self._model_locked = False
        # 이번 실행에서 한도 소진(429)이 확인된 모델 — 남은 청크에서 즉시 건너뛴다.
        #   무료 티어 RPD는 하루 단위라 프로세스 수명 동안 유지해도 무방하다.
        self._quota_blocked: set = set()
        # S9: 청크 호출 실패 집계 — 호출 측(워커)이 '전체 실패/부분 실패'를 구분해
        #   사용자에게 표출한다(과거엔 실패도 빈 결과와 구분 불가 → 침묵 성공).
        self.last_failed_chunks = 0
        self.last_total_chunks  = 0

    # ── 공개 메서드 ────────────────────────────────────

    def check_typo_integrated(self, text: str, suspicious_words: list,
                              logger=None, stop_event: threading.Event = None) -> list:
        """사전 의심 단어 검증을 포함한 오탈자·띄어쓰기 통합 보완.

        청크 간 일관성을 위해 누적 글로서리를 매 청크 프롬프트에 주입.

        성능 최적화: 600+개 의심 단어를 매 청크에 모두 주입하면 AI의 주의가
        분산되고 프롬프트가 비대해진다. 청크에 **실제로 등장하는** 단어만
        필터링해서 주입 → AI의 집중도 향상 + 호출 비용 절감.
        """
        glossary: list = []   # [(original, corrected), ...] 누적

        # 의심 단어를 (실제 어휘, 전체 설명문) 쌍으로 미리 분리
        # "키메시지 (어느 사전에도 없음 — 오탈자 가능성)" → ("키메시지", "키메시지 (...)")
        sw_parsed = []
        for sw in suspicious_words:
            word_part = sw.split(" (", 1)[0].strip()
            sw_parsed.append((word_part, sw))

        def _chunk_sw(chunk: str) -> list:
            # 이 청크에 실제 등장하는 의심 단어만 추출
            return [full for word, full in sw_parsed if word and word in chunk]

        def prompt_tmpl(chunk: str) -> str:
            chunk_sw = _chunk_sw(chunk)
            if logger:
                logger(f"    이 청크 의심 단어: {len(chunk_sw)}개 (전체 {len(sw_parsed)}개 중)")
            return build_integrated_prompt(chunk, chunk_sw, glossary=glossary)

        def fallback_tmpl(chunk: str):
            # S10: 재시도용 '글로서리 제거' 프롬프트 — 글로서리 지침 × 반복 표기 청크가
            #   greedy 생성 무한루프(→504)를 유발하는 결정론 실패의 퇴각로. 같은 프롬프트를
            #   다시 보내면 매번 같은 실패이므로, 재시도는 글로서리 없이 재구성한다
            #   (30.hwp 실측: 글로서리 10개=504, 글로서리 없음=2.4초 성공). 글로서리가
            #   비어 있으면 프롬프트가 동일하므로 None(폴백 무의미).
            if not glossary:
                return None
            return build_integrated_prompt(chunk, _chunk_sw(chunk), glossary=None)

        return self._run_chunked(
            text, prompt_tmpl, AI_CHUNK_TYPO,
            set(), "ai_typo", HL_TYPO,
            logger, stop_event,
            glossary=glossary,
            fallback_tmpl=fallback_tmpl,
            max_output_tokens=AI_MAX_OUT_TYPO,
        )

    def check_polish(self, text: str,
                     logger=None, stop_event: threading.Event = None) -> list:
        """출판사 에디터 수준의 전체 윤문"""
        prompt_tmpl = lambda chunk: build_polish_prompt(chunk)

        return self._run_chunked(
            text, prompt_tmpl, AI_CHUNK_POLISH,
            set(), "ai_polish", HL_POLISH,
            logger, stop_event,
            max_output_tokens=AI_MAX_OUT_POLISH,
        )

    def check_scope(self, text: str, suspicious_words: list,
                    scope_typo: bool = True, scope_spacing: bool = True,
                    scope_polish: bool = False,
                    logger=None, stop_event=None) -> list:
        """범위 선택적 교정."""
        results = []
        # S9: 실행 단위로 실패 집계 리셋(오탈자+윤문 두 패스 누적)
        self.last_failed_chunks = 0
        self.last_total_chunks  = 0

        if scope_typo or scope_spacing:
            if logger:
                logger("  [AI] 오탈자·사전 통합 분석 시작…")
            typo_results = self.check_typo_integrated(text, suspicious_words, logger, stop_event)
            results.extend(typo_results)

        if scope_polish and (not stop_event or not stop_event.is_set()):
            if logger:
                logger("  [AI] 윤문 분석 시작…")
            polish_results = self.check_polish(text, logger, stop_event)
            results.extend(polish_results)

        return results

    # ── 내부 메서드 ────────────────────────────────────

    def _run_chunked(self, text, prompt_tmpl, chunk_size,
                     skip_set, source, color, logger, stop_event,
                     glossary: list = None, fallback_tmpl=None,
                     max_output_tokens: int = None) -> list:
        """텍스트를 청크로 분할하여 AI 호출, 결과 병합.

        glossary 리스트가 주어지면 매 청크의 확정 교정을 누적해
        다음 청크 프롬프트에서 참조하게 한다(청크 간 일관성).
        fallback_tmpl(chunk)이 문자열을 반환하면 재시도 2차부터 그 프롬프트로 재호출
        (S10 — 글로서리 유발 생성 무한루프의 퇴각로). max_output_tokens는 폭주 절단 상한.
        """
        chunks = self._split_sentences(text, chunk_size)
        total  = len(chunks)
        result = []
        # 글로서리 폭주 방지 — 한 청크 프롬프트에 너무 많이 들어가지 않게 제한
        GLOSSARY_MAX = 50

        for i, chunk in enumerate(chunks):
            if stop_event and stop_event.is_set():
                if logger:
                    logger("  [AI] 취소 신호 감지 — 중단합니다.")
                break

            if logger:
                logger(f"  [AI] 분석 중… {i+1}/{total} 청크")

            self.last_total_chunks += 1
            fallback = fallback_tmpl(chunk) if fallback_tmpl is not None else None
            raw_items = self._call_and_parse(prompt_tmpl(chunk), logger,
                                             stop_event=stop_event,
                                             fallback_prompt=fallback,
                                             max_output_tokens=max_output_tokens)
            if raw_items is None:          # 호출 실패(재시도 소진) — 빈 결과와 구분해 집계
                self.last_failed_chunks += 1
                raw_items = []
            tagged    = self._tag(raw_items, skip_set, source, color)
            result.extend(tagged)

            # 글로서리 누적 — 이 청크에서 확정된 high-confidence 교정만
            if glossary is not None:
                existing = {orig for orig, _ in glossary}
                for c in tagged:
                    if c.confidence == "high" and c.original not in existing:
                        glossary.append((c.original, c.corrected))
                        existing.add(c.original)
                        if len(glossary) >= GLOSSARY_MAX:
                            break

            if i < total - 1:
                time.sleep(AI_CALL_DELAY)

        # 중복 제거
        seen, unique = set(), []
        for item in result:
            if item.original not in seen:
                seen.add(item.original)
                unique.append(item)
        return unique

    @staticmethod
    def _split_sentences(text: str, max_chars: int) -> list:
        """문장 종결 부호 기준 분할.

        I9: 큰따옴표("...") 내부의 종결부호는 분할점에서 제외해 인용구가
            중간에 끊기지 않도록 한다. 한국어 본문에서 흔히 쓰이는 '"' '"'
            '＂' 모두 처리.

        ⚠ 문단 경계(줄바꿈)는 여기서 공백으로 붕괴된다 — 표/그림 캡션·각주가 본문과 별도
           문단으로 추출돼도 한 줄로 합쳐져(…Figure 1 Figure 1. Proposed… 과 같은…) AI가
           '중복 설명'으로 오인·삭제하는 '캡션삭제'의 근원이다(2026-06-30 확인). **줄바꿈 보존**
           수정안을 만들어 실Gemini로 캡션삭제 근원 제거를 검증했으나, AI 입력 변경이라 비결정
           회수(recall)가 출렁였고(예: '무기체계와z' 오타 탐지가 런마다 달라짐) **AI 골드셋이
           아직 없어** 회귀 검증 불가 → 사용자 결정(2026-06-30)으로 **원복(보수)**. 캡션삭제는
           결정론 대량삭제 가드(proofreading_worker, [[safety-net-overflag-guards]] ③-d)가 전담.
           재시도는 **AI 골드셋 마련 후** precision/recall 정량 비교 선결. [[gemini-call-hardening-and-prompts]].
        """
        # 인용 영역을 잠시 sentinel로 치환하여 split 후 복원
        quote_pattern = re.compile(r'([""＂"][^""＂"]*[""＂"])')
        placeholders = {}

        def _mask(m):
            key = f"\x00Q{len(placeholders)}\x00"
            placeholders[key] = m.group(0)
            return key

        masked = quote_pattern.sub(_mask, text)
        sentences = re.split(r'(?<=[.!?。])\s+|(?<=\n)', masked)

        def _unmask(s: str) -> str:
            for key, orig in placeholders.items():
                s = s.replace(key, orig)
            return s

        sentences = [_unmask(s) for s in sentences]

        chunks, buf = [], ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(buf) + len(sent) + 1 <= max_chars:
                buf += (" " if buf else "") + sent
            else:
                if buf:
                    chunks.append(buf)
                if len(sent) > max_chars:
                    parts = sent.split(",")
                    buf = ""
                    for part in parts:
                        if len(buf) + len(part) + 1 <= max_chars:
                            buf += ("," if buf else "") + part
                        else:
                            if buf:
                                chunks.append(buf)
                            buf = part
                else:
                    buf = sent

        if buf:
            chunks.append(buf)

        return chunks or [text[:max_chars]]

    def _call_and_parse(self, prompt: str, logger,
                        stop_event: threading.Event = None,
                        fallback_prompt: str = None,
                        max_output_tokens: int = None):
        """Gemini 호출 → JSON 파싱.

        S7:  모델이 deprecated/unavailable이면 폴백 후보로 자동 전환.
        S9:  일시 오류(429/5xx/타임아웃/네트워크)는 백오프 후 재시도(_RETRY_DELAYS).
        S10: fallback_prompt가 주어지면 재시도 2차부터 그 프롬프트로 교체 — 결정론
             디코딩에선 같은 프롬프트 재전송이 같은 실패(글로서리 유발 생성 무한루프
             → 504)를 반복하므로, 퇴각 프롬프트(글로서리 제거)로 청크 유실을 막는다.
             max_output_tokens는 무한루프 폭주를 절단하는 상한(디코딩 무변경 — 절단만).

        반환: 파싱된 리스트(성공 — 빈 리스트 포함) 또는 **None(호출 실패)**.
              호출 측은 None을 실패로 집계한다(빈 결과와 구분).
        """
        cfg_kwargs = dict(
            system_instruction = SYSTEM_INSTRUCTION,
            # JSON 형식은 mime_type + _parse_json_response의 salvage 백스톱으로 보장.
            #   ⚠ response_schema(구조화 출력)는 재현성을 깨서 쓰지 않는다(상단 주석 참조).
            response_mime_type = "application/json",
            # 결정론적 출력 — 같은 원고 → 같은 교정
            temperature        = 0.0,
            top_p              = 1.0,
            top_k              = 1,
            seed               = 42,
            # 청크 호출 타임아웃 — API가 멈춰도 워커가 영원히 블록(앱 프리즈)되지 않게.
            http_options       = genai_types.HttpOptions(timeout=AI_REQUEST_TIMEOUT),
        )
        if max_output_tokens:
            cfg_kwargs["max_output_tokens"] = max_output_tokens
        config = genai_types.GenerateContentConfig(**cfg_kwargs)

        last_exc = None
        max_tries = 1 + len(_RETRY_DELAYS)

        for attempt in range(max_tries):
            if stop_event is not None and stop_event.is_set():
                return None

            # S10: 2차 시도부터 퇴각 프롬프트(있다면)로 교체
            use_prompt = prompt
            if attempt > 0 and fallback_prompt:
                use_prompt = fallback_prompt
                if attempt == 1 and logger:
                    logger("  [AI] 재시도는 글로서리 없이 재구성한 프롬프트로 호출"
                           "(반복 생성 폭주 회피)")

            # 후보 체인: 현재 모델 우선 + 나머지 후보. 이번 실행에서 **한도 소진으로 확인된**
            #   모델(_quota_blocked)은 건너뛴다 — 매 청크마다 429를 다시 맞고 백오프하면
            #   문서 하나 교정에 수 분이 날아간다.
            chain = (self._model_id,) + tuple(
                m for m in _MODEL_CANDIDATES if m != self._model_id
            )
            avail = tuple(m for m in chain if m not in self._quota_blocked)
            if not avail:
                avail = chain          # 전부 소진 → 원래 순서로(백오프 재시도가 처리)
            # 모델 고정은 유지하되, 고정된 모델이 한도 소진이면 잠금을 무시하고 체인을 탄다.
            models_to_try = (
                (avail[0],) if (self._model_locked and avail[0] == self._model_id)
                else avail
            )

            transient = False
            forced_fallback = False
            for model_id in models_to_try:
                try:
                    response = self.client.models.generate_content(
                        model    = model_id,
                        contents = use_prompt,
                        config   = config,
                    )
                    # 성공 → 이후 호출 시 동일 모델 고정
                    if not self._model_locked:
                        self._model_id = model_id
                        self._model_locked = True
                        if logger and model_id != _MODEL_CANDIDATES[0]:
                            logger(f"  [AI] 모델 자동 전환: {model_id}")
                    # S10b: 출력 상한 절단(MAX_TOKENS) 감지 — 상한에 걸린 건 대부분
                    #   반복 생성 폭주다(글로서리 항목을 등장마다 무한 나열). 부스러기를
                    #   salvage하는 것보다 퇴각 프롬프트(글로서리 제거)로 즉시 재호출해
                    #   온전한 결과를 받는 편이 낫다(서버 오류가 아니라 백오프 불필요).
                    try:
                        _fr = getattr(response.candidates[0].finish_reason, "name",
                                      str(response.candidates[0].finish_reason))
                    except Exception:
                        _fr = ""
                    if ("MAX_TOKENS" in _fr and fallback_prompt
                            and use_prompt is not fallback_prompt):
                        if logger:
                            logger("  [AI] 출력 상한 도달(반복 생성 폭주 추정) — "
                                   "글로서리 없이 즉시 재호출")
                        forced_fallback = True
                        break
                    return self._parse_json_response(response.text, logger)
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    # 모델 자체 문제(없음/deprecated/권한)면 다음 후보로
                    if any(k in msg for k in ("not found", "deprecat", "permission", "not supported", "404")):
                        if logger:
                            logger(f"  [AI] '{model_id}' 사용 불가 — 다음 모델 시도")
                        continue
                    # 한도 소진(429/RESOURCE_EXHAUSTED) → **대기 없이 백업 모델로 즉시 전환**.
                    #   무료 티어는 모델별로 한도가 따로라(3.5-lite 500 RPD, 3.1-lite 500 RPD)
                    #   한쪽이 마르면 다른 쪽이 살아 있다. 백오프로 기다리는 것보다 훨씬 빠르고,
                    #   재시도 소진 후 청크를 통째로 버리는 '조용한 교정 누락'도 막는다.
                    if _is_quota_error(msg):
                        self._quota_blocked.add(model_id)
                        remaining = [m for m in models_to_try
                                     if m not in self._quota_blocked]
                        if remaining:
                            self._model_locked = False   # 잠금 해제 → 백업 모델로 재고정
                            if logger:
                                logger(f"  [AI] '{model_id}' 한도 소진 — "
                                       f"백업 모델 '{remaining[0]}'로 전환")
                            continue
                        if logger:
                            logger("  [AI] 모든 후보 모델의 한도가 소진됨 — 백오프 후 재시도")
                    # 일시 오류(429/5xx/타임아웃/네트워크)는 재시도 대상
                    transient = _is_transient_error(msg)
                    break

            if forced_fallback and attempt < max_tries - 1:
                continue   # 딜레이 없이 다음 시도(=퇴각 프롬프트)로
            if transient and attempt < max_tries - 1:
                delay = _RETRY_DELAYS[attempt]
                if logger:
                    logger(f"  [AI] 일시 오류({type(last_exc).__name__}) — "
                           f"{delay:.0f}초 후 재시도 ({attempt + 2}/{max_tries})")
                if stop_event is not None:
                    if stop_event.wait(delay):   # 대기 중 취소되면 즉시 중단
                        return None
                else:
                    time.sleep(delay)
                continue
            break   # 비일시 오류 또는 재시도 소진

        if logger and last_exc:
            em = str(last_exc).lower()
            if "timeout" in em or "timed out" in em or "deadline" in em:
                logger(f"  [AI] 호출 타임아웃({AI_REQUEST_TIMEOUT // 1000}초 초과) — "
                       f"이 청크만 건너뛰고 계속합니다(앱 프리즈 방지).")
            else:
                logger(f"  [AI] API 호출 오류(재시도 소진): {last_exc}")
        return None

    @staticmethod
    def _parse_json_response(raw: str, logger) -> list:
        """응답 텍스트에서 JSON 배열 추출"""
        if not raw:
            return []
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        json_match = re.search(r'\[.*\]', clean, re.DOTALL)
        if not json_match:
            # 절단 응답(닫는 ']' 없음 — MAX_TOKENS 폭주 절단 등)에서도 균형 잡힌
            #   {…} 객체는 회수한다. 마지막 미완성 항목만 버려져 손실 최소.
            if "{" in clean:
                salvaged = _salvage_json_objects(clean)
                if salvaged:
                    if logger:
                        logger(f"  [AI] 절단 응답 부분 복구 — {len(salvaged)}개 항목 회수"
                               " (출력 상한/중단으로 배열이 닫히지 않음)")
                    return salvaged
            if logger:
                logger("  [AI] 올바른 JSON 응답 없음 — 청크 스킵")
            return []
        blob = json_match.group(0)
        try:
            # strict=False: Gemini가 문자열 값 안에 raw 줄바꿈/탭을 넣어
            # 보내는 경우가 있어 표준 strict 파서로는 깨진다.
            return json.loads(blob, strict=False)
        except json.JSONDecodeError as exc:
            # 1차 복구 — 흔한 깨짐(후행/누락 콤마) 안전 보정 후 재시도.
            #   과거엔 여기서 청크 전체를 버려, JSON 1곳만 깨져도 그 청크의 모든
            #   교정 제안이 통째로 손실됐다(예: 36건 중 한 청크 전부 유실).
            try:
                result = json.loads(_repair_json(blob), strict=False)
                if logger:
                    logger("  [AI] JSON 자동 복구(누락/후행 콤마 보정) — 청크 회수")
                return result
            except json.JSONDecodeError:
                pass
            # 2차 복구 — 객체 단위로 회수(깨진 항목만 버리고 나머지 살림).
            salvaged = _salvage_json_objects(blob)
            if salvaged:
                if logger:
                    logger(f"  [AI] JSON 부분 복구 — {len(salvaged)}개 항목 회수"
                           f" (깨진 항목만 제외, 오류: {exc})")
                return salvaged
            if logger:
                logger(f"  [AI] JSON 파싱 오류(복구 실패): {exc}")
            return []

    @staticmethod
    def _tag(items: list, skip_set: set, source: str, color: int) -> list:
        """유효성 검사 + 소스/색상 태그 추가 및 카테고리/신뢰도 파싱.

        AI가 NFC가 아닌 분해형(NFD) 한글이나 ZWSP/BOM/한글 채움 문자
        같은 불가시 코드포인트를 섞어 반환하면 "원문/교정 시각적 동일"
        거짓 항목이 들어와 적용에서 실패함 → 강한 정규화 적용.
        """
        # 모듈 import는 함수 내부에서 — 동일 정규식을 bridge_worker가 공유
        from core.consistency_pass import _nfc as _clean

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            original   = _clean(str(item.get("original",  "")))
            corrected  = _clean(str(item.get("corrected", "")))
            reason     = str(item.get("reason",    ""))
            category   = str(item.get("category",  ""))
            confidence = str(item.get("confidence", "high"))

            if (original and corrected
                    and original != corrected
                    and original not in skip_set):
                result.append(Correction(
                    original   = original,
                    corrected  = corrected,
                    reason     = reason,
                    source     = source,
                    color      = color,
                    category   = category,
                    confidence = confidence,
                ))
        return result
