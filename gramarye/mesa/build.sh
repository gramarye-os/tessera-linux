#!/usr/bin/env bash
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
#
# Builds the Mesa a Gramarye env uses on an Adreno phone: freedreno (OpenGL), Turnip (Vulkan) and
# Zink, all on Android's KGSL GPU device, for Debian trixie or Fedora 44, arm64. It is the lfdevs
# mesa-for-android-container tree at the revision of their 27.0.0-devel-20261025-r2 release,
# configured as their CI does but with freedreno-kmds=msm,kgsl, plus the fix in patches/.
# KGSL puts no fence on a buffer, so an X server handed a frame has nothing to wait on and copies
# it while the GPU is still drawing: tearing (gramarye/teartest.c and vkteartest.c count it).
#   OpenGL (freedreno): fixed in their tree since 20261025. It gives the server a fence to wait
#     on with each Present, which Tessera's X server honours.
#   turnip-kgsl-present-wait (ours): the same for Vulkan and for Zink, which presents through
#     Turnip. Mesa's X11 code waits for the GPU in mailbox mode only; with this, in every mode.
#
# Runs on an x86-64 Linux PC, in an arm64 container of the env's own distro under qemu-user (needs
# podman, and qemu-user-static registered with binfmt_misc), so no phone does the compiling. A
# build only loads in the distro it was made in: it links that distro's LLVM and C++ library.
# On 32 threads: 6 minutes to configure, 7 to build.
#   gramarye/mesa/build.sh [debian|fedora]        (default: debian)
# Result: $MESA_OUT/dist/mesa-kgsl_<version>-g<commit>_<debian_trixie|fedora_44>_arm64.tar.gz, laid
# out like the lfdevs tarball. In the env, as root:   tar xzf <it> -C / && ldconfig
# It goes over the distro's own Mesa packages, which must be installed first (they bring what it
# links to). On Fedora, keep dnf from putting its own Mesa back at the next upgrade:
#   echo 'excludepkgs=mesa*' >> /etc/dnf/dnf.conf
# Env: MESA_SRC and MESA_OUT (default build/gramarye-mesa/src and out, out-fedora for Fedora),
# MESA_REV, CCACHE_DIR, JOBS. One MESA_OUT per distro: it holds the build folder and the cache.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HERE/../../build/gramarye-mesa
case "${1:-debian}" in
  debian) IMAGE=localhost/tessera-mesa-trixie-arm64:1; CONTAINERFILE=Containerfile; DISTRO=debian_trixie; OUT=$WORK/out ;;
  fedora) IMAGE=localhost/tessera-mesa-fedora44-arm64:1; CONTAINERFILE=Containerfile.fedora; DISTRO=fedora_44; OUT=$WORK/out-fedora ;;
  *) echo "usage: $0 [debian|fedora]" >&2; exit 2 ;;
esac
SRC=${MESA_SRC:-$WORK/src}
OUT=${MESA_OUT:-$OUT}
REV=${MESA_REV:-b84f02a4514983ea9b2d96971cde72720c377320}
CCACHE_DIR=${CCACHE_DIR:-$OUT/ccache}
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
  podman build --platform linux/arm64 -t "$IMAGE" -f "$HERE/$CONTAINERFILE" "$HERE"
exec podman run --rm --platform linux/arm64 --security-opt label=disable \
  -v "$SRC":/src -v "$OUT":/out -v "$CCACHE_DIR":/ccache -v "$HERE":/recipe:ro \
  -e CCACHE_DIR=/ccache -e CCACHE_MAXSIZE=5G -e JOBS="${JOBS:-}" -e DISTRO="$DISTRO" \
  "$IMAGE" bash /recipe/in-container.sh
