#!/bin/sh
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
# Installs Tessera's agent in a gramarye (AOHP) env. Run as root inside the env, from a checkout:
#   sh gramarye/install.sh
# Prints the line Tessera needs in its files/aohp/agent ("<port> <token>").
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
echo "$PORT $(cat /etc/tessera/token)"
