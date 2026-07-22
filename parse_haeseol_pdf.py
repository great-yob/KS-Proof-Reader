"""
parse_haeseol_pdf.py — 국립국어원 '한글 맞춤법 표준어 규정 해설' PDF 파서 (고도화판)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
원본 PDF: https://www.korean.go.kr/attachFile/(파일명 길어 생략).pdf
(국립국어원, 2018-01-08, 등록번호 11-1371028-000712-01, 약 280쪽)

이 PDF 한 권에 다음이 모두 들어 있다.
  Ⅰ. 한글 맞춤법 해설        (제1장 총칙 ~ 제6장 그 밖의 것)
  Ⅱ. 표준어 규정 해설
     - 제1부 표준어 사정 원칙
     - 제2부 표준 발음법

각 항(項)은 대체로 다음 패턴이 반복된다.
  제N항
  <조항 원문 1~2문장>
  <국립국어원 해설, 여러 문단, 예시 단어 나열 포함>
  (선택) 더 알아보기
  <보충 설명>

이 스크립트는 그 패턴을 정규식으로 인식해 장/절/항 단위로 잘라
**어문 규범 지식 레이어 고도화 스키마**(rule_id/triggers/deterministic/context_dependent/
category/priority 포함)의 JSON Lines로 저장한다 → 그대로 build_eomun_db.py 가 적재한다.

━━━ 설계상 안전 고정 (docs/eomun-rule-layer-architecture.md) ━━━
  · 자동 파싱 레코드는 **전부 컨텍스트 전용**으로 출력한다:
        deterministic=false, context_dependent=true
    즉 KAGEC 규칙 컨텍스트(역할 A)로만 쓰이고, **결정론 자동치환(역할 B)을 만들지 않는다.**
    결정론 페어로 승격하려면 사람이 검수해 시드(eomun_seed.jsonl)에 deterministic=true로 옮긴다.
    (PDF 자동추출 예시는 표·열 깨짐 위험이 있어 자동 치환에 쓰면 과교정 위험 → 금지.)
  · triggers 는 추출된 '틀린 예'에서 자동 생성(없으면 빈 배열 → 검색에 안 걸림, DB엔 보존).

[중요] PDF 텍스트 추출은 레이아웃에 따라 100% 깨끗하지 않다. 특히 'ㄱ/ㄴ' 비교표,
       단모음/이중모음 표는 컬럼이 섞여 깨질 수 있다. 1차 결과를 그대로 쓰지 말고
       --review 로 길이·예시가 비정상인 레코드를 골라 사람이 한 번 검수할 것.

[설치]  .\\.venv64\\Scripts\\pip install pdfplumber
[사용]
    # 1) PDF를 내려받아 같은 폴더에 둔다 (예: haeseol.pdf)
    # 2) 변환 (기본 출력: data/eomun/haeseol.jsonl → build_eomun_db.py 가 자동 적재)
    .\\.venv64\\Scripts\\python.exe parse_haeseol_pdf.py haeseol.pdf
    # 3) 의심 레코드 검수
    .\\.venv64\\Scripts\\python.exe parse_haeseol_pdf.py haeseol.pdf --review
    # 4) 적재
    .\\.venv64\\Scripts\\python.exe build_eomun_db.py
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Windows 콘솔(cp949) 인코딩 크래시 방지
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
    except Exception:
        pass

try:
    import pdfplumber
except ImportError:
    print("pdfplumber가 필요합니다:  .\\.venv64\\Scripts\\pip install pdfplumber")
    sys.exit(1)


DATA_DIR = Path(__file__).parent / "data" / "eomun"
SOURCE_URL = "https://korean.go.kr/kornorms/regltn/regltnView.do?regltn_code="

# ── 구조 인식 정규식 ────────────────────────────────────────────────
CHAPTER_RE = re.compile(r"^제\s*(\d+)\s*장\s+(.+)$")
SECTION_RE = re.compile(r"^제\s*(\d+)\s*절\s+(.+)$")
ARTICLE_RE = re.compile(r"^제\s*(\d+)\s*항\s*$")
MORE_RE = re.compile(r"\n더\s*알아보기\s*\n")

# 상위 규정(부) 경계 — **헤더 줄에서만** 전환(본문에 그 어구가 나와도 무시).
#   전체 줄 앵커(^…$)로 매칭해, '표준 발음법은 …' 같은 본문 문장이 부 경계로
#   오인되어 진행 중인 항(項) 레코드를 날리는 사고를 막는다.
#   part는 '마지막 설정 우선'이며 본문 divider가 (미니)목차보다 항상 나중에 오므로 목차 오염은 자동 교정.
PART_HANGEUL = re.compile(r"^['‘“]?\s*한글\s*맞춤법\s*['’”]?(\s*해설)?\s*$")
PART_STANDARD = re.compile(r"^(Ⅱ\s*[.．]\s*)?(제?\s*\d*\s*부?\s*)?['‘“]?\s*표준어\s*(규정|사정\s*원칙)\s*['’”]?(\s*해설)?\s*$")
PART_PRONUNCIATION = re.compile(r"^(제?\s*\d*\s*부?\s*)?표준\s*발음법(\s*해설)?\s*$")

# 머리말/쪽번호/꼬리말 등 반복 잡음
NOISE_PATTERNS = [
    re.compile(r"^\d+\s*$"),                                   # 쪽번호만
    re.compile(r"^(차\s*례|차례|목\s*차|머리말|일러두기)\s*$"),   # 목차/표제
    # 머리말·꼬리말 — 쪽번호가 앞/뒤에 붙은 변형 포함
    re.compile(r"^\d*\s*['‘“]?\s*한글\s*맞춤법\s*['’”]?\s*[,，]?\s*['‘“]?\s*표준어\s*규정\s*['’”]?\s*해설\s*\d*$"),
    re.compile(r"^Ⅰ\s*[.．]\s*['‘“]?\s*한글\s*맞춤법\s*['’”]?\s*해설\s*\d*$"),
    re.compile(r"^Ⅱ\s*[.．]\s*['‘“]?\s*표준어\s*규정\s*['’”]?\s*해설\s*\d*$"),
]
# 점선 리더(목차 항목) — '제1장 총칙········· 11'처럼 3개 이상 연속 점은 목차 신호.
_DOT_LEADER = re.compile(r"[·․‥…⋯ㆍ]{3,}")

# 규정 → (코드접두, regltn_code, regulation 명)
REG_META = {
    "hangeul":      ("HAE-HM", "0001", "한글 맞춤법"),
    "standard":     ("HAE-SW", "0002", "표준어 규정"),
    "pronunciation":("HAE-SP", "0002", "표준어 규정(표준 발음법)"),
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip())


def is_noise(line: str) -> bool:
    if _DOT_LEADER.search(line):   # 목차의 점선 리더 줄 → 본문 아님
        return True
    return any(p.match(line) for p in NOISE_PATTERNS)


def extract_lines(pdf_path: str):
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            for raw in text.split("\n"):
                line = _nfc(raw)
                if line:
                    lines.append(line)
            if i % 20 == 0:
                print(f"  페이지 {i}/{total} 추출")
    return lines


def parse(lines):
    """라인 목록 → (part, 장, 절, 항번호, 원문블록) 레코드."""
    records = []
    part = "hangeul"        # 기본: 문서 첫 부분은 한글 맞춤법 해설
    chapter = section = None
    article = None
    buf = []

    def flush():
        if article is not None and buf:
            records.append({
                "part": part, "chapter": chapter, "section": section,
                "article_no": article, "raw_text": "\n".join(buf).strip(),
            })

    for line in lines:
        if is_noise(line):
            continue

        # 상위 규정 경계 전환 (장/절 초기화). '마지막 설정 우선' — 목차의 가짜 경계는
        # 뒤따르는 본문 divider가 덮어쓴다. PART_HANGEUL 복원으로 한글맞춤법 본문이
        # 직전 목차의 발음법/표준어 경계에 오염되는 것을 막는다.
        if PART_PRONUNCIATION.match(line):
            flush(); part, chapter, section, article, buf = "pronunciation", None, None, None, []
            continue
        if PART_STANDARD.match(line):
            flush(); part, chapter, section, article, buf = "standard", None, None, None, []
            continue
        if PART_HANGEUL.match(line):
            flush(); part, chapter, section, article, buf = "hangeul", None, None, None, []
            continue

        m = CHAPTER_RE.match(line)
        if m:
            flush(); chapter = f"제{m.group(1)}장 {m.group(2)}"; article, buf = None, []
            continue
        m = SECTION_RE.match(line)
        if m:
            flush(); section = f"제{m.group(1)}절 {m.group(2)}"; article, buf = None, []
            continue
        m = ARTICLE_RE.match(line)
        if m:
            flush(); article = int(m.group(1)); buf = []
            continue

        buf.append(line)

    flush()
    return records


def split_rule_and_commentary(raw_text: str):
    """원문 블록 → (조항 원문, 해설, 더알아보기)."""
    m = MORE_RE.search(raw_text)
    if m:
        main, more = raw_text[:m.start()], raw_text[m.end():]
    else:
        main, more = raw_text, ""
    parts = main.split("\n")
    rule_text = parts[0].strip() if parts else ""
    commentary = "\n".join(parts[1:]).strip()
    return rule_text, commentary, more.strip()


# ── 예시 추출 휴리스틱 ──────────────────────────────────────────────
# 인용부호 안의 한글 토큰 (‘…’ '…' "…" 모두)
_QUOTED_HANGUL = re.compile(r"['‘“\"]([가-힣]{2,})['’”\"]")
# '잘못', '×', '아니다' 등 '틀린 예' 신호
_BAD_CUE = re.compile(r"잘못|×|✕|틀린|비표준|쓰지\s*않|올바르지")


def extract_examples(commentary: str):
    """해설에서 정/오 예시를 best-effort로 추출(보수적·저잡음).

    PDF 표 추출이 불완전하므로 명시 신호가 있을 때만 잡는다:
      · 'A(→ B)' / 'A → B' 명시 교정쌍 → (B=correct, A=incorrect).
      · '잘못/×/틀린…' 신호 줄에서는 **인용부호 안 토큰만** 틀린 예로(잡음 토큰 배제).
    확신이 없으면 비워 둔다(--review 로 사람이 보강). 자동치환에 쓰지 않으므로(컨텍스트 전용)
    소량 누락은 무해하고, 잡음 트리거(아무 문서에서나 활성화)를 막는 게 더 중요하다.
    """
    correct, incorrect = [], []

    # 1) 'A(→ B)' 또는 'A → B' 명시 교정쌍
    for a, b in re.findall(r"([가-힣]{2,})\s*\(?\s*→\s*([가-힣]{2,})\s*\)?", commentary):
        incorrect.append(_nfc(a)); correct.append(_nfc(b))

    # 2) '틀린 예' 신호 줄: 인용부호 안 토큰만 채택(잡음 배제)
    for ln in commentary.split("\n"):
        if _BAD_CUE.search(ln):
            for t in _QUOTED_HANGUL.findall(ln):
                incorrect.append(_nfc(t))

    # 중복 정리 — correct 우선, incorrect 에서 correct 와 겹치는 것 제거
    correct = list(dict.fromkeys(correct))
    incorrect = list(dict.fromkeys(t for t in incorrect if t not in correct))
    return correct, incorrect


def infer_category(chapter, section) -> str:
    ctx = f"{chapter or ''} {section or ''}"
    if "띄어쓰기" in ctx:
        return "띄어쓰기"
    if "외래어" in ctx:
        return "외래어"
    if "표준" in ctx or "발음" in ctx:
        return "표준어"
    return "맞춤법"


def to_row(rec, seq_counter):
    prefix, code, regulation = REG_META[rec["part"]]
    art = rec["article_no"]
    rule_text, commentary, more = split_rule_and_commentary(rec["raw_text"])
    correct, incorrect = extract_examples(commentary + ("\n" + more if more else ""))
    # rule_id 유일화 — 같은 항번호가 부/절에 따라 중복될 수 있어 seq 부여
    seq = seq_counter.setdefault((prefix, art), 0)
    seq_counter[(prefix, art)] = seq + 1
    rid = f"{prefix}-{art:04d}" + (f"-{seq}" if seq else "")

    gloss = commentary if len(commentary) <= 600 else commentary[:600] + "…"
    return {
        "rule_id": rid,
        "regulation": regulation,
        "notice_no": "",
        "chapter": rec["chapter"] or "",
        "section": rec["section"],
        "article_no": art,
        "rule_text": rule_text,
        "gloss": gloss,
        "examples": {"correct": correct, "incorrect": incorrect},
        # ⚠ 자동 파싱분은 컨텍스트 전용으로 안전 고정 (설계 §6 / 안티패턴 1)
        "triggers": incorrect,
        "deterministic": False,
        "category": infer_category(rec["chapter"], rec["section"]),
        "context_dependent": True,
        "priority": 2,
        "source_url": SOURCE_URL + code,
        "see_also": more,
    }


def main():
    ap = argparse.ArgumentParser(description="한글 맞춤법 표준어 규정 해설 PDF 파서(고도화)")
    ap.add_argument("pdf_path")
    ap.add_argument("-o", "--out", default=str(DATA_DIR / "haeseol.jsonl"),
                    help="출력 JSONL (기본: data/eomun/haeseol.jsonl)")
    ap.add_argument("--review", action="store_true",
                    help="rule_text 길이 이상/예시 0건 레코드를 stderr로 출력")
    args = ap.parse_args()

    print(f"'{args.pdf_path}' 텍스트 추출 중…")
    lines = extract_lines(args.pdf_path)
    print(f"  → {len(lines):,}줄 추출")

    print("장/절/항 단위로 구조화 중…")
    records = parse(lines)
    print(f"  → {len(records)}개 항 인식")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seq_counter = {}
    suspicious = []
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            row = to_row(rec, seq_counter)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if len(row["rule_text"]) < 5 or len(row["rule_text"]) > 200 \
                    or (not row["examples"]["correct"] and not row["examples"]["incorrect"]):
                suspicious.append(row)
    print(f"완료: {out_path}  (총 {len(records)}건, 컨텍스트 전용=deterministic:false)")
    print("→ 적재:  .\\.venv64\\Scripts\\python.exe build_eomun_db.py")

    if args.review:
        print(f"\n[검수 대상] rule_text 이상 또는 예시 0건 {len(suspicious)}건:", file=sys.stderr)
        for row in suspicious:
            print(f"  - {row['rule_id']} {row['chapter']}/{row.get('section') or ''} "
                  f": '{row['rule_text'][:40]}…'", file=sys.stderr)


if __name__ == "__main__":
    main()
