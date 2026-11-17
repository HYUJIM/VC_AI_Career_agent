#!/bin/bash
# PostgreSQL 시작 래퍼 (deploy/docker-compose.yml의 postgres entrypoint).
# PostgreSQL은 TLS 개인 키가 postgres 사용자 소유이고 600이어야 읽는다. 서버에서 마운트한 파일은 운영자 소유라
# 컨테이너 안으로 복사해 소유자·권한을 맞춘 뒤 이미지의 원래 entrypoint로 넘긴다.
set -euo pipefail

src=/opt/vc/tls-src
dst=/run/vc-tls
for f in server.crt server.key; do
  if [ ! -s "$src/$f" ]; then
    echo "missing $src/$f: run deploy/scripts/init-secrets.sh first" >&2
    exit 1
  fi
done
install -d -o postgres -g postgres -m 700 "$dst"
install -o postgres -g postgres -m 644 "$src/server.crt" "$dst/server.crt"
install -o postgres -g postgres -m 600 "$src/server.key" "$dst/server.key"

# TCP 접속은 TLS만 받는다 (pg_hba.conf의 hostssl). BE·마이그레이션은 PGSSLMODE=verify-full로 이 인증서를 확인한다.
exec docker-entrypoint.sh "$@" \
  -c ssl=on \
  -c ssl_cert_file="$dst/server.crt" \
  -c ssl_key_file="$dst/server.key" \
  -c ssl_min_protocol_version=TLSv1.2 \
  -c hba_file=/opt/vc/pg_hba.conf
