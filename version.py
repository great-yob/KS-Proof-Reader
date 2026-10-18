"""
version.py — 앱 버전 단일 출처(Single Source of Truth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ **의존성 없는 최상위 모듈로 유지할 것.** 빌드 스크립트(build_dist.py)와 UI
  (ui/widgets/sidebar.py), 자동 업데이터(core/updater.py)가 모두 이 파일 하나를 본다.
  PySide6·core를 import하면 빌드 스크립트가 앱 전체를 끌어오게 되므로 금지.

릴리스 절차는 `/release` 스킬(.claude/skills/release/SKILL.md)이 정본이다.
  태그 규약: 앱 `v{APP_VERSION}` · 데이터 `data-{DATA_VERSION}` — 업데이터가 이 접두사로
  두 채널을 가르고(core/updater.py), 태그의 버전과 APP_VERSION을 비교해 갱신 여부를 판단한다.
"""

APP_VERSION = "1.8.5"

# ── 데이터 버전 (앱 버전과 **독립적으로** 올라간다) ──────────────────────
# 사전 데이터(stdict.db + kiwipiepy 모델) 스냅샷을 가리킨다. 형식 YYYY.MM.
#   앱  : 코드가 바뀔 때 semver로 올린다 — 자주, 작다(app.zip ~120MB)
#   데이터: 사전이 갱신될 때 YYYY.MM으로 올린다 — 드물다, 크다(data.zip ~138MB)
# 둘을 분리해야 코드 수정 한 줄에 사전 패키지를 재배포하지 않는다.
#
# ⚠ 이 값은 '이 빌드가 패키징한 데이터'를 뜻한다. 실제 **로드된** 데이터 버전은
#   stdict.db의 meta.data_version이 정답이며 `nikl_dict.data_version()`으로 읽는다.
#   UI·업데이터는 DB 쪽을 우선한다(파일이 어긋나면 즉시 드러나도록).
DATA_VERSION = "2026.09"

# GitHub 릴리스 저장소 — 자동 업데이트 확인 대상(KS-Works-Utility와 동일 계정 규약).
#   ⚠ 업데이터의 폴링 URL이므로 제품 표시명(KS-AI Editor)에 맞춰 바꾸지 말 것(CLAUDE.md).
GITHUB_OWNER = "great-yob"
GITHUB_REPO  = "KS-Proof-Reader"
