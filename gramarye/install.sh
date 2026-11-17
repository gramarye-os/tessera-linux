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
# For a rootfs template, which is built off the phone and unpacked on many:
#   TESSERA_STAGE=1 sh gramarye/install.sh
# puts the files and the enabled unit in place and stops there. The token and what the env can do
# are made on the phone, by setup.sh, when the agent first starts.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${TESSERA_PORT:-7377}
STAGE=${TESSERA_STAGE:-}
mkdir -p /opt/tessera /etc/tessera
install -m 755 "$HERE/agent.py" /opt/tessera/agent.py
install -m 755 "$HERE/enter.sh" /opt/tessera/enter.sh
install -m 755 "$HERE/setup.sh" /opt/tessera/setup.sh
install -m 644 "$HERE/v4l_links.py" /opt/tessera/v4l_links.py
if [ -d /etc/aohp/system ] || [ -n "$STAGE" ]; then
  mkdir -p /etc/aohp/system
  install -m 644 "$HERE/tessera-agent.service" /etc/aohp/system/tessera-agent.service
  [ "$PORT" = 7377 ] || sed -i "s/^\[Service\]$/[Service]\nEnvironment=TESSERA_PORT=$PORT/" /etc/aohp/system/tessera-agent.service
fi
if [ -n "$STAGE" ]; then
  mkdir -p /etc/aohp/system/aohp.target.wants
  ln -sf ../tessera-agent.service /etc/aohp/system/aohp.target.wants/tessera-agent.service
  echo "Tessera's agent is staged. It makes its token when it first starts on a phone."
  exit 0
fi
sh /opt/tessera/setup.sh
if [ -d /etc/aohp/system ]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable tessera-agent >/dev/null 2>&1 || true
  systemctl restart tessera-agent >/dev/null 2>&1 || systemctl start tessera-agent
fi
echo "Tessera's agent is installed. To pair the Tessera app with this env: tessera-pair"
