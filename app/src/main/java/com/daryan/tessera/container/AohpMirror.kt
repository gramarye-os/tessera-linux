// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import android.system.StructPollfd
import android.util.Log
import java.io.BufferedInputStream
import java.io.File
import java.io.FileDescriptor
import java.io.FileOutputStream
import java.io.IOException
import java.net.Socket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * One piece of what a [SessionMirror] and the env's agent send each other: the line
 * "<verb> <name length> <data length>", then the name's bytes, then the data's.
 */
internal class MirrorFrame(val verb: Char, val name: String, val data: ByteArray) {
    companion object {
        const val MAX_NAME = 1024
        const val MAX_DATA = 8 * 1024 * 1024

        fun header(verb: Char, nameLength: Int, dataLength: Int) =
            "$verb $nameLength $dataLength\n".toByteArray(Charsets.US_ASCII)

        /** The lengths a header [line] (without its newline) gives, or null when it isn't one. */
        fun parseHeader(line: String): Triple<Char, Int, Int>? {
            val parts = line.split(' ')
            if (parts.size != 3 || parts[0].length != 1) return null
            val name = parts[1].toIntOrNull()?.takeIf { it in 0..MAX_NAME } ?: return null
            val data = parts[2].toIntOrNull()?.takeIf { it in 0..MAX_DATA } ?: return null
            return Triple(parts[0][0], name, data)
        }
    }
}

/**
 * Keeps a session's X11 folder here ([dir]) in step with the one its X server has on a gramarye
 * env's side, over the connection that holds that server ([AohpBackend.startXServer]).
 *
 * A session's helpers and the launcher talk through files in that folder (see [SessionFiles]):
 * the window list, text-field focus, notifications one way; window commands and the session's
 * theme the other. With the server in the env the helpers' folder is there too, where this app
 * can't reach. So the env's agent sends what the helpers write, and it lands in [dir] as if
 * they had written it here; and what the launcher puts in [dir] is sent to the agent: bytes
 * written to the command pipes, and the theme's files whenever `portal`, written last, changes.
 * The session's sound comes the same way: the env's sound server plays into a pipe there, the
 * agent sends what it plays, and it goes into a pipe of the same name here, which [AudioPipe]
 * plays from as it does for the other engines.
 * Everything else in the launcher works on [dir] as it does for any other engine.
 */
internal class SessionMirror(private val socket: Socket, private val dir: File) {
    private val input = BufferedInputStream(socket.getInputStream())
    private val output = socket.getOutputStream()
    private val acks = LinkedBlockingQueue<String>()
    @Volatile private var closed = false
    private var reader: Thread? = null
    private var sound: Sound? = null

    /** The theme's files as last sent, by path under [dir], and `portal`'s stamp then. */
    private val sent = HashMap<String, Long>()
    private var themeStamp = -1L
    private var marks = 0

    /**
     * Starts following. Returns once the env has the session's theme as it is now, so an app
     * launched after this reads it there.
     */
    fun start() {
        sound = Sound(File(dir, AUDIO_FIFO_NAME))
        reader = Thread({ receive() }, "tessera-mirror-in").apply { isDaemon = true; start() }
        try {
            pushTheme()
            if (!sync(SYNC_MS)) Log.w(TAG, "the env didn't confirm the session's theme")
        } catch (e: IOException) {
            Log.w(TAG, "couldn't send the session's theme", e)
        }
        Thread({ pump() }, "tessera-mirror-out").apply { isDaemon = true; start() }
    }

    /** Until the agent hangs up: the X server is gone. */
    fun join() {
        reader?.join()
    }

    fun close() {
        closed = true
    }

    private fun send(verb: Char, name: String, data: ByteArray = NOTHING, length: Int = data.size) {
        val nameBytes = name.toByteArray()
        synchronized(output) {
            output.write(MirrorFrame.header(verb, nameBytes.size, length))
            output.write(nameBytes)
            output.write(data, 0, length)
            output.flush()
        }
    }

    /** Whether the agent has done everything sent before this, within [timeoutMs]. */
    private fun sync(timeoutMs: Long): Boolean {
        val mark = "m${++marks}"
        send('S', mark)
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while (true) {
            val left = deadline - System.nanoTime()
            if (left <= 0) return false
            if (acks.poll(left, TimeUnit.NANOSECONDS) == mark) return true
        }
    }

