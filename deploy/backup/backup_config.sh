#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

target=${1:?usage: backup_config.sh EXTERNAL_TARGET}
stage7_require_external_target "$target"
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
output=$(stage7_backup_dir "$target" config)
partial="${output}.partial"
mkdir -p "$partial"
tar -C "$repo_root" -czf "$partial/config.tar.gz" \
  README.md AGENTS.md alembic.ini migrations docs/prompts docs/operations_rules.md \
  evals/stage3/customer_service_cases_v1.json evals/stage4 evals/stage6 \
  evals/stage7 compose.yaml compose.prod.yaml compose.public.yaml deploy/Caddyfile
sha256sum "$partial/config.tar.gz" >"$partial/SHA256SUMS"
touch "$partial/COMPLETED"
mv "$partial" "$output"
stage7_prune_completed "$target/config"
echo "Non-sensitive configuration backup completed: $output"
