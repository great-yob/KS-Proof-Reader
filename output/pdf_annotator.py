"""
output/pdf_annotator.py — 한/글이 만든 PDF에 교정 주석 달기
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
결과물 축 `output_mode="pdf"`의 2단계. 1단계(한/글 → PDF)는 브리지가 하고,
여기서는 **원고를 건드리지 않고** PDF 위에만 주석을 얹는다.

★**교정 한 자리 = 주석 한 개.** `Highlight`(원문 위 형광) **하나**에 `/Contents`로
  원문→교정문·사유를 실어 보낸다 — 뷰어의 주석 목록에서 '텍스트 강조' 한 줄로 뜨고,
  그 줄을 누르면 내용이 보인다.
  ⚠ 예전엔 `Highlight` + `Text`(스티커) **두 개**를 같은 자리에 달았다. 형광은 눈에
  띄고 팝업은 읽히니 좋아 보였지만, 주석 목록에는 **한 교정이 두 줄**로 뜨고 290개
  목록이 실제 교정 145건을 뜻하게 된다(사용자 보고 2026-08-08). 형광 주석도
  `/Contents`를 가질 수 있으므로 스티커는 불필요한 중복이었다.

⚠ **FreeText(여백 상자형)를 쓰지 말 것** — 실측(2026-08-07): 상자만 그려지고 한글이
  **안 보인다**. 외형(appearance stream)을 파일이 직접 그려야 하는데 `/DA`가 Helv이고
  한글 폰트가 임베드돼 있지 않기 때문이다. 반면 `Highlight`/`Text`의 팝업은 **뷰어 UI가**
  그리므로 한글이 안전하다. 여백 상자가 꼭 필요해지면 한글 폰트 서브셋 임베딩이 선행돼야 한다.

⚠ **PyMuPDF(fitz)를 쓰지 말 것** — 검색·주석을 혼자 다 하지만 AGPL-3.0이다.
  여기서 쓰는 `pypdfium2`(BSD-3/Apache-2.0)·`pypdf`(BSD-3)만 배포 가능하다.

⚠ **PDF 물리 쪽 ≠ 정오표의 쪽(prnpageno).** prnpageno는 구역마다 새로 시작할 수 있어
  전역 단조도 유일도 아니다(실측: PageCount 17인데 문서 끝 prnpageno 18). 두 좌표계
  사이에 산술을 하지 않는다 — 이 모듈은 **PDF 물리 쪽만** 만들어 돌려주고,
  정오표는 그것을 별도 열('PDF 쪽')에 싣는다.

★이 모듈의 핵심 난제는 '등장 인덱스 정합'이다 — §_align_occurrences 주석 참조.
"""

import os
import re
import time

# 라이브러리는 **호출 시점에** 들여온다 — 미설치 환경에서도 앱이 뜨고, PDF 모드만
#   '사용 불가'로 degrade 되게 하기 위함(core/의 graceful 관용구와 같다).
_IMPORT_ERROR = None


def _load_libs():
    global _IMPORT_ERROR
    try:
        import pypdfium2 as pdfium
        import pypdf
        from pypdf.annotations import Highlight
        from pypdf.generic import (ArrayObject, FloatObject, NameObject,
                                   TextStringObject)
        return (pdfium, pypdf, Highlight, ArrayObject, FloatObject,
                NameObject, TextStringObject)
    except Exception as exc:      # ImportError뿐 아니라 DLL 로드 실패까지
        _IMPORT_ERROR = str(exc)
        return None


def available() -> bool:
    """PDF 주석 기능을 쓸 수 있는가(라이브러리 존재 여부)."""
    return _load_libs() is not None


def unavailable_reason() -> str:
    _load_libs()
    return _IMPORT_ERROR or ""


# ── 머리말/꼬리말 판정 ────────────────────────────────────────────────
#   한/글 스토리에는 한 번 있는 머리말이 PDF에는 **쪽마다 렌더**되므로 등장 수가
#   부풀고, 그러면 인덱스 정합이 통째로 깨진다(실측 2026-08-07: 표본 25낱말 중 5개가
#   어긋났고 전부 머리말이 원인. '국방분야' 23곳 → PDF 33곳, 초과분 10곳 중 8곳이
#   y=701에서 짝수 쪽마다 반복).
#
# ⚠ 판정 키는 **(x, y) 둘 다**여야 한다. y만 보면 본문도 걸린다 — 조판된 문서에서는
#   본문 줄의 y가 쪽마다 똑같이 반복되기 때문이다(실측: y만으로 거르니 '항공유'가
#   104곳 → 77곳으로 과잉 삭제돼 원고의 96곳과 더 크게 어긋났다). 같은 낱말이 여러
#   쪽에서 **같은 x·같은 y**에 오는 것은 머리말·꼬리말 말고는 사실상 없다.
_REPEAT_MIN_PAGES = 3      # 이 쪽 수 이상에서 같은 (x,y)에 나오면 머리말/꼬리말
_XY_TOLERANCE = 1.0        # 좌표 묶음 허용 오차(pt)


