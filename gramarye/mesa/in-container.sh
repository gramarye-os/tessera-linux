#!/usr/bin/env bash
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
#
# The part of build.sh that runs inside the arm64 container: configure (once), build, stage, tar.
# The options are the lfdevs CI's at this revision (.github/workflows/build-check.yml, jobs
# debian-trixie and fedora-44, which use the same ones), with freedreno-kmds=msm,kgsl in place of
# kgsl. Where the libraries go is left to meson, which follows the distro it runs in
# (usr/lib/aarch64-linux-gnu on Debian, usr/lib64 on Fedora).
set -euo pipefail
git config --global --add safe.directory "*"
cd /src
if [ ! -f /out/build/build.ninja ]; then
  meson setup /out/build /src \
    --prefix=/usr \
    -Dplatforms=x11,wayland \
    -Dgallium-drivers=freedreno,zink,virgl,llvmpipe \
    -Dgallium-va=enabled \
    -Dtermux-va-bridge=enabled \
    -Dtermux-va-wayland-shim=enabled \
    -Dvideo-codecs=all \
    -Dgallium-mediafoundation=disabled \
    -Dvulkan-drivers=freedreno \
    -Dvulkan-layers= \
    -Degl=enabled \
    -Dgles2=enabled \
    -Dglvnd=enabled \
    -Dglx=dri \
    -Dlibunwind=disabled \
    -Dintel-rt=disabled \
    -Dmicrosoft-clc=disabled \
    -Dvalgrind=disabled \
    -Dgles1=disabled \
    -Dfreedreno-kmds=msm,kgsl \
    -Dbuildtype=release
fi
ninja -C /out/build -j"${JOBS:-$(nproc)}"
rm -rf /out/stage
DESTDIR=/out/stage meson install -C /out/build --no-rebuild > /out/install.log
# What this build does, said where the session's X server is started from (Tessera's agent reads
# it): its drivers have the GPU finish a frame before they present it (patches/), so the server
# need not hold a Present back. The commit is the one Mesa names in its libraries.
mkdir -p /out/stage/usr/share/tessera
printf 'build=git-%s\npresent=finished\n' "$(git rev-parse --short=10 HEAD)" > /out/stage/usr/share/tessera/mesa
ver=$(grep -v "^[[:space:]]*$" VERSION | head -n1)
name=mesa-kgsl_${ver}-g$(git rev-parse --short=10 HEAD)_${DISTRO:-debian_trixie}_arm64.tar.gz
tar -czf "/out/dist/$name" -C /out/stage .
cd /out/dist && sha256sum "$name" | tee "$name.sha256"
