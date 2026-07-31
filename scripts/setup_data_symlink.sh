#!/usr/bin/env bash
# Setup script for new machines.
# Creates the data symlink pointing to external storage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

DATA_TARGET="${QUANT_DATA_DIR:-$HOME/HHD/stock/data}"

echo "=== Quant Setup ==="
echo "Data target: ${DATA_TARGET}"

# Create data directories on external storage
mkdir -p "${DATA_TARGET}/runtime" "${DATA_TARGET}/meta" "${DATA_TARGET}/archive"

# Remove old data directory or symlink if present
if [ -L "data" ]; then
    rm data
elif [ -d "data" ] && [ ! -L "data" ]; then
    echo "WARNING: data/ is a real directory. Move contents to ${DATA_TARGET} first."
    echo "  mv data/runtime/*.db ${DATA_TARGET}/runtime/"
    echo "  mv data/meta/*.db ${DATA_TARGET}/meta/"
    echo "  rm -rf data/"
fi

# Create symlink
if [ ! -e "data" ]; then
    ln -s "${DATA_TARGET}" data
    echo "Symlink created: data -> ${DATA_TARGET}"
else
    echo "data already exists: $(readlink data)"
fi

echo "Setup complete. Run: uv sync --frozen"
