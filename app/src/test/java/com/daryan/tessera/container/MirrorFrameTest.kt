// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MirrorFrameTest {
    @Test
    fun headerRoundTrips() {
        val header = MirrorFrame.header('P', 20, 4096)
        assertArrayEquals("P 20 4096\n".toByteArray(), header)
        assertEquals(Triple('P', 20, 4096), MirrorFrame.parseHeader(String(header).trimEnd('\n')))
    }

    @Test
    fun emptyNameAndDataAreAllowed() {
        assertEquals(Triple('S', 0, 0), MirrorFrame.parseHeader("S 0 0"))
    }

    @Test
    fun refusesWhatIsNotAHeader() {
        assertNull(MirrorFrame.parseHeader(""))
        assertNull(MirrorFrame.parseHeader("F 1"))
        assertNull(MirrorFrame.parseHeader("FF 1 2"))
        assertNull(MirrorFrame.parseHeader("F x 2"))
        assertNull(MirrorFrame.parseHeader("F 1 2 3"))
        assertNull(MirrorFrame.parseHeader("F -1 2"))
    }

    @Test
    fun refusesLengthsBeyondTheLimits() {
        assertNull(MirrorFrame.parseHeader("F ${MirrorFrame.MAX_NAME + 1} 0"))
        assertNull(MirrorFrame.parseHeader("F 1 ${MirrorFrame.MAX_DATA + 1}"))
        assertEquals(Triple('F', MirrorFrame.MAX_NAME, MirrorFrame.MAX_DATA), MirrorFrame.parseHeader("F ${MirrorFrame.MAX_NAME} ${MirrorFrame.MAX_DATA}"))
    }
}
