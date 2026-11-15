#!/bin/sh
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
# Installs Tessera's agent in a gramarye (AOHP) env. Run as root inside the env, from a checkout:
#   sh gramarye/install.sh
# Prints the line Tessera needs in its files/aohp/agent:
#   <port> <token> [vk=<vulkan icd>] [gl=<opengl driver>] [x=host]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${TESSERA_PORT:-7377}
mkdir -p /opt/tessera /etc/tessera
install -m 755 "$HERE/agent.py" /opt/tessera/agent.py
install -m 755 "$HERE/enter.sh" /opt/tessera/enter.sh
if [ ! -s /etc/tessera/token ]; then
  (umask 077; head -c 24 /dev/urandom | base64 | tr -d '+/=\n' > /etc/tessera/token)
fi
chmod 600 /etc/tessera/token
if [ -d /etc/aohp/system ]; then
  install -m 644 "$HERE/tessera-agent.service" /etc/aohp/system/tessera-agent.service
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable tessera-agent >/dev/null 2>&1 || true
  systemctl restart tessera-agent >/dev/null 2>&1 || systemctl start tessera-agent
fi
# Hardware graphics: a Turnip (freedreno Vulkan) driver built for Android's GPU device. Stock
# distro Mesa talks to the msm DRM driver only, which a phone running Android doesn't have.
ICD=
if [ -e /dev/kgsl-3d0 ]; then
  for j in /usr/share/vulkan/icd.d/freedreno_icd*.json /etc/vulkan/icd.d/freedreno_icd*.json; do
    [ -f "$j" ] || continue
    lib=$(sed -n 's/.*"library_path"[^"]*"\([^"]*\)".*/\1/p' "$j" | head -1)
    case "$lib" in /*) ;; *) lib=$(dirname "$j")/$lib ;; esac
    if [ -f "$lib" ] && grep -q kgsl "$lib" 2>/dev/null; then ICD=$j; break; fi
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
echo "$PORT $(cat /etc/tessera/token)${ICD:+ vk=$ICD}${GL:+ gl=$GL}${HOSTX:+ x=$HOSTX}"
