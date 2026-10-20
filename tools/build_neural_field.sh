#!/bin/sh
# Reproducible, local bundle. No runtime CDN or network-loaded models.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir=${AGERE_NEURAL_BUILD_DIR:-/private/tmp/agere-neuron-build}
npm install --prefix "$build_dir" --no-audit --no-fund three@0.180.0 esbuild@0.25.11
NODE_PATH="$build_dir/node_modules" "$build_dir/node_modules/.bin/esbuild" "$repo/clinical_core/agere/assets/neural-field.js" --bundle --minify --format=iife --global-name=AgereNeural --outfile="$repo/clinical_core/agere/assets/neural-field.bundle.js"
