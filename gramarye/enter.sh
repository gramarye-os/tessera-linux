# Copyright (C) 2026 Daryan Kamaran Karim
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
# Runs as root INSIDE a gramarye (AOHP) env, started by the env's Tessera agent:
#   /bin/sh /opt/tessera/enter.sh SESSION CHUNK...
# with the launch's connection as its terminal. Adapted from assets/droidspaces/enter.sh; what
# differs: nothing of Tessera's is mounted in the env (the X server is reached over loopback
# TCP), and the session gets a PID namespace so that it ends as a whole with its first process.
#
# CHUNK... are the pieces of a Base64-encoded settings payload. Decoded SETTINGS are shell
# assignments, one per line:
#   BINDS, DEVICES, USB_ALL, GPU_DEVICE, X11, XAUTH, XDIR, RUN_AS_ROOT, APP_UID, NO_NET, NET_GROUPS, GUEST_ENV, CMD
# Everything mounted here lives in a mount namespace this script makes for itself and vanishes
# with it; the env's own mounts are never touched.
set -e

SID=${1:?session id missing}
shift
# The re-exec marker is an argv token, not an inherited environment variable: a container-wide
# TESSERA_NS=1 must never make this privileged script skip its private mount namespace.
IN_NAMESPACE=
if [ "${1-}" = "--tessera-internal-namespace" ]; then
  IN_NAMESPACE=1
  shift
fi
[ "$#" -gt 0 ] || { echo "Tessera launch settings are missing" >&2; exit 2; }
# Reconstruct the payload in the guest. Do not pass it as one CLI argument: the daemon's
# per-argument cap is 8192 bytes, while GUI helper source makes the full payload much larger.
SETTINGS_B64=
for chunk do SETTINGS_B64=$SETTINGS_B64$chunk; done
SETTINGS=$(printf '%s' "$SETTINGS_B64" | base64 -d) || { echo "Invalid Tessera launch settings" >&2; exit 2; }
eval "$SETTINGS"

# Mount and PID namespaces of its own (the agent ends the session by ending the first process:
# --kill-child takes the namespace's init with it, and the kernel the rest), and a network
# namespace with nothing in it when the app has no network permission; then this script again,
# inside it.
if [ "$IN_NAMESPACE" != 1 ]; then
  NS="-m -p -f --kill-child --mount-proc"
  # The X server is on this phone's loopback: a network namespace of its own would cut a windowed
  # app off from its display, so only apps without one are isolated from the network.
  if [ "$NO_NET" = 1 ] && [ -z "$X11" ]; then NS="$NS -n"; fi
  command -v unshare >/dev/null 2>&1 || { echo "unshare (util-linux) isn't in this container" >&2; exit 127; }
  exec unshare $NS /bin/sh "$0" "$SID" --tessera-internal-namespace "$@"
fi

# The env's mounts may be shared with the rest of it: keep ours to this namespace.
mount --make-rprivate /

# Shares $1 at $2 (rbind), read-only when $3 is ro. Older kernels lack recursive read-only
# remounts, so try the top bind as a compatibility fallback. Never leave the original writable
# bind mounted if both read-only remounts fail.
share() {
  mount -o rbind "$1" "$2" || return 1
  if [ "$3" = ro ] && ! mount -o remount,bind,ro=recursive "$2" 2>/dev/null; then
    if ! mount -o remount,bind,ro "$2"; then
      if ! umount -R "$2" 2>/dev/null && ! umount "$2" 2>/dev/null; then
        echo "tessera: fatal: couldn't revoke a writable bind at $2; aborting launch" >&2
        exit 1
      fi
      echo "tessera: couldn't enforce read-only access for $2; grant not mounted" >&2
      return 1
    fi
    echo "tessera: recursive read-only unavailable for $2; nested mounts may remain writable" >&2
  fi
}

# Every app has its own /tmp and /dev/shm, so apps can't see each other's sockets.
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp
mkdir -p /dev/shm
# Keep POSIX shared-memory files on a private tmpfs instead of the container-wide /dev/shm.
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /dev/shm

