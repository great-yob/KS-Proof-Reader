"""
ui/workers/proofreading_worker.py — 교정 분석 QThread 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
표준국어대사전 1차 스크리닝 → Gemini AI → 사전 재검증 파이프라인을
백그라운드에서 실행. Signal로 UI에 실시간 진행 상황 전달.
"""

import re
import threading
import traceback
import unicodedata

from PySide6.QtCore import QThread, Signal

from core import (
    ConfigLoader,
    Correction,
    CorrectionMerger,
    HwpEditor,
    DOC_WARN_CHARS,
)
from core.models import HL_DICT, HL_TYPO
from core.correction_engine import build_engine
from core import ai_guards


# AI(생성) 과교정 가드 4종(비한글 잘라내기·괄호 구조·영문 병기·캡션 대량삭제)은
#   core/ai_guards.py로 분리했다(워커·eval 골드셋 공용 — 검증 일관성). 여기선 import해서 쓴다.

# 저자 의도 빈도 임계치 — 비표준 표기를 문서에 이 횟수 이상 반복하면 '의도된 표기'로 보고
#   ⑤ 사전 안전망은 카드를 빼고([6]), 규범표기([5.7]/[5.8])는 자동적용(high) 대신 검수 카드
#   (low)로 강등한다(사용자 결정 2026-06-30). 진짜 오탈자는 보통 1~2회에 그친다.
_FREQ_INTENTIONAL = 3