# ── ★띄어쓰기 검증 ────────────────────────────────────────────────────
#   pdfium의 `search()`는 **공백을 무시한다**. 실측(2026-08-08 · 실파일05 · 17쪽):
#   `'국방 분야'`와 `'국방분야'`로 찾은 결과가 **글자 하나 다르지 않고 같았다**(33곳).
#   그래서 띄어쓰기 교정('국방 분야'→'국방분야', 원고 2곳)이 PDF에서는 **반대 표기
#   자리까지 25곳**에 주석으로 달렸다 — 교정본·메모본은 정확히 2곳만 건드리는데
#   PDF만 25곳이라, 같은 검토 결과에서 나온 세 산출물이 서로 다른 문서가 됐다
#   (사용자 보고: "반영된 항목이 다 다른 것 같다 — 신뢰성에 치명적").
#
#   다행히 `get_text_range(idx, cnt)`는 **실제로 잡은 글자**를 그대로 돌려준다.
#   그것을 원문과 대조해 표기가 다른 매치를 버린다.
#
#   ⚠ 다만 단순 문자열 일치로 거르면 **정탐을 잃는다** — PDF는 줄이 바뀌는 자리에
#     `\r\n`을 끼워 넣기 때문이다(실측: `'엔\r\n진 부품'`은 진짜 '엔진 부품'이다).
#     그래서 줄바꿈만 흡수하고 띄어쓰기는 엄격히 본다.
#   ⚠ 남는 모호함 하나: 줄바꿈이 **하필 이음매에서** 일어나면
#     `'공급사슬\r\n관리'`가 '공급사슬 관리'인지 '공급사슬관리'인지 글자만으로는
#     가릴 수 없다 — 그래서 일단 인정하되 `exact=False`로 **표시해 두고**, 자리 수가
#     어긋날 때 `_drop_ambiguous`가 개수를 근거로 걷어낸다(§_drop_ambiguous).
_BREAK_RE = re.compile(r"[ \t ]*[\r\n]+[ \t ]*")


def _spacing_ok(actual: str, needle: str) -> bool:
    """PDF가 실제로 잡은 글자 `actual`이 원문 `needle`과 **같은 표기**인가."""
    if actual == needle:
        return True
    if _BREAK_RE.sub("", actual) == needle:      # 줄바꿈이 낱말 안에서 일어난 경우
        return True
    if _BREAK_RE.sub(" ", actual) == needle:     # 줄바꿈이 원문의 공백 자리인 경우
        return True
    return False


class _Match:
    __slots__ = ("page", "rect", "xy_key", "exact")

    def __init__(self, page, rect, exact=True):
        self.page = page
        self.rect = rect                       # (left, bottom, right, top)
        self.xy_key = (round(rect[0] / _XY_TOLERANCE),
                       round(rect[1] / _XY_TOLERANCE))
        # 잡은 글자가 원문과 **글자 그대로** 같았는가. 줄바꿈을 지워야 같아진 매치는
        #   False — 그 자리는 반대 표기일 수도 있다(위 §띄어쓰기 검증).
        self.exact = exact

    def __repr__(self):
        return f"<Match p{self.page} x{self.rect[0]:.0f} y{self.rect[1]:.0f}>"


