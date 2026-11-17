#!/bin/sh
# nginx 이미지의 /docker-entrypoint.d에서 시작할 때마다 실행된다.
# /etc/nginx/tls에 인증서가 있고 SERVER_NAME(도메인)이 있으면 HTTPS 설정을, 아니면 HTTP 설정을 conf.d/default.conf로 쓴다.
# 인증서를 처음 받은 뒤에는 컨테이너를 다시 시작하면 HTTPS로 바뀐다 (scripts/install-cert.sh가 함).
set -eu

name="${SERVER_NAME:-_}"
case "$name" in
  _) ;;
  *[!A-Za-z0-9.-]* | "" | .* | -*)
    echo "40-vc-site: SERVER_NAME must be a host name such as career.example.com (got an invalid value)" >&2
    exit 1
    ;;
esac

if [ "$name" != "_" ] && [ -s /etc/nginx/tls/fullchain.pem ] && [ -s /etc/nginx/tls/privkey.pem ]; then
  mode=https
else
  mode=http
fi

SERVER_NAME="$name" envsubst '${SERVER_NAME}' < "/etc/nginx/vc/site-$mode.conf.template" > /etc/nginx/conf.d/default.conf
echo "40-vc-site: serving $mode (server_name $name)"
