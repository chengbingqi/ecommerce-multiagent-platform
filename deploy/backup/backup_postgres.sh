#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

backup_class=external_stage7_backup
if [[ ${1:-} == "--local-migration-protection" ]]; then
  target=${2:?usage: backup_postgres.sh --local-migration-protection LOCAL_TARGET}
  stage7_require_local_migration_target "$target"
  backup_class=local_migration_protection_not_external
else
  target=${1:?usage: backup_postgres.sh EXTERNAL_TARGET}
  stage7_require_external_target "$target"
fi
exec 9>"$target/.stage7-postgres-backup.lock"
flock -n 9 || { echo "BACKUP-LOCK-001: PostgreSQL backup already running" >&2; exit 4; }

output=$(stage7_backup_dir "$target" postgres)
partial="${output}.partial"
mkdir -p "$partial"
started=$(date +%s)
docker compose exec -T postgres pg_dump \
  --format=custom --no-owner --no-acl \
  --username "${POSTGRES_USER:-ecommerce_agent}" \
  --dbname "${POSTGRES_DB:-ecommerce_agent}" >"$partial/database.dump"
(cd "$partial" && sha256sum database.dump >SHA256SUMS)
printf 'alembic_revision=%s\n' "$(docker compose exec -T postgres psql -At --username "${POSTGRES_USER:-ecommerce_agent}" --dbname "${POSTGRES_DB:-ecommerce_agent}" -c 'select version_num from alembic_version')" >"$partial/metadata.txt"
printf 'backup_class=%s\n' "$backup_class" >>"$partial/metadata.txt"
printf 'duration_seconds=%s\n' "$(( $(date +%s) - started ))" >>"$partial/metadata.txt"
touch "$partial/COMPLETED"
mv "$partial" "$output"
if [[ "$backup_class" == "external_stage7_backup" ]]; then
  stage7_prune_completed "$target/postgres"
fi
echo "PostgreSQL backup completed: $output"
