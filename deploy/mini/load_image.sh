#!/usr/bin/env bash
# Verify an exported image against the .sha256 file next to it, then load it into the dedicated Colima VM.
# Usage: bash deploy/mini/load_image.sh <path/to/horosa-public-X.Y.Z-amd64.tar.gz>
set -euo pipefail
TARBALL="${1:?path to horosa-public-<version>-amd64.tar.gz}"
SUMS="${TARBALL}.sha256"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima-horosa-plugin}"
[ -f "${TARBALL}" ] || { echo "image file not found: ${TARBALL}"; exit 1; }
[ -f "${SUMS}" ] || { echo "checksum file not found: ${SUMS}"; exit 1; }
EXPECTED="$(awk '{print $1}' "${SUMS}")"
ACTUAL="$(shasum -a 256 "${TARBALL}" | awk '{print $1}')"
[ "${EXPECTED}" = "${ACTUAL}" ] || { echo "sha256 mismatch: expected ${EXPECTED}, got ${ACTUAL}"; exit 1; }
echo "sha256 OK (${ACTUAL})"
echo "loading into ${DOCKER_CONTEXT} (1-3 minutes) ..."
gunzip -c "${TARBALL}" | docker load
