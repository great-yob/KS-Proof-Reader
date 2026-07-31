#!/usr/bin/env bash
# 턴이 끝나면 릴리스 자동 승인 잠금을 해제하는 Stop 훅.
# 자동 승인 범위를 "지금 돌고 있는 릴리스 턴"으로 못 박는 장치다.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
rm -f "$ROOT/.claude/.release-autoapprove" 2>/dev/null
exit 0
