// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ContainerOverridesTest {
    @get:Rule val tmp = TemporaryFolder()

    private val app = PermissionManifest(
        id = "xterm", name = "xterm", exec = listOf("/usr/bin/xterm"), gui = true,
        permissions = Permissions(display = true),
    )

    @Test
    fun `an app runs in the env until a container is chosen, and the choice is kept`() {
        val file = File(tmp.root, "containers.json")
        val overrides = ContainerOverrides(file)
        assertNull(overrides.apply(app).container)

        assertEquals(emptyList<String>(), overrides.set(app, "trixie"))
        assertEquals("trixie", overrides.apply(app).container)
        assertEquals(mapOf("xterm" to "trixie"), overrides.overrides.value)
        assertEquals("trixie", ContainerOverrides(file).apply(app).container)

        assertEquals(emptyList<String>(), overrides.set(app, null))
        assertNull(overrides.apply(app).container)
        assertNull(ContainerOverrides(file).apply(app).container)
    }

    @Test
    fun `a name that is none is refused and changes nothing`() {
        val overrides = ContainerOverrides(File(tmp.root, "containers.json"))
        overrides.set(app, "trixie")
        assertTrue(overrides.set(app, "--help").isNotEmpty())
        assertTrue(overrides.set(app, "a b").isNotEmpty())
        assertTrue(overrides.set(app, "").isNotEmpty())
        assertEquals("trixie", overrides.apply(app).container)
    }

    @Test
    fun `reset puts only that app back in the env`() {
        val overrides = ContainerOverrides(File(tmp.root, "containers.json"))
        val other = app.copy(id = "htop")
        overrides.set(app, "trixie")
        overrides.set(other, "box")
        overrides.reset(app.id)
        assertNull(overrides.apply(app).container)
        assertEquals("box", overrides.apply(other).container)
    }

    @Test
    fun `a file edited by hand keeps only what is a name`() {
        val file = File(tmp.root, "containers.json")
        file.writeText("""{"xterm": "--name=x", "htop": "box"}""")
        val overrides = ContainerOverrides(file)
        assertNull(overrides.apply(app).container)
        assertEquals("box", overrides.apply(app.copy(id = "htop")).container)
    }

    @Test
    fun `the manifest takes a container's name and nothing else there`() {
        assertEquals(app.validate(), app.copy(container = "deb.xfce-1").validate())
        assertTrue((app.copy(container = "-x").validate() - app.validate().toSet()).isNotEmpty())
        assertTrue((app.copy(container = "a/b").validate() - app.validate().toSet()).isNotEmpty())
    }
}