    private fun receive() {
        try {
            while (true) {
                val frame = readFrame() ?: break
                when (frame.verb) {
                    'F' -> if (frame.name in WHOLE) replace(frame.name, frame.data)
                    'A' -> if (frame.name in APPENDED) FileOutputStream(File(dir, frame.name), true).use { it.write(frame.data) }
                    'S' -> acks.offer(frame.name)
                    'U' -> if (frame.name == AUDIO_FIFO_NAME) sound?.play(frame.data)
                }
            }
        } catch (e: IOException) {
            if (!closed && !socket.isClosed) Log.w(TAG, "lost the env's side of ${dir.parentFile?.name}", e)
        } finally {
            // The agent hung up, or this was closed: nothing more comes, and the sound's pipe
            // must lose its writer for its player to end.
            closed = true
        }
    }

    /**
     * The session's sound on its way to [AudioPipe]: a pipe at [file], as the session's sound
     * server makes it for the other engines, and a thread that writes into it what the agent sent.
     * This end stays open for reading too, so the player's open never waits and its read never
     * ends while the session lasts.
     *
     * Sound that can't be written in time is dropped, oldest first, and not kept: the env's
     * clock and the phone's sound hardware don't agree exactly, and what waits is heard late
     * from then on. Pieces are whole frames (the agent sees to that), so dropping one doesn't
     * swap left and right.
     */
    private inner class Sound(file: File) {
        private val pieces = java.util.concurrent.LinkedBlockingDeque<ByteArray>()
        private val waiting = java.util.concurrent.atomic.AtomicInteger()
        private val pipe: FileDescriptor? = runCatching {
            if (!file.exists()) Os.mkfifo(file.path, "600".toInt(8))
            Os.open(file.path, OsConstants.O_RDWR or OsConstants.O_NONBLOCK, 0)
        }.onFailure { Log.w(TAG, "no pipe for the session's sound", it) }.getOrNull()

        init {
            if (pipe != null) Thread({ write(pipe) }, "tessera-mirror-sound").apply { isDaemon = true; start() }
        }

        fun play(data: ByteArray) {
            if (pipe == null || data.isEmpty()) return
            pieces.addLast(data)
            var held = waiting.addAndGet(data.size)
            while (held > MAX_WAITING) {
                val dropped = pieces.pollFirst() ?: break
                held = waiting.addAndGet(-dropped.size)
            }
        }

        private fun write(pipe: FileDescriptor) {
            val ready = arrayOf(StructPollfd().apply { fd = pipe; events = OsConstants.POLLOUT.toShort() })
            try {
                while (!closed) {
                    val piece = pieces.pollFirst(200, TimeUnit.MILLISECONDS) ?: continue
                    waiting.addAndGet(-piece.size)
                    var at = 0
                    while (at < piece.size && !closed) {
                        try {
                            at += Os.write(pipe, piece, at, piece.size - at)
                        } catch (e: ErrnoException) {
                            // Full: the player isn't taking it yet, or there is none.
                            if (e.errno != OsConstants.EAGAIN) return
                            pollQuietly(ready, 100)
                        }
                    }
                }
            } catch (_: IOException) {
            } catch (_: InterruptedException) {
            } finally {
                runCatching { Os.close(pipe) }
            }
        }
    }

    private fun readFrame(): MirrorFrame? {
        val line = StringBuilder()
        while (true) {
            val b = input.read()
            if (b < 0) return null
            if (b == '\n'.code) break
            if (line.length >= 64) throw IOException("no frame header")
            line.append(b.toChar())
        }
        val (verb, nameLength, dataLength) = MirrorFrame.parseHeader(line.toString()) ?: throw IOException("bad frame header \"$line\"")
        return MirrorFrame(verb, String(readFully(nameLength)), readFully(dataLength))
    }

    private fun readFully(length: Int): ByteArray {
        val bytes = ByteArray(length)
        var at = 0
        while (at < length) {
            val n = input.read(bytes, at, length - at)
            if (n < 0) throw IOException("the env hung up inside a frame")
            at += n
        }
        return bytes
    }

    /** As the helper does it: whole, by a rename, which is what [SessionFiles] waits for. */
    private fun replace(name: String, data: ByteArray) {
        val tmp = File(dir, "$name.mirror")
        tmp.writeBytes(data)
        if (!tmp.renameTo(File(dir, name))) tmp.delete()
    }

