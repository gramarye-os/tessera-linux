// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** The Droidspaces containers a gramarye env offers, and the line that sends a session to one. */
class AohpContainersTest {
    @get:Rule val tmp = TemporaryFolder()

    private val bare = AohpTarget(7377, "sesame")

    @Test
    fun `the agent's containers are read from its line and written back`() {
        val target = AohpTarget.parse("7377 sesame gl=kgsl x=host ds=trixie,deb.xfce")!!
        assertEquals(listOf("trixie", "deb.xfce"), target.containers)
        assertEquals("gl=kgsl x=host ds=trixie,deb.xfce", target.caps)
        assertEquals(target, AohpTarget.parse(target.toLine()))
        assertEquals(emptyList<String>(), AohpTarget.parse("7377 sesame gl=kgsl")!!.containers)
        assertEquals(listOf("box"), AohpTarget.parse("7377 sesame ds=box,box")!!.containers)
    }

    @Test
    fun `a word that names no container makes the line unreadable`() {
        assertNull(AohpTarget.parse("7377 sesame ds=--help"))
        assertNull(AohpTarget.parse("7377 sesame ds=a,,b"))
        assertNull(AohpTarget.parse("7377 sesame ds="))
        assertNull(AohpTarget.parse("7377 sesame ds=a/b"))
    }

    @Test
    fun `a ping brings the running containers, and takes them away again`() {
        val now = bare.copy(glDriver = "kgsl", containers = listOf("trixie"))
        assertEquals(AohpPairing.Pinged.Answers(now), AohpPairing.pingAnswer(bare, "PONG gl=kgsl ds=trixie"))
        assertEquals(AohpPairing.Pinged.Answers(bare.copy(glDriver = "kgsl")), AohpPairing.pingAnswer(now, "PONG gl=kgsl"))
    }

    @Test
    fun `the first line names the container when the app has one`() {
        val dir = tmp.newFolder()
        val paths = ContainerPaths(File(dir, "lib"), File(dir, "files"), File(dir, "cache"), File(dir, "proot"), File(dir, "chroot"))
        val backend = AohpBackend(paths, bare, appUid = 10178, encodeSettings = { "QQ==" })
        val session = Session(id = "s3", display = 3)
        assertEquals("TESSERA2 sesame s3 QQ==", backend.hello(session, "A"))
        assertEquals("TESSERA3 sesame s3 trixie QQ==", backend.hello(session, "A", "trixie"))
    }
}
