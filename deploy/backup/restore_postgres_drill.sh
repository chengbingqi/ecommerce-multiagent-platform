#!/usr/bin/env bash
set -euo pipefail

dump=${1:?usage: restore_postgres_drill.sh BACKUP_DUMP}
stamp=$(date -u +%Y%m%d%H%M%S)
database="stage7_restore_drill_${stamp}"
user=${POSTGRES_USER:-ecommerce_agent}
cleanup() {
  docker compose exec -T postgres dropdb --if-exists --username "$user" "$database" >/dev/null
}
trap cleanup EXIT
docker compose exec -T postgres createdb --username "$user" "$database"
docker compose exec -T postgres pg_restore --exit-on-error --no-owner --no-acl \
  --username "$user" --dbname "$database" <"$dump"
revision=$(docker compose exec -T postgres psql -At --username "$user" --dbname "$database" -c 'select version_num from alembic_version')
links=$(docker compose exec -T postgres psql -At --username "$user" --dbname "$database" -c 'select count(*) from agent_runs r left join principals p on p.id=r.principal_id where p.id is null')
if [[ -z "$revision" || "$links" != "0" ]]; then
  echo "RESTORE-PG-VERIFY-001: restore verification failed" >&2
  exit 5
fi
echo "PostgreSQL isolated restore verified; revision=$revision; orphan_run_links=0"