    /**
     * Reads what the launcher writes to the helpers' command pipes and sends it on; between
     * that, looks whether the theme changed. The pipes are open for writing too, so that the
     * launcher's writers always find a reader and a read never sees an end.
     */
    private fun pump() {
        val pipes = PIPES.mapNotNull { name ->
            val file = File(dir, name)
            if (!file.exists()) null
            else runCatching { name to Os.open(file.path, OsConstants.O_RDWR or OsConstants.O_NONBLOCK, 0) }.getOrNull()
        }
        val polled = pipes.map { (_, fd) -> StructPollfd().apply { this.fd = fd; events = OsConstants.POLLIN.toShort() } }.toTypedArray()
        val buffer = ByteArray(4096)
        try {
            while (!closed && !socket.isClosed) {
                if (polled.isEmpty()) Thread.sleep(POLL_MS.toLong()) else pollQuietly(polled)
                polled.forEachIndexed { i, p ->
                    if (p.revents.toInt() and OsConstants.POLLIN == 0) return@forEachIndexed
                    val n = try { Os.read(p.fd, buffer, 0, buffer.size) } catch (_: ErrnoException) { 0 }
                    if (n > 0) send('L', pipes[i].first, buffer, n)
                }
                pushTheme()
            }
        } catch (e: IOException) {
            if (!closed && !socket.isClosed) Log.w(TAG, "stopped sending to the env's side", e)
        } catch (_: InterruptedException) {
        } finally {
            pipes.forEach { (_, fd) -> runCatching { Os.close(fd) } }
        }
    }

    private fun pollQuietly(fds: Array<StructPollfd>, timeoutMs: Int = POLL_MS) {
        try {
            Os.poll(fds, timeoutMs)
        } catch (_: ErrnoException) {
            fds.forEach { it.revents = 0 }
        }
    }

    /** Sends the theme's files that changed since last time, and what is gone, if `portal` changed. */
    private fun pushTheme() {
        val root = File(dir, THEME)
        val stamp = stamp(File(root, ThemeFiles.PORTAL_FILE))
        if (stamp == themeStamp) return
        themeStamp = stamp
        val files = root.walkTopDown().filter { it.isFile && !it.name.endsWith(".tmp") }
            .associateBy { "$THEME/${it.relativeTo(root).invariantSeparatorsPath}" }
        // What is gone, by the highest folder that went with it.
        val gone = (sent.keys - files.keys).map { path ->
            var file = File(dir, path)
            while (file.parentFile != root && file.parentFile?.exists() == false) file = file.parentFile!!
            file.relativeTo(dir).invariantSeparatorsPath
        }.toSet()
        gone.forEach { send('D', it) }
        sent.keys.retainAll(files.keys)
        // `portal` last, as the launcher writes it: the session's theme helper picks a change up from it.
        files.entries.sortedBy { it.value.name == ThemeFiles.PORTAL_FILE && it.value.parentFile == root }.forEach { (path, file) ->
            val now = stamp(file)
            if (sent[path] == now) return@forEach
            if (file.length() > MirrorFrame.MAX_DATA) {
                Log.w(TAG, "$path is too large to send to the env")
                return@forEach
            }
            val bytes = runCatching { file.readBytes() }.getOrNull() ?: return@forEach
            send('P', path, bytes)
            sent[path] = now
        }
    }

    private fun stamp(file: File): Long = if (file.isFile) file.lastModified() * 31 + file.length() else 0L

    companion object {
        private const val TAG = "SessionMirror"
        private const val POLL_MS = 500
        private const val SYNC_MS = 5_000L
        /** A sixth of a second of 48 kHz stereo: more than that waiting is sound heard late. */
        private const val MAX_WAITING = 32 * 1024
        private val NOTHING = ByteArray(0)

        /** The names the agent knows too (gramarye/agent.py). */
        val WHOLE = setOf(WINDOWS_FILE_NAME, DESKTOP_ITEMS_NAME)
        val APPENDED = setOf(FOCUS_FILE_NAME, NOTIFY_FILE_NAME, DESKTOP_EVENTS_NAME)
        val PIPES = listOf(WINDOW_COMMANDS_NAME, NOTIFY_COMMANDS_NAME, DESKTOP_COMMANDS_NAME)
        val THEME = ThemeFiles.GUEST_DIR.substringAfterLast('/')
    }
}
