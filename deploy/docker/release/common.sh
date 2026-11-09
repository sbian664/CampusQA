#!/usr/bin/env bash
# Keep project identity stable across releases and separate across installation roots.
campusqa_project() {
  local canonical_root
  canonical_root=$(cd "$1" && pwd -P)
  printf 'campusqa-%s' "$(printf '%s' "$canonical_root" | sha256sum | cut -c1-12)"
}
