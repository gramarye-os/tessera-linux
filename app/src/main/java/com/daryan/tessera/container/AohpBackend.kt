// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import android.util.Base64
import java.io.File

/** Where a gramarye env's Tessera agent listens, and the token it lets a launch in with. */
data class AohpTarget(val port: Int, val token: String) {
    companion object {
        const val DEFAULT_PORT = 7377

        /** From the agent file's one line, "<port> <token>"; null when it isn't that. */
        fun parse(text: String): AohpTarget? {
            val parts = text.trim().split(Regex("\\s+"))
            val port = parts.getOrNull(0)?.toIntOrNull() ?: return null
            val token = parts.getOrNull(1)?.takeIf { it.isNotEmpty() } ?: return null
            return if (parts.size == 2 && port in 1..65535) AohpTarget(port, token) else null
        }
    }
}

/**
 * Backend for a gramarye (AOHP) env: a container the ROM's own daemon runs, with real mounts and
 * namespaces, which Tessera doesn't make and has no root on the phone to enter. There is no su
 * here. An app is started by the env's Tessera agent (a small service inside the env, listening
 * on the loopback interface the env shares with Android), around the env's copy of enter.sh,
 * which gives it a private mount namespace and drops it to this app's uid, as
 * [DroidspacesBackend]'s does.
 *
 * The launch is a local shell that sends one line, "TESSERA1 <token> <session> <settings>", and
 * then relays its terminal to the agent with toybox nc; the agent ends the app when the
 * connection closes. The env can't see this app's folders, so nothing is shared through files:
 * the X server listens on TCP for these sessions, the guest's DISPLAY names it over loopback,
 * and a per-session cookie ([XAuth]) is what lets a client in.
 */
class AohpBackend(
    private val paths: ContainerPaths,
    private val target: AohpTarget,
    private val appUid: Int = android.os.Process.myUid(),
    private val encodeSettings: (String) -> String = { text ->
        Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
    },
) : ContainerBackend {
    override val name = NAME

    companion object {
        const val NAME = "Gramarye"

        /** The file in the app's storage that names the agent: "<port> <token>". */
        const val AGENT_FILE = "aohp/agent"

        /** What a guest's DISPLAY is for the session's X server: loopback TCP, not a socket file. */
        fun guestDisplay(display: Int) = "127.0.0.1:$display"

        /** Where the script puts the session's X cookie for the app. */
        const val GUEST_XAUTHORITY = "/tmp/.tessera-xauth"
    }

    override fun enforcement(capability: Capability, app: PermissionManifest) = when {
        app.permissions.root -> Enforcement.ADVISORY
        capability == Capability.DEVICES -> Enforcement.ADVISORY
        else -> Enforcement.ENFORCED
    }

    override fun buildLaunch(app: PermissionManifest, session: Session): LaunchSpec =
        launchSpec(session, settings(app, session))

    /** The first line the agent reads. */
    internal fun hello(session: Session, settings: String) =
        "TESSERA1 ${target.token} ${session.id} ${encodeSettings(settings)}"

    private fun launchSpec(session: Session, settings: String): LaunchSpec {
        // In a file, not an argument or the environment: the settings carry the GUI helpers'
        // source and outgrow what one of those may hold.
        val hello = File(paths.sessionDir(session), "aohp-hello")
        hello.parentFile?.mkdirs()
        hello.writeText(hello(session, settings) + "\n")
        hello.setReadable(false, false)
        hello.setReadable(true, true)
        // nc relays this terminal once the line is sent. What feeds it reads the terminal, which
        // never ends by itself, so it is a background job the shell ends once nc returns (the agent
        // hung up: the app is gone). Its last command is exec'd so that the job is that reader and
        // not a shell waiting on it, and its input is handed over explicitly: a shell without job
        // control gives background jobs none.
        val line = "h=\"\$1\"; f=\"\$h.in\"; rm -f \"\$f\"; mkfifo -m 600 \"\$f\" || exit 1; " +
            "exec 3<&0; { cat \"\$h\"; rm -f \"\$h\"; exec cat <&3; } > \"\$f\" & c=\$!; exec 3<&-; " +
            "/system/bin/toybox nc 127.0.0.1 ${target.port} < \"\$f\"; s=\$?; " +
            "kill \$c 2>/dev/null; rm -f \"\$f\"; exit \$s"
        // SESSION_ENV lets the launcher's own end-of-session sweep find these processes too.
        return LaunchSpec(
            listOf("/system/bin/sh", "-c", line, "tessera-aohp", hello.path),
            mapOf(SESSION_ENV to session.id), "/",
        )
    }

    internal fun settings(app: PermissionManifest, session: Session): String {
        val p = app.permissions
        val env = guestEnv(app, session).toMutableMap()
        // The X server takes no client without the session's cookie; the guest gets it as a file
        // the script writes into the app's private /tmp.
        val xauth = if (p.display && session.display != null) XAuth.create(paths, session) else null
        if (xauth != null) {
            env["DISPLAY"] = guestDisplay(session.display!!)
            env["XAUTHORITY"] = GUEST_XAUTHORITY
        }
        val vars = linkedMapOf(
            // As the env sees them; one that isn't there is skipped by the script.
            "BINDS" to p.filesystem.joinToString("\n") { g -> "${g.path}:${g.mountAt}:${if (g.readOnly) "ro" else "rw"}" },
            "DEVICES" to p.devices.joinToString("\n"),
            "USB_ALL" to if (p.usb) "1" else "",
            "GPU_DEVICE" to if (session.gpu) GpuDriver.KGSL else "",
            "X11" to if (p.display) "1" else "",
            "XAUTH" to (xauth?.let { Base64.encodeToString(it, Base64.NO_WRAP) } ?: ""),
            "RUN_AS_ROOT" to if (p.root) "1" else "0",
            "APP_UID" to appUid.toString(),
            "NO_NET" to if (p.network) "" else "1",
            "NET_GROUPS" to if (p.network) "3003,3004" else "",
            "GUEST_ENV" to env.map { (k, v) -> "$k=$v" }.joinToString("\n"),
            "CMD" to guestCommand(app).joinToString(" ") { shellQuote(it) },
        )
        return vars.entries.joinToString("\n", postfix = "\n") { (k, v) -> "$k=${shellQuote(v)}" }
    }
}
