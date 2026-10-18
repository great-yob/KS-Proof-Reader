# HWP 브리지 (64비트 ↔ 32비트 COM)

> CLAUDE.md에서 옮긴 **상세 참조**다(2026-10-18 재구성 — 서술은 원문 그대로 옮기고 절 제목만 붙였다). CLAUDE.md에는 이 영역의 불변식·금지선 요약만 남았다.
> 설계·동작을 바꾸면 **이 문서와 CLAUDE.md 요약을 함께** 고칠 것(메모리 `dead-code-and-stale-memory-hygiene`).

## The 64-bit ↔ 32-bit HWP bridge

Hancom HWP's COM server (`HWPFrame.HwpObject`) is **32-bit only**, but the v5 app runs as **64-bit** PySide6. Direct `win32com.client.Dispatch` from the main process will fail.

The workaround in [core/hwp_editor.py](../core/hwp_editor.py):
1. `HwpEditor` (64-bit, in-process) spawns a 32-bit Python subprocess running [core/hwp_bridge_worker.py](../core/hwp_bridge_worker.py).
2. Communication is line-delimited JSON over stdin/stdout. Commands: `open`, `get_text`, `apply`, `save_as`, `close`, `quit`.
3. **`_bridge_command()` picks the worker**: a bundled `bridge32/hwp_bridge_worker.exe` next to the EXE wins; otherwise it falls back to `_find_python32()` + the `.py` script (dev only). 32-bit Python is discovered via `PYTHON32_PATH`, then hardcoded candidate paths (`…\Python311-32\…`), then `shutil.which`.

## 배포본 bridge32

