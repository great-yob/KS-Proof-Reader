"""
version.py — 앱 버전 단일 출처(Single Source of Truth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ **의존성 없는 최상위 모듈로 유지할 것.** 빌드 스크립트(build_dist.py)와 UI
  (ui/widgets/sidebar.py), 그리고 향후 자동 업데이터가 모두 이 파일 하나를 본다.
  PySide6·core를 import하면 빌드 스크립트가 앱 전체를 끌어오게 되므로 금지.

릴리스 절차:
  1. 여기 APP_VERSION 을 올린다 (semver: MAJOR.MINOR.PATCH).
  2. `python build_dist.py` 로 배포본을 만든다.
  3. GitHub Releases에 태그 `v{APP_VERSION}` 으로 올린다.
     → 자동 업데이터가 이 태그와 APP_VERSION을 비교해 갱신 여부를 판단한다.
"""

APP_VERSION = "1.0.7"

# ── 데이터 버전 (앱 버전과 **독립적으로** 올라간다) ──────────────────────
# 사전 데이터(stdict.db + kiwipiepy 모델) 스냅샷을 가리킨다. 형식 YYYY.MM.
#   앱  : 코드가 바뀔 때 semver로 올린다 — 자주, 작다(수십 MB)
#   데이터: 사전이 갱신될 때 YYYY.MM으로 올린다 — 드물다, 크다(283MB)
# 둘을 분리해야 코드 수정 한 줄에 283MB를 재배포하지 않는다.
#
# ⚠ 이 값은 '이 빌드가 패키징한 데이터'를 뜻한다. 실제 **로드된** 데이터 버전은
#   stdict.db의 meta.data_version이 정답이며 `nikl_dict.data_version()`으로 읽는다.
#   UI·업데이터는 DB 쪽을 우선한다(파일이 어긋나면 즉시 드러나도록).
DATA_VERSION = "2026.07"

# GitHub 릴리스 저장소 — 자동 업데이트 확인 대상(KS-Works-Utility와 동일 계정 규약).
#   아직 저장소를 만들지 않았다면 업데이터는 graceful하게 비활성된다.
GITHUB_OWNER = "great-yob"
GITHUB_REPO  = "KS-Proof-Reader"


def release_tag(version: str = APP_VERSION) -> str:
    """앱 배포 태그 — GitHub Releases 태그 규약."""
    return f"v{version}"


def data_tag(version: str = DATA_VERSION) -> str:
    """데이터 배포 태그 — 앱 릴리스와 **다른 네임스페이스**를 쓴다.

    업데이터가 두 채널을 태그 접두사로 구분한다: `v1.0.0` / `data-2026.07`.
    """
    return f"data-{version}"