def _drop_ambiguous(matches, expected):
    """★줄바꿈에 걸린 **반대 표기**를 걷어낸다. 반환 (매치 목록, 걷어낸 수).

    실측 2026-08-10 (실파일 고독사 · 사용자 보고):
      '도움요청'→'도움 요청'(문서 4곳)이 PDF에서 **5곳**으로 잡혔다. 다섯째(114쪽)는
      원고에서 이미 올바른 '도움 요청'인데 하필 '도움'과 '요청' 사이에서 줄이 바뀌어,
      줄바꿈을 지우면 '도움요청'과 글자가 같아진 자리였다. 개수가 어긋나니 정합이
      실패로 떨어지고 **네 자리 전부**에 '위치 자동 대조 실패' 경고가 붙은 채 엉뚱한
      자리까지 형광이 칠해졌다 — 같은 부류가 문서 곳곳에서 반복됐다.

    가릴 근거는 **개수**다. 줄바꿈 매치를 뺀 '글자 그대로' 매치만으로 문서의 등장 수가
    딱 맞으면, 줄바꿈 매치는 원고에 없는 자리 = 반대 표기다. 반대로 모자라면 그 매치는
    진짜 등장이므로 그대로 둔다(`'엔\\r\\n진 부품'`은 진짜 '엔진 부품'이다).

    ⚠ **개수가 넘칠 때만** 판정한다. 처음부터 맞으면 건드릴 이유가 없고, 모자랄 때
      건드리면 멀쩡한 정탐을 잃는다.
    """
    if not expected or len(matches) <= expected:
        return matches, 0
    exact = [m for m in matches if m.exact]
    if len(exact) != expected:
        return matches, 0
    return exact, len(matches) - len(exact)


def _search_all(pdf, tps, needle):
    """PDF 전체에서 원문을 찾아 문서 순(쪽 → 쪽 안 순서) `_Match` 목록으로.

    반환 (매치 목록, 표기가 달라 버린 수). 후자는 조용히 사라지면 안 되므로 센다.
    """
    out, spacing_dropped = [], 0
    for i, tp in enumerate(tps):
        try:
            s = tp.search(needle, match_case=False, match_whole_word=False)
        except Exception:
            continue
        try:
            while True:
                r = s.get_next()
                if r is None:
                    break
                idx, cnt = r
                # ★검색은 공백을 무시하므로 **잡은 글자를 직접 확인**한다(위 주석).
                try:
                    actual = tp.get_text_range(idx, cnt)
                except Exception:
                    actual = needle          # 확인 불가 — 예전대로 통과시킨다
                if not _spacing_ok(actual, needle):
                    spacing_dropped += 1
                    continue
                try:
                    n = tp.count_rects(idx, cnt)
                    rect = tp.get_rect(0) if n else None
                except Exception:
                    rect = None
                if rect:
                    out.append(_Match(i, rect, exact=(actual == needle)))
        finally:
            try:
                s.close()
            except Exception:
                pass
    return out, spacing_dropped


def _drop_running_heads(matches):
    """머리말/꼬리말로 판정된 매치를 걷어낸다. 반환 (본문 매치, 걷어낸 수)."""
    if len(matches) < _REPEAT_MIN_PAGES:
        return matches, 0
    pages_by_xy = {}
    for m in matches:
        pages_by_xy.setdefault(m.xy_key, set()).add(m.page)
    banned = {xy for xy, pages in pages_by_xy.items()
              if len(pages) >= _REPEAT_MIN_PAGES}
    if not banned:
        return matches, 0
    kept = [m for m in matches if m.xy_key not in banned]
    return kept, len(matches) - len(kept)


def _align_occurrences(matches, occ_total, skip_occ, doc_occ=0):
    """PDF 매치를 파이프라인의 **등장 인덱스**에 맞춘다.
    반환 (표시할 [(등장 인덱스, 매치)…], 경고문).

    ★이 함수가 이 모듈의 핵심이다. 파이프라인은 부분 거절을 '문서 등장 순 인덱스'
      (`skip_occurrences`)로 나르므로, "파이프라인의 k번째 등장"과 "PDF의 k번째 매치"가
      같은 자리를 가리켜야만 인덱스를 그대로 쓸 수 있다.

    ★기준 수는 **`doc_occ`(한/글 문서에서 실제로 찾은 등장 수)를 먼저 본다.**
      `occ_total`(검수 패널이 추출 텍스트에서 센 수)과 다를 수 있고, 그때 옳은 쪽은
      `doc_occ`다 — `skip_occurrences`가 사는 좌표계가 **브리지 RepeatFind 순서**이고
      locate가 바로 그 순서로 세기 때문이다(CLAUDE.md '등장 좌표계'). 실측
      2026-08-08: '지속가능항공유'가 추출 텍스트 5곳 · 문서 찾기 4곳이라, occ_total로
      대조하면 멀쩡한 항목이 '정합 실패'로 떨어졌다.

    개수가 맞으면 인덱스를 그대로 쓴다. 안 맞으면 **매핑을 포기하고 전 등장에 주석을
    달되 경고를 붙인다** — 조용히 첫 매치에 달거나 조용히 건너뛰는 선택지는 없다
    (memory: layer-order-silent-drop-audit — '조용한 드롭' 금지).
    """
    expected = doc_occ or occ_total
    if expected and len(matches) == expected:
        return [(k, m) for k, m in enumerate(matches) if k not in skip_occ], ""
    if not expected:
        # 등장 수를 모르는 항목(전자동 모드 등) — 거절 인덱스도 의미가 없다.
        return list(enumerate(matches)), ""
    src = f"문서 {doc_occ}곳" if doc_occ else f"원고 {occ_total}곳"
    warn = (f"⚠ 위치 자동 대조 실패 ({src} · PDF {len(matches)}곳) "
            f"— 정오표의 쪽과 대조해 확인하세요")
    return list(enumerate(matches)), warn