class ProofreadingWorker(QThread):
    """교정 분석 백그라운드 워커"""

    # ── 시그널 ─────────────────────────────────────
    progress    = Signal(int, str)    # (퍼센트, 메시지)
    log_message = Signal(str)         # 로그 라인
    step_changed = Signal(str, str)   # (단계ID, 메시지)
    finished    = Signal(list)        # 교정 결과 리스트(dict)
    error       = Signal(str)         # 에러 메시지
    text_extracted = Signal(str)      # 추출된 원문 텍스트
    page_count_extracted = Signal(object)  # 문서 총 페이지 수(int) 또는 None

    def __init__(self, file_path: str, options: dict, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.options   = options
        self._stop     = threading.Event()

    def request_stop(self):
        self._stop.set()

    def run(self):
        try:
            self._execute()
        except RuntimeError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"처리 중 오류: {exc}\n{traceback.format_exc()}")

    def _execute(self):
        opts = self.options
        log  = self.log_message.emit

        # [1] 텍스트 추출 — open/close를 try/finally로 보호 (32비트 워커 누수 방지)
        self.step_changed.emit("extract", "HWP 파일 열기 및 텍스트 추출 중…")
        self.progress.emit(3, "파일 열기 중…")

        editor = HwpEditor(self.file_path, logger=log)
        try:
            editor.open()
            text = editor.get_text()
            page_count = getattr(editor, "last_page_count", None)  # hwpx direct 등은 None
        finally:
            try:
                editor.close()
            except Exception:
                pass

        # HWP 추출 텍스트를 **NFC로 정규화**한다. 일부 파일은 한글이 NFD(자모 분리:
        #   ㅅ+ㅏ+ㅇ…)/NFC 혼합 형태로 추출돼, `re.sub(r"[^가-힣]", "", w)`가 결합 자모를
        #   떨어뜨려 '상용화'가 깨진 부분열('상' 등)로 축약된다 → 사전에 **거짓 미탐**(사용자
        #   보고 2026-07-01: 등재어 '상용화'가 갑자기 '어느 사전에도 없음'으로 87회 플래그).
        #   사전(stdict)·kiwi·일관성 패스(consistency_pass)는 모두 NFC를 표준 비교형으로
        #   가정하므로 파이프라인 진입점에서 한 번 통일한다(NFC 문서엔 무연산=무회귀).
        text = unicodedata.normalize("NFC", text)

        char_count = len(text.replace("\n", "").replace(" ", ""))
        if page_count:
            log(f"  추출 완료 — {page_count:,}쪽 · {char_count:,} 글자")
        else:
            log(f"  추출 완료 — {char_count:,} 글자")

        if char_count < 10:
            self.error.emit("추출된 텍스트가 너무 짧습니다. 파일을 확인하세요.")
            return

        if char_count > DOC_WARN_CHARS:
            log(f"  ℹ 문서가 큽니다 ({char_count:,}자). AI 분석에 수 분 이상 소요될 수 있습니다.")

        self.text_extracted.emit(text)
        self.page_count_extracted.emit(page_count)
        self.progress.emit(8, "텍스트 추출 완료")

        # [2] 사전 인프라 준비 + 1차 원문 스크리닝 (항상 ON — 사전이 기본 베이스)
        #     ▸ 사전은 "항상 켜는 기본 도구"다. 원문을 직접 훑어 미등재어(=비단어)를
        #       탐지하는 1차 스크리닝(①)을 매 실행 수행한다 — AI on/off와 무관.
        #       이 결과는 (a) AI 프롬프트 컨텍스트(KAGEC)와 (b) AI가 놓친 미등재어를
        #       '검수 필요' 카드로 띄우는 사전 안전망([6])에 함께 쓰인다.
        #     ▸ 과거 deep_screening 옵트인(기본 OFF)은 폐지됨 — 사전 탐지가 옵션이라
        #       치명적 미탐(예: '상담채녈')이 났다. 이제 사전이 기본, AI가 선택 레이어다.
        #       (성능 튜닝은 후속 과제 — 정확성 우선)
        suspicious_words = []
        validator = None
        is_likely_typo = lambda _w: True   # nikl_dict 로드 실패 시 폴백(필터 무력화)
        try:
            # 루트 경로의 nikl_dict 모듈 임포트
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            from nikl_dict import (KoreanDictValidator, is_likely_typo,
                                   is_registered_online, is_registered_onterm,
                                   lookup_word)
            validator = KoreanDictValidator()
        except ImportError as e:
            log(f"  [사전] 모듈 로드 실패: {e} — 사전 기능 비활성")
            is_registered_online = lambda _w: False
            is_registered_onterm = lambda _w: False
            lookup_word = lambda _w: {"exists": False}

        if validator is None or not validator.available:
            if validator is not None:
                log("  [사전] data/stdict.db 미존재 — 사전 기능 비활성")
        elif not self._stop.is_set():
            self.step_changed.emit("screening", "표준국어대사전 원문 스크리닝 중…")
            # 파이프라인 단계 경계 마커 — 활동 로그가 '무엇이 언제 시작/끝났는지'를
            #   읽을 수 있게 한다(사용자 요청 2026-07-23). 사전을 못 쓰는 경우엔
            #   찍지 않는다 — 위 분기가 '미존재' 사유를 이미 남긴다.
            log("[사전(국립국어원) 분석 시작]")
            self.progress.emit(10, "사전 스크리닝 중…")
            suspicious_words = validator.extract_suspicious_words(text, stop_event=self._stop)
            stats = getattr(validator, "last_stats", {})
            log(
                f"  → 사전 미등재/비표준 어휘 {len(suspicious_words)}개 발견"
                + (f" (전체 유니크 {stats.get('total_unique', '?')}개 중)" if stats else "")
            )
            if suspicious_words and opts.get("use_ai", True):
                log(
                    "    ※ 대부분은 작가 의도 어휘(캐릭터명·외래어·전문용어). "
                    "AI가 진짜 오탈자만 골라 교정하고, AI가 놓친 미등재어는 '검수 필요' 카드로 띄웁니다."
                )

        # 스크리닝 결과를 (어휘, clean, 사유) 구조로 1회 파싱 — 안전망/검수 모드 공용.
        #   suspicious_words 포맷: "어휘 (사유)" (nikl_dict.extract_suspicious_words).
        dict_flags = []
        _seen_flag = set()
        for sw in suspicious_words:
            word = sw.split(" (", 1)[0].strip()
            clean = re.sub(r"[^가-힣]", "", word)
            if len(clean) < 2 or clean in _seen_flag:
                continue
            _seen_flag.add(clean)
            reason = sw.split(" (", 1)[1].rstrip(")").strip() if " (" in sw else "표제어 확인 필요"
            dict_flags.append((word, clean, reason))

        # 우리말샘 API 캐싱 폴백 — 로컬 DB가 놓친 실재어(대행사·돌봄 등)를 라이브
        #   사전으로 확인해 거짓 '검수 필요'를 제거. 호출 최소화를 위해 '오타로 보이는'
        #   후보(is_likely_typo)만 조회하고, 결과는 영구 캐시(api_cache.db)된다.
        #   키 없음/오프라인이면 is_registered_online이 모두 False → 기존 동작.
        try:
            from nikl_api import available as _api_available
            _use_api = _api_available()
        except Exception:
            _use_api = False
        if _use_api and dict_flags and not self._stop.is_set():
            self.step_changed.emit("screening", "우리말샘 사전 API로 미등재어 확인 중…")
            kept_flags, online_hits = [], 0
            for word, clean, reason in dict_flags:
                if self._stop.is_set():
                    kept_flags.append((word, clean, reason))
                    continue
                if is_likely_typo(word) and is_registered_online(word):
                    online_hits += 1   # 라이브 우리말샘 등재 실재어 → 오타 아님, 제외
                    continue
                kept_flags.append((word, clean, reason))
            if online_hits:
                log(f"  → 우리말샘 API: 로컬 DB 누락 실재어 {online_hits}건 확인·제외 (거짓 검수 방지)")
            dict_flags = kept_flags

        # 온용어 API 캐싱 폴백(2단) — 우리말샘에도 없는 **기관 전문용어**(법령·정보통신·
        #   의학·전력·국방…)를 확인해 거짓 '검수 필요'를 더 줄인다. 우리말샘 뒤에 두는 건
        #   일반어를 먼저 걷어내 온용어 호출 수를 줄이기 위함(일일 5만 건 제한).
        #   실측(실문서 3건 504K자): 검수 대상 153건 중 12건 구제, 진짜 오탈자 오구제 0건.
        #   ⚠ 온용어엔 비표준 표기도 등재돼 있어(디지털컨텐츠) is_registered_onterm 안에
        #     norm_map/spelling_pairs 거부권 가드가 걸려 있다 — 설계도 §5-2.
        try:
            from onterm_api import available as _onterm_available
            _use_onterm = _onterm_available()
        except Exception:
            _use_onterm = False
        if _use_onterm and dict_flags and not self._stop.is_set():
            self.step_changed.emit("screening", "온용어 전문용어 사전 API로 확인 중…")
            # 조회 대상만 먼저 추린다 — is_likely_typo(kiwi)는 워커 스레드에서 직렬로.
            targets = [f for f in dict_flags if is_likely_typo(f[0])]
            rescued: set = set()
            if targets:
                # 순수 네트워크 I/O라 병렬화가 그대로 이득(직렬 0.5s/건 × 최대 74건 = 39초).
                #   onterm_api는 캐시 접근만 락으로 감싸고 네트워크는 밖에 두어 병렬을 허용한다
                #   (nikl_api는 호출 전체가 락 안이라 병렬화해도 소용없다 — 그래서 1단은 직렬).
                from concurrent.futures import ThreadPoolExecutor
                pool = ThreadPoolExecutor(max_workers=4)
                try:
                    futures = {pool.submit(is_registered_onterm, w): c
                               for w, c, _r in targets}
                    for fut, clean in futures.items():
                        if self._stop.is_set():
                            break
                        try:
                            if fut.result():
                                rescued.add(clean)
                        except Exception:
                            pass          # graceful — 확인 불가는 기존대로 플래그 유지
                finally:
                    # 취소 시 대기 중인 조회를 즉시 버린다(wait=True면 최대 수 초 지연).
                    pool.shutdown(wait=False, cancel_futures=True)
            if rescued:
                dict_flags = [f for f in dict_flags if f[1] not in rescued]
                log(f"  → 온용어 API: 기관 전문용어 {len(rescued)}건 확인·제외 (거짓 검수 방지)")
            try:
                from onterm_api import quota_exceeded as _onterm_quota
                if _onterm_quota():
                    log("  ⚠ 온용어 API 일일 한도(5만 건) 초과 — 이번 실행은 전문용어 확인을 건너뜁니다.")
            except Exception:
                pass

        def _make_flag(word: str, reason: str) -> Correction:
            """미등재어 '검수 필요' 카드(치환 아님 — original==corrected).

            카드 문구는 '오탈자 가능성'처럼 단정하지 않는다 — 안전망에 남는 건
            대개 고유명사·전문용어·미등재 외래어라 '오타로 단정'하면 교정 도구의
            신뢰를 해친다(사용자 피드백). '확인 권장'으로 톤을 낮춘다(AI 프롬프트
            컨텍스트 문자열은 그대로 두어 AI 동작에는 영향 없음)."""
            card_reason = reason.replace(
                "오탈자 가능성", "확인 권장 (고유명사·전문용어면 무시)")
            return Correction(
                original=word, corrected=word,
                reason=f"[검수] {card_reason}",
                source="dict_flag", color=HL_DICT,
                category="검수 필요", confidence="low",
            )

        self.progress.emit(20, "사전 스크리닝 완료")

        # [3] AI 교정 — Gemini 생성 엔진. API 키가 없으면 사용 불가.
        ai_list = []
        if opts.get("use_ai", True) and not self._stop.is_set():
            config = ConfigLoader()
            api_key = config.get_gemini_key()
            engine = build_engine(api_key, logger=log)
            if engine is None:
                self.error.emit(
                    "교정 엔진을 사용할 수 없습니다.\n"
                    "Gemini API 키를 설정하세요 (config.ini [API] GEMINI_API_KEY).")
                return

            scope_typo    = opts.get("scope_typo", True)
            scope_spacing = opts.get("scope_spacing", True)
            scope_polish  = opts.get("scope_polish", False)

            scopes = []
            if scope_typo:    scopes.append("오탈자")
            if scope_spacing: scopes.append("띄어쓰기")
            if scope_polish:  scopes.append("윤문")
            label = "·".join(scopes) if scopes else "보완"

            self.step_changed.emit("ai", f"AI {label} 분석 중…")
            self.progress.emit(40, f"AI {label} 분석 중…")

            ai_list = engine.check_scope(
                text, suspicious_words,
                scope_typo=scope_typo,
                scope_spacing=scope_spacing,
                scope_polish=scope_polish,
                logger=log, stop_event=self._stop,
            )
            log(f"  → AI 교정 제안: {len(ai_list)}건")

            # AI 호출 실패 표출 — 과거엔 실패 청크가 '제안 0건'과 구분되지 않아
            #   네트워크 단절/키 무효/할당량 초과에도 '분석 완료'로 보였다(침묵 성공).
            #   전체 실패는 오류 레벨로, 부분 실패는 경고로 로그에 드러낸다(분석은 계속
            #   — 사전·규칙 교정은 유효하므로 중단하지 않는다).
            _cs = getattr(engine, "last_call_stats", None) or {}
            _cf, _ct = _cs.get("failed", 0), _cs.get("total", 0)
            if not self._stop.is_set():
                if _ct and _cf >= _ct:
                    log(f"  ✕ 오류: AI 분석 전체 실패 — {_cf}/{_ct} 청크 호출이 모두 실패했습니다. "
                        "네트워크 연결·API 키·사용량 한도를 확인하세요. "
                        "이번 결과는 사전·규칙 교정만 포함합니다.")
                elif _cf:
                    log(f"  ⚠ AI 청크 {_cf}/{_ct} 실패 — 해당 구간의 AI 제안이 누락됐을 수 "
                        "있습니다(사전·규칙 교정은 정상).")

            # AI(생성) 과교정 4종 가드 — core/ai_guards.py가 단일 출처(eval 골드셋과 공용).
            #   ① 비한글 잘라내기(Microseparometer,P) ② 괄호 구조 변경 ③ 영문 병기(재머→재머(jammer))
            #   ④ 캡션 인라인 대량삭제(Figure 1 캡션 통째 삭제). 전부 억제 방향(과교정 0).
            #   상세·실측은 ai_guards 모듈 docstring + memory safety-net-overflag-guards ③~③-d.
            ai_list, _ = ai_guards.filter_overcorrections(ai_list, logger=log)
            # 문서 텍스트가 필요한 가드 — 부분조각 확장(소프트→소프트웨어·소규모·저→소규모·저매출·
            #   …소프트→…소프트 스킬): 문서에 이미 온전히 있는 더 긴 표기의 조각을 부풀린 과교정.
            #   조사형 등장('책임연구원으로')도 독립 사용으로 판정한다(책임연→책임연구원 차단).
            ai_list = ai_guards.filter_redundant_expansions(ai_list, text, logger=log)
            # 환각 '문서 내 일관성' 재띄어쓰기 드롭 — '수급자_사망의심자'→'수급자_사망 의심자'
            #   ("문서 내 일관성 유지" 사유)처럼 띄어 쓴 형태('사망 의심자')가 문서 어디에도
            #   없는데 일관성을 명분으로 분리한 AI 환각([M]이 밑줄 어절이라 못 잡음, 2026-07-21).
            ai_list = ai_guards.drop_hallucinated_consistency_respacing(ai_list, text, logger=log)
            # 원어 병기 명칭 치환 드롭 — '과학산업자원부(…DISR)'처럼 라틴 병기 괄호로
            #   정체가 고정된 명칭을 다른 명칭('산업통상자원부')으로 개명하는 AI 과교정
            #   차단(2026-07-14 보고, 자모거리≥4 + 병기 앵커).
            ai_list = ai_guards.drop_glossed_name_substitution(ai_list, text, logger=log)
            # 괄호 뒤 조사 받침 호응 보정 — AI가 조사 교체(격 판단)는 맞게 잡고 받침 형태를
            #   틀린 경우('가시성(visibility)가'→AI '…를') 괄호 앞 체언(가시성, 받침 ㅇ)에
            #   호응시켜 '을'로 보정(find_paren_josa와 동일 원칙의 결정론 fix-up, 2026-07-06).
            ai_list = ai_guards.fix_paren_josa_agreement(ai_list, text, logger=log)
            # 모호(헤지) 사유 강등 — AI가 "…로 보이나 … 권장함"처럼 확신 없는 판단을 남긴
            #   교정은 무조건 low 검수 카드로(자동 적용 금지, 사용자 지시 2026-07-03).
            #   Case A 변형이 canon confidence를 복사하므로 이 단계(원시 ai_list)에서 강등.
            ai_list = ai_guards.demote_hedged_corrections(ai_list, logger=log)
            # '관용 표기·관례' 주장 사유 강등 — AI가 실존 규범이 아닌 '관례/관용 표기'를
            #   근거로 대는 교정(기관명 개명 오교정 2건의 공통 패턴)은 low 검수 카드로
            #   (병기 괄호 없는 명칭 치환 변종의 마지막 그물, 사용자 지시 2026-07-14).
            ai_list = ai_guards.demote_convention_claims(ai_list, logger=log)
            # '문맥상' 판단 사유 강등 — AI가 규범이 아닌 '문맥/맥락'을 근거로 낱말을 지우거나
            #   바꾸는 교정('LH 주택공사의'→'LH의' 중복어 삭제 등)은 low 검수 카드로
            #   (중복 지적은 맞아도 삭제는 편집자 몫, 사용자 지시 2026-07-21).
            ai_list = ai_guards.demote_contextual_judgment(ai_list, logger=log)
            # 기관·부처 명칭 치환 강등 — AI는 학습 시점 이후의 개편·개명을 몰라 현행 명칭을
            #   옛 명칭으로 되돌린다('성평등가족부장관'→'여성가족부장관', 2026-07-21 보고).
            #   기관명 치환은 표기 교정이 아니라 사실 편집 → 자동 적용 금지, 편집자 검수.
            ai_list = ai_guards.demote_org_name_substitution(ai_list, logger=log)
        elif not opts.get("use_ai", True):
            log("  [AI] AI 분석 제외 모드 — Gemini 호출 없이 사전·규칙 검사만 수행합니다.")
        self.progress.emit(70, "AI 분석 완료" if opts.get("use_ai", True)
                           else "AI 분석 제외 — 사전·규칙 검사 진행")

        if self._stop.is_set():
            self.error.emit("사용자에 의해 취소되었습니다.")
            return

        # 로그 단순화 — 결정론 보강 패스([5.x]·[6]·[7])의 개별 'N건 추가' 라인을 모아
        #   끝에서 2줄(자동 적용/검토 필요)로 요약한다. (label, 건수) 누적.
        det_auto, det_review = [], []

        # ─────────────────────────────────────────────────────────
        # [4]~[7] 공용 보강 파이프라인 — 병합 → 일관성 → 사전 재검증 → 결정론 패스.
        #   'AI 분석 제외'(use_ai=False) 모드에서도 항상 수행한다(ai_list만 비어 있음).
        # ─────────────────────────────────────────────────────────
        # [4] 정렬 및 병합
        self.step_changed.emit("merge", "교정 목록 정렬 및 중복 제거 중…")
        self.progress.emit(72, "교정 병합 중…")
        merged = CorrectionMerger.merge([], ai_list)
        log(f"  → 1차 확정 교정 항목: {len(merged)}건")
        # 생성(AI) 구간이 끝나고 검증(사전·결정론 규칙) 구간이 시작되는 경계.
        log("[사전+결정론규칙 검증 시작]")

        # [4.5] 문서 전체 일관성 강화 (청크 간 비결정성 보정)
        if merged and not self._stop.is_set():
            try:
                from core.consistency_pass import enforce_consistency
                before_n = len(merged)
                merged = enforce_consistency(merged, text, logger=log)
                added = len(merged) - before_n
                if added > 0:
                    log(f"  → 일관성 보정: 변형 단어 {added}건 자동 추가")
            except Exception as e:
                log(f"  [일관성] 후처리 실패 (스킵): {e}")
            self.progress.emit(76, "일관성 보정 완료")

        # [5] 사전 재검증 (3차) — 항상 수행 (DB 사용 가능 시).
        #     AI가 비표준어로 교정하면 confidence=low로 낮춰 과교정을 억제한다.
        if validator and validator.available and not self._stop.is_set():
            self.step_changed.emit("validate", "표준국어대사전 3차 재검증 중…")
            self.progress.emit(80, "사전 재검증 중…")
            merged = validator.validate(merged, stop_event=self._stop)
            # 조사 변형 간 신뢰도 통일 — 같은 단어인데 bare=low/조사형=high로 갈려
            # 자동적용 시 한쪽만 교정되는 불일치 방지(예: 훗가이도현 vs 훗가이도현의).
            try:
                from core.consistency_pass import reconcile_variant_confidence
                merged = reconcile_variant_confidence(merged, logger=log)
            except Exception as e:
                log(f"  [일관성] 신뢰도 통일 스킵: {e}")
            # 외래어 순화(파라미터→매개변수)는 맞춤법 교정이 아니라 표기 판단 → 제외(드롭).
            #   base(조사 제거) 둘 다 등재 표제어 + 글자 완전 상이(편집거리≥3·공유 2-gram 없음)일 때만.
            #   실단어 오류(결제↔결재·지향↔지양)는 글자 공유라 무영향. validate 이후 최종 신뢰도 확정.
            try:
                import nikl_dict as _nd_demote
                merged = ai_guards.drop_loanword_paraphrase(
                    merged, lambda w: _nd_demote.lookup_word(w)["exists"], logger=log)
                # 문맥 윤문의 '단어 교체'(하에→아래처럼 멀쩡한 단어를 유의어로) 차단 —
                #   저자 고유 표기 권한. 조사 추가(BMBF→BMBF의)는 부분문자열이라 보존.
                merged = ai_guards.drop_word_substitution_paraphrase(
                    merged, lambda w: _nd_demote.lookup_word(w)["exists"], logger=log)
                # 반표준화(등재 표준형→미등재 근접변이) 차단 — '실젯값'(등재)→'실제값'(미등재)
                #   처럼 표준형을 사전에 없는 사이시옷/받침 변이로 되돌리는 자기모순 AI 교정.
                #   ⚠ exists_fn은 반드시 '직접 표제어 조회'(lookup_word)여야 함 — 재검증이 놓친
                #   원인이 is_known_form(실제=NNG+값=NNG 복합어)이라 그걸 우회해야 발동한다.
                merged = ai_guards.drop_destandardizing_variant(
                    merged, lambda w: _nd_demote.lookup_word(w)["exists"], logger=log)
            except Exception as e:
                log(f"  [순화] 외래어 순화 제외 스킵: {e}")

        # [5.6] 사내 용어 결정론 페어 — userdict.db(core.userdict)의 합의·큐레이터
        #     승인된 사내 비표준→표준 매핑(설계 역할 P). norm_map/eomun_pairs와 동일
        #     경로·형상이며 동형이의어 가드는 빌드타임(build_userdict_db.py)에 적용됨.
        #     ⚠ 국가 표준 우선 — 같은 토큰을 norm_map/eomun_pairs가 *다른* 값으로
        #     교정하면 사내 페어를 양보하고 충돌만 로깅한다(후속 [5.7]/[5.8]이 처리).
        #     userdict.db 부재/빈 스냅샷이면 batch가 {}라 자동 비활성(graceful).
        if not self._stop.is_set():
            try:
                from core import userdict as _ud
                import nikl_dict as _nd
                from core import eomun_rules as _er
                from core.consistency_pass import _strip_josa
                doc_eojeols = set(re.findall(r"[가-힣]+", text))
                bases = set(doc_eojeols)
                for w in doc_eojeols:
                    b = _strip_josa(w)
                    if b and len(b) >= 2:
                        bases.add(b)
                pair_map = _ud.batch_lookup_pair(bases)
                if pair_map:
                    # 국가 표준 우선 — 사내 페어가 매칭된 키만 norm_map/eomun과 대조
                    matched = set(pair_map.keys())
                    std_norm = _nd.batch_lookup_norm(matched)
                    std_eomun = _er.batch_lookup_eomun_pair(matched)
                    existing = {c.original for c in merged}
                    net, conflicts = [], 0
                    for w in doc_eojeols:
                        for key in (w, _strip_josa(w)):
                            norm = pair_map.get(key)
                            if not norm or norm == key:
                                continue
                            # 충돌: 국가 표준이 같은 키를 *다른* 값으로 교정 → 양보
                            std = std_norm.get(key) or std_eomun.get(key)
                            if std and std != norm:
                                conflicts += 1
                                log(f"  [사내 용어] 충돌 — '{key}': 사내 '{norm}' vs "
                                    f"국가 표준 '{std}' → 국가 표준 우선")
                                break
                            josa = w[len(key):] if w.startswith(key) else ""
                            corrected = norm + josa
                            if w != corrected and w not in existing:
                                info = _ud.pair_info(key) or {}
                                rid = info.get("rule_id") or ""
                                cat = info.get("category") or "사내용어"
                                net.append(Correction(
                                    original=w, corrected=corrected,
                                    reason=(f"[사내 용어] '{key}'는 사내 표준 표기 '{norm}' 권장"
                                            + (f" ({rid})" if rid else "")),
                                    source="dict", color=HL_DICT,
                                    category=cat, confidence="high",
                                ))
                                existing.add(w)
                            break
                    if net:
                        merged.extend(net)
                        det_auto.append(("사내용어", len(net)))
                    if conflicts:
                        log(f"  → 사내 용어 충돌 {conflicts}건은 국가 표준 우선 적용")
            except Exception as e:
                log(f"  [사내 용어] 페어 스킵: {e}")

        # [5.7] 규범표기 정규화 — 우리말샘 '규범 표기' 사전(norm_map)으로 비표준
        #     표기를 결정론적으로 교정한다(컨텐츠→콘텐츠, 수퍼마켓→슈퍼마켓,
        #     초콜렛→초콜릿 …). 비표준형도 사전엔 '등재'라 ②재검증·⑤안전망이 못 잡던
        #     사각지대를, 사전 사실에 근거해 high confidence로 메운다(설계: 사전=인프라).
        #     norm_map 테이블이 없는 구버전 DB에선 batch가 {}라 자동 비활성(graceful).
        if not self._stop.is_set():
            try:
                import nikl_dict as _nd
                from core.consistency_pass import _strip_josa
                from core.josa_rules import reconcile_josa as _rj
                doc_eojeols = set(re.findall(r"[가-힣]+", text))
                bases = set(doc_eojeols)
                for w in doc_eojeols:
                    b = _strip_josa(w)
                    if b and len(b) >= 2:
                        bases.add(b)
                norm_map = _nd.batch_lookup_norm(bases)
                if norm_map:
                    existing = {c.original for c in merged}
                    net = []
                    for w in doc_eojeols:
                        # 어절 자체 → 없으면 조사 제거형 순으로 매핑 조회
                        for key in (w, _strip_josa(w)):
                            norm = norm_map.get(key)
                            if not norm or norm == key:
                                continue
                            # 동형이의어 오매칭 차단(구글→귀글, 동기와→너새 등) — 빈출 강등으로도
                            #   못 막는 '검수 카드 자체가 오답'인 부류를 결정론 가드로 통째 제외.
                            if _nd.is_homograph_norm_key(key):
                                continue
                            # 등재 복합어 성분 가드 — key가 이 문서의 더 긴 등재 표제어의
                            #   부분이면(티어 ⊂ 톱티어) 규범표기 치환 보류(외래어 오교정 방지).
                            if _nd.is_registered_compound_component(key, doc_eojeols):
                                continue
                            # 용언 활용형 동형이의 가드 — '나올'(나오+ㄹ)이 명사 표제어
                            #   '나올(羅兀)→너울'로 오매칭되는 부류(짚고→집고 등 위험군
                            #   377건 실측). 문맥상 표준 용언의 활용형이면 치환 보류.
                            if _nd.is_verb_inflection_homograph(key, w, text):
                                continue
                            # 받침이 바뀌면 뒤 조사도 호응시킨다(스윕과→스위프와).
                            josa = _rj(norm, w[len(key):]) if w.startswith(key) else ""
                            corrected = norm + josa
                            if w != corrected and w not in existing:
                                # 저자가 비표준형을 ≥_FREQ_INTENTIONAL회 반복하면 의도된 표기일
                                #   수 있다 → 자동적용(high) 대신 검수 카드(low)로 강등해 편집자가
                                #   판단(스윕 8회 사례, 사용자 결정). 사전 사실은 그대로 노출(완전
                                #   억제 아님). 빈도는 base 부분문자열 카운트(②와 동일 방식).
                                freq = text.count(key)
                                repeated = freq >= _FREQ_INTENTIONAL
                                reason = f"[규범표기] '{key}'는 비표준 — 규범 표기 '{norm}' 권장"
                                if repeated:
                                    reason += f" (저자 {freq}회 반복 — 검수 후 결정)"
                                net.append(Correction(
                                    original=w, corrected=corrected,
                                    reason=reason,
                                    source="dict", color=HL_DICT,
                                    category="규범표기",
                                    confidence="low" if repeated else "high",
                                ))
                                existing.add(w)
                            break
                    if net:
                        merged.extend(net)
                        _n_lo = sum(1 for c in net if c.confidence == "low")
                        if len(net) - _n_lo:
                            det_auto.append(("규범표기", len(net) - _n_lo))
                        if _n_lo:
                            det_review.append(("규범표기(빈출)", _n_lo))
            except Exception as e:
                log(f"  [규범표기] 정규화 스킵: {e}")

        # [5.8] 어문 규범 결정론 페어 — eomun.db(core.eomun_rules)의 검증된 비표준→규범
        #     매핑(설계 역할 B). norm_map과 동일 경로·형상이며 동형이의어 가드는 빌드타임에
        #     적용됨. 실증상 외래어·표준어 비표준형은 norm_map이 이미 커버하므로(B는 norm_map에
        #     양보) 여기선 그 사각의 소수 페어(예: 플랫홈→플랫폼)만 더한다. 근거 조항(rule_id)을
        #     reason에 인용한다. eomun.db 없으면 batch가 {}라 자동 비활성(graceful).
        if not self._stop.is_set():
            try:
                from core import eomun_rules as _er
                import nikl_dict as _nd
                from core.consistency_pass import _strip_josa
                from core.josa_rules import reconcile_josa as _rj
                doc_eojeols = set(re.findall(r"[가-힣]+", text))
                bases = set(doc_eojeols)
                for w in doc_eojeols:
                    b = _strip_josa(w)
                    if b and len(b) >= 2:
                        bases.add(b)
                pair_map = _er.batch_lookup_eomun_pair(bases)
                if pair_map:
                    existing = {c.original for c in merged}
                    net = []
                    for w in doc_eojeols:
                        for key in (w, _strip_josa(w)):
                            norm = pair_map.get(key)
                            if not norm or norm == key:
                                continue
                            # 동형이의어 오매칭 차단(norm_map과 동일 — 구글/동기와류).
                            if _nd.is_homograph_norm_key(key):
                                continue
                            # 등재 복합어 성분 가드(norm_map [5.7]과 동일 — 티어 ⊂ 톱티어).
                            if _nd.is_registered_compound_component(key, doc_eojeols):
                                continue
                            # 용언 활용형 동형이의 가드(norm_map [5.7]과 동일 — 나올/짚고류).
                            if _nd.is_verb_inflection_homograph(key, w, text):
                                continue
                            # 받침이 바뀌면 뒤 조사도 호응시킨다(스윕과→스위프와).
                            josa = _rj(norm, w[len(key):]) if w.startswith(key) else ""
                            corrected = norm + josa
                            if w != corrected and w not in existing:
                                rid = _er.pair_rule_id(key) or ""
                                # 빈출(≥_FREQ_INTENTIONAL회) 비표준형은 검수 카드(low)로 강등 — [5.7]과 동일.
                                freq = text.count(key)
                                repeated = freq >= _FREQ_INTENTIONAL
                                reason = (f"[규범표기] '{key}'는 비표준 — 규범 표기 '{norm}' 권장"
                                          + (f" ({rid})" if rid else ""))
                                if repeated:
                                    reason += f" (저자 {freq}회 반복 — 검수 후 결정)"
                                net.append(Correction(
                                    original=w, corrected=corrected,
                                    reason=reason,
                                    source="dict", color=HL_DICT,
                                    category="규범표기",
                                    confidence="low" if repeated else "high",
                                ))
                                existing.add(w)
                            break
                    if net:
                        merged.extend(net)
                        _n_lo = sum(1 for c in net if c.confidence == "low")
                        if len(net) - _n_lo:
                            det_auto.append(("어문규범", len(net) - _n_lo))
                        if _n_lo:
                            det_review.append(("어문규범(빈출)", _n_lo))
            except Exception as e:
                log(f"  [규범표기] 어문 규범 페어 스킵: {e}")

        # [5.8.5] 활용 어간 결정론 맞춤법 — '아니였으며'→'아니었으며'처럼 **활용 어간** 오류는
        #     norm_map/eomun_pairs(표제어 수준·정답형 사전 등재 가드)에 담기지 않는다(올바른
        #     활용형은 표제어가 아니라 guard에 폐기됨). core.spelling_pairs가 비표준 어간을
        #     부분문자열 치환으로 보완한다(거짓양성 0인 어간만 등재). 결정론·예외 없음 → high.
        if not self._stop.is_set():
            try:
                from core import spelling_pairs as _sp
                existing = {c.original for c in merged}
                net = []
                for orig, fixed, why in _sp.find_spelling_fixes(text):
                    if orig in existing:
                        continue
                    net.append(Correction(
                        original=orig, corrected=fixed,
                        reason=f"[맞춤법] {why}",
                        source="dict", color=HL_DICT,
                        category="맞춤법", confidence="high",
                    ))
                    existing.add(orig)
                if net:
                    merged.extend(net)
                    det_auto.append(("맞춤법", len(net)))
            except Exception as e:
                log(f"  [맞춤법] 활용 어간 페어 스킵: {e}")

        # [5.9] 받침 호응 조사 — 괄호 설명 뒤 조사를 괄호 앞 체언의 받침에 호응시킨다
        #     (영상(15초, 30초)는 → 영상(15초, 30초)은). 받침 규칙은 결정론이라 high.
        if not self._stop.is_set():
            try:
                from core import josa_rules as _jr
                existing = {c.original for c in merged}
                net = []
                # AI(ai_typo)가 같은 조사 지점을 이미 교정했으면 스킵 — AI는 격 변경
                #   ('가'→'을')까지 판단하고 받침 형태는 fix_paren_josa_agreement가 이미
                #   보정했으므로, 받침만 맞추는 이 카드가 겹치면 충돌(검수 겹침 shadow·
                #   자동 적용 실패 항목)만 남긴다(사용자 보고 2026-07-06 '가시성(visibility)가').
                #   판정: 이 카드의 조사 위치(원문 끝 글자)가 AI 원문 span 안에 있는가.
                _ai_spans = []
                for _c in merged:
                    if _c.source == "ai_typo" and _c.original:
                        _p = text.find(_c.original)
                        if _p >= 0:
                            _ai_spans.append((_p, _p + len(_c.original)))
                for orig, fixed in _jr.find_paren_josa(text):
                    if orig in existing:
                        continue
                    _p = text.find(orig)
                    if _p >= 0 and _ai_spans:
                        _j = _p + len(orig) - 1     # 조사(마지막 글자) 위치
                        if any(s <= _j < e for s, e in _ai_spans):
                            continue
                    noun = orig.split("(", 1)[0].rstrip()
                    net.append(Correction(
                        original=orig, corrected=fixed,
                        reason=f"[맞춤법] 괄호 앞 체언 '{noun}'의 받침에 조사를 호응",
                        source="dict", color=HL_DICT,
                        category="맞춤법", confidence="high",
                    ))
                    existing.add(orig)
                # 괄호 없는 일반 어절의 받침 호응 조사 — '필드을'→'필드를'(필드 받침 없음 → 를).
                #   kiwi 가드(앞 체언·어미 제외)로 '있는'·'가을' 등 거짓양성을 막는다(실문서 0 검증).
                for orig, fixed in _jr.find_batchim_josa(text):
                    if orig in existing:
                        continue
                    net.append(Correction(
                        original=orig, corrected=fixed,
                        reason="[맞춤법] 체언 받침에 조사를 호응 (받침 없음→를/는/가/와)",
                        source="dict", color=HL_DICT,
                        category="맞춤법", confidence="high",
                    ))
                    existing.add(orig)
                # 단독으로 떨어진 조사 붙이기 — '수요 를'→'수요를', '세종 으로'→'세종으로'.
                #   '을/를/으로'는 단독 단어가 될 수 없어 떨어져 있으면 100% 붙여야 할
                #   조사다(미탐 보고 2회 — 수요 를 2026-06-30, 세종 으로 2026-07-03).
                #   '으로'는 앞 체언 받침에 호응(학교 으로→학교로). 결정론 → high.
                for orig, fixed in _jr.find_orphan_josa(text):
                    if orig in existing:
                        continue
                    net.append(Correction(
                        original=orig, corrected=fixed,
                        reason="[맞춤법] 홀로 떨어진 조사('을/를/으로')는 앞 체언에 붙여 씀",
                        source="dict", color=HL_DICT,
                        category="맞춤법", confidence="high",
                    ))
                    existing.add(orig)
                # 공동격 조사 중복('…과 와 지역') — 앞 어절이 이미 과/와(조사)로 끝나는데
                #   단독 '와/과' 어절이 또 옴 → 삭제 제안. AI가 비결정적으로 잡던 유형의
                #   결정론 백스톱(2026-07-02). 삭제 교정이라 저신뢰 '검수 카드'로만 노출.
                net_dup = []
                for orig, fixed in _jr.find_duplicate_comitative_josa(text):
                    if orig in existing:
                        continue
                    net_dup.append(Correction(
                        original=orig, corrected=fixed,
                        reason="[검수] 공동격 조사 중복 의심 — 앞 어절이 이미 '과/와'로 끝남"
                               " (중복 조사 삭제 제안, 검토 필요)",
                        source="dict", color=HL_DICT,
                        category="맞춤법", confidence="low",
                    ))
                    existing.add(orig)
                if net:
                    merged.extend(net)
                    det_auto.append(("받침조사", len(net)))
                if net_dup:
                    merged.extend(net_dup)
                    det_review.append(("조사중복", len(net_dup)))
            except Exception as e:
                log(f"  [조사] 받침 호응 스킵: {e}")

        # [5.10] 괄호 짝 맞추기 — 여는/닫는 괄호 한쪽이 빠진 묶음표를 보완한다
        #     (리플렛(외로움안녕 → …) / 고립예방센터) → (고립예방센터)). 글머리표
        #     라벨('예)·1)·가)')은 의도된 표기라 제외. 괄호 한 짝만 삽입(글자 불변=환각 0)
        #     하지만 '어디에' 넣을지는 휴리스틱이라 저신뢰 '검수 카드'로만 노출(자동수정 아님).
        if not self._stop.is_set():
            try:
                from core import bracket_rules as _br
                existing = {c.original for c in merged}
                net = []
                for orig, fixed, why in _br.find_unbalanced_brackets(text):
                    if orig in existing:
                        continue
                    net.append(Correction(
                        original=orig, corrected=fixed,
                        reason=f"[검수] {why}",
                        source="punct", color=HL_DICT,
                        category="문장부호", confidence="low",
                    ))
                    existing.add(orig)
                if net:
                    merged.extend(net)
                    det_review.append(("괄호", len(net)))
                # 따옴표 짝 맞추기 — 짝 없는 닫는 따옴표에 여는 짝 보완
                #   ('…천인계획(千人計劃)'과' → ''천인계획(千人計劃)'과', 미탐 보고 2026-07-03).
                #   괄호와 동일 패턴(삽입만·위치 휴리스틱) → 저신뢰 검수 카드.
                from core import quote_rules as _qr
                net_q = []
                for orig, fixed, why in _qr.find_unpaired_quotes(text):
                    if orig in existing:
                        continue
                    net_q.append(Correction(
                        original=orig, corrected=fixed,
                        reason=f"[검수] {why}",
                        source="punct", color=HL_DICT,
                        category="문장부호", confidence="low",
                    ))
                    existing.add(orig)
                if net_q:
                    merged.extend(net_q)
                    det_review.append(("따옴표", len(net_q)))
            except Exception as e:
                log(f"  [문장부호] 괄호·따옴표 짝 맞추기 스킵: {e}")

        # [6] 사전 안전망 — AI(선택 레이어)가 손대지 않은 미등재어를 '검수 필요'
        #     카드로 노출한다. 사전 탐지가 기본 베이스이므로, AI가 미묘한 오타
        #     (예: '상담채녈')를 놓쳐도 반드시 사용자 눈에 띈다. 자동수정이 아니라
        #     (original==corrected) → HWP 미수정, 검토 카드·정오표용.
        if dict_flags and not self._stop.is_set():
            # 윤문(ai_polish)은 original이 문장 전체라 한 문장 내 모든 어휘를
            # '처리됨'으로 덮어 안전망을 과하게 억제한다 → 단어 단위 교정만 집계.
            covered = set()
            for c in merged:
                if c.source == "ai_polish":
                    continue
                covered.update(re.findall(r"[가-힣]+", c.original))
            net = []
            skipped_noise = 0
            # 조직 예외(무교정 화이트리스트, scope='all') — 조직이 승인한 표기는 비등재라도
            #   '검수 필요' 카드로 띄우지 않는다(설계 E ⑤). DB 부재/빈 스냅샷이면 빈 집합 → 무영향.
            try:
                from core import userdict as _ud
                _ud_exc = _ud.exception_set("all")
            except Exception:
                _ud_exc = frozenset()
            excepted = 0
            skipped_freq = 0
            # 빈도 가드 — 문서에서 여러 번 반복되는 미등재어는 작가 의도 용어
            #   (외래어·전문용어·고유명사·브랜드명)일 확률이 압도적이다(사용자 보고
            #   #2: '바이오' 16회). 진짜 오탈자는 보통 1~2회에 그치고, 빈출어는 AI가
            #   여러 번 마주쳐 이미 검토했을 가능성이 높다 — 즉 안전망(⑤)의 실효는
            #   '드물게 등장해 AI가 놓치기 쉬운' 어휘에 있다. 빈출 미등재어를 검수
            #   카드에서 빼 카드 신뢰도를 지킨다(자동수정 무관 — 카드만 안 띄움).
            #   ⚠ AI 후보 추출(extract_suspicious_words)은 그대로 — AI는 빈출어도 본다.
            #   부분문자열 카운트라 '바이오'가 '바이오매스' 안에 들어도 같은 용어로 집계.
            #   조사를 떼어 base로 세서 '열산화안정성이'(조사형 1회)도 base '열산화안정성'
            #   (4회)으로 묶여 같은 용어로 억제된다(조사변형 누수 방지).
            #   임계치는 모듈 상수 _FREQ_INTENTIONAL(규범표기 강등 [5.7]/[5.8]과 공유).
            try:
                from core.consistency_pass import _strip_josa as _freq_strip
            except Exception:
                _freq_strip = lambda w: w
            for word, clean, reason in dict_flags:
                # AI가 이 어휘를 이미 교정 대상으로 다뤘으면(부분 포함 포함) 스킵
                if any(clean in cov or cov in clean for cov in covered):
                    continue
                # 문서에서 반복되는 미등재어 → 작가 의도 용어로 보고 카드 제외
                _fbase = _freq_strip(clean) or clean
                if clean and max(text.count(clean), text.count(_fbase)) >= _FREQ_INTENTIONAL:
                    skipped_freq += 1
                    continue
                # 따옴표로 영문과 붙은 한글 오타(예: 캐나가"Say)는 혼합 토큰이라 필터가
                #   통째로 제외했다 → **따옴표가 섞인 경우에만** 한글 런만 떼어 검사·표시한다.
                #   (숫자·괄호 결합 '1인가구'·'콘텐츠(키메세지)'는 따옴표가 없어 무영향 = 무회귀.)
                flag_word = word
                if re.search(r"[\"“”'‘’]", word):
                    runs = re.findall(r"[가-힣]+", word)
                    flag_word = max(runs, key=len) if runs else word
                # ★ 등재어 오플래그 근절 — 최종 사전 재확인(불변식: 사전에 있는 단어는
                #   절대 '어느 사전에도 없음' 카드가 되지 않는다). clean이 은닉문자·정규화
                #   경합·인코딩 이상으로 깨져 미등재로 잘못 판정돼도, flag_word의 NFC 표제어
                #   (또는 그 활용형)가 사전에 있으면 카드를 만들지 않는다. 원인 진단을 위해
                #   flag_word≠clean일 때 코드포인트를 로그로 남긴다(사용자 보고: 등재어
                #   '상용화'가 미등재로 플래그 — NFD 단독으론 재현 불가, 문자 이상 추적용).
                _fw_nfc = unicodedata.normalize("NFC", flag_word)
                _fw_real = lookup_word(_fw_nfc)["exists"]
                if not _fw_real:
                    try:
                        from core import morph as _mchk
                        _fw_real = _mchk.available() and _mchk.is_known_form(
                            _fw_nfc, lambda x: lookup_word(x)["exists"])
                    except Exception:
                        _fw_real = False
                if _fw_real:
                    if re.sub(r"[^가-힣]", "", flag_word) != _fw_nfc:
                        _cps = " ".join(f"U+{ord(ch):04X}" for ch in flag_word)
                        log(f"  ⚠ 사전 등재어 오플래그 억제: {flag_word!r} "
                            f"(clean={clean!r}, NFC={_fw_nfc!r}, 코드포인트=[{_cps}])")
                    skipped_noise += 1
                    continue
                # 조직 예외(무교정 화이트리스트) → 검수 카드 억제(설계 E ⑤)
                if _ud_exc and (clean in _ud_exc
                                or re.sub(r"[^가-힣]", "", flag_word) in _ud_exc):
                    excepted += 1
                    continue
                # 보수적 노이즈 필터 — 고유명사·외래어·정상 복합어는 검수 카드에서 제외.
                #   (진짜 오타만 남김. 자동수정이 아니므로 놓쳐도 AI 교정엔 영향 없음)
                if len(re.sub(r"[^가-힣]", "", flag_word)) < 2 or not is_likely_typo(flag_word):
                    skipped_noise += 1
                    continue
                net.append(_make_flag(flag_word, reason))
            if skipped_freq:
                log(f"  → 빈출 미등재어 {skipped_freq}건 검수 카드 제외 "
                    f"(반복 {_FREQ_INTENTIONAL}회+ = 작가 의도 용어로 판단)")
            if net:
                merged.extend(net)
                det_review.append(("사전안전망", len(net)))

        # [7] 띄어쓰기 백스톱 — AI가 놓친 '붙여쓰기→띄어쓰기' 누락을 kiwi 자동
        #     띄어쓰기로 재산출해 '검수 필요'(저신뢰) 카드로 노출한다. kiwi.space는
        #     공백만 삽입하고 글자는 바꾸지 않아(환각 0) 과교정 위험이 없다. 자동수정이
        #     아니라 사람 검토용(confidence=low → auto_apply 시 자동 제외, 정오표엔 '검수').
        #     설계 ⑤ 사전 안전망과 같은 패턴(AI가 놓친 것을 검수로 노출).
        if opts.get("scope_spacing", True) and not self._stop.is_set():
            try:
                from core import morph as _morph
                sp_cards = []
                existing = {c.original for c in merged}   # 중복 카드 방지(kiwi·부호 공용)
                if _morph.available():
                    from core.consistency_pass import _strip_josa
                    import nikl_dict as _nd
                    _dict_ok = bool(validator and validator.available)
                    # 조직 예외(scope='spacing') — 사내 통일 띄어쓰기 표기는 분리/통일
                    #   제안에서 제외(설계 E ⑦, 예: '매출액' 붙여쓰기). 빈 스냅샷이면 무영향.
                    try:
                        from core import userdict as _ud
                        _ud_sp_exc = _ud.exception_set("spacing")
                    except Exception:
                        _ud_sp_exc = frozenset()
                    # AI가 이미 다룬 어절은 제외(중복 카드 방지). ⚠ **방향이 중요**:
                    #   `clean in cov`(어절이 더 긴 AI 교정 구간의 부분)일 때만 스킵한다.
                    #   반대 방향 `cov in clean`(AI 단어가 어절의 부분)은 쓰지 않는다 —
                    #   한 글자 런('는')이나 접두 일치('리플렛'⊂'리플렛등', norm '리플렛→리플릿'
                    #   때문에 covered에 듦)가 **별개의 띄어쓰기 교정을 통째로 삼키는** 과억제를
                    #   냈다(실측: '하는생각으로'·'리플렛등' 미탐). 띄어쓰기는 글자를 안 바꾸므로
                    #   접두가 겹쳐도 별도 카드로 두는 게 맞다.
                    covered = set()
                    for c in merged:
                        if c.source == "ai_polish":
                            continue
                        covered.update(re.findall(r"[가-힣]+", c.original))
                    for eojeol, spaced in _morph.find_spacing_suggestions(text):
                        if eojeol in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", eojeol)
                        if any(clean in cov for cov in covered):  # 어절⊆AI구간만 스킵
                            continue
                        # 사내 통일 띄어쓰기 예외 → 분리 제안 억제(설계 E ⑦)
                        if _ud_sp_exc and (clean in _ud_sp_exc
                                           or _strip_josa(eojeol) in _ud_sp_exc):
                            continue
                        # 정상 복합어(사전 등재어)면 분리 제안하지 않음 — 노이즈 억제
                        if _dict_ok:
                            base = _strip_josa(eojeol)
                            if (_nd.lookup_word(clean)["exists"]
                                    or (base and _nd.lookup_word(base)["exists"])):
                                continue
                        sp_cards.append(Correction(
                            original=eojeol, corrected=spaced,
                            reason="[검수] 띄어쓰기 누락 의심 — 자동 띄어쓰기 제안(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(eojeol)

                    # 체언+엄선 의존명사(등/말 등) — '리플릿등'→'리플릿 등', '9월말'→'9월 말'.
                    #   ⚠ 위 '복합어 사전 등재 가드'를 적용하지 않는다. clean에서 숫자를
                    #   떼면 '월말'·'월초'가 등재어로 잡혀 정작 '9월 말'을 못 띄우기 때문.
                    #   화이트리스트(_ENUM_DEP/_TIME_DEP) + 꼬리 경계 검사가 자체 검증이다.
                    for eojeol, spaced in _morph.find_dependent_noun_spacing(text):
                        if eojeol in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", eojeol)
                        if any(clean in cov for cov in covered):  # 어절⊆AI구간만 스킵(위 [7] 주석)
                            continue
                        # '차등'(差等)·'고등'처럼 **전체가 등재 표제어**인데 kiwi가 '차+등(의존명사)'
                        #   으로 과분해한 경우는 띄우지 않는다('차등'→'차 등' 오교정, 사용자 보고).
                        #   ⚠ 숫자 포함('9월말')엔 dict 가드를 적용 안 함 — clean에서 숫자를 떼면
                        #   '월말'이 등재어라 정작 '9월 말'을 막는다(화이트리스트+꼬리경계가 자체 검증).
                        if (_dict_ok and not re.search(r"\d", eojeol)
                                and _nd.lookup_word(clean)["exists"]):
                            continue
                        if _ud_sp_exc and (clean in _ud_sp_exc
                                           or _strip_josa(eojeol) in _ud_sp_exc):
                            continue
                        sp_cards.append(Correction(
                            original=eojeol, corrected=spaced,
                            reason="[검수] 의존명사 띄어쓰기 — 체언 뒤 '등·말' 등은 띄어 씀(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(eojeol)

                    # 닫는 기호 뒤 체언 띄어쓰기 — '‘표준’규칙'→'‘표준’ 규칙', '(센터)운영'→
                    #   '(센터) 운영'. 기호 뒤 조사/용언은 후보 아님(붙임 유지). (주)·(1) 약어 제외.
                    for eojeol, spaced in _morph.find_symbol_noun_spacing(text):
                        if eojeol in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", eojeol)
                        if any(clean in cov for cov in covered):
                            continue
                        sp_cards.append(Correction(
                            original=eojeol, corrected=spaced,
                            reason="[검수] 기호 뒤 띄어쓰기 — 닫는 기호(’\")]) 뒤 명사는 띄어 씀(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(eojeol)

                    # 보조용언 '-어야 하다/되다' 띄어쓰기 — '···해야한다'→'···해야 한다'.
                    #   AI가 청크별로 한 곳은 잡고 다른 곳은 놓치는 일관성 미탐(사용자 보고:
                    #   협력해야한다 교정·고려해야한다 미탐)을 결정론 백스톱으로 전 등장 일관 처리.
                    #   kiwi 형태소 경계라 공백 위치가 확정(글자 불변·환각 0)이고, 한글 맞춤법
                    #   제47항상 '-어야'는 붙여쓰기 미허용이라 문법상 확정적 → **high(자동 적용)**.
                    #   (사용자 결정: AI가 잡은 협력해야한다처럼 두 모드 모두 일관되게 적용)
                    for eojeol, spaced in _morph.find_auxiliary_verb_spacing(text):
                        if eojeol in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", eojeol)
                        # ⚠ 정확 일치만 스킵(부분문자열 금지). 보조용언 어절('해야한다')은
                        #   더 긴 AI 교정어('협력해야한다')의 부분문자열이라, 과거 부분문자열
                        #   스킵(any clean in cov)은 '재정의 해야한다'·'있도록 해야한다'의
                        #   standalone '해야한다' 카드를 통째로 잘못 억제했다(사용자 보고 미탐).
                        if clean in covered:
                            continue
                        sp_cards.append(Correction(
                            original=eojeol, corrected=spaced,
                            reason="보조용언 띄어쓰기 — '···해야 한다/된다'는 띄어 씀이 원칙(한글 맞춤법 제47항)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="high",
                        ))
                        existing.add(eojeol)

                    # 본용언 '-아/-어' + 보조용언(주/보/나가 등) 띄어쓰기 — '보상해주는'→
                    #   '보상해 주는'. 제47항상 띄어 씀이 원칙이나 붙여 씀도 허용 → **저신뢰
                    #   검수 카드**(자동수정 아님). '-어지다' 피동·'예뻐하다'의 '하'는 화이트
                    #   리스트에서 빠져 분리 안 됨(실문서 거짓양성 0 확인).
                    for eojeol, spaced in _morph.find_aux_connective_spacing(text):
                        if eojeol in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", eojeol)
                        if clean in covered:  # 정확 일치만 스킵(부분문자열 금지 — 위 [7] 주석)
                            continue
                        sp_cards.append(Correction(
                            original=eojeol, corrected=spaced,
                            reason="[검수] 보조용언 띄어쓰기 — 본용언+보조용언은 띄어 씀이 원칙(붙임도 허용, 검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(eojeol)

                    # 피동 '-어지다' 붙여쓰기 — '이루어 졌다'→'이루어졌다'(한 단어). AI가 같은
                    #   문서에서 '이루어 졌다'는 붙이고 '이루어 졌고'는 놓치는 비결정성을 결정론
                    #   백스톱으로 전 등장 일관 붙임. '-아/-어 + 피동 지'로 좁혀 거짓양성 0(실문서
                    #   검증). 문법상 한 단어라 **high(자동 적용)**. original이 '이루어 졌고'(공백
                    #   포함)라 dedup은 그 전체 문자열로 본다(clean은 공백 무시라 부적합).
                    for orig, joined in _morph.find_eojida_join(text):
                        if orig in existing:
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=joined,
                            reason="피동 '-어지다'는 한 단어이므로 붙여 씀(예: 이루어지다)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="high",
                        ))
                        existing.add(orig)

                    # 복합명사 띄어쓰기 일관성 — 실무 교정 관행: 복합명사는 옳고 그름을
                    #   따지기보다 저자가 더 자주 쓴 표기로 통일한다(예: '정책보고서'(다수)와
                    #   '정책 보고서'(소수) 혼재 → 소수형을 다수형으로). 복합명사 '분리'는 위
                    #   POS 백스톱에서 제외했고, 여기선 혼재만 다수결로 정리한다.
                    consistency_n = 0
                    # 동률(1:1 등)도 검수 카드로 표출(사용자 정책 결정 2026-07-02) — 방향은
                    #   사전 기반 규범 기본값(등재 복합어=붙임 / 미등재=띄어쓰기 원칙).
                    _cons_exists = (lambda w: _nd.lookup_word(w)["exists"]) if _dict_ok else None
                    for minority, majority, n_min, n_maj in \
                            _morph.find_compound_spacing_consistency(text, exists_fn=_cons_exists):
                        if minority in existing:
                            continue
                        clean = re.sub(r"[^가-힣]", "", minority)
                        if any(clean in cov for cov in covered):  # 어절⊆AI구간만 스킵(위 [7] 주석)
                            continue
                        # 사내 통일 띄어쓰기 예외 → 다수표기 통일 제안에서도 제외(설계 E ⑦)
                        if _ud_sp_exc and clean in _ud_sp_exc:
                            continue
                        # 다수 표기가 명확하면 high(자동 적용), 근소한 차이·동률은 low(검토).
                        tie = (n_min == n_maj)
                        close = tie or (n_maj - n_min <= 1 and n_min >= 2)
                        conf = "low" if close else "high"
                        if tie:
                            reason = (f"[검수] 띄어쓰기 일관성 — '{majority}'({n_maj}회)와 "
                                      f"'{minority}'({n_min}회)가 동률 혼재 → 원칙 방향"
                                      f"('{majority}') 제안(검토 필요)")
                        else:
                            tag = "[검수] " if close else ""
                            reason = (f"{tag}띄어쓰기 일관성 — 문서에서 '{majority}'({n_maj}회)가 "
                                      f"'{minority}'({n_min}회)보다 우세 → 다수 표기로 통일")
                        sp_cards.append(Correction(
                            original=minority, corrected=majority,
                            reason=reason,
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence=conf,
                            # 통일 방향은 편집 선택 — 검수 패널이 '반대 표기로
                            #   통일'(다수→소수 역방향) 액션을 제공할 수 있게 표시.
                            consistency_flip=True,
                        ))
                        existing.add(minority)
                        consistency_n += 1
                    # (복합명사 일관성 건수는 아래 '띄어쓰기 백스톱' 합계에 포함)

                # 부호·영문 띄어쓰기(규칙 기반) — kiwi가 못 잡는 문장부호/스크립트
                #   경계(예: 'life.Let's'→'life. Let's', '가"Say'→'가 "Say'). 보수적
                #   규칙 + 약어·소수점·URL 예외. 한글 불필요(영문 구간 포함) → 별도 수집.
                try:
                    from core import spacing_rules as _sr
                    for orig, fixed in _sr.find_punct_spacing(text):
                        if orig in existing:
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=fixed,
                            reason="[검수] 문장부호·영문 띄어쓰기 누락 의심 — 검토 필요",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(orig)
                    # 인용부호 띄어쓰기 정규화 — 여는따옴표 앞 띄움 + 닫는따옴표 뒤 조사 붙임
                    #   (국립국어원'맞춤법규칙' 에 → 국립국어원 '맞춤법규칙'에). 공백만 가감(환각 0).
                    for orig, fixed in _sr.find_quote_spacing(text):
                        if orig in existing:
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=fixed,
                            reason="[검수] 인용부호 띄어쓰기 — 여는따옴표 앞 띄움/조사 붙임 제안(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(orig)
                    # 문장부호↔여는 따옴표 띄어쓰기 — 부호 뒤 여는 따옴표 띄움 + 여는
                    #   따옴표 뒤 공백 제거("있다.'천인계획'"→"있다. '천인계획'",
                    #   ',“ Artificial'→', “Artificial' — 미탐 보고 2026-07-03). 공백만 가감.
                    from core import quote_rules as _qr
                    for orig, fixed in _qr.find_quote_punct_spacing(text):
                        if orig in existing:
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=fixed,
                            reason="[검수] 부호·따옴표 띄어쓰기 — 문장부호 뒤 여는 따옴표는"
                                   " 띄고, 여는 따옴표는 뒤 내용에 붙임(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(orig)
                    # 종결어미 뒤 인용·보충 괄호 붙임 — '있다 (경향신문'→'있다(경향신문'
                    #   (괄호는 앞말에 붙여 씀 — 미탐 보고 2026-07-03). kiwi EF 가드로
                    #   표 머리 'A기업 (…)'류 체언+괄호 간격은 건드리지 않음. 공백만 제거.
                    from core import bracket_rules as _br_at
                    for orig, fixed in _br_at.find_paren_attach(text):
                        if orig in existing:
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=fixed,
                            reason="[검수] 괄호 붙임 — 인용·보충 괄호는 앞말에 붙여 씀(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(orig)
                    # 숫자 단위 띄어쓰기 — '13.6억원'→'13.6억 원'(큰수단위 뒤 통화 '원').
                    #   AI가 청크별로 일부만 잡는 유형이라 결정론으로 모든 등장 보강.
                    #   ⚠ AI가 이미 다룬 어절과 겹치면 스킵(원문 substring 대조 — '12,9억원'류).
                    _ai_orig = [c.original for c in merged if c.original]
                    for orig, fixed in _sr.find_unit_spacing(text):
                        if orig in existing:
                            continue
                        if any(co in orig or orig in co for co in _ai_orig):
                            continue
                        sp_cards.append(Correction(
                            original=orig, corrected=fixed,
                            reason="[검수] 숫자 단위 띄어쓰기 — 큰수단위(억/만/조) 뒤 '원' 분리(검토 필요)",
                            source="spacing", color=HL_TYPO,
                            category="띄어쓰기", confidence="low",
                        ))
                        existing.add(orig)
                except Exception as e:
                    log(f"  [띄어쓰기] 부호 규칙 스킵: {e}")
                if sp_cards:
                    merged.extend(sp_cards)
                    # 띄어쓰기 카드는 신뢰도가 섞여 있다(저신뢰 검수 백스톱 +
                    #   high 자동 적용: 보조용언 '해야 한다', 다수표기 통일). 요약 로그
                    #   버킷을 신뢰도로 갈라 '자동 적용/검토 필요'를 정확히 집계한다.
                    n_auto_sp = sum(1 for c in sp_cards if c.confidence != "low")
                    n_rev_sp = len(sp_cards) - n_auto_sp
                    if n_auto_sp:
                        det_auto.append(("띄어쓰기", n_auto_sp))
                    if n_rev_sp:
                        det_review.append(("띄어쓰기", n_rev_sp))
            except Exception as e:
                log(f"  [띄어쓰기] 백스톱 스킵: {e}")

        # [8] 적용 정합성 — 접두 부분문자열로 이미 처리되는 조사 변형 교정 제거.
        #     bare형('뱃지'→'배지')과 조사형('뱃지를'→'배지를')이 공존하면 적용 단계에서
        #     등장 인덱스가 어긋나 '수락 등장 미적용·거절 등장 오적용' 버그가 난다
        #     (부분 거절 시 특히). 조사형은 bare형의 부분문자열 치환으로 동일 결과가
        #     나오므로 안전하게 제거해 단일 교정으로 통일한다(검수 패널 등장 집합 ↔
        #     브리지 RepeatFind 집합 일치 → 부분 거절까지 정확히 동작).
        if merged:
            try:
                from core.consistency_pass import drop_redundant_josa_variants
                merged = drop_redundant_josa_variants(merged, logger=log)
            except Exception as e:
                log(f"  [적용 정합성] 조사 변형 정리 스킵: {e}")

        # [9] 겹치는 교정 합성 — 같은 구간에 '철자(규범표기/맞춤법) 교정'과 '띄어쓰기/문장부호
        #     교정'이 공존하면 띄어쓰기 카드에 철자 교정을 합성해 **한 카드로** 보여준다.
        #     예) '리플렛등→리플렛 등'(띄어쓰기·low) + '리플렛→리플릿'(규범표기·high)
        #        ⇒ '리플렛등→리플릿 등' 한 카드. 철자 카드는 다른 위치 단독 등장용으로 보존하며,
        #        적용은 '긴 원문 우선 정렬'(hwp_bridge_worker)+consumed 재분류로 이중치환 없이 정합.
        if merged:
            try:
                word_fixes = {}      # {오타/비표준 표기: 표준 표기} — 공백 없는 단어 단위 고신뢰
                for c in merged:
                    if (c.source == "dict" and c.confidence == "high"
                            and c.category in ("규범표기", "맞춤법", "표준어", "외래어")
                            and " " not in c.original and len(c.original) >= 2
                            and c.original != c.corrected):
                        word_fixes[c.original] = c.corrected
                n_compose = 0
                if word_fixes:
                    for c in merged:
                        if c.source not in ("spacing", "punct"):
                            continue
                        new_corr, applied = c.corrected, []
                        for orig, corr in word_fixes.items():
                            # 철자 오타가 이 카드의 원문·교정문 양쪽에 온전히 들어 있을 때만 합성
                            if orig in c.original and orig in new_corr:
                                composed = new_corr.replace(orig, corr)
                                if composed != new_corr:
                                    new_corr = composed
                                    applied.append((orig, corr))
                        if applied and new_corr != c.corrected:
                            tag = ", ".join(f"{o}→{n}" for o, n in applied)
                            c.corrected = new_corr
                            c.reason = (c.reason + f" + 규범표기 합성({tag})").strip()
                            n_compose += 1
                if n_compose:
                    log(f"  → 교정 합성: 철자+띄어쓰기/부호 겹침 {n_compose}건을 한 카드로 통합")
            except Exception as e:
                log(f"  [합성] 겹치는 교정 합성 스킵: {e}")

        # [9.5] 적용 가능성 검증 — '문서에서 찾을 수 있는 원문'만 카드로 내보낸다(불변식).
        #     실패 항목 근절(사용자 보고 2026-07-03 — 30.hwp 실패 33건 중 5건이 이 부류):
        #     (a) 추출 텍스트 대조 — AI가 원문을 재구성하며 줄나눔('필\n요'→'필 요')·
        #         가운뎃점('오픈소스·커뮤니티'→'오픈소스커뮤니티')을 정규화해 만든 **유령
        #         원문** 제거. 문서에 없는 원문은 적용이 100% 실패하고 미리보기 앵커도
        #         없어 카드 클릭 시 최상단으로 튄다.
        #     (b) 문서 실재 검증 — 추출 텍스트에는 있으나 문서에는 연속으로 존재하지 않는
        #         원문 제거('제시하였다.그러나' — 본문·각주 접합이나 책갈피·메모 앵커 등
        #         **보이지 않는 제어문자**가 원문 중간에 낌. GetText가 이를 삼켜 이어붙이나
        #         찾기/치환은 그 경계를 넘지 못함 — 실측 2026-07-03). 브리지 verify(RepeatFind,
        #         치환 없음·문서 무변경)로 걸러낸다. RepeatFind 도달 범위는 apply 1차 경로와
        #         동일(본문·표·글상자·각주 실측)이라 '검증 통과=적용 가능'이 성립한다.
        if merged and not self._stop.is_set():
            ghosts = [c for c in merged
                      if c.original != c.corrected and c.original not in text]
            if ghosts:
                ghost_ids = {id(c) for c in ghosts}
                merged = [c for c in merged if id(c) not in ghost_ids]
                log(f"  → 본문 대조: 추출 원문과 불일치한 교정 {len(ghosts)}건 제외 "
                    "(AI가 원문 재구성 중 문자를 바꿈 — 적용 불가)")
                for g in ghosts[:5]:
                    log(f"      · '{g.original}' → '{g.corrected}'")
            to_check = sorted({c.original for c in merged if c.original != c.corrected})
            if to_check and not self._stop.is_set():
                self.step_changed.emit("validate", "문서 대조 검증 중…")
                self.progress.emit(97, f"문서 대조 검증 중… ({len(to_check)}건)")
                try:
                    verifier = HwpEditor(self.file_path, logger=log)
                    try:
                        verifier.open()
                        found = verifier.verify_originals(to_check)
                    finally:
                        try:
                            verifier.close()
                        except Exception:
                            pass
                    missing = {o for o, ok in found.items() if not ok}
                    if missing:
                        n_before = len(merged)
                        merged = [c for c in merged
                                  if c.original == c.corrected or c.original not in missing]
                        log(f"  → 문서 대조: 문서에서 연속으로 찾을 수 없는 교정 "
                            f"{n_before - len(merged)}건 제외 (원문 중간에 각주·책갈피 등 "
                            "보이지 않는 조판 문자 — 자동 치환 불가, 해당 위치 원문 확인 권장)")
                        for o in sorted(missing)[:5]:
                            log(f"      · '{o}'")
                except Exception as e:
                    log(f"  [문서 대조] 검증 건너뜀: {e}")

        # 검증 구간 종료 → 결과 집계 구간 시작(활동 로그 단계 경계 마커).
        log("[사전+결정론규칙 검증 완료]")
        log("[분석+검증 결과 정리 시작]")

        # 결정론 보강 패스 결과를 2줄로 요약(자동 적용 / 검토 필요) — 개별 라인 통합.
        if det_auto:
            tot = sum(c for _, c in det_auto)
            log("  → 사전·규칙 자동 교정 "
                + " · ".join(f"{l} {c}" for l, c in det_auto if c) + f" = {tot}건")
        if det_review:
            tot = sum(c for _, c in det_review)
            log("  → 검수 카드(검토 필요) "
                + " · ".join(f"{l} {c}" for l, c in det_review if c) + f" = {tot}건")

        self.progress.emit(100, "분석 완료")

        # JSON 직렬화
        corrections_json = [
            {
                "id":        i + 1,
                "original":  c.original,
                "corrected": c.corrected,
                "reason":    c.reason,
                "source":    c.source,
                "color":     c.color,
                "category":  c.category,
                "confidence": c.confidence,
                "consistency_flip": c.consistency_flip,   # 검수 패널 '반대 표기로 통일'
                "status":    "pending",
            }
            for i, c in enumerate(merged)
        ]

        self.finished.emit(corrections_json)
