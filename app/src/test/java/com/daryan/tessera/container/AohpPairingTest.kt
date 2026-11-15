// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AohpPairingTest {
    private val full = AohpTarget(7377, "sesame", vulkanIcd = "/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json", glDriver = "kgsl", hostX = true)

    @Test
    fun codeAsTyped() {
        assertEquals("123456", AohpPairing.code("123456"))
        assertEquals("123456", AohpPairing.code("123 456"))
        assertEquals("012345", AohpPairing.code(" 012-345 "))
        assertNull(AohpPairing.code("12345"))
        assertNull(AohpPairing.code("1234567"))
        assertNull(AohpPairing.code("12345a"))
        assertNull(AohpPairing.code(""))
    }

    @Test
    fun targetLineRoundTrips() {
        assertEquals(full, AohpTarget.parse(full.toLine()))
        val bare = AohpTarget(7400, "t")
        assertEquals("7400 t", bare.toLine())
        assertEquals(bare, AohpTarget.parse(bare.toLine()))
        // An env whose launch script gives each app a /dev of its own says so.
        val own = AohpTarget.parse("7400 t x=host dev=private")
        assertEquals(AohpTarget(7400, "t", hostX = true, privateDev = true), own)
        assertEquals("7400 t x=host dev=private", own?.toLine())
        assertEquals(false, AohpTarget.parse("7400 t x=host")?.privateDev)
        assertNull(AohpTarget.parse("7400 t dev=shared"))
    }

    @Test
    fun pairingAnswers() {
        assertEquals(AohpPairing.Paired.Done(full), AohpPairing.pairAnswer("OK ${full.toLine()}"))
        assertEquals(AohpPairing.Paired.Done(AohpTarget(7377, "sesame")), AohpPairing.pairAnswer("OK 7377 sesame"))
        assertEquals(AohpPairing.Paired.Refused("Wrong code."), AohpPairing.pairAnswer("NO wrong code"))
        assertTrue(AohpPairing.pairAnswer(null) is AohpPairing.Paired.NoAnswer)
        assertTrue(AohpPairing.pairAnswer("OK nonsense") is AohpPairing.Paired.NoAnswer)
        assertTrue(AohpPairing.pairAnswer("tessera-agent: bad session id or settings") is AohpPairing.Paired.NoAnswer)
    }

    @Test
    fun pingAnswers() {
        val bare = AohpTarget(7377, "sesame")
        assertEquals(AohpPairing.Pinged.Answers(full), AohpPairing.pingAnswer(bare, "PONG ${full.caps}"))
        // The env lost its GPU driver: the launcher must stop naming it.
        assertEquals(AohpPairing.Pinged.Answers(bare), AohpPairing.pingAnswer(full, "PONG"))
        // An agent that doesn't know what its env can do: keep what pairing said.
        assertEquals(AohpPairing.Pinged.Answers(full), AohpPairing.pingAnswer(full, "PONG -"))
        assertEquals(AohpPairing.Pinged.Refused, AohpPairing.pingAnswer(full, null))
        assertEquals(AohpPairing.Pinged.Refused, AohpPairing.pingAnswer(full, "PONGO"))
    }
}
