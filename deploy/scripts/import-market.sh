#!/usr/bin/env bash
# (선택, 한 번) 직무별 시장 통계 스냅샷(ai/data/market_snapshots/*.json)을 DB에 넣는다. BE 서비스 계정(vc_app)으로 실행한다.
#   deploy/scripts/import-market.sh
# 서비스가 떠 있어야 한다 (deploy/scripts/deploy.sh 다음).
set -euo pipefail

DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$DEPLOY/.." && pwd)"
cd "$DEPLOY"
docker compose run --rm --no-deps \
  -v "$REPO/ai/data/market_snapshots:/app/market_snapshots:ro" \
  backend python -m app.jobs.market import --dir /app/market_snapshots "$@"
