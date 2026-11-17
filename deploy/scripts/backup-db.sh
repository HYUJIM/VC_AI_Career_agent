#!/usr/bin/env bash
# DB 전체를 pg_dump(custom 형식)로 /var/backups/vc-career에 저장하고 14일 지난 것을 지운다.
#   sudo deploy/scripts/backup-db.sh
# 매일 새벽 4시 (root crontab, deploy/README.md "백업"):
#   0 4 * * * /opt/vc-career/deploy/scripts/backup-db.sh >> /var/log/vc-career-backup.log 2>&1
# 같은 디스크에만 있으면 서버를 잃을 때 함께 잃으므로, 가끔 서버 밖(내 PC·Object Storage)으로도 옮긴다.
set -euo pipefail
umask 077

DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
dir="${BACKUP_DIR:-/var/backups/vc-career}"
keep_days="${BACKUP_KEEP_DAYS:-14}"
cd "$DEPLOY"

db=$(grep -E '^DB_NAME=' .env | tail -n 1 | cut -d= -f2- || true)
db=${db:-vc_career_agent}
install -d -m 700 "$dir"
out="$dir/${db}-$(date +%Y%m%d-%H%M%S).dump"

docker compose exec -T postgres pg_dump -U postgres -d "$db" -Fc > "$out.part"
mv "$out.part" "$out"
find "$dir" -maxdepth 1 -type f -name "${db}-*.dump" -mtime +"$keep_days" -delete
echo "$(date '+%F %T') backup: $out ($(du -h "$out" | cut -f1))"
