#!/usr/bin/env bash
# 릴리스 중에만 도구 호출을 자동 승인하는 PreToolUse 훅.
#
# 원리: .claude/.release-autoapprove 잠금 파일이 있을 때만 permissionDecision=allow 를
#       내보낸다. 파일이 없으면 아무것도 출력하지 않으므로 평소 승인 흐름 그대로다.
#
# 안전장치 2종:
#   1) 잠금이 2시간(120분)보다 오래되면 무시한다 — 정리에 실패해 파일이 남아도
#      영구 무방비 상태가 되지 않는다.
#   2) Stop 훅이 턴이 끝날 때 잠금을 지운다(settings.local.json 참조).
set -u

# 경로는 스크립트 위치에서 유도한다 — CLAUDE_PROJECT_DIR은 윈도우식 역슬래시 경로라
# Git Bash에서 뒤섞이면 판정이 흔들린다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK="$ROOT/.claude/.release-autoapprove"

[ -f "$LOCK" ] || exit 0
[ -n "$(find "$LOCK" -mmin -120 2>/dev/null)" ] || exit 0

printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"릴리스 자동 승인(.claude/.release-autoapprove 활성)"},"suppressOutput":true}'
