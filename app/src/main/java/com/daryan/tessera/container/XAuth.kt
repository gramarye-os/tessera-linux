// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import java.io.ByteArrayOutputStream
import java.io.File
import java.security.SecureRandom

/**
 * The cookie that guards a session's X server when it listens on TCP ([AohpBackend]): the server
 * binds every interface of the phone, so without one anything that can reach the phone could
 * open the display. One MIT-MAGIC-COOKIE-1 record in Xauthority format, kept in the session's
 * folder for the server (-auth) and handed to the guest inside its settings.
 */
object XAuth {
    const val PROTOCOL = "MIT-MAGIC-COOKIE-1"
    private const val FAMILY_WILD = 0xFFFF
    private const val COOKIE_BYTES = 16

    fun file(paths: ContainerPaths, session: Session) = File(paths.sessionDir(session), "Xauthority")

    /** One record that matches any address for [display]: the guest reaches it as 127.0.0.1. */
    fun record(display: Int, cookie: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        fun u16(n: Int) { out.write(n shr 8 and 0xFF); out.write(n and 0xFF) }
        fun field(b: ByteArray) { u16(b.size); out.write(b) }
        u16(FAMILY_WILD)
        field(ByteArray(0))
        field(display.toString().toByteArray(Charsets.US_ASCII))
        field(PROTOCOL.toByteArray(Charsets.US_ASCII))
        field(cookie)
        return out.toByteArray()
    }

    /** A new cookie for [session], replacing any left by an earlier session of the same id. */
    fun create(paths: ContainerPaths, session: Session): ByteArray {
        val display = requireNotNull(session.display) { "session has no display" }
        val bytes = record(display, ByteArray(COOKIE_BYTES).also { SecureRandom().nextBytes(it) })
        val f = file(paths, session)
        f.parentFile?.mkdirs()
        f.delete()
        f.createNewFile()
        f.setReadable(false, false)
        f.setReadable(true, true)
        f.setWritable(false, false)
        f.setWritable(true, true)
        f.writeBytes(bytes)
        return bytes
    }

    /** The session's record, made now if the launch didn't make one. */
    fun ensure(paths: ContainerPaths, session: Session): ByteArray {
        val f = file(paths, session)
        return if (f.isFile && f.length() > 0) f.readBytes() else create(paths, session)
    }
}
