# Running Tessera apps in a Droidspaces container (design)

Status: a design, written 2026-11-21 after a day of tests on a Saga (ingot). No code yet.

## What this is about

On a gramarye phone Tessera starts Linux apps in the env, through the env's agent
(`agent.py`, `enter.sh`). The ROM can now also run Droidspaces containers: whole Linux
systems with their own init, beside the env. The aim is to let Tessera start an app, or a
whole desktop, in one of those containers and manage its window, sound and input the same way
it does for the env's own apps.

Upstream Tessera already has a "Droidspaces mode" (`docs/DROIDSPACES.md`,
`DroidspacesBackend.kt`). It needs root: it runs `su -c 'droidspaces ... run'`. On a gramarye
phone Tessera has no root. The env's agent does, and it can already reach the Droidspaces
daemon. So here the launch goes through the agent.

## What was tried by hand

All on Saga 1, ROM build `ds1`, Droidspaces v7.2.0 run by init in its own SELinux domain
(permissive), Tessera 0.7.0-gramarye.3.

1. Opened xterm in Tessera. The agent started the session's X server on the env's side; its
   socket is `/var/lib/tessera/x/<session>/.X11-unix/X<n>` in the env.
2. Started a container with that folder bound in:
   `droidspaces --name=<c> --gpu --bind=/data/aohp/envs/oc/rootfs/var/lib/tessera/x:/mnt/tessera-x start`
3. In the container, linked the socket to `/tmp/.X11-unix/X<n>` and ran programs with the same
   `DISPLAY` and Mesa variables Tessera gives its own apps.

Results:

| Container | What ran | Result |
|---|---|---|
| Alpine 3.23 | xeyes | drawn, in a Tessera window of its own |
| Debian 13, copy of the env's template (has our Mesa) | glxgears, vkcube, glxinfo, vulkaninfo | freedreno FD725 / Turnip, direct rendering, 121 FPS vsynced |
| Debian 13 XFCE (upstream Droidspaces image) + our Mesa tarball | the XFCE session | desktop drawn, touch reaches it, 121 FPS |

No SELinux denial for the X socket or the GPU in any of these.

Three things were wrong with the desktop, and all three come from starting it beside Tessera
instead of through it:

- **Window manager.** The session was an xterm, so Tessera's wrapper had started matchbox.
  `xfwm4` found the screen taken and quit.
- **Sound.** Tessera's wrapper starts a PulseAudio for the session, playing into the
  `launcher-audio` FIFO. The container's programs were never told about it.
- **Touch.** A drag selected instead of scrolling. The programs had none of the session's
  environment, only `DISPLAY` and the Mesa variables. Which touch mode was active was not
  checked.

## Proposal

Keep the launcher side as it is. Teach the agent one more thing: a session may name a
container, and then the agent starts the session's command there.

Today (`agent.py`, `handle()`):

    argv = ["/bin/sh", ENTER, sid] + chunks

With a container named:

    argv = ["droidspaces", "--name=" + container, "run", "--",
            "/bin/sh", "/mnt/tessera/bin/enter.sh", sid] + chunks

Everything after that stays the same: the settings payload, the terminal, the frames, ending
the session when the launcher hangs up. The X server is still started by the agent on the
env's side (`TESSERAX2`), and the folder mirror still runs in the env.

`CMD` in the settings is already Tessera's GUI wrapper. So a session started this way gets
matchbox for an app, or no matchbox for an entry marked `desktop`, the session's PulseAudio,
D-Bus, the watchers and the session's environment. That is what the hand test lacked.

### What changes where

**Agent (`gramarye/agent.py`)**

- A way to name the container for a session. Simplest: a new first word, `TESSERA3 <token>
  <session> <container> <settings> <rows> <columns>`, so old launchers keep working. The name
  is checked against a strict pattern and against `droidspaces show`.