# LLVM (llvmpipe's shader JIT) picks instructions by core model, not by what the kernel enables:
# Cortex-X3 and A715 have SVE, Android kernels leave it off, and OpenGL apps drawing in software
# died of SIGILL. Every core is named a Cortex-A78 unless SVE is on.
if ! grep '^Features' /proc/cpuinfo | grep -qw sve; then
  if sed 's/^\(CPU part[[:space:]]*:[[:space:]]*\).*/\10xd41/' /proc/cpuinfo > /dev/shm/.cpuinfo 2>/dev/null; then
    mount --bind /dev/shm/.cpuinfo /proc/cpuinfo 2>/dev/null || true
  fi
  # Shaders compiled before that are in Mesa's disk cache, which doesn't key them by core.
  for c in /root/.cache /home/user/.cache; do
    if [ -d "$c" ] && [ ! -e "$c/.tessera-no-sve" ]; then
      rm -rf "$c/mesa_shader_cache" "$c/mesa_shader_cache_db"; touch "$c/.tessera-no-sve"
    fi
  done
fi

# Prepare the shared home before grants are mounted beneath it. Otherwise a recursive chown
# of /home/user crosses into mounted Android storage (e.g. Downloads) and can fail or touch host files.
U=
if [ "$RUN_AS_ROOT" != 1 ]; then
  U=${APP_UID:?APP_UID not set}
  mkdir -p /home/user
  [ "$(stat -c %u /home/user)" = "$U" ] || chown -R "$U:$U" /home/user
fi

# The folders the app was granted, as "src:dst:ro|rw" (src as this container sees it). One that
# can't be shared is reported and skipped: the app still starts, without it.
printf '%s\n' "$BINDS" | while IFS=: read -r src dst mode; do
  [ -n "$src" ] && [ -e "$src" ] || continue
  if [ -d "$src" ]; then mkdir -p "$dst"; else mkdir -p "$(dirname "$dst")"; touch "$dst"; fi
  share "$src" "$dst" "$mode" || echo "tessera: couldn't share $src at $dst" >&2
done

# The X server is reached through DISPLAY: on the env's side through a socket folder (XDIR), or in
# the launcher over loopback TCP with the session's cookie (XAUTH). The helpers keep their files
# and pipes in /tmp/.X11-unix; nothing relays those to the launcher yet.
if [ -n "$X11" ]; then
  # The session's X cookie: the server refuses a client without it. In the private /tmp, for
  # the app's user only; GUEST_ENV's XAUTHORITY names it.
  if [ -n "${XAUTH:-}" ]; then
    (umask 077; printf %s "$XAUTH" | base64 -d > /tmp/.tessera-xauth)
    [ "$RUN_AS_ROOT" = 1 ] || chown "$APP_UID:$APP_UID" /tmp/.tessera-xauth
  fi
  mkdir -p /tmp/.X11-unix
  if [ -n "${XDIR:-}" ]; then
    # An X server on the env's side (the agent started it): its socket folder, which nothing else
    # in the env is given, becomes this session's /tmp/.X11-unix. A socket file is what lets a
    # client hand the server the buffers it drew.
    [ -d "$XDIR/.X11-unix" ] || { echo "the session's X server has no socket folder ($XDIR)" >&2; exit 1; }
    mount --bind "$XDIR/.X11-unix" /tmp/.X11-unix
  fi
  chmod 1777 /tmp/.X11-unix
  for p in launcher-wincmd launcher-notifycmd; do [ -p "/tmp/.X11-unix/$p" ] || mkfifo -m 666 "/tmp/.X11-unix/$p" 2>/dev/null || true; done
fi
# GUEST_ENV is one KEY=VALUE per line; turn it into positional args for env -i.
set --
# "[ -z ] ||", not "[ -n ] &&": with an empty last line the loop returned 1 and set -e ended here.
while IFS= read -r kv; do [ -z "$kv" ] || set -- "$@" "$kv"; done <<ENVEOF
$GUEST_ENV
ENVEOF

