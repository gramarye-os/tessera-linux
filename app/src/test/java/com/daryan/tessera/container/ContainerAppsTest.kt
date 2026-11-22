// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ContainerAppsTest {
    private fun app(id: String, exec: String, vararg packages: String, name: String = id) =
        PermissionManifest(id = id, name = name, exec = listOf(exec), packages = packages.toList())

    private val terminal = app("bash", "/bin/bash")
    private val desktop = app("desktop", "/usr/bin/xfwm4", "xfwm4").copy(gui = true, desktop = true, permissions = Permissions(display = true))
    private val gimp = app("gimp", "/usr/bin/gimp", "gimp")
    private val entries = listOf(terminal, desktop, gimp)

    private val xterm = app("system-debian-xterm", "xterm", name = "XTerm")
    private val thunar = app("system-thunar", "thunar", name = "Thunar File Manager")

    private fun view(programs: Set<String>, vararg apps: PermissionManifest) =
        ContainerView(ContainerMenu(programs, emptySet(), emptySet()), apps.toList())

    @Test fun `no containers add nothing`() {
        assertTrue(containerApps(entries, listOf(terminal), emptyMap()).isEmpty())
    }

    @Test fun `an entry only a container has is listed to run there`() {
        val found = containerApps(entries, listOf(terminal), mapOf("trixie" to view(setOf("/usr/bin/xfwm4", "/bin/bash"))))
        // The terminal is the env's already; gimp is in neither.
        assertEquals(listOf(desktop.copy(container = "trixie")), found)
    }

    @Test fun `what the env lists stays the env's`() {
        val listed = listOf(terminal, gimp, xterm)
        val found = containerApps(entries, listed, mapOf("trixie" to view(setOf("/usr/bin/gimp"), xterm, thunar)))
        assertEquals(listOf(thunar.copy(container = "trixie")), found)
    }

    @Test fun `a container's menu entry isn't listed beside the launcher's entry for the same program`() {
        val thunarEntry = app("thunar", "/usr/bin/thunar", "thunar", name = "Files")
        val found = containerApps(listOf(thunarEntry), emptyList(), mapOf("trixie" to view(setOf("/usr/bin/thunar"), thunar)))
        assertEquals(listOf(thunarEntry.copy(container = "trixie")), found)
    }

    @Test fun `an app in two containers goes to the first`() {
        val views = linkedMapOf(
            "trixie" to view(setOf("/usr/bin/xfwm4"), thunar),
            "noble" to view(setOf("/usr/bin/xfwm4", "/usr/bin/gimp"), thunar, xterm),
        )
        val found = containerApps(entries, listOf(terminal), views)
        assertEquals(
            listOf("desktop" to "trixie", "system-thunar" to "trixie", "gimp" to "noble", "system-debian-xterm" to "noble"),
            found.map { it.id to it.container },
        )
    }

    @Test fun `a container whose programs weren't checked goes by its packages`() {
        val unchecked = ContainerView(ContainerMenu(null, setOf("gimp"), emptySet()), emptyList())
        assertEquals(listOf(gimp.copy(container = "trixie")), containerApps(entries, listOf(terminal), mapOf("trixie" to unchecked)))
    }

    @Test fun `a container's whole menu has what the env has too`() {
        val trixie = view(setOf("/usr/bin/xfwm4", "/bin/bash"), xterm, thunar)
        assertEquals(listOf(terminal, desktop, xterm, thunar), appsOf(trixie, entries))
        // A menu entry for a program one of the launcher's entries brings is that entry's.
        val files = app("desktop-files", "/usr/bin/xfwm4", "xfwm4", "thunar")
        assertEquals(listOf(files, xterm), appsOf(trixie, listOf(files)))
    }

    @Test fun `a desktop's Start menu has the apps of its own system`() {
        val env = app("xterm", "xterm")
        val inTrixie = thunar.copy(container = "trixie")
        assertEquals(listOf(env), com.daryan.tessera.ui.desktopApps(listOf(env, inTrixie, desktop)))
        assertEquals(listOf(inTrixie), com.daryan.tessera.ui.desktopApps(listOf(env, inTrixie, desktop), "trixie"))
    }
}
