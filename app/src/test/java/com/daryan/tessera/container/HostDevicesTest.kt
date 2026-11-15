// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Test

class HostDevicesTest {
    @Test
    fun camerasAnEnvLists() {
        assertEquals(
            listOf(
                HostDevice("/dev/video2", "OBSBOT Tiny 2", "Camera · /dev/video2"),
                HostDevice("/dev/video4", "Camera", "Camera · /dev/video4"),
            ),
            HostDevices.cameras("video2\tOBSBOT Tiny 2\nvideo4\t \n"),
        )
        assertEquals(emptyList<HostDevice>(), HostDevices.cameras(""))
    }

    @Test
    fun onlyLinesOfTheListing() {
        // What a script prints beside its answer, or a node that is no video node, is no camera.
        val listing = "python3: can't open file\nvideo2\n../sda\tDisk\nvideo2/x\tOdd\nmedia3\tOdd\n" +
            "video99999\tOdd\nvideo3\ta\tb\nvideo5\tGood\u0007 Name\nvideo5\tAgain\n"
        assertEquals(listOf(HostDevice("/dev/video5", "Good Name", "Camera · /dev/video5")), HostDevices.cameras(listing))
        assertEquals(60, HostDevices.cameras("video1\t" + "x".repeat(200)).single().label.length)
    }
}
