"""한/글 문서에서 **글자 스타일**의 글자모양 지문을 읽는다 (COM 없이, 파일만 읽는다).

왜 필요한가 — 교정 표시로 글자색만 바꿔도 한/글은 '원래 모양 + 빨강'인 **새 글자모양**을
만들어 그 런에 물린다(사용자 보고 2026-09-10, 실측: 본문강조 cs#48 → 색만 다른 cs#278.
한 파일에서 35곳). 그때는 "HWP 5.0엔 스타일 참조가 없어 글자모양 id가 같을 때만 스타일"로
보고 이것을 스타일 해제라 판단했다. ⚠ 그 전제는 **틀렸다**(2026-10-23): 참조는 문단의 영역
태그(`PARA_RANGE_TAG` 종류 1, 하위 24비트 = 스타일 순번)에 있고 한/글은 소속을 그 태그로
판정한다 — 상세는 docs/output-and-apply.md '재진단' 절. 이 모듈의 지문은 여전히 '빨강 대신
형광펜' 자리를 고르는 데 쓰인다(글자모양 id를 원래대로 지키는 것은 그대로 유효하다).

빨강과 스타일의 글자모양 id는 **양립할 수 없다**(사용자 결정 2026-09-10). 그래서
`hwp_bridge_worker.apply()`는 스타일이 걸린 자리를 **빨강 형광펜**으로 표시한다 —
형광펜은 글자모양이 아니라 런 안의 별도 요소(`<MARKPENBEGIN>`)라 글자모양을 건드리지
않는다(실측 확인). 그 판정에 쓰는 것이 이 모듈이 돌려주는 지문이다.

지문은 **COM `HCharShape`로 재현 가능한 필드만** 담는다 — 브리지가 매치 자리에서 읽은
값과 그대로 견주기 때문이다(대응은 `charshape_fingerprint()` 주석 참조).
⚠ 글자 테두리/배경(BorderFill)은 COM이 **객체**로 돌려줘 값 비교가 어려워 뺐다. 표본
19개 3,496개 스타일 런으로 실측한 오탐은 **+3건(0.09%)** — 그리고 오탐의 대가는
'빨강 대신 형광펜'뿐이라 무해하다(표시를 잃지 않는다).
⚠ 반대로 지문을 줄이면 급격히 나빠진다(장평·자간·상대크기·글자위치를 빼면 오탐 +19,327,
밑줄모양~아래첨자 비트를 빼면 +19,299). **필드를 함부로 빼지 말 것.**

`.hwp`(CFB+DocInfo 레코드)와 `.hwpx`(ZIP+OWPML `header.xml`) 둘 다 읽는다. 두 경로는
**같은 지문을 내야 한다** — 같은 원고를 두 형식으로 저장해 지문 목록이 한 글자도 다르지
않은 것을 표본 3개로 확인했다(`OWPML 열거값 ↔ 숫자` 대응이 어긋나면 그 자리에서 드러난다).
실패는 전부 빈 목록으로 수렴한다 — 스타일 보호는 **있으면 좋은 보강**이지 교정의 전제가
아니다(빈 목록이면 브리지는 예전처럼 빨강 글자만 쓴다).
"""

import io
import os
import struct
import zipfile
import zlib

# ── HWP 5.0 DocInfo 레코드 태그 ──────────────────────────────────────────
_TAG_ID_MAPPINGS = 17
_TAG_FACE_NAME = 19
_TAG_CHAR_SHAPE = 21
_TAG_STYLE = 26

# 스타일 종류(HWPTAG_STYLE 속성 하위 3비트): 0=문단 스타일, 1=글자 스타일
_STYLE_KIND_CHAR = 1

# 언어별 글꼴 칸은 7개이고 **순서가 고정**이다 — 바이너리(한글·영어·한자·일어·기타·기호·
#   사용자)와 COM(FaceNameHangul·Latin·Hanja·Japanese·Other·Symbol·User)이 같은 순서다.
_LANG_COUNT = 7

# 지문에 담는 글자 속성 비트 — 하위 17비트만 쓴다(bit0 기울임 … bit16 아래첨자).
#   그 위(강조점·글꼴에 어울리는 빈칸·취소선 모양·커닝)는 COM 대응이 불확실해 뺐고,
#   빼도 오탐이 늘지 않는 것을 실측했다(+3건 그대로).
_ATTR_MASK = 0x1FFFF


