// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import com.daryan.tessera.BackendMode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AohpAutoPairTest {
    private val never = { throw AssertionError("the env was asked without need") }

    @Test
    fun aNewInstallPairsOnce() {
        assertTrue(AohpAutoPair.wanted(off = false, chosen = null, paired = false, refused = never))
        assertFalse(AohpAutoPair.wanted(off = false, chosen = null, paired = true, refused = never))
    }

    @Test
    fun aChosenBackendIsKept() {
        for (mode in BackendMode.entries.filter { it != BackendMode.AOHP }) {
            assertFalse(mode.name, AohpAutoPair.wanted(off = false, chosen = mode, paired = false, refused = never))
        }
    }

    @Test
    fun anEnvMadeAnewIsPairedAgain() {
        assertTrue(AohpAutoPair.wanted(off = false, chosen = BackendMode.AOHP, paired = false, refused = never))
        var asked = 0
        assertTrue(AohpAutoPair.wanted(off = false, chosen = BackendMode.AOHP, paired = true, refused = { asked++; true }))
        assertFalse(AohpAutoPair.wanted(off = false, chosen = BackendMode.AOHP, paired = true, refused = { asked++; false }))
        assertEquals(2, asked)
    }

    @Test
    fun unpairingStaysUnpaired() {
        assertFalse(AohpAutoPair.wanted(off = true, chosen = null, paired = false, refused = never))
        assertFalse(AohpAutoPair.wanted(off = true, chosen = BackendMode.AOHP, paired = false, refused = never))
        assertFalse(AohpAutoPair.wanted(off = true, chosen = BackendMode.AOHP, paired = true, refused = never))
    }
}
