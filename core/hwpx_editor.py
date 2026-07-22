"""
core/hwpx_editor.py — HWPX 파일 직접 편집기 (ZIP + XML)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HWPX는 ZIP 아카이브 안에 OWPML XML이 들어있는 포맷.
한글 프로그램을 띄우지 않고 직접 텍스트를 추출·치환한다.

구조:
  example.hwpx (ZIP)
  ├─ Contents/
  │   ├─ section0.xml      ← 본문 (여러 개일 수 있음)
  │   ├─ section1.xml
  │   └─ ...
  └─ ...

본문 XML 예 (네임스페이스 prefix는 파일마다 다를 수 있음):
  <hs:sec xmlns:hp="..." xmlns:hs="...">
    <hp:p>
      <hp:run charPrIDRef="0">
        <hp:t>실제 텍스트</hp:t>
      </hp:run>
    </hp:p>
  </hs:sec>

이번 1차 구현:
  - 모든 <hp:t> 노드의 .text를 순서대로 합쳐서 본문 텍스트로 추출.
  - 적용 시 각 <hp:t> 노드 단위로 substring 치환 시도.
  - 한 <hp:t> 안에 원문이 통째로 들어가는 경우만 적용 (분할된 텍스트
    및 노드 경계를 넘는 매칭은 매칭 실패로 처리).
"""

import atexit
import os
import threading
import zipfile

from xml.etree import ElementTree as ET

from .models import Correction


# HWPX 본문 텍스트 노드의 로컬 태그명 ("hp:t" 의 "t" 부분)
_TEXT_LOCAL = "t"
# 본문 XML이 들어있는 ZIP 내부 디렉토리
_CONTENTS_DIR = "Contents/"


