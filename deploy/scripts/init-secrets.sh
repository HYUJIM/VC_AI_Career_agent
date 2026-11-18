#!/usr/bin/env bash
# deploy/.env, backend.env, ai.env와 DB TLS 인증서를 만든다. 이미 있는 파일은 절대 덮어쓰지 않는다.
#   deploy/scripts/init-secrets.sh
# 비밀값은 화면에 출력하지 않는다. 만든 뒤 .env·backend.env는 서버 밖(비밀번호 관리자 등)에도 백업한다.
set -euo pipefail
umask 077

DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DEPLOY"
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

# "KEY=" 처럼 비어 있는 줄만 "KEY=<value>"로 채운다.
fill() {
  local file=$1 key=$2 value=$3
  awk -v k="$key" -v v="$value" \
    'index($0, k "=") == 1 && length($0) == length(k) + 1 { print k "=" v; next } { print }' \
    "$file" > "$file.tmp"
  mv "$file.tmp" "$file"
}

created=()
if [ ! -e .env ]; then
  cp .env.example .env
  fill .env POSTGRES_PASSWORD "$(openssl rand -hex 32)"
  fill .env DB_OWNER_PASSWORD "$(openssl rand -hex 32)"
  fill .env DB_APP_PASSWORD "$(openssl rand -hex 32)"
  fill .env AI_SERVICE_TOKEN "$(openssl rand -hex 32)"
  created+=(".env")
fi
if [ ! -e backend.env ]; then
  cp backend.env.example backend.env
  fill backend.env JWT_SECRET "$(openssl rand -hex 48)"
  fill backend.env HOLDER_KEY_ENCRYPTION_KEY "$(openssl rand -base64 32)"
  created+=("backend.env")
fi
if [ ! -e ai.env ]; then
  cp ai.env.example ai.env
  created+=("ai.env")
fi
chmod 600 .env backend.env ai.env

# ── DB TLS: 이 서버 전용 CA와 postgres 서버 인증서 (이름 postgres, 10년). BE·migrate가 verify-full로 확인한다. ──
tls=secrets/db-tls
if [ ! -s "$tls/ca.crt" ]; then
  install -d -m 700 secrets "$tls"
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  cat > "$work/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = vc-career internal DB CA
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
  cat > "$work/server.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = postgres
[v3_server]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:postgres
authorityKeyIdentifier = keyid,issuer
EOF
  openssl ecparam -name prime256v1 -genkey -noout -out "$tls/ca.key"
  openssl req -x509 -new -key "$tls/ca.key" -sha256 -days 3650 \
    -config "$work/ca.cnf" -extensions v3_ca -out "$tls/ca.crt"
  openssl ecparam -name prime256v1 -genkey -noout -out "$tls/server.key"
  openssl req -new -key "$tls/server.key" -config "$work/server.cnf" -out "$work/server.csr"
  openssl x509 -req -in "$work/server.csr" -CA "$tls/ca.crt" -CAkey "$tls/ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days 3650 -sha256 \
    -extfile "$work/server.cnf" -extensions v3_server -out "$tls/server.crt"
  # 인증서는 컨테이너의 비루트 사용자(BE uid 10001)도 읽어야 한다. 개인 키는 600 그대로 (postgres/start.sh가 root로 읽어 옮김).
  chmod 644 "$tls/ca.crt" "$tls/server.crt"
  chmod 600 "$tls/ca.key" "$tls/server.key"
  created+=("$tls/{ca,server}.{crt,key}")
fi

if [ ${#created[@]} -eq 0 ]; then
  echo "Nothing to do: deploy/.env, backend.env, ai.env and $tls already exist (not changed)."
else
  echo "Created (values not shown): ${created[*]}"
  echo "Next: fill DXWORKS_*, ANTHROPIC_API_KEY etc. in backend.env / ai.env, set SERVER_NAME in .env when the domain is ready."
  echo "Back up deploy/.env and deploy/backend.env outside this server: losing HOLDER_KEY_ENCRYPTION_KEY makes existing wallets unreadable."
fi
