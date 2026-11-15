#!/usr/bin/env bash
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
#
# Builds the Mesa a Gramarye env uses on an Adreno phone: freedreno (OpenGL), Turnip (Vulkan) and
# Zink, all on Android's KGSL GPU device, for Debian trixie arm64. It is the lfdevs
# mesa-for-android-container tree at the revision of their 20260824 prebuilt tarball (what envs
# had until now), configured as their CI does but with freedreno-kmds=msm,kgsl, plus the fixes in
# patches/:
#   freedreno-kgsl-present-wait: freedreno finishes a frame before handing it to the X server.
#     KGSL puts no fence on the buffer, so a server has nothing to wait on and copied frames the
#     GPU was still drawing (tearing; gramarye/teartest.c counts it).
#   turnip-kgsl-present-wait: the same for Vulkan and for Zink, which presents through Turnip.
#     Mesa's X11 code waited for the GPU in mailbox mode only (gramarye/vkteartest.c counts it).
#
# Runs on an x86-64 Linux PC, in an arm64 Debian container under qemu-user (needs podman, and
# qemu-user-static registered with binfmt_misc), so no phone does the compiling. On 32 threads:
# 6 minutes to configure, 7 to build.
#   gramarye/mesa/build.sh
# Result: $MESA_OUT/dist/mesa-kgsl_<version>-g<commit>_debian_trixie_arm64.tar.gz, laid out like
# the lfdevs tarball. In the env, as root:   tar xzf <it> -C / && ldconfig
# Env: MESA_SRC and MESA_OUT (default build/gramarye-mesa/src and out), MESA_REV, CCACHE_DIR, JOBS.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HERE/../../build/gramarye-mesa
SRC=${MESA_SRC:-$WORK/src}
OUT=${MESA_OUT:-$WORK/out}
REV=${MESA_REV:-98f3d6229d61452cef80f8563af7c56ae599dc14}
CCACHE_DIR=${CCACHE_DIR:-$OUT/ccache}
IMAGE=localhost/tessera-mesa-trixie-arm64:1
mkdir -p "$SRC" "$OUT/build" "$OUT/dist" "$CCACHE_DIR"

# The source at that revision, with our patches as commits on it: Mesa puts its commit in the
# version string, so a build with a fix and one without can be told apart (glxinfo -B).
cd "$SRC"
[ -d .git ] || git init -q -b base
git cat-file -e "$REV^{commit}" 2>/dev/null ||
  git fetch --depth 1 https://github.com/lfdevs/mesa-for-android-container.git "$REV"
want=$REV-$(cat "$HERE"/patches/*.patch | sha256sum | cut -c1-12)
if [ "$(git config --get tessera.patched || true)" != "$want" ]; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "$SRC has changes of its own; commit or drop them before the patches are applied again" >&2
    exit 1
  fi
  git checkout -q -B tessera "$REV"
  git am -q --committer-date-is-author-date "$HERE"/patches/*.patch
  git config tessera.patched "$want"
fi
git log --oneline -3

podman image exists "$IMAGE" ||
  podman build --platform linux/arm64 -t "$IMAGE" -f "$HERE/Containerfile" "$HERE"
exec podman run --rm --platform linux/arm64 --security-opt label=disable \
  -v "$SRC":/src -v "$OUT":/out -v "$CCACHE_DIR":/ccache -v "$HERE":/recipe:ro \
  -e CCACHE_DIR=/ccache -e CCACHE_MAXSIZE=5G -e JOBS="${JOBS:-}" \
  "$IMAGE" bash /recipe/in-container.sh