# ══════════════════════════════════════════════════════════════════════
# ▌복합 문서(CFB) 최소 판독기
# ══════════════════════════════════════════════════════════════════════
#
# olefile을 의존성으로 들이지 않으려고 필요한 만큼만 구현했다 — 스트림 하나를
# 이름으로 읽는 것이 전부다. 디렉터리는 레드-블랙 트리지만 **전 항목을 훑어
# 이름으로 찾는다**(트리를 걸을 이유가 없다).
_CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREE_SECT = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE


class _Cfb:
    def __init__(self, data):
        if not data.startswith(_CFB_SIGNATURE):
            raise ValueError("복합 문서(CFB) 서명이 아닙니다")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        self.n_fat = struct.unpack_from("<I", data, 0x2C)[0]
        self.dir_start = struct.unpack_from("<I", data, 0x30)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
        self.minifat_start = struct.unpack_from("<I", data, 0x3C)[0]
        self.n_minifat = struct.unpack_from("<I", data, 0x40)[0]
        self.difat_start = struct.unpack_from("<I", data, 0x44)[0]
        self.n_difat = struct.unpack_from("<I", data, 0x48)[0]
        self._fat = self._read_fat()
        self._dir = self._read_dir()
        self._minifat = None
        self._ministream = None

    # ── 섹터 ───────────────────────────────────────────────────────
    def _sector(self, n):
        off = 512 + n * self.sector_size
        chunk = self.data[off:off + self.sector_size]
        if len(chunk) < self.sector_size:
            raise ValueError("섹터 %d가 파일 끝을 넘습니다" % n)
        return chunk

    def _chain(self, start, fat):
        """섹터 사슬 → 섹터 번호 목록. 순환은 길이 상한으로 끊는다."""
        out, n, guard = [], start, 0
        limit = len(fat) + 8
        while n < _END_OF_CHAIN and guard < limit:
            out.append(n)
            n = fat[n] if n < len(fat) else _END_OF_CHAIN
            guard += 1
        return out

    def _read_fat(self):
        # DIFAT: 헤더에 109개, 나머지는 DIFAT 섹터 사슬에 이어진다.
        difat = list(struct.unpack_from("<109I", self.data, 0x4C))
        nxt, guard = self.difat_start, 0
        while nxt < _END_OF_CHAIN and guard <= self.n_difat:
            sec = self._sector(nxt)
            per = self.sector_size // 4 - 1
            difat.extend(struct.unpack_from("<%dI" % per, sec, 0))
            nxt = struct.unpack_from("<I", sec, per * 4)[0]
            guard += 1
        fat = []
        for s in difat[:self.n_fat]:
            if s >= _END_OF_CHAIN:
                continue
            sec = self._sector(s)
            fat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), sec, 0))
        return fat

    def _read_dir(self):
        entries = []
        for s in self._chain(self.dir_start, self._fat):
            sec = self._sector(s)
            for off in range(0, self.sector_size, 128):
                raw = sec[off:off + 128]
                if len(raw) < 128:
                    break
                nlen = struct.unpack_from("<H", raw, 0x40)[0]
                if nlen <= 0 or nlen > 64:
                    entries.append(None)
                    continue
                name = raw[:nlen - 2].decode("utf-16-le", "replace")
                entries.append({
                    "name": name,
                    "type": raw[0x42],
                    "start": struct.unpack_from("<I", raw, 0x74)[0],
                    "size": struct.unpack_from("<Q", raw, 0x78)[0],
                })
        return entries

    # ── 스트림 ─────────────────────────────────────────────────────
    def _load_mini(self):
        if self._ministream is not None:
            return
        minifat = []
        for s in self._chain(self.minifat_start, self._fat)[:max(self.n_minifat, 0) or None]:
            minifat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4),
                                              self._sector(s), 0))
        self._minifat = minifat
        root = next((e for e in self._dir if e and e["type"] == 5), None)
        buf = io.BytesIO()
        if root is not None:
            for s in self._chain(root["start"], self._fat):
                buf.write(self._sector(s))
        self._ministream = buf.getvalue()

    def read(self, name):
        """루트 바로 아래의 스트림 하나를 바이트로 돌려준다."""
        e = next((x for x in self._dir
                  if x and x["type"] == 2 and x["name"] == name), None)
        if e is None:
            raise KeyError("스트림 없음: %s" % name)
        size = e["size"]
        if size < self.mini_cutoff:
            self._load_mini()
            out = io.BytesIO()
            for s in self._chain(e["start"], self._minifat):
                off = s * self.mini_size
                out.write(self._ministream[off:off + self.mini_size])
            return out.getvalue()[:size]
        out = io.BytesIO()
        for s in self._chain(e["start"], self._fat):
            out.write(self._sector(s))
        return out.getvalue()[:size]