⚠ **Distributed builds must ship `bridge32/`** — `build_dist.py` freezes the worker with the *32-bit* interpreter into its own onedir (22MB) and stages it beside the EXE. Before this existed (fixed 2026-07-22) the released zip had **no HWP correction at all**: PyInstaller never bundled `hwp_bridge_worker.py`, so the path didn't exist, and even with it the user's PC would have needed 32-bit Python + pywin32 (candidate path #1 was a dev-machine path). `verify()` now fails the build if `bridge32/hwp_bridge_worker.exe` is missing **or its PE header says 64-bit** — a 64-bit worker installs fine and then fails only on the user's machine, at `Dispatch`. Two build-side details are load-bearing: the worker is frozen `--console` (a `--windowed` build has no usable stdout, which *is* the protocol channel; the parent hides the window with `CREATE_NO_WINDOW`), and `pyhwpx` is `--exclude-module`d (its `import` inside `_security_dll_path()` drags in PyQt5 → 85MB; only its `FilePathCheckerModule.dll` is wanted, and that gets copied in as a file). The worker also disables `win32com` gen_py cache rebuilding when frozen — it printed to stdout, i.e. into the JSON stream.

When changing HWP behavior, edit both sides of the bridge and keep the JSON protocol in sync. Don't try to "simplify" by removing the subprocess — it's load-bearing.

## SaveAs·PDF 실패 판정 · 매치 상한

⚠ **`SaveAs`/PDF export fail by return value, not by exception** — when the target is open in another 한/글 (or a PDF viewer), `SaveAs` returns `False` and the file is untouched. The bridge used to ignore that and report success; `close`'s `Quit()` then raised a **'저장할까요?' modal for the *original* file** (default button 저장 — pressing it overwrote the original with the corrections, measured) and the parent killed only the bridge, leaving a zombie hwp.exe. Now `save_as`/`export_pdf` require both a non-False return **and** a changed file stamp (`_file_stamp`/`_written`: mtime·size·id) and raise a "다른 프로그램에 열려 있으면 닫고" error otherwise, and `close` calls `Clear(1)` (hwpDiscard) before `Quit()` so no save prompt can ever appear. ⚠ Match caps count **only matches actually replaced/marked**: the old `max_iters=100` counted *skipped* matches too, so a card whose real occurrence was #155 of 172 was never applied and S3 reclassified the miss as '정상' (dashboard 실패 0). Caps are now `_APPLY_MAX_REPLACE`/`_APPLY_MAX_MATCHES`, `_LOCATE_MAX_OCC`, `_MEMO_MAX_OCC` (runaway guards only) with a progress heartbeat every `_APPLY_HEARTBEAT` matches so the parent's idle timeout doesn't fire on long words.

## 한/글 모달 숨김 교착 (2026-08-21 실사고)

★**한/글이 띄운 모달을 우리가 숨겨 영구 대기에 빠질 수 있다** (배포 첫날 실사고 2026-08-21 — 다른 직원 PC 전부가 `[원고 추출 시작]`에서 120초 무응답). `_WindowHider`는 새 `hwp.exe`의 **보이는 최상위 창을 전부** `SW_HIDE`하는데, 여기엔 사용자 등록·개인정보 동의·문서 복구·자동화 접근 승인 **모달도 포함**된다. 모달이 숨겨진 채 `Dispatch`/`Open`이 블록되면 사용자에겐 보이지도 클릭할 수도 없다. ★**원인은 보안 모듈 등록 차이이고, 등록 위치는 COM ProgID가 아니라 한/글 전용 per-user 키다** (직원 PC 현장 확증 2026-08-21: 그 값이 실제로 **없었고**, 등록해 주자 전 배포 PC가 최종 단계까지 정상 구동) — `HKCU\Software\HNC\HwpAutomation\Modules\FilePathCheckerModule` = DLL 절대경로. 개발 PC엔 pyhwpx가 만든 이 값이 있어 `RegisterModule`이 True를 돌려주고 승인 모달이 우회되는데, pyhwpx를 깔 일이 없는 배포 PC엔 이 값이 없다. ⚠ COM ProgID는 개발 PC에도 **없다** — HKCR·WOW6432Node·HKCU\Software\Classes 어디에도 없이 True가 나온다. 그래서 `regsvr32`는 답이 아니고 (HKLM 쓰기라 `PrivilegesRequired=lowest`와도 맞지 않는다), **HKCU 값 하나만 쓰면 된다 — 관리자 권한 불필요**. `_ensure_security_module_registered()`가 `RegisterModule`보다 먼저 그 값을 채운다(이미 유효한 등록이 있으면 건드리지 않는다 — 앱 삭제 시 한/글이 사라진 DLL을 가리키지 않게). ⚠ 다른 후보도 살아 있다: **32비트 브리지 EXE 기동 실패**(백신/SmartScreen, 서명 없는 22MB EXE)는 배포본 로그상 COM 블록과 구분되지 않으므로, 진단 배치가 `{"cmd":"quit"}`만 먼저 보내 둘을 가른다. ⚠ '악성코드 차단 > 프로그램 실행 중 문서 열 때 차단' 옵션은 원인이 **아니다**(개발 PC는 켜진 채 정상 동작). 방어는 `_StallWatch`(20초 초과 시 `_WindowHider.unhide()`로 창을 되살리고 **1회만** 경고 — ⚠ 반복하면 stderr 출력이 부모의 유휴 타임아웃을 리셋해 120초가 영원히 오지 않는다)와, **호출 전에** 남기는 단계 로그(`[브리지] 워커 시작` / `[HWP] Dispatch 시도` / `파일 열기 시도` — 성공 후 로그는 블록되면 영영 오지 않는다), 그리고 부모가 보관한 `_last_worker_line`을 타임아웃 메시지에 싣는 것이다. ⚠ 브리지 진단 로그는 `activity_panel._DROP`이 `[Worker stderr]`·`Dispatch`·`[보안]`을 **화면에서 통째로 감추므로**, 사용자가 봐야 할 라인은 그 금칙어를 피하고 전용 `[태그]`를 달아야 한다(지연 경고는 `[한/글 대기]`). 현장 진단은 `KS_HWP_VISIBLE=1`(창 숨김 해제)과 [tools/hwp_bridge_diag.bat](../tools/hwp_bridge_diag.bat) — 후자는 **이미 설치된 배포본**의 `bridge32` EXE를 visible로 돌리므로 새 릴리스를 기다릴 필요가 없다.

## HwpEditor 팩토리 · 백엔드 2종

**`HwpEditor` is a factory, not a class.** [core/__init__.py](../core/__init__.py) exposes `HwpEditor(file_path, ...)` which returns one of two backends with the same interface:
- Default (all extensions, incl. `.hwpx`) → the COM bridge above (`_HwpComEditor`). Required because it's the **only** backend that can apply the red-text color marking on corrected spans.
- `KS_HWP_BACKEND=direct` + `.hwpx` → [core/hwpx_editor.py](../core/hwpx_editor.py) `HwpxEditor`, which edits the HWPX ZIP/OWPML XML directly (no Hancom process) but applies **text only, no color**. For fast bulk hwpx processing.