- Report the containers in the caps line (`PONG`), so the launcher can list them.
- The present-hold check (`frames_finished()`) reads the Mesa marker of the env. For a
  container session it has to read the container's (`/usr/share/tessera/mesa` under the
  container's root).

**Launch script (`gramarye/enter.sh`)**

- It is adapted from upstream's `assets/droidspaces/enter.sh`, so most of it applies. In a
  container the X folder is at `/mnt/tessera/x/<session>`, not `/var/lib/tessera/x/<session>`:
  `XDIR` has to be given as the container sees it.
- It makes a private `/dev` for the app from the devices around it. In a container those are
  what Droidspaces put there (`--gpu` gives kgsl, dma_heap, dri).
- It hides `/mnt/tessera` from the app once the app's own socket is bound, as upstream does.

**Container setup (once per container)**

Droidspaces binds folders only when a container boots. So each container needs two entries in
its saved `bind_mounts`, then a restart:

| In the container | From the env | Why |
|---|---|---|
| `/mnt/tessera/x` | `/var/lib/tessera/x` | every session's X folder: socket, sound FIFO, the exchange files |
| `/mnt/tessera/bin` (read-only) | `/opt/tessera` | `enter.sh` |

The env's paths have to be written as the phone sees them (`/data/aohp/envs/<env>/rootfs/...`),
because the Droidspaces daemon runs on the phone's side. The setup also turns on `--gpu`.

**Launcher (Kotlin)**

- `AohpBackend`: pass the container name; keep the settings as they are, with `XDIR` set for the
  container.
- A place to pick the container, per app or per engine entry, and the apps list read from the
  container (`run()` already runs scripts in the env; the same through `droidspaces run`).
- A desktop entry for a container that has one (`desktop = true`, exec = its session command).

### What a container must contain

- util-linux (`unshare`, `mount`, `setpriv`) and coreutils (`base64`). Same as upstream asks.
- Tessera's GUI helpers (`GUI_HELPER_PACKAGES`): `dbus-x11`, `pulseaudio` or PipeWire,
  `python3-xlib`, `python3-pyatspi`, `at-spi2-core`, `xsettingsd`, and
  `matchbox-window-manager` for single apps. The env's template has all of them. The upstream
  XFCE image was not checked.
- For the GPU: our Mesa tarball. Builds exist for Debian trixie and Fedora 44. Other distros
  need their own build, or run in software.
- Debian or Ubuntu family if the Store is to install into it (apt), as upstream.

## First results (2026-11-21, branch `container-target`)

The agent change is in: `session_argv()`, the `TESSERA3` line, and `/etc/tessera/container`
as the default for a launcher that names no container. With that file set and the launcher
unchanged, on a Saga:

- xterm opened from Tessera runs in the container: prompt `user@trixie-ds`, and matchbox, the
  session's D-Bus, the watchers and the session's environment are all there, as the session's
  user. `enter.sh` ran unchanged.
- Simpler than proposed above: the env's X folder is bound at the **same path** in the container
  (`/var/lib/tessera/x`), and the container has `enter.sh` at `/opt/tessera`. So `XDIR` and the
  rest of the settings need no rewrite. (In the test the container had its own copy of
  `enter.sh`; binding the env's `/opt/tessera` is still to do.)
- The GPU needed a fix in `enter.sh`: Droidspaces gives the GPU's nodes to a group of its own,
  and the app's user did not have it (llvmpipe). With the group added: FD725, 121 FPS.
- A session ends cleanly both ways, by its program exiting and by the launcher hanging up:
  nothing of it is left in the container.
- The env sees the container's processes (it shares the phone's PID namespace), which is what
  the task manager needs. The task manager itself was not looked at.

- The launcher's own scripts go to the container too: after `apt install` there, the Apps list
  showed the new programs. So piped sessions work through `droidspaces run`.
- Tessera's built-in Desktop runs in the container once its packages are there (`xfwm4`,
  `xfconf`, `thunar`, ...): `xfwm4` is the window manager and stays up, windows have title
  bars and float, the taskbar lists them, and the GPU is used (FD725).
