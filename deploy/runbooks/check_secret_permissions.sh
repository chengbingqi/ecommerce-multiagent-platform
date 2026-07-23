#!/usr/bin/env bash
set -euo pipefail
secret_root=${1:-secrets}
[[ -d "$secret_root" ]] || { echo "SECRET-DIR-001: missing secret directory" >&2; exit 2; }
[[ $(stat -c '%a' "$secret_root") == 700 ]] || { echo "SECRET-DIR-002: directory must be mode 0700" >&2; exit 3; }
while IFS= read -r -d '' file; do
  [[ $(stat -c '%a' "$file") == 600 ]] || { echo "SECRET-FILE-001: invalid mode for $file" >&2; exit 4; }
done < <(find "$secret_root" -type f -print0)
echo "Secret permission check passed"
