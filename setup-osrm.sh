#!/usr/bin/env bash
# setup-osrm.sh — Download and preprocess OSM data for the OSRM routing service.
# Run once before starting the stack. Takes ~30-60 min depending on hardware.
#
# Downloads: North America + Europe (covers the vast majority of SWU events).
# Merged result is written to the osrm_data Docker volume.
#
# Requirements: docker, wget, osmium-tool
#   Ubuntu/Debian: sudo apt-get install -y osmium-tool wget

set -euo pipefail

TMPDIR="$(pwd)/.osrm-build"
IMAGE="ghcr.io/project-osrm/osrm-backend:latest"

echo "==> Creating build directory: $TMPDIR"
mkdir -p "$TMPDIR"
cd "$TMPDIR"

echo "==> Pulling OSRM image"
docker pull "$IMAGE"

echo "==> Downloading OSM data from Geofabrik..."
wget -c -N https://download.geofabrik.de/north-america-latest.osm.pbf
wget -c -N https://download.geofabrik.de/europe-latest.osm.pbf

echo "==> Merging regions with osmium..."
osmium merge north-america-latest.osm.pbf europe-latest.osm.pbf -o merged.osm.pbf --overwrite

echo "==> Extracting routing graph (this takes a while)..."
docker run -t --rm -v "$TMPDIR:/data" "$IMAGE" \
  osrm-extract -p /opt/car.lua /data/merged.osm.pbf

echo "==> Partitioning..."
docker run -t --rm -v "$TMPDIR:/data" "$IMAGE" \
  osrm-partition /data/merged.osrm

echo "==> Customizing..."
docker run -t --rm -v "$TMPDIR:/data" "$IMAGE" \
  osrm-customize /data/merged.osrm

echo "==> Copying processed data into osrm_data volume..."
docker run --rm \
  -v "$TMPDIR:/src" \
  -v swuguru_osrm_data:/data \
  busybox sh -c "cp /src/merged.osrm* /data/"

echo ""
echo "==> Done. Start the stack with: docker compose up -d osrm"
echo "    OSRM will be available internally at http://osrm:5000"
