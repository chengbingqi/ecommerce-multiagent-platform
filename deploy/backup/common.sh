#!/usr/bin/env bash
set -euo pipefail
umask 077

stage7_require_external_target() {
  local target=$1
  local data_root=${DATA_ROOT:?DATA_ROOT must identify the project data filesystem}
  mkdir -p "$target"
  local target_device data_device
  target_device=$(df -P "$target" | awk 'NR==2 {print $1}')
  data_device=$(df -P "$data_root" | awk 'NR==2 {print $1}')
  if [[ "$target_device" == "$data_device" ]]; then
    echo "BACKUP-EXTERNAL-001: target is on the same filesystem as project data" >&2
    exit 2
  fi
}

stage7_require_local_migration_target() {
  local target=$1
  local data_root=${DATA_ROOT:?DATA_ROOT must identify the project data filesystem}
  local allowed_root="$data_root/backups/migration-protection"
  mkdir -p "$allowed_root"
  local resolved_target resolved_root
  resolved_target=$(realpath -m "$target")
  resolved_root=$(realpath -m "$allowed_root")
  case "$resolved_target" in
    "$resolved_root"/*) mkdir -p "$resolved_target" ;;
    *) echo "BACKUP-LOCAL-PATH-001: local migration backup must stay below $resolved_root" >&2; exit 5 ;;
  esac
}

stage7_backup_dir() {
  local root=$1 kind=$2
  local stamp
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  printf '%s/%s/%s\n' "$root" "$kind" "$stamp"
}

stage7_prune_completed() {
  local root=$1 retention_days=${BACKUP_RETENTION_DAYS:-14}
  find "$root" -mindepth 2 -maxdepth 2 -type f -name COMPLETED -mtime "+$retention_days" \
    -printf '%h\0' | while IFS= read -r -d '' completed_dir; do
      case "$completed_dir" in
        "$root"/*) rm -rf -- "$completed_dir" ;;
        *) echo "BACKUP-PATH-001: refusing cleanup outside target" >&2; exit 3 ;;
      esac
    done
}