def annotate(pdf_path: str, items: list, out_path: str = None,
             logger=None) -> dict:
    """PDF에 교정 주석을 단다.

    items: [{
        "original":  원문(=PDF에서 찾을 문자열),
        "corrected": 교정문,
        "reason":    사유,
        "category":  교정 유형,
        "occ_total": 파이프라인이 센 등장 수(0이면 모름),
        "skip_occurrences": [거절한 등장 인덱스…],
    }, …]

    반환: {"pdf": 저장 경로, "annotated": 주석 자리 수, "items": 주석 단 항목 수,
           "missing": [원문…], "warnings": [문자열…],
           "pages_by_original": {원문: [PDF 물리 쪽(1-based), …]}}
    """
    libs = _load_libs()
    if libs is None:
        raise RuntimeError(
            "PDF 주석 라이브러리를 불러오지 못했습니다 "
            f"(pypdfium2 / pypdf): {_IMPORT_ERROR}")
    (pdfium, pypdf, Highlight, ArrayObject, FloatObject,
     NameObject, TextStringObject) = libs

    log = logger or (lambda *_a, **_k: None)
    out_path = out_path or pdf_path

    # ── 1) 검색 ──────────────────────────────────────────────────
    pdf = pdfium.PdfDocument(pdf_path)
    tps = []
    try:
        for i in range(len(pdf)):
            tps.append(pdf[i].get_textpage())

        plan = []            # [(item, [_Match…], warn)]
        missing = []
        warnings = []
        head_dropped = 0
        space_dropped = 0
        break_dropped = 0
        pages_by_original = {}

        for it in items:
            needle = (it.get("original") or "").strip()
            if not needle:
                continue
            occ_total = int(it.get("occ_total") or 0)
            doc_occ   = int(it.get("doc_occ") or 0)
            matches, sp = _search_all(pdf, tps, needle)
            space_dropped += sp
            matches, dropped = _drop_running_heads(matches)
            head_dropped += dropped
            # ★줄바꿈에 걸린 반대 표기 걷어내기 — **쪽 목록을 만들기 전**에 한다.
            #   정오표의 'PDF 쪽' 칸이 이 목록을 등장 인덱스로 훑으므로, 순서를 바꾸는
            #   판정은 전부 여기서 끝나 있어야 한다.
            matches, br = _drop_ambiguous(matches, doc_occ or occ_total)
            break_dropped += br
            if not matches:
                missing.append(needle)
                continue
            pages_by_original[needle] = [m.page + 1 for m in matches]
            targets, warn = _align_occurrences(
                matches,
                occ_total,
                set(it.get("skip_occurrences") or []),
                doc_occ=doc_occ,
            )
            if warn:
                warnings.append(f"{needle}: {warn}")
            if targets:
                plan.append((it, targets, warn))
    finally:
        for tp in tps:
            try:
                tp.close()
            except Exception:
                pass
        try:
            pdf.close()
        except Exception:
            pass

    if space_dropped:
        # ⚠ 조용한 드롭 금지 — 다만 이건 '놓친 것'이 아니라 **원고와 표기가 다른 자리**를
        #   걷어낸 것이다(공백 무시 검색의 부작용 차단). 세 산출물의 자리 수를 맞춰 준다.
        log(f"  [PDF] 표기가 다른 매치 {space_dropped}곳 제외 "
            "· 검색이 공백을 무시해 반대 표기까지 잡음")
    if head_dropped:
        log(f"  [PDF] 머리말·꼬리말 반복 매치 {head_dropped}곳 제외")
    if break_dropped:
        # ⚠ 조용한 드롭 금지 — 줄바꿈 때문에 반대 표기가 원문처럼 보인 자리다.
        log(f"  [PDF] 줄바꿈에 걸린 반대 표기 {break_dropped}곳 제외 "
            "· 문서 등장 수와 대조해 판정")

    # ── 2) 주석 쓰기 ──────────────────────────────────────────────
    writer = pypdf.PdfWriter(clone_from=pdf_path)
    n_annot = 0
    # 한 번 만든 문자열을 모든 주석이 나눠 쓴다 — 한 번의 실행은 한 시각이다.
    now = _pdf_date()
    # 어느 **등장 인덱스**에 실제로 주석이 붙었는가 — 정오표가 '주석 못 단 자리'를
    #   적을 수 있게 돌려준다(교정본의 '부분 반영' 안전망과 같은 역할).
    marked_occ = {}
    for it, targets, warn in plan:
        body = _memo_body(it, warn)
        needle = (it.get("original") or "").strip()
        for k, m in targets:
            left, bottom, right, top = m.rect
            try:
                # ★한 자리 = 주석 하나. 형광 주석이 `/Contents`를 직접 들고 간다 —
                #   스티커를 따로 달면 주석 목록에 같은 교정이 두 줄로 뜬다(파일 머리말).
                hl = Highlight(
                    rect=(left, bottom, right, top),
                    quad_points=ArrayObject([FloatObject(v) for v in (
                        left, top, right, top, left, bottom, right, bottom)]),
                    highlight_color=_HL_COLOR,
                )
                hl[NameObject("/Contents")] = TextStringObject(body)
                hl[NameObject("/T")] = TextStringObject(_ANNOT_AUTHOR)
                # ★날짜를 넣지 않으면 뷰어가 그 칸에 '미정값'을 적는다(위 §주석 날짜).
                hl[NameObject("/M")] = TextStringObject(now)
                hl[NameObject("/CreationDate")] = TextStringObject(now)
                writer.add_annotation(page_number=m.page, annotation=hl)
                marked_occ.setdefault(needle, []).append(k)
                n_annot += 1
            except Exception as exc:
                log(f"  [PDF] 주석 실패: {exc}")

    tmp = out_path + ".__tmp__"
    with open(tmp, "wb") as f:
        writer.write(f)
    try:
        writer.close()
    except Exception:
        pass
    os.replace(tmp, out_path)

    return {
        "pdf":        out_path,
        "annotated":  n_annot,
        "items":      len(plan),
        "missing":    missing,
        "warnings":   warnings,
        "spacing_dropped": space_dropped,
        "break_dropped":   break_dropped,
        "pages_by_original": pages_by_original,
        # {원문: [주석을 실제로 단 등장 인덱스…]} — 정오표가 '못 단 자리'를 적는 근거
        "marked_occ": marked_occ,
    }


