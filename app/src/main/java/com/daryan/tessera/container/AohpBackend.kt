// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import android.util.Base64
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Where a gramarye env's Tessera agent listens, the token it lets a launch in with, and what the
 * env can do, as its installer found:
 * [vulkanIcd] is the env's own Turnip driver (the ICD file of a Mesa built for Android's GPU
 * device): hardware graphics there use the env's Mesa, not this app's bundle. [glDriver] is a
 * native OpenGL driver for that device in the same Mesa. [hostX] says the env can start the
 * session's X server on its own side, which is what lets a client hand it buffers. [privateDev]
 * says its launch script gives each app a /dev of its own, with the app's devices and none of the
 * env's others.
 */
/** Set (to 1) in a session whose X server is the env's, started by its agent, and not this app's. */
const val ENV_SIDE_X_ENV = "TESSERA_ENV_X"

data class AohpTarget(
    val port: Int,
    val token: String,
    val vulkanIcd: String? = null,
    val glDriver: String? = null,
    val hostX: Boolean = false,
    val privateDev: Boolean = false,
    /** The Droidspaces containers the env's agent can start an app in, by name; as it last said. */
    val containers: List<String> = emptyList(),
    /** How much of a session's folder the env's agent carries ([SessionMirror]): from 2, the desktop's pictures too. */
    val mirror: Int = 1,
) {
    /** What the env can do, as the words [parse] reads after the token. */
    val caps: String
        get() = listOfNotNull(
            vulkanIcd?.let { "vk=$it" }, glDriver?.let { "gl=$it" }, "x=host".takeIf { hostX }, "dev=private".takeIf { privateDev },
            "mirror=$mirror".takeIf { mirror > 1 },
            containers.takeIf { it.isNotEmpty() }?.joinToString(",", prefix = "ds="),
        ).joinToString(" ")

    /** The agent file's line for this, which [parse] reads back. */
    fun toLine(): String = "$port $token $caps".trimEnd()

    /** This with what the env says it can do now ([caps] as the agent sends it); null when that isn't such words. */
    fun withCaps(caps: String): AohpTarget? = parse("$port $token $caps")

    companion object {
        const val DEFAULT_PORT = 7377
        private val WORD = Regex("[A-Za-z0-9_-]{1,32}")

        /**
         * From the agent file's one line, "<port> <token> [vk=<icd file>] [gl=<driver>] [x=host] [dev=private]";
         * null when it isn't that. A bare path in third place is the ICD file, as older installers
         * wrote it.
         */
        fun parse(text: String): AohpTarget? {
            val parts = text.trim().split(Regex("\\s+"))
            val port = parts.getOrNull(0)?.toIntOrNull()?.takeIf { it in 1..65535 } ?: return null
            val token = parts.getOrNull(1)?.takeIf { it.isNotEmpty() } ?: return null
            var target = AohpTarget(port, token)
            for (word in parts.drop(2)) {
                val value = word.substringAfter('=', "")
                target = when {
                    word.startsWith("/") -> target.copy(vulkanIcd = word)
                    word.startsWith("vk=/") -> target.copy(vulkanIcd = value)
                    word.startsWith("gl=") && WORD.matches(value) -> target.copy(glDriver = value)
                    word == "x=host" -> target.copy(hostX = true)
                    word == "dev=private" -> target.copy(privateDev = true)
                    word.startsWith("mirror=") && value.toIntOrNull()?.let { it in 1..99 } == true -> target.copy(mirror = value.toInt())
                    word.startsWith("ds=") && value.split(',').all(PermissionManifest.CONTAINER::matches) ->
                        target.copy(containers = value.split(',').distinct())
                    else -> return null
                }
            }
            return target
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
 * The launch is a small program of this app's (cpp/relay.c) that sends one line, "TESSERA2
 * <token> <session> <settings> <rows> <columns>", and then carries its terminal to the one the
 * agent makes for the app in the env: what is typed, the size whenever it changes, and back the
 * app's output and exit status. The agent ends the app when the connection closes. The env
 * can't see this app's folders, so nothing is shared through files.
 *
 * The display comes one of two ways. Where the env can ([AohpTarget.hostX]), the agent starts
 * the session's X server on the env's side ([startXServer]): its socket is a file the app
 * reaches directly, so a client can pass it the buffers it drew instead of copying each frame.
 * Otherwise the X server stays in this app, listens on TCP, and a per-session cookie ([XAuth])
 * is what lets a client in.
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

    /** Whether windowed apps can draw with the phone's GPU: the env has a driver and the device is there. */
    val hasGpu: Boolean get() = target.vulkanIcd != null && File(GpuDriver.KGSL).canRead()

    /** Whether the session's X server runs on the env's side. */
    val hostX: Boolean get() = target.hostX

    companion object {
        const val NAME = "Gramarye"

        /** The relay, among the native libraries: that is where Android unpacks what it lets an app run. */
        const val RELAY = "libtessera-relay.so"

        /** What [GpuDriver]'s environment says about this app's own Mesa bundle, which an env doesn't get. */
        private val BUNDLE_ONLY = setOf("LD_LIBRARY_PATH", "__EGL_VENDOR_LIBRARY_FILENAMES", "LIBGL_DRIVERS_PATH")

        /** What makes Mesa copy each frame to the X server as an image: not wanted when buffers can be passed. */
        private val IMAGE_PATH_ONLY = setOf("MESA_VK_WSI_DEBUG", "LIBGL_KOPPER_DRI2", "LIBGL_DRI3_DISABLE")

        /** The file in the app's storage that names the agent: "<port> <token> ...". */
        const val AGENT_FILE = "aohp/agent"

        /** A guest's DISPLAY for an X server in this app: loopback TCP, not a socket file. */
        fun guestDisplay(display: Int) = "127.0.0.1:$display"

        /** Where the script puts the session's X cookie for the app. */
        const val GUEST_XAUTHORITY = "/tmp/.tessera-xauth"

        /** Where the agent keeps an env-side X server's socket folder, as the env sees it. */
        fun guestXDir(session: Session) = "/var/lib/tessera/x/${session.id}"

        private const val CONNECT_MS = 5_000
        private const val READY_MS = 20_000

        /** Numbers [run]'s sessions, over every backend made for the env. */
        private val RUNS = java.util.concurrent.atomic.AtomicInteger()
        private const val EXISTS_MARK = "@@tessera-exists@@ "
    }

    override fun enforcement(capability: Capability, app: PermissionManifest) = when {
        app.permissions.root -> Enforcement.ADVISORY
        // An app's devices are all it finds in /dev where the env's launch script makes it one
        // of its own; an older script leaves it the env's, every node of the phone.
        capability == Capability.DEVICES && !target.privateDev -> Enforcement.ADVISORY
        else -> Enforcement.ENFORCED
    }

    override fun buildLaunch(app: PermissionManifest, session: Session): LaunchSpec =
        launchSpec(session, settings(app, session), app.container)

    /** What a script run in the env ([run]) left: its exit status and what it printed. */
    class Ran(val code: Int, val bytes: ByteArray) {
        val text get() = String(bytes)
    }

    /**
     * Runs [script] (sh) in the env and waits for it: as the apps' user, or as root, with [input]
     * on its standard input and no network. How the launcher reads and changes the env's system
     * (what is installed, its menus, its config files), which it has no other way to reach. A
     * session like any other, over pipes; what it prints on either stream comes back together.
     * With a [container] the script runs in that Droidspaces container instead, which is how the
     * launcher reads what a container has.
     * Null when the env's agent can't be reached or the script outlasts [timeoutSeconds].
     */
    fun run(script: String, root: Boolean = false, input: ByteArray? = null, timeoutSeconds: Long = 60, container: String? = null): Ran? {
        val session = Session(id = "env${RUNS.incrementAndGet()}", term = "dumb")
        val app = PermissionManifest(
            id = "env-run", name = "env", exec = listOf("/bin/sh", "-c", script),
            permissions = Permissions(root = root), container = container,
        )
        return try {
            val spec = buildLaunch(app, session)
            val process = ProcessBuilder(spec.argv).directory(File(spec.cwd)).redirectErrorStream(true)
                .apply { environment().putAll(spec.env) }.start()
            // Fed from a thread of its own: a script that prints before it has read everything
            // would otherwise fill its pipe while this end is still writing.
            val feeder = Thread {
                runCatching { process.outputStream.use { out -> input?.let(out::write) } }
            }.apply { isDaemon = true; start() }
            val out = java.io.ByteArrayOutputStream()
            val reader = Thread { runCatching { process.inputStream.use { it.copyTo(out) } } }.apply { isDaemon = true; start() }
            if (!process.waitFor(timeoutSeconds, java.util.concurrent.TimeUnit.SECONDS)) {
                process.destroyForcibly()
                return null
            }
            reader.join(5_000)
            feeder.join(1_000)
            // The relay's own words when it can't reach the agent are no answer from the env.
            if (process.exitValue() == 1 && out.size() < 512 && String(out.toByteArray()).startsWith("tessera: ")) null
            else Ran(process.exitValue(), out.toByteArray())
        } catch (_: IOException) {
            null
        } finally {
            paths.sessionDir(session).deleteRecursively()
        }
    }

    /**
     * The cameras plugged in, as the env sees them; none when it couldn't be asked or its launch
     * scripts are older than the listing. Asked as root: the env's own /dev has the cameras, an
     * app's has only the ones it was given.
     */
    fun cameras(): List<HostDevice> =
        run("python3 /opt/tessera/v4l_links.py --cameras", root = true, timeoutSeconds = 15)
            ?.takeIf { it.code == 0 }?.let { HostDevices.cameras(it.text) }.orEmpty()

    /** Which of [paths] (absolute) exist in the env, or in [container]; null when it couldn't be asked. */
    fun existing(paths: Collection<String>, container: String? = null): Set<String>? {
        val wanted = paths.filter { p -> p.startsWith("/") && p.none { it == '\n' || it == '\u0000' } }.distinct()
        if (wanted.isEmpty()) return emptySet()
        val script = "for p in ${wanted.joinToString(" ") { shellQuote(it) }}; do " +
            "if [ -e \"\$p\" ] || [ -L \"\$p\" ]; then echo \"$EXISTS_MARK\$p\"; fi; done; exit 0"
        val ran = run(script, timeoutSeconds = 30, container = container)?.takeIf { it.code == 0 } ?: return null
        return ran.text.lineSequence().filter { it.startsWith(EXISTS_MARK) }
            .map { it.removePrefix(EXISTS_MARK) }.filterTo(mutableSetOf()) { it in wanted }
    }

    /**
     * The first line the agent reads, less the terminal's rows and columns, which the relay adds.
     * With a [container] it is the line that names one: the agent starts the session there
     * rather than in the env. Without, the env decides (itself, unless it has a default).
     */
    internal fun hello(session: Session, settings: String, container: String? = null) =
        if (container == null) "TESSERA2 ${target.token} ${session.id} ${encodeSettings(settings)}"
        else "TESSERA3 ${target.token} ${session.id} $container ${encodeSettings(settings)}"

    /**
     * Has the agent start [session]'s X server on the env's side, for the window tagged [tag] of
     * the app [pkg] (whose APK holds the server), and returns once it accepts clients. The
     * server lives as long as the returned process isn't destroyed: it is the connection to the
     * agent, which stops the server when that closes. The same connection keeps the session's
     * X11 folder here in step with the server's own, see [SessionMirror].
     */
    fun startXServer(session: Session, tag: String, pkg: String): Process {
        val display = requireNotNull(session.display) { "session has no display" }
        val socket = Socket()
        try {
            socket.connect(InetSocketAddress(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)), target.port), CONNECT_MS)
            socket.getOutputStream().run {
                write("TESSERAX2 ${target.token} ${session.id} $display $tag $pkg\n".toByteArray(Charsets.US_ASCII))
                flush()
            }
            socket.soTimeout = READY_MS
            val reply = readLine(socket.getInputStream())
            socket.soTimeout = 0
            if (reply != "READY") {
                throw IOException("the env could not start the X server: ${reply ?: "no answer (is its agent older than this app?)"}")
            }
            // From here the connection carries the session's folder both ways.
            return RemoteXServer(socket, SessionMirror(socket, paths.x11SocketDir(session), pictures = target.mirror >= 2).apply { start() })
        } catch (e: IOException) {
            runCatching { socket.close() }
            throw e
        }
    }

    private fun readLine(input: InputStream): String? {
        val line = StringBuilder()
        while (line.length < 512) {
            val b = input.read()
            if (b < 0) return line.toString().takeIf { it.isNotEmpty() }
            if (b == '\n'.code) return line.toString()
            line.append(b.toChar())
        }
        return line.toString()
    }

    private fun launchSpec(session: Session, settings: String, container: String? = null): LaunchSpec {
        // In a file, not an argument or the environment: the settings carry the GUI helpers'
        // source and outgrow what one of those may hold.
        val hello = File(paths.sessionDir(session), "aohp-hello")
        hello.parentFile?.mkdirs()
        hello.writeText(hello(session, settings, container))
        hello.setReadable(false, false)
        hello.setReadable(true, true)
        // The relay (cpp/relay.c) reads that line, adds this terminal's size, and carries the
        // terminal to the one the agent makes for the app: sizes as they change, the app's exit
        // status at the end. SESSION_ENV lets the launcher's own end-of-session sweep find it.
        return LaunchSpec(
            listOf(File(paths.nativeLibDir, RELAY).path, target.port.toString(), hello.path),
            mapOf(SESSION_ENV to session.id), "/",
        )
    }

    internal fun settings(app: PermissionManifest, session: Session): String {
        val p = app.permissions
        val env = guestEnv(app, session).toMutableMap()
        val windowed = p.display && session.display != null
        val envSideX = windowed && target.hostX
        // The desktop's helper keeps xfwm4's compositor off on that server: see DESKTOP_HELPER.
        if (envSideX) env[ENV_SIDE_X_ENV] = "1"
        // Hardware graphics come from the env's Mesa: name its driver instead of the bundle's.
        if (session.gpu && target.vulkanIcd != null) {
            BUNDLE_ONLY.forEach { env.remove(it) }
            env["VK_ICD_FILENAMES"] = target.vulkanIcd
            if (envSideX) {
                // The server takes the client's buffers: no frame copies, and OpenGL can use the
                // env's native driver where it has one instead of going through Vulkan.
                IMAGE_PATH_ONLY.forEach { env.remove(it) }
                target.glDriver?.let {
                    env["MESA_LOADER_DRIVER_OVERRIDE"] = it
                    env.remove("GALLIUM_DRIVER")
                }
            }
        }
        // An X server in this app takes no client without the session's cookie; the guest gets it
        // as a file the script writes into the app's private /tmp. One on the env's side is
        // reached through a socket folder only this session is given.
        val xauth = if (windowed && !envSideX) XAuth.create(paths, session) else null
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
            "XDIR" to if (envSideX) guestXDir(session) else "",
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

/**
 * A session's X server on the env's side, seen from here: the connection to the agent that
 * started it. Closing it is what stops the server.
 */
private class RemoteXServer(private val socket: Socket, private val mirror: SessionMirror) : Process() {
    // The connection is the mirror's: nothing of the server's own comes through it.
    override fun getOutputStream(): OutputStream = object : OutputStream() {
        override fun write(b: Int) = Unit
    }
    override fun getInputStream(): InputStream = ByteArrayInputStream(ByteArray(0))
    override fun getErrorStream(): InputStream = ByteArrayInputStream(ByteArray(0))

    /** Until the agent hangs up: the server is gone. */
    override fun waitFor(): Int {
        mirror.join()
        return 0
    }

    override fun exitValue(): Int =
        if (socket.isClosed) 0 else throw IllegalThreadStateException("the X server is still running")

    override fun destroy() {
        mirror.close()
        runCatching { socket.close() }
    }
}
