#!/usr/bin/env bash
set -euo pipefail
release=$(cd "$(dirname "$0")/.." && pwd)
export CAMPUSQA_ROOT=${1:?Usage: manage.sh /absolute/root up|down|logs|check|migrate|backup|restore [arguments]}
action=${2:?Missing action}
shift 2
export CAMPUSQA_ROOT=$(cd "$CAMPUSQA_ROOT" && pwd -P)
source "$release/scripts/common.sh"
project=$(campusqa_project "$CAMPUSQA_ROOT")
export CAMPUSQA_MODELS=$(cat "$CAMPUSQA_ROOT/config/models.path" 2>/dev/null || printf '%s' "$release/models")
export CAMPUSQA_IMAGE=$(sed -n 's/^CAMPUSQA_IMAGE=//p' "$release/release.env")
compose=(docker compose --project-name "$project" -f "$release/compose.yaml")
case "$action" in
  up) "${compose[@]}" up -d;;
  down) "${compose[@]}" down;;
  logs) "${compose[@]}" logs --tail 100;;
  check|migrate) "${compose[@]}" run --rm --no-deps app "$action" "$@";;
  backup|restore)
    "${compose[@]}" run --rm --no-deps -v "$CAMPUSQA_ROOT/backups:/backups" app "$action" "$@";;
  *) echo 'Unknown command' >&2; exit 1;;
esac
