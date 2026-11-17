#!/usr/bin/env bash
# deploy/.env의 SERVER_NAME(도메인)으로 Let's Encrypt 인증서를 받고 nginx를 HTTPS로 바꾼다.
#   sudo deploy/scripts/issue-cert.sh [certbot 옵션...]
#   예: sudo deploy/scripts/issue-cert.sh --email you@example.com      (약관 동의는 certbot이 물음)
#       sudo deploy/scripts/issue-cert.sh --staging                     (시험용 인증서로 먼저 확인)
# 먼저: 도메인 A 레코드 → 이 서버 공인 IP, NCP ACG·ufw에서 80·443 허용, deploy/scripts/deploy.sh로 nginx가 떠 있음.
# 갱신은 certbot이 설치한 systemd 타이머가 하루 두 번 확인하고, 갱신되면 install-cert.sh가 nginx를 다시 시작한다.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo $0 $*" >&2
  exit 1
fi
DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DEPLOY"

env_value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
domain=$(env_value SERVER_NAME)
webroot=$(env_value ACME_WEBROOT)
webroot=${webroot:-/var/www/certbot}

if [ -z "$domain" ] || [ "$domain" = "_" ]; then
  echo "Set SERVER_NAME=<domain> in deploy/.env first (e.g. career.example.com), then run deploy/scripts/deploy.sh." >&2
  exit 1
fi
if ! printf '%s' "$domain" | grep -Eq '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$'; then
  echo "SERVER_NAME must be a domain name such as career.example.com (an IP address cannot get a certificate)." >&2
  exit 1
fi
command -v certbot >/dev/null || { echo "certbot not found: run deploy/scripts/setup-server.sh" >&2; exit 1; }

# certbot보다 먼저: 이 도메인의 80번이 정말 이 서버의 nginx로 오는지 확인한다 (실패를 반복하면 Let's Encrypt가 잠시 막음).
# 서버가 자기 공인 IP로 나갔다 돌아오지 못하는 네트워크면 이 확인만 실패한다: 그때는 SKIP_PROBE=1로 건너뛴다.
if [ "${SKIP_PROBE:-0}" != "1" ]; then
  probe="vc-probe-$(openssl rand -hex 8)"
  install -d -m 755 "$webroot/.well-known/acme-challenge"
  echo ok > "$webroot/.well-known/acme-challenge/$probe"
  chmod 644 "$webroot/.well-known/acme-challenge/$probe"
  if ! curl -fsS --max-time 10 "http://$domain/.well-known/acme-challenge/$probe" 2>/dev/null | grep -qx ok; then
    rm -f "$webroot/.well-known/acme-challenge/$probe"
    echo "http://$domain does not reach this server's nginx yet." >&2
    echo "Check: DNS A record of $domain -> this server's public IP, NCP ACG and ufw allow 80, 'docker compose ps' shows nginx running." >&2
    echo "If all of that is right, rerun with: sudo SKIP_PROBE=1 $0 $*" >&2
    exit 1
  fi
  rm -f "$webroot/.well-known/acme-challenge/$probe"
fi

certbot certonly --webroot -w "$webroot" -d "$domain" \
  --deploy-hook "$DEPLOY/scripts/install-cert.sh" "$@"

# 이미 받은 인증서라 certbot이 훅을 부르지 않았어도 nginx에 넣는다.
RENEWED_LINEAGE="/etc/letsencrypt/live/$domain" "$DEPLOY/scripts/install-cert.sh"
echo "HTTPS ready: https://$domain"
