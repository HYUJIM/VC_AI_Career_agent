#!/usr/bin/env bash
# 새 NCP 서버(Ubuntu 22.04/24.04)를 한 번 준비한다:
#   Docker Engine + Compose 플러그인, 방화벽(ufw: 22·80·443), certbot, 자동 보안 업데이트, 스왑 2 GB,
#   시간대 Asia/Seoul, 인증서 webroot(/var/www/certbot), 백업 폴더(/var/backups/vc-career).
#   sudo deploy/scripts/setup-server.sh
# 여러 번 실행해도 된다. Rocky Linux는 다루지 않는다 (deploy/README.md "OS").
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
  echo "This script supports Ubuntu only (found: ${PRETTY_NAME:-unknown})." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg ufw certbot unattended-upgrades openssl

# ── Docker Engine (Docker 공식 apt 저장소) ──
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
# sudo로 실행한 운영자 계정이 sudo 없이 docker를 쓰게 한다 (다시 로그인하면 적용. docker 그룹은 root와 같은 권한).
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG docker "$SUDO_USER"
fi

# ── 방화벽: SSH·HTTP·HTTPS만. NCP ACG(서버 앞 방화벽)에도 같은 규칙을 둔다. ──
# Docker가 연 포트(80·443)는 ufw 규칙과 별개로 열린다. 이 구성은 그 둘만 연다.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ── 자동 보안 업데이트 ──
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# ── 스왑 2 GB (메모리 4 GB에서 이미지 빌드·FE 빌드가 몰릴 때 대비) ──
if ! swapon --show --noheadings | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

timedatectl set-timezone Asia/Seoul || true
install -d -m 755 /var/www/certbot
install -d -m 700 /var/backups/vc-career

echo
docker --version
docker compose version
echo "Server ready. Log out and back in once so '${SUDO_USER:-your user}' can run docker without sudo."