class HwpxEditor:
    """HWPX 파일 직접 편집기 — HwpEditor와 같은 인터페이스"""

    _active_instances = []

    def __init__(self, file_path: str, logger=None, visible: bool = False):
        self.file_path = file_path
        self.logger    = logger
        # 메모리상 작업: section 파일들의 (path, ET.ElementTree) 보관
        self._sections = []   # [(zip_path, ElementTree), ...]
        # ZIP 안의 다른 파일들 (그대로 복사할 항목)
        self._other_files = []   # [(zip_path, bytes), ...]

    # ══════════════════════════════════════════════
    # 공개 API
    # ══════════════════════════════════════════════
    def open(self):
        """ZIP을 열어 본문 XML들을 메모리에 로드"""
        if not os.path.isfile(self.file_path):
            raise FileNotFoundError(f"파일 없음: {self.file_path}")

        # 시그니처 검사 — .hwpx 확장자라도 한글 구버전은 OLE Compound 형식으로
        # 저장하는 경우가 있다. ZIP이 아니면 즉시 알린다.
        sig = self._read_signature()
        if not sig.startswith(b"PK"):
            kind = self._classify_signature(sig)
            raise RuntimeError(
                f"HWPX_NOT_ZIP: 이 파일은 ZIP 포맷이 아닙니다 "
                f"(감지: {kind}). 한글 프로그램을 통한 처리가 필요합니다."
            )

        if self.logger:
            self.logger(f"  HWPX 직접 편집 모드 ({os.path.basename(self.file_path)})")

        with zipfile.ZipFile(self.file_path, "r") as zf:
            # 원본의 entry 순서와 압축 방식을 보존해야 한글이 정상 인식.
            for info in zf.infolist():
                data = zf.read(info.filename)
                if (info.filename.startswith(_CONTENTS_DIR)
                        and info.filename.endswith(".xml")
                        and "section" in info.filename.lower()):
                    try:
                        tree = ET.ElementTree(ET.fromstring(data))
                        self._sections.append(
                            (info.filename, tree, info.compress_type)
                        )
                        continue
                    except ET.ParseError:
                        # 파싱 실패 시 그대로 다른 파일로 취급
                        pass
                self._other_files.append(
                    (info.filename, data, info.compress_type)
                )

        if not self._sections:
            raise RuntimeError("HWPX 본문(section) 파일을 찾지 못했습니다.")

        HwpxEditor._active_instances.append(self)

    def _read_signature(self, n: int = 8) -> bytes:
        """파일 시그니처(첫 n바이트)를 읽어 반환"""
        try:
            with open(self.file_path, "rb") as f:
                return f.read(n)
        except Exception:
            return b""

    @staticmethod
    def _classify_signature(sig: bytes) -> str:
        """시그니처로 포맷 추정 — 진단 메시지용"""
        if sig.startswith(b"PK\x03\x04") or sig.startswith(b"PK\x05\x06"):
            return "ZIP"
        if sig.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
            return "OLE Compound (한글 바이너리 .hwp 또는 구버전 .hwpx)"
        if sig.startswith(b"<?xml") or sig.startswith(b"<"):
            return "Raw XML (압축되지 않은 OWPML)"
        return f"알 수 없음 (헤더: {sig.hex()[:16]})"

    def get_text(self) -> str:
        """전체 본문 텍스트 — 모든 section의 <hp:t> 노드 .text를 이어붙임"""
        chunks = []
        for entry in self._sections:
            tree = entry[1]
            for t in self._iter_text_nodes(tree.getroot()):
                if t.text:
                    chunks.append(t.text)
            chunks.append("\n")   # 섹션 사이 줄바꿈
        return "".join(chunks).strip()

    def verify_originals(self, originals: list) -> dict:
        """각 원문이 문서에서 치환 가능한 형태로 존재하는지 검증 (COM 브리지와 인터페이스 동형).

        이 백엔드의 apply는 <hp:t> 노드 텍스트 치환이므로 '노드 텍스트에 존재'가 곧 도달
        가능이다. 노드 단위로 검사해 노드 경계에 걸친 원문은 False가 된다(치환 불가와 일치).
        """
        found = {o: False for o in originals}
        remain = set(originals)
        for entry in self._sections:
            if not remain:
                break
            tree = entry[1]
            for t in self._iter_text_nodes(tree.getroot()):
                if not remain:
                    break
                if not t.text:
                    continue
                for o in list(remain):
                    if o and o in t.text:
                        found[o] = True
                        remain.discard(o)
        return found

    def apply_corrections(self, corrections: list,
                          progress_cb=None,
                          stop_event: threading.Event = None) -> tuple:
        """
        교정 적용 — 각 항목을 모든 section의 <hp:t> 노드에 대해 치환 시도.

        Returns:
            tuple: (stats dict, detail list)
        """
        stats  = {"dict": 0, "ai_typo": 0, "ai_polish": 0, "fail": 0}
        detail = []

        # Correction 객체 또는 dict 모두 수용
        normalized = []
        for c in corrections:
            if isinstance(c, Correction):
                normalized.append({
                    "original":  c.original,
                    "corrected": c.corrected,
                    "reason":    c.reason,
                    "source":    c.source,
                    "color":     c.color,
                })
            elif isinstance(c, dict):
                normalized.append(c)

        # 빠른 접근을 위해 모든 텍스트 노드를 미리 수집
        all_text_nodes = []
        for entry in self._sections:
            tree = entry[1]
            for t in self._iter_text_nodes(tree.getroot()):
                all_text_nodes.append(t)

        # 진단용 전체 본문 (실패 사유 분류에만 사용 — 1회만 계산)
        full_text_cache = None

        total = len(normalized)
        for idx, item in enumerate(normalized):
            if stop_event is not None and stop_event.is_set():
                break

            original  = item.get("original", "")
            corrected = item.get("corrected", "")
            source    = item.get("source", "dict")
            reason    = item.get("reason", "")
            color     = item.get("color", 0)

            if not original or not corrected or original == corrected:
                continue

            replaced = self._replace_in_nodes(all_text_nodes, original, corrected)

            if replaced > 0:
                source_key = source if source in stats else "dict"
                stats[source_key] = stats.get(source_key, 0) + 1
                detail.append({
                    "original":  original, "corrected": corrected,
                    "reason":    reason,   "source":    source,
                    "color":     color,    "applied":   True,
                    "error":     "",
                })
            else:
                stats["fail"] += 1
                if full_text_cache is None:
                    full_text_cache = "".join(
                        (t.text or "") for t in all_text_nodes
                    )
                if original in full_text_cache:
                    err = "노드 경계에 걸친 텍스트 — 1차 구현 미지원"
                else:
                    err = "본문에 원문이 존재하지 않음"
                detail.append({
                    "original":  original, "corrected": corrected,
                    "reason":    reason,   "source":    source,
                    "color":     color,    "applied":   False,
                    "error":     err,
                })

            if progress_cb and ((idx + 1) % 5 == 0 or idx == total - 1):
                progress_cb(idx + 1, total)

        return stats, detail

    def save_as(self, output_path: str):
        """변경된 XML을 포함한 새 ZIP 생성.

        HWPX는 OOXML 계열이라 다음 규칙을 지켜야 한글이 정상 인식한다:
          1) `mimetype` 파일이 ZIP의 첫 entry
          2) `mimetype`은 무압축(STORED)
          3) 그 외 entry는 원본 순서·압축 방식 유지
        """
        # 수정된 section XML을 path→bytes 로 미리 만들어 둔다
        section_bytes = {
            entry[0]: self._serialize_tree(entry[1])
            for entry in self._sections
        }
        # 다른 파일들도 path→(bytes, compress_type) 로 매핑
        other_map = {
            entry[0]: (entry[1], entry[2])
            for entry in self._other_files
        }

        # 원본의 entry 순서를 그대로 가져온다 (mimetype을 먼저로 정렬)
        with zipfile.ZipFile(self.file_path, "r") as zf_orig:
            original_order = [info.filename for info in zf_orig.infolist()]

        def sort_key(name):
            return (0 if name == "mimetype" else 1,
                    original_order.index(name) if name in original_order else 99999)

        ordered = sorted(set(list(section_bytes) + list(other_map)), key=sort_key)

        tmp_out = output_path + ".tmp"
        with zipfile.ZipFile(tmp_out, "w") as zf:
            for path in ordered:
                if path in section_bytes:
                    data = section_bytes[path]
                    # section은 압축 (원본 방식 유지)
                    ctype = next(
                        (e[2] for e in self._sections if e[0] == path),
                        zipfile.ZIP_DEFLATED,
                    )
                else:
                    data, ctype = other_map[path]

                # mimetype은 무조건 STORED (HWPX 사양)
                if path == "mimetype":
                    ctype = zipfile.ZIP_STORED

                info = zipfile.ZipInfo(path)
                info.compress_type = ctype
                zf.writestr(info, data)

        # I8: os.replace는 동일 볼륨 내에서 아토믹 교체.
        #     기존 파일이 Excel/Word 등으로 잠겨있어도 OS 단에서 일관성 보장.
        os.replace(tmp_out, output_path)

    def close(self):
        """리소스 해제"""
        if self in HwpxEditor._active_instances:
            HwpxEditor._active_instances.remove(self)
        self._sections = []
        self._other_files = []

    # ══════════════════════════════════════════════
    # 내부 유틸
    # ══════════════════════════════════════════════

    @staticmethod
    def _iter_text_nodes(root):
        """root 아래의 모든 <hp:t> 텍스트 노드를 순회"""
        for elem in root.iter():
            # tag는 "{namespace}t" 형태. 로컬명만 비교.
            tag = elem.tag
            if isinstance(tag, str):
                local = tag.split("}", 1)[-1] if "}" in tag else tag
                if local == _TEXT_LOCAL:
                    yield elem

    @staticmethod
    def _replace_in_nodes(nodes, original: str, corrected: str) -> int:
        """각 텍스트 노드에서 substring 치환. 치환된 횟수 합계 반환."""
        total = 0
        for elem in nodes:
            text = elem.text
            if not text or original not in text:
                continue
            count = text.count(original)
            elem.text = text.replace(original, corrected)
            total += count
        return total

    @staticmethod
    def _serialize_tree(tree: ET.ElementTree) -> bytes:
        """ElementTree → bytes (XML 선언 + UTF-8)"""
        # ElementTree.write는 네임스페이스 prefix를 자동 관리.
        # 원본에 등록된 prefix를 보존하기 위해 xml_declaration=True 옵션 사용.
        from io import BytesIO
        buf = BytesIO()
        tree.write(buf, encoding="utf-8", xml_declaration=True)
        return buf.getvalue()


# ── atexit 핸들러 ────────────────────────────────
# I7: 비정상 종료 시 메모리에 로드된 ZIP/XML 참조를 풀어준다.
@atexit.register
def _cleanup_hwpx_instances():
    for editor in list(HwpxEditor._active_instances):
        try:
            editor.close()
        except Exception:
            pass


# ── HWPX 네임스페이스 등록 ────────────────────────
# OWPML 표준 네임스페이스들을 ET에 미리 등록하면 직렬화 시
# 깔끔한 prefix가 유지된다. (한컴 OWPML 2011/2013)
_HWPX_NS = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hhs":"http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "ht": "http://www.hancom.co.kr/hwpml/2011/template",
    "config": "http://www.hancom.co.kr/hwpml/2011/configuration",
}
for prefix, uri in _HWPX_NS.items():
    ET.register_namespace(prefix, uri)
