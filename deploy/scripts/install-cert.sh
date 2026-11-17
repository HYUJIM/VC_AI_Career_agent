#!/usr/bin/env bash
# certbot deploy hook: 받은(갱신한) 인증서를 nginx 컨테이너(uid 101)가 읽을 수 있게 deploy/secrets/tls로 복사하고 nginx를 다시 시작한다.
# issue-cert.sh가 certbot에 등록하므로 갱신 때도 자동으로 불린다. 직접 부를 때:
#   sudo RENEWED_LINEAGE=/etc/letsencrypt/live/<domain> deploy/scripts/install-cert.sh
set -euo pipefail

DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
lineage="${RENEWED_LINEAGE:?RENEWED_LINEAGE is not set (certbot sets it; or pass /etc/letsencrypt/live/<domain>)}"
dst="$DEPLOY/secrets/tls"
nginx_uid=101

install -d -m 700 "$dst"
# compose가 먼저 만든 빈 폴더(root 소유)일 수 있어 소유자·권한을 다시 맞춘다.
chown "$nginx_uid:$nginx_uid" "$dst"
chmod 700 "$dst"
install -o "$nginx_uid" -g "$nginx_uid" -m 644 "$lineage/fullchain.pem" "$dst/fullchain.pem.new"
install -o "$nginx_uid" -g "$nginx_uid" -m 600 "$lineage/privkey.pem" "$dst/privkey.pem.new"
mv -f "$dst/fullchain.pem.new" "$dst/fullchain.pem"
mv -f "$dst/privkey.pem.new" "$dst/privkey.pem"

# 다시 만들어야 40-vc-site.sh가 지금 .env의 SERVER_NAME과 인증서로 HTTPS 설정을 고른다 (몇 초 끊김).
# restart가 아니라 recreate: SERVER_NAME을 고친 뒤 deploy.sh를 건너뛰었어도 새 값이 들어간다.
cd "$DEPLOY"
docker compose up -d --no-deps --force-recreate nginx
echo "install-cert: certificate installed, nginx recreated"
