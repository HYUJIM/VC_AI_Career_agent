#!/usr/bin/env bash
# 이미지를 (다시) 빌드하고 서비스를 띄운다. 코드를 받은 뒤(git pull)마다 실행한다.
#   deploy/scripts/deploy.sh
# 순서: postgres → migrate(스키마, 끝나면 종료) → ai → backend(healthy) → nginx. 실패하면 로그 보는 명령을 알려 준다.
set -euo pipefail

DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DEPLOY"
for f in .env backend.env ai.env secrets/db-tls/ca.crt secrets/db-tls/server.crt secrets/db-tls/server.key; do
  if [ ! -s "$f" ]; then
    echo "missing deploy/$f: run deploy/scripts/init-secrets.sh first" >&2
    exit 1
  fi
done

# --pull: 베이스 이미지(python, node, nginx)의 보안 업데이트를 받는다.
docker compose build --pull
docker compose pull postgres
if ! docker compose up -d --remove-orphans; then
  echo >&2
  echo "Start failed. Check: docker compose -f $DEPLOY/docker-compose.yml logs --tail=100 migrate backend ai" >&2
  exit 1
fi

docker compose ps
# 쓰지 않는 옛 이미지를 지워 디스크(50 GB)를 아낀다.
docker image prune -f >/dev/null
echo
echo "Deployed. Check: curl -sS -o /dev/null -w '%{http_code}\n' http://localhost/ (expect 200, or 301 in HTTPS mode)"
