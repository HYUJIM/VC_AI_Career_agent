#!/bin/bash
# 새 DB 볼륨을 처음 만들 때 한 번만 실행된다 (이미지의 docker-entrypoint-initdb.d).
# - vc_owner: DB와 테이블을 소유하는 마이그레이션 계정 (슈퍼유저 아님)
# - vc_app:   BE 서비스 계정. 권한은 마이그레이션(DB_APP_ROLE=vc_app)이 앱 테이블 DML만 준다.
# 스키마·시드 SQL은 여기서 넣지 않는다. 스키마는 migrate 서비스가 만들고, 공개 비밀번호의 시드 계정은 운영에 넣지 않는다.
set -euo pipefail
: "${DB_NAME:?}" "${DB_OWNER_PASSWORD:?}" "${DB_APP_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v db="$DB_NAME" -v owner_pw="$DB_OWNER_PASSWORD" -v app_pw="$DB_APP_PASSWORD" <<'SQL'
CREATE ROLE vc_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE vc_app LOGIN PASSWORD :'app_pw';
CREATE DATABASE :"db" OWNER vc_owner;
SQL
echo "10-roles.sh: created roles vc_owner, vc_app and database $DB_NAME"
