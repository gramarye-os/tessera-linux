// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionPicturesTest {
    @Test fun `an agent that takes the desktop's pictures says so`() {
        val target = AohpTarget.parse("7377 sesame gl=kgsl x=host mirror=2 ds=trixie")!!
        assertEquals(2, target.mirror)
        assertEquals("gl=kgsl x=host mirror=2 ds=trixie", target.caps)
        assertEquals(target, AohpTarget.parse(target.toLine()))
    }

    @Test fun `one that doesn't say carries the first things only`() {
        val target = AohpTarget.parse("7377 sesame x=host")!!
        assertEquals(1, target.mirror)
        assertEquals("x=host", target.caps)
    }

    @Test fun `a word that is no count is not read`() {
        assertNull(AohpTarget.parse("7377 sesame mirror=0"))
        assertNull(AohpTarget.parse("7377 sesame mirror=two"))
        assertNull(AohpTarget.parse("7377 sesame mirror="))
        assertNull(AohpTarget.parse("7377 sesame mirror=100"))
    }

    @Test fun `an older agent put back in the env is taken at its word`() {
        val before = AohpTarget.parse("7377 sesame x=host mirror=2")!!
        val now = (AohpPairing.pingAnswer(before, "PONG x=host") as AohpPairing.Pinged.Answers).target
        assertEquals(1, now.mirror)
    }

    @Test fun `only the desktop's pictures are sent`() {
        assertTrue(MirrorFrame.PICTURE.matches("desktop-wallpaper-1.raw"))
        assertTrue(MirrorFrame.PICTURE.matches("desktop-icons-12.raw"))
        assertFalse(MirrorFrame.PICTURE.matches("desktop-wallpaper-1.tmp"))
        assertFalse(MirrorFrame.PICTURE.matches("desktop-wallpaper-.raw"))
        assertFalse(MirrorFrame.PICTURE.matches("desktop-other-1.raw"))
        assertFalse(MirrorFrame.PICTURE.matches("launcher-windows"))
        assertFalse(MirrorFrame.PICTURE.matches("incoming/desktop-icons-1.raw"))
    }

    @Test fun `the desktop's helper keeps the compositor off on the env's X server`() {
        assertTrue("os.environ.get(\"$ENV_SIDE_X_ENV\")" in DESKTOP_HELPER)
        assertTrue("settle_compositor()" in DESKTOP_HELPER)
    }

    @Test fun `the desktop's helper fits a window that leaves its maximized state`() {
        // It hears of the change, then looks once the window manager has resized the window.
        assertTrue("e.atom == A(\"_NET_WM_STATE\") and e.window.id in fitted" in DESKTOP_HELPER)
        assertTrue("keep_in_screen(wid, ax, ay, aw, ah)" in DESKTOP_HELPER)
    }
}
