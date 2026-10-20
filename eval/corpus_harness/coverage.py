"""
eval/corpus_harness/coverage.py — 추출 충실도(파이프라인이 원고의 어느 글을 못 보나)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
추출에서 빠진 글은 어떤 계층도 교정하지 못한다 — 가장 조용한 미탐이다. 원고 파일을 **한/글 없이 직접
판독**한 '정답 문단'이 브리지 추출 텍스트(원고 캐시 `text`)에 들어 있는지 대조한다.

  · .hwp  — 복합 문서(`core.hwp_styles._Cfb`)의 BodyText/Section{n} 레코드에서 HWPTAG_PARA_TEXT(67)를 모두
            모은다. 표·글상자·각주·머리말·꼬리말 속 문단도 같은 스트림에 있다(바깥 CTRL_HEADER(71)의 컨트롤
            ID로 소속을 단다 — 'tbl '·'fn  '·'head'·'foot'·'gso '). 암호·배포용 문서는 건너뛴다.
  · .hwpx — Contents/section*.xml의 문단(p)마다 자기 글(t)만 모은다(중첩 문단은 따로).
  · 대조 — 영숫자·한글만 남겨 ① 문단 전체가 추출 텍스트에 있으면 통과 ② 없으면 12자 조각(4자 간격)의
            포함률을 보고 50% 미만만 **누락 후보**로 센다. ⚠ ①만 쓰면 본문 3.9%가 누락으로 보인다 — 추출이
            각주 본문·표를 앵커 자리(문단 한가운데)에 끼워 넣어 문단이 쪼개지기 때문이다(가짜).

2026-10-20 첫 측정(20원고, 정답 약 171만 자): 후보 39문단 = 전부 설명됨(각주 분절 · 이전 교정 표시가 박힌
원고 · 목차 필드 조각) — **실제 누락 0**. 그 39문단이 기준선이고, 게이트는 **새 후보**만 △로 보인다.
추출 경로(core/hwp_bridge_worker.py·hwp_editor.py)를 고치고 `run_corpus.py extract --force` 한 뒤에 의미가 있다.
⚠ 고객 원고다 — 결과(문단 원문)는 교정샘플/.corpus/runs/ 아래에만 둔다.
"""
import hashlib
import os
import re
import struct
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

_TAG_PARA_TEXT, _TAG_CTRL_HEADER = 67, 71
_SKIP8 = set(range(1, 10)) | {11, 12} | set(range(14, 24))   # 8 WCHAR짜리 확장·인라인 컨트롤
_SHINGLE, _STEP, _MIN_COVER = 12, 4, 0.5


def _norm(s):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def _para_text(body):
    out, i, n = [], 0, len(body) // 2
    while i < n:
        (ch,) = struct.unpack_from("<H", body, i * 2)
        if ch < 32:
            if ch in _SKIP8:
                i += 8
                continue
            out.append("\n" if ch == 10 else " " if ch in (30, 31) else "-" if ch == 24 else "")
            i += 1
            continue
        out.append(chr(ch))
        i += 1
    return "".join(out)


def _records(buf):
    """(태그, 수준, 본문) — 레코드 헤더는 태그 10비트·수준 10비트·크기 12비트(0xFFF면 뒤 4바이트가 크기)."""
    i, n = 0, len(buf)
    while i + 4 <= n:
        (h,) = struct.unpack_from("<I", buf, i)
        i += 4
        size = (h >> 20) & 0xFFF
        if size == 0xFFF:
            (size,) = struct.unpack_from("<I", buf, i)
            i += 4
        yield h & 0x3FF, (h >> 10) & 0x3FF, buf[i:i + size]
        i += size


def _from_hwp(path):
    from core.hwp_styles import _Cfb
    with open(path, "rb") as fp:
        cfb = _Cfb(fp.read())
    flags = struct.unpack_from("<I", cfb.read("FileHeader"), 36)[0]
    if flags & 6:                      # 암호 · 배포용
        return None
    paras, k = [], 0
    while True:
        try:
            buf = cfb.read(f"Section{k}")
        except KeyError:
            break
        if flags & 1:
            buf = zlib.decompress(buf, -15)
        stack = []
        for tag, lv, body in _records(buf):
            while stack and stack[-1][0] >= lv:
                stack.pop()
            if tag == _TAG_CTRL_HEADER and len(body) >= 4:
                stack.append((lv, body[:4][::-1].decode("latin-1").strip()))
            elif tag == _TAG_PARA_TEXT:
                paras.append((_para_text(body), stack[0][1] if stack else "본문"))
        k += 1
    return paras


_HWPX_CTX = {"tbl", "footNote", "endNote", "header", "footer", "rect", "drawText", "container", "pic"}


def _from_hwpx(path):
    import xml.etree.ElementTree as ET

    def local(e):
        return e.tag.rsplit("}", 1)[-1]
    paras = []
    with zipfile.ZipFile(path) as z:
        for nm in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
            root = ET.fromstring(z.read(nm))
            parent = {c: p for p in root.iter() for c in p}
            for p in root.iter():
                if local(p) != "p":
                    continue
                txt, stack = [], [p]
                while stack:
                    e = stack.pop()
                    for ch in list(e)[::-1]:
                        if local(ch) == "p":
                            continue
                        if local(ch) == "t":
                            txt.append(ch.text or "")
                            txt.extend(sub.tail or "" for sub in ch)
                        stack.append(ch)
                ctx, a = "본문", parent.get(p)
                while a is not None:
                    if local(a) in _HWPX_CTX:
                        ctx = local(a)
                    a = parent.get(a)
                paras.append(("".join(txt), ctx))
    return paras


def candidates(path, extracted_text):
    """원고 하나의 누락 후보 [(키, 자리, 문단 원문)] — 키는 정규화 문단의 해시(기준선 대조용).
    판독할 수 없는 원고(암호·배포용·깨짐)는 None."""
    try:
        paras = (_from_hwpx if path.lower().endswith(".hwpx") else _from_hwp)(path)
    except Exception:
        return None
    if paras is None:
        return None
    full, out, seen = _norm(extracted_text), [], set()
    for t, ctx in paras:
        nt = _norm(t)
        if len(re.findall(r"[가-힣]", nt)) < 2 or nt in full:
            continue
        sh = [nt[i:i + _SHINGLE] for i in range(0, max(len(nt) - _SHINGLE + 1, 1), _STEP)]
        if len(nt) > _SHINGLE and sum(s in full for s in sh) / len(sh) >= _MIN_COVER:
            continue
        key = hashlib.sha1(nt.encode("utf-8")).hexdigest()[:16]
        if key not in seen:
            seen.add(key)
            out.append((key, ctx, t))
    return out


def source_stamp(path):
    st = os.stat(path)
    return f"{st.st_size}|{st.st_mtime_ns}"
