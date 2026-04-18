#!/usr/bin/env bash
# Clean up lingering Docker containers, networks, volumes, and dangling images
# from prior OpenHarness sweeps without deleting the cached task images.

set -euo pipefail

echo "==> Stopping any lingering Harbor task containers..."
# Find all containers EXCEPT the langfuse-selfhost infrastructure
ZOMBIES=$(docker ps -a --format "{{.ID}} {{.Names}}" | grep -v "langfuse-selfhost" | awk '{print $1}' || true)

if [ -n "$ZOMBIES" ]; then
    echo "Force removing lingering containers..."
    for z in $ZOMBIES; do
        docker rm -f "$z" || true
    done
else
    echo "No lingering task containers found."
fi

echo "==> Pruning stopped containers..."
docker container prune -f

echo "==> Pruning unused networks..."
docker network prune -f

echo "==> Pruning unused volumes..."
docker volume prune -f

echo "==> Pruning dangling (intermediate/untagged) images..."
# NOTE: We deliberately do NOT use `-a` here. 
# `docker image prune -f` only removes dangling (untagged/intermediate) images.
# This preserves all the cached `alexgshaw/*:20251031` task images so the 
# next sweep doesn't have to download 100GB from the registry again.
docker image prune -f

echo "==> Docker cleanup complete! Current disk footprint:"
echo ""
docker system df
echo ""
df -h /
