#!/bin/sh
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
# Installs Tessera's agent in a gramarye (AOHP) env. Run as root inside the env, from a checkout:
#   sh gramarye/install.sh
# Tessera then asks for a pairing code, under Setup > Gramarye env: run tessera-pair for one.
# (Tessera keeps "<port> <token> [vk=<vulkan icd>] [gl=<opengl driver>] [x=host] [dev=private]", which
# pairing gives it, in its files/aohp/agent.)
# A second env on one phone shares its loopback with the first, and needs a port of its own:
#   TESSERA_PORT=7378 sh gramarye/install.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${TESSERA_PORT:-7377}
mkdir -p /opt/tessera /etc/tessera
install -m 755 "$HERE/agent.py" /opt/tessera/agent.py
install -m 755 "$HERE/enter.sh" /opt/tessera/enter.sh
install -m 644 "$HERE/v4l_links.py" /opt/tessera/v4l_links.py
if [ ! -s /etc/tessera/token ]; then
  (umask 077; head -c 24 /dev/urandom | base64 | tr -d '+/=\n' > /etc/tessera/token)
fi
chmod 600 /etc/tessera/token
if [ -d /etc/aohp/system ]; then
  install -m 644 "$HERE/tessera-agent.service" /etc/aohp/system/tessera-agent.service
  [ "$PORT" = 7377 ] || sed -i "s/^\[Service\]$/[Service]\nEnvironment=TESSERA_PORT=$PORT/" /etc/aohp/system/tessera-agent.service
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
# launch script installed above gives each app a /dev of its own, so the list of an app's devices
# is a limit the launcher can call enforced.
CAPS="${ICD:+vk=$ICD }${GL:+gl=$GL }${HOSTX:+x=$HOSTX }dev=private"
echo "${CAPS% }" > /etc/tessera/caps
printf '#!/bin/sh\nexec /usr/bin/python3 /opt/tessera/agent.py pair\n' > /usr/local/bin/tessera-pair
chmod 755 /usr/local/bin/tessera-pair
echo "Tessera's agent is installed. To pair the Tessera app with this env: tessera-pair"
