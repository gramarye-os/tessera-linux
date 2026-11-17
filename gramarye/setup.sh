#!/bin/sh
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
# What Tessera's agent needs that depends on the phone it runs on: a token of this env's own, and
# what this env can do. The agent's unit runs it before each start, so an env made from a template
# that carries the agent gets both the first time it starts. install.sh runs it too.
# Safe to run again: the token is made once and kept.
set -eu
ETC=${TESSERA_ETC:-/etc/tessera}
mkdir -p "$ETC"
if [ ! -s "$ETC/token" ]; then
  (umask 077; head -c 24 /dev/urandom | base64 | tr -d '+/=\n' > "$ETC/token")
fi
chmod 600 "$ETC/token"
# Hardware graphics: a Turnip (freedreno Vulkan) driver built for Android's GPU device. Stock
# distro Mesa talks to the msm DRM driver only, which a phone running Android doesn't have.
ICD=
if [ -e /dev/kgsl-3d0 ]; then
  for j in /usr/share/vulkan/icd.d/freedreno_icd*.json /etc/vulkan/icd.d/freedreno_icd*.json; do
    [ -f "$j" ] || continue
    lib=$(sed -n 's/.*"library_path"[^"]*"\([^"]*\)".*/\1/p' "$j" | head -1)
    case "$lib" in /*) ;; *) lib=$(dirname "$j")/$lib ;; esac
    # By the device's path: a Mesa built without it (Fedora's own) still has the word "kgsl" in it.
    if [ -f "$lib" ] && grep -q /dev/kgsl-3d0 "$lib" 2>/dev/null; then ICD=$j; break; fi
  done
fi
# A native OpenGL driver for that device in the same Mesa (faster than OpenGL over Vulkan).
GL=
if [ -n "$ICD" ]; then
  for d in /usr/lib/*/dri/kgsl_dri.so /usr/lib/dri/kgsl_dri.so /usr/lib64/dri/kgsl_dri.so; do [ -e "$d" ] && { GL=kgsl; break; }; done
fi
# The session's X server can run on this env's side when its unit manager is here to start it.
HOSTX=
[ -d /etc/aohp/system ] && command -v systemctl >/dev/null 2>&1 && HOSTX=host
# What the agent tells a paired launcher this env can do, each time it asks. dev=private: the
# launch script gives each app a /dev of its own, so the list of an app's devices is a limit the
# launcher can call enforced.
CAPS="${ICD:+vk=$ICD }${GL:+gl=$GL }${HOSTX:+x=$HOSTX }dev=private"
echo "${CAPS% }" > "$ETC/caps.new"
mv "$ETC/caps.new" "$ETC/caps"
if [ ! -x /usr/local/bin/tessera-pair ]; then
  mkdir -p /usr/local/bin
  printf '#!/bin/sh\nexec /usr/bin/python3 /opt/tessera/agent.py pair\n' > /usr/local/bin/tessera-pair
  chmod 755 /usr/local/bin/tessera-pair
fi