- Sound is connected end to end in that session: the container's PulseAudio has the
  `launcher-audio` FIFO open, and while a test tone played Android showed a started AudioTrack
  of the Tessera app. Whether it is audible was not checked by ear.

- The launcher side is in: the agent's ping answer ends with `ds=<name>,<name>`, the running
  containers; an app's long-press menu then has "Runs in…", and the choice is kept per app.
  On the Saga, with no default file: xterm set to the container opened with the prompt
  `user@trixie-ds`, and UXTerm, left alone, opened in the env (`user@localhost`), side by side.

- The launcher reads each container the agent offers (its programs, its packages, its
  `.desktop` files) with scripts the agent runs there, and lists what the env lacks, set to run
  in that container; the tile says which. On the Saga, with no default file, the Desktop button
  came back (its packages are only in the container), the Desktop opened there, and its Start
  menu listed the container's apps.

Known gaps:

- "Gramarye env" in the dialog means no choice. Such an app follows the env's default file,
  when there is one, so it cannot be held in the env against that file.
- Installs and removals work in the env only. An app that runs in a container is installed
  and removed there by hand; the launcher refuses both for it.
- An app both systems have is listed once, as the env's; "Runs in…" moves it.
- The containers are read again when the launcher comes back, at most once a minute, so an
  app installed in one from a terminal can take that long to show.
- A session left open when the agent restarts keeps its X server unit; nothing stops it.

Not done yet: the present-hold check for the container's Mesa, binding the env's
`/opt/tessera` into the container.

## Security

- **The container's domain is permissive.** Droidspaces upstream says its domain cannot be made
  enforcing. Everything in a container, including what Tessera starts there, runs in it.
  An enforcing domain would need at least: connect to the X server's socket (it is the env's),
  and use of the GPU node, which `--gpu` creates with a generic label.
- **Every session's X socket is in the container.** `/mnt/tessera/x` holds all of them. An app
  Tessera starts has it hidden, but the container's own services can connect to any session and
  read its windows and keys. Upstream says the same of its mode.
- **Root in a Droidspaces container can reach the phone.** It is a privileged runtime.
- **The agent runs `droidspaces` as root on the env's word.** The container name must never
  reach a shell unchecked.

Treat a container used this way as trusted as the env itself.

## Open questions

- **Session end.** In the env, the session has a PID namespace and ends as a whole. Through
  `droidspaces run` the processes are children of the container. Does ending the `run` process
  end them all? Upstream ends a session's processes as root (`ProcessTree.killSessionAsRoot`).
- **Task manager.** It finds a session's processes by an environment variable, in the env's
  process list. The env shares the phone's PID namespace, so it may see the container's
  processes too. Not checked.
- **Sound.** The FIFO is in the bound folder, so a PulseAudio in the container should reach it.
  Not tried.
- **Network.** A NAT container has its own network namespace. Fine for the socket-folder
  display. The loopback-TCP display (X server in the launcher) cannot work there.
- **App uid.** Upstream renames the container's `user` to Tessera's uid. A container someone
  also works in should not be changed that way.
- **Folders.** A grant only works for folders the container can see.
- **A desktop in a Tessera window.** Tessera has a desktop mode with its own shell around it.
  Whether XFCE behaves in it has not been tried on this phone.

## Test plan

1. Bind the two folders into the template-copy container; start `enter.sh` there by hand through
   `droidspaces run` with a real settings payload captured from an env session. Expect xterm
   with matchbox, as in the env.
2. The same with the audio permission; play a sound.
3. End the session from the launcher side; check nothing of it is left in the container.
4. Agent change behind `TESSERA3`; repeat 1 to 3 from the launcher.
5. A desktop entry for the XFCE container; check `xfwm4` stays up.
6. Tear test and `glxgears` in the container session: 60 or ~119 FPS shows whether the present
   hold was turned off for it.
7. OnePlus 13 and Pixel 6, once their images have Droidspaces.
