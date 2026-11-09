#!/usr/bin/env bash
set -euo pipefail
release=$(cd "$(dirname "$0")/.." && pwd)
root=${1:?Usage: bash scripts/install.sh /absolute/install/root}
case "$root" in /*) ;; *) echo 'Install root must be absolute' >&2; exit 1;; esac
test ! -f "$release/INCOMPLETE"
cd "$release"
sha256sum --strict --check SHA256SUMS
docker version >/dev/null
docker compose version >/dev/null
image=$(sed -n 's/^CAMPUSQA_IMAGE=//p' release.env)
expected=$(sed -n 's/^CAMPUSQA_IMAGE_ID=//p' release.env)
test -n "$image" && test -n "$expected"
mkdir -p "$root"
root=$(cd "$root" && pwd -P)
source "$release/scripts/common.sh"
project=$(campusqa_project "$root")
required=$(sed -n 's/^CAMPUSQA_REQUIRED_KB=//p' release.env)
free=$(df -Pk "$root" | awk 'NR==2 {print $4}')
if [ "$free" -lt "$required" ]; then echo "Insufficient space: need at least $required KB, excluding business backups" >&2; exit 1; fi
included=$(sed -n 's/^CAMPUSQA_MODELS_INCLUDED=//p' release.env)
models="$release/models"
if [ "$included" = 0 ]; then
  if [ ! -f "$root/config/models.path" ]; then echo 'Application update requires a previously verified model installation' >&2; exit 1; fi
  models=$(cat "$root/config/models.path")
  test "$(sha256sum "$models/manifest.json" | cut -d' ' -f1)" = "$(sha256sum "$release/models/manifest.json" | cut -d' ' -f1)" || { echo 'Installed model version does not match this update' >&2; exit 1; }
fi
arch=$(docker info --format '{{.Architecture}}')
case "$arch" in x86_64|amd64) ;; *)
  if [ "${CAMPUSQA_ALLOW_EMULATION:-0}" != 1 ]; then
    echo 'This package targets linux/amd64. For explicit emulation testing only set CAMPUSQA_ALLOW_EMULATION=1.' >&2
    exit 1
  fi;; esac
docker image load -i images/app.tar
# Image IDs differ between classic and containerd stores. The archive checksum
# plus committed-source labels and platform identify the portable artifact.
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/amd64
test "$(docker image inspect --format '{{index .Config.Labels "io.campusqa.backend-revision"}}' "$image")" = "$(sed -n 's/^CAMPUSQA_BACKEND_REV=//p' release.env)"
test "$(docker image inspect --format '{{index .Config.Labels "io.campusqa.admin-revision"}}' "$image")" = "$(sed -n 's/^CAMPUSQA_ADMIN_REV=//p' release.env)"
mkdir -p "$root/data" "$root/config" "$root/backups"
if [ ! -f "$root/config/app.env" ]; then
  cp config/app.env.example "$root/config/app.env"
  chmod 600 "$root/config/app.env"
fi
# Initialize only the root ownership. Existing nested data is never recursively changed.
docker run --rm --platform linux/amd64 --user 0 --entrypoint python \
  --mount "type=bind,src=$root/data,dst=/target" "$image" \
  -c 'import os; os.chown("/target",10001,10001)'
# The model directory remains in this versioned release, mounted read-only.
export CAMPUSQA_IMAGE="$image" CAMPUSQA_ROOT="$root" CAMPUSQA_MODELS="$models"
docker compose --project-name "$project" -f "$release/compose.yaml" config --quiet
docker compose --project-name "$project" -f "$release/compose.yaml" run --rm --no-deps app check
printf '%s\n' "$models" > "$root/config/models.path"
printf 'Preflight passed. To start, use:\nbash %q %q up\n' \
  "$release/scripts/manage.sh" "$root"