# ══════════════════════════════════════════════════════════════════════
# ▌HWP 레코드
# ══════════════════════════════════════════════════════════════════════
def _records(buf):
    """레코드 헤더는 32비트다 — 태그 10비트 · 수준 10비트 · 크기 12비트.
    크기가 0xFFF이면 뒤따르는 4바이트가 진짜 크기다."""
    i, n = 0, len(buf)
    while i + 4 <= n:
        (h,) = struct.unpack_from("<I", buf, i)
        i += 4
        tag = h & 0x3FF
        size = (h >> 20) & 0xFFF
        if size == 0xFFF:
            (size,) = struct.unpack_from("<I", buf, i)
            i += 4
        yield tag, buf[i:i + size]
        i += size


def _wstr(b, i):
    (ln,) = struct.unpack_from("<H", b, i)
    i += 2
    return b[i:i + ln * 2].decode("utf-16-le", "replace"), i + ln * 2


def _parse_face_name(body):
    """HWPTAG_FACE_NAME → 글꼴 이름. 속성 바이트 뒤가 이름이고, 그 뒤 선택 필드
    (대체 글꼴·파노즈·기본 글꼴)는 쓰지 않으므로 읽지 않는다."""
    name, _ = _wstr(body, 1)
    return name


def _parse_char_shape(body):
    """HWPTAG_CHAR_SHAPE(74바이트) → COM과 견줄 수 있는 값들.

    ⚠ 상대 크기는 **BYTE[7]**이지 WORD[7]가 아니다 — 여기서 한 칸 밀리면 뒤가 전부
      어긋나고, 그래도 예외는 안 난다(레코드가 74바이트라 우연히 읽힌다).
    """
    faces = struct.unpack_from("<7H", body, 0)
    ratio = struct.unpack_from("<7B", body, 14)
    spacing = struct.unpack_from("<7b", body, 21)
    relsize = struct.unpack_from("<7B", body, 28)
    offset = struct.unpack_from("<7b", body, 35)
    height = struct.unpack_from("<i", body, 42)[0]
    attr = struct.unpack_from("<I", body, 46)[0]
    color = struct.unpack_from("<I", body, 52)[0]
    ul_color = struct.unpack_from("<I", body, 56)[0]
    shade = struct.unpack_from("<I", body, 60)[0]
    shadow = struct.unpack_from("<I", body, 64)[0]
    return {
        "faces": faces, "ratio": ratio, "spacing": spacing, "relsize": relsize,
        "offset": offset, "height": height, "attr": attr, "color": color,
        "ul_color": ul_color, "shade": shade, "shadow": shadow,
    }


def _parse_style(body):
    ko, i = _wstr(body, 0)
    en, i = _wstr(body, i)
    prop = body[i]
    char_shape = struct.unpack_from("<H", body, i + 6)[0]
    return {"name": ko, "kind": prop & 0x07, "char_shape": char_shape}