# 형광 색 — 교정본의 빨강과 달리 '읽어야 할 자리' 표시라 노랑 계열로 둔다.
_HL_COLOR = "ffd400"
# 주석 작성자(`/T`) — 뷰어의 주석 목록에 이 이름으로 묶여 원고 저자의 주석과 구분된다.
_ANNOT_AUTHOR = "KS-AI Editor"


# ── ★주석 날짜(`/M`) ──────────────────────────────────────────────────
#   ⚠ 날짜를 **빼면 사라지는 게 아니라 '미정값'이 찍힌다**(사용자 보고 2026-08-12:
#   주석 목록마다 "2페이지 미정값"). 뷰어의 주석 목록은 `작성자 · 쪽 · 수정 날짜`를
#   고정 칸으로 그리므로, 우리가 `/M`을 넣지 않으면 그 칸이 빈 채로 남고 한컴 뷰어는
#   빈 날짜를 '미정값'이라고 적는다. 즉 **지울 수 있는 문자열이 아니라 비어 있다는
#   표시**이고, 없애는 유일한 방법은 실제 날짜를 넣는 것이다.
#   PDF 날짜 서식은 `D:YYYYMMDDHHmmSS+09'00'`(PDF 32000-1 §7.9.4).
def _pdf_date(ts=None) -> str:
    t = time.localtime(ts)
    off = -(time.altzone if t.tm_isdst and time.daylight else time.timezone)
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return (f"D:{time.strftime('%Y%m%d%H%M%S', t)}"
            f"{sign}{off // 3600:02d}'{(off % 3600) // 60:02d}'")


def _memo_body(item: dict, warn: str = "") -> str:
    """주석 하나의 본문 — 문구는 `output.annot_text`가 단일 출처다(메모와 같은 말)."""
    from .annot_text import annotation_body
    return annotation_body(item.get("original", ""), item.get("corrected", ""),
                           item.get("category", ""), item.get("reason", ""),
                           extra=warn)


