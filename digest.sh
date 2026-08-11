#!/bin/bash
# launchd가 매일 호출하는 진입점 — subscribe_server.py(Railway)의 관리자 엔드포인트를
# 트리거만 한다. 구독자마다 수신 주기(매주/2주마다/매달)가 달라 "찬" 날짜도 사람마다
# 다르다 — 트리거는 매일 돌고, 실제로 누구에게 보낼지는 서버의 sub.due_for_digest()가
# 매번 판단한다. publish.sh와 똑같은 이유로 PATH를 직접 고정한다(launchd는 로그인 셸
# PATH를 안 물려받아 curl은 시스템 기본 경로에 있어 보통 괜찮지만, 일관성을 위해 맞춘다).
set -euo pipefail
export PATH="/Applications/anaconda3/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_DIR="/Users/gimminji/현장실습/wehome-market-report"
cd "$PROJECT_DIR"

mkdir -p "$PROJECT_DIR/logs"
LOGFILE="$PROJECT_DIR/logs/digest_$(date +%Y%m%d_%H%M%S).log"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') 다이제스트 발송 트리거 ==="
  ENDPOINT=$(grep '^SUBSCRIBE_ENDPOINT=' .env | cut -d= -f2 | sed 's#/subscribe$##')
  SECRET=$(grep '^UNSUB_SECRET=' .env | cut -d= -f2)
  curl -sf -X POST "$ENDPOINT/admin/send-digest?token=$SECRET"
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') 완료 ==="
} 2>&1 | tee "$LOGFILE"
exit "${PIPESTATUS[0]}"