# ══════════════════════════════════════════════════════════════════════
# ▌지문
# ══════════════════════════════════════════════════════════════════════
def _fingerprint(shape, face_names):
    """글자모양 한 개 → 지문(리스트). **브리지의 COM 쪽 구성 순서와 반드시 같아야 한다.**

    대응(실측 확인, 05.hwp cs#1·#7·#48):
        faces[k]  ↔ FaceName{Hangul,Latin,Hanja,Japanese,Other,Symbol,User}
        ratio[k]  ↔ Ratio*      spacing[k] ↔ Spacing*
        relsize[k]↔ Size*       offset[k]  ↔ Offset*
        height    ↔ Height      color      ↔ TextColor
        ul_color  ↔ UnderlineColor   shade ↔ ShadeColor   shadow ↔ ShadowColor
        attr 하위 17비트 ↔ Italic·Bold·UnderlineType·UnderlineShape·OutLineType·
                          ShadowType·Emboss·Engrave·SuperScript·SubScript
    """
    attr = shape["attr"] & _ATTR_MASK
    fp = []
    fp.extend(face_names)
    fp.extend(shape["ratio"])
    fp.extend(shape["spacing"])
    fp.extend(shape["relsize"])
    fp.extend(shape["offset"])
    # ⚠ **꺼져 있는 속성의 색은 0으로 눕힌다.** 바이너리(.hwp)는 밑줄이 없어도 밑줄색을,
    #   그림자가 없어도 그림자색(기본 0xB2B2B2)을 들고 있는데 OWPML(.hwpx)은 그 요소
    #   자체가 없다. 눕히지 않으면 **같은 원고인데 형식만 달라도 지문이 전부 어긋난다**
    #   (실측: 05 표본 23종이 그림자색 한 칸 때문에 23종 모두 불일치했다).
    #   ⚠ 브리지의 COM 지문(`_charshape_fingerprint`)도 **같은 규칙**을 써야 한다.
    ul_color = shape["ul_color"] if (attr >> 2) & 3 else 0
    shadow = shape["shadow"] if (attr >> 11) & 3 else 0
    fp.extend([shape["height"], attr, shape["color"], ul_color,
               shape["shade"], shadow])
    return fp


# ══════════════════════════════════════════════════════════════════════
# ▌OWPML(.hwpx)
# ══════════════════════════════════════════════════════════════════════
#
# 같은 지문을 내야 하므로 OWPML의 **열거값을 숫자로** 되돌려야 한다. 아래 대응은
# 같은 원고를 .hwp/.hwpx 두 형식으로 저장해 지문이 **완전히 같아지는지**로 검증했다
# (틀리면 그 스타일 한 줄이 어긋나 바로 드러난다).
_UL_TYPE = {"NONE": 0, "BOTTOM": 1, "CENTER": 2, "TOP": 3}
# ⚠ 실선(SOLID)이 **0**이다 — 1로 두면 밑줄 있는 스타일이 전부 어긋난다(실측으로 잡음).
_LINE_SHAPE = {"NONE": 0, "SOLID": 0, "DASH": 1, "DOT": 2, "DASH_DOT": 3,
               "DASH_DOT_DOT": 4, "LONG_DASH": 5, "CIRCLE": 6, "DOUBLE_SLIM": 7,
               "SLIM_THICK": 8, "THICK_SLIM": 9, "SLIM_THICK_SLIM": 10,
               "WAVE": 11, "DOUBLE_WAVE": 12, "THICK_3D": 13,
               "THICK_3D_REVERSE": 14, "3D": 15, "3D_REVERSE": 16}
_OUTLINE_TYPE = {"NONE": 0, "SOLID": 1, "DOT": 2, "THICK": 3, "DASH": 4,
                 "DASH_DOT": 5, "DASH_DOT_DOT": 6}
_SHADOW_TYPE = {"NONE": 0, "DROP": 1, "CONT": 2, "CONTINUOUS": 2}

# OWPML 글꼴 목록의 언어 이름 — 바이너리 7칸과 **같은 순서**여야 한다.
_OWPML_LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
_OWPML_LANG_ATTRS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")

_COLOR_NONE = 0xFFFFFFFF