ENVBIN=$(command -v env || echo /usr/bin/env)

# A session that owns the terminal, so bash has job control (Ctrl+C, Ctrl+Z). --wait: when this
# process already leads a group setsid forks, and without it the parent would return at once and
# the launcher would take the app for finished. Without a terminal (the Files tab's shell talks
# over pipes) there's nothing to own, and --ctty would fail.
SETSID=
CTTY=
[ -t 0 ] && CTTY=--ctty
SETSIDBIN=$(command -v setsid || true)
[ -n "$SETSIDBIN" ] && SETSID="$SETSIDBIN $CTTY --wait"
# Start in $HOME; expanded by the guest shell, not this one.
START='cd "$HOME" 2>/dev/null; exec '

if [ "$RUN_AS_ROOT" = 1 ]; then
  # Not exec: see the end of this script.
  $SETSID "$ENVBIN" -i "$@" /bin/sh -c "$START$CMD" || exit $?
  exit 0
fi

# Apps run as the launcher app's own uid (APP_UID), not Debian's usual 1000: on Android, 1000 is
# "system", which system services trust. The app's uid carries no rights beyond the app's, and
# is what the other engines run apps as too.
U=${APP_UID:?APP_UID not set}
SETPRIV=$(command -v setpriv || true)
[ -n "$SETPRIV" ] || { echo "setpriv (util-linux) isn't in this container" >&2; exit 127; }
# Give the uid a name. Without one, D-Bus won't start ("Could not get password database
# information for UID of current process") and other programs complain.
if grep -q '^user:' /etc/passwd; then
  sed -i "s#^user:[^:]*:[0-9]*:[0-9]*:#user:x:$U:$U:#" /etc/passwd
else
  echo "user:x:$U:$U:Tessera user:/home/user:/bin/bash" >> /etc/passwd
fi
if grep -q '^user:' /etc/group; then
  sed -i "s#^user:[^:]*:[0-9]*:#user:x:$U:#" /etc/group
else
  echo "user:x:$U:" >> /etc/group
fi
[ ! -f /etc/shadow ] || grep -q '^user:' /etc/shadow || echo 'user:!:19000::::::' >> /etc/shadow

# Supplementary groups: Android's network groups when network is granted, and the group of each
# granted device, so the app can open just those (not "system", 1000, which system services trust).
GROUPS_LIST=$NET_GROUPS
DEV_GROUPS=$({
  printf '%s\n' "$DEVICES"
  if [ "$USB_ALL" = 1 ]; then find /dev/bus/usb -type c 2>/dev/null; ls /dev/ttyACM* 2>/dev/null; fi
} | while IFS= read -r d; do
  [ -n "$d" ] && [ -e "$d" ] && stat -c %g "$d"
done | grep -vx 1000 | sort -u | tr '\n' ',' | sed 's/,$//')
[ -n "$DEV_GROUPS" ] && GROUPS_LIST=${GROUPS_LIST:+$GROUPS_LIST,}$DEV_GROUPS
if [ -n "$GROUPS_LIST" ]; then GROUP_ARG="--groups=$GROUPS_LIST"; else GROUP_ARG=--clear-groups; fi

if [ "${FLATPAK_MODE:-}" = 1 ]; then echo "Flathub apps are not supported in a gramarye env yet" >&2; exit 1; fi
# --no-new-privs keeps setuid binaries inert.
# Not exec: this shell stays as the PID namespace's first process, as root. The agent ends a
# session by ending unshare, whose --kill-child then reaches this shell, and the kernel ends
# everything else in the namespace with it. Were the app the first process, its change of uid
# would have dropped that kill-on-parent-death setting and the session would outlive its window.
$SETSID "$SETPRIV" --reuid="$U" --regid="$U" "$GROUP_ARG" --no-new-privs --inh-caps=-all \
  "$ENVBIN" -i "$@" /bin/sh -c "$START$CMD" || exit $?
exit 0
