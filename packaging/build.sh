#!/bin/bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKGDIR="$(mktemp -d)"
trap 'rm -rf "$PKGDIR"' EXIT

rsync -a \
    --exclude='var/lib/cloudyhome/nas/' \
    --exclude='**/__pycache__/' \
    --exclude='**/*.pyc' \
    --exclude='**/*.egg-info/' \
    "$REPO_ROOT/nas_root/" "$PKGDIR/"

cp -r "$REPO_ROOT/packaging/DEBIAN" "$PKGDIR/"
chmod 0755 "$PKGDIR/DEBIAN/postinst"

dpkg-deb --build "$PKGDIR" "$REPO_ROOT"
echo "Built: $REPO_ROOT/cloudyhome-nas_$(grep '^Version:' "$REPO_ROOT/packaging/DEBIAN/control" | awk '{print $2}')_all.deb"