def _owpml_color(value):
    """OWPML 색 문자열 → COM/COLORREF 정수.

    ⚠ OWPML은 `#RRGGBB`인데 COLORREF는 **0x00BBGGRR**이라 바이트를 뒤집어야 한다
      (안 뒤집으면 빨강 스타일 하나 때문에 지문이 통째로 어긋난다).
    ⚠ 'none'은 **0xFFFFFFFF**다(바이너리의 음영색 FFFFFFFF와 같은 값).
    """
    if not value or value == "none":
        return _COLOR_NONE
    v = value.lstrip("#")
    try:
        n = int(v, 16)
    except ValueError:
        return _COLOR_NONE
    r, g, b = (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF
    return (b << 16) | (g << 8) | r


def _tag(elem):
    t = elem.tag
    return t.rsplit("}", 1)[-1] if "}" in t else t


def _child(elem, name):
    for c in elem:
        if _tag(c) == name:
            return c
    return None


class _StrikeAsUnderline:
    """`<hh:strikeout>`를 '밑줄 종류 2'로 읽히게 감싼다(위 주석 참조)."""

    def __init__(self, strike):
        self._shape = strike.get("shape") or "SOLID"
        self._color = strike.get("color")

    def get(self, name, default=None):
        if name == "type":
            return "CENTER"          # → _UL_TYPE 2
        if name == "shape":
            return self._shape
        if name == "color":
            return self._color
        return default


def _lang_ints(elem, default=0):
    if elem is None:
        return [default] * _LANG_COUNT
    return [int(elem.get(a, default) or default) for a in _OWPML_LANG_ATTRS]


def _from_hwpx(file_path, log):
    """`.hwpx`(ZIP+OWPML) → 글자 스타일 지문. 실패하면 빈 목록."""
    try:
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(file_path) as z:
            header = z.read("Contents/header.xml")
        root = ET.fromstring(header)

        # 언어별 글꼴 이름표
        fonts = {}
        for face in root.iter():
            if _tag(face) != "fontface":
                continue
            lang = (face.get("lang") or "").upper()
            names = {}
            for f in face:
                if _tag(f) == "font":
                    names[f.get("id")] = f.get("face") or ""
            fonts[lang] = names

        # 글자 모양
        shapes = {}
        for cp in root.iter():
            if _tag(cp) != "charPr":
                continue
            ul = _child(cp, "underline")
            sh = _child(cp, "shadow")
            ol = _child(cp, "outline")
            # ⚠ **취소선은 바이너리에서 '밑줄 종류 2'로 들어간다**(실측: OWPML이
            #   `<hh:strikeout shape="SOLID"/>`로 적은 스타일을 .hwp는 attr bit3로
            #   적었다). OWPML은 밑줄과 취소선을 따로 적으므로 여기서 합쳐 준다 —
            #   안 그러면 취소선 스타일만 지문이 어긋나 보호에서 빠진다.
            if ul is None:
                st = _child(cp, "strikeout")
                if st is not None:
                    ul = _StrikeAsUnderline(st)
            attr = ((1 if _child(cp, "italic") is not None else 0)
                    | (1 if _child(cp, "bold") is not None else 0) << 1
                    | (_UL_TYPE.get((ul.get("type") or "NONE").upper(), 0)
                       if ul is not None else 0) << 2
                    | (_LINE_SHAPE.get((ul.get("shape") or "NONE").upper(), 0)
                       if ul is not None else 0) << 4
                    | (_OUTLINE_TYPE.get((ol.get("type") or "NONE").upper(), 0)
                       if ol is not None else 0) << 8
                    | (_SHADOW_TYPE.get((sh.get("type") or "NONE").upper(), 0)
                       if sh is not None else 0) << 11
                    | (1 if _child(cp, "emboss") is not None else 0) << 13
                    | (1 if _child(cp, "engrave") is not None else 0) << 14
                    | (1 if _child(cp, "supscript") is not None else 0) << 15
                    | (1 if _child(cp, "subscript") is not None else 0) << 16)
            ref = _child(cp, "fontRef")
            names = []
            for k, lang in enumerate(_OWPML_LANGS):
                fid = ref.get(_OWPML_LANG_ATTRS[k]) if ref is not None else None
                names.append(fonts.get(lang, {}).get(fid, ""))
            shapes[cp.get("id")] = {
                "names": names,
                "ratio": _lang_ints(_child(cp, "ratio"), 100),
                "spacing": _lang_ints(_child(cp, "spacing"), 0),
                "relsize": _lang_ints(_child(cp, "relSz"), 100),
                "offset": _lang_ints(_child(cp, "offset"), 0),
                "height": int(cp.get("height") or 0),
                "attr": attr,
                "color": _owpml_color(cp.get("textColor")),
                "ul_color": (_owpml_color(ul.get("color")) if ul is not None else 0),
                "shade": _owpml_color(cp.get("shadeColor")),
                "shadow": (_owpml_color(sh.get("color")) if sh is not None else 0),
            }

        out, seen = [], {}
        for st in root.iter():
            if _tag(st) != "style" or (st.get("type") or "").upper() != "CHAR":
                continue
            sh = shapes.get(st.get("charPrIDRef"))
            if sh is None:
                continue
            fp = _fingerprint(sh, sh["names"])
            key = tuple(fp)
            # 스타일 `id`는 .hwp의 스타일 순번과 같은 체계다(= COM `HStyle.Apply`).
            sid = int(st.get("id")) if (st.get("id") or "").isdigit() else None
            if key in seen:
                if sid is not None:
                    seen[key]["ids"].append(sid)
                continue
            seen[key] = {"name": st.get("name") or "", "fp": fp,
                         "ids": [] if sid is None else [sid]}
            out.append(seen[key])
        return out
    except Exception as exc:
        log(f"[글자 스타일] hwpx 스타일 정보를 읽지 못했습니다({exc}) — 스타일 보호 없이 진행")
        return []


def char_style_fingerprints(file_path, logger=None):
    """문서에 정의된 **글자 스타일**들의 글자모양 지문 목록.

    Returns: [{"name": "본문강조", "fp": [...], "ids": [스타일 순번, …]}, …] — 실패하면 **빈 목록**.
    `ids`는 그 지문을 쓰는 글자 스타일들의 순번(DocInfo `HWPTAG_STYLE` 순서 = OWPML 스타일
    `id` = COM `HStyle.Apply`)이다. 브리지가 '이 글자가 글자 스타일 소속인가'를 판정하는 데 쓴다.
    """
    def log(msg):
        if logger:
            try:
                logger(msg)
            except Exception:
                pass

    ext = os.path.splitext(file_path or "")[1].lower()
    if ext == ".hwpx":
        return _from_hwpx(file_path, log)
    if ext != ".hwp":
        return []
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        docinfo = _Cfb(raw).read("DocInfo")
        try:
            docinfo = zlib.decompress(docinfo, -15)
        except zlib.error:
            pass        # 압축 안 된 문서 — 그대로 쓴다

        counts, face_names, shapes, styles = None, [], [], []
        for tag, body in _records(docinfo):
            if tag == _TAG_ID_MAPPINGS:
                counts = list(struct.unpack_from("<%di" % (len(body) // 4), body, 0))
            elif tag == _TAG_FACE_NAME:
                face_names.append(_parse_face_name(body))
            elif tag == _TAG_CHAR_SHAPE:
                shapes.append(_parse_char_shape(body))
            elif tag == _TAG_STYLE:
                styles.append(_parse_style(body))

        # 글꼴 이름표는 언어 그룹별로 따로 매겨진다 — 그룹 시작점을 미리 구해 둔다.
        #   counts[1..7]이 한글·영어·한자·일어·기타·기호·사용자 글꼴 수다.
        if not counts or len(counts) < 8:
            log("[글자 스타일] 글꼴 매핑 정보를 읽지 못해 스타일 보호를 건너뜁니다")
            return []
        bases, acc = [], 0
        for k in range(1, 1 + _LANG_COUNT):
            bases.append(acc)
            acc += counts[k]

        out = []
        seen = {}
        for sid, st in enumerate(styles):
            if st["kind"] != _STYLE_KIND_CHAR:
                continue
            cid = st["char_shape"]
            if cid >= len(shapes):
                continue
            sh = shapes[cid]
            names = []
            for k in range(_LANG_COUNT):
                idx = bases[k] + sh["faces"][k]
                names.append(face_names[idx] if idx < len(face_names) else "")
            fp = _fingerprint(sh, names)
            key = tuple(fp)
            if key in seen:
                seen[key]["ids"].append(sid)    # 같은 글자모양을 쓰는 스타일이 여럿이면 한 줄에
                continue
            seen[key] = {"name": st["name"], "fp": fp, "ids": [sid]}
            out.append(seen[key])
        return out
    except Exception as exc:
        log(f"[글자 스타일] 스타일 정보를 읽지 못했습니다({exc}) — 스타일 보호 없이 진행")
        return []
