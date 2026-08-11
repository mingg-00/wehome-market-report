#!/bin/bash
# cron/launchd가 호출하는 진입점 — 빌드→Vercel 프로덕션 배포까지 한 번에 돈다.
# wehome-newsletter/newsletter/run_scheduled.sh와 같은 패턴(고정 경로·타임스탬프
# 로그·PIPESTATUS로 종료코드 보존). set -e라 build_site.py가 실패하면 그 자리에서
# 멈추고 배포 단계로 안 넘어간다 — 깨진 빌드를 프로덕션에 올리는 일은 없어야 한다.
set -euo pipefail

# launchd는 로그인 셸 PATH를 안 물려받는다(기본 /usr/bin:/bin:/usr/sbin:/sbin뿐) —
# bare `python3`가 requests 없는 시스템 파이썬으로 잡히고, npx조차 자기 내부에서
# `node`를 PATH로 못 찾아 죽는 걸 둘 다 실측으로 확인했다. PATH를 직접 고정한다.
# anaconda3가 homebrew보다 먼저 와야 한다 — 둘 다 python3가 있는데 순서가 바뀌면
# (실제로 한번 그렇게 써서 실패했다) dotenv 등 conda 쪽에만 깔린 패키지를 못 찾는다.
# .zshrc의 conda init 훅도 항상 anaconda3를 PATH 맨 앞에 넣는다 — 그거랑 맞춘다.
export PATH="/Applications/anaconda3/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_DIR="/Users/gimminji/현장실습/wehome-market-report"
cd "$PROJECT_DIR"

mkdir -p "$PROJECT_DIR/logs"
LOGFILE="$PROJECT_DIR/logs/publish_$(date +%Y%m%d_%H%M%S).log"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') 발행 시작 ==="
  # set -e라 테스트가 깨지면 여기서 멈춘다 — 매일 무인으로 도는 경로라, 깨진 코드가
  # 조용히 프로덕션까지 가는 걸 막는 유일한 관문이다. 파일별 __main__ 러너 대신
  # pytest로 한 번에 도는 이유는 러너가 빠진 파일이 생겨도 여기선 안 새기 때문.
  python3 -m pytest -q
  python3 build_site.py
  VERCEL_TOKEN=$(grep '^VERCEL_TOKEN=' .env | cut -d= -f2)
  npx --yes vercel@latest deploy --prod --token="$VERCEL_TOKEN" --yes --cwd site
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') 발행 완료 ==="
} 2>&1 | tee "$LOGFILE"
exit "${PIPESTATUS[0]}"
